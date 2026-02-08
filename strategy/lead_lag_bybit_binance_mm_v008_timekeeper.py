"""Lead-Lag Market Maker v008 TIMEKEEPER - Inventory Age Management.

KEY INSIGHT FROM v007 LOSSES:
The problem is NOT spread width - it's STALE INVENTORY.
- ARB bought at 05:29 @ 0.1203, sold at 07:40 @ 0.1168 = 2h 11m hold = -290 bps
- The market moved 2.9% against us while inventory sat idle

NEW INVENTORY MANAGEMENT SYSTEMS:
1. Inventory Age Tracking - know when each position was acquired
2. Time-Based Exit Skew - progressively tighten exit side over time  
3. Underwater Inventory Detection - force exit if inventory is losing
4. Max Hold Time - absolute limit on how long to hold inventory
5. Entry Quality Filter - only enter when conditions are favorable

All protection systems from v007 are retained.
"""

from __future__ import annotations

import gc
from collections import deque
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
    PortfolioRiskMixin = object
    PORTFOLIO_COORDINATOR_AVAILABLE = False


# =============================================================================
# CONSTANTS
# =============================================================================
BPS_MULTIPLIER: Final[float] = 10000.0
BPS_DIVISOR: Final[float] = 0.0001


class LeadLagMMv8TimekeeperConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for LeadLagMMv8Timekeeper - Inventory Age Management."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId

    # === ORDER SIZING ===
    order_qty: float = 10.0
    max_position_qty: float = 100.0
    min_order_qty: float = 0.001

    # === BASE QUOTING ===
    spread_bps: float = 80.0
    quote_refresh_interval_ms: int = 30
    min_quote_lifetime_ms: int = 20

    # ==========================================================================
    # NEW: INVENTORY TIME MANAGEMENT (v008 Core Feature)
    # ==========================================================================
    
    # Maximum time to hold inventory before forcing exit
    inventory_max_hold_secs: int = 1800  # 30 minutes default
    
    # Time-based exit skew: progressively tighten exit side
    # After inventory_skew_start_secs, start tightening exit side
    # At inventory_max_hold_secs, exit at mid price
    inventory_exit_skew_enabled: bool = True
    inventory_exit_skew_start_secs: int = 300  # Start tightening after 5 min
    inventory_exit_skew_max_tighten_bps: float = 40.0  # Max tightening on exit
    
    # Force exit if inventory is underwater by this much
    inventory_underwater_exit_enabled: bool = True
    inventory_underwater_threshold_bps: float = -50.0  # Force exit at -50bps
    inventory_underwater_check_after_secs: int = 600  # Only check after 10 min
    
    # Entry quality filter: only enter when favorable
    entry_quality_filter_enabled: bool = True
    entry_quality_min_ofi: float = 0.0  # Minimum OFI to enter (0 = neutral)
    entry_quality_max_trend_strength: float = 0.50  # Don't enter in strong trends
    
    # NEW v008.1: Don't add to underwater positions
    no_add_to_loser_enabled: bool = True  # Block adding to losing positions
    no_add_to_loser_threshold_bps: float = -10.0  # Block adds if position P&L < -10bps
    
    # ==========================================================================
    # TOXIC FLOW AVOIDANCE
    # ==========================================================================
    ofi_enabled: bool = True
    ofi_depth: int = 10
    ofi_widen_threshold: float = 0.3
    ofi_widen_bps: float = 15.0
    ofi_tighten_bps: float = 3.0

    # === VOLATILITY ===
    volatility_enabled: bool = True
    volatility_window_ticks: int = 20
    volatility_base_threshold_bps: float = 5.0
    volatility_max_widen_bps: float = 50.0

    # === REGIME DETECTION ===
    regime_detection_enabled: bool = True
    regime_window_ticks: int = 150
    regime_trending_threshold: float = 0.65
    regime_ranging_threshold: float = 0.25
    regime_trending_spread_mult: float = 2.5
    regime_trending_size_mult: float = 0.3
    regime_ema_alpha: float = 0.1  # EMA smoothing for trend strength (lower = smoother)

    # === TREND FILTER ===
    trend_filter_enabled: bool = True
    trend_filter_threshold: float = 0.75
    trend_filter_pause_secs: int = 60
    trend_filter_consecutive_ticks: int = 10  # Require N consecutive trending ticks

    # === GUARD ===
    guard_threshold_bps: float = 5.0
    guard_hysteresis_bps: float = 10.0

    # === INVENTORY SKEW PROTECTION (position size based) ===
    inventory_skew_enabled: bool = True
    inventory_skew_threshold_pct: float = 0.30
    inventory_skew_max_widen_bps: float = 50.0
    inventory_skew_block_threshold_pct: float = 0.80

    # === INVENTORY (Avellaneda-Stoikov) ===
    risk_aversion: float = 0.8
    volatility: float = 0.02
    inventory_time_horizon_secs: float = 60.0
    internal_price_delta_limit_bps: float = 50.0

    # === POSITION SAFETY ===
    hard_position_cap_enabled: bool = True
    position_cap_buffer_pct: float = 0.90

    # === RUNAWAY DETECTION ===
    runaway_detection_enabled: bool = True
    runaway_window_fills: int = 5
    runaway_threshold_pct: float = 0.70
    runaway_pause_secs: int = 60

    # === HIT RATE TRACKING ===
    hit_rate_tracking_enabled: bool = True
    hit_rate_window_fills: int = 20
    hit_rate_imbalance_threshold: float = 0.70
    hit_rate_pause_secs: int = 30

    # === FEES ===
    maker_fee_bps: float = 10.0
    min_profit_bps: float = 10.0

    # === REALIZED SPREAD GUARDIAN ===
    realized_spread_guardian_enabled: bool = True
    realized_spread_window_fills: int = 5
    realized_spread_min_bps: float = 10.0
    realized_spread_widen_step_bps: float = 20.0
    realized_spread_max_widen_bps: float = 150.0
    realized_spread_cooldown_fills: int = 3
    log_realized_spread: bool = True

    # === FIFO P&L TRACKING ===
    fifo_pnl_enabled: bool = True
    fifo_pnl_window: int = 50
    fifo_pnl_loss_threshold_bps: float = -20.0
    fifo_pnl_pause_secs: int = 120

    # === DYNAMIC SPREAD ===
    dynamic_spread_enabled: bool = True
    dynamic_spread_min_bps: float = 50.0
    dynamic_spread_max_bps: float = 200.0
    dynamic_spread_adjust_rate: float = 0.1

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
    log_protection_events: bool = True
    log_inventory_age: bool = True  # NEW

    # === METRICS ===
    metrics_enabled: bool = True
    metrics_interval_secs: int = 5

    # === PORTFOLIO COORDINATOR ===
    portfolio_coordinator_enabled: bool = True
    portfolio_coordinator_redis_host: str = "localhost"
    portfolio_coordinator_redis_port: int = 6379


