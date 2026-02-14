"""Lead-Lag Market Maker v009 SNIPER - Range Detection HFT.

PHILOSOPHY:
HFT MM thrives in RANGING markets. Crypto oscillates between price levels,
spending time in "price channels" before breaking out to new levels.

THE SNIPER APPROACH:
1. WAIT for a ranging period to be confirmed (Bollinger squeeze + neutral RSI)
2. ENTER immediately with tight spreads (22-30 bps)
3. HAMMER trades while in the range - high frequency, small profits
4. DETECT breakout before it happens (band expansion + RSI extreme + OFI surge)
5. EXIT cleanly - sell inventory before depreciation, or hold if appreciating
6. REPEAT at the next price level

KEY INSIGHT:
Don't fight trends. Don't hold depreciating inventory. Get in, hammer trades,
get out. The strategy is a "sniper" - precise entry, rapid execution, clean exit.

INDICATORS:
- Bollinger Bands: Range boundaries + squeeze detection
- RSI: Overbought/oversold extremes signal breakout
- ATR: Volatility expansion = breakout imminent
- OFI: Order flow imbalance predicts breakout direction
- Volume Profile: Identify support/resistance and value areas

VERSION: v009.1 - Added Volume Profile
"""

from __future__ import annotations

import gc
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Final

import numpy as np

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators.momentum import RelativeStrengthIndex
from nautilus_trader.indicators.volatility import AverageTrueRange, BollingerBands
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
BPS_MULTIPLIER: Final[float] = 10000.0
BPS_DIVISOR: Final[float] = 0.0001


# =============================================================================
# VOLUME PROFILE
# =============================================================================
@dataclass
class VolumeProfileResult:
    """Results from volume profile analysis."""
    poc_price: float          # Point of Control - highest volume price
    poc_volume: float         # Volume at POC
    value_area_high: float    # Upper bound of 70% volume area
    value_area_low: float     # Lower bound of 70% volume area
    total_volume: float       # Total volume in profile
    num_levels: int           # Number of price levels tracked
    hvn_prices: list[float]   # High Volume Node prices (support/resistance)
    lvn_prices: list[float]   # Low Volume Node prices (breakout zones)


class VolumeProfile:
    """
    Real-time Volume Profile for HFT.
    
    Tracks volume at each price level to identify:
    - POC (Point of Control): Price with highest volume
    - Value Area: Where 70% of volume occurs
    - HVN (High Volume Nodes): Support/resistance levels
    - LVN (Low Volume Nodes): Areas price moves through quickly
    
    Uses a rolling window to keep profile fresh.
    """
    
    __slots__ = (
        '_tick_size', '_num_bins', '_window_ticks',
        '_volume_bins', '_price_bins', '_min_price', '_max_price',
        '_tick_count', '_total_volume',
        '_poc_price', '_poc_volume',
        '_value_area_high', '_value_area_low',
        '_hvn_threshold', '_lvn_threshold',
        '_initialized',
    )
    
    def __init__(
        self,
        tick_size: float,
        num_bins: int = 100,
        window_ticks: int = 500,
        hvn_threshold: float = 1.5,  # HVN if volume > 150% of average
        lvn_threshold: float = 0.5,  # LVN if volume < 50% of average
    ) -> None:
        self._tick_size = tick_size
        self._num_bins = num_bins
        self._window_ticks = window_ticks
        self._hvn_threshold = hvn_threshold
        self._lvn_threshold = lvn_threshold
        
        # Volume tracking - use deque for rolling window
        self._volume_bins: np.ndarray = np.zeros(num_bins, dtype=np.float64)
        self._price_bins: np.ndarray = np.zeros(num_bins, dtype=np.float64)
        self._min_price: float = 0.0
        self._max_price: float = 0.0
        
        self._tick_count: int = 0
        self._total_volume: float = 0.0
        
        # Cached results
        self._poc_price: float = 0.0
        self._poc_volume: float = 0.0
        self._value_area_high: float = 0.0
        self._value_area_low: float = 0.0
        
        self._initialized: bool = False
    
    @property
    def initialized(self) -> bool:
        return self._initialized
    
    @property
    def poc_price(self) -> float:
        return self._poc_price
    
    @property
    def value_area_high(self) -> float:
        return self._value_area_high
    
    @property
    def value_area_low(self) -> float:
        return self._value_area_low
    
    def update(self, price: float, volume: float = 1.0) -> None:
        """Update volume profile with new price/volume tick."""
        self._tick_count += 1
        
        # Initialize price range on first tick
        if self._min_price == 0.0:
            self._min_price = price * 0.99  # 1% below
            self._max_price = price * 1.01  # 1% above
            self._update_price_bins()
        
        # Expand range if needed
        if price < self._min_price:
            self._min_price = price * 0.995
            self._update_price_bins()
        elif price > self._max_price:
            self._max_price = price * 1.005
            self._update_price_bins()
        
        # Find bin for this price
        bin_idx = self._price_to_bin(price)
        if 0 <= bin_idx < self._num_bins:
            self._volume_bins[bin_idx] += volume
            self._total_volume += volume
        
        # Apply decay for rolling window effect
        if self._tick_count % 50 == 0:  # Decay every 50 ticks
            decay = 0.98
            self._volume_bins *= decay
            self._total_volume *= decay
        
        # Update analysis periodically
        if self._tick_count % 20 == 0:  # Recalculate every 20 ticks
            self._analyze()
        
        if self._tick_count >= 100:
            self._initialized = True
    
    def _update_price_bins(self) -> None:
        """Update price bin boundaries."""
        step = (self._max_price - self._min_price) / self._num_bins
        for i in range(self._num_bins):
            self._price_bins[i] = self._min_price + (i + 0.5) * step
    
    def _price_to_bin(self, price: float) -> int:
        """Convert price to bin index."""
        if self._max_price == self._min_price:
            return self._num_bins // 2
        ratio = (price - self._min_price) / (self._max_price - self._min_price)
        return int(ratio * (self._num_bins - 1))
    
    def _analyze(self) -> None:
        """Analyze volume distribution to find POC and Value Area."""
        if self._total_volume <= 0:
            return
        
        # Find POC (highest volume bin)
        poc_idx = int(np.argmax(self._volume_bins))
        self._poc_price = self._price_bins[poc_idx]
        self._poc_volume = self._volume_bins[poc_idx]
        
        # Calculate Value Area (70% of volume centered on POC)
        target_volume = self._total_volume * 0.70
        accumulated = self._poc_volume
        
        low_idx = poc_idx
        high_idx = poc_idx
        
        while accumulated < target_volume and (low_idx > 0 or high_idx < self._num_bins - 1):
            # Expand to side with more volume
            low_vol = self._volume_bins[low_idx - 1] if low_idx > 0 else 0
            high_vol = self._volume_bins[high_idx + 1] if high_idx < self._num_bins - 1 else 0
            
            if low_vol >= high_vol and low_idx > 0:
                low_idx -= 1
                accumulated += low_vol
            elif high_idx < self._num_bins - 1:
                high_idx += 1
                accumulated += high_vol
            else:
                break
        
        self._value_area_low = self._price_bins[low_idx]
        self._value_area_high = self._price_bins[high_idx]
    
    def get_hvn_lvn(self) -> tuple[list[float], list[float]]:
        """Get High Volume Nodes and Low Volume Nodes."""
        if self._total_volume <= 0:
            return [], []
        
        avg_volume = self._total_volume / self._num_bins
        hvn = []
        lvn = []
        
        for i in range(self._num_bins):
            if self._volume_bins[i] > avg_volume * self._hvn_threshold:
                hvn.append(self._price_bins[i])
            elif self._volume_bins[i] < avg_volume * self._lvn_threshold and self._volume_bins[i] > 0:
                lvn.append(self._price_bins[i])
        
        return hvn, lvn
    
    def get_result(self) -> VolumeProfileResult:
        """Get full volume profile analysis."""
        hvn, lvn = self.get_hvn_lvn()
        return VolumeProfileResult(
            poc_price=self._poc_price,
            poc_volume=self._poc_volume,
            value_area_high=self._value_area_high,
            value_area_low=self._value_area_low,
            total_volume=self._total_volume,
            num_levels=self._num_bins,
            hvn_prices=hvn,
            lvn_prices=lvn,
        )
    
    def is_at_hvn(self, price: float, tolerance_bps: float = 10.0) -> bool:
        """Check if price is near a High Volume Node (support/resistance)."""
        hvn, _ = self.get_hvn_lvn()
        tolerance = price * tolerance_bps * BPS_DIVISOR
        for hvn_price in hvn:
            if abs(price - hvn_price) <= tolerance:
                return True
        return False
    
    def is_at_lvn(self, price: float, tolerance_bps: float = 10.0) -> bool:
        """Check if price is in a Low Volume Node (breakout zone)."""
        _, lvn = self.get_hvn_lvn()
        tolerance = price * tolerance_bps * BPS_DIVISOR
        for lvn_price in lvn:
            if abs(price - lvn_price) <= tolerance:
                return True
        return False
    
    def is_inside_value_area(self, price: float) -> bool:
        """Check if price is within the Value Area."""
        return self._value_area_low <= price <= self._value_area_high
    
    def distance_to_poc_bps(self, price: float) -> float:
        """Get distance from price to POC in basis points."""
        if self._poc_price <= 0:
            return 0.0
        return (price - self._poc_price) / self._poc_price * BPS_MULTIPLIER
    
    def reset(self) -> None:
        """Reset volume profile for new session."""
        self._volume_bins.fill(0)
        self._tick_count = 0
        self._total_volume = 0.0
        self._min_price = 0.0
        self._max_price = 0.0
        self._poc_price = 0.0
        self._value_area_high = 0.0
        self._value_area_low = 0.0
        self._initialized = False


