#!/usr/bin/env python3
"""HFT Grid Market Maker for Hyperliquid — v001 GENESIS.

Avellaneda-Stoikov grid market maker optimized for Hyperliquid's fee structure.

CORE DESIGN:
    - 20 grid levels per side centered around mid-price
    - Skewed reservation price for inventory control (Avellaneda-Stoikov)
    - Post-Only (ALO) orders for guaranteed maker fee
    - 1-second refresh cycle (matches HL 200ms consensus latency)
    - Batch order submission (up to 40 orders per API call)

FEE MATH (VIP0):
    Maker: 1.0 bps | Taker: 3.5 bps | Roundtrip: 4.5 bps
    At 10 bps half-spread → 20 bps gross → 15.5 bps net ✅

INSTRUMENTS:
    BTC-USD-PERP.HYPERLIQUID
    ETH-USD-PERP.HYPERLIQUID
    SOL-USD-PERP.HYPERLIQUID (optional)

VERSION: v001.0
CREATED: February 2026
"""

from __future__ import annotations

import time
from collections import deque
from datetime import timedelta
from decimal import Decimal
from typing import Final

import numpy as np

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, OrderStatus, TimeInForce
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, VenueOrderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
BPS: Final[float] = 0.0001
LOG_INTERVAL_SECS: Final[int] = 10


# =============================================================================
# CONFIGURATION
# =============================================================================

class HLGridMMConfig(StrategyConfig, frozen=True):
    """Configuration for Hyperliquid Grid Market Maker.

    VOLUME MODE PRESETS
    ===================
    Profile       half_spread  grid_int  levels  qty_usd  note
    ------------- ----------   --------  ------  -------  ----
    conservative  12.0         8.0       5       50       safe for small accounts
    balanced       6.0         4.0       10      100      moderate volume
    volume         3.5         2.0       10      200      max fills, thin edge
    aggro          2.5         1.5       15      300      bleeding-edge, needs rebates

    VIP TIERS (14d weighted volume)
    ================================
    VIP0    $0      maker 1.5 bps   taker 4.5 bps
    VIP1    $5M     maker 1.2 bps   taker 4.0 bps
    VIP2    $25M    maker 0.8 bps   taker 3.5 bps
    VIP3    $100M   maker 0.4 bps   taker 3.0 bps
    VIP4    $500M   maker 0.0 bps   taker 2.8 bps

    MM REBATES (% of exchange maker volume)
    ========================================
    >0.5%  → -0.1 bps rebate
    >1.5%  → -0.2 bps rebate
    >3.0%  → -0.3 bps rebate
    """

    instrument_id: str = "ETH-USD-PERP.HYPERLIQUID"

    # === GRID STRUCTURE ===
    grid_levels: int = 10            # Levels per side (10 buy + 10 sell)
    half_spread_bps: float = 5.0     # Distance from mid to inner grid level
    grid_interval_bps: float = 2.0   # Distance between grid levels
    order_qty_usd: float = 200.0     # Notional per level in USD

    # TICK-BASED MINIMUMS: On low-price assets ($2-5), bps-based spacing
    # collapses to 1-2 ticks, eliminating spread capture.
    # These floors ensure the grid always has enough tick separation
    # to capture spread above the maker fee.
    # At $2.09 with 0.001 tick: 4 ticks = 0.004 = ~19bps half-spread
    # Full spread = 2 × 4 ticks = 8 ticks = ~38bps > 3bps roundtrip fee
    min_half_spread_ticks: int = 4    # Min 4 ticks from mid to inner level
    min_interval_ticks: int = 2       # Min 2 ticks between grid levels

    # === POSITION LIMITS ===
    max_position_usd: float = 5000.0  # Max notional exposure
    max_position_qty: float = 0.0     # Computed from price if 0

    # === INVENTORY MANAGEMENT (Avellaneda-Stoikov) ===
    skew_bps_per_unit: float = 3.0   # Aggressive skewing to shed inventory fast
    skew_enabled: bool = True         # Enable Stoikov skewing

    # === PROFIT PROTECTION ===
    min_profit_bps: float = 2.0      # Minimum profit target per roundtrip (after fees)
    # Sell floor = entry + roundtrip_fees + min_profit_bps
    # Buy ceiling (short) = entry - roundtrip_fees - min_profit_bps

    # Profit floor TIME DECAY — don't freeze the bot when underwater.
    # The floor starts at (entry + fees + min_profit), then after decay_start_secs
    # it linearly decays until at decay_end_secs it reaches (entry - max_loss_accept_bps).
    # This ensures the bot always resumes trading, accepting small losses to unwind.
    profit_floor_decay_start_secs: float = 30.0   # Start decay after 30s underwater
    profit_floor_decay_end_secs: float = 120.0    # Full decay after 120s underwater
    max_loss_accept_bps: float = 10.0             # At full decay, accept up to 10bps loss per exit

    # === REFRESH ===
    refresh_interval_secs: float = 3.0   # 3 second refresh (reduces API calls, HL rate limit friendly)
    min_refresh_interval_secs: float = 2.0  # Minimum time between refreshes
    stale_order_secs: float = 15.0    # Cancel orders older than 15s

    # === RISK CONTROLS ===
    max_loss_usd: float = -100.0     # Emergency kill switch
    max_inventory_age_secs: float = 300.0  # Force exit after 5 min (fast turnover)
    pause_on_spread_collapse_bps: float = 1.0  # Only pause if spread < 1bps
    flatten_on_stop: bool = True      # Flatten position on graceful shutdown

    # === MAKER CLOSE MODE (replaces taker dumps for inventory age) ===
    # Instead of dumping at market (IOC taker, 4.5bps fee + slippage),
    # enter close-only mode: yank adding side, keep only closing levels
    # at tighter prices to exit via maker.
    maker_close_enabled: bool = True     # Use maker close instead of taker dump
    take_profit_unrealized_usd: float = 1.5  # Enter close mode when unrealized P&L > $X
    take_profit_pct: float = 0.0         # Or when unrealized > X% of position notional (0=disabled)
    close_mode_tighten_bps: float = 2.0  # How much to tighten closing side spread (bps off mid)
    close_mode_interval_bps: float = 1.0 # Spacing between close-mode levels (tighter than normal grid)
    close_mode_max_wait_secs: float = 120.0  # Max time in close mode before escalation
    close_mode_no_taker_fallback: bool = True  # If True, NEVER fall back to taker — stay in maker close forever

    # === MARGIN AWARENESS (unified account) ===
    min_margin_pct: float = 15.0      # Pause new entries if available margin < 15%
    liquidation_warn_pct: float = 25.0  # Warn if price is within 25% of liq price
    liquidation_panic_pct: float = 10.0  # Emergency flatten if within 10% of liq

    # === FILL BURST COOLDOWN ===
    burst_count: int = 3              # Fills on one side in burst window to trigger cooldown
    burst_window_secs: float = 5.0    # Time window for burst detection
    burst_cooldown_secs: float = 10.0 # Pause that side after burst (let other side catch up)

    # === ASYMMETRIC GRID ===
    # When inventory builds, reduce levels on the ADDING side
    # e.g., at 50% long → max 3 buy levels instead of 5
    min_grid_levels_adding: int = 1   # Minimum levels on position-adding side

    # === TREND BIAS ===
    # Detect market direction and align position WITH the trend:
    # - Downtrend: ZERO buy levels → only sells fill → build/maintain SHORT
    # - Uptrend:   ZERO sell levels → only buys fill → build/maintain LONG
    # - Ranging:   symmetric grid, normal market making
    # If holding a counter-trend position (e.g. long in downtrend),
    # the trend-aligned side gets extra levels to unwind faster.
    trend_bias_enabled: bool = True
    trend_ema_fast: int = 50           # Fast EMA period (~10s at 5 updates/sec)
    trend_ema_slow: int = 200          # Slow EMA period (~40s) — slow enough to filter noise
    trend_threshold_bps: float = 2.0   # EMA spread > X bps → enter trend
    trend_exit_threshold_bps: float = 0.5  # EMA spread < X bps → exit trend back to RANGING
                                           # (hysteresis: wide entry, narrow exit = sticky trends)
    trend_bias_max_bps: float = 8.0    # Max reservation price shift in trend direction

    # TREND-AWARE GRID SKEW (replaces the old zero-counter-trend approach)
    # Instead of zeroing one side (which misses pullback fills), we:
    # 1) Always keep at least 1 level on BOTH sides (for spread capture)
    # 2) Skew level counts: more levels on trend side, fewer on counter side
    # 3) Use reservation price shift to make counter-trend fills less likely
    trend_min_levels_per_side: int = 1  # ALWAYS at least 1 level each side
    trend_max_skew_levels: int = 1      # Extra levels on trend side (trend gets base+skew)

    # === BOLLINGER BAND REGIME ===
    # Use BB width to distinguish consolidation from trend:
    # - Wide BB (high vol) + EMA cross = real trend → apply full bias
    # - Narrow BB (compressing) + EMA cross = pullback/consolidation → light bias
    # - BB squeeze (very narrow) = breakout incoming → go symmetric, wait
    bb_period: int = 100              # ~20s of mid prices for BB calculation
    bb_std_multiplier: float = 2.0    # Standard BB width (2σ)
    bb_squeeze_pct: float = 0.3       # BB width < 30% of average → squeeze
    bb_trend_confirm_pct: float = 0.7 # BB width > 70% of average → trend confirmed

    # === TRAILING TAKE-PROFIT ===
    # When position is profitable AND aligned with trend, trail the stop:
    # - Lock in a percentage of peak unrealized profit
    # - If unrealized drops below trail threshold, enter close mode
    trailing_tp_enabled: bool = True
    trailing_tp_activation_usd: float = 0.30  # Start trailing after $0.30 unrealized
    trailing_tp_lock_pct: float = 50.0        # Lock in 50% of peak profit
    trailing_tp_reset_on_flat: bool = True     # Reset trail when position goes flat

    # === MACRO TREND FILTER (5-minute direction) ===
    # The micro EMAs (50/200 ticks) flip-flop every few seconds on noise.
    # This macro filter samples mid price every N seconds, computes an EMA
    # on those samples, and determines the 5-minute direction.
    # It acts as a ONE-WAY GATE: blocks new inventory against the macro
    # direction. E.g., if macro is UP, new short-building sells are blocked
    # (but longs can still be closed via sells).
    # This prevents the "missed rally" problem where the bot shorts during
    # an uptrend because the micro EMAs flip on every micro-pullback.
    macro_trend_enabled: bool = True
    macro_sample_interval_secs: float = 30.0   # Sample mid every 30s
    macro_ema_periods: int = 10                # 10 samples × 30s = 5 minutes
    macro_threshold_bps: float = 5.0           # Need 5bps EMA spread to call a macro trend
    macro_block_adding: bool = True            # Block new inventory against macro direction

    # === POSITION SYNC GUARD ===
    sync_guard_after_fill_secs: float = 10.0  # Don't trust sync resets within 10s of last fill

    # === RUNAWAY DETECTION ===
    runaway_window_secs: float = 30.0  # Window to check for one-sided fills
    runaway_max_one_side: int = 15     # Max fills on one side in window before pause
    runaway_pause_secs: float = 30.0   # How long to pause after runaway

    # === RATE LIMIT BACKOFF ===
    rate_limit_backoff_secs: float = 60.0  # Pause grid refresh on rate limit

    # === WS HEARTBEAT ===
    max_book_stale_secs: float = 30.0  # Kill grid if no book update in 30s

    # === FEE TIER ===
    maker_fee_bps: float = 1.5       # VIP0 maker fee (0.00015)
    taker_fee_bps: float = 4.5       # VIP0 taker fee (0.00045)

    # === EXCHANGE CONFIG ===
    hl_api_url: str = "https://api.hyperliquid-testnet.xyz"  # Override for mainnet


