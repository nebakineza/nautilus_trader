"""Lead-Lag Market Maker v006 TURBO - Maximum Performance Edition.

PERFORMANCE OPTIMIZATIONS:
1. Float64 arithmetic throughout hot path (10-100x faster than Decimal)
2. Pre-computed constants at startup
3. NumPy arrays for price history (AVX-512 vectorized operations)
4. Guarded logging (no string formatting unless actually logging)
5. Inlined critical calculations
6. Minimized object allocations
7. Ring buffers instead of deques where possible
8. Cached attribute access

Target: Sub-microsecond quote refresh latency.
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

from strategy.metrics.questdb_writer import QuestDbILPWriter


# =============================================================================
# CONSTANTS (computed once at module load)
# =============================================================================
BPS_MULTIPLIER: Final[float] = 10000.0
BPS_DIVISOR: Final[float] = 0.0001
HALF_BPS_DIVISOR: Final[float] = 0.00005  # For spread half calculation


class LeadLagMMv6TurboConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for LeadLagMMv6Turbo - Performance Optimized."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId
    global_guard_id: InstrumentId | None = None

    # Order sizing (use floats for speed)
    order_qty: float = 10.0
    max_position_qty: float = 100.0
    min_order_qty: float = 0.001

    # === BASE QUOTING ===
    spread_bps: float = 60.0
    quote_refresh_interval_ms: int = 30
    min_quote_lifetime_ms: int = 20
    min_requote_ticks: int = 1

    # === TOXIC FLOW AVOIDANCE ===
    ofi_enabled: bool = True
    ofi_depth: int = 10
    ofi_widen_threshold: float = 0.3
    ofi_widen_bps: float = 10.0
    ofi_tighten_bps: float = 5.0

    # === VOLATILITY ===
    volatility_enabled: bool = True
    volatility_window_ticks: int = 20
    volatility_base_threshold_bps: float = 5.0
    volatility_max_widen_bps: float = 30.0

    # === REGIME DETECTION ===
    regime_detection_enabled: bool = True
    regime_window_ticks: int = 100
    regime_trending_threshold: float = 0.50
    regime_ranging_threshold: float = 0.30
    regime_trending_spread_mult: float = 2.0
    regime_trending_size_mult: float = 0.5

    # === GUARD ===
    guard_threshold_bps: float = 10.0
    guard_hysteresis_bps: float = 5.0

    # === INVENTORY ===
    risk_aversion: float = 0.8
    volatility: float = 0.02
    inventory_time_horizon_secs: float = 60.0
    internal_price_delta_limit_bps: float = 50.0

    # === POSITION SAFETY ===
    hard_position_cap_enabled: bool = True
    position_cap_buffer_pct: float = 0.95
    runaway_detection_enabled: bool = True
    runaway_window_fills: int = 10
    runaway_threshold_pct: float = 0.80
    runaway_pause_secs: int = 30

    # === FEES ===
    # VIP0 without MNT discount = 0.10% per side (MNT discount excluded for API trades)
    maker_fee_bps: float = 10.0
    min_profit_bps: float = 5.0

    # === REALIZED SPREAD GUARDIAN ===
    # Auto-widens spread when not covering fees
    realized_spread_guardian_enabled: bool = True
    realized_spread_window_fills: int = 10
    realized_spread_min_bps: float = 0.0  # 0 = just cover fees
    realized_spread_widen_step_bps: float = 10.0
    realized_spread_max_widen_bps: float = 100.0
    realized_spread_cooldown_fills: int = 5
    log_realized_spread: bool = True

    # === BOOK SETTINGS ===
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50
    max_data_staleness_ms: int = 5000

    # === EXECUTION ===
    post_only: bool = True
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None

    # === LOGGING (disabled by default for speed) ===
    log_quotes: bool = False
    log_regime_changes: bool = True
    log_guards: bool = True
    log_fills: bool = True

    # === METRICS ===
    metrics_enabled: bool = True
    metrics_interval_secs: int = 5


class LeadLagMMv6Turbo(Strategy):
    """
    Ultra-low-latency Lead-Lag Market Maker.
    
    All calculations use float64 for maximum speed.
    Decimal conversion only happens at order submission boundary.
    NumPy arrays for vectorized operations (AVX-512 on supported CPUs).
    """

    # Use __slots__ to reduce memory and speed attribute access
    __slots__ = (
        # Config cache (floats for speed)
        '_spread_bps', '_order_qty', '_max_pos', '_min_qty',
        '_ofi_widen_threshold', '_ofi_widen_bps', '_ofi_tighten_bps',
        '_vol_base_threshold', '_vol_max_widen',
        '_regime_trending_threshold', '_regime_ranging_threshold',
        '_regime_spread_mult', '_regime_size_mult',
        '_guard_threshold', '_guard_hysteresis',
        '_risk_aversion', '_volatility', '_time_horizon',
        '_delta_limit_bps', '_cap_pct',
        '_fee_floor_bps',
        # Pre-computed constants
        '_half_spread_divisor',
        # Instruments
        'follower_instrument', 'leader_instrument', 'guard_instrument',
        'leader_book', 'follower_book', 'guard_book',
        # Float mid prices (hot path)
        '_leader_mid', '_follower_mid', '_guard_mid',
        # State (floats)
        '_net_position', '_current_ofi',
        '_current_volatility_bps', '_trend_strength',
        '_current_regime_is_trending',
        '_guard_block_buy', '_guard_block_sell',
        '_diff_bps',
        # Guardian state
        '_guardian_extra_bps', '_guardian_cooldown',
        '_realized_spreads', '_realized_idx', '_realized_count',
        # WAP tracking
        '_wap_buy_total', '_wap_buy_qty', '_wap_sell_total', '_wap_sell_qty',
        # Orders
        '_bid_order', '_ask_order',
        '_bid_order_ts_ns', '_ask_order_ts_ns',
        # Timing
        '_last_quote_ts_ns', '_last_metrics_ts_ns',
        '_runaway_pause_buy_ns', '_runaway_pause_sell_ns',
        # NumPy arrays (ring buffers)
        '_price_history', '_price_idx', '_price_count',
        '_fill_sides', '_fill_idx', '_fill_count',
        # Cached functions
        '_now_ns', '_cached_account',
        # Tick size
        '_tick_size',
        # Metrics
        '_metrics',
        # Client ID
        'client_id',
        # Initialization flag
        '_initialized',
    )

    def __init__(self, config: LeadLagMMv6TurboConfig) -> None:
        super().__init__(config)

        # Cache config as floats (avoid repeated attribute access)
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

        # Pre-compute fee floor
        self._fee_floor_bps: float = (config.maker_fee_bps * 2.0) + config.min_profit_bps

        # Pre-compute spread divisor
        self._half_spread_divisor: float = 20000.0  # bps to half-spread ratio

        # Instruments (set on start)
        self.follower_instrument: Instrument | None = None
        self.leader_instrument: Instrument | None = None
        self.guard_instrument: Instrument | None = None
        self.leader_book: OrderBook | None = None
        self.follower_book: OrderBook | None = None
        self.guard_book: OrderBook | None = None

        # Float mid prices (hot path - no Decimal)
        self._leader_mid: float = 0.0
        self._follower_mid: float = 0.0
        self._guard_mid: float = 0.0

        # State (all floats)
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

        # WAP tracking for realized P&L
        self._wap_buy_total: float = 0.0
        self._wap_buy_qty: float = 0.0
        self._wap_sell_total: float = 0.0
        self._wap_sell_qty: float = 0.0

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

        # NumPy ring buffers (pre-allocated, AVX-512 friendly)
        window = max(config.volatility_window_ticks, config.regime_window_ticks)
        self._price_history: np.ndarray = np.zeros(window, dtype=np.float64)
        self._price_idx: int = 0
        self._price_count: int = 0

        self._fill_sides: np.ndarray = np.zeros(config.runaway_window_fills, dtype=np.int8)
        self._fill_idx: int = 0
        self._fill_count: int = 0

        # Cached functions
        self._now_ns = None
        self._cached_account = None

        # Tick size
        self._tick_size: float = 0.0

        # Metrics
        self._metrics: QuestDbILPWriter | None = None

        # Client ID
        self.client_id = config.client_id

        # Initialization flag - prevents quoting before fully started
        self._initialized: bool = False

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    def on_start(self) -> None:
        gc.disable()  # Disable GC during trading

        # Cache clock function
        self._now_ns = self.clock.timestamp_ns

        # Get instruments (should be available after node.build())
        self.follower_instrument = self.cache.instrument(self.config.follower_instrument_id)
        self.leader_instrument = self.cache.instrument(self.config.leader_instrument_id)

        if self.follower_instrument is None:
            self.log.error(f"Follower instrument not found: {self.config.follower_instrument_id}")
            self.stop()
            return

        if self.leader_instrument is None:
            self.log.error(f"Leader instrument not found: {self.config.leader_instrument_id}")
            self.stop()
            return

        # Cache tick size as float
        self._tick_size = float(self.follower_instrument.price_increment)

        # Subscribe to order books
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

        # Initialize metrics
        if self.config.metrics_enabled:
            self._metrics = QuestDbILPWriter.from_env("LLMMv6Turbo")

        # Schedule delayed activation using a timer
        # This allows the exec client to fully authenticate before we start quoting
        from datetime import timedelta
        self.clock.set_timer(
            name="delayed_activation",
            interval=timedelta(seconds=5),
            callback=self._on_delayed_activation,
        )

        self.log.info("LeadLagMMv6Turbo initializing, will activate in 5 seconds...")

    def _on_delayed_activation(self, event) -> None:
        """Timer callback to activate quoting after connection stabilizes."""
        # Check if trading is actually ready
        if not self._is_trading_ready():
            self.log.warning("Timer fired but trading not ready yet - waiting...")
            # Timer will fire again in 5 seconds
            return

        # Cancel the timer (it only needs to fire once)
        try:
            self.clock.cancel_timer("delayed_activation")
        except Exception:
            pass

        # Mark as initialized - allow quoting
        self._initialized = True

        self.log.info("LeadLagMMv6Turbo started - TURBO MODE ENGAGED", LogColor.GREEN)

    def _is_trading_ready(self) -> bool:
        """Check if all trading prerequisites are met.
        
        This is a critical safety check that ensures:
        1. We have accounts in cache (exec client connected)
        2. We have order books with data (data client streaming)
        3. We have valid mid prices (market data flowing)
        
        Returns True only when all conditions are met.
        """
        # Check for accounts (exec client must be connected and authenticated)
        accounts = self.cache.accounts()
        if not accounts:
            return False
        
        # Check for order books with data
        if self.leader_book is None or self.follower_book is None:
            return False
        
        # Check for valid mid prices (market data must be flowing)
        if self._leader_mid <= 0.0 or self._follower_mid <= 0.0:
            return False
        
        return True

    def on_stop(self) -> None:
        self._initialized = False
        gc.enable()
        # Cancel delayed activation timer if still pending
        try:
            self.clock.cancel_timer("delayed_activation")
        except Exception:
            pass
        if self.follower_instrument is not None:
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
        self.log.info("LeadLagMMv6Turbo stopped")

    # =========================================================================
    # DATA HANDLERS (Hot Path)
    # =========================================================================

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """Ultra-fast order book handler - all float arithmetic."""
        # Guard: Skip if not initialized
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

            # Refresh quotes
            now_ns = self._now_ns()
            self._refresh_quotes_fast(now_ns)

    # =========================================================================
    # FAST CALCULATIONS (All Float64, No Decimal)
    # =========================================================================

    def _update_price_history_fast(self, mid: float) -> None:
        """Ring buffer update - O(1), no allocation."""
        self._price_history[self._price_idx] = mid
        self._price_idx = (self._price_idx + 1) % len(self._price_history)
        if self._price_count < len(self._price_history):
            self._price_count += 1

        # Update volatility and regime inline
        if self._price_count >= 3:
            self._calculate_volatility_fast()
        if self._price_count >= 10 and self.config.regime_detection_enabled:
            self._calculate_regime_fast()

    def _calculate_volatility_fast(self) -> None:
        """Fast volatility calculation - pure Python for small arrays (faster than NumPy)."""
        n = self._price_count
        if n < 3:
            return

        # Get prices in order (reconstruct from ring buffer)
        prices = self._price_history
        idx = self._price_idx
        buf_len = len(prices)

        # Calculate returns directly from ring buffer (no allocation)
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
        """Fast regime detection - pure Python for speed."""
        n = min(self._price_count, self.config.regime_window_ticks)
        if n < 10:
            return

        # Get first and last prices from ring buffer
        prices = self._price_history
        idx = self._price_idx
        buf_len = len(prices)

        first_idx = (idx - n) % buf_len
        last_idx = (idx - 1) % buf_len

        first_price = prices[first_idx]
        last_price = prices[last_idx]
        net_move = abs(last_price - first_price)

        # Sum of absolute moves (no allocation)
        total_move = 0.0
        for i in range(n - 1):
            curr_idx = (idx - n + i) % buf_len
            next_idx = (idx - n + i + 1) % buf_len
            total_move += abs(prices[next_idx] - prices[curr_idx])

        if total_move > 0:
            self._trend_strength = net_move / total_move
        else:
            self._trend_strength = 0.0

        # Update regime
        old_trending = self._current_regime_is_trending
        if self._trend_strength >= self._regime_trending_threshold:
            self._current_regime_is_trending = True
        elif self._trend_strength <= self._regime_ranging_threshold:
            self._current_regime_is_trending = False

        # Log regime change (only if actually changed)
        if self.config.log_regime_changes and old_trending != self._current_regime_is_trending:
            regime = "TRENDING" if self._current_regime_is_trending else "RANGING"
            self.log.warning(
                f"🔄 REGIME: {regime} | Strength={self._trend_strength:.2f}",
                LogColor.MAGENTA if self._current_regime_is_trending else LogColor.GREEN,
            )

    def _update_ofi_fast(self) -> None:
        """Fast OFI calculation using BookLevel objects.
        
        OrderBook.bids() returns list[BookLevel], where each BookLevel has:
        - price: BookPrice object
        - size(): method returning float (aggregate size at this price level)
        - exposure(): method returning float (price * size)
        
        This is optimized for speed with minimal allocations.
        """
        if not self.config.ofi_enabled or self.leader_book is None:
            return

        depth = self.config.ofi_depth
        bids = self.leader_book.bids()  # Returns list[BookLevel]
        asks = self.leader_book.asks()  # Returns list[BookLevel]

        bid_vol = 0.0
        ask_vol = 0.0

        # BookLevel.size() is a method that returns the aggregate size as float
        for i, level in enumerate(bids):
            if i >= depth:
                break
            bid_vol += level.size()  # BookLevel.size() returns float

        for i, level in enumerate(asks):
            if i >= depth:
                break
            ask_vol += level.size()  # BookLevel.size() returns float

        total = bid_vol + ask_vol
        if total > 0:
            self._current_ofi = (bid_vol - ask_vol) / total
        else:
            self._current_ofi = 0.0

    def _update_guard_fast(self) -> None:
        """Fast guard check."""
        if self._leader_mid == 0.0 or self._follower_mid == 0.0:
            return

        self._diff_bps = (self._leader_mid - self._follower_mid) / self._follower_mid * BPS_MULTIPLIER

        # Block buy if leader dumping
        block_buy = self._diff_bps < -self._guard_threshold
        # Block sell if leader pumping
        block_sell = self._diff_bps > self._guard_threshold

        # Hysteresis
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

    # =========================================================================
    # QUOTE REFRESH (Ultra-Fast Hot Path)
    # =========================================================================

    def _refresh_quotes_fast(self, now_ns: int) -> None:
        """Main quote refresh - all float64 arithmetic for speed."""
        # Guard: Skip if not initialized (critical for startup timing)
        if not self._initialized:
            return

        # Rate limit (avoid excessive order updates)
        min_interval_ns = self.config.quote_refresh_interval_ms * 1_000_000
        if now_ns - self._last_quote_ts_ns < min_interval_ns:
            return

        # Sanity checks (should always pass after initialization)
        if self.follower_instrument is None or self._follower_mid == 0.0:
            return
        
        if self._leader_mid == 0.0:
            return  # Need leader price for fair value calculation

        # Update guard
        self._update_guard_fast()

        # Guard blocks
        if self._guard_block_buy and self._guard_block_sell:
            return

        # Clean up closed orders
        if self._bid_order is not None and self._bid_order.is_closed:
            self._bid_order = None
        if self._ask_order is not None and self._ask_order.is_closed:
            self._ask_order = None

        self._last_quote_ts_ns = now_ns

        # === CALCULATE SPREAD (all floats) ===
        spread_bps = self._spread_bps

        # OFI adjustment
        ofi_bid_adj = 0.0
        ofi_ask_adj = 0.0
        if self.config.ofi_enabled:
            if self._current_ofi > self._ofi_widen_threshold:
                # Buying pressure -> widen ask
                ofi_ask_adj = self._ofi_widen_bps
                ofi_bid_adj = -self._ofi_tighten_bps
            elif self._current_ofi < -self._ofi_widen_threshold:
                # Selling pressure -> widen bid
                ofi_bid_adj = self._ofi_widen_bps
                ofi_ask_adj = -self._ofi_tighten_bps

        # Volatility adjustment
        vol_adj = 0.0
        if self.config.volatility_enabled and self._current_volatility_bps > self._vol_base_threshold:
            excess = self._current_volatility_bps - self._vol_base_threshold
            max_excess = self._vol_base_threshold * 2.0
            intensity = min(excess / max_excess, 1.0)
            vol_adj = self._vol_max_widen * intensity

        # Regime adjustment (multiply base spread)
        if self._current_regime_is_trending:
            spread_bps *= self._regime_spread_mult

        # Guardian adjustment (auto-widen when not profitable)
        guardian_adj = self._guardian_extra_bps if self.config.realized_spread_guardian_enabled else 0.0

        # Final spreads
        bid_spread_bps = max(spread_bps + ofi_bid_adj + vol_adj + guardian_adj, self._fee_floor_bps)
        ask_spread_bps = max(spread_bps + ofi_ask_adj + vol_adj + guardian_adj, self._fee_floor_bps)

        # === CALCULATE PRICES (all floats) ===
        fair_price = self._follower_mid * 0.1 + self._leader_mid * 0.9 if self._leader_mid > 0 else self._follower_mid

        # Inventory skew (Avellaneda-Stoikov simplified)
        pos_ratio = self._net_position / self._max_pos if self._max_pos > 0 else 0.0
        skew_bps = -self._risk_aversion * (self._volatility ** 2) * self._time_horizon * pos_ratio * BPS_MULTIPLIER
        skew_bps = max(-self._delta_limit_bps, min(self._delta_limit_bps, skew_bps))
        skew_price = fair_price * skew_bps * BPS_DIVISOR

        bid_price = fair_price - (fair_price * bid_spread_bps / self._half_spread_divisor) + skew_price
        ask_price = fair_price + (fair_price * ask_spread_bps / self._half_spread_divisor) + skew_price

        # Round to tick
        if self._tick_size > 0:
            bid_price = round(bid_price / self._tick_size) * self._tick_size
            ask_price = round(ask_price / self._tick_size) * self._tick_size

        # === CALCULATE SIZES ===
        base_qty = self._order_qty

        # Regime size adjustment
        if self._current_regime_is_trending:
            base_qty *= self._regime_size_mult

        # Inventory size skew
        bid_mult = 1.0 - (pos_ratio * 0.5)
        ask_mult = 1.0 + (pos_ratio * 0.5)
        bid_mult = max(0.3, min(2.0, bid_mult))
        ask_mult = max(0.3, min(2.0, ask_mult))

        bid_qty = max(base_qty * bid_mult, self._min_qty)
        ask_qty = max(base_qty * ask_mult, self._min_qty)

        # === POSITION CAP CHECK ===
        cap = self._max_pos * self._cap_pct
        place_bid = not self._guard_block_buy
        place_ask = not self._guard_block_sell

        if self.config.hard_position_cap_enabled:
            if self._net_position >= cap:
                place_bid = False
            if self._net_position <= -cap:
                place_ask = False

        # === RUNAWAY PAUSE CHECK ===
        if self.config.runaway_detection_enabled:
            if now_ns < self._runaway_pause_buy_ns:
                place_bid = False
            if now_ns < self._runaway_pause_sell_ns:
                place_ask = False

        # === PLACE ORDERS ===
        if place_bid:
            self._place_order_fast(OrderSide.BUY, bid_price, bid_qty, now_ns)
        if place_ask:
            self._place_order_fast(OrderSide.SELL, ask_price, ask_qty, now_ns)

    def _place_order_fast(self, side: OrderSide, price: float, qty: float, now_ns: int) -> None:
        """Fast order placement with minimal conversion."""
        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns

        # Check if update needed
        if order is not None and not order.is_closed:
            current_price = float(order.price)
            age_ms = (now_ns - order_ts) / 1_000_000
            if age_ms < self.config.min_quote_lifetime_ms:
                return
            if abs(price - current_price) < self._tick_size:
                return
            # Cancel old order
            self.cancel_order(order, client_id=self.client_id)

        # Convert to Decimal only at API boundary
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
        """Handle fill events with minimal overhead."""
        if not hasattr(event, "last_qty") or not hasattr(event, "order_side"):
            return

        qty = float(event.last_qty)
        side = event.order_side
        price = float(event.last_px) if hasattr(event, "last_px") else 0.0
        fee = float(event.commission) if hasattr(event, "commission") and event.commission is not None else 0.0

        # Update position
        if side == OrderSide.BUY:
            self._net_position += qty
        else:
            self._net_position -= qty

        # WAP tracking and realized P&L for Guardian
        if self.config.realized_spread_guardian_enabled and price > 0:
            self._update_wap_and_guardian(side, price, qty, fee)

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
                    if self.config.log_fills:
                        self.log.warning(f"🚨 RUNAWAY BUYS: {buy_ratio:.0%} -> PAUSE BUY", LogColor.RED)
                elif buy_ratio <= (1.0 - self.config.runaway_threshold_pct):
                    self._runaway_pause_sell_ns = now_ns + pause_ns
                    if self.config.log_fills:
                        self.log.warning(f"🚨 RUNAWAY SELLS: {1-buy_ratio:.0%} -> PAUSE SELL", LogColor.RED)

        # Log fill
        if self.config.log_fills:
            self.log.info(f"FILL: {side.name} {qty:.4f} @ {price:.6f} | Pos={self._net_position:.4f}")

        # Metrics
        if self._metrics is not None:
            self._metrics.send(
                table="live_fills",
                tags={
                    "strategy": "LLMMv6Turbo",
                    "symbol": self.config.follower_instrument_id.symbol.value,
                    "side": side.name,
                },
                fields={
                    "qty": qty,
                    "price": price,
                    "position": self._net_position,
                },
                ts_ns=self._now_ns(),
            )

    def _update_wap_and_guardian(self, side: OrderSide, price: float, qty: float, fee: float) -> None:
        """Track WAP and update Guardian spread adjustment."""
        notional = price * qty
        roundtrip_fee_bps = self.config.maker_fee_bps * 2.0

        if side == OrderSide.BUY:
            # Accumulate buy side
            self._wap_buy_total += notional
            self._wap_buy_qty += qty

            # Check for realized spread (buy fills against previous sells)
            if self._wap_sell_qty > 0:
                sell_wap = self._wap_sell_total / self._wap_sell_qty
                realized_spread_bps = (sell_wap - price) / price * BPS_MULTIPLIER
                net_spread_bps = realized_spread_bps - roundtrip_fee_bps
                self._record_realized_spread(net_spread_bps, price, qty, "BUY", realized_spread_bps)
        else:
            # Accumulate sell side
            self._wap_sell_total += notional
            self._wap_sell_qty += qty

            # Check for realized spread (sell fills against previous buys)
            if self._wap_buy_qty > 0:
                buy_wap = self._wap_buy_total / self._wap_buy_qty
                realized_spread_bps = (price - buy_wap) / buy_wap * BPS_MULTIPLIER
                net_spread_bps = realized_spread_bps - roundtrip_fee_bps
                self._record_realized_spread(net_spread_bps, price, qty, "SELL", realized_spread_bps)

    def _record_realized_spread(self, net_bps: float, price: float, qty: float, side: str, realized_bps: float) -> None:
        """Record realized spread and update Guardian."""
        # Add to ring buffer
        self._realized_spreads[self._realized_idx] = net_bps
        self._realized_idx = (self._realized_idx + 1) % len(self._realized_spreads)
        if self._realized_count < len(self._realized_spreads):
            self._realized_count += 1

        # Log the fill pair
        if self.config.log_realized_spread:
            self.log.info(
                f"FILL PAIR: Realized={realized_bps:.1f}bps | Fees={self.config.maker_fee_bps * 2:.0f}bps | "
                f"Net={net_bps:.1f}bps | Guardian={self._guardian_extra_bps:.1f}bps",
                LogColor.GREEN if net_bps > 0 else LogColor.RED,
            )

        # Check if we need to adjust Guardian
        if self._realized_count >= 3:
            avg_net = float(np.mean(self._realized_spreads[:self._realized_count]))

            if avg_net < self.config.realized_spread_min_bps:
                # Losing money -> widen spread
                old_guardian = self._guardian_extra_bps
                self._guardian_extra_bps = min(
                    self._guardian_extra_bps + self.config.realized_spread_widen_step_bps,
                    self.config.realized_spread_max_widen_bps,
                )
                self._guardian_cooldown = self.config.realized_spread_cooldown_fills
                if self._guardian_extra_bps != old_guardian and self.config.log_realized_spread:
                    self.log.warning(
                        f"🛡️ GUARDIAN: Widening spread +{self.config.realized_spread_widen_step_bps:.0f}bps "
                        f"(avg_net={avg_net:.1f}bps) | Total guardian={self._guardian_extra_bps:.0f}bps",
                        LogColor.YELLOW,
                    )
            elif self._guardian_cooldown > 0:
                self._guardian_cooldown -= 1
            elif self._guardian_extra_bps > 0 and avg_net > self.config.realized_spread_min_bps + 10.0:
                # Profitable and cooldown done -> relax spread
                old_guardian = self._guardian_extra_bps
                self._guardian_extra_bps = max(
                    self._guardian_extra_bps - self.config.realized_spread_widen_step_bps,
                    0.0,
                )
                if self._guardian_extra_bps != old_guardian and self.config.log_realized_spread:
                    self.log.info(
                        f"🛡️ GUARDIAN: Relaxing spread -{self.config.realized_spread_widen_step_bps:.0f}bps "
                        f"(avg_net={avg_net:.1f}bps) | Total guardian={self._guardian_extra_bps:.0f}bps",
                        LogColor.GREEN,
                    )
