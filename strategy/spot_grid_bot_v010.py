"""Spot Grid Bot v010 - Bybit-Style Grid Trading.

PHILOSOPHY:
Mimic Bybit's proven Grid Bot approach but with smarter range detection.
Wide spreads (50-200 bps), fewer trades, reliable profits per trade.

THE GRID APPROACH:
1. DETECT a price range using Bollinger Bands (automatic range finding)
2. CREATE a grid of N levels within the range
3. PLACE buy orders below mid, sell orders above mid
4. When buy fills → place sell at next grid up
5. When sell fills → place buy at next grid down
6. PROFIT from each completed grid cycle

KEY INSIGHT FROM BYBIT:
- Their example: 740 bps per grid level ($4000 on $54000 BTC)
- We'll use 50-150 bps depending on pair volatility
- Much wider than HFT (8-15 bps) = more reliable profit per trade

ADVANTAGES OVER HFT:
- Each trade captures 50-150 bps vs 8-15 bps
- Fee impact: 13.5 bps RT is only 9-27% of spread (vs 90-170% in HFT)
- Works on ANY pair regardless of natural spread
- No need for tight timing - orders sit and wait

VERSION: v010.1
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

import numpy as np

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import (
    BookType,
    OrderSide,
    OrderStatus,
    TimeInForce,
    TriggerType,
)
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
BPS_MULTIPLIER: Final[float] = 10000.0
BPS_DIVISOR: Final[float] = 0.0001


class GridState(Enum):
    """Grid bot operational state."""
    INITIALIZING = "INITIALIZING"
    CALCULATING_RANGE = "CALCULATING_RANGE"
    PLACING_GRID = "PLACING_GRID"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    OUT_OF_RANGE = "OUT_OF_RANGE"


@dataclass
class GridLevel:
    """Represents a single grid level."""
    price: float
    side: OrderSide
    order_id: ClientOrderId | None = None
    is_filled: bool = False
    fill_count: int = 0


# =============================================================================
# CONFIGURATION
# =============================================================================
class SpotGridBotConfig(StrategyConfig, frozen=True):
    """Configuration for Spot Grid Bot v010."""
    
    # === INSTRUMENT IDS ===
    instrument_id: str  # Trading instrument (e.g., "LINKUSDT-SPOT.BYBIT")
    
    # === GRID SETTINGS ===
    num_grids: int = 10  # Number of grid levels (5-20 typical)
    grid_spread_bps: float = 100.0  # Spread between grid levels in bps
    
    # Use either fixed range OR auto-detect
    use_auto_range: bool = True  # Auto-detect range using BB
    
    # Fixed range (only if use_auto_range=False)
    fixed_upper_price: float = 0.0
    fixed_lower_price: float = 0.0
    
    # Auto-range settings (Bollinger Bands)
    bb_period: int = 50  # Lookback for range calculation
    bb_std_dev: float = 2.0  # Standard deviations for range
    range_recalc_interval: int = 100  # Recalculate range every N ticks
    
    # === ORDER SIZING ===
    order_qty: float = 1.0  # Quantity per grid order
    max_position_qty: float = 10.0  # Maximum total position
    min_order_qty: float = 0.001
    min_order_value_usd: float = 5.50  # Bybit minimum
    
    # === RISK MANAGEMENT ===
    max_open_orders: int = 20  # Max open orders at once
    pause_on_trend: bool = True  # Pause if strong trend detected
    trend_threshold_bps: float = 300.0  # Trend = price moved > 300 bps
    
    # === EXECUTION ===
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = True  # Always maker
    
    # === LOGGING ===
    log_grid_updates: bool = True
    log_fills: bool = True
    log_range_calcs: bool = True


# =============================================================================
# STRATEGY
# =============================================================================
class SpotGridBot(Strategy):
    """
    Spot Grid Bot v010 - Bybit-Style Grid Trading.
    
    Places a grid of buy/sell orders across a price range.
    Profits from price oscillation within the range.
    """
    
    def __init__(self, config: SpotGridBotConfig) -> None:
        super().__init__(config)
        
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.instrument: Instrument | None = None
        
        # Grid settings
        self.num_grids = config.num_grids
        self.grid_spread_bps = config.grid_spread_bps
        self.use_auto_range = config.use_auto_range
        self.fixed_upper = config.fixed_upper_price
        self.fixed_lower = config.fixed_lower_price
        
        # Auto-range settings
        self.bb_period = config.bb_period
        self.bb_std_dev = config.bb_std_dev
        self.range_recalc_interval = config.range_recalc_interval
        
        # Order settings
        self.order_qty = config.order_qty
        self.max_position_qty = config.max_position_qty
        self.min_order_qty = config.min_order_qty
        self.min_order_value = config.min_order_value_usd
        
        # Risk settings
        self.max_open_orders = config.max_open_orders
        self.pause_on_trend = config.pause_on_trend
        self.trend_threshold_bps = config.trend_threshold_bps
        
        # Execution settings
        self.time_in_force = config.time_in_force
        self.post_only = config.post_only
        
        # Logging settings
        self.log_grid = config.log_grid_updates
        self.log_fills = config.log_fills
        self.log_range = config.log_range_calcs
        
        # State
        self.grid_state = GridState.INITIALIZING
        self.grid_levels: list[GridLevel] = []
        self.upper_price: float = 0.0
        self.lower_price: float = 0.0
        self.mid_price: float = 0.0
        
        # Price tracking for range calculation
        self.price_history: deque[float] = deque(maxlen=max(100, self.bb_period * 2))
        self.tick_count: int = 0
        self.last_range_calc_tick: int = 0
        
        # Position tracking
        self.net_position: float = 0.0
        self.base_balance: float = 0.0
        self.quote_balance: float = 0.0
        
        # Performance tracking
        self.total_grid_profits: float = 0.0
        self.completed_cycles: int = 0
        self.total_buys: int = 0
        self.total_sells: int = 0
        
        # Order tracking
        self.active_orders: dict[ClientOrderId, GridLevel] = {}
    
    def on_start(self) -> None:
        """Strategy startup."""
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found")
            return
        
        # Subscribe to quote ticks for price updates
        self.subscribe_quote_ticks(self.instrument_id)
        
        self.log.info(
            f"SpotGridBot v010 started | "
            f"Instrument: {self.instrument_id} | "
            f"Grids: {self.num_grids} | "
            f"Spread: {self.grid_spread_bps} bps | "
            f"Qty: {self.order_qty}",
            color=LogColor.GREEN,
        )
        
        self.grid_state = GridState.CALCULATING_RANGE
    
    def on_stop(self) -> None:
        """Strategy shutdown."""
        self.log.info("SpotGridBot stopping - cancelling all orders")
        self.cancel_all_orders(self.instrument_id)
        
        # Log final stats
        self.log.info(
            f"FINAL STATS | "
            f"Cycles: {self.completed_cycles} | "
            f"Profit: ${self.total_grid_profits:.2f} | "
            f"Buys: {self.total_buys} | "
            f"Sells: {self.total_sells}",
            color=LogColor.CYAN,
        )
    
    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Handle quote tick updates."""
        if tick.instrument_id != self.instrument_id:
            return
        
        # Calculate mid price
        bid = float(tick.bid_price)
        ask = float(tick.ask_price)
        mid = (bid + ask) / 2
        
        self.tick_count += 1
        self.price_history.append(mid)
        
        # State machine
        if self.grid_state == GridState.CALCULATING_RANGE:
            self._handle_range_calculation(mid)
        elif self.grid_state == GridState.PLACING_GRID:
            self._place_grid_orders(mid)
        elif self.grid_state == GridState.ACTIVE:
            self._handle_active_state(mid)
        elif self.grid_state == GridState.OUT_OF_RANGE:
            self._handle_out_of_range(mid)
    
    def _handle_range_calculation(self, mid: float) -> None:
        """Calculate price range for grid."""
        # Need enough price history
        if len(self.price_history) < self.bb_period:
            if self.tick_count % 50 == 0:
                self.log.info(
                    f"Collecting price data: {len(self.price_history)}/{self.bb_period}",
                    color=LogColor.BLUE,
                )
            return
        
        if self.use_auto_range:
            # Calculate Bollinger Bands for range
            prices = np.array(list(self.price_history)[-self.bb_period:])
            sma = np.mean(prices)
            std = np.std(prices)
            
            self.upper_price = sma + (self.bb_std_dev * std)
            self.lower_price = sma - (self.bb_std_dev * std)
            self.mid_price = sma
        else:
            # Use fixed range
            self.upper_price = self.fixed_upper
            self.lower_price = self.fixed_lower
            self.mid_price = (self.upper_price + self.lower_price) / 2
        
        # Validate range
        range_bps = (self.upper_price - self.lower_price) / self.mid_price * BPS_MULTIPLIER
        min_range_bps = self.grid_spread_bps * self.num_grids
        
        if range_bps < min_range_bps:
            if self.log_range:
                self.log.warning(
                    f"Range too narrow: {range_bps:.1f} bps < {min_range_bps:.1f} bps needed"
                )
            return
        
        if self.log_range:
            self.log.info(
                f"RANGE CALCULATED | "
                f"Upper: ${self.upper_price:.4f} | "
                f"Lower: ${self.lower_price:.4f} | "
                f"Mid: ${self.mid_price:.4f} | "
                f"Range: {range_bps:.1f} bps",
                color=LogColor.GREEN,
            )
        
        self.last_range_calc_tick = self.tick_count
        self.grid_state = GridState.PLACING_GRID
    
    def _create_grid_levels(self, current_price: float) -> list[GridLevel]:
        """Create grid levels based on current price and range."""
        levels = []
        
        # Calculate grid interval
        range_size = self.upper_price - self.lower_price
        grid_interval = range_size / (self.num_grids + 1)
        
        for i in range(1, self.num_grids + 1):
            price = self.lower_price + (i * grid_interval)
            
            # Determine side based on price vs current
            if price < current_price:
                side = OrderSide.BUY
            else:
                side = OrderSide.SELL
            
            levels.append(GridLevel(
                price=price,
                side=side,
                order_id=None,
                is_filled=False,
                fill_count=0,
            ))
        
        return levels
    
    def _place_grid_orders(self, current_price: float) -> None:
        """Place initial grid orders."""
        # Check if price is within range
        if current_price < self.lower_price or current_price > self.upper_price:
            self.log.warning(
                f"Price ${current_price:.4f} outside range "
                f"[${self.lower_price:.4f}, ${self.upper_price:.4f}]"
            )
            self.grid_state = GridState.OUT_OF_RANGE
            return
        
        # Create grid levels
        self.grid_levels = self._create_grid_levels(current_price)
        
        if self.log_grid:
            self.log.info(
                f"GRID CREATED | "
                f"{len(self.grid_levels)} levels | "
                f"Current: ${current_price:.4f}",
                color=LogColor.CYAN,
            )
        
        # Place orders at each level
        orders_placed = 0
        for level in self.grid_levels:
            if orders_placed >= self.max_open_orders:
                break
            
            # Skip levels too close to current price (within 1 grid interval)
            distance_bps = abs(level.price - current_price) / current_price * BPS_MULTIPLIER
            if distance_bps < self.grid_spread_bps * 0.5:
                continue
            
            order_id = self._place_grid_order(level)
            if order_id:
                level.order_id = order_id
                self.active_orders[order_id] = level
                orders_placed += 1
        
        if self.log_grid:
            buys = sum(1 for l in self.grid_levels if l.side == OrderSide.BUY and l.order_id)
            sells = sum(1 for l in self.grid_levels if l.side == OrderSide.SELL and l.order_id)
            self.log.info(
                f"ORDERS PLACED | Buys: {buys} | Sells: {sells}",
                color=LogColor.GREEN,
            )
        
        self.grid_state = GridState.ACTIVE
    
    def _place_grid_order(self, level: GridLevel) -> ClientOrderId | None:
        """Place a single grid order."""
        if self.instrument is None:
            return None
        
        # Check position limits
        if level.side == OrderSide.BUY:
            if self.net_position + self.order_qty > self.max_position_qty:
                return None
        else:
            if self.net_position - self.order_qty < -self.max_position_qty:
                return None
        
        # Check minimum order value
        order_value = level.price * self.order_qty
        if order_value < self.min_order_value:
            return None
        
        # Create order
        try:
            price = self.instrument.make_price(level.price)
            qty = self.instrument.make_qty(self.order_qty)
            
            order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=level.side,
                quantity=qty,
                price=price,
                time_in_force=self.time_in_force,
                post_only=self.post_only,
            )
            
            self.submit_order(order)
            
            if self.log_grid:
                side_str = "BUY" if level.side == OrderSide.BUY else "SELL"
                self.log.debug(
                    f"Grid order: {side_str} {self.order_qty} @ ${level.price:.4f}"
                )
            
            return order.client_order_id
            
        except Exception as e:
            self.log.error(f"Failed to place grid order: {e}")
            return None
    
    def _handle_active_state(self, mid: float) -> None:
        """Handle active grid trading."""
        # Check if price moved out of range
        if mid < self.lower_price or mid > self.upper_price:
            self.log.warning(
                f"Price ${mid:.4f} moved OUT OF RANGE "
                f"[${self.lower_price:.4f}, ${self.upper_price:.4f}]",
                color=LogColor.YELLOW,
            )
            self.grid_state = GridState.OUT_OF_RANGE
            return
        
        # Check for trend (large price movement)
        if self.pause_on_trend and len(self.price_history) > 10:
            recent_prices = list(self.price_history)[-10:]
            price_change_bps = (max(recent_prices) - min(recent_prices)) / mid * BPS_MULTIPLIER
            if price_change_bps > self.trend_threshold_bps:
                self.log.warning(
                    f"TREND DETECTED: {price_change_bps:.1f} bps movement | PAUSING",
                    color=LogColor.YELLOW,
                )
                self.grid_state = GridState.PAUSED
                return
        
        # Periodic range recalculation
        if self.use_auto_range:
            ticks_since_recalc = self.tick_count - self.last_range_calc_tick
            if ticks_since_recalc >= self.range_recalc_interval:
                self._recalculate_range(mid)
    
    def _recalculate_range(self, mid: float) -> None:
        """Recalculate range and adjust grid if needed."""
        if len(self.price_history) < self.bb_period:
            return
        
        prices = np.array(list(self.price_history)[-self.bb_period:])
        sma = np.mean(prices)
        std = np.std(prices)
        
        new_upper = sma + (self.bb_std_dev * std)
        new_lower = sma - (self.bb_std_dev * std)
        
        # Check if range shifted significantly (> 50 bps)
        upper_shift = abs(new_upper - self.upper_price) / self.upper_price * BPS_MULTIPLIER
        lower_shift = abs(new_lower - self.lower_price) / self.lower_price * BPS_MULTIPLIER
        
        if upper_shift > 50 or lower_shift > 50:
            if self.log_range:
                self.log.info(
                    f"RANGE SHIFT | "
                    f"Upper: ${self.upper_price:.4f} → ${new_upper:.4f} ({upper_shift:.1f} bps) | "
                    f"Lower: ${self.lower_price:.4f} → ${new_lower:.4f} ({lower_shift:.1f} bps)",
                    color=LogColor.BLUE,
                )
            
            # Cancel all orders and rebuild grid
            self.cancel_all_orders(self.instrument_id)
            self.active_orders.clear()
            
            self.upper_price = new_upper
            self.lower_price = new_lower
            self.mid_price = sma
            
            self.grid_state = GridState.PLACING_GRID
        
        self.last_range_calc_tick = self.tick_count
    
    def _handle_out_of_range(self, mid: float) -> None:
        """Handle price outside grid range."""
        # Check if price returned to range
        if self.lower_price <= mid <= self.upper_price:
            self.log.info(
                f"Price ${mid:.4f} returned to range | RESUMING",
                color=LogColor.GREEN,
            )
            self.grid_state = GridState.PLACING_GRID
            return
        
        # Recalculate range to catch up with price
        if self.use_auto_range:
            ticks_since_recalc = self.tick_count - self.last_range_calc_tick
            if ticks_since_recalc >= self.range_recalc_interval // 2:  # Faster recalc when out of range
                self.grid_state = GridState.CALCULATING_RANGE
    
    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle order fill events."""
        order_id = event.client_order_id
        
        if order_id not in self.active_orders:
            return
        
        level = self.active_orders[order_id]
        level.is_filled = True
        level.fill_count += 1
        
        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)
        fill_value = fill_price * fill_qty
        
        # Update position
        if event.order_side == OrderSide.BUY:
            self.net_position += fill_qty
            self.total_buys += 1
        else:
            self.net_position -= fill_qty
            self.total_sells += 1
        
        if self.log_fills:
            side_str = "BUY" if event.order_side == OrderSide.BUY else "SELL"
            self.log.info(
                f"GRID FILL | {side_str} {fill_qty} @ ${fill_price:.4f} "
                f"(${fill_value:.2f}) | Net pos: {self.net_position:.4f}",
                color=LogColor.GREEN if event.order_side == OrderSide.BUY else LogColor.MAGENTA,
            )
        
        # Remove from active orders
        del self.active_orders[order_id]
        level.order_id = None
        
        # Place counter order at opposite grid level
        self._place_counter_order(level, fill_price)
    
    def _place_counter_order(self, filled_level: GridLevel, fill_price: float) -> None:
        """Place counter order after a fill."""
        # Find the next grid level in opposite direction
        grid_interval = (self.upper_price - self.lower_price) / (self.num_grids + 1)
        
        if filled_level.side == OrderSide.BUY:
            # Was a buy, place sell above
            counter_price = fill_price + grid_interval
            counter_side = OrderSide.SELL
        else:
            # Was a sell, place buy below
            counter_price = fill_price - grid_interval
            counter_side = OrderSide.BUY
        
        # Validate counter price is within range
        if counter_price < self.lower_price or counter_price > self.upper_price:
            if self.log_grid:
                self.log.debug(f"Counter price ${counter_price:.4f} outside range, skipping")
            return
        
        # Create new level for counter order
        counter_level = GridLevel(
            price=counter_price,
            side=counter_side,
            order_id=None,
            is_filled=False,
            fill_count=0,
        )
        
        order_id = self._place_grid_order(counter_level)
        if order_id:
            counter_level.order_id = order_id
            self.active_orders[order_id] = counter_level
            self.grid_levels.append(counter_level)
            
            # Track completed cycle
            if filled_level.fill_count > 0:
                # This was a round-trip (buy then sell, or sell then buy)
                profit_bps = self.grid_spread_bps
                profit_usd = fill_price * filled_level.fill_count * self.order_qty * profit_bps * BPS_DIVISOR
                self.total_grid_profits += profit_usd
                self.completed_cycles += 1
                
                if self.log_fills:
                    self.log.info(
                        f"CYCLE COMPLETE | Profit: ${profit_usd:.4f} | "
                        f"Total cycles: {self.completed_cycles} | "
                        f"Total profit: ${self.total_grid_profits:.2f}",
                        color=LogColor.CYAN,
                    )
    
    def on_order_rejected(self, event) -> None:
        """Handle order rejection."""
        self.log.warning(f"Order rejected: {event.reason}")
        
        # Remove from tracking
        if event.client_order_id in self.active_orders:
            del self.active_orders[event.client_order_id]
    
    def on_order_canceled(self, event) -> None:
        """Handle order cancellation."""
        if event.client_order_id in self.active_orders:
            del self.active_orders[event.client_order_id]