# Inventory entry: (price, qty, timestamp_ns)
InventoryEntry = tuple[float, float, int]


class LeadLagMMv8Timekeeper(Strategy, PortfolioRiskMixin):
    """
    Inventory Age Management Lead-Lag Market Maker.
    
    Key innovation: Track WHEN inventory was acquired, not just WHAT.
    - Exit stale inventory faster
    - Don't let losing positions sit
    - Better entry timing
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
        '_trend_strength_ema', '_trending_tick_count',
        '_current_regime_is_trending',
        '_guard_block_buy', '_guard_block_sell',
        '_diff_bps',
        # Portfolio coordinator
        '_portfolio_limits',
        # Guardian state
        '_guardian_extra_bps', '_guardian_cooldown',
        '_realized_spreads', '_realized_idx', '_realized_count',
        # FIFO tracking with timestamps (NEW v008)
        '_inventory_buys',  # deque of (price, qty, timestamp_ns)
        '_inventory_sells',  # deque of (price, qty, timestamp_ns)
        '_fifo_pnl_history', '_fifo_pnl_idx', '_fifo_pnl_count',
        '_fifo_pause_until_ns',
        # Inventory age tracking (NEW v008)
        '_oldest_buy_ts_ns', '_oldest_sell_ts_ns',
        '_oldest_buy_price', '_oldest_sell_price',
        '_inventory_exit_tighten_bps',  # Current exit tightening
        # Hit rate tracking
        '_hit_rate_sides', '_hit_rate_idx', '_hit_rate_count',
        '_hit_rate_pause_buy_ns', '_hit_rate_pause_sell_ns',
        # Trend filter
        '_trend_pause_until_ns',
        # Dynamic spread
        '_dynamic_spread_bps',
        # Inventory skew
        '_inventory_skew_extra_bid_bps', '_inventory_skew_extra_ask_bps',
        # Orders
        '_bid_order', '_ask_order',
        '_bid_order_ts_ns', '_ask_order_ts_ns',
        '_bid_reject_pause_ns', '_ask_reject_pause_ns',  # Rejection cooldown
        # Balance tracking (NEW v008.9)
        '_base_currency', '_quote_currency',
        '_available_base', '_available_quote',
        # Timing
        '_last_quote_ts_ns', '_last_metrics_ts_ns', '_last_inventory_check_ns',
        '_runaway_pause_buy_ns', '_runaway_pause_sell_ns',
        '_last_force_exit_ns',  # Cooldown for force exit spam prevention
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
        '_forced_exits', '_entries_blocked_quality',
    )

    def __init__(self, config: LeadLagMMv8TimekeeperConfig) -> None:
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
        self._trend_strength_ema: float = 0.0  # Smoothed trend strength
        self._trending_tick_count: int = 0  # Consecutive trending ticks
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

        # FIFO tracking WITH TIMESTAMPS (v008 key change)
        self._inventory_buys: deque[InventoryEntry] = deque()  # (price, qty, timestamp_ns)
        self._inventory_sells: deque[InventoryEntry] = deque()
        self._fifo_pnl_history: np.ndarray = np.zeros(config.fifo_pnl_window, dtype=np.float64)
        self._fifo_pnl_idx: int = 0
        self._fifo_pnl_count: int = 0
        self._fifo_pause_until_ns: int = 0

        # Inventory age tracking (v008)
        self._oldest_buy_ts_ns: int = 0
        self._oldest_sell_ts_ns: int = 0
        self._oldest_buy_price: float = 0.0
        self._oldest_sell_price: float = 0.0
        self._inventory_exit_tighten_bps: float = 0.0

        # Hit rate tracking
        self._hit_rate_sides: np.ndarray = np.zeros(config.hit_rate_window_fills, dtype=np.int8)
        self._hit_rate_idx: int = 0
        self._hit_rate_count: int = 0
        self._hit_rate_pause_buy_ns: int = 0
        self._hit_rate_pause_sell_ns: int = 0

        # Trend filter
        self._trend_pause_until_ns: int = 0

        # Dynamic spread
        self._dynamic_spread_bps: float = config.spread_bps

        # Inventory skew protection
        self._inventory_skew_extra_bid_bps: float = 0.0
        self._inventory_skew_extra_ask_bps: float = 0.0

        # Portfolio coordinator limits cache
        self._portfolio_limits: dict = {"allow_buy": True, "allow_sell": True, "size_scalar": 1.0}

        # Orders
        self._bid_order: Order | None = None
        self._ask_order: Order | None = None
        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0
        self._bid_reject_pause_ns: int = 0  # Rejection cooldown
        self._ask_reject_pause_ns: int = 0

        # Balance tracking (v008.9)
        # Extract base/quote currencies from instrument (e.g., LINKUSDT-SPOT -> LINK, USDT)
        symbol = str(config.follower_instrument_id.symbol)
        # Remove -SPOT suffix if present
        if "-SPOT" in symbol:
            symbol = symbol.replace("-SPOT", "")
        # Now extract base/quote
        if symbol.endswith("USDT"):
            self._base_currency = symbol[:-4]  # Remove USDT
            self._quote_currency = "USDT"
        elif symbol.endswith("USDC"):
            self._base_currency = symbol[:-4]
            self._quote_currency = "USDC"
        else:
            self._base_currency = symbol[:3]  # Fallback
            self._quote_currency = "USDT"
        self._available_base: float = 0.0
        self._available_quote: float = 0.0

        # Timing
        self._last_quote_ts_ns: int = 0
        self._last_metrics_ts_ns: int = 0
        self._last_inventory_check_ns: int = 0
        self._runaway_pause_buy_ns: int = 0
        self._runaway_pause_sell_ns: int = 0
        self._last_force_exit_ns: int = 0  # Cooldown for force exit spam

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
        self._forced_exits: int = 0
        self._entries_blocked_quality: int = 0

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
            self._metrics = QuestDbILPWriter.from_env("LLMMv8Timekeeper")

        if self.config.portfolio_coordinator_enabled and PORTFOLIO_COORDINATOR_AVAILABLE:
            symbol = self.config.follower_instrument_id.symbol.value
            connected = self.init_portfolio_risk(
                symbol=symbol,
                redis_host=self.config.portfolio_coordinator_redis_host,
                redis_port=self.config.portfolio_coordinator_redis_port,
            )
            if connected:
                self.log.info(f"📡 Portfolio Coordinator connected for {symbol}", LogColor.BLUE)

        from datetime import timedelta
        self.clock.set_timer(
            name="delayed_activation",
            interval=timedelta(seconds=5),
            callback=self._on_delayed_activation,
        )

        self.log.info("LeadLagMMv8Timekeeper initializing, will activate in 5 seconds...")

    def _on_delayed_activation(self, event) -> None:
        if not self._is_trading_ready():
            self.log.warning("Timer fired but trading not ready - waiting...")
            return

        try:
            self.clock.cancel_timer("delayed_activation")
        except Exception:
            pass

        # Initialize inventory from exchange balance
        self._initialize_inventory_from_exchange()
        
        # Initialize available balances for size capping (v008.9)
        self._update_available_balances()
        self.log.info(
            f"💰 BALANCE INIT: {self._base_currency}={self._available_base:.4f} | "
            f"{self._quote_currency}={self._available_quote:.2f}",
            LogColor.CYAN,
        )
        
        self._initialized = True
        
        self.log.info(
            f"⏱️ TIMEKEEPER v008 ACTIVATED | "
            f"spread={self._spread_bps:.0f}bps | "
            f"max_hold={self.config.inventory_max_hold_secs}s | "
            f"exit_skew={self.config.inventory_exit_skew_enabled} | "
            f"underwater_exit={self.config.inventory_underwater_exit_enabled} | "
            f"entry_filter={self.config.entry_quality_filter_enabled}",
            LogColor.GREEN,
        )
    
    def _initialize_inventory_from_exchange(self) -> None:
        """Query exchange balance and initialize inventory tracking.
        
        For SPOT trading, having base currency = long position.
        We need to track this as existing inventory with unknown entry price.
        """
        if self.follower_instrument is None:
            return
        
        # Get base currency from instrument (e.g., SUI from SUIUSDT)
        base_currency = self.follower_instrument.base_currency
        if base_currency is None:
            self.log.warning("Cannot determine base currency for inventory init")
            return
        
        # Get account for venue
        venue = self.config.follower_instrument_id.venue
        account = self.cache.account_for_venue(venue)
        if account is None:
            self.log.warning(f"No account found for venue {venue}")
            return
        
        # Query balance for base currency
        try:
            balance = account.balance(base_currency)
            if balance is not None and float(balance.total) > 0:
                base_qty = float(balance.total)
                
                # Calculate "target" base quantity (what we should hold at neutral)
                # For our strategy, neutral = 0 position
                # So any base currency held = long position
                self._net_position = base_qty
                
                # Initialize FIFO with current price as entry price
                # This is an approximation - we don't know actual entry prices
                now_ns = self._now_ns()
                entry_price = self._follower_mid if self._follower_mid > 0 else 1.0
                
                if base_qty > 0:
                    # We have base currency = long position
                    self._inventory_buys.append((entry_price, base_qty, now_ns))
                    self._oldest_buy_ts_ns = now_ns
                    self._oldest_buy_price = entry_price
                    
                    self.log.warning(
                        f"📦 INVENTORY INIT: Found {base_qty:.4f} {base_currency} | "
                        f"Treating as LONG @ {entry_price:.6f} (assumed entry)",
                        LogColor.YELLOW,
                    )
        except Exception as e:
            self.log.warning(f"Error querying balance: {e}")

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
        
        self.log.info(
            f"📊 SESSION SUMMARY: Fills={self._total_fills} (B:{self._total_buy_fills}/S:{self._total_sell_fills}) | "
            f"Net P&L=${self._session_net_pnl:.2f} | "
            f"Forced exits={self._forced_exits} | Entries blocked={self._entries_blocked_quality}",
            LogColor.CYAN,
        )
        self.log.info("LeadLagMMv8Timekeeper stopped")

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
            
            # Check inventory age every second (v008)
            if now_ns - self._last_inventory_check_ns > 1_000_000_000:
                self._check_inventory_age(now_ns)
                self._update_available_balances()  # Also update balances
                self._last_inventory_check_ns = now_ns
            
            self._refresh_quotes_fast(now_ns)

    # =========================================================================
    # v008 CORE: INVENTORY AGE MANAGEMENT
    # =========================================================================

    def _check_inventory_age(self, now_ns: int) -> None:
        """Check inventory age and calculate exit skew / force exits."""
        
        max_hold_ns = self.config.inventory_max_hold_secs * 1_000_000_000
        skew_start_ns = self.config.inventory_exit_skew_start_secs * 1_000_000_000
        underwater_check_ns = self.config.inventory_underwater_check_after_secs * 1_000_000_000
        
        # Update oldest inventory tracking
        if self._inventory_buys:
            oldest_buy = self._inventory_buys[0]
            self._oldest_buy_ts_ns = oldest_buy[2]
            self._oldest_buy_price = oldest_buy[0]
        else:
            self._oldest_buy_ts_ns = 0
            self._oldest_buy_price = 0.0
            
        if self._inventory_sells:
            oldest_sell = self._inventory_sells[0]
            self._oldest_sell_ts_ns = oldest_sell[2]
            self._oldest_sell_price = oldest_sell[0]
        else:
            self._oldest_sell_ts_ns = 0
            self._oldest_sell_price = 0.0

        # Calculate exit tightening based on inventory age
        self._inventory_exit_tighten_bps = 0.0
        oldest_inventory_age_ns = 0
        
        if self._oldest_buy_ts_ns > 0:
            buy_age_ns = now_ns - self._oldest_buy_ts_ns
            oldest_inventory_age_ns = max(oldest_inventory_age_ns, buy_age_ns)
            
        if self._oldest_sell_ts_ns > 0:
            sell_age_ns = now_ns - self._oldest_sell_ts_ns
            oldest_inventory_age_ns = max(oldest_inventory_age_ns, sell_age_ns)

        if oldest_inventory_age_ns > 0 and self.config.inventory_exit_skew_enabled:
            if oldest_inventory_age_ns >= skew_start_ns:
                # Calculate progressive tightening
                age_past_start = oldest_inventory_age_ns - skew_start_ns
                time_range = max_hold_ns - skew_start_ns
                if time_range > 0:
                    progress = min(age_past_start / time_range, 1.0)
                    self._inventory_exit_tighten_bps = progress * self.config.inventory_exit_skew_max_tighten_bps

        # Check for underwater inventory that should be force-exited
        if self.config.inventory_underwater_exit_enabled and self._follower_mid > 0:
            # Check long inventory (buys)
            if self._oldest_buy_ts_ns > 0:
                buy_age_ns = now_ns - self._oldest_buy_ts_ns
                if buy_age_ns >= underwater_check_ns:
                    pnl_bps = (self._follower_mid - self._oldest_buy_price) / self._oldest_buy_price * BPS_MULTIPLIER
                    if pnl_bps <= self.config.inventory_underwater_threshold_bps:
                        self._force_exit_oldest_inventory("BUY", pnl_bps, buy_age_ns)
            
            # Check short inventory (sells)
            if self._oldest_sell_ts_ns > 0:
                sell_age_ns = now_ns - self._oldest_sell_ts_ns
                if sell_age_ns >= underwater_check_ns:
                    pnl_bps = (self._oldest_sell_price - self._follower_mid) / self._oldest_sell_price * BPS_MULTIPLIER
                    if pnl_bps <= self.config.inventory_underwater_threshold_bps:
                        self._force_exit_oldest_inventory("SELL", pnl_bps, sell_age_ns)

        # Check max hold time - force exit regardless of P&L
        if oldest_inventory_age_ns >= max_hold_ns:
            if self._oldest_buy_ts_ns > 0 and (now_ns - self._oldest_buy_ts_ns) >= max_hold_ns:
                pnl_bps = (self._follower_mid - self._oldest_buy_price) / self._oldest_buy_price * BPS_MULTIPLIER if self._oldest_buy_price > 0 else 0
                self._force_exit_oldest_inventory("BUY", pnl_bps, now_ns - self._oldest_buy_ts_ns)
            if self._oldest_sell_ts_ns > 0 and (now_ns - self._oldest_sell_ts_ns) >= max_hold_ns:
                pnl_bps = (self._oldest_sell_price - self._follower_mid) / self._oldest_sell_price * BPS_MULTIPLIER if self._oldest_sell_price > 0 else 0
                self._force_exit_oldest_inventory("SELL", pnl_bps, now_ns - self._oldest_sell_ts_ns)

        # Log inventory age periodically
        if self.config.log_inventory_age and oldest_inventory_age_ns > 60_000_000_000:  # > 1 min
            age_mins = oldest_inventory_age_ns / 60_000_000_000
            self.log.info(
                f"⏱️ INVENTORY AGE: {age_mins:.1f}m | Exit tighten={self._inventory_exit_tighten_bps:.1f}bps | "
                f"Buys={len(self._inventory_buys)} Sells={len(self._inventory_sells)}",
                LogColor.YELLOW if age_mins > 10 else LogColor.NORMAL,
            )

    def _force_exit_oldest_inventory(self, side: str, pnl_bps: float, age_ns: int) -> None:
        """Force exit of stale/underwater inventory by placing aggressive order."""
        # Cooldown: Only try force exit every 30 seconds to avoid order spam
        now_ns = self._now_ns()
        if hasattr(self, '_last_force_exit_ns') and (now_ns - self._last_force_exit_ns) < 30_000_000_000:
            return
        self._last_force_exit_ns = now_ns
        
        age_mins = age_ns / 60_000_000_000
        
        if self.config.log_protection_events:
            self.log.warning(
                f"🚨 FORCE EXIT: {side} inventory | Age={age_mins:.1f}m | P&L={pnl_bps:.1f}bps | "
                f"Placing aggressive exit order",
                LogColor.RED,
            )
        
        self._forced_exits += 1
        
        # Place aggressive order to exit
        # For long inventory (buys), we need to SELL
        # For short inventory (sells), we need to BUY
        if side == "BUY" and self._inventory_buys:
            oldest = self._inventory_buys[0]
            qty = oldest[1]
            # Sell at current bid (aggressive) or slightly below
            if self.follower_book is not None:
                best_bid = self.follower_book.best_bid_price()
                if best_bid is not None:
                    exit_price = float(best_bid)
                    self._place_aggressive_exit(OrderSide.SELL, exit_price, qty)
        elif side == "SELL" and self._inventory_sells:
            oldest = self._inventory_sells[0]
            qty = oldest[1]
            # Buy at current ask (aggressive) or slightly above
            if self.follower_book is not None:
                best_ask = self.follower_book.best_ask_price()
                if best_ask is not None:
                    exit_price = float(best_ask)
                    self._place_aggressive_exit(OrderSide.BUY, exit_price, qty)

    def _place_aggressive_exit(self, side: OrderSide, price: float, qty: float) -> None:
        """Place aggressive exit order (can cross spread)."""
        if self.follower_instrument is None:
            return
        
        # Cancel existing open orders first to free up reserved balance
        open_orders = self.cache.orders_open(instrument_id=self.config.follower_instrument_id)
        if open_orders:
            for order in open_orders:
                if order.is_open:
                    self.cancel_order(order)
            # After cancelling, wait - the balance won't be immediately free
            # So we need to be more conservative with our qty estimate
        
        # CRITICAL: Cap qty to actual available balance to prevent rejections
        # Use a more aggressive buffer since balance might still be reserved
        if side == OrderSide.SELL:
            # Selling base currency - cap to what we have
            # Use 90% buffer to account for potentially reserved balance
            max_qty = self._available_base * 0.90
            if self._net_position > 0:
                max_qty = min(max_qty, self._net_position * 0.90)
            if qty > max_qty:
                qty = max_qty
        else:
            # Buying - cap to what we can afford
            if self._available_quote > 0 and price > 0:
                max_qty = (self._available_quote * 0.90) / price
                if qty > max_qty:
                    qty = max_qty
        
        # Check minimum order value (~$5 for Bybit)
        order_value = qty * price
        if order_value < 5.0:
            if self.config.log_protection_events:
                self.log.warning(
                    f"⚠️ FORCE EXIT SKIPPED: Order value ${order_value:.2f} below $5 min | "
                    f"qty={qty:.4f} @ {price:.4f}",
                    LogColor.YELLOW,
                )
            return
        
        # Ensure qty is positive
        if qty <= 0:
            return
            
        # Round to tick
        if self._tick_size > 0:
            price = round(price / self._tick_size) * self._tick_size
        
        price_dec = self.follower_instrument.make_price(Decimal(str(price)))
        qty_obj = self.follower_instrument.make_qty(Decimal(str(qty)))

        order = self.order_factory.limit(
            instrument_id=self.config.follower_instrument_id,
            order_side=side,
            price=price_dec,
            quantity=qty_obj,
            time_in_force=TimeInForce.IOC,  # Immediate or cancel
            post_only=False,  # Allow taking
        )
        
        self.submit_order(order)

    def _check_entry_quality(self, side: OrderSide) -> bool:
        """Check if entry conditions are favorable."""
        if not self.config.entry_quality_filter_enabled:
            return True

        # Don't enter during strong trends
        if self._trend_strength >= self.config.entry_quality_max_trend_strength:
            self._entries_blocked_quality += 1
            return False

        # OFI check - don't buy into selling pressure, don't sell into buying pressure
        if side == OrderSide.BUY and self._current_ofi < -self.config.entry_quality_min_ofi:
            self._entries_blocked_quality += 1
            return False
        if side == OrderSide.SELL and self._current_ofi > self.config.entry_quality_min_ofi:
            self._entries_blocked_quality += 1
            return False

        return True

    # =========================================================================
    # FAST CALCULATIONS (from v007)
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
            raw_strength = net_move / total_move
        else:
            raw_strength = 0.0
        
        # Apply EMA smoothing to reduce whipsaw
        self._trend_strength = raw_strength
        alpha = self.config.regime_ema_alpha
        self._trend_strength_ema = alpha * raw_strength + (1 - alpha) * self._trend_strength_ema
        
        # Track consecutive trending ticks
        if self._trend_strength_ema >= self._regime_trending_threshold:
            self._trending_tick_count += 1
        else:
            self._trending_tick_count = 0

        old_trending = self._current_regime_is_trending
        
        # Use smoothed strength with consecutive tick confirmation
        if self._trend_strength_ema >= self._regime_trending_threshold and self._trending_tick_count >= 5:
            self._current_regime_is_trending = True
        elif self._trend_strength_ema <= self._regime_ranging_threshold:
            self._current_regime_is_trending = False

        if self.config.log_regime_changes and old_trending != self._current_regime_is_trending:
            regime = "TRENDING" if self._current_regime_is_trending else "RANGING"
            self.log.warning(
                f"🔄 REGIME: {regime} | Raw={self._trend_strength:.2f} | EMA={self._trend_strength_ema:.2f}",
                LogColor.MAGENTA if self._current_regime_is_trending else LogColor.GREEN,
            )

        # Trend filter pause - use EMA and require consecutive ticks
        if self.config.trend_filter_enabled:
            consecutive_required = self.config.trend_filter_consecutive_ticks
            if self._trend_strength_ema >= self.config.trend_filter_threshold and self._trending_tick_count >= consecutive_required:
                now_ns = self._now_ns()
                pause_ns = self.config.trend_filter_pause_secs * 1_000_000_000
                if now_ns >= self._trend_pause_until_ns:
                    self._trend_pause_until_ns = now_ns + pause_ns
                    if self.config.log_protection_events:
                        self.log.warning(
                            f"⚠️ TREND FILTER: EMA={self._trend_strength_ema:.2f} > {self.config.trend_filter_threshold:.2f} "
                            f"for {self._trending_tick_count} ticks | PAUSING for {self.config.trend_filter_pause_secs}s",
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
        if not self.config.inventory_skew_enabled:
            self._inventory_skew_extra_bid_bps = 0.0
            self._inventory_skew_extra_ask_bps = 0.0
            return

        pos_ratio = abs(self._net_position) / self._max_pos if self._max_pos > 0 else 0.0
        
        if pos_ratio < self.config.inventory_skew_threshold_pct:
            self._inventory_skew_extra_bid_bps = 0.0
            self._inventory_skew_extra_ask_bps = 0.0
            return

        scale = (pos_ratio - self.config.inventory_skew_threshold_pct) / (1.0 - self.config.inventory_skew_threshold_pct)
        scale = min(scale, 1.0)
        extra_bps = scale * self.config.inventory_skew_max_widen_bps

        if self._net_position > 0:
            self._inventory_skew_extra_bid_bps = extra_bps
            self._inventory_skew_extra_ask_bps = 0.0
        else:
            self._inventory_skew_extra_bid_bps = 0.0
            self._inventory_skew_extra_ask_bps = extra_bps

    def _update_available_balances(self) -> None:
        """Query cache for current available balances to prevent insufficient balance errors."""
        try:
            venue = self.config.follower_instrument_id.venue
            account = self.cache.account_for_venue(venue)
            if account is None:
                return
            
            # Get free balance for base currency (what we can sell)
            base_currency = Currency.from_str(self._base_currency)
            base_balance = account.balance_free(base_currency)
            if base_balance is not None:
                self._available_base = float(base_balance.as_decimal())
            
            # Get free balance for quote currency (what we can use to buy)
            quote_currency = Currency.from_str(self._quote_currency)
            quote_balance = account.balance_free(quote_currency)
            if quote_balance is not None:
                self._available_quote = float(quote_balance.as_decimal())
        except Exception as e:
            # Don't crash on balance lookup errors
            pass

    def _calculate_position_pnl_bps(self) -> float:
        """Calculate current position P&L in basis points.
        
        Returns a large negative value if FIFO tracking is unavailable,
        to ensure we don't add to positions with unknown P&L.
        """
        if self._net_position == 0 or self._follower_mid == 0.0:
            return 0.0
        
        # Use inventory tracking if available
        # Inventory entries are (price, qty, timestamp_ns) tuples
        if self._net_position > 0:
            # Long position - compare mid to average buy price
            if self._inventory_buys and len(self._inventory_buys) > 0:
                total_qty = sum(qty for px, qty, ts in self._inventory_buys)
                if total_qty > 0:
                    avg_buy = sum(qty * px for px, qty, ts in self._inventory_buys) / total_qty
                    return (self._follower_mid - avg_buy) / avg_buy * BPS_MULTIPLIER
            # FIFO empty but have position = unknown P&L, assume worst
            return -100.0  # Conservative: treat as -100 bps
        else:
            # Short position - compare average sell price to mid
            if self._inventory_sells and len(self._inventory_sells) > 0:
                total_qty = sum(qty for px, qty, ts in self._inventory_sells)
                if total_qty > 0:
                    avg_sell = sum(qty * px for px, qty, ts in self._inventory_sells) / total_qty
                    return (avg_sell - self._follower_mid) / avg_sell * BPS_MULTIPLIER
            # FIFO empty but have position = unknown P&L, assume worst
            return -100.0  # Conservative: treat as -100 bps

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

        # Entry quality filter (v008) - check for ALL entries, not just new positions
        if self.config.entry_quality_filter_enabled:
            # Check quality for buying
            if place_bid:
                if not self._check_entry_quality(OrderSide.BUY):
                    place_bid = False
            # Check quality for selling  
            if place_ask:
                if not self._check_entry_quality(OrderSide.SELL):
                    place_ask = False
        
        # v008.1 FIX: Don't add to losing positions
        if self.config.no_add_to_loser_enabled:
            position_pnl_bps = self._calculate_position_pnl_bps()
            if position_pnl_bps < self.config.no_add_to_loser_threshold_bps:
                # Position is underwater - only allow exit side
                if self._net_position > 0:
                    # Long position losing - block buys (adding), allow sells (exit)
                    if place_bid and self.config.log_protection_events:
                        self.log.warning(
                            f"🚫 NO ADD TO LOSER: Long pos P&L={position_pnl_bps:.1f}bps < {self.config.no_add_to_loser_threshold_bps}bps | Blocking BUY"
                        )
                    place_bid = False
                elif self._net_position < 0:
                    # Short position losing - block sells (adding), allow buys (exit)
                    if place_ask and self.config.log_protection_events:
                        self.log.warning(
                            f"🚫 NO ADD TO LOSER: Short pos P&L={position_pnl_bps:.1f}bps < {self.config.no_add_to_loser_threshold_bps}bps | Blocking SELL"
                        )
                    place_ask = False

        # Portfolio coordinator limits
        if self.config.portfolio_coordinator_enabled and PORTFOLIO_COORDINATOR_AVAILABLE:
            limits = self.get_portfolio_limits()
            if not limits.get("allow_buy", True):
                place_bid = False
            if not limits.get("allow_sell", True):
                place_ask = False

        if not place_bid and not place_ask:
            self._last_quote_ts_ns = now_ns
            return

        # Clean up closed orders and handle rejection cooldown
        if self._bid_order is not None and self._bid_order.is_closed:
            if self._bid_order.status == OrderStatus.REJECTED:
                self._bid_reject_pause_ns = now_ns + 5_000_000_000  # 5 sec cooldown
            self._bid_order = None
        if self._ask_order is not None and self._ask_order.is_closed:
            if self._ask_order.status == OrderStatus.REJECTED:
                self._ask_reject_pause_ns = now_ns + 5_000_000_000  # 5 sec cooldown
            self._ask_order = None

        self._last_quote_ts_ns = now_ns

        # === CALCULATE SPREAD ===
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

        # v008: Exit side tightening based on inventory age
        exit_tighten_bid = 0.0
        exit_tighten_ask = 0.0
        if self.config.inventory_exit_skew_enabled and self._inventory_exit_tighten_bps > 0:
            # If long (have buys), tighten ask (exit side)
            if self._net_position > 0:
                exit_tighten_ask = self._inventory_exit_tighten_bps
            # If short (have sells), tighten bid (exit side)
            elif self._net_position < 0:
                exit_tighten_bid = self._inventory_exit_tighten_bps

        # Final spreads
        bid_spread_bps = max(
            spread_bps + ofi_bid_adj + vol_adj + guardian_adj + self._inventory_skew_extra_bid_bps - exit_tighten_bid,
            self._fee_floor_bps
        )
        ask_spread_bps = max(
            spread_bps + ofi_ask_adj + vol_adj + guardian_adj + self._inventory_skew_extra_ask_bps - exit_tighten_ask,
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

        # === BALANCE CAP (v008.9) - Prevent "Insufficient balance" rejections ===
        # For SELL orders: Cap to actual position we hold (net_position if positive)
        # SPOT TRADING: You can only sell what you own - no short selling
        if self._net_position > 0:
            # We have long inventory - cap sell to what we actually have
            max_sell_qty = self._net_position * 0.98  # 2% buffer for rounding
            if ask_qty > max_sell_qty:
                ask_qty = max_sell_qty
                if ask_qty < self._min_qty:
                    place_ask = False  # Position too small to exit
        elif self._net_position <= 0:
            # No long inventory - check if we have untracked base balance
            if self._available_base > 0:
                max_sell_qty = self._available_base * 0.95
                if ask_qty > max_sell_qty:
                    ask_qty = max_sell_qty
                    if ask_qty < self._min_qty:
                        place_ask = False
            else:
                # SPOT: Can't sell without inventory - skip ask order
                place_ask = False
        
        # For BUY orders: Cap to available USDT
        if self._available_quote > 0 and bid_price > 0:
            max_buy_qty = (self._available_quote * 0.95) / bid_price
            if bid_qty > max_buy_qty:
                bid_qty = max_buy_qty
                if bid_qty < self._min_qty:
                    place_bid = False

        # === MINIMUM ORDER VALUE CHECK (~$5 for Bybit) ===
        min_order_value = 5.5  # Slightly above $5 to be safe
        if place_bid and bid_qty * bid_price < min_order_value:
            place_bid = False
        if place_ask and ask_qty * ask_price < min_order_value:
            place_ask = False

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
                place_bid = False
            if self._net_position <= -block_threshold:
                place_ask = False

        # === PLACE ORDERS ===
        if place_bid:
            self._place_order_fast(OrderSide.BUY, bid_price, bid_qty, now_ns)
        if place_ask:
            self._place_order_fast(OrderSide.SELL, ask_price, ask_qty, now_ns)

    def _place_order_fast(self, side: OrderSide, price: float, qty: float, now_ns: int) -> None:
        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns
        reject_pause = self._bid_reject_pause_ns if side == OrderSide.BUY else self._ask_reject_pause_ns

        # Check if we're still in rejection cooldown (5 seconds)
        if now_ns < reject_pause:
            return

        price_dec = self.follower_instrument.make_price(Decimal(str(price)))
        qty_obj = self.follower_instrument.make_qty(Decimal(str(qty)))

        # If we have an existing order that's still open, MODIFY it (preserves queue position)
        if order is not None and not order.is_closed:
            current_price = float(order.price)
            age_ms = (now_ns - order_ts) / 1_000_000
            
            # Don't modify if too young
            if age_ms < self.config.min_quote_lifetime_ms:
                return
            
            # Don't modify if price hasn't changed enough
            if abs(price - current_price) < self._tick_size:
                return
            
            # MODIFY existing order (faster than cancel+replace, preserves queue position)
            self.modify_order(order, quantity=qty_obj, price=price_dec, client_id=self.client_id)
            # Update timestamp
            if side == OrderSide.BUY:
                self._bid_order_ts_ns = now_ns
            else:
                self._ask_order_ts_ns = now_ns
            return

        # No existing order - place new one
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
        
        now_ns = self._now_ns()

        # Update position
        if side == OrderSide.BUY:
            self._net_position += qty
            self._total_buy_fills += 1
            # Update balance tracking - we spent USDT and received base
            if price > 0:
                self._available_quote -= qty * price
                self._available_base += qty
        else:
            self._net_position -= qty
            self._total_sell_fills += 1
            # Update balance tracking - we sold base and received USDT
            if price > 0:
                self._available_base -= qty
                self._available_quote += qty * price
        self._total_fills += 1

        # FIFO P&L tracking with timestamps (v008)
        if self.config.fifo_pnl_enabled and price > 0:
            self._update_fifo_pnl_with_timestamp(side, price, qty, now_ns)

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

    def _update_fifo_pnl_with_timestamp(self, side: OrderSide, price: float, qty: float, now_ns: int) -> None:
        """Track FIFO matched P&L with timestamps for age tracking."""
        fee_bps = self.config.maker_fee_bps * 2.0

        if side == OrderSide.BUY:
            # Try to match against sells (FIFO)
            remaining = qty
            while remaining > 0 and self._inventory_sells:
                sell_price, sell_qty, sell_ts = self._inventory_sells[0]
                match_qty = min(remaining, sell_qty)
                
                spread_bps = (sell_price - price) / price * BPS_MULTIPLIER
                net_bps = spread_bps - fee_bps
                
                # Calculate hold time for this match
                hold_time_secs = (now_ns - sell_ts) / 1_000_000_000
                
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

                if self.config.log_realized_spread:
                    self.log.info(
                        f"FILL PAIR: Realized={spread_bps:.1f}bps | Fees={fee_bps:.0f}bps | "
                        f"Net={net_bps:.1f}bps | HoldTime={hold_time_secs:.0f}s | Guardian={self._guardian_extra_bps:.1f}bps",
                        LogColor.GREEN if net_bps > 0 else LogColor.RED,
                    )

                # Guardian update
                self._update_guardian_from_pnl(net_bps, spread_bps)

                remaining -= match_qty
                if match_qty >= sell_qty:
                    self._inventory_sells.popleft()
                else:
                    self._inventory_sells[0] = (sell_price, sell_qty - match_qty, sell_ts)

            # Remaining goes to inventory with timestamp
            if remaining > 0:
                self._inventory_buys.append((price, remaining, now_ns))
        else:
            # Try to match against buys (FIFO)
            remaining = qty
            while remaining > 0 and self._inventory_buys:
                buy_price, buy_qty, buy_ts = self._inventory_buys[0]
                match_qty = min(remaining, buy_qty)
                
                spread_bps = (price - buy_price) / buy_price * BPS_MULTIPLIER
                net_bps = spread_bps - fee_bps
                
                hold_time_secs = (now_ns - buy_ts) / 1_000_000_000
                
                self._fifo_pnl_history[self._fifo_pnl_idx] = net_bps
                self._fifo_pnl_idx = (self._fifo_pnl_idx + 1) % len(self._fifo_pnl_history)
                if self._fifo_pnl_count < len(self._fifo_pnl_history):
                    self._fifo_pnl_count += 1

                notional = match_qty * buy_price
                self._session_gross_pnl += (spread_bps / BPS_MULTIPLIER) * notional
                self._session_fees += (fee_bps / BPS_MULTIPLIER) * notional
                self._session_net_pnl = self._session_gross_pnl - self._session_fees

                if self.config.log_realized_spread:
                    self.log.info(
                        f"FILL PAIR: Realized={spread_bps:.1f}bps | Fees={fee_bps:.0f}bps | "
                        f"Net={net_bps:.1f}bps | HoldTime={hold_time_secs:.0f}s | Guardian={self._guardian_extra_bps:.1f}bps",
                        LogColor.GREEN if net_bps > 0 else LogColor.RED,
                    )

                self._update_guardian_from_pnl(net_bps, spread_bps)

                remaining -= match_qty
                if match_qty >= buy_qty:
                    self._inventory_buys.popleft()
                else:
                    self._inventory_buys[0] = (buy_price, buy_qty - match_qty, buy_ts)

            if remaining > 0:
                self._inventory_sells.append((price, remaining, now_ns))

        # Check average FIFO P&L and pause if losing
        if self._fifo_pnl_count >= 5:
            avg_pnl = float(np.mean(self._fifo_pnl_history[:self._fifo_pnl_count]))
            if avg_pnl < self.config.fifo_pnl_loss_threshold_bps:
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
                    target_spread = min(
                        self._dynamic_spread_bps + self.config.dynamic_spread_adjust_rate * 20,
                        self.config.dynamic_spread_max_bps
                    )
                elif avg_pnl > self.config.min_profit_bps + 10:
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

    def _update_guardian_from_pnl(self, net_bps: float, realized_bps: float) -> None:
        """Update Guardian spread adjustment based on P&L."""
        self._realized_spreads[self._realized_idx] = net_bps
        self._realized_idx = (self._realized_idx + 1) % len(self._realized_spreads)
        if self._realized_count < len(self._realized_spreads):
            self._realized_count += 1

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
