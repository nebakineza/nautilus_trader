#!/usr/bin/env python3
"""Print a quick performance summary from QuestDB."""

from __future__ import annotations

import json
from decimal import Decimal
from urllib.parse import quote
import urllib.request


def _qdb(sql: str) -> dict:
    url = f"http://127.0.0.1:9000/exec?query={quote(sql)}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _latest_pnl() -> list[list]:
    data = _qdb(
        "select strategy, last(total_pnl) as total_pnl, last(commission) as commission, "
        "last(inventory_value) as inventory_value, last(cash_pnl) as cash_pnl "
        "from live_strategy_pnl group by strategy"
    )
    return data.get("dataset") or []


def _latest_inventory() -> list[list]:
    data = _qdb(
        "select symbol, last(base_ratio) as base_ratio, last(ratio_drift) as ratio_drift, "
        "last(equity_usdt) as equity_usdt "
        "from live_inventory_metrics group by symbol"
    )
    return data.get("dataset") or []


def main() -> None:
    pnl_rows = _latest_pnl()
    inv_rows = _latest_inventory()

    print("\nStrategy P&L:")
    for strategy, total_pnl, commission, inv_value, cash_pnl in pnl_rows:
        print(
            f"- {strategy}: total={total_pnl}, cash={cash_pnl}, inv={inv_value}, commission={commission}"
        )

    print("\nInventory Ratios:")
    for symbol, base_ratio, ratio_drift, equity_usdt in inv_rows:
        print(
            f"- {symbol}: ratio={base_ratio}, drift={ratio_drift}, equity={equity_usdt}"
        )


if __name__ == "__main__":
    main()
