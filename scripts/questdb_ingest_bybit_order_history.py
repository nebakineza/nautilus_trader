#!/usr/bin/env python3
"""Ingest Bybit unified spot trade history CSV (zip) into QuestDB via ILP.

Usage:
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
    scripts/questdb_ingest_bybit_order_history.py \
    --zip /path/to/bybit_order_history.zip \
    --table order_history_spot
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import socket
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QuestDB ILP ingest for Bybit spot trade history.")
    parser.add_argument("--zip", required=True, help="Path to Bybit order history zip file.")
    parser.add_argument("--host", default="127.0.0.1", help="QuestDB ILP host.")
    parser.add_argument("--port", type=int, default=9009, help="QuestDB ILP port.")
    parser.add_argument("--table", default="order_history_spot", help="QuestDB table name.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_ts(ts_str: str) -> int:
    # Example: 2026-01-31 23:38:05
    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def build_ilp(table: str, symbol: str, side: str, order_type: str, fields: dict[str, str], ts_ns: int) -> str:
    tags = f"venue=BYBIT,symbol={symbol},side={side},order_type={order_type}"
    field_parts = []
    for key, value in fields.items():
        if key in {"filled_value", "filled_price", "filled_qty", "fees"}:
            field_parts.append(f"{key}={value}")
        else:
            field_parts.append(f"{key}=\"{value}\"")
    return f"{table},{tags} {','.join(field_parts)} {ts_ns}\n"


def main() -> None:
    args = parse_args()
    zip_path = Path(args.zip).expanduser().resolve()
    if not zip_path.exists():
        raise SystemExit(f"Zip not found: {zip_path}")

    sock: socket.socket | None = None
    if not args.dry_run:
        sock = socket.create_connection((args.host, args.port), timeout=10)

    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        for name in csv_names:
            with zf.open(name) as handle:
                raw = handle.read().decode("utf-8")
                lines = raw.splitlines()
                if lines and lines[0].startswith("UID:"):
                    lines = lines[1:]
                text = io.StringIO("\n".join(lines))
                reader = csv.DictReader(text)
                for row in reader:
                    symbol = row.get("Spot Pairs", "").strip()
                    side = row.get("Direction", "").strip().upper()
                    order_type = row.get("Order Type", "").strip().upper()
                    ts_str = row.get("Timestamp (UTC+0)", "").strip()
                    if not symbol or not side or not ts_str:
                        continue

                    ts_ns = parse_ts(ts_str)
                    fields = {
                        "uid": row.get("Uid", ""),
                        "filled_value": row.get("Filled Value", "0"),
                        "filled_price": row.get("Filled Price", "0"),
                        "filled_qty": row.get("Filled Quantity", "0"),
                        "fees": row.get("Fees", "0"),
                        "transaction_id": row.get("Transaction ID", ""),
                        "order_no": row.get("Order No.", ""),
                    }

                    ilp = build_ilp(args.table, symbol, side, order_type, fields, ts_ns)
                    if args.dry_run:
                        print(ilp, end="")
                    else:
                        sock.sendall(ilp.encode("utf-8"))
                    total += 1

    if sock:
        sock.close()

    print(f"Ingested {total:,} rows into {args.table}")


if __name__ == "__main__":
    main()
