#!/usr/bin/env python3
"""Snapshot Bybit spot fee rates into QuestDB.

Usage:
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
    scripts/questdb_snapshot_fees.py --symbols BTCUSDT,ETHUSDT
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class QuestDbILPConfig:
    host: str = "127.0.0.1"
    port: int = 9009
    table: str = "fee_rates"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snapshot Bybit fee rates to QuestDB")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    parser.add_argument("--ilp-host", default="127.0.0.1")
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--table", default="fee_rates")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _request_json(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _ilp_line(table: str, symbol: str, maker_bps: Decimal, taker_bps: Decimal) -> str:
    tags = f"venue=BYBIT,symbol={symbol}"
    fields = f"maker_bps={maker_bps},taker_bps={taker_bps}"
    return f"{table},{tags} {fields}\n"


def main() -> None:
    args = parse_args()
    api_key = os.getenv("BYBIT_API_KEY_SENTINEL") or os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET_SENTINEL") or os.getenv("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        raise SystemExit("BYBIT_API_KEY_SENTINEL/BYBIT_API_SECRET_SENTINEL not set")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    ilp_cfg = QuestDbILPConfig(host=args.ilp_host, port=args.ilp_port, table=args.table)

    sock: socket.socket | None = None
    if not args.dry_run:
        sock = socket.create_connection((ilp_cfg.host, ilp_cfg.port), timeout=10)

    total = 0
    for symbol in symbols:
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        params = f"category=spot&symbol={urllib.parse.quote(symbol)}"
        sign_payload = timestamp + api_key + recv_window + params
        signature = _sign(api_secret, sign_payload)

        url = f"https://api.bybit.com/v5/account/fee-rate?{params}"
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
        }

        data = _request_json(url, headers)
        items = data.get("result", {}).get("list", [])
        if not items:
            continue

        fee = items[0]
        maker = Decimal(fee.get("makerFeeRate", "0")) * Decimal("10000")
        taker = Decimal(fee.get("takerFeeRate", "0")) * Decimal("10000")

        line = _ilp_line(ilp_cfg.table, symbol, maker, taker)
        total += 1
        if args.dry_run:
            print(line, end="")
        else:
            sock.sendall(line.encode("utf-8"))

    if sock:
        sock.close()

    print(f"Wrote {total:,} fee records into {ilp_cfg.table}")


if __name__ == "__main__":
    main()
