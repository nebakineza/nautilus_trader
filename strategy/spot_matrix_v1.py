from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class SpotMatrixConfig(StrategyConfig, frozen=True, kw_only=True):
    instrument_ids: list[InstrumentId]
    base_currency: str = "USDT"

    bar_interval: str = "1-MINUTE"
    bar_price_type: str = "MID"
    bar_source: str = "EXTERNAL"

    rebalance_interval_mins: int = 60
    lookback_window: int = 500
    min_rebalance_bps: Decimal = Decimal("50")
    order_size_chunk_usd: Decimal = Decimal("20")


class SpotMatrix(Strategy):
    def __init__(self, config: SpotMatrixConfig) -> None:
        super().__init__(config)

        self.history: dict[str, list[float]] = {str(i): [] for i in config.instrument_ids}
        self.latest_price: dict[str, Decimal] = {}
        self.target_weights: dict[str, float] = {}
        self.last_rebalance_ts: float = 0.0
        self._instruments: dict[str, Instrument] = {}

    def on_start(self) -> None:
        self.log.info(
            f"SpotMatrix Initialized. Tracking {len(self.config.instrument_ids)} assets.",
            LogColor.GREEN,
        )

        for instrument_id in self.config.instrument_ids:
            self._instruments[str(instrument_id)] = self.cache.instrument(instrument_id)
            bar_type = BarType.from_str(
                f"{instrument_id}-{self.config.bar_interval}-{self.config.bar_price_type}-{self.config.bar_source}"
            )
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        symbol = str(bar.bar_type.instrument_id)

        if symbol in self.history:
            self.history[symbol].append(float(bar.close))
            if len(self.history[symbol]) > self.config.lookback_window:
                self.history[symbol].pop(0)
            self.latest_price[symbol] = bar.close.as_decimal()

        now = self._bar_ts_seconds(bar)
        if now - self.last_rebalance_ts < (self.config.rebalance_interval_mins * 60):
            return

        if not self._has_sufficient_history():
            return

        self._run_optimization_cycle()
        self.last_rebalance_ts = now

    def _bar_ts_seconds(self, bar: Bar) -> float:
        ts_event = getattr(bar, "ts_event", None)
        if ts_event is None:
            return time.time()
        return float(ts_event) / 1_000_000_000.0

    def _has_sufficient_history(self) -> bool:
        for values in self.history.values():
            if len(values) < self.config.lookback_window:
                return False
        return True

    def _run_optimization_cycle(self) -> None:
        self.log.info("Running SpotMatrix optimization...", LogColor.CYAN)

        df = pd.DataFrame(self.history)
        returns = np.log(df / df.shift(1)).dropna()
        if returns.empty:
            return

        volatility = returns.std()
        inv_vol = 1.0 / volatility
        sum_inv = inv_vol.sum()
        if sum_inv == 0:
            return

        self.target_weights = (inv_vol / sum_inv).to_dict()
        self._execute_rebalance()

    def _execute_rebalance(self) -> None:
        account = self.cache.account_for_venue(self.config.instrument_ids[0].venue)
        if account is None:
            accounts = self.cache.accounts()
            if accounts:
                account = next(iter(accounts.values()))
        if account is None:
            return

        total_equity_usd = Decimal("0")
        current_values: dict[str, Decimal] = {}

        base_balance = account.balance_total(self._instruments[str(self.config.instrument_ids[0])].quote_currency)
        if base_balance is not None:
            total_equity_usd += base_balance.as_decimal()

        for instrument_id in self.config.instrument_ids:
            key = str(instrument_id)
            instrument = self._instruments.get(key)
            price = self.latest_price.get(key)
            if instrument is None or price is None:
                continue

            balance = account.balance_total(instrument.base_currency)
            if balance is None:
                continue
            qty = balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)
            value = qty * price
            current_values[key] = value
            total_equity_usd += value

        if total_equity_usd <= 0:
            return

        for instrument_id in self.config.instrument_ids:
            key = str(instrument_id)
            instrument = self._instruments.get(key)
            price = self.latest_price.get(key)
            if instrument is None or price is None:
                continue

            target_pct = Decimal(str(self.target_weights.get(key, 0.0)))
            target_usd = total_equity_usd * target_pct
            current_usd = current_values.get(key, Decimal("0"))
            diff_usd = target_usd - current_usd

            drift_bps = (abs(diff_usd) / total_equity_usd) * Decimal("10000")
            if drift_bps < self.config.min_rebalance_bps:
                continue

            self._execute_rebalance_leg(instrument_id, instrument, price, diff_usd)

    def _execute_rebalance_leg(
        self,
        instrument_id: InstrumentId,
        instrument: Instrument,
        price: Decimal,
        diff_usd: Decimal,
    ) -> None:
        remaining = diff_usd
        chunk = self.config.order_size_chunk_usd

        while abs(remaining) > Decimal("0"):
            slice_usd = remaining
            if abs(remaining) > chunk:
                slice_usd = chunk if remaining > 0 else -chunk

            side = OrderSide.BUY if slice_usd > 0 else OrderSide.SELL
            qty_dec = abs(slice_usd) / price
            qty = instrument.make_qty(qty_dec)
            if qty.as_decimal() <= 0:
                break

            self.log.info(
                f"REBALANCE {instrument_id.symbol.value}: diff ${slice_usd:.2f}",
                LogColor.YELLOW,
            )
            self.submit_order(
                self.order_factory.market(
                    instrument_id=instrument_id,
                    order_side=side,
                    quantity=qty,
                    time_in_force=TimeInForce.GTC,
                )
            )

            remaining -= slice_usd
            if abs(remaining) < Decimal("0.01"):
                break