# =============================================================================
# STRATEGY
# =============================================================================

class HLGridMM(Strategy):
    """HFT Grid Market Maker for Hyperliquid.

    Places a symmetric grid of Post-Only limit orders around mid-price,
    skewed by inventory using Avellaneda-Stoikov reservation price.
    """

    def __init__(self, config: HLGridMMConfig) -> None:
        super().__init__(config)

        self._instrument: Instrument | None = None
        self._instrument_id = InstrumentId.from_str(config.instrument_id)

        # Grid state — track full order info for modify-in-place
        # key = "BUY_0"/"SELL_3" etc, value = (client_order_id, price, qty)
        self._active_buy_orders: dict[str, tuple[ClientOrderId, float, float]] = {}
        self._active_sell_orders: dict[str, tuple[ClientOrderId, float, float]] = {}
        self._all_order_ids: set[ClientOrderId] = set()
        self._last_reservation_price: float = 0.0  # Skip modify if res price unchanged

        # Position tracking
        self._net_position: float = 0.0
        self._avg_entry_price: float = 0.0
        self._position_entry_time: float = 0.0
        self._realized_pnl: float = 0.0
        self._unrealized_pnl: float = 0.0

        # Market state
        self._mid_price: float = 0.0
        self._best_bid: float = 0.0
        self._best_ask: float = 0.0
        self._market_spread_bps: float = 0.0
        self._mid_price_history: deque[float] = deque(maxlen=100)

        # Timing
        self._last_refresh_time: float = 0.0
        self._last_log_time: float = 0.0
        self._start_time: float = 0.0

        # Metrics
        self._total_fills: int = 0
        self._buy_fills: int = 0
        self._sell_fills: int = 0
        self._total_volume_usd: float = 0.0
        self._grid_refreshes: int = 0
        self._orders_submitted: int = 0
        self._orders_canceled: int = 0

        # Safety
        self._killed: bool = False
        self._paused: bool = False
        self._flatten_in_progress: bool = False  # Guard against infinite flatten loop
        self._flatten_order_id: ClientOrderId | None = None  # Track the flatten order
        self._position_synced: bool = False  # Block grid until first position sync

        # Exchange position sync
        self._exchange_position: float = 0.0  # Last synced from exchange
        self._exchange_entry_price: float = 0.0
        self._exchange_unrealized_pnl: float = 0.0
        self._last_position_sync: float = 0.0

        # Margin awareness
        self._available_margin_pct: float = 100.0
        self._liquidation_price: float = 0.0
        self._margin_paused: bool = False

        # Runaway detection — track recent fill timestamps by side
        self._recent_buy_fills: deque[float] = deque(maxlen=100)
        self._recent_sell_fills: deque[float] = deque(maxlen=100)
        self._runaway_paused_until: float = 0.0

        # Fill burst cooldown — per-side pause
        self._buy_cooldown_until: float = 0.0
        self._sell_cooldown_until: float = 0.0
        self._last_fill_time: float = 0.0  # For position sync guard

        # Profit floor decay — track when position went underwater
        self._underwater_since: float = 0.0  # timestamp when floor started blocking
        self._was_underwater: bool = False

        # Maker close mode — exit position via maker instead of taker
        self._close_mode: bool = False
        self._close_mode_since: float = 0.0
        self._close_mode_reason: str = ""

        # Trend detection (EMA-based)
        self._ema_fast: float = 0.0
        self._ema_slow: float = 0.0
        self._ema_initialized: bool = False
        self._ema_update_count: int = 0
        self._trend_regime: str = "RANGING"  # TRENDING_UP, TRENDING_DOWN, RANGING
        self._trend_bias_bps: float = 0.0  # Current bias applied (+ = bullish shift)

        # Bollinger Band state
        self._bb_prices: deque[float] = deque(maxlen=500)  # Price history for BB
        self._bb_width_history: deque[float] = deque(maxlen=100)  # BB width history for squeeze detection
        self._bb_upper: float = 0.0
        self._bb_lower: float = 0.0
        self._bb_mid: float = 0.0
        self._bb_width_pct: float = 0.0  # Current BB width as pct of mid
        self._bb_regime: str = "NORMAL"  # SQUEEZE, NORMAL, WIDE
        self._trend_confidence: float = 0.0  # 0.0-1.0: how much to trust the trend signal

        # Trailing take-profit
        self._trail_peak_pnl: float = 0.0  # Peak unrealized P&L since position opened
        self._trail_active: bool = False    # Whether trailing stop is armed
        self._trail_threshold: float = 0.0  # Close mode triggers if PnL drops below this

        # Macro trend filter (5-minute direction)
        self._macro_ema_fast: float = 0.0
        self._macro_ema_slow: float = 0.0
        self._macro_initialized: bool = False
        self._macro_sample_count: int = 0
        self._macro_last_sample_time: float = 0.0
        self._macro_trend: str = "NEUTRAL"  # UP, DOWN, NEUTRAL
        self._macro_trend_bps: float = 0.0   # Current macro EMA spread in bps

        # Last fill price tracking — for spread capture guard
        self._last_buy_fill_price: float = 0.0   # Most recent buy fill price
        self._last_sell_fill_price: float = 0.0   # Most recent sell fill price

        # Rate limit backoff
        self._rate_limited_until: float = 0.0

        # WS heartbeat
        self._last_book_update: float = 0.0

    # -------------------------------------------------------------------------
    # LIFECYCLE
    # -------------------------------------------------------------------------

    def on_start(self) -> None:
        self._start_time = time.time()

        self._instrument = self.cache.instrument(self._instrument_id)
        if self._instrument is None:
            self.log.error(f"❌ Instrument not found: {self._instrument_id}")
            self.stop()
            return

        self.log.info("=" * 60, LogColor.CYAN)
        self.log.info("⚡ HFT GRID MM v001 GENESIS — Hyperliquid", LogColor.CYAN)
        self.log.info(f"   Instrument:  {self._instrument_id}", LogColor.CYAN)
        self.log.info(f"   Grid Levels: {self.config.grid_levels} per side", LogColor.CYAN)
        self.log.info(f"   Half Spread: {self.config.half_spread_bps} bps (min {self.config.min_half_spread_ticks} ticks)", LogColor.CYAN)
        self.log.info(f"   Grid Step:   {self.config.grid_interval_bps} bps (min {self.config.min_interval_ticks} ticks)", LogColor.CYAN)
        self.log.info(f"   Order Size:  ${self.config.order_qty_usd} per level", LogColor.CYAN)
        self.log.info(f"   Max Position:${self.config.max_position_usd}", LogColor.CYAN)
        self.log.info(f"   Maker Fee:   {self.config.maker_fee_bps} bps", LogColor.CYAN)
        self.log.info(f"   Tick Size:   {self._instrument.price_increment}", LogColor.CYAN)
        self.log.info(f"   Lot Size:    {self._instrument.size_increment}", LogColor.CYAN)
        self.log.info(f"   Skewing:     {'ON' if self.config.skew_enabled else 'OFF'}", LogColor.CYAN)
        self.log.info(f"   Min Profit:  {self.config.min_profit_bps} bps", LogColor.CYAN)
        self.log.info("=" * 60, LogColor.CYAN)

        # Subscribe to order book
        self.subscribe_order_book_deltas(self._instrument_id)

        # Sync position from exchange FIRST before placing any orders
        self._sync_position_from_exchange()
        self._position_synced = True
        self.log.info("✅ Initial position sync complete", LogColor.GREEN)

        # Set up refresh timer
        self.clock.set_timer(
            name="grid_refresh",
            interval=timedelta(seconds=self.config.refresh_interval_secs),
            callback=self._on_grid_refresh_event,
        )

        # Set up logging timer
        self.clock.set_timer(
            name="log_state",
            interval=timedelta(seconds=LOG_INTERVAL_SECS),
            callback=self._on_log_state_event,
        )

        # Set up position sync timer (sync from exchange every 15s to reduce API calls)
        self.clock.set_timer(
            name="position_sync",
            interval=timedelta(seconds=15),
            callback=self._on_position_sync_event,
        )

        self.log.info("🟢 Grid MM running — waiting for order book data...", LogColor.GREEN)

    def on_stop(self) -> None:
        self.cancel_all_orders(self._instrument_id)

        # Flatten position on stop (via direct API to guarantee execution)
        if self.config.flatten_on_stop and abs(self._net_position) > 0.0001:
            self.log.warning(
                f"🛑 STOP: Flattening position {self._net_position:+.6f} via API",
                LogColor.YELLOW,
            )
            self._api_flatten_position()

        elapsed = time.time() - self._start_time
        self.log.info("=" * 60, LogColor.YELLOW)
        self.log.info("🔴 GRID MM STOPPED", LogColor.YELLOW)
        self.log.info(f"   Runtime:       {elapsed/60:.1f} min", LogColor.YELLOW)
        self.log.info(f"   Total Fills:   {self._total_fills}", LogColor.YELLOW)
        self.log.info(f"   Buy Fills:     {self._buy_fills}", LogColor.YELLOW)
        self.log.info(f"   Sell Fills:    {self._sell_fills}", LogColor.YELLOW)
        self.log.info(f"   Volume:        ${self._total_volume_usd:,.0f}", LogColor.YELLOW)
        self.log.info(f"   Grid Refresh:  {self._grid_refreshes}", LogColor.YELLOW)
        self.log.info(f"   Orders:        {self._orders_submitted} submitted, {self._orders_canceled} canceled", LogColor.YELLOW)
        self.log.info(f"   Realized PnL:  ${self._realized_pnl:+.2f}", LogColor.YELLOW)
        self.log.info(f"   Net Position:  {self._net_position:.6f}", LogColor.YELLOW)
        self.log.info("=" * 60, LogColor.YELLOW)

    # -------------------------------------------------------------------------
    # MARKET DATA
    # -------------------------------------------------------------------------

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """Process order book updates — update mid price."""
        book = self.cache.order_book(self._instrument_id)
        if book is None:
            return

        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            return

        self._best_bid = float(bid)
        self._best_ask = float(ask)
        self._mid_price = (self._best_bid + self._best_ask) / 2.0
        self._market_spread_bps = ((self._best_ask - self._best_bid) / self._mid_price) / BPS
        self._mid_price_history.append(self._mid_price)
        self._last_book_update = time.time()

        # Update trend EMAs
        self._update_trend_emas()

        # Log first price update
        if len(self._mid_price_history) == 1:
            self.log.info(
                f"📡 First book update: bid={self._best_bid:.2f} ask={self._best_ask:.2f} "
                f"mid={self._mid_price:.2f} spread={self._market_spread_bps:.1f}bps",
                LogColor.GREEN,
            )

    # -------------------------------------------------------------------------
    # TIMER EVENTS
    # -------------------------------------------------------------------------

    def _on_grid_refresh_event(self, event) -> None:
        """Timer callback wrapper for grid refresh."""
        self._on_grid_refresh()

    def _on_log_state_event(self, event) -> None:
        """Timer callback wrapper for logging."""
        self._on_log_state()

    def _on_position_sync_event(self, event) -> None:
        """Timer callback wrapper for position sync."""
        self._sync_position_from_exchange()

    def _on_grid_refresh(self) -> None:
        """Periodic grid refresh — the core loop."""
        if self._killed or self._paused:
            return

        if not self._position_synced:
            self.log.debug("Grid refresh skipped: waiting for position sync")
            return

        if self._flatten_in_progress:
            return  # Don't place grid orders while flattening

        if self._mid_price <= 0.0:
            self.log.debug("Grid refresh skipped: no mid price yet")
            return  # No market data yet

        now = time.time()

        # Rate limit backoff
        if now < self._rate_limited_until:
            return

        # Runaway pause
        if now < self._runaway_paused_until:
            return

        # WS heartbeat — cancel grid if book data is stale
        if (self._last_book_update > 0
                and now - self._last_book_update > self.config.max_book_stale_secs):
            self.log.error(
                f"💀 WS STALE: No book update for {now - self._last_book_update:.0f}s "
                f"> {self.config.max_book_stale_secs}s — canceling all orders",
            )
            self._cancel_all_grid_orders()
            return

        # Respect minimum refresh interval
        if now - self._last_refresh_time < self.config.min_refresh_interval_secs:
            return

        # Safety checks (kill switch, inventory age, margin, liquidation, runaway)
        self._check_safety(now)
        if self._killed:
            return

        # Pause if market spread is too tight (likely crossed/stale)
        if self._market_spread_bps < self.config.pause_on_spread_collapse_bps:
            return

        # Margin pause — only allow closing orders
        if self._margin_paused:
            # Still allow grid refresh, but _refresh_grid will skip adding side
            pass

        # Compute grid and refresh orders
        self._refresh_grid(now)
        self._last_refresh_time = now
        self._grid_refreshes += 1

    def _on_log_state(self) -> None:
        """Periodic state logging with VIP progress tracking."""
        if self._mid_price <= 0.0:
            return

        self._update_unrealized_pnl()
        total_pnl = self._realized_pnl + self._unrealized_pnl
        active_buys = len(self._active_buy_orders)
        active_sells = len(self._active_sell_orders)
        position_usd = abs(self._net_position * self._mid_price)

        # Volume projection
        stats = self._get_volume_stats()
        daily_proj = stats["daily_projected_usd"]
        fills_min = stats["fills_per_min"]

        # VIP tier tracking
        vip_tiers = [
            (0, 0), (1, 5_000_000), (2, 25_000_000), (3, 100_000_000),
            (4, 500_000_000), (5, 2_000_000_000), (6, 7_000_000_000),
        ]
        current_tier = 0
        next_target = 5_000_000
        for tier, cutoff in vip_tiers:
            if daily_proj * 14 >= cutoff:
                current_tier = tier
            else:
                next_target = cutoff
                break
        days_to_next = next_target / max(daily_proj, 1) / 14 if daily_proj > 0 else float("inf")

        self.log.info(
            f"📊 mid={self._mid_price:.2f} | "
            f"pos={self._net_position:+.6f} (${position_usd:,.0f}) | "
            f"entry={self._avg_entry_price:.2f} | "
            f"pnl=${total_pnl:+.2f} (real=${self._realized_pnl:+.2f}) | "
            f"fills={self._total_fills} ({fills_min:.1f}/min) | "
            f"vol=${self._total_volume_usd:,.0f} | "
            f"orders={active_buys}B/{active_sells}S | "
            f"spread={self._market_spread_bps:.1f}bps | "
            f"{self._trend_regime}({self._trend_bias_bps:+.1f}bps) "
            f"BB:{self._bb_regime}(conf={self._trend_confidence:.0%}) "
            f"MACRO:{self._macro_trend}({self._macro_trend_bps:+.1f}bps)",
            LogColor.BLUE,
        )
        self.log.info(
            f"📈 24h proj=${daily_proj:,.0f} | "
            f"14d proj=${daily_proj*14:,.0f} | "
            f"VIP{current_tier} → VIP{min(current_tier+1, 6)} in {days_to_next:.1f}d",
            LogColor.CYAN,
        )

    # -------------------------------------------------------------------------
    # POSITION SYNC (from exchange)
    # -------------------------------------------------------------------------

    def _sync_position_from_exchange(self) -> None:
        """Sync position state from Hyperliquid exchange.

        Polls clearinghouseState to get ground truth position data.
        This compensates for missing WebSocket fill events by ensuring
        the strategy always knows the real position.
        """
        import os
        import requests

        wallet = os.environ.get("HYPERLIQUID_WALLET")
        if not wallet:
            return

        base_url = self.config.hl_api_url

        try:
            r = requests.post(
                f"{base_url}/info",
                json={"type": "clearinghouseState", "user": wallet},
                timeout=5,
            )
            if r.status_code != 200:
                return

            state = r.json()
            positions = state.get("assetPositions", [])

            # Extract coin from instrument_id
            coin = self.config.instrument_id.split("-")[0]  # "ETH"

            for p in positions:
                pos = p.get("position", {})
                if pos.get("coin") == coin:
                    exchange_size = float(pos.get("szi", 0))
                    entry_px = float(pos.get("entryPx", 0))
                    unrealized = float(pos.get("unrealizedPnl", 0))

                    # Detect position drift (our tracking vs exchange truth)
                    drift = abs(exchange_size - self._net_position)
                    if drift > 0.0001:
                        self.log.warning(
                            f"🔄 POSITION SYNC: exchange={exchange_size:+.6f} "
                            f"strategy={self._net_position:+.6f} drift={drift:.6f} "
                            f"entry={entry_px:.2f}",
                            LogColor.YELLOW,
                        )
                        # Adopt exchange position as truth
                        self._net_position = exchange_size
                        self._avg_entry_price = entry_px
                        if abs(exchange_size) > 0 and self._position_entry_time == 0:
                            self._position_entry_time = time.time()
                        elif abs(exchange_size) < 0.0001:
                            self._position_entry_time = 0.0

                    self._exchange_position = exchange_size
                    self._exchange_entry_price = entry_px
                    self._exchange_unrealized_pnl = unrealized
                    self._last_position_sync = time.time()
                    return

            # No position found for this coin — we're flat
            if abs(self._net_position) > 0.0001:
                # SYNC GUARD: Don't trust "flat" within N seconds of last fill.
                # Exchange may not have settled yet. Trusting this causes the
                # phantom-flat → re-buy → double-position → dump cycle.
                time_since_fill = time.time() - self._last_fill_time if self._last_fill_time > 0 else 999
                if time_since_fill < self.config.sync_guard_after_fill_secs:
                    self.log.warning(
                        f"🛡️ SYNC GUARD: exchange says FLAT but last fill was {time_since_fill:.0f}s ago "
                        f"< {self.config.sync_guard_after_fill_secs}s — ignoring reset "
                        f"(keeping pos={self._net_position:+.6f})",
                        LogColor.YELLOW,
                    )
                    return
                self.log.info(
                    f"🔄 POSITION SYNC: exchange=FLAT, strategy={self._net_position:+.6f} → reset",
                    LogColor.YELLOW,
                )
                self._net_position = 0.0
                self._avg_entry_price = 0.0
                self._position_entry_time = 0.0
                self._exchange_position = 0.0

            # --- MARGIN AWARENESS ---
            # For unified accounts, margin comes from spotClearinghouseState
            self._sync_margin_state(base_url, wallet, coin, positions)

        except Exception as e:
            self.log.debug(f"Position sync error: {e}")

    # -------------------------------------------------------------------------
    # GRID LOGIC
    # -------------------------------------------------------------------------

    def _refresh_grid(self, now: float) -> None:
        """Refresh grid using modify-in-place to minimize API calls.

        Instead of canceling all orders and re-placing them (2 API calls),
        we compute the desired grid, diff against existing orders, and:
        - MODIFY orders whose price or qty changed (1 batchModify call)
        - CANCEL orders that have no slot in new grid
        - PLACE orders for empty slots

        This cuts API usage from ~40 calls/cycle to ~1-2 calls/cycle and
        preserves queue priority for unchanged levels.
        """
        # Compute reservation price (Avellaneda-Stoikov)
        reservation_price = self._compute_reservation_price()

        # Snap reservation price — if it didn't change AND all order slots are filled,
        # grid prices are identical so we can skip the reconcile (saves API calls)
        snapped_res = self._snap_price(reservation_price)
        actual_slots = len(self._active_buy_orders) + len(self._active_sell_orders)
        # Only skip if reservation price unchanged AND no cooldown state changes
        if (snapped_res == self._last_reservation_price
                and actual_slots >= self.config.grid_levels * 2
                and self._buy_cooldown_until <= now
                and self._sell_cooldown_until <= now):
            return
        self._last_reservation_price = snapped_res

        # Compute grid prices
        buy_prices, sell_prices = self._compute_grid_prices(reservation_price)

        # NOTE: Profit floor is applied AFTER close mode (below) so that
        # close mode can't override it with below-entry prices.

        # Compute order quantity based on current mid price
        qty = self._compute_order_qty()
        if qty <= 0:
            return

        # Max position check — how much room do we have?
        max_qty = self.config.max_position_usd / self._mid_price if self._mid_price > 0 else 0
        if self.config.max_position_qty > 0:
            max_qty = min(max_qty, self.config.max_position_qty)

        buy_room = max_qty - self._net_position
        sell_room = max_qty + self._net_position

        # CRITICAL: Always allow closing orders even when over max position.
        # If position > max_qty, buy_room or sell_room goes negative.
        # Without this fix, the bot goes 0B/0S and freezes completely.
        if self._net_position > 0 and sell_room < qty:
            sell_room = max(sell_room, abs(self._net_position))  # Always room to close longs
        if self._net_position < 0 and buy_room < qty:
            buy_room = max(buy_room, abs(self._net_position))  # Always room to close shorts

        # Trailing take-profit check
        if self.config.trailing_tp_enabled and abs(self._net_position) > 0:
            self._update_unrealized_pnl()
            upnl = self._unrealized_pnl
            # Track peak unrealized
            if upnl > self._trail_peak_pnl:
                self._trail_peak_pnl = upnl
            # Arm trail once activation threshold is reached
            if upnl >= self.config.trailing_tp_activation_usd and not self._trail_active:
                self._trail_active = True
                lock_pct = self.config.trailing_tp_lock_pct / 100.0
                self._trail_threshold = self._trail_peak_pnl * lock_pct
                self.log.info(
                    f"📈 TRAIL ARMED: peak=${self._trail_peak_pnl:+.2f} "
                    f"threshold=${self._trail_threshold:+.2f} ({lock_pct:.0%})",
                    LogColor.GREEN,
                )
            # Update threshold as peak rises
            if self._trail_active:
                lock_pct = self.config.trailing_tp_lock_pct / 100.0
                self._trail_threshold = self._trail_peak_pnl * lock_pct
                # Trigger close mode if profit drops below trail
                if upnl < self._trail_threshold and not self._close_mode:
                    self.log.info(
                        f"📉 TRAIL TRIGGERED: upnl=${upnl:+.2f} < "
                        f"threshold=${self._trail_threshold:+.2f} "
                        f"(peak=${self._trail_peak_pnl:+.2f}) → CLOSE MODE",
                        LogColor.YELLOW,
                    )
                    self._close_mode = True
                    self._close_mode_since = time.time()
                    self._close_mode_reason = "TRAILING_TP"
        elif abs(self._net_position) < qty * 0.1 and self.config.trailing_tp_reset_on_flat:
            # Reset trail when flat
            self._trail_peak_pnl = 0.0
            self._trail_active = False
            self._trail_threshold = 0.0

        # Margin pause — only allow closing orders (reduce-only mode)
        if self._margin_paused:
            if self._net_position >= 0:
                buy_room = 0  # Don't add to long
            if self._net_position <= 0:
                sell_room = 0  # Don't add to short

        # MACRO WARMUP GUARD — during macro warmup, block inventory-adding.
        # Only allow trades that CLOSE existing positions.
        # This prevents building wrong-side inventory before the macro trend
        # has had time to determine direction (~45-90s after startup).
        if self.config.macro_trend_enabled and not self._macro_initialized:
            if self._net_position >= 0:
                buy_room = 0    # Don't build more long without macro confirmation
            if self._net_position <= 0:
                sell_room = 0   # Don't build more short without macro confirmation

        # CLOSE MODE — tighten closing side, cap adding side
        #
        # CRITICAL LESSON: Close mode can TRAP the bot.
        # Close mode zeros one side and places tight passive orders on
        # the closing side. But if price trends AWAY from those orders,
        # they never fill and the bot sits frozen with a growing loss.
        #
        # ABORT CONDITIONS (any close mode reason):
        #   1. Position is flat → done
        #   2. Unrealized PnL went negative and been in close mode > 30s
        #      → the profit/age opportunity is gone, normal grid better
        #   3. Position exceeds 1.5x max_position_usd
        #      → close mode is making it WORSE (fills on wrong side)
        #
        # After abort, the normal grid with Stoikov skew naturally unwinds:
        # heavy position → quadratic skew → aggressive closing prices,
        # plus the always-both-sides grid captures spread on every pullback.
        if self._close_mode:
            time_in_close = time.time() - self._close_mode_since
            pos_usd = abs(self._net_position) * self._mid_price
            abort_reason = ""

            if abs(self._net_position) < qty * 0.1:
                # Position is flat — exit close mode
                abort_reason = "FLAT"
            elif self._unrealized_pnl < -0.05 and time_in_close > 30.0:
                # Underwater for too long — close mode isn't helping
                abort_reason = f"UNDERWATER(upnl=${self._unrealized_pnl:+.2f}, {time_in_close:.0f}s)"
            elif pos_usd > self.config.max_position_usd * 1.5:
                # Position overshot max by 50% — close mode made it worse
                abort_reason = f"OVERSIZED(${pos_usd:.0f} > ${self.config.max_position_usd * 1.5:.0f})"

            if abort_reason:
                self.log.info(
                    f"🔓 CLOSE MODE ABORT [{self._close_mode_reason}]: {abort_reason} "
                    f"after {time_in_close:.0f}s — resuming normal grid",
                    LogColor.GREEN,
                )
                self._close_mode = False
                self._close_mode_since = 0.0
                self._trail_peak_pnl = 0.0
                self._trail_active = False
                self._trail_threshold = 0.0
                # CRITICAL: Reset inventory age timer so INVENTORY_AGE doesn't
                # immediately re-trigger on the next tick. Give it a fresh window.
                if self._close_mode_reason == "INVENTORY_AGE":
                    self._position_entry_time = time.time()
            else:
                # Active close mode — tighten closing side
                close_interval = self.config.close_mode_interval_bps * BPS * self._mid_price
                if self._net_position > 0:
                    buy_room = min(buy_room, qty)  # Cap buys to 1 level
                    tighten = self.config.close_mode_tighten_bps * BPS * self._mid_price
                    sell_prices = [
                        self._snap_price(self._mid_price + tighten + (i * close_interval))
                        for i in range(self.config.grid_levels)
                    ]
                elif self._net_position < 0:
                    sell_room = min(sell_room, qty)  # Cap sells to 1 level
                    tighten = self.config.close_mode_tighten_bps * BPS * self._mid_price
                    buy_prices = [
                        self._snap_price(self._mid_price - tighten - (i * close_interval))
                        for i in range(self.config.grid_levels)
                    ]

        # Apply profit floor ONLY in close mode.
        # In normal grid mode, Stoikov skew + trend bias handle pricing —
        # the floor would freeze the bot when underwater (0 fills, 0 volume).
        # In close mode, the floor prevents giving back profit on take-profit exits,
        # with time decay so we eventually unwind even at a small loss.
        if self._close_mode:
            buy_prices, sell_prices = self._apply_profit_floor(buy_prices, sell_prices)

        # ASYMMETRIC GRID LEVELS — reduce levels on the position-ADDING side
        # as inventory grows, to prevent the one-sided accumulation pattern.
        # At 0% inventory: full levels both sides (e.g. 5B/5S)
        # At 50% inventory: reduced adding side (e.g. 3B/5S when long)
        # At 100% inventory: minimum adding side (e.g. 1B/5S when long)
        base_levels = self.config.grid_levels
        min_adding = self.config.min_grid_levels_adding
        inv_pct = abs(self._net_position) / max(max_qty, 1e-8)
        inv_pct = min(inv_pct, 1.0)
        # Linear interpolation: full levels at 0% → min levels at 100%
        adding_levels = max(min_adding, int(base_levels * (1.0 - inv_pct * 0.8) + 0.5))
        closing_levels = base_levels  # Always keep full closing side

        if self._net_position > 0:
            buy_levels = adding_levels
            sell_levels = closing_levels
        elif self._net_position < 0:
            buy_levels = closing_levels
            sell_levels = adding_levels
        else:
            buy_levels = base_levels
            sell_levels = base_levels

        # TREND-AWARE GRID — ALWAYS keep orders on both sides.
        #
        # The old approach zeroed one side entirely (e.g. zero buys in downtrend).
        # This MISSED pullback fills — the best fills happen when price
        # retraces to the mean after a trend move. The old simple grid caught
        # these because it always had buy orders resting on the book.
        #
        # NEW APPROACH: Skew level counts, never zero.
        #   TRENDING_DOWN → more sell levels, fewer buy levels (but always ≥1)
        #   TRENDING_UP   → more buy levels, fewer sell levels (but always ≥1)
        #   RANGING       → symmetric grid
        #
        # The Avellaneda-Stoikov reservation price shift handles the rest:
        # In a downtrend, buys are pushed further below mid, so they only
        # fill on deep pullbacks (exactly when we WANT to buy).
        #
        # Bollinger Band regime adjusts confidence:
        #   SQUEEZE (narrow BB) = pullback/consolidation → ignore trend signal
        #   WIDE BB + EMA cross = confirmed trend → apply full skew
        min_per_side = max(self.config.trend_min_levels_per_side, 1)
        skew_extra = self.config.trend_max_skew_levels

        if (self.config.trend_bias_enabled
                and self._ema_initialized
                and self._trend_regime != "RANGING"):

            # Scale the level skew by trend confidence (BB-derived)
            # In a squeeze/consolidation, confidence is low → nearly symmetric
            effective_skew = max(0, int(skew_extra * self._trend_confidence + 0.5))

            if self._trend_regime == "TRENDING_DOWN":
                sell_levels = min(base_levels + effective_skew, base_levels)
                buy_levels = max(min_per_side, base_levels - effective_skew)
            elif self._trend_regime == "TRENDING_UP":
                buy_levels = min(base_levels + effective_skew, base_levels)
                sell_levels = max(min_per_side, base_levels - effective_skew)
        else:
            # RANGING or trend not initialized: symmetric, but cap adding side
            # if already holding inventory (prevent position from growing)
            if abs(self._net_position) > qty * 0.5:
                if self._net_position > 0:
                    buy_levels = max(min_per_side, adding_levels)
                    sell_levels = base_levels
                else:
                    sell_levels = max(min_per_side, adding_levels)
                    buy_levels = base_levels

        # FILL BURST COOLDOWN — if one side got too many fills recently,
        # zero out that side's room to let the opposite side catch up.
        if now < self._buy_cooldown_until:
            buy_room = 0
        if now < self._sell_cooldown_until:
            sell_room = 0

        # MACRO TREND GATE — block new inventory against the 5-minute direction.
        # This is the #1 fix for the "missed rally" problem:
        # - Macro UP: don't allow sells that BUILD shorts (but always allow
        #   sells that REDUCE longs = closing trades)
        # - Macro DOWN: don't allow buys that BUILD longs (but always allow
        #   buys that REDUCE shorts = closing trades)
        # This ensures the bot rides multi-minute trends instead of fighting them.
        if self.config.macro_trend_enabled and self._macro_initialized and self.config.macro_block_adding:
            if self._macro_trend == "UP":
                # Block SHORT-BUILDING sells. Allow sells that reduce longs.
                if self._net_position <= 0:
                    # Already flat or short — block ALL sells (would build more short)
                    sell_room = 0
                    sell_levels = 0
                else:
                    # Long — cap sell_room to current position so we don't
                    # overshoot into a short via filling
                    sell_room = min(sell_room, self._net_position)
            elif self._macro_trend == "DOWN":
                # Block LONG-BUILDING buys. Allow buys that reduce shorts.
                if self._net_position >= 0:
                    # Already flat or long — block ALL buys (would build more long)
                    buy_room = 0
                    buy_levels = 0
                else:
                    # Short — cap buy_room to current short so we don't
                    # overshoot into a long via filling
                    buy_room = min(buy_room, abs(self._net_position))

        # POST-ONLY SAFETY CLAMP — prevent orders from crossing the spread.
        #
        # When inventory is extreme, the Stoikov quadratic skew + trend bias
        # can push reservation price so far from mid that sell prices end up
        # BELOW the best bid, or buy prices ABOVE the best ask.
        # PostOnly orders would be rejected ("would have immediately matched").
        #
        # Fix: clamp sell prices to >= mid, buy prices to <= mid.
        # This ensures we always post on the correct side of the book.
        # The grid spacing still applies above/below mid respectively.
        tick = float(self._instrument.price_increment) if self._instrument else 0.0001
        mid = self._mid_price
        for i in range(len(sell_prices)):
            if sell_prices[i] < mid:
                sell_prices[i] = self._snap_price(mid + tick * (i + 1))
        for i in range(len(buy_prices)):
            if buy_prices[i] > mid:
                buy_prices[i] = self._snap_price(mid - tick * (i + 1))

        # Build desired order specs: list of (key, side, price, qty)
        desired_buys: dict[str, tuple[float, float]] = {}
        for i, price in enumerate(buy_prices[:buy_levels]):
            if buy_room <= 0:
                break
            effective_qty = min(qty, buy_room)
            if effective_qty * price < 5.0:
                continue
            key = f"BUY_{i}"
            desired_buys[key] = (price, effective_qty)
            buy_room -= effective_qty

        desired_sells: dict[str, tuple[float, float]] = {}
        for i, price in enumerate(sell_prices[:sell_levels]):
            if sell_room <= 0:
                break
            effective_qty = min(qty, sell_room)
            if effective_qty * price < 5.0:
                continue
            key = f"SELL_{i}"
            desired_sells[key] = (price, effective_qty)
            sell_room -= effective_qty

        # Diff existing vs desired and issue modify/cancel/place
        modified = 0
        placed = 0
        canceled = 0

        # Process BUYS
        modified_b, placed_b, canceled_b = self._reconcile_side(
            existing=self._active_buy_orders,
            desired=desired_buys,
            side=OrderSide.BUY,
        )
        modified += modified_b
        placed += placed_b
        canceled += canceled_b

        # Process SELLS
        modified_s, placed_s, canceled_s = self._reconcile_side(
            existing=self._active_sell_orders,
            desired=desired_sells,
            side=OrderSide.SELL,
        )
        modified += modified_s
        placed += placed_s
        canceled += canceled_s

        action = "" if (modified + placed + canceled) == 0 else f" | Δ mod={modified} new={placed} can={canceled}"
        cooldown_info = ""
        if self._close_mode:
            cooldown_info += f" 🔒CLOSE({self._close_mode_reason})"
        if now < self._buy_cooldown_until:
            cooldown_info += " BUY_CD"
        if now < self._sell_cooldown_until:
            cooldown_info += " SELL_CD"
        # Show actual room (after close mode zeroing), not theoretical
        actual_buy_room = sum(q for _, q in desired_buys.values())
        actual_sell_room = sum(q for _, q in desired_sells.values())
        trend_info = ""
        if self.config.trend_bias_enabled and self._ema_initialized:
            trend_info = f" {self._trend_regime}({self._trend_bias_bps:+.1f}bps) BB:{self._bb_regime}({self._trend_confidence:.0%})"
        macro_info = f" M:{self._macro_trend}" if self._macro_initialized else ""
        self.log.info(
            f"🔄 Grid refresh: mid={self._mid_price:.2f} res={reservation_price:.2f} "
            f"pos={self._net_position:+.4f} entry={self._avg_entry_price:.2f} "
            f"qty={qty:.6f} grid={len(desired_buys)}B/{len(desired_sells)}S "
            f"buy_room={actual_buy_room:.4f} "
            f"sell_room={actual_sell_room:.4f}{action}{cooldown_info}{trend_info}{macro_info}",
        )

    def _reconcile_side(
        self,
        existing: dict[str, tuple[ClientOrderId, float, float]],
        desired: dict[str, tuple[float, float]],
        side: OrderSide,
    ) -> tuple[int, int, int]:
        """Reconcile existing orders against desired grid for one side.

        Returns (modified, placed, canceled) counts.
        """
        modified = 0
        placed = 0
        canceled = 0

        all_keys = set(existing.keys()) | set(desired.keys())

        for key in all_keys:
            have = existing.get(key)  # (client_order_id, price, qty) or None
            want = desired.get(key)   # (price, qty) or None

            if have and want:
                # Both exist — check if modification needed
                client_oid, old_px, old_qty = have
                new_px, new_qty = want

                # Check if price or qty actually changed (with tolerance)
                px_changed = abs(old_px - new_px) > 1e-8
                qty_changed = abs(old_qty - new_qty) > 1e-8

                if px_changed or qty_changed:
                    # Modify in-place
                    order = self.cache.order(client_oid)
                    if order is not None and order.is_open:
                        try:
                            new_price = self._instrument.make_price(self._snap_price(new_px))
                            new_quantity = self._instrument.make_qty(new_qty)
                            self.modify_order(order, quantity=new_quantity, price=new_price)
                            # Update tracking with new price/qty
                            existing[key] = (client_oid, new_px, new_qty)
                            modified += 1
                        except Exception as e:
                            self.log.warning(f"Modify failed for {key}: {e}")
                    else:
                        # Order gone (filled/canceled) — place fresh
                        del existing[key]
                        level = int(key.split("_")[1])
                        self._place_grid_order(side, new_px, new_qty, level)
                        placed += 1
                # else: no change needed, keep resting (preserves queue priority)

            elif have and not want:
                # Exists but not desired — cancel
                client_oid, _, _ = have
                order = self.cache.order(client_oid)
                if order is not None and order.is_open:
                    self.cancel_order(order)
                    self._orders_canceled += 1
                    canceled += 1
                del existing[key]

            elif want and not have:
                # Desired but doesn't exist — place new
                new_px, new_qty = want
                level = int(key.split("_")[1])
                self._place_grid_order(side, new_px, new_qty, level)
                placed += 1

        return modified, placed, canceled

    def _compute_reservation_price(self) -> float:
        """Avellaneda-Stoikov reservation price.

        Shifts mid price based on inventory:
        - Long position → reservation price drops (cheaper bids, cheaper asks = sell faster)
        - Short position → reservation price rises (buy faster)
        """
        if not self.config.skew_enabled or self._mid_price <= 0:
            return self._mid_price

        # Normalize position to [-1, 1] range
        max_qty = self.config.max_position_usd / self._mid_price if self._mid_price > 0 else 1.0
        normalized_position = self._net_position / max(max_qty, 1e-8)
        normalized_position = max(-1.0, min(1.0, normalized_position))

        # QUADRATIC skew: gentle at low inventory, aggressive at high inventory
        # At 10% inventory: skew = 25 * 0.1^2 = 0.25 bps (gentle)
        # At 50% inventory: skew = 25 * 0.5^2 = 6.25 bps (moderate)
        # At 100% inventory: skew = 25 * 1.0^2 = 25 bps (dump mode)
        sign = 1.0 if normalized_position >= 0 else -1.0
        quad_norm = normalized_position * normalized_position * sign
        skew = self.config.skew_bps_per_unit * BPS * self._mid_price * quad_norm
        reservation = self._mid_price - skew

        # TREND BIAS: shift reservation price in trend direction
        # Uptrend → reservation moves UP → tighter buys (catch dips), wider sells
        # Downtrend → reservation moves DOWN → tighter sells (catch rallies), wider buys
        # This makes us accumulate WITH the trend, not against it
        if self.config.trend_bias_enabled and self._ema_initialized:
            trend_shift = self._trend_bias_bps * BPS * self._mid_price
            reservation += trend_shift

        # CAP TOTAL SHIFT: The combined inventory skew + trend bias can exceed
        # the half-spread, causing the grid to cross over (sells below mid,
        # buys above mid). This guarantees losing roundtrips.
        #
        # Cap the total reservation shift to 80% of half-spread, so the
        # inner grid level always has at least 20% of half-spread from mid.
        # This preserves spread capture in all conditions.
        max_shift = self.config.half_spread_bps * BPS * self._mid_price * 0.8
        tick = self._get_tick_size(self._mid_price)
        # Also respect tick-based minimum: ensure at least min_half_spread_ticks remain
        min_remaining = self.config.min_half_spread_ticks * tick
        effective_max_shift = max_shift
        # But don't shift so much that inner level is < min_remaining ticks from mid
        half_spread_abs = self.config.half_spread_bps * BPS * self._mid_price
        if half_spread_abs - effective_max_shift < min_remaining:
            effective_max_shift = max(0, half_spread_abs - min_remaining)

        total_shift = reservation - self._mid_price
        if total_shift > effective_max_shift:
            reservation = self._mid_price + effective_max_shift
        elif total_shift < -effective_max_shift:
            reservation = self._mid_price - effective_max_shift

        return reservation

    def _update_trend_emas(self) -> None:
        """Update fast/slow EMAs, Bollinger Bands, and detect trend regime.

        Called on every book update. Uses:
        1. EMA crossover for trend direction (TRENDING_UP/DOWN/RANGING)
        2. Bollinger Band width for trend confidence (SQUEEZE/NORMAL/WIDE)
        3. Hysteresis for sticky regime transitions

        The trend_confidence output (0.0-1.0) controls how much the grid
        skews. In a squeeze/consolidation, confidence drops toward 0 and
        the grid stays nearly symmetric — catching pullback fills.
        In a confirmed wide-BB trend, confidence is 1.0 and the grid
        skews heavily toward the trend side.
        """
        if not self.config.trend_bias_enabled or self._mid_price <= 0:
            self._trend_regime = "RANGING"
            self._trend_bias_bps = 0.0
            self._trend_confidence = 0.0
            return

        self._ema_update_count += 1

        # --- BOLLINGER BANDS ---
        self._bb_prices.append(self._mid_price)
        if len(self._bb_prices) >= self.config.bb_period:
            prices = list(self._bb_prices)
            n = self.config.bb_period
            recent = prices[-n:]
            bb_mean = sum(recent) / n
            bb_std = (sum((p - bb_mean) ** 2 for p in recent) / n) ** 0.5
            self._bb_mid = bb_mean
            self._bb_upper = bb_mean + self.config.bb_std_multiplier * bb_std
            self._bb_lower = bb_mean - self.config.bb_std_multiplier * bb_std
            self._bb_width_pct = (self._bb_upper - self._bb_lower) / bb_mean * 100 if bb_mean > 0 else 0

            # Track width history for squeeze detection
            self._bb_width_history.append(self._bb_width_pct)
            if len(self._bb_width_history) >= 20:
                avg_width = sum(self._bb_width_history) / len(self._bb_width_history)
                if avg_width > 0:
                    width_ratio = self._bb_width_pct / avg_width
                    if width_ratio < self.config.bb_squeeze_pct:
                        self._bb_regime = "SQUEEZE"  # Consolidation/pullback
                    elif width_ratio > self.config.bb_trend_confirm_pct:
                        self._bb_regime = "WIDE"     # Confirmed volatility
                    else:
                        self._bb_regime = "NORMAL"

        # --- EMAs ---
        if not self._ema_initialized:
            if self._ema_update_count == 1:
                self._ema_fast = self._mid_price
                self._ema_slow = self._mid_price
            alpha_fast = 2.0 / (self.config.trend_ema_fast + 1)
            alpha_slow = 2.0 / (self.config.trend_ema_slow + 1)
            self._ema_fast += alpha_fast * (self._mid_price - self._ema_fast)
            self._ema_slow += alpha_slow * (self._mid_price - self._ema_slow)
            if self._ema_update_count >= self.config.trend_ema_slow:
                self._ema_initialized = True
            return

        alpha_fast = 2.0 / (self.config.trend_ema_fast + 1)
        alpha_slow = 2.0 / (self.config.trend_ema_slow + 1)
        self._ema_fast += alpha_fast * (self._mid_price - self._ema_fast)
        self._ema_slow += alpha_slow * (self._mid_price - self._ema_slow)

        # --- TREND REGIME (with hysteresis) ---
        ema_spread_bps = (self._ema_fast - self._ema_slow) / self._ema_slow * 10000
        threshold = self.config.trend_threshold_bps
        max_bias = self.config.trend_bias_max_bps

        if ema_spread_bps > threshold:
            self._trend_regime = "TRENDING_UP"
            strength = min((ema_spread_bps - threshold) / max(threshold, 0.1), 1.0)
            self._trend_bias_bps = strength * max_bias
        elif ema_spread_bps < -threshold:
            self._trend_regime = "TRENDING_DOWN"
            strength = min((-ema_spread_bps - threshold) / max(threshold, 0.1), 1.0)
            self._trend_bias_bps = -strength * max_bias
        else:
            exit_thresh = self.config.trend_exit_threshold_bps
            if self._trend_regime == "TRENDING_UP" and ema_spread_bps > exit_thresh:
                strength = min(ema_spread_bps / max(threshold, 0.1), 1.0)
                self._trend_bias_bps = strength * max_bias
            elif self._trend_regime == "TRENDING_DOWN" and ema_spread_bps < -exit_thresh:
                strength = min(-ema_spread_bps / max(threshold, 0.1), 1.0)
                self._trend_bias_bps = -strength * max_bias
            else:
                self._trend_regime = "RANGING"
                self._trend_bias_bps = 0.0

        # --- MICRO-MACRO ALIGNMENT ---
        # If the macro (5-min) trend is established, don't let the micro
        # (10-40s) trend go against it. This prevents the reservation price
        # from shifting the wrong direction during pullbacks within a larger move.
        # Macro UP + Micro DOWN → force RANGING (no negative bias)
        # Macro DOWN + Micro UP → force RANGING (no positive bias)
        if self.config.macro_trend_enabled and self._macro_initialized:
            if self._macro_trend == "UP" and self._trend_bias_bps < 0:
                self._trend_regime = "RANGING"
                self._trend_bias_bps = 0.0
            elif self._macro_trend == "DOWN" and self._trend_bias_bps > 0:
                self._trend_regime = "RANGING"
                self._trend_bias_bps = 0.0

        # --- MACRO TREND SAMPLER (5-minute direction) ---
        # Sample mid price every 30s, compute slow EMA to determine the
        # macro trend direction. This is the 5-minute chart equivalent.
        # Unlike the micro EMAs which flip every few seconds, this only
        # changes direction after 2-3 minutes of sustained movement.
        if self.config.macro_trend_enabled:
            now_ts = time.time()
            if (self._macro_last_sample_time == 0.0
                    or now_ts - self._macro_last_sample_time >= self.config.macro_sample_interval_secs):
                self._macro_last_sample_time = now_ts
                self._macro_sample_count += 1

                if self._macro_sample_count == 1:
                    self._macro_ema_fast = self._mid_price
                    self._macro_ema_slow = self._mid_price
                else:
                    # Fast macro EMA: ~2 min (4 samples × 30s)
                    alpha_fast_m = 2.0 / (4 + 1)
                    # Slow macro EMA: ~5 min (10 samples × 30s)
                    alpha_slow_m = 2.0 / (self.config.macro_ema_periods + 1)
                    self._macro_ema_fast += alpha_fast_m * (self._mid_price - self._macro_ema_fast)
                    self._macro_ema_slow += alpha_slow_m * (self._mid_price - self._macro_ema_slow)

                if self._macro_sample_count >= 3:  # Initialize after 3 samples (90s) — fast enough to be useful
                    self._macro_initialized = True

                if self._macro_initialized:
                    macro_spread = (self._macro_ema_fast - self._macro_ema_slow) / self._macro_ema_slow * 10000
                    self._macro_trend_bps = macro_spread
                    old_macro = self._macro_trend
                    if macro_spread > self.config.macro_threshold_bps:
                        self._macro_trend = "UP"
                    elif macro_spread < -self.config.macro_threshold_bps:
                        self._macro_trend = "DOWN"
                    else:
                        self._macro_trend = "NEUTRAL"
                    if self._macro_trend != old_macro:
                        self.log.info(
                            f"🧭 MACRO TREND: {old_macro} → {self._macro_trend} "
                            f"(spread={macro_spread:+.1f}bps, "
                            f"fast={self._macro_ema_fast:.5f}, slow={self._macro_ema_slow:.5f})",
                            LogColor.MAGENTA,
                        )

        # --- TREND CONFIDENCE (BB-derived) ---
        # Squeeze = pullback within trend → low confidence → symmetric grid
        # Wide BB = real trend momentum → high confidence → skewed grid
        if self._bb_regime == "SQUEEZE":
            self._trend_confidence = 0.2  # Almost symmetric — catch pullback fills
        elif self._bb_regime == "WIDE":
            self._trend_confidence = 1.0  # Full skew — confirmed trend
        else:
            self._trend_confidence = 0.6  # Moderate skew

    def _compute_grid_prices(self, reservation_price: float) -> tuple[list[float], list[float]]:
        """Compute buy and sell grid prices around reservation price.

        Returns:
            (buy_prices, sell_prices) — sorted closest to mid first.
        """
        half_spread = self.config.half_spread_bps * BPS * reservation_price
        interval = self.config.grid_interval_bps * BPS * reservation_price

        # TICK-BASED MINIMUM: On low-price assets, bps spacing collapses
        # to 1-2 ticks and the grid can't capture spread above fees.
        # Enforce minimum spacing in ticks to guarantee profitability.
        tick = self._get_tick_size(reservation_price)
        min_half_spread = self.config.min_half_spread_ticks * tick
        min_interval = self.config.min_interval_ticks * tick
        if half_spread < min_half_spread:
            half_spread = min_half_spread
        if interval < min_interval:
            interval = min_interval

        buy_prices = []
        sell_prices = []

        for i in range(self.config.grid_levels):
            buy_price = reservation_price - half_spread - (i * interval)
            sell_price = reservation_price + half_spread + (i * interval)

            # Snap to Hyperliquid 5 significant figures
            buy_price = self._snap_price(buy_price)
            sell_price = self._snap_price(sell_price)

            buy_prices.append(buy_price)
            sell_prices.append(sell_price)

        # The reservation price shift cap in _compute_reservation_price
        # ensures sell prices are always above mid and buy prices below mid,
        # so every roundtrip captures spread. No additional entry-price guard
        # needed here — the cap guarantees the grid never crosses over.

        return buy_prices, sell_prices

    def _apply_profit_floor(
        self,
        buy_prices: list[float],
        sell_prices: list[float],
    ) -> tuple[list[float], list[float]]:
        """Apply profit floor to closing-side grid prices WITH TIME DECAY.

        The floor prevents selling below entry (the #1 cause of P&L bleeding),
        BUT it decays over time to prevent the bot from freezing.

        Timeline when underwater:
        - 0 to decay_start:  Full floor (entry + fees + min_profit)
        - decay_start to decay_end:  Linear decay from +min_profit to -max_loss_accept
        - After decay_end:  Floor at entry - max_loss_accept_bps (accepting losses to unwind)

        This ensures the bot ALWAYS resumes trading after a brief pause.
        """
        if self._net_position == 0.0 or self._avg_entry_price <= 0.0:
            self._was_underwater = False
            self._underwater_since = 0.0
            return buy_prices, sell_prices

        now = time.time()

        # Roundtrip fee cost in price terms (entry side + exit side)
        fee_cost_bps = 2.0 * self.config.maker_fee_bps  # Both legs are maker (ALO)

        # Check if position is underwater (floor would block trades at mid)
        full_floor_bps = fee_cost_bps + self.config.min_profit_bps
        is_underwater = False
        if self._net_position > 0:
            ideal_min_sell = self._avg_entry_price * (1.0 + full_floor_bps * BPS)
            is_underwater = ideal_min_sell > self._mid_price
        elif self._net_position < 0:
            ideal_max_buy = self._avg_entry_price * (1.0 - full_floor_bps * BPS)
            is_underwater = ideal_max_buy < self._mid_price

        # Track underwater timing
        if is_underwater:
            if not self._was_underwater:
                self._underwater_since = now
                self._was_underwater = True
        else:
            self._was_underwater = False
            self._underwater_since = 0.0

        # Compute effective edge with time decay
        if is_underwater and self._underwater_since > 0:
            underwater_secs = now - self._underwater_since
            decay_start = self.config.profit_floor_decay_start_secs
            decay_end = self.config.profit_floor_decay_end_secs

            if underwater_secs <= decay_start:
                # Phase 1: Full protection
                effective_edge_bps = full_floor_bps
                decay_pct = 0.0
            elif underwater_secs >= decay_end:
                # Phase 3: Maximum loss acceptance
                effective_edge_bps = fee_cost_bps - self.config.max_loss_accept_bps
                decay_pct = 100.0
            else:
                # Phase 2: Linear decay
                t = (underwater_secs - decay_start) / max(decay_end - decay_start, 1.0)
                top = full_floor_bps
                bottom = fee_cost_bps - self.config.max_loss_accept_bps
                effective_edge_bps = top + t * (bottom - top)
                decay_pct = t * 100.0
        else:
            effective_edge_bps = full_floor_bps
            decay_pct = 0.0

        edge_multiplier = effective_edge_bps * BPS

        if self._net_position > 0:
            # LONG — floor all sell prices
            min_sell = self._snap_price(self._avg_entry_price * (1.0 + edge_multiplier))

            new_sells = []
            for px in sell_prices:
                floored = self._snap_price(max(px, min_sell))
                new_sells.append(floored)
            sell_prices = new_sells

            if min_sell > self._mid_price:
                uw_secs = now - self._underwater_since if self._underwater_since > 0 else 0
                self.log.info(
                    f"🛡️ FLOOR: long {self._net_position:+.1f} @ {self._avg_entry_price:.4f} "
                    f"→ min_sell={min_sell:.4f} (mid={self._mid_price:.4f} "
                    f"edge={effective_edge_bps:+.1f}bps decay={decay_pct:.0f}% "
                    f"uw={uw_secs:.0f}s)",
                    LogColor.YELLOW,
                )

        elif self._net_position < 0:
            # SHORT — cap all buy prices
            max_buy = self._snap_price(self._avg_entry_price * (1.0 - edge_multiplier))

            new_buys = []
            for px in buy_prices:
                capped = self._snap_price(min(px, max_buy))
                new_buys.append(capped)
            buy_prices = new_buys

            if max_buy < self._mid_price:
                uw_secs = now - self._underwater_since if self._underwater_since > 0 else 0
                self.log.info(
                    f"🛡️ FLOOR: short {self._net_position:+.1f} @ {self._avg_entry_price:.4f} "
                    f"→ max_buy={max_buy:.4f} (mid={self._mid_price:.4f} "
                    f"edge={effective_edge_bps:+.1f}bps decay={decay_pct:.0f}% "
                    f"uw={uw_secs:.0f}s)",
                    LogColor.YELLOW,
                )

        return buy_prices, sell_prices

    @staticmethod
    def _round_to_sig_figs(value: float, sig_figs: int = 5) -> float:
        """Round a value to N significant figures (Hyperliquid max = 5).

        Integer prices are always valid regardless of sig figs.
        """
        if value == 0:
            return 0.0
        import math
        d = math.ceil(math.log10(abs(value)))
        power = sig_figs - d
        magnitude = 10 ** power
        rounded = round(value * magnitude) / magnitude
        return rounded

    def _get_tick_size(self, price: float) -> float:
        """Get the tick size for a given price on Hyperliquid.

        HL uses 5 significant figures. The tick is the value of the
        least significant figure:
          $2.09XX → tick = $0.0001 (5th sig fig is 4th decimal)
          $20.9XX → tick = $0.001 (5th sig fig is 3rd decimal)
          $209.XX → tick = $0.01
          $2090.X → tick = $0.1
        """
        if price <= 0:
            return 0.0001
        import math
        d = math.ceil(math.log10(abs(price)))  # Number of digits left of decimal
        # 5 sig figs → (5 - d) decimal places → tick = 10^-(5-d)
        tick = 10.0 ** -(5 - d)
        return tick

    def _snap_price(self, price: float) -> float:
        """Snap a price to Hyperliquid's valid tick grid.

        Hyperliquid rule: prices have at most 5 significant figures,
        and no more than (MAX_DECIMALS - szDecimals) decimal places.
        For perps MAX_DECIMALS=6. Integer prices are always valid.

        This replaces instrument.make_price() which may have wrong precision
        loaded from the Rust adapter.
        """
        if price <= 0:
            return 0.0
        # Step 1: Round to 5 significant figures
        rounded = self._round_to_sig_figs(price, 5)
        # Step 2: Determine max decimal places (6 - szDecimals for perps)
        # For ETH szDecimals=4 → max 2 decimals. For BTC szDecimals=5 → max 1.
        # But 5 sig figs already constrains this more tightly for most prices.
        # E.g., ETH ~$1950: 5 sig figs → 1 decimal place (1950.1)
        #       BTC ~$95000: 5 sig figs → 0 decimal places (95001)
        return rounded

    def _compute_order_qty(self) -> float:
        """Compute order quantity in base units from USD notional."""
        if self._mid_price <= 0:
            return 0.0

        raw_qty = self.config.order_qty_usd / self._mid_price
        # Snap to instrument lot size
        try:
            snapped = float(self._instrument.make_qty(raw_qty))
            return snapped
        except Exception:
            return 0.0

    # -------------------------------------------------------------------------
    # ORDER MANAGEMENT
    # -------------------------------------------------------------------------

    def _place_grid_order(
        self,
        side: OrderSide,
        price: float,
        qty: float,
        grid_level: int,
    ) -> None:
        """Place a single grid order."""
        try:
            snapped_price = self._snap_price(price)
            order = self.order_factory.limit(
                instrument_id=self._instrument_id,
                order_side=side,
                quantity=self._instrument.make_qty(qty),
                price=self._instrument.make_price(snapped_price),
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
            self.submit_order(order)
            self._orders_submitted += 1

            key = f"{side.name}_{grid_level}"
            if side == OrderSide.BUY:
                self._active_buy_orders[key] = (order.client_order_id, snapped_price, qty)
            else:
                self._active_sell_orders[key] = (order.client_order_id, snapped_price, qty)
            self._all_order_ids.add(order.client_order_id)

        except Exception as e:
            self.log.warning(f"Failed to place {side.name} order L{grid_level}: {e}")

    def _get_volume_stats(self) -> dict:
        """Return volume stats for external monitoring."""
        elapsed = max(time.time() - self._start_time, 1.0)
        elapsed_hours = elapsed / 3600.0
        daily_projected = (self._total_volume_usd / elapsed_hours) * 24.0 if elapsed_hours > 0 else 0.0
        fill_rate = self._total_fills / elapsed * 60.0  # fills per minute
        return {
            "total_volume_usd": self._total_volume_usd,
            "total_fills": self._total_fills,
            "elapsed_hours": elapsed_hours,
            "daily_projected_usd": daily_projected,
            "fills_per_min": fill_rate,
            "buy_fills": self._buy_fills,
            "sell_fills": self._sell_fills,
            "realized_pnl": self._realized_pnl,
        }

    def _cancel_all_grid_orders(self) -> None:
        """Cancel all active grid orders."""
        self.cancel_all_orders(self._instrument_id)
        total = len(self._active_buy_orders) + len(self._active_sell_orders)
        self._orders_canceled += total
        self._active_buy_orders.clear()
        self._active_sell_orders.clear()

    # -------------------------------------------------------------------------
    # FILLS
    # -------------------------------------------------------------------------

    def on_order_filled(self, event) -> None:
        """Process order fills — update position and PnL."""
        fill_qty = float(event.last_qty)
        fill_px = float(event.last_px)
        fill_usd = fill_qty * fill_px

        # Track fill timestamps for runaway detection and burst cooldown
        fill_time = time.time()
        self._last_fill_time = fill_time
        if event.order_side == OrderSide.BUY:
            self._recent_buy_fills.append(fill_time)
            self._last_buy_fill_price = fill_px  # Track for spread capture guard
            # Burst cooldown: if N buys in window, pause buy side
            window_start = fill_time - self.config.burst_window_secs
            recent = sum(1 for t in self._recent_buy_fills if t > window_start)
            if recent >= self.config.burst_count:
                self._buy_cooldown_until = fill_time + self.config.burst_cooldown_secs
        else:
            self._recent_sell_fills.append(fill_time)
            self._last_sell_fill_price = fill_px  # Track for spread capture guard
            window_start = fill_time - self.config.burst_window_secs
            recent = sum(1 for t in self._recent_sell_fills if t > window_start)
            if recent >= self.config.burst_count:
                self._sell_cooldown_until = fill_time + self.config.burst_cooldown_secs

        if event.order_side == OrderSide.BUY:
            # Update average entry price
            if self._net_position >= 0:
                # Adding to long or going long from flat
                old_cost = self._net_position * self._avg_entry_price
                self._net_position += fill_qty
                if self._net_position > 0:
                    self._avg_entry_price = (old_cost + fill_qty * fill_px) / self._net_position
            else:
                # Covering short — realize PnL
                cover_qty = min(fill_qty, abs(self._net_position))
                pnl = cover_qty * (self._avg_entry_price - fill_px)
                pnl -= cover_qty * fill_px * self.config.maker_fee_bps * BPS  # Fee on this leg
                self._realized_pnl += pnl
                self._net_position += fill_qty
                if self._net_position > 0:
                    self._avg_entry_price = fill_px

            self._buy_fills += 1

        else:  # SELL
            if self._net_position <= 0:
                # Adding to short or going short from flat
                old_cost = abs(self._net_position) * self._avg_entry_price
                self._net_position -= fill_qty
                if self._net_position < 0:
                    self._avg_entry_price = (old_cost + fill_qty * fill_px) / abs(self._net_position)
            else:
                # Closing long — realize PnL
                close_qty = min(fill_qty, self._net_position)
                pnl = close_qty * (fill_px - self._avg_entry_price)
                pnl -= close_qty * fill_px * self.config.maker_fee_bps * BPS
                self._realized_pnl += pnl
                self._net_position -= fill_qty
                if self._net_position < 0:
                    self._avg_entry_price = fill_px

            self._sell_fills += 1

        # Track position entry time (reset when flat)
        if abs(self._net_position) < 1e-10:
            self._net_position = 0.0
            self._avg_entry_price = 0.0
            self._position_entry_time = 0.0
            self._last_buy_fill_price = 0.0   # Reset spread guard when flat
            self._last_sell_fill_price = 0.0   # (stale fills would block next cycle)
            self._flatten_in_progress = False  # Position is flat, clear guard
            self._flatten_order_id = None
            # Exit close mode — we're flat!
            if self._close_mode:
                elapsed = time.time() - self._close_mode_since if self._close_mode_since > 0 else 0
                self.log.info(
                    f"🔓 CLOSE MODE → FLAT via maker in {elapsed:.0f}s "
                    f"(reason={self._close_mode_reason}) ✅ Saved taker fees!",
                    LogColor.GREEN,
                )
                self._close_mode = False
                self._close_mode_since = 0.0
                # Cancel ALL orders and pause both sides to prevent instant
                # re-accumulation from resting orders filling during the
                # next refresh cycle
                self._cancel_all_grid_orders()
                cooldown = 10.0
                self._buy_cooldown_until = time.time() + cooldown
                self._sell_cooldown_until = time.time() + cooldown
        elif self._position_entry_time == 0:
            self._position_entry_time = time.time()

        # CLOSE MODE OVERSHOOT GUARD — if a fill during close mode causes
        # the position to flip sign (e.g. covering short overshoots to long),
        # immediately cancel all orders on the now-adding side to prevent
        # the overshoot from growing larger.
        if self._close_mode and abs(self._net_position) > 0.0001:
            if self._net_position > 0:
                # We're now long — cancel all buy orders (they'd add to long)
                if self._active_buy_orders:
                    for key, (oid, px, qty) in list(self._active_buy_orders.items()):
                        order = self.cache.order(oid)
                        if order is not None and order.is_open:
                            self.cancel_order(order)
                            self._orders_canceled += 1
                    self._active_buy_orders.clear()
            else:
                # We're now short — cancel all sell orders (they'd add to short)
                if self._active_sell_orders:
                    for key, (oid, px, qty) in list(self._active_sell_orders.items()):
                        order = self.cache.order(oid)
                        if order is not None and order.is_open:
                            self.cancel_order(order)
                            self._orders_canceled += 1
                    self._active_sell_orders.clear()

        # If this was a flatten order fill, clear the guard even if not fully flat
        if (self._flatten_order_id is not None
                and hasattr(event, 'client_order_id')
                and event.client_order_id == self._flatten_order_id):
            self.log.info(
                f"✅ Flatten order filled | remaining pos={self._net_position:+.6f}",
                LogColor.GREEN,
            )
            self._flatten_in_progress = False
            self._flatten_order_id = None

        self._total_fills += 1
        self._total_volume_usd += fill_usd

        # Remove filled order from grid tracking so next refresh places a fresh one
        if hasattr(event, 'client_order_id'):
            self._remove_from_grid_tracking(event.client_order_id)

        # Log fill
        side_emoji = "🟢" if event.order_side == OrderSide.BUY else "🔴"
        self.log.info(
            f"{side_emoji} FILL: {event.order_side.name} {fill_qty:.6f} @ {fill_px:.2f} "
            f"(${fill_usd:.0f}) | pos={self._net_position:+.6f} | "
            f"pnl=${self._realized_pnl:+.2f}",
            LogColor.GREEN if event.order_side == OrderSide.BUY else LogColor.RED,
        )

    def _remove_from_grid_tracking(self, client_order_id) -> None:
        """Remove an order from active grid tracking by client_order_id."""
        for key, val in list(self._active_buy_orders.items()):
            if val[0] == client_order_id:
                del self._active_buy_orders[key]
                return
        for key, val in list(self._active_sell_orders.items()):
            if val[0] == client_order_id:
                del self._active_sell_orders[key]
                return

    def on_order_rejected(self, event) -> None:
        """Handle order rejection — remove from tracking, detect rate limits."""
        if hasattr(event, 'client_order_id'):
            self._remove_from_grid_tracking(event.client_order_id)

        # Detect rate limit rejections
        reason = str(getattr(event, 'reason', ''))
        if 'Too many cumulative requests' in reason or 'rate limit' in reason.lower():
            backoff_until = time.time() + self.config.rate_limit_backoff_secs
            if backoff_until > self._rate_limited_until:
                self._rate_limited_until = backoff_until
                self.log.error(
                    f"🚫 RATE LIMITED — backing off {self.config.rate_limit_backoff_secs}s. "
                    f"Canceling grid to stop request bleeding.",
                )
                self._cancel_all_grid_orders()

        if (self._flatten_order_id is not None
                and hasattr(event, 'client_order_id')
                and event.client_order_id == self._flatten_order_id):
            self.log.warning(
                f"⚠️ Flatten order REJECTED — will retry on next cycle",
                LogColor.YELLOW,
            )
            self._flatten_in_progress = False
            self._flatten_order_id = None

    def on_order_canceled(self, event) -> None:
        """Handle order cancelation — remove from grid tracking."""
        if hasattr(event, 'client_order_id'):
            self._remove_from_grid_tracking(event.client_order_id)

    def on_order_expired(self, event) -> None:
        """Handle IOC expiry — clear flatten guard so it can retry."""
        if hasattr(event, 'client_order_id'):
            self._remove_from_grid_tracking(event.client_order_id)

        if (self._flatten_order_id is not None
                and hasattr(event, 'client_order_id')
                and event.client_order_id == self._flatten_order_id):
            self.log.warning(
                f"⚠️ Flatten IOC expired (no fill) — will retry with wider slippage",
                LogColor.YELLOW,
            )
            self._flatten_in_progress = False
            self._flatten_order_id = None

    # -------------------------------------------------------------------------
    # SAFETY
    # -------------------------------------------------------------------------

    def _check_safety(self, now: float) -> None:
        """Run all safety checks: kill switch, inventory age, margin, liquidation, runaway."""
        self._update_unrealized_pnl()
        total_pnl = self._realized_pnl + self._unrealized_pnl

        # 1. Kill switch — hard PnL limit
        if total_pnl < self.config.max_loss_usd:
            self.log.error(
                f"💀 KILL SWITCH: PnL ${total_pnl:.2f} < ${self.config.max_loss_usd}",
            )
            self._emergency_flatten("KILL_SWITCH")
            self._killed = True
            return

        # 2. Profit-take trigger — enter close mode when position is green
        if (abs(self._net_position) > 0.0001
                and not self._close_mode
                and not self._flatten_in_progress):
            pos_usd = abs(self._net_position) * self._mid_price if self._mid_price > 0 else 0
            take_usd = self.config.take_profit_unrealized_usd
            take_pct = self.config.take_profit_pct
            # Check unrealized P&L thresholds
            if take_usd > 0 and self._unrealized_pnl >= take_usd:
                self._enter_close_mode("TAKE_PROFIT", now)
            elif take_pct > 0 and pos_usd > 0 and (self._unrealized_pnl / pos_usd * 100) >= take_pct:
                self._enter_close_mode("TAKE_PROFIT_PCT", now)

        # 3. Inventory age check — enter close mode (NOT taker dump)
        if (self._net_position != 0.0
                and self._position_entry_time > 0
                and not self._flatten_in_progress
                and not self._close_mode
                and now - self._position_entry_time > self.config.max_inventory_age_secs):
            self.log.warning(
                f"⏰ INVENTORY AGE: {now - self._position_entry_time:.0f}s > "
                f"{self.config.max_inventory_age_secs}s",
                LogColor.YELLOW,
            )
            if self.config.maker_close_enabled:
                self._enter_close_mode("INVENTORY_AGE", now)
            else:
                self._emergency_flatten("INVENTORY_AGE")

        # 4. Close mode timeout — either keep trying maker or (if enabled) taker
        if (self._close_mode
                and now - self._close_mode_since > self.config.close_mode_max_wait_secs):
            if self.config.close_mode_no_taker_fallback:
                # STAY in close mode — never taker. Just log and keep going.
                # The maker close grid will keep posting tight levels.
                # Log every 60s to show we're still trying.
                elapsed = now - self._close_mode_since
                if int(elapsed) % 60 < 4:  # Log ~every 60s
                    self.log.warning(
                        f"⏳ CLOSE MODE: {elapsed:.0f}s in maker-close, still working | "
                        f"pos={self._net_position:+.4f} upnl=${self._unrealized_pnl:+.2f}",
                        LogColor.YELLOW,
                    )
            else:
                self.log.warning(
                    f"⏰ CLOSE MODE TIMEOUT: {now - self._close_mode_since:.0f}s in close mode, "
                    f"maker exit failed — using taker as last resort",
                    LogColor.YELLOW,
                )
                self._close_mode = False
                self._emergency_flatten("CLOSE_MODE_TIMEOUT")
                self._buy_cooldown_until = now + 15.0
                self._sell_cooldown_until = now + 15.0

        # 5. Liquidation distance check
        if (self._liquidation_price > 0 and self._mid_price > 0
                and abs(self._net_position) > 0.0001):
            if self._net_position > 0:
                liq_dist_pct = (self._mid_price - self._liquidation_price) / self._mid_price * 100
            else:
                liq_dist_pct = (self._liquidation_price - self._mid_price) / self._mid_price * 100

            if liq_dist_pct < self.config.liquidation_panic_pct:
                self.log.error(
                    f"💀 LIQUIDATION PANIC: price {self._mid_price:.4f} is {liq_dist_pct:.1f}% "
                    f"from liq {self._liquidation_price:.4f} — EMERGENCY FLATTEN",
                )
                self._emergency_flatten("LIQUIDATION_PANIC")
                self._killed = True
                return
            elif liq_dist_pct < self.config.liquidation_warn_pct:
                self.log.warning(
                    f"⚠️ LIQUIDATION WARN: price {self._mid_price:.4f} is {liq_dist_pct:.1f}% "
                    f"from liq {self._liquidation_price:.4f}",
                    LogColor.YELLOW,
                )

        # 6. Margin check — pause new entries when margin is low
        if self._available_margin_pct < self.config.min_margin_pct:
            if not self._margin_paused:
                self.log.warning(
                    f"⚠️ MARGIN LOW: {self._available_margin_pct:.1f}% < {self.config.min_margin_pct}% "
                    f"— pausing new entries (close-only mode)",
                    LogColor.YELLOW,
                )
                self._margin_paused = True
        elif self._margin_paused and self._available_margin_pct > self.config.min_margin_pct * 1.5:
            self.log.info("✅ Margin recovered — resuming full grid", LogColor.GREEN)
            self._margin_paused = False

        # 7. Runaway fill detection — too many one-sided fills in window
        self._check_runaway(now)

    def _enter_close_mode(self, reason: str, now: float) -> None:
        """Enter maker-only close mode.

        Instead of dumping the entire position via IOC taker (which costs
        4.5bps + slippage), we:
        1. Yank ALL orders on the adding side (no new inventory)
        2. Tighten closing-side levels to just inside the spread
        3. Wait for maker fills to close the position
        4. Only fall back to taker if close_mode_max_wait_secs expires

        This saves ~$0.05-0.20 per exit vs taker, which over hundreds
        of exits is the difference between profitable and bleeding.
        """
        if self._close_mode:
            return  # Already in close mode
        self._close_mode = True
        self._close_mode_since = now
        self._close_mode_reason = reason
        self.log.warning(
            f"🔒 CLOSE MODE [{reason}]: pos={self._net_position:+.4f} "
            f"upnl=${self._unrealized_pnl:+.2f} — yanking adding side, "
            f"tightening closing side (max wait={self.config.close_mode_max_wait_secs}s)",
            LogColor.YELLOW,
        )

        # IMMEDIATELY cancel all adding-side orders to prevent
        # further fills that would increase the position.
        # This eliminates the race condition where resting orders
        # fill between now and the next grid refresh.
        if self._net_position > 0:
            # Long — cancel all buy orders immediately
            for key, (oid, px, qty) in list(self._active_buy_orders.items()):
                order = self.cache.order(oid)
                if order is not None and order.is_open:
                    self.cancel_order(order)
                    self._orders_canceled += 1
            self._active_buy_orders.clear()
        elif self._net_position < 0:
            # Short — cancel all sell orders immediately
            for key, (oid, px, qty) in list(self._active_sell_orders.items()):
                order = self.cache.order(oid)
                if order is not None and order.is_open:
                    self.cancel_order(order)
                    self._orders_canceled += 1
            self._active_sell_orders.clear()

    def _check_runaway(self, now: float) -> None:
        """Detect one-sided fill runaway (adverse selection).

        If we get too many fills on one side within the window, it means
        the market is trending and we're accumulating inventory fast.
        Pause to avoid building a huge position during a trend.
        """
        window_start = now - self.config.runaway_window_secs

        # Count recent fills per side within window
        recent_buys = sum(1 for t in self._recent_buy_fills if t > window_start)
        recent_sells = sum(1 for t in self._recent_sell_fills if t > window_start)

        max_one_side = self.config.runaway_max_one_side
        if recent_buys >= max_one_side and recent_sells < max_one_side // 2:
            self._runaway_paused_until = now + self.config.runaway_pause_secs
            self.log.warning(
                f"🏃 RUNAWAY BUY: {recent_buys} buys vs {recent_sells} sells "
                f"in {self.config.runaway_window_secs}s — pausing {self.config.runaway_pause_secs}s",
                LogColor.YELLOW,
            )
            self._cancel_all_grid_orders()
        elif recent_sells >= max_one_side and recent_buys < max_one_side // 2:
            self._runaway_paused_until = now + self.config.runaway_pause_secs
            self.log.warning(
                f"🏃 RUNAWAY SELL: {recent_sells} sells vs {recent_buys} buys "
                f"in {self.config.runaway_window_secs}s — pausing {self.config.runaway_pause_secs}s",
                LogColor.YELLOW,
            )
            self._cancel_all_grid_orders()

    def _sync_margin_state(
        self,
        base_url: str,
        wallet: str,
        coin: str,
        positions: list,
    ) -> None:
        """Sync margin and liquidation data from exchange.

        Handles unified accounts where margin comes from spot USDC balance.
        """
        import requests

        try:
            # Get liquidation price from position data
            for p in positions:
                pos = p.get("position", {})
                if pos.get("coin") == coin:
                    liq_px = pos.get("liquidationPx")
                    if liq_px is not None:
                        self._liquidation_price = float(liq_px)

            # Get account value from perps clearinghouse
            r = requests.post(
                f"{base_url}/info",
                json={"type": "clearinghouseState", "user": wallet},
                timeout=5,
            )
            if r.status_code == 200:
                state = r.json()
                acct_val = float(state.get("marginSummary", {}).get("accountValue", 0))
                margin_used = float(state.get("marginSummary", {}).get("totalMarginUsed", 0))

                # For unified accounts, perps accountValue may be 0
                # Check spot balance as the true margin source
                if acct_val < 1.0:
                    r2 = requests.post(
                        f"{base_url}/info",
                        json={"type": "spotClearinghouseState", "user": wallet},
                        timeout=5,
                    )
                    if r2.status_code == 200:
                        spot = r2.json()
                        for b in spot.get("balances", []):
                            if b.get("coin") == "USDC":
                                acct_val = float(b.get("total", 0))
                                break

                if acct_val > 0:
                    self._available_margin_pct = (
                        (acct_val - margin_used) / acct_val * 100
                    )
                else:
                    self._available_margin_pct = 0.0

        except Exception as e:
            self.log.debug(f"Margin sync error: {e}")

    def _api_flatten_position(self) -> None:
        """Flatten position via direct Hyperliquid Python SDK.

        Used in on_stop() because NautilusTrader order pipeline may not
        process orders after stop is called. This bypasses the framework
        and talks directly to the exchange.
        """
        import os

        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
        except ImportError:
            self.log.error("Cannot flatten: hyperliquid SDK not installed")
            return

        try:
            pk = os.environ.get("HYPERLIQUID_MAINNET_PK") or os.environ.get("HYPERLIQUID_TESTNET_PK")
            wallet_addr = os.environ.get("HYPERLIQUID_WALLET")
            if not pk or not wallet_addr:
                self.log.error("Cannot flatten: missing HYPERLIQUID env vars")
                return

            base_url = self.config.hl_api_url
            wallet = Account.from_key(pk)
            exchange = Exchange(wallet, base_url, account_address=wallet_addr)
            info = Info(base_url, skip_ws=True)

            # Get current exchange position
            state = info.user_state(wallet_addr)
            coin = self.config.instrument_id.split("-")[0]
            for p in state.get("assetPositions", []):
                pos = p.get("position", {})
                if pos.get("coin") == coin:
                    sz = float(pos.get("szi", 0))
                    if abs(sz) < 0.0001:
                        self.log.info("Position already flat on exchange")
                        return

                    mid = float(info.all_mids().get(coin, 0))
                    if mid <= 0:
                        self.log.error("Cannot flatten: no mid price")
                        return

                    # IOC with 1% slippage to guarantee fill
                    is_buy = sz < 0  # Buy to close short, sell to close long
                    if is_buy:
                        px = round(mid * 1.01, 5)
                    else:
                        px = round(mid * 0.99, 5)

                    # Round qty to szDecimals
                    meta = info.meta()
                    sz_decimals = 0
                    for u in meta.get("universe", []):
                        if u["name"] == coin:
                            sz_decimals = u.get("szDecimals", 0)
                            break
                    qty = round(abs(sz), sz_decimals)

                    result = exchange.order(
                        coin, is_buy, qty, px,
                        {"limit": {"tif": "Ioc"}},
                        reduce_only=True,
                    )
                    self.log.warning(
                        f"🛑 API FLATTEN: {'BUY' if is_buy else 'SELL'} {qty} {coin} "
                        f"@ {px:.4f} → {result.get('status')}",
                        LogColor.YELLOW,
                    )
                    return

            self.log.info("No position to flatten on exchange")

        except Exception as e:
            self.log.error(f"API flatten failed: {e}")

    def _update_unrealized_pnl(self) -> None:
        """Calculate unrealized PnL at current mid price."""
        if self._net_position == 0.0 or self._mid_price <= 0:
            self._unrealized_pnl = 0.0
            return

        if self._net_position > 0:
            self._unrealized_pnl = self._net_position * (self._mid_price - self._avg_entry_price)
        else:
            self._unrealized_pnl = abs(self._net_position) * (self._avg_entry_price - self._mid_price)

    def _emergency_flatten(self, reason: str) -> None:
        """Cancel all orders and aggressively close position.

        Uses IOC limit order with 0.5% slippage instead of market order
        because Hyperliquid doesn't support native market orders.
        Sets _flatten_in_progress guard to prevent infinite loop.
        """
        if self._flatten_in_progress:
            return  # Already attempting to flatten — don't spam

        self._flatten_in_progress = True
        self.log.warning(f"🚨 EMERGENCY FLATTEN: {reason} | pos={self._net_position:+.6f}")
        self.cancel_all_orders(self._instrument_id)
        self._active_buy_orders.clear()
        self._active_sell_orders.clear()

        if abs(self._net_position) > 0 and self._mid_price > 0:
            side = OrderSide.SELL if self._net_position > 0 else OrderSide.BUY
            # Use aggressive IOC limit with 0.5% slippage
            if side == OrderSide.SELL:
                aggressive_px = self._mid_price * 0.995  # Sell 0.5% below mid
            else:
                aggressive_px = self._mid_price * 1.005  # Buy 0.5% above mid

            try:
                qty = self._instrument.make_qty(abs(self._net_position))
                aggressive_px = self._snap_price(aggressive_px)
                px = self._instrument.make_price(aggressive_px)
                order = self.order_factory.limit(
                    instrument_id=self._instrument_id,
                    order_side=side,
                    quantity=qty,
                    price=px,
                    time_in_force=TimeInForce.IOC,
                    post_only=False,
                    reduce_only=True,
                )
                self._flatten_order_id = order.client_order_id
                self.submit_order(order)
                self.log.warning(
                    f"   → IOC {side.name} {qty} @ {px} (0.5% slippage) to flatten"
                )
            except Exception as e:
                self.log.error(f"   → FLATTEN FAILED: {e}")
                self._flatten_in_progress = False
        else:
            # No position to flatten
            self._flatten_in_progress = False
