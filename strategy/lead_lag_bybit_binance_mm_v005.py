"""Lead-Lag Market Maker v005 - Toxic Flow Avoidance & Latency Edge.

This version implements the comprehensive optimization plan:
1. Toxic Flow Avoidance (asymmetric OFI, volatility spread adjustment, dynamic quote lifetime)
2. Queue Position Optimization (estimated queue, smart price placement, inventory urgency)
3. Latency Edge Exploitation (event-driven requotes, cancel-replace optimization)
4. Spread Intelligence (dynamic spread conditions, competitive monitoring)
5. Fill Quality Analytics (metrics for toxicity detection)
"""

from __future__ import annotations

import gc
import math
import random
from collections import deque
from decimal import Decimal
from typing import NamedTuple

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

from strategy.inventory.risk_manager import InventoryRiskManager
from strategy.metrics.questdb_writer import QuestDbILPWriter


class FillRecord(NamedTuple):
    """Record of a fill for toxicity analysis."""
    ts_ns: int
    side: OrderSide
    price: float
    qty: float
    mid_at_fill: float
    ofi_at_fill: float
    volatility_at_fill: float
    time_in_book_ms: float
    queue_position_est: float


class LeadLagMMv5Config(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for ``LeadLagMMv5``."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId
    global_guard_id: InstrumentId | None = None

    order_qty: Decimal
    max_position_qty: Decimal

    # === BASE QUOTING ===
    spread_bps: Decimal = Decimal("60.0")  # Base spread
    quote_refresh_interval_ms: int = 30
    quote_refresh_jitter_ms: int = 30
    quote_refresh_offset_ms: int = 0
    min_quote_lifetime_ms: int = 20
    min_requote_ticks: int = 1

    # === TOXIC FLOW AVOIDANCE ===
    # 1.1 Asymmetric OFI (widens the OPPOSITE side when flow is toxic)
    ofi_enabled: bool = True
    ofi_depth: int = 10
    ofi_widen_threshold: Decimal = Decimal("0.3")  # OFI > this triggers spread adjustment
    ofi_widen_bps: Decimal = Decimal("10.0")  # Extra bps added to toxic side
    ofi_tighten_bps: Decimal = Decimal("5.0")  # Bps removed from favorable side
    
    # 1.2 Volatility-based spread widening
    volatility_enabled: bool = True
    volatility_window_ticks: int = 20  # Rolling window for mid-price std dev
    volatility_base_threshold_bps: Decimal = Decimal("5.0")  # Normal volatility baseline
    volatility_max_widen_bps: Decimal = Decimal("30.0")  # Max additional spread from volatility
    
    # 1.3 Dynamic quote lifetime
    dynamic_lifetime_enabled: bool = True
    lifetime_volatility_fast_ms: int = 500  # Quote lifetime when volatility high
    lifetime_volatility_slow_ms: int = 3000  # Quote lifetime when volatility low

    # 1.4 REGIME DETECTION (Trending vs Ranging)
    regime_detection_enabled: bool = True
    regime_window_ticks: int = 50  # Window for trend strength calculation
    regime_trending_threshold: Decimal = Decimal("0.50")  # Above this = trending
    regime_ranging_threshold: Decimal = Decimal("0.30")  # Below this = ranging
    regime_trending_spread_multiplier: Decimal = Decimal("2.0")  # 2x spread when trending
    regime_trending_size_multiplier: Decimal = Decimal("0.5")  # 50% size when trending
    regime_pause_when_trending: bool = False  # If True, pause entirely when trending
    log_regime_changes: bool = True

    # === QUEUE POSITION OPTIMIZATION ===
    # 2.1 Estimated queue position
    queue_tracking_enabled: bool = True
    queue_max_position_pct: Decimal = Decimal("50.0")  # Max % of level to queue behind
    
    # 2.2 Smart price placement
    smart_placement_enabled: bool = True
    penny_improvement_ticks: int = 1  # Ticks to improve on leader price change
    
    # 2.3 Inventory-driven urgency
    inventory_urgency_enabled: bool = True
    urgency_threshold_pct: Decimal = Decimal("70.0")  # Inventory % to trigger urgency
    urgency_spread_reduction_bps: Decimal = Decimal("10.0")  # Spread reduction when urgent

    # === LATENCY EDGE EXPLOITATION ===
    # 3.1 Event-driven requoting on leader updates
    event_driven_requote: bool = True
    leader_update_min_interval_ms: int = 10  # Min ms between leader-triggered requotes
    
    # 3.2 Fast cancel-replace
    use_cancel_replace: bool = True  # Cancel + new order instead of modify
    
    # === SPREAD INTELLIGENCE ===
    # 4.1 Dynamic spread conditions
    binance_spread_tracking: bool = True
    binance_wide_spread_add_bps: Decimal = Decimal("5.0")  # Add when Binance spread wide
    binance_wide_threshold_bps: Decimal = Decimal("10.0")
    
    # 4.2 Competitive spread monitoring (follower market)
    competitive_monitoring: bool = True
    competitive_min_edge_bps: Decimal = Decimal("5.0")  # Min edge over BBO to maintain

    # === GUARD (LEAD-LAG PROTECTION) ===
    guard_threshold_bps: Decimal = Decimal("10.0")
    guard_hysteresis_bps: Decimal = Decimal("5.0")
    global_guard_threshold_bps: Decimal = Decimal("15.0")
    global_guard_window_ms: int = 500

    # === DATA QUALITY ===
    max_data_staleness_ms: int = 5000

    # === BOOK SUBSCRIPTIONS ===
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50

    # === EXECUTION ===
    post_only: bool = True
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None

    # === INVENTORY ===
    risk_aversion: float = 0.8
    volatility: float = 0.02
    inventory_time_horizon_secs: float = 60.0
    internal_price_delta_limit: Decimal = Decimal("50.0")

    # === DYNAMIC SIZING ===
    min_order_qty: Decimal = Decimal("0.00001")
    max_order_qty: Decimal = Decimal("1.0")
    vol_scalar_threshold_bps: float = 5.0
    vol_scalar_reduction: Decimal = Decimal("0.5")
    liquidity_high_qty: Decimal = Decimal("5.0")
    liquidity_low_qty: Decimal = Decimal("0.1")
    liquidity_high_scalar: Decimal = Decimal("1.5")
    liquidity_low_scalar: Decimal = Decimal("0.5")

    # === EDGE-BASED REQUOTE ===
    edge_min_ratio: Decimal = Decimal("0.3")
    edge_max_ratio: Decimal = Decimal("0.8")

    # === MARKOUT-BASED RISK ===
    markout_window_ms: int = 1000
    markout_ema_alpha: Decimal = Decimal("0.2")
    markout_widen_bps: Decimal = Decimal("5.0")
    markout_max_spread_multiplier: Decimal = Decimal("2.0")
    log_markout_events: bool = False

    # === BALANCE PROTECTION ===
    min_balance_ratio: Decimal = Decimal("0.95")
    min_quote_reserve_ratio: Decimal = Decimal("0.2")
    min_quote_reserve_usdt: Decimal = Decimal("0")

    # === FEES & PROFIT FLOOR (BPS) ===
    # VIP0 without MNT discount = 0.10% per side (MNT discount excluded for API trades)
    maker_fee_bps: Decimal = Decimal("10.0")
    min_profit_bps: Decimal = Decimal("5.0")

    # === METRICS ===
    metrics_snapshot_interval_secs: int = 5
    fill_analytics_enabled: bool = True
    fill_history_max_size: int = 1000

    # === EQUITY PROTECTION ===
    max_drawdown_pct: Decimal = Decimal("1.0")  # 100% = disabled

    # === HARD POSITION CAP ===
    hard_position_cap_enabled: bool = True  # Enforce max_position_qty strictly
    position_cap_buffer_pct: Decimal = Decimal("0.95")  # Block at 95% of max
    
    # === RUNAWAY FILL DETECTION ===
    runaway_detection_enabled: bool = True
    runaway_window_fills: int = 10  # Rolling window of fills to check
    runaway_threshold_pct: Decimal = Decimal("0.80")  # 80% same-side = runaway
    runaway_pause_secs: int = 30  # Pause accumulating side for N seconds
    
    # === REALIZED SPREAD GUARDIAN ===
    # Ensures fees are ALWAYS covered by tracking realized P&L
    realized_spread_guardian_enabled: bool = True
    realized_spread_window_fills: int = 10  # Rolling window of fills to track
    realized_spread_min_bps: Decimal = Decimal("0.0")  # Min realized spread (0 = just cover fees)
    realized_spread_widen_step_bps: Decimal = Decimal("10.0")  # How much to widen when unprofitable
    realized_spread_max_widen_bps: Decimal = Decimal("100.0")  # Max additional spread from guardian
    realized_spread_cooldown_fills: int = 5  # Fills before relaxing spread
    
    # === LOGGING ===
    log_guard_events: bool = True
    log_leader_updates: bool = False
    log_spread_adjustments: bool = True
    log_toxic_flow: bool = True
    log_realized_spread: bool = True


class LeadLagMMv5(Strategy):
    """Lead-Lag Market Maker v005 - Toxic Flow Avoidance & Latency Edge.
    
    Key improvements over v004:
    1. Asymmetric OFI - widens spread on toxic side, tightens on favorable side
    2. Volatility-adaptive spread - widens during high volatility
    3. Dynamic quote lifetime - faster requotes during volatility spikes
    4. Queue position tracking - estimates position in queue
    5. Event-driven leader following - instant requotes on leader changes
    6. Fill quality analytics - tracks toxicity metrics per fill
    """

    def __init__(self, config: LeadLagMMv5Config) -> None:
        super().__init__(config)

        # Instruments
        self.follower_instrument: Instrument | None = None
        self.leader_instrument: Instrument | None = None
        self.guard_instrument: Instrument | None = None

        # Order books
        self.follower_book: OrderBook | None = None
        self.leader_book: OrderBook | None = None
        self.guard_book: OrderBook | None = None

        # Mid prices (Decimal for precision, float for speed)
        self.leader_mid: Decimal | None = None
        self.guard_mid: Decimal | None = None
        self.follower_mid: Decimal | None = None
        self.leader_mid_f: float = 0.0
        self.follower_mid_f: float = 0.0
        
        # Previous leader mid for detecting changes
        self._prev_leader_mid_f: float = 0.0

        # Order tracking
        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0
        self._bid_order: Order | None = None
        self._ask_order: Order | None = None
        self._bid_submit_ts_ns: int = 0  # When order was submitted (for time-in-book)
        self._ask_submit_ts_ns: int = 0

        # Pricing
        self._tick_size: Decimal = Decimal("0")
        self._order_qty: Quantity | None = None
        
        # Timestamps
        self._last_quote_ts_ns: int = 0
        self._next_refresh_ts_ns: int = 0
        self._last_leader_ts_ns: int = 0
        self._last_leader_requote_ts_ns: int = 0
        self._last_guard_ts_ns: int = 0
        self._last_follower_ts_ns: int = 0
        self._last_sync_ts_ns: int = 0
        self._last_metrics_ts_ns: int = 0

        # Guard state
        self._last_guard_mid: Decimal | None = None
        self._current_diff_bps: float = 0.0
        self._guard_block_buy: bool = False
        self._guard_block_sell: bool = False
        self._global_guard_active: bool = False

        # Position tracking
        self._net_position: Decimal = Decimal("0")
        
        # === TOXIC FLOW AVOIDANCE STATE ===
        # 1.1 OFI tracking
        self._current_ofi: float = 0.0
        self._ofi_bid_adjustment_bps: Decimal = Decimal("0")
        self._ofi_ask_adjustment_bps: Decimal = Decimal("0")
        
        # 1.2 Volatility tracking (rolling mid-price changes)
        self._mid_price_history: deque[float] = deque(maxlen=config.volatility_window_ticks)
        self._current_volatility_bps: float = 0.0
        self._volatility_spread_adjustment_bps: Decimal = Decimal("0")
        
        # 1.3 Dynamic quote lifetime
        self._current_quote_lifetime_ms: int = config.min_quote_lifetime_ms

        # 1.4 Regime detection (trending vs ranging)
        self._regime_price_history: deque[float] = deque(maxlen=config.regime_window_ticks)
        self._current_trend_strength: float = 0.0
        self._current_regime: str = "RANGING"  # "RANGING" or "TRENDING"
        self._regime_spread_multiplier: Decimal = Decimal("1.0")
        self._regime_size_multiplier: Decimal = Decimal("1.0")
        self._last_regime_log_ns: int = 0

        # === QUEUE POSITION STATE ===
        self._bid_queue_position_est: float = 0.0
        self._ask_queue_position_est: float = 0.0
        self._last_bid_level_qty: float = 0.0
        self._last_ask_level_qty: float = 0.0

        # === SPREAD INTELLIGENCE STATE ===
        self._binance_spread_bps: float = 0.0
        self._follower_spread_bps: float = 0.0
        
        # === MARKOUT TRACKING ===
        self._markout_pending: list[tuple[int, OrderSide, Decimal]] = []
        self._markout_ema_bps: Decimal = Decimal("0")
        
        # === FILL QUALITY ANALYTICS ===
        self._fill_history: deque[FillRecord] = deque(maxlen=config.fill_history_max_size)
        self._toxic_fill_count: int = 0
        self._good_fill_count: int = 0
        
        # === EQUITY & P&L TRACKING ===
        self._starting_equity: Decimal | None = None
        self._killswitch_triggered: bool = False
        self._wap_inventory: Decimal = Decimal("0")
        self._wap_price: Decimal = Decimal("0")
        self._realized_pnl: Decimal = Decimal("0")
        self._fees_paid: Decimal = Decimal("0")
        
        # === REALIZED SPREAD GUARDIAN ===
        # Tracks rolling window of realized spreads to ensure fees are covered
        self._realized_spread_history: deque[Decimal] = deque(maxlen=config.realized_spread_window_fills)
        self._guardian_extra_spread_bps: Decimal = Decimal("0")
        self._profitable_streak: int = 0  # Consecutive profitable fills
        self._last_buy_price: Decimal | None = None
        self._last_sell_price: Decimal | None = None
        
        # === RUNAWAY FILL DETECTION ===
        self._recent_fill_sides: deque[OrderSide] = deque(maxlen=config.runaway_window_fills)
        self._runaway_pause_buy_until_ns: int = 0
        self._runaway_pause_sell_until_ns: int = 0
        self._runaway_alert_logged: bool = False
        
        # === METRICS ===
        self._metrics = QuestDbILPWriter.from_env("LLMMv5")
        
        # === INVENTORY MANAGEMENT ===
        self._inventory_manager = InventoryRiskManager(
            max_position=self.config.max_position_qty,
            risk_aversion=self.config.risk_aversion,
            volatility=self.config.volatility,
            time_horizon=self.config.inventory_time_horizon_secs,
        )
        self._local_quote_budget: Decimal | None = None

        # Client ID
        self.client_id = config.client_id
        
        # Cross-pair inventory coordinator (injected after init)
        self._inventory_coordinator = None
        
        # === PERFORMANCE OPTIMIZATIONS ===
        self._get_now_ns = None  # Cached clock function
        self._cached_account = None  # Cached account object

    def set_inventory_coordinator(self, coordinator) -> None:
        """Inject cross-pair inventory coordinator."""
        self._inventory_coordinator = coordinator
        if self.follower_instrument is not None:
            self.log.info(
                f"Cross-pair inventory coordinator injected for {self.follower_instrument.id.symbol.value}",
                color=LogColor.BLUE,
            )

    def on_start(self) -> None:
        gc.disable()
        
        # Cache clock function
        self._get_now_ns = self.clock.timestamp_ns
        
        self._next_refresh_ts_ns = self._now_ns() + (self.config.quote_refresh_offset_ms * 1_000_000)
        
        # Load instruments
        self.follower_instrument = self.cache.instrument(self.config.follower_instrument_id)
        if self.follower_instrument is None:
            self.log.error(f"Could not find follower instrument {self.config.follower_instrument_id}")
            self.stop()
            return

        self.leader_instrument = self.cache.instrument(self.config.leader_instrument_id)
        if self.leader_instrument is None:
            self.log.error(f"Could not find leader instrument {self.config.leader_instrument_id}")
            self.stop()
            return

        if self.config.global_guard_id is not None:
            self.guard_instrument = self.cache.instrument(self.config.global_guard_id)
            if self.guard_instrument is None:
                self.log.error(f"Could not find global guard instrument {self.config.global_guard_id}")
                self.stop()
                return

        # Initialize order books
        self.follower_book = OrderBook(
            instrument_id=self.follower_instrument.id,
            book_type=self.config.book_type,
        )
        self.leader_book = OrderBook(
            instrument_id=self.leader_instrument.id,
            book_type=self.config.book_type,
        )
        if self.guard_instrument is not None:
            self.guard_book = OrderBook(
                instrument_id=self.guard_instrument.id,
                book_type=self.config.book_type,
            )

        self._tick_size = self.follower_instrument.price_increment.as_decimal()
        self._order_qty = self.follower_instrument.make_qty(self.config.order_qty)

        # Subscribe to order book deltas
        self.subscribe_order_book_deltas(
            instrument_id=self.config.follower_instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )
        self.subscribe_order_book_deltas(
            instrument_id=self.config.leader_instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )
        if self.config.global_guard_id is not None and self.config.global_guard_id != self.config.leader_instrument_id:
            self.subscribe_order_book_deltas(
                instrument_id=self.config.global_guard_id,
                book_type=self.config.book_type,
                depth=self.config.book_depth,
            )

        # Sync state
        self._sync_position_with_exchange()
        self._sync_quote_budget()
        
        # Cache account object
        self._cached_account = self.cache.account_for_venue(self.config.follower_instrument_id.venue)
        if self._cached_account is None:
            accounts = self.cache.accounts()
            if accounts:
                self._cached_account = accounts[0]
        
        self._last_sync_ts_ns = self._now_ns()
        
        self.log.info(
            f"LLMMv5 STARTED: {self.config.follower_instrument_id.symbol.value} | "
            f"Spread={self.config.spread_bps}bps | OFI={self.config.ofi_enabled} | "
            f"Volatility={self.config.volatility_enabled} | EventDriven={self.config.event_driven_requote}",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        gc.enable()
        
        # Log final fill quality stats
        if self._fill_history:
            self.log.info(
                f"FILL QUALITY STATS: Good={self._good_fill_count}, Toxic={self._toxic_fill_count}, "
                f"Ratio={self._good_fill_count/(self._good_fill_count + self._toxic_fill_count + 1):.2%}",
                LogColor.CYAN,
            )

    # =========================================================================
    # ORDER BOOK DELTA HANDLERS
    # =========================================================================

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if deltas.instrument_id == self.config.leader_instrument_id:
            self._handle_leader_deltas(deltas)
            return

        if self.config.global_guard_id is not None and deltas.instrument_id == self.config.global_guard_id:
            self._handle_guard_deltas(deltas)
            return

        if deltas.instrument_id == self.config.follower_instrument_id:
            self._handle_follower_deltas(deltas)
            return

    def _handle_leader_deltas(self, deltas: OrderBookDeltas) -> None:
        """Handle leader (Binance) order book updates with event-driven requoting."""
        if self.leader_book is None:
            return

        self.leader_book.apply_deltas(deltas)
        self._last_leader_ts_ns = self._event_ts_ns(deltas)
        
        # Get mid price
        mid_f = self._book_mid_float(self.leader_book)
        if mid_f is None:
            return

        # Cache previous for change detection
        self._prev_leader_mid_f = self.leader_mid_f
        self.leader_mid = Decimal(str(mid_f))
        self.leader_mid_f = mid_f
        
        # Track Binance spread
        if self.config.binance_spread_tracking:
            self._update_leader_spread()
        
        if self.config.log_leader_updates:
            self.log.info(f"Leader mid update: {self.leader_mid}", LogColor.CYAN)
            
        self._update_guard()

        # === EVENT-DRIVEN REQUOTING (3.1) ===
        now_ns = self._now_ns()
        
        if self.config.event_driven_requote:
            # Check if leader mid changed significantly (1 tick or more)
            if self._prev_leader_mid_f > 0:
                change_bps = abs(mid_f - self._prev_leader_mid_f) / self._prev_leader_mid_f * 10000.0
                min_interval_ns = self.config.leader_update_min_interval_ms * 1_000_000
                
                if change_bps >= 1.0 and (now_ns - self._last_leader_requote_ts_ns) >= min_interval_ns:
                    # Leader moved! Race to update follower quotes
                    self._last_leader_requote_ts_ns = now_ns
                    next_refresh = self._refresh_quotes(now_ns)
                    if next_refresh:
                        self._next_refresh_ts_ns = next_refresh
                    return

        # Normal rate-limited refresh
        if now_ns >= self._next_refresh_ts_ns:
            next_refresh = self._refresh_quotes(now_ns)
            if next_refresh:
                self._next_refresh_ts_ns = next_refresh

    def _handle_guard_deltas(self, deltas: OrderBookDeltas) -> None:
        """Handle global guard (BTC) order book updates."""
        if self.guard_book is None:
            return

        self.guard_book.apply_deltas(deltas)
        self._last_guard_ts_ns = self._event_ts_ns(deltas)
        mid = self._book_mid(self.guard_book)
        if mid is None:
            return

        self.guard_mid = mid
        self._update_global_guard()

    def _handle_follower_deltas(self, deltas: OrderBookDeltas) -> None:
        """Handle follower (Bybit) order book updates."""
        if self.follower_book is None:
            return

        self.follower_book.apply_deltas(deltas)
        self._last_follower_ts_ns = self._event_ts_ns(deltas)
        
        # Get mid price
        mid_f = self._book_mid_float(self.follower_book)
        if mid_f is None:
            return

        self.follower_mid = Decimal(str(mid_f))
        self.follower_mid_f = mid_f
        
        # === VOLATILITY TRACKING (1.2) ===
        if self.config.volatility_enabled:
            self._update_volatility(mid_f)
        
        # === REGIME DETECTION (1.4) ===
        if self.config.regime_detection_enabled:
            self._update_regime(mid_f)
        
        # === QUEUE POSITION TRACKING (2.1) ===
        if self.config.queue_tracking_enabled:
            self._update_queue_positions()
        
        # === COMPETITIVE MONITORING (4.2) ===
        if self.config.competitive_monitoring:
            self._update_follower_spread()
        
        self._update_guard()
        now_ns = self._now_ns()
        self._update_markout(now_ns)

        if now_ns < self._next_refresh_ts_ns:
            return

        next_refresh_ts_ns = self._refresh_quotes(now_ns)
        if next_refresh_ts_ns:
            self._next_refresh_ts_ns = next_refresh_ts_ns

    # =========================================================================
    # TOXIC FLOW AVOIDANCE
    # =========================================================================

    def _calculate_ofi(self, book: OrderBook) -> float:
        """Calculate Order Flow Imbalance (OFI) from order book.
        
        Returns value in range [-1, 1]:
        - Positive = Buy pressure (price likely to go UP)
        - Negative = Sell pressure (price likely to go DOWN)
        """
        bid_levels = book.bids()
        ask_levels = book.asks()
        
        if not bid_levels or not ask_levels:
            return 0.0
        
        # Exponential decay weighting
        decay = 0.5
        w_bid_sum = 0.0
        w_ask_sum = 0.0
        
        depth = min(len(bid_levels), len(ask_levels), self.config.ofi_depth)
        
        for i in range(depth):
            weight = math.exp(-decay * i)
            w_bid_sum += float(bid_levels[i].size()) * weight
            w_ask_sum += float(ask_levels[i].size()) * weight
        
        total = w_bid_sum + w_ask_sum
        if total <= 0.0:
            return 0.0
        
        return (w_bid_sum - w_ask_sum) / total

    def _calculate_ofi_adjustments(self) -> tuple[Decimal, Decimal]:
        """Calculate asymmetric spread adjustments based on OFI.
        
        Returns (bid_adjustment_bps, ask_adjustment_bps):
        - Positive values WIDEN the spread on that side
        - Negative values TIGHTEN the spread on that side
        """
        if not self.config.ofi_enabled or self.follower_book is None:
            return Decimal("0"), Decimal("0")
        
        ofi = self._calculate_ofi(self.follower_book)
        self._current_ofi = ofi
        
        threshold = float(self.config.ofi_widen_threshold)
        
        bid_adj = Decimal("0")
        ask_adj = Decimal("0")
        
        if ofi > threshold:
            # Strong BUY pressure -> price likely going UP
            # WIDEN bid (avoid buying before pump), TIGHTEN ask (capture upward move)
            intensity = min((ofi - threshold) / (1.0 - threshold), 1.0)
            bid_adj = self.config.ofi_widen_bps * Decimal(str(intensity))
            ask_adj = -self.config.ofi_tighten_bps * Decimal(str(intensity))
            
            if self.config.log_toxic_flow:
                self.log.info(
                    f"OFI BUY PRESSURE: OFI={ofi:.3f} | Bid+{bid_adj:.1f}bps, Ask{ask_adj:.1f}bps",
                    LogColor.YELLOW,
                )
                
        elif ofi < -threshold:
            # Strong SELL pressure -> price likely going DOWN
            # TIGHTEN bid (capture downward move), WIDEN ask (avoid selling before dump)
            intensity = min((abs(ofi) - threshold) / (1.0 - threshold), 1.0)
            bid_adj = -self.config.ofi_tighten_bps * Decimal(str(intensity))
            ask_adj = self.config.ofi_widen_bps * Decimal(str(intensity))
            
            if self.config.log_toxic_flow:
                self.log.info(
                    f"OFI SELL PRESSURE: OFI={ofi:.3f} | Bid{bid_adj:.1f}bps, Ask+{ask_adj:.1f}bps",
                    LogColor.YELLOW,
                )
        
        self._ofi_bid_adjustment_bps = bid_adj
        self._ofi_ask_adjustment_bps = ask_adj
        
        return bid_adj, ask_adj

    def _update_volatility(self, mid_f: float) -> None:
        """Update rolling volatility estimate from mid-price changes."""
        if len(self._mid_price_history) > 0:
            last_mid = self._mid_price_history[-1]
            if last_mid > 0:
                change_bps = abs(mid_f - last_mid) / last_mid * 10000.0
                self._mid_price_history.append(mid_f)
                
                # Calculate rolling standard deviation of changes
                if len(self._mid_price_history) >= 3:
                    changes = []
                    mids = list(self._mid_price_history)
                    for i in range(1, len(mids)):
                        if mids[i-1] > 0:
                            changes.append(abs(mids[i] - mids[i-1]) / mids[i-1] * 10000.0)
                    
                    if changes:
                        mean = sum(changes) / len(changes)
                        variance = sum((c - mean) ** 2 for c in changes) / len(changes)
                        self._current_volatility_bps = math.sqrt(variance)
            else:
                self._mid_price_history.append(mid_f)
        else:
            self._mid_price_history.append(mid_f)

    def _calculate_volatility_adjustment(self) -> Decimal:
        """Calculate spread adjustment based on current volatility."""
        if not self.config.volatility_enabled:
            return Decimal("0")
        
        base_vol = float(self.config.volatility_base_threshold_bps)
        max_widen = float(self.config.volatility_max_widen_bps)
        
        if self._current_volatility_bps <= base_vol:
            return Decimal("0")
        
        # Linear scaling from base to 3x base
        excess_vol = self._current_volatility_bps - base_vol
        max_excess = base_vol * 2  # 3x baseline triggers max widening
        
        intensity = min(excess_vol / max_excess, 1.0)
        adjustment = Decimal(str(max_widen * intensity))
        
        if adjustment > Decimal("5") and self.config.log_spread_adjustments:
            self.log.info(
                f"VOLATILITY WIDEN: Vol={self._current_volatility_bps:.1f}bps | +{adjustment:.1f}bps",
                LogColor.YELLOW,
            )
        
        self._volatility_spread_adjustment_bps = adjustment
        return adjustment

    # =========================================================================
    # REGIME DETECTION (Trending vs Ranging)
    # =========================================================================

    def _update_regime(self, mid_f: float) -> None:
        """Update regime detection based on price movement pattern."""
        if not self.config.regime_detection_enabled:
            return
        
        self._regime_price_history.append(mid_f)
        
        if len(self._regime_price_history) < 10:
            return  # Need minimum data
        
        # Calculate trend strength using efficiency ratio
        # Formula: |net_move| / sum(|individual_moves|)
        # High ratio = trending (price moved efficiently in one direction)
        # Low ratio = ranging (price oscillated, lots of movement but little net change)
        
        prices = list(self._regime_price_history)
        net_move = abs(prices[-1] - prices[0])
        
        total_move = 0.0
        for i in range(1, len(prices)):
            total_move += abs(prices[i] - prices[i-1])
        
        if total_move > 0:
            self._current_trend_strength = net_move / total_move
        else:
            self._current_trend_strength = 0.0
        
        # Determine regime
        old_regime = self._current_regime
        trending_threshold = float(self.config.regime_trending_threshold)
        ranging_threshold = float(self.config.regime_ranging_threshold)
        
        if self._current_trend_strength >= trending_threshold:
            self._current_regime = "TRENDING"
            self._regime_spread_multiplier = self.config.regime_trending_spread_multiplier
            self._regime_size_multiplier = self.config.regime_trending_size_multiplier
        elif self._current_trend_strength <= ranging_threshold:
            self._current_regime = "RANGING"
            self._regime_spread_multiplier = Decimal("1.0")
            self._regime_size_multiplier = Decimal("1.0")
        # else: stay in current regime (hysteresis)
        
        # Log regime changes
        now_ns = self._now_ns()
        if old_regime != self._current_regime and self.config.log_regime_changes:
            direction = "UP" if prices[-1] > prices[0] else "DOWN"
            self.log.warning(
                f"🔄 REGIME CHANGE: {old_regime} → {self._current_regime} | "
                f"Trend={self._current_trend_strength:.2f} | Direction={direction} | "
                f"Spread={self._regime_spread_multiplier}x, Size={self._regime_size_multiplier}x",
                LogColor.MAGENTA if self._current_regime == "TRENDING" else LogColor.GREEN,
            )
            self._last_regime_log_ns = now_ns
        
        # Periodic logging when trending (every 30 seconds)
        elif self._current_regime == "TRENDING" and (now_ns - self._last_regime_log_ns) > 30_000_000_000:
            direction = "UP" if prices[-1] > prices[0] else "DOWN"
            self.log.info(
                f"⚠️ STILL TRENDING: Strength={self._current_trend_strength:.2f} | Direction={direction}",
                LogColor.YELLOW,
            )
            self._last_regime_log_ns = now_ns

    def _is_trending(self) -> bool:
        """Check if currently in trending regime."""
        return self._current_regime == "TRENDING"

    def _get_dynamic_quote_lifetime_ms(self) -> int:
        """Get quote lifetime based on current volatility."""
        if not self.config.dynamic_lifetime_enabled:
            return self.config.min_quote_lifetime_ms
        
        base_vol = float(self.config.volatility_base_threshold_bps)
        
        if self._current_volatility_bps > base_vol * 2:
            # High volatility -> fast requotes
            return self.config.lifetime_volatility_fast_ms
        elif self._current_volatility_bps < base_vol:
            # Low volatility -> slower requotes
            return self.config.lifetime_volatility_slow_ms
        else:
            # Linear interpolation
            ratio = (self._current_volatility_bps - base_vol) / base_vol
            fast = self.config.lifetime_volatility_fast_ms
            slow = self.config.lifetime_volatility_slow_ms
            return int(slow - (slow - fast) * ratio)

    # =========================================================================
    # QUEUE POSITION OPTIMIZATION
    # =========================================================================

    def _update_queue_positions(self) -> None:
        """Estimate our position in queue at current price levels."""
        if self.follower_book is None:
            return
        
        # Get best bid/ask sizes
        best_bid_size = self.follower_book.best_bid_size()
        best_ask_size = self.follower_book.best_ask_size()
        
        if best_bid_size is not None:
            current_bid_qty = float(best_bid_size)
            # Estimate queue position as % of level that arrived after us
            if self._last_bid_level_qty > 0 and current_bid_qty > self._last_bid_level_qty:
                # Queue grew -> we moved back
                new_qty = current_bid_qty - self._last_bid_level_qty
                self._bid_queue_position_est = min(
                    self._bid_queue_position_est + new_qty / current_bid_qty * 100,
                    100.0
                )
            elif current_bid_qty < self._last_bid_level_qty:
                # Queue shrunk -> we moved forward
                removed_qty = self._last_bid_level_qty - current_bid_qty
                self._bid_queue_position_est = max(
                    self._bid_queue_position_est - removed_qty / self._last_bid_level_qty * 100,
                    0.0
                )
            self._last_bid_level_qty = current_bid_qty
        
        if best_ask_size is not None:
            current_ask_qty = float(best_ask_size)
            if self._last_ask_level_qty > 0 and current_ask_qty > self._last_ask_level_qty:
                new_qty = current_ask_qty - self._last_ask_level_qty
                self._ask_queue_position_est = min(
                    self._ask_queue_position_est + new_qty / current_ask_qty * 100,
                    100.0
                )
            elif current_ask_qty < self._last_ask_level_qty:
                removed_qty = self._last_ask_level_qty - current_ask_qty
                self._ask_queue_position_est = max(
                    self._ask_queue_position_est - removed_qty / self._last_ask_level_qty * 100,
                    0.0
                )
            self._last_ask_level_qty = current_ask_qty

    def _should_move_closer_to_mid(self, side: OrderSide) -> bool:
        """Check if we should move order closer to mid based on queue position."""
        if not self.config.queue_tracking_enabled:
            return False
        
        max_queue_pct = float(self.config.queue_max_position_pct)
        
        if side == OrderSide.BUY:
            return self._bid_queue_position_est > max_queue_pct
        else:
            return self._ask_queue_position_est > max_queue_pct

    def _calculate_inventory_urgency_adjustment(self) -> Decimal:
        """Reduce spread when inventory is out of balance and we need fills."""
        if not self.config.inventory_urgency_enabled:
            return Decimal("0")
        
        max_pos = float(self.config.max_position_qty)
        if max_pos == 0:
            return Decimal("0")
        
        inventory_pct = abs(float(self._net_position)) / max_pos * 100
        threshold = float(self.config.urgency_threshold_pct)
        
        if inventory_pct > threshold:
            # High inventory -> reduce spread to get fills
            intensity = min((inventory_pct - threshold) / (100 - threshold), 1.0)
            reduction = self.config.urgency_spread_reduction_bps * Decimal(str(intensity))
            return -reduction  # Negative = tighter spread
        
        return Decimal("0")

    # =========================================================================
    # SPREAD INTELLIGENCE
    # =========================================================================

    def _update_leader_spread(self) -> None:
        """Track Binance spread for competitive intelligence."""
        if self.leader_book is None:
            return
        
        bid = self.leader_book.best_bid_price()
        ask = self.leader_book.best_ask_price()
        
        if bid is None or ask is None or float(bid) <= 0:
            return
        
        self._binance_spread_bps = (float(ask) - float(bid)) / float(bid) * 10000.0

    def _update_follower_spread(self) -> None:
        """Track Bybit spread for competitive monitoring."""
        if self.follower_book is None:
            return
        
        bid = self.follower_book.best_bid_price()
        ask = self.follower_book.best_ask_price()
        
        if bid is None or ask is None or float(bid) <= 0:
            return
        
        self._follower_spread_bps = (float(ask) - float(bid)) / float(bid) * 10000.0

    def _calculate_binance_spread_adjustment(self) -> Decimal:
        """Widen spread when Binance spread is unusually wide."""
        if not self.config.binance_spread_tracking:
            return Decimal("0")
        
        threshold = float(self.config.binance_wide_threshold_bps)
        if self._binance_spread_bps > threshold:
            # Binance spread is wide -> widen ours too (less competition)
            return self.config.binance_wide_spread_add_bps
        
        return Decimal("0")

    # =========================================================================
    # GUARD LOGIC
    # =========================================================================

    def _book_mid(self, book: OrderBook) -> Decimal | None:
        """Get mid price as Decimal."""
        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            return None
        return Decimal((bid + ask) / 2)

    def _book_mid_float(self, book: OrderBook) -> float | None:
        """Get mid price as float (faster for hot path)."""
        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            return None
        return (float(bid) + float(ask)) / 2.0

    def _update_guard(self) -> None:
        """Update lead-lag guard state."""
        if self.leader_mid_f == 0.0 or self.follower_mid_f == 0.0:
            return

        if self._is_data_stale():
            return

        diff_bps = (self.leader_mid_f - self.follower_mid_f) / self.follower_mid_f * 10000.0
        self._current_diff_bps = diff_bps

        threshold = float(self.config.guard_threshold_bps)
        hysteresis = float(self.config.guard_hysteresis_bps)

        block_buy = diff_bps < -threshold
        block_sell = diff_bps > threshold

        if abs(diff_bps) < hysteresis:
            block_buy = False
            block_sell = False

        if block_buy != self._guard_block_buy or block_sell != self._guard_block_sell:
            self._guard_block_buy = block_buy
            self._guard_block_sell = block_sell
            
            if self.config.log_guard_events:
                self.log.info(
                    f"Guard state: diff_bps={diff_bps:.2f}, block_buy={block_buy}, block_sell={block_sell}",
                    LogColor.YELLOW,
                )

            if block_buy:
                if self.config.log_guard_events:
                    self.log.warning(f"TOXIC DUMP: diff_bps={diff_bps:.2f} -> cancel buys", LogColor.RED)
                self._cancel_side_orders(OrderSide.BUY)
            if block_sell:
                if self.config.log_guard_events:
                    self.log.warning(f"TOXIC PUMP: diff_bps={diff_bps:.2f} -> cancel sells", LogColor.RED)
                self._cancel_side_orders(OrderSide.SELL)

    def _update_global_guard(self) -> None:
        """Update global guard (BTC king) state."""
        if self.guard_mid is None:
            return

        if self._is_guard_data_stale():
            return

        window_ns = self.config.global_guard_window_ms * 1_000_000
        now_ns = self._now_ns()

        if self._last_guard_mid is None:
            self._last_guard_mid = self.guard_mid
            self._last_guard_ts_ns = now_ns
            return

        if now_ns - self._last_guard_ts_ns > window_ns:
            self._last_guard_mid = self.guard_mid
            self._last_guard_ts_ns = now_ns
            return

        guard_mid = float(self.guard_mid)
        last_mid = float(self._last_guard_mid)
        diff_bps = (guard_mid - last_mid) / last_mid * 10000.0
        threshold = float(self.config.global_guard_threshold_bps)
        self._global_guard_active = abs(diff_bps) >= threshold

        if self._global_guard_active and self.config.log_guard_events:
            self.log.warning(f"GLOBAL GUARD: diff_bps={diff_bps:.2f} -> cancel quotes", LogColor.RED)
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)

    def _cancel_side_orders(self, side: OrderSide) -> None:
        """Cancel all orders on a specific side."""
        open_orders = self.cache.orders_open(
            instrument_id=self.config.follower_instrument_id,
            strategy_id=self.id,
        )
        for order in open_orders:
            if order.side == side:
                self.cancel_order(order, client_id=self.client_id)

    # =========================================================================
    # QUOTE REFRESH
    # =========================================================================

    def _refresh_quotes(self, now_ns: int) -> int:
        """Main quote refresh logic with all optimizations."""
        # Periodic syncs
        if now_ns - self._last_sync_ts_ns > 5 * 1_000_000_000:
            self._sync_position_with_exchange()
            self._sync_quote_budget()
            self._last_sync_ts_ns = now_ns

        if now_ns - self._last_metrics_ts_ns > self.config.metrics_snapshot_interval_secs * 1_000_000_000:
            self._emit_account_snapshot(now_ns)
            self._last_metrics_ts_ns = now_ns

        if self.follower_instrument is None or self.follower_mid is None:
            return 0

        if self._check_killswitch():
            return 0

        # Check for pending orders
        pending_statuses = (OrderStatus.PENDING_CANCEL, OrderStatus.PENDING_UPDATE)
        if self._bid_order is not None and self._bid_order.status in pending_statuses:
            return 0
        if self._ask_order is not None and self._ask_order.status in pending_statuses:
            return 0

        # Guard blocks
        if self._guard_block_buy or self._guard_block_sell or self._global_guard_active:
            return 0

        if self._is_data_stale():
            return 0

        # Rate limiting with dynamic lifetime
        jitter = random.randint(0, max(0, self.config.quote_refresh_jitter_ms)) * 1_000_000
        min_interval = (self.config.quote_refresh_interval_ms * 1_000_000) + jitter
        if now_ns - self._last_quote_ts_ns < min_interval:
            return 0

        self._last_quote_ts_ns = now_ns

        # Update markout
        self._update_markout(now_ns)

        # Clean up closed orders
        if self._bid_order and self._bid_order.is_closed:
            self._bid_order = None
        if self._ask_order and self._ask_order.is_closed:
            self._ask_order = None

        # Get inventory adjustments
        inventory_skew, bid_qty, ask_qty = self._inventory_adjustments()

        # === CALCULATE DYNAMIC SPREAD ===
        base_spread_bps = self._current_spread_bps()
        
        # 1.1 OFI adjustments (asymmetric)
        ofi_bid_adj, ofi_ask_adj = self._calculate_ofi_adjustments()
        
        # 1.2 Volatility adjustment (symmetric)
        vol_adj = self._calculate_volatility_adjustment()
        
        # 2.3 Inventory urgency adjustment
        urgency_adj = self._calculate_inventory_urgency_adjustment()
        
        # 4.1 Binance spread adjustment
        binance_adj = self._calculate_binance_spread_adjustment()
        
        # 5. Realized Spread Guardian adjustment (ensures fees are covered)
        guardian_adj = self._guardian_extra_spread_bps if self.config.realized_spread_guardian_enabled else Decimal("0")
        
        # === REGIME-BASED ADJUSTMENTS (Trending vs Ranging) ===
        if self.config.regime_detection_enabled and self._is_trending():
            # Option 1: Pause entirely when trending
            if self.config.regime_pause_when_trending:
                return 0  # Don't quote at all
            
            # Option 2: Apply spread multiplier to make quotes less attractive
            # This is applied multiplicatively to widen spreads significantly
            base_spread_bps = base_spread_bps * self._regime_spread_multiplier
            
            # Also reduce order sizes
            if bid_qty is not None:
                bid_qty = self.follower_instrument.make_qty(
                    max(bid_qty.as_decimal() * self._regime_size_multiplier, self.config.min_order_qty)
                )
            if ask_qty is not None:
                ask_qty = self.follower_instrument.make_qty(
                    max(ask_qty.as_decimal() * self._regime_size_multiplier, self.config.min_order_qty)
                )
        
        # Calculate final spreads for each side
        bid_spread_bps = base_spread_bps + ofi_bid_adj + vol_adj + urgency_adj + binance_adj + guardian_adj
        ask_spread_bps = base_spread_bps + ofi_ask_adj + vol_adj + urgency_adj + binance_adj + guardian_adj
        
        # Ensure minimum profitability
        fee_floor = (self.config.maker_fee_bps * Decimal("2")) + self.config.min_profit_bps
        bid_spread_bps = max(bid_spread_bps, fee_floor)
        ask_spread_bps = max(ask_spread_bps, fee_floor)
        
        if self.config.log_spread_adjustments and (ofi_bid_adj != 0 or vol_adj > 5 or guardian_adj > 0):
            self.log.info(
                f"SPREAD: Base={base_spread_bps:.1f} | OFI[B/A]={ofi_bid_adj:.1f}/{ofi_ask_adj:.1f} | "
                f"Vol={vol_adj:.1f} | Guardian={guardian_adj:.1f} | Final[B/A]={bid_spread_bps:.1f}/{ask_spread_bps:.1f}",
                LogColor.CYAN,
            )

        # Calculate prices
        bid_spread_half = self.follower_mid * (bid_spread_bps / Decimal("20000"))
        ask_spread_half = self.follower_mid * (ask_spread_bps / Decimal("20000"))
        
        if self.leader_mid is None:
            fair_price = self.follower_mid
        else:
            fair_price = (self.follower_mid * Decimal("0.1")) + (self.leader_mid * Decimal("0.9"))

        raw_bid = (fair_price - bid_spread_half) + inventory_skew
        raw_ask = (fair_price + ask_spread_half) + inventory_skew

        # Clamp to BBO
        best_bid = self.follower_book.best_bid_price() if self.follower_book else None
        best_ask = self.follower_book.best_ask_price() if self.follower_book else None
        if best_bid is None or best_ask is None:
            return 0

        tick = self._tick_size if self._tick_size else Decimal("0")
        best_bid_dec = best_bid.as_decimal()
        best_ask_dec = best_ask.as_decimal()
        clamp_bid = best_ask_dec - tick if tick else best_ask_dec
        clamp_ask = best_bid_dec + tick if tick else best_bid_dec

        desired_bid = min(raw_bid, clamp_bid)
        desired_ask = max(raw_ask, clamp_ask)

        # Ensure valid spread
        if desired_bid >= desired_ask:
            mid = (best_bid_dec + best_ask_dec) / Decimal("2")
            desired_bid = mid - (tick * 2 if tick else Decimal("0.01"))
            desired_ask = mid + (tick * 2 if tick else Decimal("0.01"))

        # === HARD POSITION CAP ENFORCEMENT ===
        if self.config.hard_position_cap_enabled:
            cap = self.config.max_position_qty * self.config.position_cap_buffer_pct
            if self._net_position >= cap:
                if bid_qty is not None:
                    self.log.warning(
                        f"POSITION CAP: Long {self._net_position:.4f} >= {cap:.4f} -> blocking BUY",
                        LogColor.RED,
                    )
                bid_qty = None  # Block buys at max long
            if self._net_position <= -cap:
                if ask_qty is not None:
                    self.log.warning(
                        f"POSITION CAP: Short {self._net_position:.4f} <= -{cap:.4f} -> blocking SELL",
                        LogColor.RED,
                    )
                ask_qty = None  # Block sells at max short

        # === RUNAWAY FILL DETECTION PAUSE ===
        if self.config.runaway_detection_enabled:
            now_ns = self._now_ns()
            if now_ns < self._runaway_pause_buy_until_ns:
                bid_qty = None  # Still paused
            if now_ns < self._runaway_pause_sell_until_ns:
                ask_qty = None  # Still paused

        # Place/replace orders
        new_orders = []

        if bid_qty is not None and not self._guard_block_buy:
            order = self._place_or_replace(
                OrderSide.BUY, desired_bid, fair_price, bid_spread_bps, now_ns, bid_qty, defer_submit=True
            )
            if order:
                new_orders.append(order)

        if ask_qty is not None and not self._guard_block_sell:
            order = self._place_or_replace(
                OrderSide.SELL, desired_ask, fair_price, ask_spread_bps, now_ns, ask_qty, defer_submit=True
            )
            if order:
                new_orders.append(order)

        # Batch submit
        if new_orders:
            if len(new_orders) > 1:
                order_list = self.order_factory.create_list(new_orders)
                self.submit_order_list(order_list)
            else:
                self.submit_order(new_orders[0])

        return now_ns + min_interval

    def _place_or_replace(
        self,
        side: OrderSide,
        desired_price: Decimal,
        fair_price: Decimal,
        spread_bps: Decimal,
        now_ns: int,
        desired_qty: Quantity,
        defer_submit: bool = False,
    ) -> Order | None:
        """Place new order or replace existing one."""
        if desired_qty.as_decimal() < self.config.min_order_qty:
            return None

        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts_ns = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns

        # Check if order needs update
        if order is not None and not order.is_closed:
            current_price = order.price.as_decimal()
            current_qty = order.quantity.as_decimal() if order.quantity else None
            if current_qty is not None:
                if desired_price == current_price and desired_qty.as_decimal() == current_qty:
                    return None

        if self._should_replace(order, order_ts_ns, desired_price, fair_price, spread_bps, desired_qty, now_ns):
            # Cancel existing order
            if order is not None and not order.is_closed:
                self.cancel_order(order, client_id=self.client_id)

            # Create new order
            price = self.follower_instrument.make_price(desired_price)
            new_order = self.order_factory.limit(
                instrument_id=self.config.follower_instrument_id,
                order_side=side,
                price=price,
                quantity=desired_qty,
                time_in_force=self.config.time_in_force,
                post_only=self.config.post_only,
            )

            if side == OrderSide.BUY:
                self._bid_order = new_order
                self._bid_order_ts_ns = now_ns
                self._bid_submit_ts_ns = now_ns
                self._bid_queue_position_est = 0.0  # Reset queue position
            else:
                self._ask_order = new_order
                self._ask_order_ts_ns = now_ns
                self._ask_submit_ts_ns = now_ns
                self._ask_queue_position_est = 0.0

            if defer_submit:
                return new_order
            
            self.submit_order(new_order)
        
        return None

    def _should_replace(
        self,
        order: Order | None,
        order_ts_ns: int,
        desired_price: Decimal,
        fair_price: Decimal,
        spread_bps: Decimal,
        desired_qty: Quantity,
        now_ns: int,
    ) -> bool:
        """Determine if order should be replaced."""
        if order is None or order.is_closed:
            return True

        # Dynamic quote lifetime
        lifetime_ms = self._get_dynamic_quote_lifetime_ms()
        age_ms = (now_ns - order_ts_ns) / 1_000_000
        if age_ms < lifetime_ms:
            return False

        current_price = order.price.as_decimal()

        # Edge-based requote logic
        if fair_price > Decimal("0"):
            if order.side == OrderSide.BUY:
                current_edge_bps = (fair_price - current_price) / fair_price * Decimal("10000")
            else:
                current_edge_bps = (current_price - fair_price) / fair_price * Decimal("10000")

            min_edge = spread_bps * self.config.edge_min_ratio
            max_edge = spread_bps * self.config.edge_max_ratio
            if current_edge_bps < min_edge:
                return True
            if current_edge_bps > max_edge:
                return True

        # Tick-based requote
        ticks_delta = (abs(desired_price - current_price) / self._tick_size) if self._tick_size else Decimal("0")
        quantity_delta = Decimal("0")
        if hasattr(order, "quantity") and order.quantity is not None:
            quantity_delta = abs(order.quantity.as_decimal() - desired_qty.as_decimal())

        return ticks_delta >= self.config.min_requote_ticks or quantity_delta > Decimal("0")

    def _current_spread_bps(self) -> Decimal:
        """Calculate base spread with markout adjustment."""
        multiplier = Decimal("1")
        if self.config.markout_widen_bps > Decimal("0"):
            widen = self._markout_ema_bps / self.config.markout_widen_bps
            widen = min(widen, self.config.markout_max_spread_multiplier - Decimal("1"))
            if widen > Decimal("0"):
                multiplier += widen
        return self.config.spread_bps * multiplier

    # =========================================================================
    # INVENTORY & SIZING
    # =========================================================================

    def _inventory_adjustments(self) -> tuple[Decimal, Quantity | None, Quantity | None]:
        """Calculate inventory skew and order sizes."""
        if self.follower_mid is None or self._order_qty is None:
            return Decimal("0"), None, None

        optimal_target = self.config.max_position_qty / Decimal("2")
        skew_bps = Decimal(
            str(
                self._inventory_manager.calculate_skew(
                    position=self._net_position,
                    optimal_target=optimal_target,
                    mid_price=float(self.follower_mid),
                ),
            ),
        )
        limit = self.config.internal_price_delta_limit
        if limit > Decimal("0"):
            skew_bps = max(-limit, min(limit, skew_bps))
        skew_px = self.follower_mid * (skew_bps / Decimal("10000"))

        bid_size, ask_size = self._inventory_manager.calculate_sizes(
            position=self._net_position,
            optimal_target=optimal_target,
            base_size=self.config.order_qty,
        )

        # Cross-pair inventory coordinator
        if self._inventory_coordinator is not None:
            quote_currency = Currency.from_str("USDT")
            
            if self._inventory_coordinator.should_skip_pair(
                self.follower_instrument.base_currency,
                quote_currency,
            ):
                return skew_px, None, None
            
            size_scalar = self._inventory_coordinator.get_size_scalar(
                self.follower_instrument.base_currency,
                quote_currency,
                bid_size,
            )
            
            bid_size = bid_size * size_scalar
            ask_size = ask_size * size_scalar

        bid_size = self._calculate_dynamic_size(bid_size, OrderSide.BUY)
        ask_size = self._calculate_dynamic_size(ask_size, OrderSide.SELL)

        bid_qty = self._apply_balance_limits(bid_size, OrderSide.BUY)
        ask_qty = self._apply_balance_limits(ask_size, OrderSide.SELL)

        return skew_px, bid_qty, ask_qty

    def _calculate_dynamic_size(self, base_qty: Decimal, side: OrderSide) -> Decimal:
        """Calculate dynamic order size based on market conditions."""
        abs_diff = abs(self._current_diff_bps)
        
        if abs_diff < 2.0:
            vol_scalar = Decimal("1.0")
        elif abs_diff > 10.0:
            vol_scalar = Decimal("0.1")
        else:
            decay_factor = (abs_diff - 2.0) / 8.0
            vol_scalar = Decimal(str(1.0 - (0.9 * decay_factor)))

        depth_scalar = Decimal("1.0")
        if self.leader_book is not None:
            if side == OrderSide.BUY:
                best_bid_size = self.leader_book.best_bid_size()
                if best_bid_size is not None:
                    best_bid_qty = best_bid_size.as_decimal()
                    if best_bid_qty > self.config.liquidity_high_qty:
                        depth_scalar = self.config.liquidity_high_scalar
                    elif best_bid_qty < self.config.liquidity_low_qty:
                        depth_scalar = self.config.liquidity_low_scalar
            elif side == OrderSide.SELL:
                best_ask_size = self.leader_book.best_ask_size()
                if best_ask_size is not None:
                    best_ask_qty = best_ask_size.as_decimal()
                    if best_ask_qty > self.config.liquidity_high_qty:
                        depth_scalar = self.config.liquidity_high_scalar
                    elif best_ask_qty < self.config.liquidity_low_qty:
                        depth_scalar = self.config.liquidity_low_scalar

        final_size = base_qty * vol_scalar * depth_scalar
        final_size = min(final_size, self.config.max_order_qty)
        min_floor = self.config.order_qty * Decimal("0.2")
        final_size = max(final_size, min_floor, self.config.min_order_qty)
        return final_size

    def _apply_balance_limits(self, base_qty: Decimal, side: OrderSide) -> Quantity | None:
        """Apply balance limits to order size."""
        if self.follower_instrument is None or self.follower_mid is None:
            return None

        base_currency = self.follower_instrument.base_currency
        quote_currency = self.follower_instrument.quote_currency

        if side == OrderSide.BUY:
            quote_balance = self._get_available_balance(quote_currency)
            if self._local_quote_budget is None:
                budget = quote_balance
            else:
                budget = max(self._local_quote_budget, quote_balance)
            reserve_ratio = budget * self.config.min_quote_reserve_ratio
            reserve_abs = self.config.min_quote_reserve_usdt
            reserve = max(reserve_ratio, reserve_abs)
            available = max(budget - reserve, Decimal("0"))
            max_buy = (available * self.config.min_balance_ratio) / self.follower_mid
            qty = min(base_qty, max_buy)
        else:
            base_balance = self._get_available_balance(base_currency)
            max_sell = base_balance * self.config.min_balance_ratio
            qty = min(base_qty, max_sell)

        if qty < self.config.min_order_qty or qty <= Decimal("0"):
            return None

        return self.follower_instrument.make_qty(qty)

    # =========================================================================
    # MARKOUT & FILL QUALITY
    # =========================================================================

    def _update_markout(self, now_ns: int) -> None:
        """Update markout EMA from completed fills."""
        if self.follower_mid is None or not self._markout_pending:
            return

        window_ns = self.config.markout_window_ms * 1_000_000
        remaining: list[tuple[int, OrderSide, Decimal]] = []
        
        for ts_ns, side, price in self._markout_pending:
            if now_ns - ts_ns < window_ns:
                remaining.append((ts_ns, side, price))
                continue

            if price <= Decimal("0"):
                continue

            # Calculate markout
            if side == OrderSide.BUY:
                markout_bps = (self.follower_mid - price) / price * Decimal("10000")
            else:
                markout_bps = (price - self.follower_mid) / price * Decimal("10000")

            raw_val = -markout_bps
            
            alpha = self.config.markout_ema_alpha
            self._markout_ema_bps = (alpha * raw_val) + ((Decimal("1") - alpha) * self._markout_ema_bps)
            self._markout_ema_bps = max(Decimal("0"), self._markout_ema_bps)

            # Track fill quality
            if self.config.fill_analytics_enabled:
                is_toxic = markout_bps < Decimal("-5")  # Lost more than 5 bps after fill
                if is_toxic:
                    self._toxic_fill_count += 1
                else:
                    self._good_fill_count += 1

            if self.config.log_markout_events:
                self.log.info(
                    f"Markout bps={markout_bps:.2f}, ema={self._markout_ema_bps:.2f}",
                    LogColor.MAGENTA,
                )

        self._markout_pending = remaining

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _calculate_total_equity(self) -> Decimal:
        if self.follower_instrument is None:
            return Decimal("0")

        quote_balance = self._get_available_balance(self.follower_instrument.quote_currency)
        base_balance = self._get_available_balance(self.follower_instrument.base_currency)
        mid = self.follower_mid if self.follower_mid is not None else Decimal("0")
        return quote_balance + (base_balance * mid)

    def _check_killswitch(self) -> bool:
        if self._killswitch_triggered:
            return True

        if self._starting_equity is None:
            equity = self._calculate_total_equity()
            if equity > Decimal("0"):
                self._starting_equity = equity
                self.log.info(
                    f"KILLSWITCH ARMED: Starting Equity = {self._starting_equity} USDT",
                    LogColor.YELLOW,
                )
            return False

        current_equity = self._calculate_total_equity()
        if current_equity <= Decimal("0"):
            return False

        drawdown = (self._starting_equity - current_equity) / self._starting_equity
        if drawdown > self.config.max_drawdown_pct:
            self.log.error(
                f"KILLSWITCH TRIGGERED: Drawdown {drawdown:.2%} > Limit {self.config.max_drawdown_pct:.2%}",
                LogColor.RED,
            )
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
            self._killswitch_triggered = True
            self.stop()
            return True

        return False

    def _get_available_balance(self, currency: Currency) -> Decimal:
        if self._cached_account is None:
            return Decimal("0")

        balance = self._cached_account.balance_free(currency)
        if balance is None:
            return Decimal("0")
        return balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)

    def _sync_quote_budget(self) -> None:
        if self.follower_instrument is None or self._cached_account is None:
            return

        quote_currency = self.follower_instrument.quote_currency
        quote_balance = self._cached_account.balance_total(quote_currency)
        if quote_balance is None:
            return

        self._local_quote_budget = (
            quote_balance.as_decimal() if hasattr(quote_balance, "as_decimal") else Decimal(quote_balance)
        )

    def _sync_position_with_exchange(self) -> None:
        if self.follower_instrument is None or self._cached_account is None:
            return

        base_currency = self.follower_instrument.base_currency
        wallet_balance = self._cached_account.balance_total(base_currency)
        if wallet_balance is None:
            return

        real_qty = wallet_balance.as_decimal() if hasattr(wallet_balance, "as_decimal") else Decimal(wallet_balance)
        drift = real_qty - self._net_position
        if abs(drift) > self.config.min_order_qty:
            self.log.warning(
                f"DRIFT DETECTED: Algo={self._net_position:.4f} vs Wallet={real_qty:.4f} -> Syncing.",
                LogColor.YELLOW,
            )
            self._net_position = real_qty

    def _emit_account_snapshot(self, now_ns: int) -> None:
        if self.follower_instrument is None or self._cached_account is None:
            return

        base_currency = self.follower_instrument.base_currency
        quote_currency = self.follower_instrument.quote_currency

        base_balance = self._cached_account.balance_total(base_currency)
        quote_balance = self._cached_account.balance_total(quote_currency)
        if base_balance is None or quote_balance is None:
            return

        base_qty = base_balance.as_decimal() if hasattr(base_balance, "as_decimal") else Decimal(base_balance)
        quote_qty = quote_balance.as_decimal() if hasattr(quote_balance, "as_decimal") else Decimal(quote_balance)
        mid = self.follower_mid if self.follower_mid is not None else Decimal("0")
        equity = quote_qty + (base_qty * mid)

        self._metrics.send(
            table="live_account_snapshot",
            tags={
                "strategy": "LLMMv5",
                "venue": self.config.follower_instrument_id.venue.value,
                "symbol": self.config.follower_instrument_id.symbol.value,
            },
            fields={
                "base_qty": base_qty,
                "quote_qty": quote_qty,
                "mid": mid,
                "equity": equity,
                "equity_usd": equity,
                "net_position": self._net_position,
                "ofi": self._current_ofi,
                "volatility_bps": self._current_volatility_bps,
                "markout_ema_bps": self._markout_ema_bps,
                "toxic_fill_count": self._toxic_fill_count,
                "good_fill_count": self._good_fill_count,
            },
            ts_ns=now_ns,
        )

    # =========================================================================
    # RUNAWAY FILL DETECTION
    # =========================================================================

    def _check_runaway_fills(self) -> None:
        """Check for runaway one-sided fills and pause accumulating side."""
        if len(self._recent_fill_sides) < self.config.runaway_window_fills:
            return  # Not enough data yet
        
        buy_count = sum(1 for s in self._recent_fill_sides if s == OrderSide.BUY)
        total = len(self._recent_fill_sides)
        buy_ratio = Decimal(str(buy_count / total))
        threshold = self.config.runaway_threshold_pct
        
        now_ns = self._now_ns()
        pause_ns = self.config.runaway_pause_secs * 1_000_000_000
        
        if buy_ratio >= threshold:
            # Too many buys - accumulating long, pause buying
            if not self._runaway_alert_logged or now_ns > self._runaway_pause_buy_until_ns:
                self.log.warning(
                    f"🚨 RUNAWAY BUYS: {buy_count}/{total} fills ({buy_ratio*100:.0f}%) are BUYs -> "
                    f"PAUSING BUY for {self.config.runaway_pause_secs}s | Position={self._net_position:.4f}",
                    LogColor.RED,
                )
                self._runaway_alert_logged = True
            self._runaway_pause_buy_until_ns = now_ns + pause_ns
            # Cancel existing buy orders
            self._cancel_side_orders(OrderSide.BUY)
            
        elif buy_ratio <= (Decimal("1") - threshold):
            # Too many sells - accumulating short, pause selling
            if not self._runaway_alert_logged or now_ns > self._runaway_pause_sell_until_ns:
                self.log.warning(
                    f"🚨 RUNAWAY SELLS: {total - buy_count}/{total} fills ({(1-buy_ratio)*100:.0f}%) are SELLs -> "
                    f"PAUSING SELL for {self.config.runaway_pause_secs}s | Position={self._net_position:.4f}",
                    LogColor.RED,
                )
                self._runaway_alert_logged = True
            self._runaway_pause_sell_until_ns = now_ns + pause_ns
            # Cancel existing sell orders
            self._cancel_side_orders(OrderSide.SELL)
        else:
            # Balanced fills - reset alert flag
            self._runaway_alert_logged = False

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================

    def on_event(self, event) -> None:
        """Handle fill events."""
        if hasattr(event, "last_qty") and hasattr(event, "order_side"):
            if event.order_side == OrderSide.BUY:
                self._net_position += event.last_qty.as_decimal()
            elif event.order_side == OrderSide.SELL:
                self._net_position -= event.last_qty.as_decimal()

            # === RUNAWAY FILL DETECTION ===
            if self.config.runaway_detection_enabled:
                self._recent_fill_sides.append(event.order_side)
                self._check_runaway_fills()

            price = None
            if hasattr(event, "last_px") and event.last_px is not None:
                price = event.last_px.as_decimal()
            elif hasattr(event, "price") and event.price is not None:
                price = event.price.as_decimal()

            if price is not None:
                # Update quote budget
                if self._local_quote_budget is not None:
                    notional = event.last_qty.as_decimal() * price
                    if event.order_side == OrderSide.BUY:
                        self._local_quote_budget = max(self._local_quote_budget - notional, Decimal("0"))
                    elif event.order_side == OrderSide.SELL:
                        self._local_quote_budget += notional

                # Update WAP ledger
                commission = getattr(event, "commission", None)
                commission_val = commission.as_decimal() if hasattr(commission, "as_decimal") else commission
                self._update_wap_ledger(
                    event.order_side,
                    price,
                    event.last_qty.as_decimal(),
                    Decimal(str(commission_val)) if commission_val is not None else Decimal("0"),
                )

                # Track markout
                ts_event = getattr(event, "ts_event", None)
                ts_ns = self._event_ts_ns_from_event(ts_event)
                self._markout_pending.append((ts_ns, event.order_side, price))

                # Record fill for analytics
                if self.config.fill_analytics_enabled:
                    submit_ts = self._bid_submit_ts_ns if event.order_side == OrderSide.BUY else self._ask_submit_ts_ns
                    time_in_book_ms = (ts_ns - submit_ts) / 1_000_000 if submit_ts > 0 else 0
                    queue_pos = self._bid_queue_position_est if event.order_side == OrderSide.BUY else self._ask_queue_position_est
                    
                    fill_record = FillRecord(
                        ts_ns=ts_ns,
                        side=event.order_side,
                        price=float(price),
                        qty=float(event.last_qty.as_decimal()),
                        mid_at_fill=self.follower_mid_f,
                        ofi_at_fill=self._current_ofi,
                        volatility_at_fill=self._current_volatility_bps,
                        time_in_book_ms=time_in_book_ms,
                        queue_position_est=queue_pos,
                    )
                    self._fill_history.append(fill_record)

                # Send metrics
                liquidity_side = getattr(event, "liquidity_side", None)
                self._metrics.send(
                    table="live_fills",
                    tags={
                        "strategy": "LLMMv5",
                        "venue": self.config.follower_instrument_id.venue.value,
                        "symbol": self.config.follower_instrument_id.symbol.value,
                        "side": event.order_side.name,
                        "liquidity": getattr(liquidity_side, "name", None),
                    },
                    fields={
                        "qty": event.last_qty.as_decimal(),
                        "price": price,
                        "commission": commission_val,
                        "ofi": self._current_ofi,
                        "volatility_bps": self._current_volatility_bps,
                        "time_in_book_ms": time_in_book_ms if self.config.fill_analytics_enabled else 0,
                    },
                    ts_ns=ts_ns,
                )

    def _update_wap_ledger(
        self,
        side: OrderSide,
        price: Decimal,
        qty: Decimal,
        fee: Decimal,
    ) -> None:
        """Update WAP and realized P&L tracking."""
        self._fees_paid += fee
        self._realized_pnl -= fee

        if side == OrderSide.BUY:
            if self._wap_inventory >= 0:
                total_cost = (self._wap_inventory * self._wap_price) + (qty * price)
                self._wap_inventory += qty
                if self._wap_inventory != 0:
                    self._wap_price = total_cost / self._wap_inventory
            else:
                remaining_short = abs(self._wap_inventory)
                if qty <= remaining_short:
                    pnl = (self._wap_price - price) * qty
                    self._realized_pnl += pnl
                    self._wap_inventory += qty
                else:
                    pnl = (self._wap_price - price) * remaining_short
                    self._realized_pnl += pnl
                    excess_qty = qty - remaining_short
                    self._wap_inventory = excess_qty
                    self._wap_price = price
        elif side == OrderSide.SELL:
            if self._wap_inventory <= 0:
                total_cost = (abs(self._wap_inventory) * self._wap_price) + (qty * price)
                self._wap_inventory -= qty
                if self._wap_inventory != 0:
                    self._wap_price = total_cost / abs(self._wap_inventory)
            else:
                if qty <= self._wap_inventory:
                    pnl = (price - self._wap_price) * qty
                    self._realized_pnl += pnl
                    self._wap_inventory -= qty
                else:
                    pnl = (price - self._wap_price) * self._wap_inventory
                    self._realized_pnl += pnl
                    excess_qty = qty - self._wap_inventory
                    self._wap_inventory = -excess_qty
                    self._wap_price = price

        # === REALIZED SPREAD GUARDIAN ===
        # Track realized spread after each roundtrip to ensure fees are covered
        if self.config.realized_spread_guardian_enabled:
            self._update_realized_spread_guardian(side, price, qty, fee)

    def _update_realized_spread_guardian(
        self,
        side: OrderSide,
        price: Decimal,
        qty: Decimal,
        fee: Decimal,
    ) -> None:
        """Track realized spread and adjust future quotes to ensure fees are covered."""
        # Track last prices for spread calculation
        if side == OrderSide.BUY:
            self._last_buy_price = price
        else:
            self._last_sell_price = price
        
        # Calculate realized spread when we have both buy and sell
        if self._last_buy_price is not None and self._last_sell_price is not None:
            # Realized spread = (sell - buy) / mid, in bps
            mid_price = (self._last_buy_price + self._last_sell_price) / Decimal("2")
            if mid_price > 0:
                realized_spread_bps = (
                    (self._last_sell_price - self._last_buy_price) / mid_price
                ) * Decimal("10000")
                
                # Fee cost in bps (roundtrip)
                roundtrip_fee_bps = self.config.maker_fee_bps * Decimal("2")
                min_required_bps = roundtrip_fee_bps + self.config.realized_spread_min_bps
                
                # Track in rolling window
                net_spread_bps = realized_spread_bps - roundtrip_fee_bps
                self._realized_spread_history.append(net_spread_bps)
                
                # Calculate average realized spread
                if len(self._realized_spread_history) >= 2:
                    avg_realized = sum(self._realized_spread_history) / Decimal(str(len(self._realized_spread_history)))
                    
                    if avg_realized < self.config.realized_spread_min_bps:
                        # Not covering fees - widen spread
                        shortfall = self.config.realized_spread_min_bps - avg_realized
                        new_extra = min(
                            self._guardian_extra_spread_bps + self.config.realized_spread_widen_step_bps,
                            self.config.realized_spread_max_widen_bps,
                        )
                        
                        if new_extra != self._guardian_extra_spread_bps:
                            self._guardian_extra_spread_bps = new_extra
                            self._profitable_streak = 0
                            if self.config.log_realized_spread:
                                self.log.warning(
                                    f"GUARDIAN: Avg realized={avg_realized:.1f}bps < min={self.config.realized_spread_min_bps}bps | "
                                    f"Widening spread +{self._guardian_extra_spread_bps}bps",
                                    color=LogColor.YELLOW,
                                )
                    else:
                        # Profitable - track streak and potentially relax
                        self._profitable_streak += 1
                        
                        if (self._profitable_streak >= self.config.realized_spread_cooldown_fills 
                            and self._guardian_extra_spread_bps > 0):
                            # Gradually relax the extra spread
                            old_extra = self._guardian_extra_spread_bps
                            self._guardian_extra_spread_bps = max(
                                Decimal("0"),
                                self._guardian_extra_spread_bps - self.config.realized_spread_widen_step_bps,
                            )
                            self._profitable_streak = 0
                            if self.config.log_realized_spread and old_extra != self._guardian_extra_spread_bps:
                                self.log.info(
                                    f"GUARDIAN: Avg realized={avg_realized:.1f}bps >= min | "
                                    f"Relaxing spread to +{self._guardian_extra_spread_bps}bps",
                                    color=LogColor.GREEN,
                                )
                
                # Reset for next roundtrip
                self._last_buy_price = None
                self._last_sell_price = None
                
                if self.config.log_realized_spread:
                    self.log.info(
                        f"FILL PAIR: Realized={realized_spread_bps:.1f}bps | Fees={roundtrip_fee_bps}bps | "
                        f"Net={net_spread_bps:.1f}bps | Guardian+{self._guardian_extra_spread_bps}bps",
                        color=LogColor.CYAN if net_spread_bps >= 0 else LogColor.RED,
                    )

    def _get_realized_pnl(self) -> Decimal:
        return self._realized_pnl

    # =========================================================================
    # TIMESTAMP HELPERS
    # =========================================================================

    def _event_ts_ns_from_event(self, ts_event) -> int:
        if ts_event is None:
            return self._now_ns()
        ts_value = int(ts_event)
        if ts_value < 1_000_000_000_000:
            return ts_value * 1_000_000_000
        if ts_value < 1_000_000_000_000_000:
            return ts_value * 1_000_000
        return ts_value

    def _now_ns(self) -> int:
        return self._get_now_ns()

    def _event_ts_ns(self, deltas: OrderBookDeltas) -> int:
        ts_event = getattr(deltas, "ts_event", None)
        if ts_event is None:
            return self._now_ns()
        ts_value = int(ts_event)
        if ts_value < 1_000_000_000_000:
            return ts_value * 1_000_000_000
        if ts_value < 1_000_000_000_000_000:
            return ts_value * 1_000_000
        return ts_value

    def _is_data_stale(self) -> bool:
        if self._last_leader_ts_ns == 0 or self._last_follower_ts_ns == 0:
            return True
        now_ns = self._now_ns()
        max_age_ns = self.config.max_data_staleness_ms * 1_000_000
        if now_ns - self._last_leader_ts_ns > max_age_ns:
            return True
        if now_ns - self._last_follower_ts_ns > max_age_ns:
            return True
        return False

    def _is_guard_data_stale(self) -> bool:
        if self._last_guard_ts_ns == 0:
            return True
        now_ns = self._now_ns()
        max_age_ns = self.config.max_data_staleness_ms * 1_000_000
        return now_ns - self._last_guard_ts_ns > max_age_ns
