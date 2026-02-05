#!/usr/bin/env python3
"""Snapshot Bybit wallet balances into QuestDB.

Usage:
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
    scripts/questdb_snapshot_balances.py
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
    table: str = "account_balances"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snapshot Bybit balances to QuestDB")
    parser.add_argument("--ilp-host", default="127.0.0.1")
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--table", default="account_balances")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _request_json(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _ilp_line(table: str, currency: str, total: Decimal, free: Decimal, locked: Decimal) -> str:
    tags = f"venue=BYBIT,currency={currency}"
    fields = f"total={total},free={free},locked={locked}"
    return f"{table},{tags} {fields}\n"


def _to_decimal(value: str | None) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def main() -> None:
    args = parse_args()
    api_key = os.getenv("BYBIT_API_KEY_SENTINEL") or os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET_SENTINEL") or os.getenv("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        raise SystemExit("BYBIT_API_KEY_SENTINEL/BYBIT_API_SECRET_SENTINEL not set")

    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    params = "accountType=UNIFIED"
    sign_payload = timestamp + api_key + recv_window + params
    signature = _sign(api_secret, sign_payload)

    url = f"https://api.bybit.com/v5/account/wallet-balance?{params}"
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
    }

    data = _request_json(url, headers)
    result = data.get("result", {}).get("list", [])
    if not result:
        raise SystemExit("No balances returned from Bybit")

    coins = result[0].get("coin", [])

    ilp_cfg = QuestDbILPConfig(host=args.ilp_host, port=args.ilp_port, table=args.table)

    sock: socket.socket | None = None
    if not args.dry_run:
        sock = socket.create_connection((ilp_cfg.host, ilp_cfg.port), timeout=10)

    total = 0
    for coin in coins:
        currency = coin.get("coin", "")
        total_balance = _to_decimal(coin.get("walletBalance"))
        free_balance = _to_decimal(coin.get("availableToWithdraw"))
        locked_balance = total_balance - free_balance

        line = _ilp_line(ilp_cfg.table, currency, total_balance, free_balance, locked_balance)
        total += 1
        if args.dry_run:
            print(line, end="")
        else:
            sock.sendall(line.encode("utf-8"))

    if sock:
        sock.close()

    print(f"Wrote {total:,} balance records into {ilp_cfg.table}")


if __name__ == "__main__":
    main()
