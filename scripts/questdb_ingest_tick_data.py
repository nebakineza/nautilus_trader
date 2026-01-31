#!/usr/bin/env python3
"""Ingest tick_data CSV(.gz) files into QuestDB via ILP.

Usage:
  python scripts/questdb_ingest_tick_data.py \
        --data-dir /home/ubuntu/trading/data/tick_data \
    --host 127.0.0.1 --port 9009
"""

from __future__ import annotations

import argparse
import csv
import gzip
import socket
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QuestDB ILP ingest for tick_data CSV files.")
    parser.add_argument("--data-dir", required=True, help="Base tick_data directory.")
    parser.add_argument("--host", default="127.0.0.1", help="QuestDB ILP host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=9009, help="QuestDB ILP TCP port (default: 9009).")
    parser.add_argument("--table", default="trade_ticks", help="QuestDB table name.")
    parser.add_argument("--dry-run", action="store_true", help="Print ILP lines instead of sending.")
    return parser.parse_args()


def iter_csv_files(base_dir: Path) -> list[Path]:
    return sorted(base_dir.rglob("*.csv")) + sorted(base_dir.rglob("*.csv.gz"))


def open_csv(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def build_ilp(
    table: str,
    venue: str,
    symbol: str,
    side: str,
    trade_id: str,
    price: str,
    size: str,
    rpi: str | None,
    ts_ms: int,
) -> str:
    tags = f"venue={venue},symbol={symbol},side={side}"
    fields = f"trade_id={trade_id}i,price={price},size={size}"
    if rpi is not None:
        fields += f",rpi={rpi}"
    return f"{table},{tags} {fields} {ts_ms}000000\n"


def ingest_file(path: Path, table: str, sock: socket.socket | None, dry_run: bool) -> int:
    count = 0
    venue = "BYBIT"
    symbol = path.parent.name.split("_")[0]

    with open_csv(path) as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ts_ms = int(row.get("timestamp", "0"))
            if ts_ms == 0:
                continue
            side = row.get("side", "").upper()
            if side == "BUY":
                side_tag = "BUY"
            elif side == "SELL":
                side_tag = "SELL"
            else:
                side_tag = side or "NA"

            ilp = build_ilp(
                table=table,
                venue=venue,
                symbol=symbol,
                side=side_tag,
                trade_id=row.get("id", "0"),
                price=row.get("price", "0"),
                size=row.get("volume", "0"),
                rpi=row.get("rpi"),
                ts_ms=ts_ms,
            )
            if dry_run:
                print(ilp, end="")
            else:
                sock.sendall(ilp.encode("utf-8"))
            count += 1

    return count


def main() -> None:
    args = parse_args()
    base_dir = Path(args.data_dir).expanduser().resolve()
    files = iter_csv_files(base_dir)
    if not files:
        raise SystemExit(f"No CSV files found under {base_dir}")

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
