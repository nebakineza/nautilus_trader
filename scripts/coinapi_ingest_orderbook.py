#!/usr/bin/env python3
"""Download CoinAPI orderbook history and ingest into QuestDB.

Uses CoinAPI REST endpoint:
  GET /v1/orderbooks/{symbol_id}/history

Writes snapshots to QuestDB in Nautilus orderbook_deltas schema.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable


@dataclass(frozen=True)
class QuestDbConfig:
    host: str = "127.0.0.1"
    port: int = 9000
    table: str = "orderbook_deltas"
    ilp_port: int = 9009

    @property
    def exec_url(self) -> str:
        return f"http://{self.host}:{self.port}/exec?query="

    @property
    def ilp_address(self) -> tuple[str, int]:
        return (self.host, self.ilp_port)


def _ensure_table(cfg: QuestDbConfig) -> None:
    sql = (
        f"create table if not exists {cfg.table} ("
        "timestamp timestamp, "
        "venue symbol, "
        "symbol symbol, "
        "side string, "
        "price double, "
        "size double, "
        "snapshot boolean"
        ") timestamp(timestamp) partition by day"
    )
    url = cfg.exec_url + urllib.parse.quote_plus(sql)
    with urllib.request.urlopen(url, timeout=30) as resp:
        resp.read()


def _iter_time_windows(start: datetime, end: datetime, step: timedelta) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        yield cursor, nxt
        cursor = nxt


def _format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fetch_history(symbol_id: str, time_start: datetime, time_end: datetime, limit: int) -> list[dict]:
    base = "https://rest.coinapi.io"
    params = urllib.parse.urlencode(
        {
            "time_start": _format_ts(time_start),
            "time_end": _format_ts(time_end),
            "limit": limit,
        }
    )
    url = f"{base}/v1/orderbooks/{symbol_id}/history?{params}"
    key = os.getenv("COIN_API")
    if not key:
        raise SystemExit("COIN_API not set")
    req = urllib.request.Request(url, headers={"X-CoinAPI-Key": key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _send_lines(cfg: QuestDbConfig, lines: list[str]) -> None:
    if not lines:
        return
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    with socket.create_connection(cfg.ilp_address, timeout=10) as sock:
        sock.sendall(payload)


def _build_lines(
    table: str,
    venue: str,
    symbol: str,
    ts_ns: int,
    asks: list[dict],
    bids: list[dict],
    max_levels: int | None,
) -> list[str]:
    lines: list[str] = []
    if max_levels is not None:
        bids = bids[:max_levels]
        asks = asks[:max_levels]
    for level in bids:
        price = level.get("price")
        size = level.get("size")
        if price is None or size is None:
            continue
        lines.append(
            f"{table},venue={venue},symbol={symbol} "
            f"side=\"BUY\",price={price},size={size},snapshot=true {ts_ns}"
        )
    for level in asks:
        price = level.get("price")
        size = level.get("size")
        if price is None or size is None:
            continue
        lines.append(
            f"{table},venue={venue},symbol={symbol} "
            f"side=\"SELL\",price={price},size={size},snapshot=true {ts_ns}"
        )
    return lines


def ingest_day(
    cfg: QuestDbConfig,
    symbol_id: str,
    venue: str,
    symbol: str,
    date_str: str,
    step: timedelta,
    limit: int,
    max_levels: int | None,
) -> int:
    start = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    ingested = 0
    for window_start, window_end in _iter_time_windows(start, end, step):
        data = _fetch_history(symbol_id, window_start, window_end, limit)
        if not data:
            continue
        batch: list[str] = []
        for snapshot in data:
            ts = snapshot.get("time_exchange") or snapshot.get("time_coinapi")
            if not ts:
                continue
            ts_ns = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
            asks = snapshot.get("asks", [])
            bids = snapshot.get("bids", [])
            batch.extend(_build_lines(cfg.table, venue, symbol, ts_ns, asks, bids, max_levels))
        if batch:
            _send_lines(cfg, batch)
            ingested += len(batch)
        time.sleep(0.15)
    return ingested


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest CoinAPI orderbook history into QuestDB")
    parser.add_argument("--symbol-id", required=True, help="CoinAPI symbol_id (e.g., BINANCE_SPOT_SOL_USDT)")
    parser.add_argument("--symbol", required=True, help="Symbol tag for QuestDB (e.g., SOLUSDT)")
    parser.add_argument("--venue", default="BINANCE")
    parser.add_argument("--date", required=True, help="Date to ingest (YYYY-MM-DD)")
    parser.add_argument("--step-minutes", type=int, default=30)
    parser.add_argument("--step-seconds", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-levels", type=int, default=200)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--table", default="orderbook_deltas")
    parser.add_argument("--ilp-port", type=int, default=9009)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = QuestDbConfig(host=args.host, port=args.port, table=args.table, ilp_port=args.ilp_port)
    _ensure_table(cfg)
    max_levels = args.max_levels if args.max_levels > 0 else None
    if args.step_seconds and args.step_seconds > 0:
        step = timedelta(seconds=args.step_seconds)
    else:
        step = timedelta(minutes=args.step_minutes)

    total = ingest_day(
        cfg,
        args.symbol_id,
        args.venue,
        args.symbol,
        args.date,
        step,
        args.limit,
        max_levels,
    )
    print(f"Ingested {total:,} rows into {cfg.table}")


if __name__ == "__main__":
    main()
