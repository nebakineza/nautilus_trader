#!/usr/bin/env python3
"""Compute inventory ratio metrics from QuestDB balances and write to QuestDB.

Requires account_balances snapshots and live prices.
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from urllib.parse import quote
import urllib.request

from strategy.metrics.questdb_writer import QuestDbILPWriter

SYMBOLS = ["DOGEUSDT", "AVAXUSDT", "SOLUSDT"]


def _qdb(sql: str) -> dict:
    url = f"http://127.0.0.1:9000/exec?query={quote(sql)}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _get_latest_balances() -> dict[str, Decimal]:
    data = _qdb(
        "select currency, last(total) as total "
        "from account_balances where venue='BYBIT' group by currency"
    )
    rows = data.get("dataset") or []
    balances: dict[str, Decimal] = {}
    for currency, total in rows:
        if currency is None:
            continue
        balances[str(currency)] = Decimal(str(total))
    return balances


def _get_prices(symbols: list[str]) -> dict[str, Decimal]:
    prices = {"USDT": Decimal("1")}
    for symbol in symbols:
        with urllib.request.urlopen(
            f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}",
            timeout=10,
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        last = payload["result"]["list"][0]["lastPrice"]
        prices[symbol.replace("USDT", "")] = Decimal(str(last))
    return prices


def main() -> None:
    writer = QuestDbILPWriter.from_env("inventory_metrics")
    interval_secs = int(os.getenv("INVENTORY_METRICS_SECS", "300"))
    target_ratio = Decimal(os.getenv("INVENTORY_TARGET_RATIO", "0.50"))

    while True:
        try:
            balances = _get_latest_balances()
            prices = _get_prices(SYMBOLS)

            usdt_total = balances.get("USDT", Decimal("0"))
            for symbol in SYMBOLS:
                coin = symbol.replace("USDT", "")
                qty = balances.get(coin, Decimal("0"))
                price = prices.get(coin, Decimal("0"))
                base_value = qty * price
                equity = usdt_total + base_value
                if equity <= Decimal("0"):
                    continue

                base_ratio = base_value / equity
                drift = base_ratio - target_ratio

                writer.send(
                    table="live_inventory_metrics",
                    tags={"venue": "BYBIT", "symbol": symbol},
                    fields={
                        "equity_usdt": equity,
                        "usdt_value": usdt_total,
                        "base_value": base_value,
                        "base_ratio": base_ratio,
                        "target_ratio": target_ratio,
                        "ratio_drift": drift,
                    },
                )
        except Exception:
            pass

        time.sleep(interval_secs)


if __name__ == "__main__":
    main()
