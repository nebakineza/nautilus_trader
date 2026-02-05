#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


@dataclass
class LedgerState:
    inventory: Decimal = Decimal("0")
    wap: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")

    def on_fill(
        self,
        side: str,
        price: Decimal,
        qty: Decimal,
        fee: Decimal,
        fee_asset: str,
        base_asset: str,
        quote_asset: str,
    ) -> None:
        fee_cost = fee
        if fee_asset != quote_asset and quote_asset == "USDT" and fee_asset == base_asset:
            fee_cost = fee * price
        self.fees_paid += fee_cost
        self.realized_pnl -= fee_cost

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


def _parse_instrument(inst: str) -> Tuple[str, str]:
    symbol = inst.split("-")[0]
    if symbol.endswith("USDT"):
        return symbol.replace("USDT", ""), "USDT"
    return symbol[:-3], symbol[-3:]


def _iter_fills(
    log_path: Path,
    start_ts: Optional[datetime],
    end_ts: Optional[datetime],
) -> Iterable[Tuple[datetime, str, str, Decimal, Decimal, str, Decimal, str]]:
    ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)")
    payload_re = re.compile(r"OrderFilled\((.*)\)")

    def get(payload: str, field: str) -> Optional[str]:
        fm = re.search(rf"{field}=([^,]+)", payload)
        return fm.group(1) if fm else None

    with log_path.open() as f:
        for line in f:
            if "OrderFilled" not in line:
                continue
            tm = ts_re.match(line)
            if not tm:
                continue
            ts = datetime.fromisoformat(tm.group(1).replace("Z", "+00:00"))
            if start_ts and ts < start_ts:
                continue
            if end_ts and ts > end_ts:
                continue
            pm = payload_re.search(line)
            if not pm:
                continue
            payload = pm.group(1)
            inst = get(payload, "instrument_id")
            side = get(payload, "order_side")
            qty = get(payload, "last_qty")
            price = get(payload, "last_px")
            fee = get(payload, "commission")
            if not inst or not side or not qty or not price or not fee:
                continue

            price_val, price_ccy = price.split()
            fee_val, fee_ccy = fee.split()
            yield (
                ts,
                inst,
                side,
                Decimal(qty),
                Decimal(price_val.replace("_", "")),
                price_ccy,
                Decimal(fee_val),
                fee_ccy,
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--start", default=None, help="ISO timestamp, e.g. 2026-01-25T00:00:00Z")
    parser.add_argument("--end", default=None, help="ISO timestamp, e.g. 2026-01-31T23:59:59Z")
    args = parser.parse_args()

    start_ts = datetime.fromisoformat(args.start.replace("Z", "+00:00")) if args.start else None
    end_ts = datetime.fromisoformat(args.end.replace("Z", "+00:00")) if args.end else None

    ledgers: Dict[str, LedgerState] = {}
    last_price: Dict[str, Decimal] = {}

    for ts, inst, side, qty, price, price_ccy, fee, fee_ccy in _iter_fills(
        Path(args.log), start_ts, end_ts
    ):
        base, quote = _parse_instrument(inst)
        if quote != "USDT":
            continue
        ledger = ledgers.setdefault(inst, LedgerState())
        ledger.on_fill(side, price, qty, fee, fee_ccy, base, quote)
        last_price[inst] = price

    total_realized = Decimal("0")
    total_unrealized = Decimal("0")
    total_fees = Decimal("0")

    for inst, ledger in ledgers.items():
        mark = last_price.get(inst, Decimal("0"))
        unrealized = Decimal("0")
        if ledger.inventory > 0:
            unrealized = (mark - ledger.wap) * ledger.inventory
        elif ledger.inventory < 0:
            unrealized = (ledger.wap - mark) * abs(ledger.inventory)

        total_realized += ledger.realized_pnl
        total_unrealized += unrealized
        total_fees += ledger.fees_paid

        print(inst)
        print(f"  inventory: {ledger.inventory}")
        print(f"  wap: {ledger.wap}")
        print(f"  realized_pnl: {ledger.realized_pnl}")
        print(f"  unrealized_pnl: {unrealized}")
        print(f"  total_pnl: {ledger.realized_pnl + unrealized}")
        print(f"  fees_paid: {ledger.fees_paid}")

    print("TOTAL")
    print(f"  realized_pnl: {total_realized}")
    print(f"  unrealized_pnl: {total_unrealized}")
    print(f"  total_pnl: {total_realized + total_unrealized}")
    print(f"  fees_paid: {total_fees}")


if __name__ == "__main__":
    main()
