#!/usr/bin/env python3
"""Download CoinAPI Flat Files L2 limit book and ingest into QuestDB.

Uses CoinAPI Flat Files S3 API:
  https://s3.flatfiles.coinapi.io/coinapi/

Files: T-LIMITBOOK_FULL/D-YYYYMMDD/E-EXCHANGE/IDDI-...+SC-COINAPI_SYMBOL_ID+S-EXCHANGE_SYMBOL.csv.gz
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import os
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


S3_BASE = "https://s3.flatfiles.coinapi.io/coinapi/"


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


def _auth_header() -> dict[str, str]:
    key = os.getenv("COIN_API")
    if not key:
        raise SystemExit("COIN_API not set")
    return {"Authorization": key, "Accept": "application/xml"}


def _list_keys(prefix: str) -> list[str]:
    url = f"{S3_BASE}?prefix={urllib.parse.quote(prefix)}"
    req = urllib.request.Request(url, headers=_auth_header())
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    return [elem.text for elem in root.findall(".//{*}Key") if elem.text]


def _normalize_iso(dt_str: str) -> str:
    if "T" not in dt_str and " " not in dt_str:
        return dt_str
    if "T" not in dt_str:
        dt_str = dt_str.replace(" ", "T")

    if "." not in dt_str:
        return dt_str

    tz = ""
    base = dt_str
    if dt_str.endswith("Z"):
        base = dt_str[:-1]
        tz = "Z"
    elif "+" in dt_str[10:] or "-" in dt_str[10:]:
        for i in range(len(dt_str) - 1, 9, -1):
            if dt_str[i] in "+-":
                base = dt_str[:i]
                tz = dt_str[i:]
                break

    main, frac = base.split(".", 1)
    frac = (frac + "000000")[:6]
    return f"{main}.{frac}{tz}"


def _parse_ts_ns(date_str: str, time_value: str) -> int:
    if "T" in time_value or " " in time_value:
        iso = time_value
    else:
        iso = f"{date_str}T{time_value}"
    iso = _normalize_iso(iso)
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _send_lines(cfg: QuestDbConfig, lines: list[str]) -> None:
    if not lines:
        return
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    with socket.create_connection(cfg.ilp_address, timeout=10) as sock:
        sock.sendall(payload)


def _iter_rows_from_gzip(url: str) -> Iterable[dict[str, str]]:
    req = urllib.request.Request(url, headers=_auth_header())
    with urllib.request.urlopen(req, timeout=120) as resp:
        with gzip.GzipFile(fileobj=resp) as gz:
            text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
            reader = csv.DictReader(text, delimiter=";")
            for row in reader:
                yield row


def ingest_flatfile(
    cfg: QuestDbConfig,
    key_path: str,
    date_str: str,
    venue: str,
    symbol: str,
    batch_size: int,
) -> int:
    url = f"{S3_BASE}{key_path}"
    ingested = 0
    batch: list[str] = []

    for row in _iter_rows_from_gzip(url):
        time_exchange = row.get("time_exchange") or ""
        update_type = (row.get("update_type") or "").upper()
        is_buy = row.get("is_buy")
        price = row.get("entry_px")
        size = row.get("entry_sx")
        if not time_exchange or price is None or size is None or is_buy is None:
            continue
        if size == "" or price == "":
            continue

        ts_ns = _parse_ts_ns(date_str, time_exchange)
        side = "BUY" if str(is_buy) == "1" else "SELL"
        snapshot = "true" if update_type == "SNAPSHOT" else "false"

        batch.append(
            f"{cfg.table},venue={venue},symbol={symbol} "
            f"side=\"{side}\",price={price},size={size},snapshot={snapshot} {ts_ns}"
        )
        ingested += 1

        if len(batch) >= batch_size:
            _send_lines(cfg, batch)
            batch.clear()

    if batch:
        _send_lines(cfg, batch)

    return ingested


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest CoinAPI flat files limit book into QuestDB")
    parser.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    parser.add_argument("--exchange", default="BINANCE")
    parser.add_argument("--symbol", required=True, help="QuestDB symbol tag, e.g. SOLUSDT")
    parser.add_argument("--coinapi-symbol-id", required=True, help="CoinAPI symbol id, e.g. BINANCE_SPOT_SOL_USDT")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--table", default="orderbook_deltas")
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--batch-size", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = QuestDbConfig(host=args.host, port=args.port, table=args.table, ilp_port=args.ilp_port)
    _ensure_table(cfg)

    date_compact = args.date.replace("-", "")
    prefix = f"T-LIMITBOOK_FULL/D-{date_compact}/E-{args.exchange}/"
    keys = _list_keys(prefix)
    match = f"SC-{args.coinapi_symbol_id}"
    keys = [k for k in keys if match in k]
    if not keys:
        raise SystemExit(f"No keys found for {match} under {prefix}")

    total = 0
    for key_path in keys:
        total += ingest_flatfile(
            cfg,
            key_path,
            args.date,
            args.exchange,
            args.symbol,
            args.batch_size,
        )

    print(f"Ingested {total:,} rows into {cfg.table}")


if __name__ == "__main__":
    main()
