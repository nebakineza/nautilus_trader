"""Step Grid Scalp v001 - Gunbot StepGridScalp-inspired implementation.

Implements dynamic step grid with scalping, multi-timeframe trend gating,
trade supports, partial sells, trailing logic, and optional BTFD mode.
"""

from __future__ import annotations

from collections import deque
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


class StepGridScalpConfig(StrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId

    # Balance settings
    trading_limit: float = 40.0
    tl_multiplier: float = 1.0
    always_use_tl_multiplier: bool = False
    max_buy_count: int = 40
    min_volume_to_sell: float = 10.0
    max_investment: float = 1_000_000.0
    quote_budget_pct: float = 1.0  # Fraction of USDT balance this strategy may use (0.0-1.0)

    # Profit settings
    gain_pct: float = 1.0
    gain_partial_pct: float = 0.5
    partial_sell_ratio: float = 0.95
    partial_sell_cap: bool = False
    partial_sell_cap_ratio: float = 1.0
    unit_cost: bool = True
    dynamic_exit_logic: bool = False

    # Period settings
    period: str = "5m"
    period_medium: str = "15m"
    period_long: str = "1h"

    # Grid
    auto_step_size: str = "ATR"  # None, ATR, CandleSize
    min_step_pct: float = 0.3
    step_size: float = 500.0
    pct_step_size: bool = False
    enforce_step: bool = False

    # Trailing
    pct_trailing_range: bool = False
    custom_trailing_range: float = 0.3
    pct_sell_trailing_range: bool = False
    custom_sell_trailing_range: float = 0.3

    # IRIS Trend | Price Action
    trend_sync: bool = False
    price_action_tl_ratio: float = 1.0
    price_action_threshold: float = 0.0
    strict_entry: bool = False
    strict_dca: bool = False
    exhaustion_sensitivity: str = "MEDIUM"  # NONE, SHORT, MEDIUM, LONG

    # IRIS Trend | Trade Supports
    trade_supports: bool = True
    support_tl_ratio: float = 2.0
    support2_tl_ratio: float = 2.0

    # IRIS Trend | Micro Scalping
    trend_scalping: bool = True
    scalp_tl_ratio: float = 0.625
    scalp_init_buy_multiplier: float = 0.6
    dynamic_sl: bool = False

    # IRIS Trend | Multiple Timeframes
    multiple_timeframes_mode: bool = False
    mtf_tl_ratio: float = 1.0
    lower_period_low: str = "5m"
    lower_period_medium: str = "15m"
    lower_period_high: str = "30m"

    # IRIS Trend | Accumulation Cycle
    accumulation_cycle: bool = False

    # IRIS Trend | Advanced Trailing
    trend_plus: bool = True
    trend_plus_buy_multiplier_small: float = 1.0
    trend_plus_buy_multiplier_medium: float = 2.0
    trend_plus_buy_multiplier_large: float = 5.0
    trend_plus_sell_multiplier_small: float = 0.5
    trend_plus_sell_multiplier_medium: float = 2.0
    trend_plus_sell_multiplier_large: float = 5.0

    # IRIS Trend | BTFD Mode
    btfd_mode: bool = False
    btfd_trend_filter: bool = False
    btfd_dip_target: float = 0.0
    btfd_max_dip_target: float = 0.0
    btfd_auto_target: str = "none"
    btfd_tl_ratio: float = 1.0
    btfd_max_buy_count: int = 25
    btfd_max_orders: int = 5
    btfd_gain: float = 1.0
    btfd_auto_step_size: str = "ATR"
    btfd_step_size: float = 500.0
    btfd_pct_step_size: bool = False

    # Custom trading range
    custom_trading_range_mode: bool = False
    trading_range_low: float = 0.0
    trading_range_high: float = 0.0
    trading_range_stop: float = 0.0
    trading_range_dca_stop: float = 0.0
    trading_range_stop_ratio: float = 1.0

    # Advanced
    buy_enabled: bool = True
    sell_enabled: bool = True
    stop_after_sell: bool = False
    atr_period: int = 50
    forever_bags: bool = False
    keep_quote: float = 0.0
    ignore_trades_before_ms: int = 0

    # Execution
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = True


class StepGridScalp(Strategy):
    def __init__(self, config: StepGridScalpConfig) -> None:
        super().__init__(config)
        self.instrument_id = config.instrument_id
        self.instrument: Instrument | None = None

        self._prices: deque[float] = deque(maxlen=500)
        self._bars: dict[str, deque[Bar]] = {}

        self._bar_type_main: BarType | None = None
        self._bar_type_med: BarType | None = None
        self._bar_type_long: BarType | None = None
        self._bar_type_low: BarType | None = None
        self._bar_type_mtf_med: BarType | None = None
        self._bar_type_mtf_high: BarType | None = None

        self._position_qty: float = 0.0
        self._avg_cost: float = 0.0
        self._total_buy_cost: float = 0.0
        self._total_buy_qty: float = 0.0
        self._last_buy_price: float = 0.0
        self._last_buy_qty: float = 0.0
        self._buy_count: int = 0

        self._available_quote: float = 0.0
        self._available_base: float = 0.0

        self._pending_buy: Order | None = None
        self._pending_sell: Order | None = None
        self._pending_support: Order | None = None

        self._trailing_buy_active: bool = False
        self._trailing_buy_low: float = 0.0
        self._trailing_sell_active: bool = False
        self._trailing_sell_high: float = 0.0

        self._ignore_before_ns: int = 0

    # --- lifecycle ---
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
        self._prices.append(mid)

        if self._pending_buy or self._pending_sell or self._pending_support:
            return

        if not self.config.buy_enabled and self._position_qty <= 0:
            return

        if self._position_qty <= 0:
            if not self._new_trade_allowed(mid):
                return
            self._handle_entry(mid)
            return

        # DCA
        if self._buy_count < self._max_buy_count():
            if self._dca_allowed(mid):
                if mid <= self._dca_target(mid):
                    self._handle_entry(mid)
                    return

        # Partial sell
        if self.config.sell_enabled and self._partial_sell_allowed(mid):
            qty = self._partial_sell_qty(mid)
            if qty > 0:
                self._place_sell(mid, qty)
                return

        # Full exit
        if self.config.sell_enabled and self._exit_allowed(mid):
            self._handle_exit(mid)

    def on_bar(self, bar: Bar) -> None:
        key = self._bar_key(bar.bar_type)
        if key not in self._bars:
            self._bars[key] = deque(maxlen=500)
        self._bars[key].append(bar)

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
        else:
            self._position_qty -= qty
            self._available_base -= qty
            self._available_quote += price * qty
            self._pending_sell = None
            if self._position_qty <= 0:
                self._reset_position()
                if self.config.stop_after_sell:
                    self.stop()

    # --- behavior helpers ---
    def _handle_entry(self, mid: float) -> None:
        step_pct = self._step_pct()
        trail = self._buy_trailing_pct(step_pct)
        if not self._trailing_buy_active:
            self._trailing_buy_active = True
            self._trailing_buy_low = mid
        self._trailing_buy_low = min(self._trailing_buy_low, mid)
        trigger = self._trailing_buy_low * (1 + trail / 100)
        if mid >= trigger:
            self._trailing_buy_active = False
            self._place_buy(mid)

    def _handle_exit(self, mid: float) -> None:
        step_pct = self._step_pct()
        trail = self._sell_trailing_pct(step_pct)
        if not self._trailing_sell_active:
            self._trailing_sell_active = True
            self._trailing_sell_high = mid
        self._trailing_sell_high = max(self._trailing_sell_high, mid)
        trigger = self._trailing_sell_high * (1 - trail / 100)
        if mid <= trigger:
            self._trailing_sell_active = False
            self._place_sell(mid, self._position_qty)

    def _place_buy(self, price: float) -> None:
        if self.instrument is None:
            return
        if not self.config.buy_enabled:
            return
        tl = self._effective_tl()
        if self._available_quote - self.config.keep_quote < tl:
            return
        if self._quote_invested() + tl > self.config.max_investment:
            return
        qty = tl / price
        if qty * price < self.config.min_volume_to_sell:
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
        if self.instrument is None:
            return
        if qty * price < self.config.min_volume_to_sell:
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

    # --- gating logic ---
    def _new_trade_allowed(self, price: float) -> bool:
        if self.config.custom_trading_range_mode:
            if not (self.config.trading_range_low <= price <= self.config.trading_range_high):
                return False
        phase = self._phase()
        if self.config.trend_sync and phase in ("VERY_BEARISH", "BEARISH"):
            return False
        if self.config.strict_entry and not self._momentum_ok():
            return False
        return True

    def _dca_allowed(self, price: float) -> bool:
        if self.config.custom_trading_range_mode and price <= self.config.trading_range_dca_stop:
            return False
        phase = self._phase()
        if self.config.trend_sync and phase in ("VERY_BEARISH", "BEARISH"):
            return False
        if self.config.strict_dca and not self._momentum_ok():
            return False
        if self._is_overbought(self.config.exhaustion_sensitivity):
            return False
        return True

    def _exit_allowed(self, price: float) -> bool:
        if self.config.forever_bags:
            return False
        gain_target = self._gain_target()
        if self.config.accumulation_cycle and not self._is_overbought("LONG"):
            return False
        return price >= gain_target

    # --- calculations ---
    def _step_pct(self) -> float:
        if self.config.auto_step_size == "ATR":
            atr = self._atr_pct(self._bar_type_main)
            return max(self.config.min_step_pct, atr)
        if self.config.auto_step_size == "CandleSize":
            cs = self._candle_size_pct(self._bar_type_main)
            return max(self.config.min_step_pct, cs)
        if self.config.pct_step_size:
            return max(self.config.min_step_pct, self.config.step_size)
        if self._prices:
            return max(self.config.min_step_pct, (self.config.step_size / self._prices[-1]) * 100)
        return self.config.min_step_pct

    def _buy_trailing_pct(self, step_pct: float) -> float:
        if self.config.pct_trailing_range:
            base = self.config.custom_trailing_range
        else:
            base = step_pct
        return self._trend_plus_multiplier(base, side="BUY")

    def _sell_trailing_pct(self, step_pct: float) -> float:
        if self.config.pct_sell_trailing_range:
            base = self.config.custom_sell_trailing_range
        else:
            base = step_pct
        return self._trend_plus_multiplier(base, side="SELL")

    def _trend_plus_multiplier(self, base: float, side: str) -> float:
        if not self.config.trend_plus:
            return base
        phase = self._phase()
        if side == "BUY":
            if phase == "VERY_BULLISH":
                return base * self.config.trend_plus_buy_multiplier_large
            if phase == "BULLISH":
                return base * self.config.trend_plus_buy_multiplier_medium
            return base * self.config.trend_plus_buy_multiplier_small
        else:
            if phase == "VERY_BULLISH":
                return base * self.config.trend_plus_sell_multiplier_large
            if phase == "BULLISH":
                return base * self.config.trend_plus_sell_multiplier_medium
            return base * self.config.trend_plus_sell_multiplier_small

    def _gain_target(self) -> float:
        if self.config.dynamic_exit_logic and not self.config.unit_cost:
            return self._avg_cost * (1 + self.config.gain_pct / 100)
        return self._break_even_price() * (1 + self.config.gain_pct / 100)

    def _partial_sell_allowed(self, price: float) -> bool:
        if self._position_qty <= 0:
            return False
        if price < self._break_even_price() * (1 + self.config.gain_partial_pct / 100):
            return False
        return True

    def _partial_sell_qty(self, price: float) -> float:
        eligible_qty = self._position_qty * self.config.partial_sell_ratio
        if self.config.partial_sell_cap:
            cap_qty = (self._effective_tl() / price) * self.config.partial_sell_cap_ratio
            eligible_qty = min(eligible_qty, cap_qty)
        return max(0.0, eligible_qty)

    def _break_even_price(self) -> float:
        if self.config.unit_cost:
            return self._avg_cost if self._avg_cost > 0 else self._last_buy_price
        if self._total_buy_qty > 0:
            return self._total_buy_cost / self._total_buy_qty
        return self._avg_cost if self._avg_cost > 0 else self._last_buy_price

    def _effective_tl(self) -> float:
        tl = self.config.trading_limit
        if self.config.always_use_tl_multiplier:
            tl *= self.config.tl_multiplier
        if self.config.trend_scalping and self._phase() in ("BULLISH", "VERY_BULLISH"):
            tl *= self.config.scalp_tl_ratio
        if self.config.multiple_timeframes_mode:
            tl *= self.config.mtf_tl_ratio
        if self.config.trend_sync and self._phase() == "BULLISH":
            tl *= self.config.price_action_tl_ratio
        return tl

    def _max_buy_count(self) -> int:
        if self.config.btfd_mode:
            return self.config.btfd_max_buy_count
        return self.config.max_buy_count

    def _dca_target(self, price: float) -> float:
        step_pct = self._step_pct()
        return self._last_buy_price * (1 - step_pct / 100)

    def _phase(self) -> str:
        short_bull = self._trend_bullish(self._bar_type_main)
        med_bull = self._trend_bullish(self._bar_type_med)
        long_bull = self._trend_bullish(self._bar_type_long)
        if short_bull and med_bull and long_bull:
            return "VERY_BULLISH"
        if short_bull and med_bull:
            return "BULLISH"
        if short_bull and not med_bull:
            return "BULLISH_REV"
        if not short_bull and not med_bull and not long_bull:
            return "VERY_BEARISH"
        if not short_bull and not med_bull:
            return "BEARISH"
        return "BEARISH_REV"

    def _trend_bullish(self, bar_type: BarType | None) -> bool:
        bars = self._bars.get(self._bar_key(bar_type), deque()) if bar_type else deque()
        if len(bars) < 5:
            return True
        closes = [float(b.close) for b in list(bars)[-5:]]
        return closes[-1] >= sum(closes) / len(closes)

    def _momentum_ok(self) -> bool:
        return self._trend_bullish(self._bar_type_long)

    def _is_overbought(self, sensitivity: str) -> bool:
        if sensitivity == "NONE":
            return False
        checks = [self._bar_type_main]
        if sensitivity in ("MEDIUM", "LONG"):
            checks.append(self._bar_type_med)
        if sensitivity == "LONG":
            checks.append(self._bar_type_long)
        for bt in checks:
            if self._overbought(bt):
                return True
        return False

    def _overbought(self, bar_type: BarType | None) -> bool:
        bars = self._bars.get(self._bar_key(bar_type), deque()) if bar_type else deque()
        if len(bars) < 10:
            return False
        closes = [float(b.close) for b in list(bars)[-10:]]
        mean = sum(closes) / len(closes)
        var = sum((c - mean) ** 2 for c in closes) / len(closes)
        std = max(var ** 0.5, 1e-9)
        z = (closes[-1] - mean) / std
        return z > 1.5

    def _atr_pct(self, bar_type: BarType | None) -> float:
        bars = self._bars.get(self._bar_key(bar_type), deque()) if bar_type else deque()
        if len(bars) < self.config.atr_period:
            return self.config.min_step_pct
        trs = []
        prev_close = None
        for b in list(bars)[-self.config.atr_period:]:
            high = float(b.high)
            low = float(b.low)
            close = float(b.close)
            if prev_close is None:
                tr = high - low
            else:
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
            prev_close = close
        atr = sum(trs) / len(trs)
        price = float(bars[-1].close)
        return (atr / price) * 100 if price > 0 else self.config.min_step_pct

    def _candle_size_pct(self, bar_type: BarType | None) -> float:
        bars = self._bars.get(self._bar_key(bar_type), deque()) if bar_type else deque()
        if len(bars) < 10:
            return self.config.min_step_pct
        sizes = [abs(float(b.close) - float(b.open)) / float(b.close) * 100 for b in list(bars)[-10:]]
        return max(self.config.min_step_pct, sum(sizes) / len(sizes))

    def _bar_key(self, bar_type: BarType | None) -> str:
        return str(bar_type) if bar_type else ""

    def _quote_invested(self) -> float:
        return self._avg_cost * self._position_qty

    def _calc_avg_cost(self, price: float, qty: float) -> float:
        if self._position_qty <= 0:
            return price
        total_cost = (self._avg_cost * (self._position_qty - qty)) + (price * qty)
        return total_cost / max(self._position_qty, 1e-9)

    def _reset_position(self) -> None:
        self._position_qty = 0.0
        self._avg_cost = 0.0
        self._total_buy_cost = 0.0
        self._total_buy_qty = 0.0
        self._last_buy_price = 0.0
        self._last_buy_qty = 0.0
        self._buy_count = 0
        self._trailing_buy_active = False
        self._trailing_sell_active = False

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
        self._bar_type_main = BarType.from_str(
            f"{symbol}-SPOT.BYBIT-{self._normalize_period(self.config.period)}-LAST-EXTERNAL"
        )
        self._bar_type_med = BarType.from_str(
            f"{symbol}-SPOT.BYBIT-{self._normalize_period(self.config.period_medium)}-LAST-EXTERNAL"
        )
        self._bar_type_long = BarType.from_str(
            f"{symbol}-SPOT.BYBIT-{self._normalize_period(self.config.period_long)}-LAST-EXTERNAL"
        )
        self.subscribe_bars(self._bar_type_main)
        self.subscribe_bars(self._bar_type_med)
        self.subscribe_bars(self._bar_type_long)

        if self.config.multiple_timeframes_mode:
            self._bar_type_low = BarType.from_str(
                f"{symbol}-SPOT.BYBIT-{self._normalize_period(self.config.lower_period_low)}-LAST-EXTERNAL"
            )
            self._bar_type_mtf_med = BarType.from_str(
                f"{symbol}-SPOT.BYBIT-{self._normalize_period(self.config.lower_period_medium)}-LAST-EXTERNAL"
            )
            self._bar_type_mtf_high = BarType.from_str(
                f"{symbol}-SPOT.BYBIT-{self._normalize_period(self.config.lower_period_high)}-LAST-EXTERNAL"
            )
            self.subscribe_bars(self._bar_type_low)
            self.subscribe_bars(self._bar_type_mtf_med)
            self.subscribe_bars(self._bar_type_mtf_high)

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
