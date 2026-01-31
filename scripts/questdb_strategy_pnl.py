#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from decimal import Decimal
from urllib.parse import quote
import urllib.request

from strategy.metrics.questdb_writer import QuestDbILPWriter

STRATEGIES = ["LLMMv3", "StatArb"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOTUSDT", "LINKUSDT", "OPUSDT"]


def _qdb(sql: str) -> dict:
    url = f"http://127.0.0.1:9000/exec?query={quote(sql)}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _get_rows(sql: str) -> list[list]:
    data = _qdb(sql)
    return data.get("dataset") or []


def _get_prices() -> dict[str, Decimal]:
    prices = {"USDT": Decimal("1")}
    for symbol in SYMBOLS:
        with urllib.request.urlopen(
            f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}",
            timeout=10,
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        last = payload["result"]["list"][0]["lastPrice"]
        prices[symbol.replace("USDT", "")] = Decimal(str(last))
    return prices


def _calc_pnl(rows: list[list], prices: dict[str, Decimal]) -> dict[str, Decimal]:
    cash_pnl = Decimal("0")
    commission = Decimal("0")
    net_qty = defaultdict(Decimal)

    for symbol, side, qty, price, fee in rows:
        qty_d = Decimal(str(qty))
        price_d = Decimal(str(price))
        if side == "BUY":
            cash_pnl -= qty_d * price_d
            net_qty[symbol] += qty_d
        else:
            cash_pnl += qty_d * price_d
            net_qty[symbol] -= qty_d
        if fee is not None:
            commission += Decimal(str(fee))

    inv_value = Decimal("0")
    for symbol, qty in net_qty.items():
        if symbol.endswith("USDT"):
            coin = symbol.replace("USDT", "")
        else:
            coin = symbol
        inv_value += qty * prices.get(coin, Decimal("0"))

    total = cash_pnl + inv_value - commission
    return {
        "cash_pnl": cash_pnl,
        "commission": commission,
        "inventory_value": inv_value,
        "total_pnl": total,
    }


def main() -> None:
    writer = QuestDbILPWriter.from_env("strategy_pnl")
    window_hours = int(os.getenv("PNL_WINDOW_HOURS", "24"))
    interval_secs = int(os.getenv("PNL_INTERVAL_SECS", "60"))

    while True:
        try:
            prices = _get_prices()
            since = f"now() - {window_hours}h"

            for strategy in STRATEGIES:
                rows = _get_rows(
                    "select symbol, side, qty, price, commission "
                    "from live_fills "
                    f"where (strategy='{strategy}' or source='{strategy}') "
                    f"and (timestamp > {since} or timestamp is null)"
                )
                if not rows:
                    continue

                pnl = _calc_pnl(rows, prices)
                writer.send(
                    table="live_strategy_pnl",
                    tags={"strategy": strategy},
                    fields={
                        "cash_pnl": pnl["cash_pnl"],
                        "commission": pnl["commission"],
                        "inventory_value": pnl["inventory_value"],
                        "total_pnl": pnl["total_pnl"],
                        "window_hours": window_hours,
                    },
                )
        except Exception:
            pass

        time.sleep(interval_secs)


if __name__ == "__main__":
    main()
