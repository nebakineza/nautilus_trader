"""Spot Grid Advanced v001 - Dynamic grid with trailing and optional continuous trading.

This is an original NautilusTrader implementation inspired by public SpotGridAdvanced
behavior: dynamic buy/sell targets, trailing entries/exits, optional trend gating,
and continuous trading (partial sells on local recoveries).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Final

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy

BPS_MULTIPLIER: Final[float] = 10000.0


class SpotGridAdvancedConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for Spot Grid Advanced v001."""

    instrument_id: InstrumentId

    # Balance and sizing
    trading_limit: float = 20.0  # quote amount per base buy
    tl_multiplier: float = 1.0
    max_buy_count: int = 15
    min_volume_to_sell: float = 10.0
    min_order_value_usd: float = 5.50
    max_investment: float = 1_000_000.0
    funds_reserve: float = 0.0
    keep_quote: float = 0.0
    quote_budget_pct: float = 1.0  # Fraction of USDT balance this strategy may use (0.0-1.0)

    # Targeting
    period: str = "15m"
    auto_gain: bool = True
    gain_pct: float = 0.5
    fee_bps: float = 20.0
    grid_multiplier: float = 1.0
    trailing_multiplier: float = 1.0
    min_step_pct: float = 0.3
    unit_cost: bool = True

    # Bar config
    bar_price_type: str = "LAST"
    bar_source: str = "EXTERNAL"

    # Continuous trading (partial sells)
    ct_enabled: bool = True
    start_cont_trading: int = 3
    ct_tl_multiplier: float = 0.5
    ct_restart_multiplier: float = 1.0

    # Trend options (simple SMA gate)
    trend_open: bool = False
    trend_block_dca: bool = False
    trend_lower_dca: bool = False
    trend_grid_multiplier: float = 2.0
    trend_ct_multiplier: float = 2.0
    sma_period: int = 50
    trend_fast_period: str = "15m"
    trend_slow_period: str = "4h"

    # Data
    price_lookback: int = 200

    # Execution
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = True

    # Advanced controls
    buy_enabled: bool = True
    sell_enabled: bool = True
    stop_after_sell: bool = False
    ignore_trades_before_ms: int = 0

    # Logging
    log_events: bool = True


