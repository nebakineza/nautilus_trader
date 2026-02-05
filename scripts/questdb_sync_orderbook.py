#!/usr/bin/env python3
"""Sync local orderbook .data files into QuestDB.

Assumes table `orderbook_deltas` with columns:
- timestamp (designated)
- venue (tag)
- symbol (tag)
- side (string)
- price (double)
- size (double)
- snapshot (boolean)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
import urllib.error
import socket
from dataclasses import dataclass
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


def _query_json(cfg: QuestDbConfig, sql: str) -> dict:
    url = cfg.exec_url + urllib.parse.quote_plus(sql)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


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
    _query_json(cfg, sql)


def _iter_files(data_dir: str) -> Iterable[str]:
    for root, _, files in os.walk(data_dir):
        for name in files:
            if name.endswith(".data"):
                yield os.path.join(root, name)


def _infer_symbol(path: str) -> str | None:
    base = os.path.basename(path)
    match = re.match(r"\d{4}-\d{2}-\d{2}_([A-Z0-9]+)_", base)
    if match:
        return match.group(1)
    return None


def _get_max_ts_ms(cfg: QuestDbConfig, venue: str, symbol: str) -> int:
    sql = (
        f"select max(timestamp) from {cfg.table} "
        f"where venue='{venue}' and symbol='{symbol}'"
    )
    try:
        data = _query_json(cfg, sql)
    except urllib.error.HTTPError:
        return 0
    dataset = data.get("dataset", [])
    if not dataset or dataset[0][0] is None:
        return 0
    value = dataset[0][0]
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    return int(value)


def _send_lines(cfg: QuestDbConfig, lines: list[str]) -> None:
    if not lines:
        return
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    try:
        with socket.create_connection(cfg.ilp_address, timeout=10) as sock:
            sock.sendall(payload)
    except OSError:
        if len(lines) == 1:
            return
        mid = len(lines) // 2
        _send_lines(cfg, lines[:mid])
        _send_lines(cfg, lines[mid:])


def sync_file(cfg: QuestDbConfig, path: str, venue: str, symbol: str, dry_run: bool) -> int:
    max_ts_ms = _get_max_ts_ms(cfg, venue, symbol)
    ingested = 0
    batch: list[str] = []
    batch_size = 500

    with open(path, "r") as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_ms = msg.get("ts")
            if ts_ms is None or ts_ms <= max_ts_ms:
                continue

            msg_type = msg.get("type", "").lower()
            snapshot = True if msg_type == "snapshot" else False
            data = msg.get("data", {})
            bids = data.get("b", [])
            asks = data.get("a", [])
            ts_ns = int(ts_ms) * 1_000_000

            def add_lines(levels, side: str):
                nonlocal ingested
                for price_str, size_str in levels:
                    price = float(price_str)
                    size = float(size_str)
                    line = (
                        f"{cfg.table},venue={venue},symbol={symbol} "
                        f"side=\"{side}\",price={price},size={size},snapshot={'true' if snapshot else 'false'} "
                        f"{ts_ns}"
                    )
                    batch.append(line)
                    ingested += 1

                    if len(batch) >= batch_size:
                        if not dry_run:
                            _send_lines(cfg, batch)
                        batch.clear()

            add_lines(bids, "BUY")
            add_lines(asks, "SELL")

    if batch and not dry_run:
        _send_lines(cfg, batch)

    return ingested


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=str, default="/home/seb/nebakineza/nautilus_trader/data/ob_data")
    parser.add_argument("--venue", type=str, default="BYBIT")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--table", type=str, default="orderbook_deltas")
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = QuestDbConfig(host=args.host, port=args.port, table=args.table, ilp_port=args.ilp_port)

    _ensure_table(cfg)

    total = 0
    for path in sorted(_iter_files(args.data_dir)):
        symbol = _infer_symbol(path)
        if symbol is None:
            continue
        ingested = sync_file(cfg, path, args.venue, symbol, args.dry_run)
        if ingested:
            print(f"Synced {ingested} rows from {path}")
            total += ingested

    print(f"Total rows synced: {total}")


if __name__ == "__main__":
    main()
