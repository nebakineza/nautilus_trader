#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote
import urllib.request

from strategy.metrics.questdb_writer import QuestDbILPWriter

STRATEGIES = ["LLMMv3", "LLMMv3Primer", "StatArb"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOTUSDT", "LINKUSDT", "OPUSDT", "DOGEUSDT", "AVAXUSDT"]


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


@dataclass
class WapLedger:
    inventory: Decimal = Decimal("0")
    wap: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")

    def on_fill(self, side: str, price: Decimal, qty: Decimal, fee: Decimal) -> None:
        self.fees_paid += fee
        self.realized_pnl -= fee

        if side == "BUY":
            if self.inventory >= 0:
                total_cost = (self.inventory * self.wap) + (qty * price)
                self.inventory += qty
                if self.inventory != 0:
                    self.wap = total_cost / self.inventory
            else:
                remaining_short = abs(self.inventory)
                if qty <= remaining_short:
                    pnl = (self.wap - price) * qty
                    self.realized_pnl += pnl
                    self.inventory += qty
                else:
                    pnl = (self.wap - price) * remaining_short
                    self.realized_pnl += pnl
                    excess_qty = qty - remaining_short
                    self.inventory = excess_qty
                    self.wap = price
        elif side == "SELL":
            if self.inventory <= 0:
                total_cost = (abs(self.inventory) * self.wap) + (qty * price)
                self.inventory -= qty
                if self.inventory != 0:
                    self.wap = total_cost / abs(self.inventory)
            else:
                if qty <= self.inventory:
                    pnl = (price - self.wap) * qty
                    self.realized_pnl += pnl
                    self.inventory -= qty
                else:
                    pnl = (price - self.wap) * self.inventory
                    self.realized_pnl += pnl
                    excess_qty = qty - self.inventory
                    self.inventory = -excess_qty
                    self.wap = price


def _symbol_to_coin(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return symbol.replace("USDT", "")
    return symbol


def _calc_pnl(rows: list[list], prices: dict[str, Decimal]) -> dict[str, Decimal]:
    ledgers: dict[str, WapLedger] = {}

    for symbol, side, qty, price, fee in rows:
        qty_d = Decimal(str(qty))
        price_d = Decimal(str(price))
        fee_d = Decimal(str(fee)) if fee is not None else Decimal("0")
        ledger = ledgers.setdefault(symbol, WapLedger())
        ledger.on_fill(side, price_d, qty_d, fee_d)

    inventory_value = Decimal("0")
    unrealized_pnl = Decimal("0")
    realized_pnl = Decimal("0")
    commission = Decimal("0")

    for symbol, ledger in ledgers.items():
        coin = _symbol_to_coin(symbol)
        mark = prices.get(coin, Decimal("0"))
        if ledger.inventory > 0:
            unrealized_pnl += (mark - ledger.wap) * ledger.inventory
        elif ledger.inventory < 0:
            unrealized_pnl += (ledger.wap - mark) * abs(ledger.inventory)
        inventory_value += ledger.inventory * mark
        realized_pnl += ledger.realized_pnl
        commission += ledger.fees_paid

    total = realized_pnl + unrealized_pnl
    return {
        "cash_pnl": realized_pnl,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "commission": commission,
        "inventory_value": inventory_value,
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
                        "realized_pnl": pnl["realized_pnl"],
                        "unrealized_pnl": pnl["unrealized_pnl"],
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
