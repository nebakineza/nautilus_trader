"""ATR Volatility-Sized Confluence Scanner v003 - Multi-Indicator Portfolio Manager.

STRATEGY OVERVIEW:
- Evolved from v002: replaces simple EMA crossover with 4-indicator confluence scoring
- Inspired by traderGPT's H1 confluence system (RSI + KDJ + BB + MACD)
- Each indicator covers a different dimension of price action
- Entry requires N-of-4 indicators agreeing → dramatically fewer false signals
- All v002 infrastructure preserved: ATR sizing, correlation filter, DCA, trailing stops

CONFLUENCE SIGNALS (0-4 score):
1. RSI Oversold Recovery: RSI was below oversold and is now crossing back above
2. KDJ Golden Cross:     Stochastic %K crosses above %D from below 30
3. BB Lower Touch:       Price touched or penetrated lower Bollinger Band
4. MACD Histogram Turn:  MACD histogram turns positive (MACD crosses above signal)

EXIT SIGNALS (any 1 triggers):
- RSI enters overbought (>70) + KDJ death cross (%K < %D from above 70)
- MACD histogram turns negative
- Trailing stop hit
- ATR stop hit

DESIGN PRINCIPLES:
- H1 timeframe for faster signals (vs v002's 4H)
- Confluence reduces false positives: 4 independent confirming signals
- Each indicator can be independently tuned or disabled
- Backward compatible: set confluence_min_score=1 to approximate v002 behavior

TARGET: Bybit UK Spot, H1 timeframe, 3-5 concurrent uncorrelated positions.
"""

from __future__ import annotations

import gc
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

import numpy as np

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators.momentum import RelativeStrengthIndex, Stochastics
from nautilus_trader.indicators.trend import MovingAverageConvergenceDivergence
from nautilus_trader.indicators.volatility import BollingerBands
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.events import OrderFilled, PositionChanged, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
BPS_MULTIPLIER: Final[float] = 10000.0
LOG_RETURNS_EPSILON: Final[float] = 1e-10


@dataclass
class InstrumentState:
    """Per-instrument state tracking for the confluence scanner."""

    instrument_id: InstrumentId
    instrument: Instrument | None = None
    tick_size: float = 0.0
    min_qty: float = 0.0

    # Price history (ring buffer)
    close_buffer: np.ndarray = field(default_factory=lambda: np.zeros(250, dtype=np.float64))
    high_buffer: np.ndarray = field(default_factory=lambda: np.zeros(250, dtype=np.float64))
    low_buffer: np.ndarray = field(default_factory=lambda: np.zeros(250, dtype=np.float64))
    buffer_idx: int = 0
    buffer_count: int = 0

    # Log returns for correlation
    log_returns: np.ndarray = field(default_factory=lambda: np.zeros(100, dtype=np.float64))
    returns_idx: int = 0
    returns_count: int = 0

    # --- NautilusTrader indicator instances (set per-instrument in on_start) ---
    rsi: object = None           # RelativeStrengthIndex
    stoch: object = None         # Stochastics (KDJ)
    bb: object = None            # BollingerBands
    macd: object = None          # MovingAverageConvergenceDivergence

    # --- Confluence state (computed each bar) ---
    confluence_score: int = 0    # 0-4 how many indicators agree
    signal_rsi: bool = False
    signal_kdj: bool = False
    signal_bb: bool = False
    signal_macd: bool = False

    # --- Previous values for crossover / histogram detection ---
    prev_rsi: float = 50.0
    prev_stoch_k: float = 50.0
    prev_stoch_d: float = 50.0
    prev_macd: float = 0.0
    prev_macd_signal: float = 0.0
    prev_bb_lower: float = 0.0

    # MACD signal line (EMA of MACD value, computed manually)
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    prev_macd_histogram: float = 0.0

    # Trend SMA (long-term filter)
    trend_sma: float = 0.0

    # ATR (computed from buffers, same as v002)
    atr: float = 0.0

    # Current price
    last_close: float = 0.0

    # Trend score for ranking
    trend_score: float = 0.0

    # Position tracking
    has_position: bool = False
    position_side: OrderSide | None = None
    entry_price: float = 0.0
    entry_atr: float = 0.0

    # DCA tracking
    dca_count: int = 0
    avg_entry_price: float = 0.0
    total_qty: float = 0.0
    last_dca_price: float = 0.0

    # Warmup tracking
    bars_seen: int = 0


class ConfluenceScannerConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for Confluence Scanner v003.

    Multi-indicator confluence entry with ATR-sized positions and
    correlation-filtered portfolio management.
    """

    # === UNIVERSE ===
    instrument_ids: tuple[InstrumentId, ...]
    bar_type_template: str = "{symbol}-1-HOUR-LAST-EXTERNAL"

    # === PORTFOLIO ===
    max_positions: int = 3
    max_portfolio_heat_pct: float = 10.0
    risk_per_trade_usd: float = 100.0
    max_position_usd: float = 5000.0
    min_position_usd: float = 10.0

    # === CORRELATION FILTER ===
    correlation_enabled: bool = True
    correlation_threshold: float = 0.70
    correlation_lookback: int = 30

    # === CONFLUENCE ENTRY ===
    confluence_min_score: int = 3        # Require N-of-4 signals for entry (1-4)

    # --- RSI ---
    rsi_enabled: bool = True
    rsi_period: int = 14
    rsi_oversold: float = 30.0          # RSI crossing UP through this = bullish
    rsi_overbought: float = 70.0        # RSI crossing DOWN through this = exit

    # --- KDJ / Stochastics ---
    kdj_enabled: bool = True
    kdj_period_k: int = 14
    kdj_period_d: int = 3
    kdj_slowing: int = 3                # Standard stochastic slowing
    kdj_oversold: float = 30.0          # Golden cross below this = signal
    kdj_overbought: float = 70.0        # Death cross above this = exit

    # --- Bollinger Bands ---
    bb_enabled: bool = True
    bb_period: int = 20
    bb_std_dev: float = 2.0
    bb_touch_pct: float = 0.5           # Price within 0.5% of lower band = touch

    # --- MACD ---
    macd_enabled: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal_period: int = 9         # EMA period for signal line

    # === TREND FILTER (optional, long-term) ===
    trend_filter_enabled: bool = True
    trend_filter_period: int = 200

    # === ATR & STOPS ===
    atr_period: int = 14
    atr_stop_multiplier: float = 2.5
    trailing_stop_enabled: bool = True
    trailing_atr_multiplier: float = 2.5
    trailing_activation_profit_pct: float = 1.5

    # === SAFETY ===
    max_drawdown_pct: float = 15.0
    daily_loss_limit_usd: float = 500.0

    # === DCA ===
    dca_enabled: bool = False
    dca_max_adds: int = 2
    dca_atr_drop_multiplier: float = 2.0
    dca_qty_multiplier: float = 0.5
    dca_require_trend: bool = True
    dca_max_total_usd: float = 0.0

    # === EXECUTION ===
    use_limit_orders: bool = True
    limit_chase_ticks: int = 3
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None

    # === FEES ===
    taker_fee_pct: float = 0.10
    maker_fee_pct: float = 0.10

    # === LOGGING ===
    log_scans: bool = True
    log_correlations: bool = True
    log_trades: bool = True
    log_sizing: bool = True
    log_confluence: bool = True          # Log per-bar confluence breakdown


class ConfluenceScanner(Strategy):
    """Multi-Asset Confluence Scanner v003.

    Replaces v002's EMA crossover entry with a 4-indicator confluence system:
    RSI oversold recovery + KDJ golden cross + BB lower touch + MACD histogram turn.

    All portfolio management, sizing, correlation filtering, DCA, and safety
    systems are inherited from v002.
    """

    __slots__ = (
        # Config cache
        '_max_positions', '_max_heat_pct', '_risk_usd', '_max_pos_usd',
        '_corr_threshold', '_corr_lookback',
        '_atr_period', '_stop_mult', '_trailing_mult',
        '_fee_roundtrip', '_min_score',
        # Confluence flags
        '_rsi_enabled', '_kdj_enabled', '_bb_enabled', '_macd_enabled',
        '_rsi_os', '_rsi_ob', '_kdj_os', '_kdj_ob',
        '_bb_touch_pct', '_macd_signal_alpha',
        '_trend_period',
        # DCA config cache
        '_dca_enabled', '_dca_max_adds', '_dca_atr_drop',
        '_dca_qty_mult', '_dca_require_trend', '_dca_max_total',
        # Per-instrument state
        '_states', '_bar_types',
        # Active positions
        '_active_positions',
        # Portfolio state
        '_peak_equity', '_starting_equity', '_daily_start_equity',
        '_realized_pnl_today', '_total_unrealized_pnl',
        '_killswitch_triggered', '_daily_limit_hit',
        # Orders
        '_pending_orders', '_stop_orders',
        # Client ID
        'client_id',
    )

    def __init__(self, config: ConfluenceScannerConfig) -> None:
        super().__init__(config)

        if len(config.instrument_ids) < 2:
            raise ValueError("Need at least 2 instruments for universe scanning")
        if not (1 <= config.confluence_min_score <= 4):
            raise ValueError("confluence_min_score must be 1-4")

        # --- Portfolio ---
        self._max_positions: int = config.max_positions
        self._max_heat_pct: float = config.max_portfolio_heat_pct
        self._risk_usd: float = config.risk_per_trade_usd
        self._max_pos_usd: float = config.max_position_usd
        self._corr_threshold: float = config.correlation_threshold
        self._corr_lookback: int = config.correlation_lookback
        self._atr_period: int = config.atr_period
        self._stop_mult: float = config.atr_stop_multiplier
        self._trailing_mult: float = config.trailing_atr_multiplier
        self._fee_roundtrip: float = config.taker_fee_pct + config.maker_fee_pct
        self._min_score: int = config.confluence_min_score
        self._trend_period: int = config.trend_filter_period

        # --- Confluence toggles ---
        self._rsi_enabled: bool = config.rsi_enabled
        self._kdj_enabled: bool = config.kdj_enabled
        self._bb_enabled: bool = config.bb_enabled
        self._macd_enabled: bool = config.macd_enabled
        self._rsi_os: float = config.rsi_oversold
        self._rsi_ob: float = config.rsi_overbought
        self._kdj_os: float = config.kdj_oversold
        self._kdj_ob: float = config.kdj_overbought
        self._bb_touch_pct: float = config.bb_touch_pct
        self._macd_signal_alpha: float = 2.0 / (config.macd_signal_period + 1)

        # --- DCA ---
        self._dca_enabled: bool = config.dca_enabled
        self._dca_max_adds: int = config.dca_max_adds
        self._dca_atr_drop: float = config.dca_atr_drop_multiplier
        self._dca_qty_mult: float = config.dca_qty_multiplier
        self._dca_require_trend: bool = config.dca_require_trend
        self._dca_max_total: float = config.dca_max_total_usd if config.dca_max_total_usd > 0 else config.max_position_usd

        # --- State ---
        self._states: dict[InstrumentId, InstrumentState] = {}
        self._bar_types: dict[InstrumentId, BarType] = {}
        self._active_positions: set[InstrumentId] = set()
        self._peak_equity: float = 0.0
        self._starting_equity: float = 0.0
        self._daily_start_equity: float = 0.0
        self._realized_pnl_today: float = 0.0
        self._total_unrealized_pnl: float = 0.0
        self._killswitch_triggered: bool = False
        self._daily_limit_hit: bool = False
        self._pending_orders: dict[InstrumentId, Order] = {}
        self._stop_orders: dict[InstrumentId, Order] = {}
        self.client_id = config.client_id

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    def on_start(self) -> None:
        gc.disable()
        cfg = self.config

        enabled = [n for n, e in [("RSI", self._rsi_enabled), ("KDJ", self._kdj_enabled),
                                   ("BB", self._bb_enabled), ("MACD", self._macd_enabled)] if e]

        self.log.info("=" * 70, LogColor.GREEN)
        self.log.info("🚀 CONFLUENCE SCANNER v003 - STARTING", LogColor.GREEN)
        self.log.info(f"   Universe: {len(cfg.instrument_ids)} instruments", LogColor.GREEN)
        self.log.info(f"   Timeframe: {cfg.bar_type_template}", LogColor.GREEN)
        self.log.info(f"   Indicators: {', '.join(enabled)}", LogColor.GREEN)
        self.log.info(f"   Min Score: {self._min_score}/{len(enabled)}", LogColor.GREEN)
        self.log.info(f"   Max Positions: {self._max_positions}", LogColor.GREEN)
        self.log.info(f"   Risk/Trade: ${self._risk_usd:.2f}", LogColor.GREEN)
        self.log.info("=" * 70, LogColor.GREEN)

        for inst_id in cfg.instrument_ids:
            instrument = self.cache.instrument(inst_id)
            if instrument is None:
                self.log.warning(f"⚠️ Instrument not found: {inst_id}")
                continue

            state = InstrumentState(instrument_id=inst_id)
            state.instrument = instrument
            state.tick_size = float(instrument.price_increment)
            state.min_qty = float(instrument.min_quantity)

            # Allocate buffers
            max_lb = max(self._trend_period, cfg.bb_period, cfg.macd_slow, self._atr_period) + 10
            state.close_buffer = np.zeros(max_lb, dtype=np.float64)
            state.high_buffer = np.zeros(max_lb, dtype=np.float64)
            state.low_buffer = np.zeros(max_lb, dtype=np.float64)
            state.log_returns = np.zeros(self._corr_lookback + 10, dtype=np.float64)

            # Create NautilusTrader indicator instances per instrument
            if self._rsi_enabled:
                state.rsi = RelativeStrengthIndex(cfg.rsi_period)

            if self._kdj_enabled:
                state.stoch = Stochastics(cfg.kdj_period_k, cfg.kdj_period_d, cfg.kdj_slowing)

            if self._bb_enabled:
                state.bb = BollingerBands(cfg.bb_period, cfg.bb_std_dev)

            if self._macd_enabled:
                state.macd = MovingAverageConvergenceDivergence(cfg.macd_fast, cfg.macd_slow)

            self._states[inst_id] = state

            # Subscribe to bars
            symbol_str = str(inst_id)
            bar_type_str = cfg.bar_type_template.replace("{symbol}", symbol_str)
            bar_type = BarType.from_str(bar_type_str)
            self._bar_types[inst_id] = bar_type
            self.subscribe_bars(bar_type)

            self.log.info(f"   📊 Subscribed: {inst_id}", LogColor.BLUE)

        # Initial equity
        try:
            accounts = self.cache.accounts()
            if accounts:
                from nautilus_trader.model.currencies import USDT
                balance = accounts[0].balance(USDT)
                if balance is not None:
                    self._starting_equity = float(balance.total)
                    self._peak_equity = self._starting_equity
                    self._daily_start_equity = self._starting_equity
        except Exception:
            self._starting_equity = 10000.0
            self._peak_equity = self._starting_equity
            self._daily_start_equity = self._starting_equity

        self.log.info(f"   💰 Starting Equity: ${self._starting_equity:.2f}", LogColor.GREEN)
        self.log.info(f"   ✅ Initialized {len(self._states)} instruments", LogColor.GREEN)

    def on_stop(self) -> None:
        gc.enable()
        for inst_id in self._states:
            self.cancel_all_orders(inst_id)
        for inst_id in self._active_positions.copy():
            self.close_all_positions(inst_id)
        self.log.info("🛑 Confluence Scanner v003 STOPPED", LogColor.RED)

    # =========================================================================
    # BAR HANDLER
    # =========================================================================

    def on_bar(self, bar: Bar) -> None:
        inst_id = bar.bar_type.instrument_id
        if inst_id not in self._states:
            return

        state = self._states[inst_id]

        if self._killswitch_triggered or self._daily_limit_hit:
            return

        # Update price buffers, indicators, confluence
        self._update_state(state, bar)

        state.bars_seen += 1

        # Warmup: need enough bars for all indicators
        min_bars = max(self._trend_period, self.config.bb_period,
                       self.config.macd_slow, self._atr_period, self.config.rsi_period,
                       self.config.kdj_period_k) + 10
        if state.bars_seen < min_bars:
            return

        # Safety
        self._check_safety()

        # --- Manage existing position ---
        if state.has_position:
            self._manage_position(state, bar)
            return

        # --- Entry logic ---
        if inst_id in self._active_positions or inst_id in self._pending_orders:
            return
        if len(self._active_positions) >= self._max_positions:
            return
        if not self._check_portfolio_heat():
            return

        if self._should_enter(state):
            if self._passes_correlation_filter(inst_id):
                self._execute_entry(state, float(bar.close))
            elif self.config.log_correlations:
                self.log.info(
                    f"❌ {inst_id.symbol} rejected: too correlated with portfolio",
                    LogColor.YELLOW,
                )

    # =========================================================================
    # STATE UPDATE & INDICATOR FEED
    # =========================================================================

    def _update_state(self, state: InstrumentState, bar: Bar) -> None:
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)

        # --- Price ring buffers ---
        idx = state.buffer_idx
        state.close_buffer[idx] = close
        state.high_buffer[idx] = high
        state.low_buffer[idx] = low
        state.buffer_idx = (idx + 1) % len(state.close_buffer)
        if state.buffer_count < len(state.close_buffer):
            state.buffer_count += 1

        # Log returns for correlation
        if state.last_close > 0:
            log_ret = np.log(close / (state.last_close + LOG_RETURNS_EPSILON))
            ri = state.returns_idx
            state.log_returns[ri] = log_ret
            state.returns_idx = (ri + 1) % len(state.log_returns)
            if state.returns_count < len(state.log_returns):
                state.returns_count += 1

        state.last_close = close

        # --- Feed NautilusTrader indicators ---
        # Save previous values BEFORE updating
        if state.rsi is not None:
            state.prev_rsi = state.rsi.value if state.rsi.initialized else 50.0

        if state.stoch is not None:
            state.prev_stoch_k = state.stoch.value_k if state.stoch.initialized else 50.0
            state.prev_stoch_d = state.stoch.value_d if state.stoch.initialized else 50.0

        if state.bb is not None:
            state.prev_bb_lower = state.bb.lower if state.bb.initialized else 0.0

        if state.macd is not None:
            state.prev_macd = state.macd.value if state.macd.initialized else 0.0
            state.prev_macd_signal = state.macd_signal
            state.prev_macd_histogram = state.macd_histogram

        # Update indicators with new bar
        if state.rsi is not None:
            state.rsi.handle_bar(bar)

        if state.stoch is not None:
            state.stoch.handle_bar(bar)

        if state.bb is not None:
            state.bb.handle_bar(bar)

        if state.macd is not None:
            state.macd.handle_bar(bar)
            # Compute signal line: EMA of MACD value
            macd_val = state.macd.value
            if state.macd_signal == 0.0 and state.bars_seen < self.config.macd_signal_period + self.config.macd_slow:
                state.macd_signal = macd_val  # seed
            else:
                state.macd_signal = (
                    self._macd_signal_alpha * macd_val
                    + (1 - self._macd_signal_alpha) * state.macd_signal
                )
            state.macd_histogram = macd_val - state.macd_signal

        # --- ATR (from buffers, same as v002) ---
        state.atr = self._calculate_atr(state)

        # --- Trend SMA ---
        if self.config.trend_filter_enabled and state.buffer_count >= self._trend_period:
            closes = self._get_ordered_prices(state.close_buffer, state.buffer_idx, state.buffer_count)
            state.trend_sma = float(np.mean(closes[-self._trend_period:]))

        # --- Confluence scoring ---
        self._score_confluence(state, close, low)

    def _score_confluence(self, state: InstrumentState, close: float, low: float) -> None:
        """Compute 0-4 confluence score from indicator signals."""
        state.signal_rsi = False
        state.signal_kdj = False
        state.signal_bb = False
        state.signal_macd = False
        score = 0

        # 1. RSI: was oversold, now crossing back above threshold
        if self._rsi_enabled and state.rsi is not None and state.rsi.initialized:
            rsi_val = state.rsi.value
            if state.prev_rsi <= self._rsi_os and rsi_val > self._rsi_os:
                state.signal_rsi = True
                score += 1

        # 2. KDJ: golden cross — %K crosses above %D, both were in oversold zone
        if self._kdj_enabled and state.stoch is not None and state.stoch.initialized:
            k = state.stoch.value_k
            d = state.stoch.value_d
            prev_k = state.prev_stoch_k
            prev_d = state.prev_stoch_d
            if prev_k <= prev_d and k > d and prev_k < self._kdj_os:
                state.signal_kdj = True
                score += 1

        # 3. BB: price touched or penetrated lower band
        if self._bb_enabled and state.bb is not None and state.bb.initialized:
            lower = state.bb.lower
            if lower > 0:
                touch_threshold = lower * (1 + self._bb_touch_pct / 100.0)
                if low <= touch_threshold:
                    state.signal_bb = True
                    score += 1

        # 4. MACD: histogram turns positive (crosses zero from below)
        if self._macd_enabled and state.macd is not None and state.macd.initialized:
            if state.prev_macd_histogram <= 0 and state.macd_histogram > 0:
                state.signal_macd = True
                score += 1

        state.confluence_score = score

        # Log confluence when any signal fires
        if self.config.log_confluence and score > 0:
            signals = []
            if state.signal_rsi:
                signals.append(f"RSI({state.rsi.value:.1f}↑)")
            if state.signal_kdj:
                signals.append(f"KDJ({state.stoch.value_k:.0f}/{state.stoch.value_d:.0f})")
            if state.signal_bb:
                signals.append(f"BB(low={low:.4f}≤{state.bb.lower:.4f})")
            if state.signal_macd:
                signals.append(f"MACD(hist={state.macd_histogram:.6f}↑)")
            self.log.info(
                f"🔍 CONFLUENCE: {state.instrument_id.symbol} score={score}/4 | "
                + " | ".join(signals),
                LogColor.CYAN,
            )

    # =========================================================================
    # ENTRY LOGIC
    # =========================================================================

    def _should_enter(self, state: InstrumentState) -> bool:
        """Enter if confluence score meets threshold."""
        if state.confluence_score < self._min_score:
            return False

        # Long-term trend filter
        if self.config.trend_filter_enabled and state.trend_sma > 0:
            if state.last_close < state.trend_sma:
                return False

        if self.config.log_scans:
            self.log.info(
                f"✅ SIGNAL: {state.instrument_id.symbol} | "
                f"Confluence={state.confluence_score}/4 | ATR={state.atr:.4f}",
                LogColor.CYAN,
            )

        return True

    def _execute_entry(self, state: InstrumentState, price: float) -> None:
        """ATR-sized entry (identical to v002)."""
        if state.atr <= 0 or state.instrument is None:
            return

        stop_distance = state.atr * self._stop_mult
        raw_qty = self._risk_usd / stop_distance
        position_value = raw_qty * price

        if position_value > self._max_pos_usd:
            raw_qty = self._max_pos_usd / price
            position_value = self._max_pos_usd

        if position_value < self.config.min_position_usd:
            if self.config.log_sizing:
                self.log.warning(f"⚠️ {state.instrument_id.symbol} position too small: ${position_value:.2f}")
            return

        if raw_qty < state.min_qty:
            raw_qty = state.min_qty

        quantity = state.instrument.make_qty(Decimal(str(raw_qty)))

        if self.config.log_sizing:
            self.log.info(
                f"📐 SIZING {state.instrument_id.symbol} | ATR={state.atr:.4f} | "
                f"Stop={stop_distance:.4f} | Qty={quantity} | Value=${position_value:.2f}",
                LogColor.BLUE,
            )

        if self.config.use_limit_orders:
            chase = state.tick_size * self.config.limit_chase_ticks
            limit_price = price + chase
            limit_price = round(limit_price / state.tick_size) * state.tick_size
            price_obj = state.instrument.make_price(Decimal(str(limit_price)))
            order = self.order_factory.limit(
                instrument_id=state.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                price=price_obj,
                time_in_force=self.config.time_in_force,
                post_only=False,
            )
        else:
            order = self.order_factory.market(
                instrument_id=state.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
            )

        self._pending_orders[state.instrument_id] = order
        self.submit_order(order)

        if self.config.log_trades:
            self.log.info(
                f"📤 ENTRY: {state.instrument_id.symbol} BUY {quantity} @ {price:.4f} | "
                f"Confluence={state.confluence_score}/4 | Stop={self._stop_mult}x ATR",
                LogColor.GREEN,
            )

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================

    def _manage_position(self, state: InstrumentState, bar: Bar) -> None:
        close = float(bar.close)

        # --- Confluence EXIT check ---
        if self._should_exit(state):
            self._close_position(state, "Confluence Exit")
            return

        # DCA
        if self._dca_enabled:
            self._check_dca(state, close)

        # Trailing stop
        if self.config.trailing_stop_enabled:
            self._update_trailing_stop(state, close)

    def _should_exit(self, state: InstrumentState) -> bool:
        """Exit when bearish confluence appears.

        Any ONE of these triggers an exit:
        1. RSI overbought + falling back below (exhaustion)
        2. KDJ death cross from overbought zone
        3. MACD histogram turns negative
        """
        exit_signals = 0

        # RSI: was overbought, now dropping below
        if self._rsi_enabled and state.rsi is not None and state.rsi.initialized:
            if state.prev_rsi >= self._rsi_ob and state.rsi.value < self._rsi_ob:
                exit_signals += 1

        # KDJ: death cross from overbought
        if self._kdj_enabled and state.stoch is not None and state.stoch.initialized:
            k = state.stoch.value_k
            d = state.stoch.value_d
            if state.prev_stoch_k >= state.prev_stoch_d and k < d and state.prev_stoch_k > self._kdj_ob:
                exit_signals += 1

        # MACD: histogram turns negative
        if self._macd_enabled and state.macd is not None and state.macd.initialized:
            if state.prev_macd_histogram >= 0 and state.macd_histogram < 0:
                exit_signals += 1

        if exit_signals > 0 and self.config.log_trades:
            reasons = []
            if self._rsi_enabled and state.rsi is not None and state.prev_rsi >= self._rsi_ob and state.rsi.value < self._rsi_ob:
                reasons.append(f"RSI({state.rsi.value:.1f}↓)")
            if self._kdj_enabled and state.stoch is not None and state.prev_stoch_k >= state.prev_stoch_d and state.stoch.value_k < state.stoch.value_d:
                reasons.append(f"KDJ(death)")
            if self._macd_enabled and state.macd is not None and state.prev_macd_histogram >= 0 and state.macd_histogram < 0:
                reasons.append(f"MACD(hist↓)")
            self.log.info(
                f"🔻 EXIT SIGNAL: {state.instrument_id.symbol} | {' | '.join(reasons)}",
                LogColor.RED,
            )

        return exit_signals > 0

    def _close_position(self, state: InstrumentState, reason: str) -> None:
        inst_id = state.instrument_id
        if inst_id in self._stop_orders:
            stop = self._stop_orders[inst_id]
            if not stop.is_closed:
                self.cancel_order(stop)
            del self._stop_orders[inst_id]
        self.close_all_positions(inst_id)
        if self.config.log_trades:
            self.log.info(f"📥 CLOSE: {inst_id.symbol} | Reason: {reason}", LogColor.YELLOW)

    # =========================================================================
    # TRAILING STOP
    # =========================================================================

    def _update_trailing_stop(self, state: InstrumentState, current_price: float) -> None:
        if not state.has_position or state.entry_price == 0:
            return
        ref_price = state.avg_entry_price if state.avg_entry_price > 0 else state.entry_price
        profit_pct = (current_price - ref_price) / ref_price * 100
        if profit_pct < self.config.trailing_activation_profit_pct:
            return

        trailing_distance = state.atr * self._trailing_mult
        new_stop = current_price - trailing_distance
        inst_id = state.instrument_id

        if inst_id in self._stop_orders:
            old_stop = self._stop_orders[inst_id]
            if not old_stop.is_closed:
                old_stop_price = float(old_stop.trigger_price) if old_stop.trigger_price else 0
                if new_stop <= old_stop_price:
                    return
                self.cancel_order(old_stop)

        if state.instrument is not None:
            position = self.cache.position(inst_id)
            if position is not None and position.quantity > 0:
                new_stop = round(new_stop / state.tick_size) * state.tick_size
                stop_price = state.instrument.make_price(Decimal(str(new_stop)))
                stop_order = self.order_factory.stop_market(
                    instrument_id=inst_id,
                    order_side=OrderSide.SELL,
                    quantity=position.quantity,
                    trigger_price=stop_price,
                    trigger_type=TriggerType.LAST_PRICE,
                    time_in_force=TimeInForce.GTC,
                )
                self._stop_orders[inst_id] = stop_order
                self.submit_order(stop_order)

    # =========================================================================
    # DCA (identical to v002)
    # =========================================================================

    def _check_dca(self, state: InstrumentState, current_price: float) -> None:
        if state.dca_count >= self._dca_max_adds:
            return
        if state.instrument_id in self._pending_orders:
            return
        ref_price = state.last_dca_price if state.last_dca_price > 0 else state.entry_price
        if ref_price <= 0 or state.entry_atr <= 0:
            return
        drop_required = state.entry_atr * self._dca_atr_drop
        if current_price > ref_price - drop_required:
            return
        # For v003, check that confluence is still supportive (BB lower touch present)
        if self._dca_require_trend:
            if self._bb_enabled and state.bb is not None and state.bb.initialized:
                if state.last_close > state.bb.lower * (1 + self._bb_touch_pct / 100.0):
                    return  # Not near lower BB — don't DCA into mid-range
        current_value = state.total_qty * current_price
        if current_value >= self._dca_max_total:
            return
        self._execute_dca(state, current_price)

    def _execute_dca(self, state: InstrumentState, price: float) -> None:
        if state.instrument is None or state.entry_atr <= 0:
            return
        stop_distance = state.entry_atr * self._stop_mult
        initial_qty = self._risk_usd / stop_distance
        dca_qty = initial_qty * self._dca_qty_mult
        dca_value = dca_qty * price
        new_total_value = (state.total_qty + dca_qty) * price
        if new_total_value > self._dca_max_total:
            dca_qty = max(0, (self._dca_max_total / price) - state.total_qty)
            dca_value = dca_qty * price
        if dca_value < self.config.min_position_usd:
            return
        if dca_qty < state.min_qty:
            dca_qty = state.min_qty
        quantity = state.instrument.make_qty(Decimal(str(dca_qty)))

        if self.config.use_limit_orders:
            chase = state.tick_size * self.config.limit_chase_ticks
            lp = round((price + chase) / state.tick_size) * state.tick_size
            price_obj = state.instrument.make_price(Decimal(str(lp)))
            order = self.order_factory.limit(
                instrument_id=state.instrument_id, order_side=OrderSide.BUY,
                quantity=quantity, price=price_obj,
                time_in_force=self.config.time_in_force, post_only=False,
            )
        else:
            order = self.order_factory.market(
                instrument_id=state.instrument_id, order_side=OrderSide.BUY,
                quantity=quantity, time_in_force=TimeInForce.GTC,
            )
        self._pending_orders[state.instrument_id] = order
        self.submit_order(order)
        if self.config.log_trades:
            drop_pct = (state.entry_price - price) / state.entry_price * 100
            new_avg = ((state.avg_entry_price * state.total_qty) + (price * dca_qty)) / (state.total_qty + dca_qty)
            self.log.info(
                f"📉 DCA #{state.dca_count + 1}: {state.instrument_id.symbol} BUY {quantity} "
                f"@ {price:.4f} | Drop={drop_pct:.1f}% | AvgEntry→{new_avg:.4f}",
                LogColor.CYAN,
            )

    # =========================================================================
    # CORRELATION FILTER (identical to v002)
    # =========================================================================

    def _passes_correlation_filter(self, candidate_id: InstrumentId) -> bool:
        if not self.config.correlation_enabled or not self._active_positions:
            return True
        candidate_state = self._states.get(candidate_id)
        if candidate_state is None or candidate_state.returns_count < self._corr_lookback:
            return True
        candidate_returns = self._get_ordered_returns(candidate_state)
        for pos_id in self._active_positions:
            pos_state = self._states.get(pos_id)
            if pos_state is None or pos_state.returns_count < self._corr_lookback:
                continue
            pos_returns = self._get_ordered_returns(pos_state)
            if len(candidate_returns) >= self._corr_lookback and len(pos_returns) >= self._corr_lookback:
                corr = np.corrcoef(
                    candidate_returns[-self._corr_lookback:],
                    pos_returns[-self._corr_lookback:],
                )[0, 1]
                if not np.isnan(corr) and corr > self._corr_threshold:
                    if self.config.log_correlations:
                        self.log.info(
                            f"🔗 CORRELATION: {candidate_id.symbol} <-> {pos_id.symbol} = {corr:.2f}",
                            LogColor.YELLOW,
                        )
                    return False
        return True

    def _get_ordered_returns(self, state: InstrumentState) -> np.ndarray:
        return self._get_ordered_prices(state.log_returns, state.returns_idx, state.returns_count)

    # =========================================================================
    # SAFETY (identical to v002)
    # =========================================================================

    def _check_portfolio_heat(self) -> bool:
        if self._starting_equity <= 0:
            return True
        current_positions = len(self._active_positions)
        potential_heat = ((current_positions + 1) * self._risk_usd) / self._starting_equity * 100
        if potential_heat > self._max_heat_pct:
            if self.config.log_sizing:
                self.log.info(
                    f"🔥 HEAT LIMIT: {potential_heat:.1f}% > {self._max_heat_pct}%",
                    LogColor.YELLOW,
                )
            return False
        return True

    def _check_safety(self) -> None:
        current_equity = self._starting_equity + self._realized_pnl_today + self._total_unrealized_pnl
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        if self._peak_equity > 0:
            dd_pct = (self._peak_equity - current_equity) / self._peak_equity * 100
            if dd_pct >= self.config.max_drawdown_pct:
                self._killswitch_triggered = True
                self._close_all_positions("KILLSWITCH: Max Drawdown")
                self.log.error(
                    f"🚨 KILLSWITCH: Drawdown {dd_pct:.1f}% >= {self.config.max_drawdown_pct}%",
                    LogColor.RED,
                )
        daily_pnl = current_equity - self._daily_start_equity
        if daily_pnl <= -self.config.daily_loss_limit_usd:
            self._daily_limit_hit = True
            self._close_all_positions("Daily Loss Limit")
            self.log.warning(
                f"⚠️ DAILY LIMIT: ${daily_pnl:.2f} <= -${self.config.daily_loss_limit_usd}",
                LogColor.RED,
            )

    def reset_daily_limits(self) -> None:
        current_equity = self._starting_equity + self._realized_pnl_today + self._total_unrealized_pnl
        self._daily_start_equity = current_equity
        self._realized_pnl_today = 0.0
        self._daily_limit_hit = False
        self.log.info(f"🔄 DAILY RESET: equity ${current_equity:.2f}", LogColor.GREEN)

    def _close_all_positions(self, reason: str) -> None:
        for inst_id in list(self._active_positions):
            state = self._states.get(inst_id)
            if state:
                self._close_position(state, reason)

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================

    def on_order_filled(self, event: OrderFilled) -> None:
        inst_id = event.instrument_id
        state = self._states.get(inst_id)
        if state is None:
            return
        qty = float(event.last_qty)
        price = float(event.last_px)
        side = event.order_side

        if self.config.log_trades:
            self.log.info(
                f"✅ FILLED: {inst_id.symbol} {side.name} {qty} @ {price:.4f}",
                LogColor.GREEN if side == OrderSide.BUY else LogColor.RED,
            )

        if inst_id in self._pending_orders and side == OrderSide.BUY:
            del self._pending_orders[inst_id]
            if not state.has_position:
                state.has_position = True
                state.position_side = side
                state.entry_price = price
                state.entry_atr = state.atr
                state.dca_count = 0
                state.avg_entry_price = price
                state.total_qty = qty
                state.last_dca_price = price
                self._active_positions.add(inst_id)
                self._place_initial_stop(state, price)
            else:
                old_total = state.total_qty
                state.total_qty += qty
                state.avg_entry_price = (state.avg_entry_price * old_total + price * qty) / state.total_qty
                state.dca_count += 1
                state.last_dca_price = price
                if self.config.log_trades:
                    self.log.info(
                        f"📊 DCA FILL #{state.dca_count}: {inst_id.symbol} | "
                        f"AvgEntry=${state.avg_entry_price:.4f} | Qty={state.total_qty:.6f}",
                        LogColor.CYAN,
                    )
                self._update_stop_after_dca(state)

    def _place_initial_stop(self, state: InstrumentState, entry_price: float) -> None:
        if state.entry_atr <= 0 or state.instrument is None:
            return
        stop_distance = state.entry_atr * self._stop_mult
        stop_price = entry_price - stop_distance
        if stop_price <= 0:
            return
        stop_price = round(stop_price / state.tick_size) * state.tick_size
        position = self.cache.position(state.instrument_id)
        if position is None or position.quantity <= 0:
            return
        stop_price_obj = state.instrument.make_price(Decimal(str(stop_price)))
        stop_order = self.order_factory.stop_market(
            instrument_id=state.instrument_id, order_side=OrderSide.SELL,
            quantity=position.quantity, trigger_price=stop_price_obj,
            trigger_type=TriggerType.LAST_PRICE, time_in_force=TimeInForce.GTC,
        )
        self._stop_orders[state.instrument_id] = stop_order
        self.submit_order(stop_order)
        if self.config.log_trades:
            self.log.info(
                f"🛡️ STOP: {state.instrument_id.symbol} @ {stop_price:.4f} "
                f"(ATR {state.entry_atr:.4f} x {self._stop_mult})",
                LogColor.YELLOW,
            )

    def _update_stop_after_dca(self, state: InstrumentState) -> None:
        inst_id = state.instrument_id
        if state.instrument is None or state.entry_atr <= 0:
            return
        if inst_id in self._stop_orders:
            old_stop = self._stop_orders[inst_id]
            if not old_stop.is_closed:
                self.cancel_order(old_stop)
            del self._stop_orders[inst_id]
        stop_distance = state.entry_atr * self._stop_mult
        stop_price = state.avg_entry_price - stop_distance
        if stop_price <= 0:
            return
        stop_price = round(stop_price / state.tick_size) * state.tick_size
        position = self.cache.position(inst_id)
        if position is None or position.quantity <= 0:
            return
        stop_price_obj = state.instrument.make_price(Decimal(str(stop_price)))
        stop_order = self.order_factory.stop_market(
            instrument_id=inst_id, order_side=OrderSide.SELL,
            quantity=position.quantity, trigger_price=stop_price_obj,
            trigger_type=TriggerType.LAST_PRICE, time_in_force=TimeInForce.GTC,
        )
        self._stop_orders[inst_id] = stop_order
        self.submit_order(stop_order)

    def on_position_opened(self, event: PositionOpened) -> None:
        inst_id = event.position.instrument_id
        state = self._states.get(inst_id)
        if state:
            state.has_position = True
            self._active_positions.add(inst_id)

    def on_position_closed(self, event: PositionClosed) -> None:
        inst_id = event.position.instrument_id
        state = self._states.get(inst_id)
        if state:
            state.has_position = False
            state.position_side = None
            state.entry_price = 0.0
            state.entry_atr = 0.0
            state.dca_count = 0
            state.avg_entry_price = 0.0
            state.total_qty = 0.0
            state.last_dca_price = 0.0
        self._active_positions.discard(inst_id)
        if inst_id in self._stop_orders:
            del self._stop_orders[inst_id]
        pnl = float(event.realized_pnl) if event.realized_pnl else 0.0
        self._realized_pnl_today += pnl
        if self.config.log_trades:
            self.log.info(
                f"📊 CLOSED: {inst_id.symbol} | PnL=${pnl:.2f} | Daily=${self._realized_pnl_today:.2f}",
                LogColor.GREEN if pnl >= 0 else LogColor.RED,
            )

    def on_position_changed(self, event: PositionChanged) -> None:
        total_unrealized = 0.0
        for pos_id in self._active_positions:
            pos = self.cache.position(pos_id)
            if pos and pos.unrealized_pnl:
                total_unrealized += float(pos.unrealized_pnl)
        self._total_unrealized_pnl = total_unrealized

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_ordered_prices(self, buffer: np.ndarray, idx: int, count: int) -> np.ndarray:
        buf_len = len(buffer)
        if count == buf_len:
            result = np.empty(count, dtype=np.float64)
            first = buf_len - idx
            result[:first] = buffer[idx:]
            result[first:] = buffer[:idx]
            return result
        return buffer[:count].copy()

    def _calculate_atr(self, state: InstrumentState) -> float:
        n = min(state.buffer_count, self._atr_period + 1)
        if n < 2:
            return 0.0
        highs = self._get_ordered_prices(state.high_buffer, state.buffer_idx, state.buffer_count)
        lows = self._get_ordered_prices(state.low_buffer, state.buffer_idx, state.buffer_count)
        closes = self._get_ordered_prices(state.close_buffer, state.buffer_idx, state.buffer_count)
        tr_sum = 0.0
        period = min(n - 1, self._atr_period)
        for i in range(1, period + 1):
            idx = len(closes) - period - 1 + i
            if idx < 1:
                continue
            tr = max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1]),
            )
            tr_sum += tr
        return tr_sum / period if period > 0 else 0.0

    def get_ranked_opportunities(self) -> list[tuple[InstrumentId, int, float]]:
        """Get instruments ranked by confluence score then trend strength."""
        scores = []
        for inst_id, state in self._states.items():
            if state.has_position:
                continue
            if state.confluence_score > 0:
                scores.append((inst_id, state.confluence_score, state.atr))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores
