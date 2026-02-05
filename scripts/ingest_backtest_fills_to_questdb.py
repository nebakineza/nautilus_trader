#!/usr/bin/env python3
"""Ingest backtest fills_report.csv into QuestDB via ILP.

Creates/updates a QuestDB table named backtest_fills by default.
"""
from __future__ import annotations

import argparse
import csv
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _escape_tag(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(" ", "\\ ")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


def _to_ns(ts_value: str) -> int:
    if not ts_value:
        return 0
    dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _ilp_line(table: str, tags: dict[str, str], fields: dict[str, str], ts_ns: int) -> str:
    tag_str = ",".join(f"{k}={_escape_tag(v)}" for k, v in tags.items() if v)
    field_str = ",".join(f"{k}={v}" for k, v in fields.items())
    if tag_str:
        return f"{table},{tag_str} {field_str} {ts_ns}"
    return f"{table} {field_str} {ts_ns}"


@dataclass
class FillRow:
    trader_id: str
    instrument_id: str
    side: str
    quantity: float
    price: float
    fees: float
    ts: int


def _parse_fills(csv_path: Path) -> list[FillRow]:
    rows: list[FillRow] = []
    with csv_path.open() as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            trader_id = row.get("trader_id", "")
            instrument_id = row.get("instrument_id", "")
            side = row.get("side", "")
            qty = float(row.get("quantity", "0") or 0)
            price = float(row.get("price", "0") or 0)
            fees_raw = row.get("commissions", "") or "0"
            fees_val = "".join(ch for ch in str(fees_raw) if (ch.isdigit() or ch in ".-"))
            fees = float(fees_val) if fees_val else 0.0
            ts = _to_ns(row.get("ts_last", "")) or _to_ns(row.get("ts_init", ""))
            if qty == 0 or price == 0:
                continue
            rows.append(
                FillRow(
                    trader_id=trader_id,
                    instrument_id=instrument_id,
                    side=side,
                    quantity=qty,
                    price=price,
                    fees=fees,
                    ts=ts,
                )
            )
    return rows


def _iter_fills(root: Path) -> list[FillRow]:
    rows: list[FillRow] = []
    for csv_path in root.rglob("fills_report.csv"):
        rows.extend(_parse_fills(csv_path))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest backtest fills into QuestDB")
    parser.add_argument("--root", required=True, help="Root directory containing backtest outputs")
    parser.add_argument("--table", default="backtest_fills")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9009)
    args = parser.parse_args()

    root = Path(args.root)
    rows = _iter_fills(root)
    if not rows:
        raise SystemExit(f"No fills_report.csv files found under {root}")

    sock = socket.create_connection((args.host, args.port), timeout=10)
    try:
        for row in rows:
            symbol = row.instrument_id.split("-")[0]
            run_id = row.trader_id.replace("BACKTEST-LLMMV3-PRIMER-", "")
            tags = {
                "symbol": symbol,
                "side": row.side,
                "trader_id": row.trader_id,
                "run_id": run_id,
            }
            fields = {
                "price": f"{row.price}",
                "qty": f"{row.quantity}",
                "fees": f"{row.fees}",
            }
            line = _ilp_line(args.table, tags, fields, row.ts)
            sock.sendall((line + "\n").encode("utf-8"))
    finally:
        sock.close()

    print(f"Ingested {len(rows)} fills into {args.table} from {root}")


if __name__ == "__main__":
    main()
