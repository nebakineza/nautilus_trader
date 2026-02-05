#!/usr/bin/env python3
"""Ingest JSONL order book deltas into QuestDB via ILP TCP."""

from __future__ import annotations

import argparse
import json
import socket
from datetime import datetime
from pathlib import Path
from typing import Iterable


def _iter_files(data_dir: Path, symbols: list[str], start_date: str | None, end_date: str | None) -> Iterable[Path]:
    def date_ok(date_str: str) -> bool:
        if start_date and date_str < start_date:
            return False
        if end_date and date_str > end_date:
            return False
        return True

    for symbol in symbols:
        spot_dir = data_dir / f"{symbol}_Spot"
        if not spot_dir.exists():
            continue
        for path in sorted(spot_dir.glob("*.data")):
            name = path.name
            if len(name) < 10:
                continue
            date_str = name[:10]
            if not date_ok(date_str):
                continue
            if symbol not in name:
                continue
            yield path


def _ensure_table(host: str, http_port: int, table: str) -> None:
    import urllib.parse
    import urllib.request

    sql = (
        f"create table if not exists {table} ("
        "timestamp timestamp, venue symbol, symbol symbol, side symbol, "
        "price double, size double, snapshot boolean"
        ") timestamp(timestamp) partition by DAY"
    )
    url = f"http://{host}:{http_port}/exec?query=" + urllib.parse.quote_plus(sql)
    with urllib.request.urlopen(url, timeout=30) as _:
        pass


def _open_ilp_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    return sock


def _send_batch(sock: socket.socket, lines: list[str]) -> None:
    if not lines:
        return
    payload = "\n".join(lines) + "\n"
    sock.sendall(payload.encode("utf-8"))


def _iter_lines_for_file(path: Path, venue: str, symbol: str, table: str) -> Iterable[str]:
    with path.open("r") as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = (msg.get("type") or "").lower()
            snapshot = msg_type == "snapshot"
            ts_ms = msg.get("ts")
            data = msg.get("data") or {}
            if ts_ms is None or not data:
                continue
            ts_ns = int(ts_ms) * 1_000_000

            bids = data.get("b", [])
            asks = data.get("a", [])

            for price_str, size_str in bids:
                try:
                    price = float(price_str)
                    size = float(size_str)
                except ValueError:
                    continue
                yield (
                    f"{table},venue={venue},symbol={symbol},side=BUY "
                    f"price={price},size={size},snapshot={'true' if snapshot else 'false'} {ts_ns}"
                )

            for price_str, size_str in asks:
                try:
                    price = float(price_str)
                    size = float(size_str)
                except ValueError:
                    continue
                yield (
                    f"{table},venue={venue},symbol={symbol},side=SELL "
                    f"price={price},size={size},snapshot={'true' if snapshot else 'false'} {ts_ns}"
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=str, default="/home/seb/nebakineza/nautilus_trader/data/ob_data")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,BTCUSDC,USDCUSDT,ETHUSDT,ETHBTC,SOLUSDT,SOLBTC")
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--venue", type=str, default="BYBIT")
    parser.add_argument("--table", type=str, default="orderbook_deltas")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=9000)
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--batch", type=int, default=2000)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    _ensure_table(args.host, args.http_port, args.table)

    sock = _open_ilp_socket(args.host, args.ilp_port)
    try:
        batch: list[str] = []
        files = list(_iter_files(data_dir, symbols, args.start_date, args.end_date))
        if not files:
            print("No files matched. Check symbols/date range.")
            return

        for path in files:
            print(f"Ingesting {path}...")
            symbol = path.name.split("_")[1] if "_" in path.name else "UNKNOWN"
            for line in _iter_lines_for_file(path, args.venue, symbol, args.table):
                batch.append(line)
                if len(batch) >= args.batch:
                    try:
                        _send_batch(sock, batch)
                    except BrokenPipeError:
                        sock.close()
                        sock = _open_ilp_socket(args.host, args.ilp_port)
                        _send_batch(sock, batch)
                    batch = []
        if batch:
            try:
                _send_batch(sock, batch)
            except BrokenPipeError:
                sock.close()
                sock = _open_ilp_socket(args.host, args.ilp_port)
                _send_batch(sock, batch)

        print("Ingestion complete.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
