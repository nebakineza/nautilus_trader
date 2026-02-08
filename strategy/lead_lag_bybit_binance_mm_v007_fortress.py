"""Lead-Lag Market Maker v007 FORTRESS - Maximum Protection Edition.

PROTECTION SYSTEMS:
1. Inventory Skew Protection - widen aggressive side as inventory builds
2. Trend Filter - pause quoting during strong trends (>75% directional)
3. Hit Rate Tracking - stop one side if getting hit >70%
4. Real-time FIFO P&L - track actual P&L per matched trade
5. Per-Fill Dynamic Spreads - based on recent fill profitability
6. Enhanced Guardian - faster reaction, bigger steps
7. Cross-Side Balance - detect and correct imbalanced fills

PERFORMANCE (maintained from v006):
- Float64 arithmetic throughout hot path
- NumPy ring buffers for history
- Guarded logging
- Minimal allocations

Based on Feb 6, 2026 loss analysis:
- ENA: -348 bps (trending market exposure)
- SUI: -257 bps (inventory buildup during trend)
- Root cause: FIFO matched spread only +16 bps while fees cost 20 bps
"""

from __future__ import annotations

import gc
from decimal import Decimal
from typing import Final

import numpy as np

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, OrderStatus, TimeInForce
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Currency, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy

try:
    from strategy.metrics.questdb_writer import QuestDbILPWriter
except ImportError:
    QuestDbILPWriter = None

try:
    from strategy.inventory.portfolio_risk_coordinator import PortfolioRiskMixin
    PORTFOLIO_COORDINATOR_AVAILABLE = True
except ImportError:
    PortfolioRiskMixin = object  # Fallback to empty base
    PORTFOLIO_COORDINATOR_AVAILABLE = False


# =============================================================================
# CONSTANTS
# =============================================================================
BPS_MULTIPLIER: Final[float] = 10000.0
BPS_DIVISOR: Final[float] = 0.0001


class LeadLagMMv7FortressConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for LeadLagMMv7Fortress - Maximum Protection."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId

    # === ORDER SIZING ===
    order_qty: float = 10.0
    max_position_qty: float = 100.0
    min_order_qty: float = 0.001

    # === BASE QUOTING ===
    spread_bps: float = 80.0  # Default wider for safety (was 60)
    quote_refresh_interval_ms: int = 30
    min_quote_lifetime_ms: int = 20

    # === TOXIC FLOW AVOIDANCE ===
    ofi_enabled: bool = True
    ofi_depth: int = 10
    ofi_widen_threshold: float = 0.3
    ofi_widen_bps: float = 15.0  # Increased from 10
    ofi_tighten_bps: float = 3.0  # Decreased from 5

    # === VOLATILITY ===
    volatility_enabled: bool = True
    volatility_window_ticks: int = 20
    volatility_base_threshold_bps: float = 5.0
    volatility_max_widen_bps: float = 50.0  # Increased from 30

    # === REGIME DETECTION (stricter) ===
    regime_detection_enabled: bool = True
    regime_window_ticks: int = 150  # Longer window (was 100)
    regime_trending_threshold: float = 0.65  # Stricter (was 0.50)
    regime_ranging_threshold: float = 0.25  # Wider hysteresis (was 0.30)
    regime_trending_spread_mult: float = 2.5  # More aggressive (was 2.0)
    regime_trending_size_mult: float = 0.3  # Smaller in trends (was 0.5)

    # === TREND FILTER (NEW - pause in strong trends) ===
    trend_filter_enabled: bool = True
    trend_filter_threshold: float = 0.75  # Pause if >75% directional
    trend_filter_pause_secs: int = 60  # Pause duration

    # === GUARD ===
    guard_threshold_bps: float = 5.0  # Stricter (was 10)
    guard_hysteresis_bps: float = 10.0  # Wider hysteresis (was 5)

    # === INVENTORY SKEW PROTECTION (NEW) ===
    inventory_skew_enabled: bool = True
    inventory_skew_threshold_pct: float = 0.30  # Start widening at 30% of max
    inventory_skew_max_widen_bps: float = 50.0  # Max extra spread when full
    inventory_skew_block_threshold_pct: float = 0.80  # Block aggressive side at 80%

    # === INVENTORY (Avellaneda-Stoikov) ===
    risk_aversion: float = 0.8
    volatility: float = 0.02
    inventory_time_horizon_secs: float = 60.0
    internal_price_delta_limit_bps: float = 50.0

    # === POSITION SAFETY ===
    hard_position_cap_enabled: bool = True
    position_cap_buffer_pct: float = 0.90  # Stricter (was 0.95)

    # === RUNAWAY DETECTION (stricter) ===
    runaway_detection_enabled: bool = True
    runaway_window_fills: int = 5  # Faster detection (was 10)
    runaway_threshold_pct: float = 0.70  # Stricter (was 0.80)
    runaway_pause_secs: int = 60  # Longer pause (was 30)

    # === HIT RATE TRACKING (NEW) ===
    hit_rate_tracking_enabled: bool = True
    hit_rate_window_fills: int = 20
    hit_rate_imbalance_threshold: float = 0.70  # Stop side if >70% on one side
    hit_rate_pause_secs: int = 30

    # === FEES ===
    maker_fee_bps: float = 10.0
    min_profit_bps: float = 10.0  # Increased from 5

    # === REALIZED SPREAD GUARDIAN (enhanced) ===
    realized_spread_guardian_enabled: bool = True
    realized_spread_window_fills: int = 5  # Faster reaction (was 10)
    realized_spread_min_bps: float = 10.0  # Higher requirement (was 0)
    realized_spread_widen_step_bps: float = 20.0  # Bigger steps (was 10)
    realized_spread_max_widen_bps: float = 150.0  # Allow more (was 100)
    realized_spread_cooldown_fills: int = 3  # Faster recovery (was 5)
    log_realized_spread: bool = True

    # === FIFO P&L TRACKING (NEW) ===
    fifo_pnl_enabled: bool = True
    fifo_pnl_window: int = 50  # Track last 50 matched trades
    fifo_pnl_loss_threshold_bps: float = -20.0  # Pause if avg below this
    fifo_pnl_pause_secs: int = 120  # 2 minute pause

    # === DYNAMIC SPREAD (NEW - based on recent performance) ===
    dynamic_spread_enabled: bool = True
    dynamic_spread_min_bps: float = 40.0
    dynamic_spread_max_bps: float = 200.0
    dynamic_spread_adjust_rate: float = 0.1  # How fast to adjust (0-1)

    # === BOOK SETTINGS ===
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50
    max_data_staleness_ms: int = 5000

    # === EXECUTION ===
    post_only: bool = True
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None

    # === LOGGING ===
    log_quotes: bool = False
    log_regime_changes: bool = True
    log_guards: bool = True
    log_fills: bool = True
    log_protection_events: bool = True  # NEW

    # === METRICS ===
    metrics_enabled: bool = True
    metrics_interval_secs: int = 5

    # === PORTFOLIO COORDINATOR (NEW - cross-pair risk) ===
    portfolio_coordinator_enabled: bool = True
    portfolio_coordinator_redis_host: str = "localhost"
    portfolio_coordinator_redis_port: int = 6379


