#!/usr/bin/env python3
"""Ingest Nautilus-style order book JSONL data into QuestDB via ILP.

Usage:
  python scripts/questdb_ingest_ob_data.py \
    --data-dir /home/seb/nebakineza/nautilus_trader/ob_data \
    --host 127.0.0.1 --port 9009
"""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QuestDB ILP ingest for ob_data JSONL files.")
    parser.add_argument("--data-dir", required=True, help="Base ob_data directory.")
    parser.add_argument("--host", default="127.0.0.1", help="QuestDB ILP host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=9009, help="QuestDB ILP TCP port (default: 9009).")
    parser.add_argument("--table", default="orderbook_deltas", help="QuestDB table name.")
    parser.add_argument("--dry-run", action="store_true", help="Print ILP lines instead of sending.")
    return parser.parse_args()


def iter_jsonl_files(base_dir: Path) -> list[Path]:
    return sorted(base_dir.rglob("*.data"))


def build_ilp(
    table: str,
    venue: str,
    symbol: str,
    side: str,
    price: str,
    size: str,
    action: str,
    is_snapshot: bool,
    ts_ms: int,
) -> str:
    tags = f"venue={venue},symbol={symbol}"
    fields = (
        f"side=\"{side}\",price={price},size={size},action=\"{action}\",snapshot={str(is_snapshot).lower()}"
    )
    return f"{table},{tags} {fields} {ts_ms}000000\n"


def ingest_file(path: Path, table: str, sock: socket.socket | None, dry_run: bool) -> int:
    count = 0
    venue = "BYBIT" if "bybit" in path.name.lower() else "BINANCE"
    symbol = path.name.split("_")[1]

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            ts_ms = int(record.get("ts", 0))
            data = record.get("data", {})
            bids = data.get("b", [])
            asks = data.get("a", [])
            is_snapshot = record.get("type") == "snapshot"

            for price, size in bids:
                ilp = build_ilp(table, venue, symbol, "BUY", price, size, "UPDATE", is_snapshot, ts_ms)
                if dry_run:
                    print(ilp, end="")
                else:
                    sock.sendall(ilp.encode("utf-8"))
                count += 1

            for price, size in asks:
                ilp = build_ilp(table, venue, symbol, "SELL", price, size, "UPDATE", is_snapshot, ts_ms)
                if dry_run:
                    print(ilp, end="")
                else:
                    sock.sendall(ilp.encode("utf-8"))
                count += 1

    return count


def main() -> None:
    args = parse_args()
    base_dir = Path(args.data_dir).expanduser().resolve()
    files = iter_jsonl_files(base_dir)
    if not files:
        raise SystemExit(f"No .data files found under {base_dir}")

    sock: socket.socket | None = None
    if not args.dry_run:
        sock = socket.create_connection((args.host, args.port), timeout=10)

    total = 0
    for path in files:
        total += ingest_file(path, args.table, sock, args.dry_run)

    if sock:
        sock.close()

    print(f"Ingested {total:,} rows into {args.table}")


if __name__ == "__main__":
    main()