class SpotGridAdvanced(Strategy):
    """Dynamic grid with trailing entries/exits and optional continuous trading."""

    def __init__(self, config: SpotGridAdvancedConfig) -> None:
        super().__init__(config)
        self.instrument_id = config.instrument_id
        self.instrument: Instrument | None = None

        self.prices: deque[float] = deque(maxlen=max(50, config.price_lookback))
        self._sma_window: deque[float] = deque(maxlen=max(10, config.sma_period))
        self._bar_closes_main: deque[float] = deque(maxlen=max(10, config.sma_period * 2))
        self._bar_highs_main: deque[float] = deque(maxlen=max(10, config.sma_period * 2))
        self._bar_lows_main: deque[float] = deque(maxlen=max(10, config.sma_period * 2))
        self._bar_closes_fast: deque[float] = deque(maxlen=max(10, config.sma_period * 2))
        self._bar_closes_slow: deque[float] = deque(maxlen=max(10, config.sma_period * 2))

        self._bar_type_main: BarType | None = None
        self._bar_type_fast: BarType | None = None
        self._bar_type_slow: BarType | None = None

        self._buy_count: int = 0
        self._position_qty: float = 0.0
        self._avg_cost: float = 0.0
        self._last_buy_price: float = 0.0
        self._last_buy_qty: float = 0.0
        self._total_buy_cost: float = 0.0
        self._total_buy_qty: float = 0.0

        self._available_base: float = 0.0
        self._available_quote: float = 0.0

        self._pending_buy: Order | None = None
        self._pending_sell: Order | None = None
        self._pending_ct_sell: Order | None = None
        self._pending_ct_buy: Order | None = None
        self._last_ct_sell_price: float = 0.0
        self._last_ct_sell_qty: float = 0.0
        self._stop_after_sell_armed: bool = False

        self._trailing_buy_active: bool = False
        self._trailing_buy_low: float = 0.0
        self._trailing_sell_active: bool = False
        self._trailing_sell_high: float = 0.0
        self._last_mid: float = 0.0
        self._ignore_before_ns: int = 0
        self._startup_buy_done: bool = False

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found")
            self.stop()
            return

        self.subscribe_quote_ticks(self.instrument_id)
        self._subscribe_bars()
        self._initialize_balance()
        if self.config.ignore_trades_before_ms > 0:
            self._ignore_before_ns = self.config.ignore_trades_before_ms * 1_000_000

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if tick.instrument_id != self.instrument_id:
            return

        now_ns = self.clock.timestamp_ns()
        if self._ignore_before_ns and now_ns < self._ignore_before_ns:
            return

        mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
        self._last_mid = mid
        self.prices.append(mid)
        self._sma_window.append(mid)

        if self._pending_ct_buy is not None:
            return

        if self._pending_buy or self._pending_sell or self._pending_ct_sell:
            return

        step_pct = self._calc_step_pct()
        gain_pct = self._calc_gain_pct()
        trail_pct = step_pct * self.config.trailing_multiplier

        if self._position_qty <= 0.0:
            if not self.config.buy_enabled:
                return
            if self.config.trend_open and not self._is_bullish():
                return
            if self._available_base <= 0.0 and not self._startup_buy_done:
                self._place_market_buy(mid)
                self._startup_buy_done = True
            else:
                self._handle_entry(mid, trail_pct)
            return

        # DCA logic
        if self._buy_count < self.config.max_buy_count and self.config.buy_enabled:
            if not (self.config.trend_block_dca and not self._is_bullish()):
                dca_step = step_pct * self.config.grid_multiplier
                if self.config.trend_lower_dca and not self._is_bullish():
                    dca_step *= self.config.trend_grid_multiplier
                target = self._last_buy_price * (1 - dca_step / 100)
                if mid <= target:
                    self._handle_entry(mid, trail_pct)
                    return

        # Continuous trading (partial sells on recoveries)
        if self.config.ct_enabled and self._ct_ready():
            if mid >= self._last_buy_price * (1 + gain_pct / 100):
                self._place_ct_sell(mid)
                return

        if self.config.ct_enabled and self._last_ct_sell_price > 0.0:
            ct_step = step_pct * self.config.ct_restart_multiplier
            if self.config.trend_lower_dca and not self._is_bullish():
                ct_step *= self.config.trend_ct_multiplier
            if mid <= self._last_ct_sell_price * (1 - ct_step / 100):
                self._place_ct_buy(mid)
                return

        # Full exit
        if not self.config.sell_enabled:
            return

        if mid >= self._break_even_price() * (1 + gain_pct / 100):
            self._handle_exit(mid, trail_pct)

    def on_order_filled(self, event: OrderFilled) -> None:
        price = float(event.last_px)
        qty = float(event.last_qty)

        if event.order_side == OrderSide.BUY:
            self._position_qty += qty
            self._available_base += qty
            self._available_quote -= price * qty
            self._last_buy_price = price
            self._last_buy_qty = qty
            self._buy_count += 1
            self._avg_cost = self._calc_avg_cost(price, qty)
            self._total_buy_cost += price * qty
            self._total_buy_qty += qty
            self._pending_buy = None
            if self._pending_ct_buy is not None:
                self._pending_ct_buy = None
                self._last_ct_sell_price = 0.0
                self._last_ct_sell_qty = 0.0
        else:
            self._position_qty -= qty
            self._available_base -= qty
            self._available_quote += price * qty
            if self._pending_ct_sell is not None:
                self._pending_ct_sell = None
                self._last_ct_sell_price = price
                self._last_ct_sell_qty = qty
            else:
                self._pending_sell = None
                if self._position_qty <= 0:
                    self._reset_position()
                    if self.config.stop_after_sell:
                        self.stop()

        if self.config.log_events:
            self.log.info(
                f"FILL {event.order_side.name} {qty:.4f} @ {price:.4f} | pos={self._position_qty:.4f}",
                color=LogColor.GREEN if event.order_side == OrderSide.BUY else LogColor.MAGENTA,
            )

    # --- Core helpers ---
    def _handle_entry(self, mid: float, trail_pct: float) -> None:
        if not self._trailing_buy_active:
            self._trailing_buy_active = True
            self._trailing_buy_low = mid
        self._trailing_buy_low = min(self._trailing_buy_low, mid)
        trigger = self._trailing_buy_low * (1 + trail_pct / 100)
        if mid >= trigger:
            self._trailing_buy_active = False
            self._place_buy(mid)

    def _handle_exit(self, mid: float, trail_pct: float) -> None:
        if not self._trailing_sell_active:
            self._trailing_sell_active = True
            self._trailing_sell_high = mid
        self._trailing_sell_high = max(self._trailing_sell_high, mid)
        trigger = self._trailing_sell_high * (1 - trail_pct / 100)
        if mid <= trigger:
            self._trailing_sell_active = False
            self._place_sell(mid, self._position_qty)

    def _place_buy(self, price: float) -> None:
        if self.instrument is None:
            return
        reserved = max(self.config.funds_reserve, self.config.keep_quote)
        if self._available_quote - reserved < self.config.trading_limit:
            return
        if self._available_quote < self.config.trading_limit:
            return
        if self._quote_invested() + self.config.trading_limit > self.config.max_investment:
            return

        qty = (self.config.trading_limit * (self.config.tl_multiplier ** max(0, self._buy_count))) / price
        if qty * price < self.config.min_order_value_usd:
            return

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(qty),
            price=self.instrument.make_price(price),
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
        )
        self.submit_order(order)
        self._pending_buy = order

    def _place_sell(self, price: float, qty: float) -> None:
        if self.instrument is None or qty <= 0:
            return
        if qty * price < max(self.config.min_order_value_usd, self.config.min_volume_to_sell):
            return

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.instrument.make_qty(qty),
            price=self.instrument.make_price(price),
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
        )
        self.submit_order(order)
        self._pending_sell = order

    def _place_ct_sell(self, price: float) -> None:
        if self.instrument is None:
            return
        if self._pending_ct_sell is not None:
            return
        if not self.config.sell_enabled:
            return
        qty = min(self._last_buy_qty * self.config.ct_tl_multiplier, self._position_qty)
        if qty * price < max(self.config.min_order_value_usd, self.config.min_volume_to_sell):
            return

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.instrument.make_qty(qty),
            price=self.instrument.make_price(price),
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
        )
        self.submit_order(order)
        self._pending_ct_sell = order

    def _place_ct_buy(self, price: float) -> None:
        if self.instrument is None:
            return
        if self._last_ct_sell_qty <= 0:
            return
        if self._pending_ct_buy is not None:
            return
        if not self.config.buy_enabled:
            return

        qty = self._last_ct_sell_qty
        if qty * price < self.config.min_order_value_usd:
            return
        reserved = max(self.config.funds_reserve, self.config.keep_quote)
        if self._available_quote - reserved < qty * price:
            return

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(qty),
            price=self.instrument.make_price(price),
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
        )
        self.submit_order(order)
        self._pending_ct_buy = order

    def _calc_avg_cost(self, price: float, qty: float) -> float:
        if self._position_qty <= 0:
            return price
        total_cost = (self._avg_cost * (self._position_qty - qty)) + (price * qty)
        return total_cost / max(self._position_qty, 1e-9)

    def _calc_step_pct(self) -> float:
        if len(self._bar_lows_main) >= 10 and len(self._bar_highs_main) >= 10:
            support = min(self._bar_lows_main)
            resistance = max(self._bar_highs_main)
        elif len(self.prices) >= 10:
            support = min(self.prices)
            resistance = max(self.prices)
        else:
            return self.config.min_step_pct

        range_pct = max((resistance - support) / max(support, 1e-9) * 100, 0.0)
        step_pct = max(self.config.min_step_pct, range_pct / 4)
        return step_pct

    def _calc_gain_pct(self) -> float:
        fee_min = (2 * self.config.fee_bps) / 100
        if not self.config.auto_gain:
            return max(self.config.gain_pct, fee_min)
        step_pct = self._calc_step_pct()
        return max(self.config.gain_pct, step_pct, fee_min)

    def _is_bullish(self) -> bool:
        if len(self._bar_closes_fast) < self.config.sma_period:
            return True
        if len(self._bar_closes_slow) < self.config.sma_period:
            return True
        fast_sma = sum(self._bar_closes_fast[-self.config.sma_period:]) / self.config.sma_period
        slow_sma = sum(self._bar_closes_slow[-self.config.sma_period:]) / self.config.sma_period
        return self._bar_closes_fast[-1] >= fast_sma and self._bar_closes_slow[-1] >= slow_sma

    def _break_even_price(self) -> float:
        if self.config.unit_cost:
            return self._avg_cost if self._avg_cost > 0 else self._last_buy_price
        if self._total_buy_qty > 0:
            return self._total_buy_cost / self._total_buy_qty
        return self._avg_cost if self._avg_cost > 0 else self._last_buy_price

    def _quote_invested(self) -> float:
        return self._avg_cost * self._position_qty

    def _reset_position(self) -> None:
        self._position_qty = 0.0
        self._avg_cost = 0.0
        self._last_buy_price = 0.0
        self._last_buy_qty = 0.0
        self._buy_count = 0
        self._total_buy_cost = 0.0
        self._total_buy_qty = 0.0
        self._trailing_buy_active = False
        self._trailing_sell_active = False
        self._pending_ct_buy = None
        self._pending_ct_sell = None
        self._pending_buy = None
        self._pending_sell = None
        self._last_ct_sell_price = 0.0
        self._last_ct_sell_qty = 0.0

    def _initialize_balance(self) -> None:
        self._refresh_balance()

    def _refresh_balance(self) -> None:
        """Read live balance from cache and apply quote_budget_pct."""
        try:
            accounts = self.cache.accounts()
            if not accounts:
                return
            account = accounts[0]
            balances = account.balances()
            base_currency = self.instrument.base_currency
            quote_currency = self.instrument.quote_currency
            base_balance = balances.get(base_currency)
            quote_balance = balances.get(quote_currency)
            if base_balance is not None:
                if hasattr(base_balance, "total"):
                    self._available_base = float(base_balance.total.as_decimal())
                elif hasattr(base_balance, "as_decimal"):
                    self._available_base = float(base_balance.as_decimal())
                else:
                    self._available_base = float(base_balance)
            raw_quote = 0.0
            if quote_balance is not None:
                if hasattr(quote_balance, "total"):
                    raw_quote = float(quote_balance.total.as_decimal())
                elif hasattr(quote_balance, "as_decimal"):
                    raw_quote = float(quote_balance.as_decimal())
                else:
                    raw_quote = float(quote_balance)
            # Apply budget percentage
            budget_pct = max(0.01, min(1.0, self.config.quote_budget_pct))
            self._available_quote = raw_quote * budget_pct
            if self.config.log_events:
                pct_str = f" ({budget_pct:.0%} of {raw_quote:.2f})" if budget_pct < 1.0 else ""
                self.log.info(
                    f"Balance: {self._available_base:.4f} {base_currency} | {self._available_quote:.2f} {quote_currency}{pct_str}",
                    color=LogColor.BLUE,
                )
        except Exception as exc:
            self.log.warning(f"Balance refresh error: {exc}")

    def on_account_state(self, event) -> None:
        """Refresh available capital whenever Bybit pushes an account update."""
        self._refresh_balance()

    def _subscribe_bars(self) -> None:
        symbol = self.instrument_id.symbol.value.replace("-SPOT", "")
        main_period = self._normalize_period(self.config.period)
        fast_period = self._normalize_period(self.config.trend_fast_period)
        slow_period = self._normalize_period(self.config.trend_slow_period)

        self._bar_type_main = BarType.from_str(
            f"{symbol}-SPOT.BYBIT-{main_period}-{self.config.bar_price_type}-{self.config.bar_source}"
        )
        self._bar_type_fast = BarType.from_str(
            f"{symbol}-SPOT.BYBIT-{fast_period}-{self.config.bar_price_type}-{self.config.bar_source}"
        )
        self._bar_type_slow = BarType.from_str(
            f"{symbol}-SPOT.BYBIT-{slow_period}-{self.config.bar_price_type}-{self.config.bar_source}"
        )
        self.subscribe_bars(self._bar_type_main)
        self.subscribe_bars(self._bar_type_fast)
        self.subscribe_bars(self._bar_type_slow)

    def on_bar(self, bar: Bar) -> None:
        if self._bar_type_main and bar.bar_type == self._bar_type_main:
            self._bar_closes_main.append(float(bar.close))
            self._bar_highs_main.append(float(bar.high))
            self._bar_lows_main.append(float(bar.low))
        elif self._bar_type_fast and bar.bar_type == self._bar_type_fast:
            self._bar_closes_fast.append(float(bar.close))
        elif self._bar_type_slow and bar.bar_type == self._bar_type_slow:
            self._bar_closes_slow.append(float(bar.close))

    def _ct_ready(self) -> bool:
        if self.config.start_cont_trading <= 0:
            return True
        if self.config.trading_limit <= 0:
            return False
        return (self._quote_invested() / self.config.trading_limit) >= self.config.start_cont_trading

    def _normalize_period(self, period: str) -> str:
        mapping = {
            "1m": "1-MINUTE",
            "3m": "3-MINUTE",
            "5m": "5-MINUTE",
            "15m": "15-MINUTE",
            "30m": "30-MINUTE",
            "1h": "1-HOUR",
            "2h": "2-HOUR",
            "4h": "4-HOUR",
            "6h": "6-HOUR",
            "12h": "12-HOUR",
            "1d": "1-DAY",
        }
        return mapping.get(period, period)

    def _place_market_buy(self, price: float) -> None:
        if self.instrument is None:
            return
        if not self.config.buy_enabled:
            return
        reserved = max(self.config.funds_reserve, self.config.keep_quote)
        if self._available_quote - reserved < self.config.trading_limit:
            return
        qty = self.config.trading_limit / price
        if qty * price < self.config.min_order_value_usd:
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(qty),
            time_in_force=self.config.time_in_force,
        )
        self.submit_order(order)
        self._pending_buy = order