class LeadLagMMv7Fortress(Strategy, PortfolioRiskMixin):
    """
    Maximum Protection Lead-Lag Market Maker.
    
    Designed to survive adverse market conditions while still capturing spread.
    All protection systems can be individually enabled/disabled.
    
    Integrates with Portfolio Risk Coordinator for cross-pair risk management.
    """

    __slots__ = (
        # Config cache
        '_spread_bps', '_order_qty', '_max_pos', '_min_qty',
        '_ofi_widen_threshold', '_ofi_widen_bps', '_ofi_tighten_bps',
        '_vol_base_threshold', '_vol_max_widen',
        '_regime_trending_threshold', '_regime_ranging_threshold',
        '_regime_spread_mult', '_regime_size_mult',
        '_guard_threshold', '_guard_hysteresis',
        '_risk_aversion', '_volatility', '_time_horizon',
        '_delta_limit_bps', '_cap_pct',
        '_fee_floor_bps',
        '_half_spread_divisor',
        # Instruments
        'follower_instrument', 'leader_instrument',
        'leader_book', 'follower_book',
        # Float mid prices
        '_leader_mid', '_follower_mid',
        # State
        '_net_position', '_current_ofi',
        '_current_volatility_bps', '_trend_strength',
        '_current_regime_is_trending',
        '_guard_block_buy', '_guard_block_sell',
        '_diff_bps',
        # Portfolio coordinator
        '_portfolio_limits',
        # Guardian state
        '_guardian_extra_bps', '_guardian_cooldown',
        '_realized_spreads', '_realized_idx', '_realized_count',
        # WAP tracking
        '_wap_buy_total', '_wap_buy_qty', '_wap_sell_total', '_wap_sell_qty',
        # FIFO tracking (NEW)
        '_fifo_buys', '_fifo_sells',  # Lists of (price, qty) tuples
        '_fifo_pnl_history', '_fifo_pnl_idx', '_fifo_pnl_count',
        '_fifo_pause_until_ns',
        # Hit rate tracking (NEW)
        '_hit_rate_sides', '_hit_rate_idx', '_hit_rate_count',
        '_hit_rate_pause_buy_ns', '_hit_rate_pause_sell_ns',
        # Trend filter (NEW)
        '_trend_pause_until_ns',
        # Dynamic spread (NEW)
        '_dynamic_spread_bps',
        # Inventory skew (NEW)
        '_inventory_skew_extra_bid_bps', '_inventory_skew_extra_ask_bps',
        # Orders
        '_bid_order', '_ask_order',
        '_bid_order_ts_ns', '_ask_order_ts_ns',
        # Timing
        '_last_quote_ts_ns', '_last_metrics_ts_ns',
        '_runaway_pause_buy_ns', '_runaway_pause_sell_ns',
        # NumPy ring buffers
        '_price_history', '_price_idx', '_price_count',
        '_fill_sides', '_fill_idx', '_fill_count',
        # Cached functions
        '_now_ns', '_tick_size',
        # Metrics
        '_metrics',
        # Client ID
        'client_id',
        # Initialization
        '_initialized',
        # Stats
        '_total_fills', '_total_buy_fills', '_total_sell_fills',
        '_session_gross_pnl', '_session_fees', '_session_net_pnl',
    )

    def __init__(self, config: LeadLagMMv7FortressConfig) -> None:
        super().__init__(config)

        # Cache config as floats
        self._spread_bps: float = config.spread_bps
        self._order_qty: float = config.order_qty
        self._max_pos: float = config.max_position_qty
        self._min_qty: float = config.min_order_qty
        self._ofi_widen_threshold: float = config.ofi_widen_threshold
        self._ofi_widen_bps: float = config.ofi_widen_bps
        self._ofi_tighten_bps: float = config.ofi_tighten_bps
        self._vol_base_threshold: float = config.volatility_base_threshold_bps
        self._vol_max_widen: float = config.volatility_max_widen_bps
        self._regime_trending_threshold: float = config.regime_trending_threshold
        self._regime_ranging_threshold: float = config.regime_ranging_threshold
        self._regime_spread_mult: float = config.regime_trending_spread_mult
        self._regime_size_mult: float = config.regime_trending_size_mult
        self._guard_threshold: float = config.guard_threshold_bps
        self._guard_hysteresis: float = config.guard_hysteresis_bps
        self._risk_aversion: float = config.risk_aversion
        self._volatility: float = config.volatility
        self._time_horizon: float = config.inventory_time_horizon_secs
        self._delta_limit_bps: float = config.internal_price_delta_limit_bps
        self._cap_pct: float = config.position_cap_buffer_pct
        self._fee_floor_bps: float = (config.maker_fee_bps * 2.0) + config.min_profit_bps
        self._half_spread_divisor: float = 20000.0

        # Instruments
        self.follower_instrument: Instrument | None = None
        self.leader_instrument: Instrument | None = None
        self.leader_book: OrderBook | None = None
        self.follower_book: OrderBook | None = None

        # Float mid prices
        self._leader_mid: float = 0.0
        self._follower_mid: float = 0.0

        # State
        self._net_position: float = 0.0
        self._current_ofi: float = 0.0
        self._current_volatility_bps: float = 0.0
        self._trend_strength: float = 0.0
        self._current_regime_is_trending: bool = False
        self._guard_block_buy: bool = False
        self._guard_block_sell: bool = False
        self._diff_bps: float = 0.0

        # Guardian state
        self._guardian_extra_bps: float = 0.0
        self._guardian_cooldown: int = 0
        self._realized_spreads: np.ndarray = np.zeros(config.realized_spread_window_fills, dtype=np.float64)
        self._realized_idx: int = 0
        self._realized_count: int = 0

        # WAP tracking
        self._wap_buy_total: float = 0.0
        self._wap_buy_qty: float = 0.0
        self._wap_sell_total: float = 0.0
        self._wap_sell_qty: float = 0.0

        # FIFO tracking (NEW)
        self._fifo_buys: list = []  # [(price, qty), ...]
        self._fifo_sells: list = []
        self._fifo_pnl_history: np.ndarray = np.zeros(config.fifo_pnl_window, dtype=np.float64)
        self._fifo_pnl_idx: int = 0
        self._fifo_pnl_count: int = 0
        self._fifo_pause_until_ns: int = 0

        # Hit rate tracking (NEW)
        self._hit_rate_sides: np.ndarray = np.zeros(config.hit_rate_window_fills, dtype=np.int8)
        self._hit_rate_idx: int = 0
        self._hit_rate_count: int = 0
        self._hit_rate_pause_buy_ns: int = 0
        self._hit_rate_pause_sell_ns: int = 0

        # Trend filter (NEW)
        self._trend_pause_until_ns: int = 0

        # Dynamic spread (NEW)
        self._dynamic_spread_bps: float = config.spread_bps

        # Inventory skew protection (NEW)
        self._inventory_skew_extra_bid_bps: float = 0.0
        self._inventory_skew_extra_ask_bps: float = 0.0

        # Portfolio coordinator limits cache
        self._portfolio_limits: dict = {"allow_buy": True, "allow_sell": True, "size_scalar": 1.0}

        # Orders
        self._bid_order: Order | None = None
        self._ask_order: Order | None = None
        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0

        # Timing
        self._last_quote_ts_ns: int = 0
        self._last_metrics_ts_ns: int = 0
        self._runaway_pause_buy_ns: int = 0
        self._runaway_pause_sell_ns: int = 0

        # NumPy ring buffers
        window = max(config.volatility_window_ticks, config.regime_window_ticks)
        self._price_history: np.ndarray = np.zeros(window, dtype=np.float64)
        self._price_idx: int = 0
        self._price_count: int = 0
        self._fill_sides: np.ndarray = np.zeros(config.runaway_window_fills, dtype=np.int8)
        self._fill_idx: int = 0
        self._fill_count: int = 0

        # Cached
        self._now_ns = None
        self._tick_size: float = 0.0
        self._metrics = None
        self.client_id = config.client_id
        self._initialized: bool = False

        # Session stats
        self._total_fills: int = 0
        self._total_buy_fills: int = 0
        self._total_sell_fills: int = 0
        self._session_gross_pnl: float = 0.0
        self._session_fees: float = 0.0
        self._session_net_pnl: float = 0.0

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    def on_start(self) -> None:
        gc.disable()
        self._now_ns = self.clock.timestamp_ns

        self.follower_instrument = self.cache.instrument(self.config.follower_instrument_id)
        self.leader_instrument = self.cache.instrument(self.config.leader_instrument_id)

        if self.follower_instrument is None:
            self.log.error(f"Follower not found: {self.config.follower_instrument_id}")
            self.stop()
            return

        if self.leader_instrument is None:
            self.log.error(f"Leader not found: {self.config.leader_instrument_id}")
            self.stop()
            return

        self._tick_size = float(self.follower_instrument.price_increment)

        self.subscribe_order_book_deltas(
            self.config.follower_instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )
        self.subscribe_order_book_deltas(
            self.config.leader_instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )

        if self.config.metrics_enabled and QuestDbILPWriter is not None:
            self._metrics = QuestDbILPWriter.from_env("LLMMv7Fortress")

        # Initialize portfolio coordinator for cross-pair risk management
        if self.config.portfolio_coordinator_enabled and PORTFOLIO_COORDINATOR_AVAILABLE:
            symbol = self.config.follower_instrument_id.symbol.value
            connected = self.init_portfolio_risk(
                symbol=symbol,
                redis_host=self.config.portfolio_coordinator_redis_host,
                redis_port=self.config.portfolio_coordinator_redis_port,
            )
            if connected:
                self.log.info(f"📡 Portfolio Coordinator connected for {symbol}", LogColor.BLUE)
            else:
                self.log.warning("⚠️ Portfolio Coordinator: Redis not available", LogColor.YELLOW)

        from datetime import timedelta
        self.clock.set_timer(
            name="delayed_activation",
            interval=timedelta(seconds=5),
            callback=self._on_delayed_activation,
        )

        self.log.info("LeadLagMMv7Fortress initializing, will activate in 5 seconds...")

    def _on_delayed_activation(self, event) -> None:
        if not self._is_trading_ready():
            self.log.warning("Timer fired but trading not ready - waiting...")
            return

        try:
            self.clock.cancel_timer("delayed_activation")
        except Exception:
            pass

        self._initialized = True
        
        # Log protection status
        self.log.info(
            f"🏰 FORTRESS v007 ACTIVATED | "
            f"spread={self._spread_bps:.0f}bps | "
            f"trend_filter={self.config.trend_filter_enabled} | "
            f"hit_rate={self.config.hit_rate_tracking_enabled} | "
            f"fifo_pnl={self.config.fifo_pnl_enabled} | "
            f"inv_skew={self.config.inventory_skew_enabled}",
            LogColor.GREEN,
        )

    def _is_trading_ready(self) -> bool:
        accounts = self.cache.accounts()
        if not accounts:
            return False
        if self.leader_book is None or self.follower_book is None:
            return False
        if self._leader_mid <= 0.0 or self._follower_mid <= 0.0:
            return False
        return True

    def on_stop(self) -> None:
        self._initialized = False
        gc.enable()
        try:
            self.clock.cancel_timer("delayed_activation")
        except Exception:
            pass
        if self.follower_instrument is not None:
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
        
        # Log session summary
        self.log.info(
            f"📊 SESSION SUMMARY: Fills={self._total_fills} (B:{self._total_buy_fills}/S:{self._total_sell_fills}) | "
            f"Net P&L=${self._session_net_pnl:.2f}",
            LogColor.CYAN,
        )
        self.log.info("LeadLagMMv7Fortress stopped")

    # =========================================================================
    # DATA HANDLERS
    # =========================================================================

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if self.follower_instrument is None or self.leader_instrument is None:
            return

        instrument_id = deltas.instrument_id

        if instrument_id == self.config.leader_instrument_id:
            self.leader_book = self.cache.order_book(instrument_id)
            if self.leader_book is not None:
                bid = self.leader_book.best_bid_price()
                ask = self.leader_book.best_ask_price()
                if bid is not None and ask is not None:
                    self._leader_mid = (float(bid) + float(ask)) * 0.5
                    self._update_ofi_fast()

        elif instrument_id == self.config.follower_instrument_id:
            self.follower_book = self.cache.order_book(instrument_id)
            if self.follower_book is not None:
                bid = self.follower_book.best_bid_price()
                ask = self.follower_book.best_ask_price()
                if bid is not None and ask is not None:
                    mid = (float(bid) + float(ask)) * 0.5
                    self._follower_mid = mid
                    self._update_price_history_fast(mid)

            now_ns = self._now_ns()
            self._refresh_quotes_fast(now_ns)

    # =========================================================================
    # FAST CALCULATIONS
    # =========================================================================

    def _update_price_history_fast(self, mid: float) -> None:
        self._price_history[self._price_idx] = mid
        self._price_idx = (self._price_idx + 1) % len(self._price_history)
        if self._price_count < len(self._price_history):
            self._price_count += 1

        if self._price_count >= 3:
            self._calculate_volatility_fast()
        if self._price_count >= 10 and self.config.regime_detection_enabled:
            self._calculate_regime_fast()

    def _calculate_volatility_fast(self) -> None:
        n = self._price_count
        if n < 3:
            return

        prices = self._price_history
        idx = self._price_idx
        buf_len = len(prices)

        changes_sum = 0.0
        changes_sq_sum = 0.0
        count = 0

        for i in range(n - 1):
            curr_idx = (idx - n + i) % buf_len
            next_idx = (idx - n + i + 1) % buf_len
            p0 = prices[curr_idx]
            p1 = prices[next_idx]
            if p0 > 0:
                ret = abs(p1 - p0) / p0 * BPS_MULTIPLIER
                changes_sum += ret
                changes_sq_sum += ret * ret
                count += 1

        if count > 0:
            mean = changes_sum / count
            variance = (changes_sq_sum / count) - (mean * mean)
            self._current_volatility_bps = variance ** 0.5 if variance > 0 else 0.0

    def _calculate_regime_fast(self) -> None:
        n = min(self._price_count, self.config.regime_window_ticks)
        if n < 10:
            return

        prices = self._price_history
        idx = self._price_idx
        buf_len = len(prices)

        first_idx = (idx - n) % buf_len
        last_idx = (idx - 1) % buf_len

        first_price = prices[first_idx]
        last_price = prices[last_idx]
        net_move = abs(last_price - first_price)

        total_move = 0.0
        for i in range(n - 1):
            curr_idx = (idx - n + i) % buf_len
            next_idx = (idx - n + i + 1) % buf_len
            total_move += abs(prices[next_idx] - prices[curr_idx])

        if total_move > 0:
            self._trend_strength = net_move / total_move
        else:
            self._trend_strength = 0.0

        old_trending = self._current_regime_is_trending
        if self._trend_strength >= self._regime_trending_threshold:
            self._current_regime_is_trending = True
        elif self._trend_strength <= self._regime_ranging_threshold:
            self._current_regime_is_trending = False

        if self.config.log_regime_changes and old_trending != self._current_regime_is_trending:
            regime = "TRENDING" if self._current_regime_is_trending else "RANGING"
            self.log.warning(
                f"🔄 REGIME: {regime} | Strength={self._trend_strength:.2f}",
                LogColor.MAGENTA if self._current_regime_is_trending else LogColor.GREEN,
            )

        # TREND FILTER: Pause if very strong trend
        if self.config.trend_filter_enabled:
            if self._trend_strength >= self.config.trend_filter_threshold:
                now_ns = self._now_ns()
                pause_ns = self.config.trend_filter_pause_secs * 1_000_000_000
                if now_ns >= self._trend_pause_until_ns:  # Only log once
                    self._trend_pause_until_ns = now_ns + pause_ns
                    if self.config.log_protection_events:
                        self.log.warning(
                            f"⚠️ TREND FILTER: Strength={self._trend_strength:.2f} > {self.config.trend_filter_threshold:.2f} | "
                            f"PAUSING for {self.config.trend_filter_pause_secs}s",
                            LogColor.RED,
                        )

    def _update_ofi_fast(self) -> None:
        if not self.config.ofi_enabled or self.leader_book is None:
            return

        depth = self.config.ofi_depth
        bids = self.leader_book.bids()
        asks = self.leader_book.asks()

        bid_vol = 0.0
        ask_vol = 0.0

        for i, level in enumerate(bids):
            if i >= depth:
                break
            bid_vol += level.size()

        for i, level in enumerate(asks):
            if i >= depth:
                break
            ask_vol += level.size()

        total = bid_vol + ask_vol
        if total > 0:
            self._current_ofi = (bid_vol - ask_vol) / total
        else:
            self._current_ofi = 0.0

    def _update_guard_fast(self) -> None:
        if self._leader_mid == 0.0 or self._follower_mid == 0.0:
            return

        self._diff_bps = (self._leader_mid - self._follower_mid) / self._follower_mid * BPS_MULTIPLIER

        block_buy = self._diff_bps < -self._guard_threshold
        block_sell = self._diff_bps > self._guard_threshold

        if abs(self._diff_bps) < self._guard_hysteresis:
            block_buy = False
            block_sell = False

        if block_buy != self._guard_block_buy or block_sell != self._guard_block_sell:
            self._guard_block_buy = block_buy
            self._guard_block_sell = block_sell
            if self.config.log_guards and (block_buy or block_sell):
                self.log.warning(
                    f"GUARD: diff={self._diff_bps:.1f}bps | block_buy={block_buy} block_sell={block_sell}",
                    LogColor.RED,
                )

    def _calculate_inventory_skew_protection(self) -> None:
        """Calculate extra spread widening based on inventory position."""
        if not self.config.inventory_skew_enabled:
            self._inventory_skew_extra_bid_bps = 0.0
            self._inventory_skew_extra_ask_bps = 0.0
            return

        pos_ratio = abs(self._net_position) / self._max_pos if self._max_pos > 0 else 0.0
        
        if pos_ratio < self.config.inventory_skew_threshold_pct:
            self._inventory_skew_extra_bid_bps = 0.0
            self._inventory_skew_extra_ask_bps = 0.0
            return

        # Scale from threshold to 1.0
        scale = (pos_ratio - self.config.inventory_skew_threshold_pct) / (1.0 - self.config.inventory_skew_threshold_pct)
        scale = min(scale, 1.0)
        extra_bps = scale * self.config.inventory_skew_max_widen_bps

        # Apply to aggressive side (the side that would increase inventory)
        if self._net_position > 0:  # Long -> widen bid (buying more would increase long)
            self._inventory_skew_extra_bid_bps = extra_bps
            self._inventory_skew_extra_ask_bps = 0.0
        else:  # Short -> widen ask
            self._inventory_skew_extra_bid_bps = 0.0
            self._inventory_skew_extra_ask_bps = extra_bps

    # =========================================================================
    # QUOTE REFRESH (Main Logic)
    # =========================================================================

    def _refresh_quotes_fast(self, now_ns: int) -> None:
        if not self._initialized:
            return

        min_interval_ns = self.config.quote_refresh_interval_ms * 1_000_000
        if now_ns - self._last_quote_ts_ns < min_interval_ns:
            return

        if self.follower_instrument is None or self._follower_mid == 0.0 or self._leader_mid == 0.0:
            return

        self._update_guard_fast()
        self._calculate_inventory_skew_protection()

        # Check all pause conditions
        place_bid = not self._guard_block_buy
        place_ask = not self._guard_block_sell

        # Trend filter pause
        if self.config.trend_filter_enabled and now_ns < self._trend_pause_until_ns:
            place_bid = False
            place_ask = False

        # FIFO P&L pause
        if self.config.fifo_pnl_enabled and now_ns < self._fifo_pause_until_ns:
            place_bid = False
            place_ask = False

        # Hit rate pause
        if self.config.hit_rate_tracking_enabled:
            if now_ns < self._hit_rate_pause_buy_ns:
                place_bid = False
            if now_ns < self._hit_rate_pause_sell_ns:
                place_ask = False

        # Runaway pause
        if self.config.runaway_detection_enabled:
            if now_ns < self._runaway_pause_buy_ns:
                place_bid = False
            if now_ns < self._runaway_pause_sell_ns:
                place_ask = False

        # Portfolio coordinator limits (cross-pair risk)
        if self.config.portfolio_coordinator_enabled and PORTFOLIO_COORDINATOR_AVAILABLE:
            limits = self.get_portfolio_limits()
            if not limits.get("allow_buy", True):
                place_bid = False
                if self.config.log_protection_events and limits.get("reason"):
                    self.log.warning(f"📡 PORTFOLIO: {limits['reason']}", LogColor.YELLOW)
            if not limits.get("allow_sell", True):
                place_ask = False
                if self.config.log_protection_events and limits.get("reason"):
                    self.log.warning(f"📡 PORTFOLIO: {limits['reason']}", LogColor.YELLOW)

        if not place_bid and not place_ask:
            self._last_quote_ts_ns = now_ns
            return

        # Clean up closed orders
        if self._bid_order is not None and self._bid_order.is_closed:
            self._bid_order = None
        if self._ask_order is not None and self._ask_order.is_closed:
            self._ask_order = None

        self._last_quote_ts_ns = now_ns

        # === CALCULATE SPREAD ===
        # Use dynamic spread if enabled, otherwise base spread
        spread_bps = self._dynamic_spread_bps if self.config.dynamic_spread_enabled else self._spread_bps

        # OFI adjustment
        ofi_bid_adj = 0.0
        ofi_ask_adj = 0.0
        if self.config.ofi_enabled:
            if self._current_ofi > self._ofi_widen_threshold:
                ofi_ask_adj = self._ofi_widen_bps
                ofi_bid_adj = -self._ofi_tighten_bps
            elif self._current_ofi < -self._ofi_widen_threshold:
                ofi_bid_adj = self._ofi_widen_bps
                ofi_ask_adj = -self._ofi_tighten_bps

        # Volatility adjustment
        vol_adj = 0.0
        if self.config.volatility_enabled and self._current_volatility_bps > self._vol_base_threshold:
            excess = self._current_volatility_bps - self._vol_base_threshold
            max_excess = self._vol_base_threshold * 2.0
            intensity = min(excess / max_excess, 1.0)
            vol_adj = self._vol_max_widen * intensity

        # Regime adjustment
        if self._current_regime_is_trending:
            spread_bps *= self._regime_spread_mult

        # Guardian adjustment
        guardian_adj = self._guardian_extra_bps if self.config.realized_spread_guardian_enabled else 0.0

        # Final spreads with inventory skew
        bid_spread_bps = max(
            spread_bps + ofi_bid_adj + vol_adj + guardian_adj + self._inventory_skew_extra_bid_bps,
            self._fee_floor_bps
        )
        ask_spread_bps = max(
            spread_bps + ofi_ask_adj + vol_adj + guardian_adj + self._inventory_skew_extra_ask_bps,
            self._fee_floor_bps
        )

        # === CALCULATE PRICES ===
        fair_price = self._follower_mid * 0.1 + self._leader_mid * 0.9

        pos_ratio = self._net_position / self._max_pos if self._max_pos > 0 else 0.0
        skew_bps = -self._risk_aversion * (self._volatility ** 2) * self._time_horizon * pos_ratio * BPS_MULTIPLIER
        skew_bps = max(-self._delta_limit_bps, min(self._delta_limit_bps, skew_bps))
        skew_price = fair_price * skew_bps * BPS_DIVISOR

        bid_price = fair_price - (fair_price * bid_spread_bps / self._half_spread_divisor) + skew_price
        ask_price = fair_price + (fair_price * ask_spread_bps / self._half_spread_divisor) + skew_price

        if self._tick_size > 0:
            bid_price = round(bid_price / self._tick_size) * self._tick_size
            ask_price = round(ask_price / self._tick_size) * self._tick_size

        # === CALCULATE SIZES ===
        base_qty = self._order_qty
        if self._current_regime_is_trending:
            base_qty *= self._regime_size_mult

        bid_mult = 1.0 - (pos_ratio * 0.5)
        ask_mult = 1.0 + (pos_ratio * 0.5)
        bid_mult = max(0.3, min(2.0, bid_mult))
        ask_mult = max(0.3, min(2.0, ask_mult))

        bid_qty = max(base_qty * bid_mult, self._min_qty)
        ask_qty = max(base_qty * ask_mult, self._min_qty)

        # === POSITION CAP CHECK ===
        cap = self._max_pos * self._cap_pct
        if self.config.hard_position_cap_enabled:
            if self._net_position >= cap:
                place_bid = False
            if self._net_position <= -cap:
                place_ask = False

        # === INVENTORY SKEW BLOCK ===
        if self.config.inventory_skew_enabled:
            block_threshold = self._max_pos * self.config.inventory_skew_block_threshold_pct
            if self._net_position >= block_threshold:
                place_bid = False  # Block buys when very long
            if self._net_position <= -block_threshold:
                place_ask = False  # Block sells when very short

        # === PLACE ORDERS ===
        if place_bid:
            self._place_order_fast(OrderSide.BUY, bid_price, bid_qty, now_ns)
        if place_ask:
            self._place_order_fast(OrderSide.SELL, ask_price, ask_qty, now_ns)

    def _place_order_fast(self, side: OrderSide, price: float, qty: float, now_ns: int) -> None:
        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns

        if order is not None and not order.is_closed:
            current_price = float(order.price)
            age_ms = (now_ns - order_ts) / 1_000_000
            if age_ms < self.config.min_quote_lifetime_ms:
                return
            if abs(price - current_price) < self._tick_size:
                return
            self.cancel_order(order, client_id=self.client_id)

        price_dec = self.follower_instrument.make_price(Decimal(str(price)))
        qty_obj = self.follower_instrument.make_qty(Decimal(str(qty)))

        new_order = self.order_factory.limit(
            instrument_id=self.config.follower_instrument_id,
            order_side=side,
            price=price_dec,
            quantity=qty_obj,
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
        )

        if side == OrderSide.BUY:
            self._bid_order = new_order
            self._bid_order_ts_ns = now_ns
        else:
            self._ask_order = new_order
            self._ask_order_ts_ns = now_ns

        self.submit_order(new_order)

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================

    def on_event(self, event) -> None:
        if not hasattr(event, "last_qty") or not hasattr(event, "order_side"):
            return

        qty = float(event.last_qty)
        side = event.order_side
        price = float(event.last_px) if hasattr(event, "last_px") else 0.0
        fee = float(event.commission) if hasattr(event, "commission") and event.commission is not None else 0.0

        # Update position
        if side == OrderSide.BUY:
            self._net_position += qty
            self._total_buy_fills += 1
        else:
            self._net_position -= qty
            self._total_sell_fills += 1
        self._total_fills += 1

        # FIFO P&L tracking
        if self.config.fifo_pnl_enabled and price > 0:
            self._update_fifo_pnl(side, price, qty)

        # WAP and Guardian
        if self.config.realized_spread_guardian_enabled and price > 0:
            self._update_wap_and_guardian(side, price, qty, fee)

        # Hit rate tracking
        if self.config.hit_rate_tracking_enabled:
            self._update_hit_rate(side)

        # Runaway detection
        if self.config.runaway_detection_enabled:
            self._fill_sides[self._fill_idx] = 1 if side == OrderSide.BUY else 0
            self._fill_idx = (self._fill_idx + 1) % len(self._fill_sides)
            if self._fill_count < len(self._fill_sides):
                self._fill_count += 1

            if self._fill_count >= self.config.runaway_window_fills:
                buy_count = int(np.sum(self._fill_sides[:self._fill_count]))
                buy_ratio = buy_count / self._fill_count

                now_ns = self._now_ns()
                pause_ns = self.config.runaway_pause_secs * 1_000_000_000

                if buy_ratio >= self.config.runaway_threshold_pct:
                    self._runaway_pause_buy_ns = now_ns + pause_ns
                    if self.config.log_protection_events:
                        self.log.warning(f"🚨 RUNAWAY BUYS: {buy_ratio:.0%} -> PAUSE BUY", LogColor.RED)
                elif buy_ratio <= (1.0 - self.config.runaway_threshold_pct):
                    self._runaway_pause_sell_ns = now_ns + pause_ns
                    if self.config.log_protection_events:
                        self.log.warning(f"🚨 RUNAWAY SELLS: {1-buy_ratio:.0%} -> PAUSE SELL", LogColor.RED)

        # Log fill
        if self.config.log_fills:
            self.log.info(f"FILL: {side.name} {qty:.4f} @ {price:.6f} | Pos={self._net_position:.4f}")

        # Publish state to portfolio coordinator
        if self.config.portfolio_coordinator_enabled and PORTFOLIO_COORDINATOR_AVAILABLE:
            self.publish_to_coordinator(
                position=self._net_position,
                mid_price=self._follower_mid,
                session_pnl_bps=float(np.mean(self._fifo_pnl_history[:self._fifo_pnl_count])) if self._fifo_pnl_count > 0 else 0.0,
                session_pnl_usd=self._session_net_pnl,
                fill_count=self._total_fills,
                buy_fills=self._total_buy_fills,
                sell_fills=self._total_sell_fills,
            )

    def _update_fifo_pnl(self, side: OrderSide, price: float, qty: float) -> None:
        """Track FIFO matched P&L."""
        fee_bps = self.config.maker_fee_bps * 2.0

        if side == OrderSide.BUY:
            # Try to match against sells
            remaining = qty
            while remaining > 0 and self._fifo_sells:
                sell_price, sell_qty = self._fifo_sells[0]
                match_qty = min(remaining, sell_qty)
                
                # Calculate P&L for this match
                spread_bps = (sell_price - price) / price * BPS_MULTIPLIER
                net_bps = spread_bps - fee_bps
                
                # Record
                self._fifo_pnl_history[self._fifo_pnl_idx] = net_bps
                self._fifo_pnl_idx = (self._fifo_pnl_idx + 1) % len(self._fifo_pnl_history)
                if self._fifo_pnl_count < len(self._fifo_pnl_history):
                    self._fifo_pnl_count += 1

                # Update session stats
                notional = match_qty * price
                self._session_gross_pnl += (spread_bps / BPS_MULTIPLIER) * notional
                self._session_fees += (fee_bps / BPS_MULTIPLIER) * notional
                self._session_net_pnl = self._session_gross_pnl - self._session_fees

                remaining -= match_qty
                if match_qty >= sell_qty:
                    self._fifo_sells.pop(0)
                else:
                    self._fifo_sells[0] = (sell_price, sell_qty - match_qty)

            # Remaining goes to FIFO queue
            if remaining > 0:
                self._fifo_buys.append((price, remaining))
        else:
            # Try to match against buys
            remaining = qty
            while remaining > 0 and self._fifo_buys:
                buy_price, buy_qty = self._fifo_buys[0]
                match_qty = min(remaining, buy_qty)
                
                spread_bps = (price - buy_price) / buy_price * BPS_MULTIPLIER
                net_bps = spread_bps - fee_bps
                
                self._fifo_pnl_history[self._fifo_pnl_idx] = net_bps
                self._fifo_pnl_idx = (self._fifo_pnl_idx + 1) % len(self._fifo_pnl_history)
                if self._fifo_pnl_count < len(self._fifo_pnl_history):
                    self._fifo_pnl_count += 1

                notional = match_qty * buy_price
                self._session_gross_pnl += (spread_bps / BPS_MULTIPLIER) * notional
                self._session_fees += (fee_bps / BPS_MULTIPLIER) * notional
                self._session_net_pnl = self._session_gross_pnl - self._session_fees

                remaining -= match_qty
                if match_qty >= buy_qty:
                    self._fifo_buys.pop(0)
                else:
                    self._fifo_buys[0] = (buy_price, buy_qty - match_qty)

            if remaining > 0:
                self._fifo_sells.append((price, remaining))

        # Check average FIFO P&L and pause if losing
        if self._fifo_pnl_count >= 5:
            avg_pnl = float(np.mean(self._fifo_pnl_history[:self._fifo_pnl_count]))
            if avg_pnl < self.config.fifo_pnl_loss_threshold_bps:
                now_ns = self._now_ns()
                self._fifo_pause_until_ns = now_ns + self.config.fifo_pnl_pause_secs * 1_000_000_000
                if self.config.log_protection_events:
                    self.log.warning(
                        f"🛑 FIFO P&L PAUSE: avg={avg_pnl:.1f}bps < {self.config.fifo_pnl_loss_threshold_bps:.1f}bps | "
                        f"PAUSING for {self.config.fifo_pnl_pause_secs}s",
                        LogColor.RED,
                    )

            # Dynamic spread adjustment
            if self.config.dynamic_spread_enabled:
                target_spread = self._spread_bps
                if avg_pnl < 0:
                    # Losing -> widen
                    target_spread = min(
                        self._dynamic_spread_bps + self.config.dynamic_spread_adjust_rate * 20,
                        self.config.dynamic_spread_max_bps
                    )
                elif avg_pnl > self.config.min_profit_bps + 10:
                    # Profitable -> can tighten
                    target_spread = max(
                        self._dynamic_spread_bps - self.config.dynamic_spread_adjust_rate * 10,
                        self.config.dynamic_spread_min_bps
                    )
                
                if abs(target_spread - self._dynamic_spread_bps) > 1:
                    old_spread = self._dynamic_spread_bps
                    self._dynamic_spread_bps = target_spread
                    if self.config.log_protection_events:
                        self.log.info(
                            f"📐 DYNAMIC SPREAD: {old_spread:.0f} -> {target_spread:.0f}bps (avg_pnl={avg_pnl:.1f}bps)",
                            LogColor.YELLOW if target_spread > old_spread else LogColor.GREEN,
                        )

    def _update_hit_rate(self, side: OrderSide) -> None:
        """Track hit rate and pause if imbalanced."""
        self._hit_rate_sides[self._hit_rate_idx] = 1 if side == OrderSide.BUY else 0
        self._hit_rate_idx = (self._hit_rate_idx + 1) % len(self._hit_rate_sides)
        if self._hit_rate_count < len(self._hit_rate_sides):
            self._hit_rate_count += 1

        if self._hit_rate_count >= self.config.hit_rate_window_fills:
            buy_count = int(np.sum(self._hit_rate_sides[:self._hit_rate_count]))
            buy_ratio = buy_count / self._hit_rate_count

            now_ns = self._now_ns()
            pause_ns = self.config.hit_rate_pause_secs * 1_000_000_000

            if buy_ratio >= self.config.hit_rate_imbalance_threshold:
                if now_ns >= self._hit_rate_pause_buy_ns:
                    self._hit_rate_pause_buy_ns = now_ns + pause_ns
                    if self.config.log_protection_events:
                        self.log.warning(
                            f"⚖️ HIT RATE IMBALANCE: Buys={buy_ratio:.0%} -> PAUSE BUY for {self.config.hit_rate_pause_secs}s",
                            LogColor.YELLOW,
                        )
            elif buy_ratio <= (1.0 - self.config.hit_rate_imbalance_threshold):
                if now_ns >= self._hit_rate_pause_sell_ns:
                    self._hit_rate_pause_sell_ns = now_ns + pause_ns
                    if self.config.log_protection_events:
                        self.log.warning(
                            f"⚖️ HIT RATE IMBALANCE: Sells={1-buy_ratio:.0%} -> PAUSE SELL for {self.config.hit_rate_pause_secs}s",
                            LogColor.YELLOW,
                        )

    def _update_wap_and_guardian(self, side: OrderSide, price: float, qty: float, fee: float) -> None:
        """Track WAP and update Guardian spread adjustment."""
        notional = price * qty
        roundtrip_fee_bps = self.config.maker_fee_bps * 2.0

        if side == OrderSide.BUY:
            self._wap_buy_total += notional
            self._wap_buy_qty += qty
            if self._wap_sell_qty > 0:
                sell_wap = self._wap_sell_total / self._wap_sell_qty
                realized_spread_bps = (sell_wap - price) / price * BPS_MULTIPLIER
                net_spread_bps = realized_spread_bps - roundtrip_fee_bps
                self._record_realized_spread(net_spread_bps, realized_spread_bps)
        else:
            self._wap_sell_total += notional
            self._wap_sell_qty += qty
            if self._wap_buy_qty > 0:
                buy_wap = self._wap_buy_total / self._wap_buy_qty
                realized_spread_bps = (price - buy_wap) / buy_wap * BPS_MULTIPLIER
                net_spread_bps = realized_spread_bps - roundtrip_fee_bps
                self._record_realized_spread(net_spread_bps, realized_spread_bps)

    def _record_realized_spread(self, net_bps: float, realized_bps: float) -> None:
        """Record realized spread and update Guardian."""
        self._realized_spreads[self._realized_idx] = net_bps
        self._realized_idx = (self._realized_idx + 1) % len(self._realized_spreads)
        if self._realized_count < len(self._realized_spreads):
            self._realized_count += 1

        if self.config.log_realized_spread:
            self.log.info(
                f"FILL PAIR: Realized={realized_bps:.1f}bps | Fees={self.config.maker_fee_bps * 2:.0f}bps | "
                f"Net={net_bps:.1f}bps | Guardian={self._guardian_extra_bps:.1f}bps",
                LogColor.GREEN if net_bps > 0 else LogColor.RED,
            )

        if self._realized_count >= 3:
            avg_net = float(np.mean(self._realized_spreads[:self._realized_count]))

            if avg_net < self.config.realized_spread_min_bps:
                old_guardian = self._guardian_extra_bps
                self._guardian_extra_bps = min(
                    self._guardian_extra_bps + self.config.realized_spread_widen_step_bps,
                    self.config.realized_spread_max_widen_bps,
                )
                self._guardian_cooldown = self.config.realized_spread_cooldown_fills
                if self._guardian_extra_bps != old_guardian and self.config.log_realized_spread:
                    self.log.warning(
                        f"🛡️ GUARDIAN: Widening +{self.config.realized_spread_widen_step_bps:.0f}bps "
                        f"(avg_net={avg_net:.1f}bps) | Total={self._guardian_extra_bps:.0f}bps",
                        LogColor.YELLOW,
                    )
            elif self._guardian_cooldown > 0:
                self._guardian_cooldown -= 1
            elif self._guardian_extra_bps > 0 and avg_net > self.config.realized_spread_min_bps + 10.0:
                old_guardian = self._guardian_extra_bps
                self._guardian_extra_bps = max(
                    self._guardian_extra_bps - self.config.realized_spread_widen_step_bps,
                    0.0,
                )
                if self._guardian_extra_bps != old_guardian and self.config.log_realized_spread:
                    self.log.info(
                        f"🛡️ GUARDIAN: Relaxing -{self.config.realized_spread_widen_step_bps:.0f}bps "
                        f"(avg_net={avg_net:.1f}bps) | Total={self._guardian_extra_bps:.0f}bps",
                        LogColor.GREEN,
                    )
