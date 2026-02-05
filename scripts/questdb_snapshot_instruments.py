#!/usr/bin/env python3
"""Snapshot exchange instrument metadata into QuestDB.

Usage:
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
    scripts/questdb_snapshot_instruments.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --venue BYBIT --venue BINANCE
"""

from __future__ import annotations

import argparse
import json
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class QuestDbILPConfig:
    host: str = "127.0.0.1"
    port: int = 9009
    table: str = "instrument_meta"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snapshot instrument metadata to QuestDB")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols (e.g., BTCUSDT)")
    parser.add_argument("--venue", action="append", default=[], help="Venue to pull (BYBIT, BINANCE)")
    parser.add_argument("--ilp-host", default="127.0.0.1")
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--table", default="instrument_meta")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _ilp_line(table: str, venue: str, symbol: str, fields: dict[str, Decimal | str]) -> str:
    tags = f"venue={venue},symbol={symbol}"
    field_parts = []
    for key, value in fields.items():
        if isinstance(value, Decimal):
            field_parts.append(f"{key}={value}")
        else:
            field_parts.append(f"{key}={value}")
    return f"{table},{tags} {','.join(field_parts)}\n"


def _request_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def _bybit_symbols(symbols: list[str]) -> list[dict[str, str]]:
    base_url = "https://api.bybit.com/v5/market/instruments-info?category=spot"
    data = _request_json(base_url)
    items = data.get("result", {}).get("list", [])
    if symbols:
        items = [item for item in items if item.get("symbol") in symbols]
    return items


def _binance_symbols(symbols: list[str]) -> list[dict[str, str]]:
    if symbols:
        payload = urllib.parse.quote(json.dumps(symbols))
        url = f"https://api.binance.com/api/v3/exchangeInfo?symbols={payload}"
    else:
        url = "https://api.binance.com/api/v3/exchangeInfo"
    data = _request_json(url)
    return data.get("symbols", [])


def _extract_binance_filters(filters: list[dict[str, str]]) -> dict[str, str]:
    result = {}
    for f in filters:
        if f.get("filterType") == "PRICE_FILTER":
            result["price_tick"] = f.get("tickSize")
        if f.get("filterType") == "LOT_SIZE":
            result["qty_step"] = f.get("stepSize")
            result["min_qty"] = f.get("minQty")
            result["max_qty"] = f.get("maxQty")
        if f.get("filterType") == "MIN_NOTIONAL":
            result["min_notional"] = f.get("minNotional")
    return result


def main() -> None:
    args = parse_args()
    venues = [v.upper() for v in args.venue] if args.venue else ["BYBIT", "BINANCE"]
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    ilp_cfg = QuestDbILPConfig(host=args.ilp_host, port=args.ilp_port, table=args.table)

    sock: socket.socket | None = None
    if not args.dry_run:
        sock = socket.create_connection((ilp_cfg.host, ilp_cfg.port), timeout=10)

    total = 0
    for venue in venues:
        if venue == "BYBIT":
            for item in _bybit_symbols(symbols):
                fields = {
                    "price_tick": Decimal(item.get("priceFilter", {}).get("tickSize", "0")),
                    "qty_step": Decimal(item.get("lotSizeFilter", {}).get("qtyStep", "0")),
                    "min_qty": Decimal(item.get("lotSizeFilter", {}).get("minOrderQty", "0")),
                    "max_qty": Decimal(item.get("lotSizeFilter", {}).get("maxOrderQty", "0")),
                    "min_notional": Decimal(item.get("lotSizeFilter", {}).get("minOrderAmt", "0")),
                }
                line = _ilp_line(ilp_cfg.table, venue, item.get("symbol", ""), fields)
                total += 1
                if args.dry_run:
                    print(line, end="")
                else:
                    sock.sendall(line.encode("utf-8"))

        if venue == "BINANCE":
            for item in _binance_symbols(symbols):
                filters = _extract_binance_filters(item.get("filters", []))
                fields = {
                    "price_tick": Decimal(filters.get("price_tick", "0")),
                    "qty_step": Decimal(filters.get("qty_step", "0")),
                    "min_qty": Decimal(filters.get("min_qty", "0")),
                    "max_qty": Decimal(filters.get("max_qty", "0")),
                    "min_notional": Decimal(filters.get("min_notional", "0")),
                }
                line = _ilp_line(ilp_cfg.table, venue, item.get("symbol", ""), fields)
                total += 1
                if args.dry_run:
                    print(line, end="")
                else:
                    sock.sendall(line.encode("utf-8"))

    if sock:
        sock.close()

    print(f"Wrote {total:,} instrument records into {ilp_cfg.table}")


if __name__ == "__main__":
    main()