# =============================================================================
# STATE MACHINE
# =============================================================================
class SniperState(Enum):
    """Strategy state machine states."""
    WAITING = "WAITING"              # No position, waiting for range confirmation
    RANGING_ENTRY = "RANGING_ENTRY"  # Range detected, placing first entry
    RANGING_ACTIVE = "RANGING_ACTIVE"  # In range, hammering trades
    BREAKOUT_PENDING = "BREAKOUT_PENDING"  # Breakout signals detected, preparing exit
    EXITING = "EXITING"              # Actively clearing inventory
    COOLDOWN = "COOLDOWN"            # Post-exit cooldown before next cycle


# =============================================================================
# CONFIG
# =============================================================================
class LeadLagMMv9SniperConfig(StrategyConfig, frozen=True):
    """Configuration for v009 Sniper strategy."""
    
    # Instrument IDs
    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId
    
    # Order book settings
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 10
    
    # === SPREAD SETTINGS ===
    # Target spread during ranging (tight for high fill rate)
    ranging_spread_bps: float = 25.0  # 25 bps = ~5 bps profit after 20 bps fees
    # Spread during breakout pending (wider for safety)
    breakout_spread_bps: float = 50.0
    # Minimum profitable spread (must cover roundtrip fees)
    min_spread_bps: float = 22.0  # 20 bps fees + 2 bps buffer
    
    # === ORDER SETTINGS ===
    order_qty: float = 10.0
    max_position_qty: float = 50.0  # Smaller max position for quick exits
    min_order_qty: float = 0.001
    min_order_value_usd: float = 5.50  # Bybit minimum
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = True
    
    # === INDICATOR SETTINGS ===
    # Bollinger Bands
    bb_period: int = 20  # 20 ticks for fast response
    bb_std_dev: float = 2.0
    bb_squeeze_threshold: float = 0.7  # Width < 70% of EMA = squeeze
    bb_breakout_threshold: float = 0.995  # Price within 0.5% of band = near edge
    
    # RSI
    rsi_period: int = 14
    rsi_neutral_low: float = 40.0
    rsi_neutral_high: float = 60.0
    rsi_extreme_low: float = 30.0
    rsi_extreme_high: float = 70.0
    
    # ATR
    atr_period: int = 14
    atr_expansion_threshold: float = 1.3  # ATR > 130% of EMA = expansion
    
    # === VOLUME PROFILE SETTINGS ===
    volume_profile_enabled: bool = True
    volume_profile_bins: int = 100       # Number of price bins
    volume_profile_window: int = 500     # Rolling window in ticks
    volume_profile_hvn_threshold: float = 1.5  # HVN if vol > 150% avg
    volume_profile_lvn_threshold: float = 0.5  # LVN if vol < 50% avg
    volume_profile_poc_tolerance_bps: float = 15.0  # POC proximity tolerance
    
    # === RANGE DETECTION ===
    # Require multiple confirmations for range entry
    range_confirm_ticks: int = 10  # Need 10 consecutive "ranging" ticks
    range_min_width_bps: float = 30.0  # Range must be at least 30 bps wide
    range_max_width_bps: float = 200.0  # Range can't be too wide (spread target)
    
    # === BREAKOUT DETECTION ===
    breakout_confirm_ticks: int = 3  # 3 consecutive breakout signals
    breakout_ofi_threshold: float = 0.4  # Strong directional OFI
    breakout_volume_spike: float = 1.5  # Volume > 150% of average
    
    # === EXIT SETTINGS ===
    exit_cooldown_secs: int = 30  # Cooldown after exit before re-entry
    exit_aggressive_threshold_bps: float = -20.0  # Use taker if losing > 20 bps
    hold_if_appreciating: bool = True  # Hold long if price going up at breakout
    max_hold_secs: int = 300  # Force exit after 5 min regardless
    
    # === INVENTORY MANAGEMENT ===
    inventory_skew_enabled: bool = True
    inventory_skew_multiplier: float = 0.3  # Skew intensity
    
    # === ORDER MANAGEMENT ===
    min_quote_lifetime_ms: int = 50
    quote_refresh_interval_ms: int = 100
    
    # === LOGGING ===
    log_state_changes: bool = True
    log_indicator_values: bool = False
    log_trade_signals: bool = True


# =============================================================================
# STRATEGY
# =============================================================================
class LeadLagMMv9Sniper(Strategy):
    """
    HFT Sniper Market Maker - Range detection with aggressive entry/exit.
    
    Uses Bollinger Bands, RSI, and ATR to detect ranging periods and
    breakout signals. Operates with tight spreads during ranges and
    exits cleanly before trends.
    """
    
    __slots__ = (
        # Instruments
        'follower_instrument', 'leader_instrument',
        'leader_book', 'follower_book',
        # Indicators
        '_bb', '_rsi', '_atr',
        '_bb_width_ema', '_atr_ema',
        # Volume Profile
        '_volume_profile',
        # State machine
        '_state', '_state_entry_ns', '_state_ticks',
        '_range_confirm_count', '_breakout_confirm_count',
        # Range tracking
        '_range_high', '_range_low', '_range_mid',
        '_range_entry_price', '_range_entry_side',
        # Price tracking
        '_leader_mid', '_follower_mid',
        '_price_history', '_price_idx', '_price_count',
        # OFI
        '_current_ofi', '_prev_bid_qty', '_prev_ask_qty',
        # Position
        '_net_position', '_entry_price', '_entry_ts_ns',
        # Orders
        '_bid_order', '_ask_order',
        '_bid_order_ts_ns', '_ask_order_ts_ns',
        # Balance
        '_available_base', '_available_quote',
        '_base_currency', '_quote_currency',
        # Precision
        '_price_precision', '_size_precision', '_tick_size',
        # Timing
        '_now_ns', '_last_quote_ts_ns', '_cooldown_until_ns',
        # Stats
        '_total_fills', '_range_cycles', '_profitable_cycles',
        '_session_pnl',
        # Client
        'client_id',
        '_initialized',
    )
    
    def __init__(self, config: LeadLagMMv9SniperConfig) -> None:
        super().__init__(config)
        
        # Client ID
        self.client_id = ClientId(f"SNIPER-{config.follower_instrument_id.symbol}")
        
        # Instruments
        self.follower_instrument: Instrument | None = None
        self.leader_instrument: Instrument | None = None
        self.leader_book: OrderBook | None = None
        self.follower_book: OrderBook | None = None
        
        # Initialize indicators
        self._bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self._rsi = RelativeStrengthIndex(config.rsi_period)
        self._atr = AverageTrueRange(config.atr_period)
        self._bb_width_ema: float = 0.0
        self._atr_ema: float = 0.0
        
        # Volume Profile (initialized with tick_size in on_start)
        self._volume_profile: VolumeProfile | None = None
        
        # State machine
        self._state: SniperState = SniperState.WAITING
        self._state_entry_ns: int = 0
        self._state_ticks: int = 0
        self._range_confirm_count: int = 0
        self._breakout_confirm_count: int = 0
        
        # Range tracking
        self._range_high: float = 0.0
        self._range_low: float = 0.0
        self._range_mid: float = 0.0
        self._range_entry_price: float = 0.0
        self._range_entry_side: OrderSide | None = None
        
        # Price tracking
        self._leader_mid: float = 0.0
        self._follower_mid: float = 0.0
        window = max(config.bb_period, config.rsi_period, config.atr_period) + 10
        self._price_history: np.ndarray = np.zeros(window, dtype=np.float64)
        self._price_idx: int = 0
        self._price_count: int = 0
        
        # OFI tracking
        self._current_ofi: float = 0.0
        self._prev_bid_qty: float = 0.0
        self._prev_ask_qty: float = 0.0
        
        # Position tracking
        self._net_position: float = 0.0
        self._entry_price: float = 0.0
        self._entry_ts_ns: int = 0
        
        # Orders
        self._bid_order: Order | None = None
        self._ask_order: Order | None = None
        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0
        
        # Balance tracking
        self._available_base: float = 0.0
        self._available_quote: float = 0.0
        symbol = str(config.follower_instrument_id.symbol)
        if "-SPOT" in symbol:
            symbol = symbol.replace("-SPOT", "")
        if symbol.endswith("USDT"):
            self._base_currency = symbol[:-4]
            self._quote_currency = "USDT"
        else:
            self._base_currency = symbol[:3]
            self._quote_currency = "USDT"
        
        # Precision (set in on_start)
        self._price_precision: int = 8
        self._size_precision: int = 8
        self._tick_size: float = 0.0001
        
        # Timing
        self._now_ns = lambda: 0
        self._last_quote_ts_ns: int = 0
        self._cooldown_until_ns: int = 0
        
        # Stats
        self._total_fills: int = 0
        self._range_cycles: int = 0
        self._profitable_cycles: int = 0
        self._session_pnl: float = 0.0
        
        self._initialized: bool = False
    
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
        
        # Cache precision
        self._tick_size = float(self.follower_instrument.price_increment)
        self._price_precision = self.follower_instrument.price_precision
        self._size_precision = self.follower_instrument.size_precision
        
        # Initialize Volume Profile now that we have tick_size
        if self.config.volume_profile_enabled:
            self._volume_profile = VolumeProfile(
                tick_size=self._tick_size,
                num_bins=self.config.volume_profile_bins,
                window_ticks=self.config.volume_profile_window,
                hvn_threshold=self.config.volume_profile_hvn_threshold,
                lvn_threshold=self.config.volume_profile_lvn_threshold,
            )
        
        # Subscribe to orderbook
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
        
        # Initialize balance
        self._initialize_balance()
        
        # Delayed activation
        from datetime import timedelta
        self.clock.set_timer(
            name="delayed_activation",
            interval=timedelta(seconds=5),
            callback=self._activate,
        )
        
        self.log.info(
            f"🎯 SNIPER v009.1 Starting | "
            f"Ranging spread: {self.config.ranging_spread_bps} bps | "
            f"BB period: {self.config.bb_period} | "
            f"Volume Profile: {'ON' if self.config.volume_profile_enabled else 'OFF'}",
            LogColor.CYAN,
        )
    
    def _activate(self, event=None) -> None:
        try:
            self.clock.cancel_timer("delayed_activation")
        except Exception:
            pass
        self._initialized = True
        self._state_entry_ns = self._now_ns()
        self.log.info("🎯 SNIPER ACTIVATED - Waiting for range", LogColor.GREEN)
    
    def _initialize_balance(self) -> None:
        """Initialize balance tracking from account."""
        try:
            accounts = self.cache.accounts()
            if accounts:
                account = accounts[0]
                balances = account.balances()
                
                base_currency = Currency.from_str(self._base_currency)
                quote_currency = Currency.from_str(self._quote_currency)
                
                base_balance = balances.get(base_currency)
                quote_balance = balances.get(quote_currency)
                
                if base_balance:
                    # Handle both Money and AccountBalance objects
                    if hasattr(base_balance, 'total'):
                        self._available_base = float(base_balance.total.as_decimal())
                    elif hasattr(base_balance, 'as_decimal'):
                        self._available_base = float(base_balance.as_decimal())
                    else:
                        self._available_base = float(base_balance)
                        
                if quote_balance:
                    if hasattr(quote_balance, 'total'):
                        self._available_quote = float(quote_balance.total.as_decimal())
                    elif hasattr(quote_balance, 'as_decimal'):
                        self._available_quote = float(quote_balance.as_decimal())
                    else:
                        self._available_quote = float(quote_balance)
                    
                self.log.info(
                    f"💰 Balance: {self._available_base:.4f} {self._base_currency} | "
                    f"{self._available_quote:.2f} {self._quote_currency}",
                    LogColor.BLUE,
                )
        except Exception as e:
            self.log.warning(f"Balance init error: {e}")
    
    def on_stop(self) -> None:
        self._initialized = False
        gc.enable()
        
        if self.follower_instrument is not None:
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
        
        win_rate = (self._profitable_cycles / self._range_cycles * 100) if self._range_cycles > 0 else 0
        self.log.info(
            f"📊 SNIPER SESSION SUMMARY | "
            f"Fills: {self._total_fills} | "
            f"Cycles: {self._range_cycles} (Win: {win_rate:.1f}%) | "
            f"P&L: ${self._session_pnl:.2f}",
            LogColor.CYAN,
        )
    
    # =========================================================================
    # DATA HANDLERS
    # =========================================================================
    
    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if not self._initialized:
            return
        
        # Update book reference
        book = self.cache.order_book(deltas.instrument_id)
        if book is None:
            return
        
        # Check if this is leader or follower by comparing venue
        is_leader = "BINANCE" in str(deltas.instrument_id.venue)
        
        if is_leader:
            self.leader_book = book
            bid = book.best_bid_price()
            ask = book.best_ask_price()
            if bid and ask:
                self._leader_mid = (float(bid) + float(ask)) / 2.0
        else:
            self.follower_book = book
            bid = book.best_bid_price()
            ask = book.best_ask_price()
            if bid and ask:
                self._follower_mid = (float(bid) + float(ask)) / 2.0
        
        # Only process on follower book updates (where we trade)
        if not is_leader and self._is_trading_ready():
            self._on_tick()
    
    def _is_trading_ready(self) -> bool:
        accounts = self.cache.accounts()
        if not accounts:
            return False
        if self.leader_book is None or self.follower_book is None:
            return False
        if self._leader_mid <= 0.0 or self._follower_mid <= 0.0:
            return False
        return True
    
    # =========================================================================
    # MAIN TICK HANDLER
    # =========================================================================
    
    def _on_tick(self) -> None:
        """Main tick processing - update indicators and run state machine."""
        now_ns = self._now_ns()
        
        # Update price history
        self._update_price_history(self._follower_mid)
        
        # Update indicators
        self._update_indicators()
        
        # Update OFI
        self._update_ofi()
        
        # Run state machine
        self._run_state_machine(now_ns)
    
    def _update_price_history(self, price: float) -> None:
        """Add price to rolling history."""
        self._price_history[self._price_idx] = price
        self._price_idx = (self._price_idx + 1) % len(self._price_history)
        if self._price_count < len(self._price_history):
            self._price_count += 1
    
    def _update_indicators(self) -> None:
        """Update all technical indicators."""
        if self._price_count < 2:
            return
        
        mid = self._follower_mid
        
        # Build OHLC from recent price history for meaningful indicator values
        # Use the actual high/low from recent ticks, not synthetic values
        lookback = min(self._price_count, 10)  # Use last 10 ticks for H/L
        recent_prices = []
        for i in range(lookback):
            idx = (self._price_idx - 1 - i) % len(self._price_history)
            recent_prices.append(self._price_history[idx])
        
        if recent_prices:
            high = max(recent_prices)
            low = min(recent_prices)
        else:
            high = mid + self._tick_size * 5
            low = mid - self._tick_size * 5
        
        # Ensure minimum spread for meaningful BB calculation
        min_spread = mid * 0.0005  # 5 bps minimum
        if (high - low) < min_spread:
            spread_add = (min_spread - (high - low)) / 2
            high += spread_add
            low -= spread_add
        
        # Update Bollinger Bands
        self._bb.update_raw(high, low, mid)
        
        # Update RSI
        self._rsi.update_raw(mid)
        
        # Update ATR
        if self._price_count >= 2:
            prev_idx = (self._price_idx - 2) % len(self._price_history)
            prev_close = self._price_history[prev_idx]
            self._atr.update_raw(high, low, prev_close)
        
        # Update EMAs for squeeze/expansion detection
        if self._bb.initialized:
            width = self._bb.upper - self._bb.lower
            alpha = 0.1
            if self._bb_width_ema == 0:
                self._bb_width_ema = width
            else:
                self._bb_width_ema = alpha * width + (1 - alpha) * self._bb_width_ema
        
        if self._atr.initialized:
            alpha = 0.1
            if self._atr_ema == 0:
                self._atr_ema = self._atr.value
            else:
                self._atr_ema = alpha * self._atr.value + (1 - alpha) * self._atr_ema
        
        # Update Volume Profile
        if self._volume_profile is not None:
            self._volume_profile.update(mid, volume=1.0)  # Use tick count as proxy for volume
        
        # Log indicator values periodically
        if self.config.log_indicator_values and self._state_ticks % 100 == 0:
            if self._bb.initialized:
                vp_info = ""
                if self._volume_profile is not None and self._volume_profile.initialized:
                    poc_dist = self._volume_profile.distance_to_poc_bps(mid)
                    in_va = self._volume_profile.is_inside_value_area(mid)
                    vp_info = f" | VP POC: {poc_dist:+.1f}bps VA: {'IN' if in_va else 'OUT'}"
                self.log.info(
                    f"📈 Indicators | BB: [{self._bb.lower:.6f}, {self._bb.upper:.6f}] "
                    f"Width: {(self._bb.upper - self._bb.lower):.6f} | "
                    f"RSI: {self._rsi.value:.1f} | ATR: {self._atr.value:.6f}{vp_info}",
                    LogColor.BLUE,
                )
    
    def _update_ofi(self) -> None:
        """Update Order Flow Imbalance from leader book."""
        if self.leader_book is None:
            return
        
        bid_qty = 0.0
        ask_qty = 0.0
        
        for level in self.leader_book.bids()[:5]:
            # Handle both property and method access for size
            size = level.size() if callable(level.size) else level.size
            bid_qty += float(size)
        for level in self.leader_book.asks()[:5]:
            size = level.size() if callable(level.size) else level.size
            ask_qty += float(size)
        
        total = bid_qty + ask_qty
        if total > 0:
            # OFI: positive = buying pressure, negative = selling pressure
            self._current_ofi = (bid_qty - ask_qty) / total
        
        self._prev_bid_qty = bid_qty
        self._prev_ask_qty = ask_qty
    
    # =========================================================================
    # STATE MACHINE
    # =========================================================================
    
    def _run_state_machine(self, now_ns: int) -> None:
        """Execute state machine logic."""
        self._state_ticks += 1
        
        if self._state == SniperState.WAITING:
            self._state_waiting(now_ns)
        elif self._state == SniperState.RANGING_ENTRY:
            self._state_ranging_entry(now_ns)
        elif self._state == SniperState.RANGING_ACTIVE:
            self._state_ranging_active(now_ns)
        elif self._state == SniperState.BREAKOUT_PENDING:
            self._state_breakout_pending(now_ns)
        elif self._state == SniperState.EXITING:
            self._state_exiting(now_ns)
        elif self._state == SniperState.COOLDOWN:
            self._state_cooldown(now_ns)
    
    def _transition_state(self, new_state: SniperState, now_ns: int, reason: str = "") -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._state_entry_ns = now_ns
        self._state_ticks = 0
        
        if self.config.log_state_changes:
            self.log.warning(
                f"🔄 STATE: {old_state.value} → {new_state.value} | {reason}",
                LogColor.MAGENTA,
            )
    
    # -------------------------------------------------------------------------
    # WAITING STATE
    # -------------------------------------------------------------------------
    
    def _state_waiting(self, now_ns: int) -> None:
        """Wait for range confirmation before entering."""
        # Check cooldown
        if now_ns < self._cooldown_until_ns:
            return
        
        # Need initialized indicators
        if not self._bb.initialized or not self._rsi.initialized:
            return
        
        # Check if conditions indicate ranging
        if self._is_range_starting():
            self._range_confirm_count += 1
            
            if self._range_confirm_count >= self.config.range_confirm_ticks:
                # Range confirmed! Record range boundaries
                self._range_high = self._bb.upper
                self._range_low = self._bb.lower
                self._range_mid = self._bb.middle
                
                # Build VP info for log
                vp_info = ""
                if self._volume_profile is not None and self._volume_profile.initialized:
                    poc = self._volume_profile.poc_price
                    vah = self._volume_profile.value_area_high
                    val = self._volume_profile.value_area_low
                    vp_info = f" | VP POC:{poc:.6f} VA:[{val:.6f},{vah:.6f}]"
                
                # SNIPER SPEED: Place orders IMMEDIATELY - don't wait for next tick!
                self._place_ranging_quotes(now_ns)
                self._range_cycles += 1
                
                self._transition_state(
                    SniperState.RANGING_ACTIVE,  # Skip RANGING_ENTRY - go straight to active
                    now_ns,
                    f"Range [{self._range_low:.6f}, {self._range_high:.6f}] - QUOTES PLACED{vp_info}",
                )
                self._range_confirm_count = 0
        else:
            self._range_confirm_count = max(0, self._range_confirm_count - 1)
    
    def _is_range_starting(self) -> bool:
        """Detect if a ranging period is starting.
        
        For HFT, we use simple criteria:
        1. BB initialized
        2. Price not at extreme edge of BB
        3. OFI not extremely strong
        
        We removed complex indicators that don't work well with tick data.
        """
        if not self._bb.initialized:
            return False
        
        # Basic width check
        width = self._bb.upper - self._bb.lower
        if width <= 0:
            return False
        
        # 1. Check range width in bps - must be tradeable
        width_bps = width / self._follower_mid * BPS_MULTIPLIER
        width_ok = self.config.range_min_width_bps <= width_bps <= self.config.range_max_width_bps
        if not width_ok:
            return False
        
        # 2. Price not at extreme edge (within 90% of range from center - relaxed)
        mid_distance = abs(self._follower_mid - self._bb.middle)
        half_width = width / 2
        near_edge = mid_distance > half_width * 0.9 if half_width > 0 else True
        if near_edge:
            return False
        
        # 3. OFI not extreme - relaxed to 0.8
        ofi_extreme = abs(self._current_ofi) > 0.8
        if ofi_extreme:
            return False
        
        # All basic conditions met
        return True
    
    # -------------------------------------------------------------------------
    # RANGING ENTRY STATE (Legacy - now skipped, orders placed immediately)
    # -------------------------------------------------------------------------
    
    def _state_ranging_entry(self, now_ns: int) -> None:
        """Legacy state - we now place orders immediately in WAITING.
        This should rarely be called, but handle it just in case."""
        # Place quotes and go to active
        self._place_ranging_quotes(now_ns)
        self._range_cycles += 1
        self._transition_state(
            SniperState.RANGING_ACTIVE,
            now_ns,
            f"Quotes placed (legacy path)",
        )
    
    # -------------------------------------------------------------------------
    # RANGING ACTIVE STATE
    # -------------------------------------------------------------------------
    
    def _state_ranging_active(self, now_ns: int) -> None:
        """Actively market making within the range."""
        # Check for breakout signals
        if self._is_breakout_imminent():
            self._breakout_confirm_count += 1
            
            if self._breakout_confirm_count >= self.config.breakout_confirm_ticks:
                self._transition_state(
                    SniperState.BREAKOUT_PENDING,
                    now_ns,
                    f"Breakout detected! OFI={self._current_ofi:.2f} RSI={self._rsi.value:.1f}",
                )
                self._breakout_confirm_count = 0
                return
        else:
            self._breakout_confirm_count = max(0, self._breakout_confirm_count - 1)
        
        # Check max hold time
        hold_secs = (now_ns - self._state_entry_ns) / 1_000_000_000
        if hold_secs > self.config.max_hold_secs:
            self._transition_state(SniperState.EXITING, now_ns, f"Max hold {hold_secs:.0f}s")
            return
        
        # Update quotes
        self._place_ranging_quotes(now_ns)
    
    def _place_ranging_quotes(self, now_ns: int) -> None:
        """Place tight spread quotes for ranging market."""
        # Calculate spread
        half_spread_bps = self.config.ranging_spread_bps / 2.0
        half_spread = self._follower_mid * half_spread_bps * BPS_DIVISOR
        
        # Calculate prices
        bid_price = self._follower_mid - half_spread
        ask_price = self._follower_mid + half_spread
        
        # Apply inventory skew
        if self.config.inventory_skew_enabled and self._net_position != 0:
            skew = self._calculate_inventory_skew()
            bid_price -= skew
            ask_price -= skew
        
        # Calculate quantities
        bid_qty, ask_qty = self._calculate_quantities()
        
        # Place/update orders
        if bid_qty > 0:
            self._place_or_update_order(OrderSide.BUY, bid_price, bid_qty, now_ns)
        if ask_qty > 0:
            self._place_or_update_order(OrderSide.SELL, ask_price, ask_qty, now_ns)
    
    def _calculate_inventory_skew(self) -> float:
        """Calculate price skew based on inventory."""
        if self.config.max_position_qty == 0:
            return 0.0
        
        inventory_pct = self._net_position / self.config.max_position_qty
        skew_bps = inventory_pct * self.config.inventory_skew_multiplier * self.config.ranging_spread_bps
        return self._follower_mid * skew_bps * BPS_DIVISOR
    
    def _calculate_quantities(self) -> tuple[float, float]:
        """Calculate bid/ask quantities respecting limits."""
        base_qty = self.config.order_qty
        
        # Limit based on position
        max_buy = self.config.max_position_qty - self._net_position
        max_sell = self.config.max_position_qty + self._net_position
        
        bid_qty = min(base_qty, max_buy)
        ask_qty = min(base_qty, max_sell)
        
        # Cap sell to available balance
        if self._net_position > 0:
            ask_qty = min(ask_qty, self._net_position * 0.98)
        
        # Check minimums
        if bid_qty < self.config.min_order_qty:
            bid_qty = 0
        if ask_qty < self.config.min_order_qty:
            ask_qty = 0
        
        # Check minimum value
        if bid_qty > 0 and bid_qty * self._follower_mid < self.config.min_order_value_usd:
            bid_qty = 0
        if ask_qty > 0 and ask_qty * self._follower_mid < self.config.min_order_value_usd:
            ask_qty = 0
        
        return bid_qty, ask_qty
    
    # -------------------------------------------------------------------------
    # BREAKOUT DETECTION
    # -------------------------------------------------------------------------
    
    def _is_breakout_imminent(self) -> bool:
        """Detect if price is about to break out of range.
        
        For ranging-only strategy, we need to be CONSERVATIVE about breakout detection.
        Only exit when there's strong evidence of a real breakout, not just noise.
        
        Key principle: If in doubt, STAY IN THE RANGE.
        """
        if not self._bb.initialized:
            return False
        
        signals = 0
        
        # 1. Price SIGNIFICANTLY outside original range - MOST IMPORTANT
        # This is the most reliable signal
        if self._range_high > 0 and self._range_low > 0:
            if self._follower_mid > self._range_high:
                pct_above = (self._follower_mid - self._range_high) / self._range_high
                if pct_above > 0.002:  # >0.2% above range (was 0.1%)
                    signals += 2
                elif pct_above > 0.001:  # >0.1% above range (was 0.05%)
                    signals += 1
            elif self._follower_mid < self._range_low:
                pct_below = (self._range_low - self._follower_mid) / self._range_low
                if pct_below > 0.002:  # >0.2% below range
                    signals += 2
                elif pct_below > 0.001:  # >0.1% below range
                    signals += 1
        
        # 2. VERY strong OFI only - normal fluctuation is NOT a breakout
        # OFI of 0.6-0.7 is normal noise. Only >0.85 indicates real imbalance.
        if abs(self._current_ofi) >= 0.85:  # Was 0.6 for "very strong"
            signals += 1
        if abs(self._current_ofi) >= 0.95:  # Extreme imbalance
            signals += 1
        
        # 3. ATR expanding SIGNIFICANTLY
        if self._atr.initialized and self._atr_ema > 0:
            atr_ratio = self._atr.value / self._atr_ema
            if atr_ratio >= self.config.atr_expansion_threshold * 1.5:  # 50% above threshold
                signals += 1
        
        # 4. Price near band edge AND moving away from center
        near_upper = self._follower_mid >= self._bb.upper * 0.998
        near_lower = self._follower_mid <= self._bb.lower * 1.002
        if near_upper or near_lower:
            signals += 1
        
        # 5. Volume Profile: Price at LVN or outside Value Area
        if self._volume_profile is not None and self._volume_profile.initialized:
            at_lvn = self._volume_profile.is_at_lvn(self._follower_mid, tolerance_bps=10.0)
            outside_va = not self._volume_profile.is_inside_value_area(self._follower_mid)
            if at_lvn and outside_va:
                signals += 1  # Only count if BOTH conditions met
        
        # Require 3+ signals for breakout
        return signals >= 3
    
    # -------------------------------------------------------------------------
    # BREAKOUT PENDING STATE
    # -------------------------------------------------------------------------
    
    def _state_breakout_pending(self, now_ns: int) -> None:
        """Breakout detected - prepare to exit."""
        # Determine breakout direction
        breakout_up = self._current_ofi > 0 or self._follower_mid > self._range_mid
        
        # Cancel buy orders (don't add to position)
        if self._bid_order and not self._bid_order.is_closed:
            self.cancel_order(self._bid_order)
            self._bid_order = None
        
        if self._net_position > 0:
            if breakout_up and self.config.hold_if_appreciating:
                # Price going up, hold long position
                if self.config.log_trade_signals:
                    self.log.warning(
                        f"📈 BREAKOUT UP: Holding {self._net_position:.4f} long",
                        LogColor.GREEN,
                    )
                # Widen ask to ride the trend
                ask_price = self._follower_mid * (1 + self.config.breakout_spread_bps * BPS_DIVISOR)
                self._place_or_update_order(OrderSide.SELL, ask_price, self._net_position * 0.98, now_ns)
            else:
                # Price going down or not holding - exit immediately
                self._transition_state(SniperState.EXITING, now_ns, "Breakout down - exiting")
                return
        elif self._net_position < 0:
            # Short position - shouldn't happen in MM but handle it
            self._transition_state(SniperState.EXITING, now_ns, "Exiting short")
            return
        else:
            # No position - go to cooldown
            self._transition_state(SniperState.COOLDOWN, now_ns, "No position at breakout")
            return
        
        # Check if position cleared or trend reversed
        if self._net_position <= 0.01 or not self._is_breakout_imminent():
            self._transition_state(SniperState.COOLDOWN, now_ns, "Position cleared")
    
    # -------------------------------------------------------------------------
    # EXITING STATE
    # -------------------------------------------------------------------------
    
    def _state_exiting(self, now_ns: int) -> None:
        """Actively exit position - handles both long and short."""
        if abs(self._net_position) < self.config.min_order_qty:
            # Position cleared
            self._transition_state(SniperState.COOLDOWN, now_ns, "Exit complete")
            return
        
        # Cancel all orders first
        if self._bid_order and not self._bid_order.is_closed:
            self.cancel_order(self._bid_order)
            self._bid_order = None
        if self._ask_order and not self._ask_order.is_closed:
            self.cancel_order(self._ask_order)
            self._ask_order = None
        
        if self._net_position > 0:
            # LONG position - need to SELL to exit
            if self._entry_price > 0:
                pnl_bps = (self._follower_mid - self._entry_price) / self._entry_price * BPS_MULTIPLIER
                if pnl_bps < self.config.exit_aggressive_threshold_bps:
                    self._place_aggressive_exit(now_ns)
                    return
            
            # Place tight ask to exit long
            exit_spread_bps = 5.0  # Tight for quick exit
            ask_price = self._follower_mid * (1 + exit_spread_bps * BPS_DIVISOR)
            exit_qty = min(self._net_position * 0.98, self._available_base * 0.9)
            
            if exit_qty >= self.config.min_order_qty and exit_qty * self._follower_mid >= self.config.min_order_value_usd:
                self._place_or_update_order(OrderSide.SELL, ask_price, exit_qty, now_ns)
                
        elif self._net_position < 0:
            # SHORT position - need to BUY to cover
            # For shorts, always use aggressive exit since we're in unexpected state
            short_qty = abs(self._net_position)
            
            # Aggressive IOC buy to cover short
            if self.follower_book:
                best_ask = self.follower_book.best_ask_price()
                if best_ask:
                    cover_price = float(best_ask) * 1.001  # 0.1% above ask
                else:
                    cover_price = self._follower_mid * 1.005
            else:
                cover_price = self._follower_mid * 1.005
            
            cover_price = round(cover_price / self._tick_size) * self._tick_size
            
            if short_qty >= self.config.min_order_qty and short_qty * self._follower_mid >= self.config.min_order_value_usd:
                price_obj = Price(cover_price, precision=self._price_precision)
                qty_obj = Quantity(short_qty * 0.98, precision=self._size_precision)
                
                order = self.order_factory.limit(
                    instrument_id=self.config.follower_instrument_id,
                    order_side=OrderSide.BUY,
                    price=price_obj,
                    quantity=qty_obj,
                    time_in_force=TimeInForce.IOC,
                    post_only=False,
                )
                self.submit_order(order)
                
                if self.config.log_trade_signals:
                    self.log.warning(
                        f"🔴 COVER SHORT: {float(qty_obj):.4f} @ {cover_price:.6f} (IOC)",
                        LogColor.RED,
                    )
    
    def _place_aggressive_exit(self, now_ns: int) -> None:
        """Place aggressive IOC order to exit immediately."""
        if self._net_position <= 0:
            return
        
        exit_qty = min(self._net_position * 0.98, self._available_base * 0.9)
        if exit_qty < self.config.min_order_qty:
            return
        if exit_qty * self._follower_mid < self.config.min_order_value_usd:
            return
        
        # Get best bid and go slightly below
        if self.follower_book:
            best_bid = self.follower_book.best_bid_price()
            if best_bid:
                exit_price = float(best_bid) * 0.999  # 0.1% below bid
            else:
                exit_price = self._follower_mid * 0.995
        else:
            exit_price = self._follower_mid * 0.995
        
        # Round to tick
        exit_price = round(exit_price / self._tick_size) * self._tick_size
        
        price_obj = Price(exit_price, precision=self._price_precision)
        qty_obj = Quantity(exit_qty, precision=self._size_precision)
        
        order = self.order_factory.limit(
            instrument_id=self.config.follower_instrument_id,
            order_side=OrderSide.SELL,
            price=price_obj,
            quantity=qty_obj,
            time_in_force=TimeInForce.IOC,
            post_only=False,
        )
        
        self.submit_order(order)
        
        if self.config.log_trade_signals:
            self.log.warning(
                f"🔴 AGGRESSIVE EXIT: {exit_qty:.4f} @ {exit_price:.6f} (IOC)",
                LogColor.RED,
            )
    
    # -------------------------------------------------------------------------
    # COOLDOWN STATE
    # -------------------------------------------------------------------------
    
    def _state_cooldown(self, now_ns: int) -> None:
        """Post-exit cooldown before next range entry."""
        elapsed_secs = (now_ns - self._state_entry_ns) / 1_000_000_000
        
        if elapsed_secs >= self.config.exit_cooldown_secs:
            self._cooldown_until_ns = 0
            self._transition_state(SniperState.WAITING, now_ns, "Cooldown complete")
    
    # =========================================================================
    # ORDER MANAGEMENT
    # =========================================================================
    
    def _place_or_update_order(
        self,
        side: OrderSide,
        price: float,
        qty: float,
        now_ns: int,
    ) -> None:
        """Place new order or update existing."""
        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns
        
        # Round price to tick
        price = round(price / self._tick_size) * self._tick_size
        
        price_obj = Price(price, precision=self._price_precision)
        qty_obj = Quantity(qty, precision=self._size_precision)
        
        # Check if we should modify existing order
        if order is not None and not order.is_closed:
            age_ms = (now_ns - order_ts) / 1_000_000
            if age_ms < self.config.min_quote_lifetime_ms:
                return
            
            current_price = float(order.price)
            if abs(price - current_price) < self._tick_size:
                return
            
            self.modify_order(order, quantity=qty_obj, price=price_obj, client_id=self.client_id)
            
            if side == OrderSide.BUY:
                self._bid_order_ts_ns = now_ns
            else:
                self._ask_order_ts_ns = now_ns
            return
        
        # Place new order
        new_order = self.order_factory.limit(
            instrument_id=self.config.follower_instrument_id,
            order_side=side,
            price=price_obj,
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
        """Handle fill events."""
        if not hasattr(event, "last_qty") or not hasattr(event, "order_side"):
            return
        
        qty = float(event.last_qty)
        side = event.order_side
        price = float(event.last_px) if hasattr(event, "last_px") else 0.0
        
        now_ns = self._now_ns()
        
        # Update position
        if side == OrderSide.BUY:
            self._net_position += qty
            if price > 0:
                self._available_quote -= qty * price
                self._available_base += qty
            # Track entry
            if self._entry_price == 0:
                self._entry_price = price
                self._entry_ts_ns = now_ns
        else:
            old_position = self._net_position
            self._net_position -= qty
            if price > 0:
                self._available_base -= qty
                self._available_quote += qty * price
            
            # Calculate P&L on exit
            if self._entry_price > 0 and old_position > 0:
                pnl = (price - self._entry_price) * qty
                self._session_pnl += pnl
                
                if pnl > 0:
                    self._profitable_cycles += 1
                
                if self.config.log_trade_signals:
                    pnl_bps = (price - self._entry_price) / self._entry_price * BPS_MULTIPLIER
                    self.log.info(
                        f"💰 FILL: SELL {qty:.4f} @ {price:.6f} | "
                        f"P&L: ${pnl:.2f} ({pnl_bps:+.1f} bps)",
                        LogColor.GREEN if pnl > 0 else LogColor.RED,
                    )
            
            # Reset entry if flat
            if self._net_position <= 0:
                self._entry_price = 0.0
                self._entry_ts_ns = 0
        
        self._total_fills += 1
        
        if self.config.log_trade_signals:
            self.log.info(
                f"📝 FILL: {side.name} {qty:.4f} @ {price:.6f} | "
                f"Position: {self._net_position:.4f}",
                LogColor.CYAN,
            )
