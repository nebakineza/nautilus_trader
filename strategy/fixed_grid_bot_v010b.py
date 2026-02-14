"""
Spot Grid Bot v010b - Fixed Range Grid Trading

PHILOSOPHY:
Like Bybit's Grid Bot but simpler - use FIXED percentage ranges.
No complex volatility detection. Just set upper/lower bounds and let it trade.

Example:
- Current price: $21.00 (LINK)
- Range: +/- 2% = $20.58 to $21.42 (200 bps up and down)
- 5 grids = 80 bps per grid (100 bps total spread with fees)
- Place buys at: $20.58, $20.75, $20.91
- Place sells at: $21.08, $21.25, $21.42

When buy fills -> place sell at next grid up (+80 bps)
When sell fills -> place buy at next grid down (-80 bps)

PROFIT PER ROUNDTRIP:
- Grid profit: 80 bps
- Fees: 13.5 bps RT (VIP1)
- Net: 66.5 bps per cycle

VERSION: v010b.1
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Final

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import (
    OrderSide,
    TimeInForce,
)
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy


BPS_MULTIPLIER: Final[float] = 10000.0
BPS_DIVISOR: Final[float] = 0.0001


class FixedGridBotConfig(StrategyConfig, frozen=True):
    """Configuration for Fixed Range Grid Bot."""
    
    # Instrument
    instrument_id: str
    
    # Grid parameters
    num_grids: int = 5  # Number of grid levels each side (total = 2*num_grids)
    total_grids: int | None = None  # If set, use total grid count across range (Bybit-style)
    range_pct: float = 2.0  # Range as % from mid (e.g., 2% = $20.58-$21.42 on $21)
    fixed_lower_price: float = 0.0  # Manual lower bound (if > 0, use fixed range)
    fixed_upper_price: float = 0.0  # Manual upper bound (if > 0, use fixed range)
    
    # Order sizing
    order_qty: float = 1.0
    max_position_qty: float = 10.0
    min_order_value_usd: float = 5.50
    
    # Capital allocation (for shared accounts)
    quote_budget_pct: float = 1.0  # Fraction of USDT balance this strategy may use (0.0-1.0)
    
    # Range behavior
    rebuild_on_out_of_range: bool = False  # Bybit-style: pause when out of range
    max_orders_per_tick: int = 2  # Throttle initial grid placement
    min_submit_interval_ms: int = 250  # Minimum time between order submissions
    
    # Execution
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = True
    
    # Warmup - wait for this many ticks before placing grid
    warmup_ticks: int = 10
    
    # Logging
    log_level: int = 1  # 0=minimal, 1=fills, 2=verbose


class FixedGridBot(Strategy):
    """
    Fixed Range Grid Bot - Simple Bybit-style grid trading.
    
    Places a fixed grid of orders centered on the initial price.
    Profits from price oscillation within the grid.
    """
    
    def __init__(self, config: FixedGridBotConfig) -> None:
        super().__init__(config)
        
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.instrument: Instrument | None = None
        
        # Grid config
        self.num_grids = config.num_grids
        self.total_grids = config.total_grids or (config.num_grids * 2)
        self.range_pct = config.range_pct / 100.0  # Convert to decimal
        self.fixed_lower_price = config.fixed_lower_price
        self.fixed_upper_price = config.fixed_upper_price
        self.use_fixed_range = self.fixed_lower_price > 0 and self.fixed_upper_price > 0
        self.order_qty = config.order_qty
        self.max_position_qty = config.max_position_qty
        self.min_order_value = config.min_order_value_usd
        self.quote_budget_pct = max(0.01, min(1.0, config.quote_budget_pct))
        self.rebuild_on_out_of_range = config.rebuild_on_out_of_range
        self.max_orders_per_tick = max(1, config.max_orders_per_tick)
        self.min_submit_interval_ms = max(50, config.min_submit_interval_ms)
        
        # Execution
        self.tif = config.time_in_force
        self.post_only = config.post_only
        self.warmup_ticks = config.warmup_ticks
        
        # Logging
        self.log_level = config.log_level
        
        # State
        self.tick_count = 0
        self.grid_placed = False
        self.mid_price = 0.0
        self.upper_price = 0.0
        self.lower_price = 0.0
        self.grid_interval = 0.0
        self.out_of_range = False
        self.pending_orders: list[tuple[float, OrderSide]] = []
        self.last_submit_ts_ns = 0
        
        # Grid tracking: price_level -> (order_id, side)
        self.grid_orders: dict[float, tuple[ClientOrderId, OrderSide]] = {}
        self.order_to_level: dict[ClientOrderId, float] = {}
        
        # Position tracking
        self.net_position = 0.0
        self.available_base = 0.0
        self.available_quote = 0.0
        
        # Stats
        self.total_buys = 0
        self.total_sells = 0
        self.grid_cycles = 0
        self.total_profit_bps = 0.0
    
    def on_start(self) -> None:
        """Strategy startup."""
        self.instrument = self.cache.instrument(self.instrument_id)
        if not self.instrument:
            self.log.error(f"Instrument {self.instrument_id} not found")
            return
        
        self.subscribe_quote_ticks(self.instrument_id)
        self._refresh_balance()
        
        range_label = (
            f"${self.fixed_lower_price:.2f}-${self.fixed_upper_price:.2f}"
            if self.use_fixed_range
            else f"±{self.range_pct*100:.1f}%"
        )
        self.log.info(
            f"FixedGridBot v010b started | "
            f"Inst: {self.instrument_id} | "
            f"Grids: {self.total_grids} | "
            f"Range: {range_label}",
            color=LogColor.GREEN,
        )
    
    def on_stop(self) -> None:
        """Strategy shutdown."""
        self.cancel_all_orders(self.instrument_id)
        
        # Calculate profit per cycle
        if self.use_fixed_range:
            grid_bps = ((self.fixed_upper_price - self.fixed_lower_price) / self.mid_price) * BPS_MULTIPLIER
            grid_bps = grid_bps / max(1, self.total_grids)
        else:
            grid_bps = (self.range_pct * 2 * BPS_MULTIPLIER) / max(1, self.total_grids)
        fee_bps = 13.5  # VIP1 RT
        net_bps = grid_bps - fee_bps
        total_profit_est = self.grid_cycles * net_bps * self.order_qty * self.mid_price / BPS_MULTIPLIER
        
        self.log.info(
            f"FINAL | Cycles: {self.grid_cycles} | "
            f"Est profit: ${total_profit_est:.2f} | "
            f"Buys: {self.total_buys} | Sells: {self.total_sells}",
            color=LogColor.CYAN,
        )
    
    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Handle quote tick."""
        if tick.instrument_id != self.instrument_id:
            return
        
        self.tick_count += 1
        mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
        self._process_order_queue()
        
        # Warmup phase - collect initial price
        if not self.grid_placed:
            if self.tick_count >= self.warmup_ticks:
                self._setup_grid(mid)
            return
        
        if self.out_of_range:
            if self.lower_price <= mid <= self.upper_price:
                self.out_of_range = False
                if self.log_level >= 1:
                    self.log.info(
                        f"Price ${mid:.4f} back in range | Resuming",
                        color=LogColor.GREEN,
                    )
            else:
                return
        
        # Check if price moved out of range
        if mid < self.lower_price or mid > self.upper_price:
            if self.log_level >= 1:
                action = "Rebuilding grid" if self.rebuild_on_out_of_range else "Pausing"
                self.log.warning(
                    f"Price ${mid:.4f} out of range "
                    f"[${self.lower_price:.4f}, ${self.upper_price:.4f}] | "
                    f"{action}",
                    color=LogColor.YELLOW,
                )
            if self.rebuild_on_out_of_range:
                self._rebuild_grid(mid)
            else:
                self.out_of_range = True
            return
    
    def _setup_grid(self, mid_price: float) -> None:
        """Set up the initial grid around mid price."""
        if self.use_fixed_range:
            self.lower_price = self.fixed_lower_price
            self.upper_price = self.fixed_upper_price
            self.mid_price = (self.upper_price + self.lower_price) / 2
        else:
            self.mid_price = mid_price
            self.upper_price = mid_price * (1 + self.range_pct)
            self.lower_price = mid_price * (1 - self.range_pct)
        
        if mid_price < self.lower_price or mid_price > self.upper_price:
            if self.log_level >= 1:
                self.log.warning(
                    f"Mid ${mid_price:.4f} outside range "
                    f"[${self.lower_price:.4f}, ${self.upper_price:.4f}] | Waiting",
                    color=LogColor.YELLOW,
                )
            self.out_of_range = True
            return
        
        # Grid interval = total range / total_grids (Bybit-style)
        total_range = self.upper_price - self.lower_price
        self.grid_interval = total_range / max(1, self.total_grids)
        
        grid_bps = (self.grid_interval / self.mid_price) * BPS_MULTIPLIER
        
        if self.log_level >= 1:
            self.log.info(
                f"GRID SETUP | Mid: ${mid_price:.4f} | "
                f"Range: [${self.lower_price:.4f}, ${self.upper_price:.4f}] | "
                f"Interval: ${self.grid_interval:.4f} ({grid_bps:.1f} bps)",
                color=LogColor.GREEN,
            )
        
        self._place_grid_orders(mid_price)
        self.grid_placed = True
    
    def _place_grid_orders(self, current_price: float) -> None:
        """Place all grid orders."""
        if not self.instrument:
            return
        
        self.pending_orders.clear()
        
        buys_placed = 0
        sells_placed = 0
        buy_notional = 0.0
        
        levels = [self.lower_price + (i * self.grid_interval) for i in range(self.total_grids + 1)]
        for price in levels:
            if abs(price - current_price) < (self.grid_interval * 0.1):
                continue
            if price < current_price:
                # Buy below market
                potential_pos = self.net_position + (buys_placed + 1) * self.order_qty
                if potential_pos > self.max_position_qty:
                    continue
                if price * self.order_qty < self.min_order_value:
                    continue
                # Check if we have enough quote budget
                if buy_notional + (price * self.order_qty) > self.available_quote:
                    continue
                self.pending_orders.append((price, OrderSide.BUY))
                buy_notional += price * self.order_qty
                buys_placed += 1
            elif price > current_price:
                # Sell above market
                potential_pos = self.net_position - (sells_placed + 1) * self.order_qty
                if potential_pos < -self.max_position_qty:
                    continue
                if price * self.order_qty < self.min_order_value:
                    continue
                # Check if we have enough base to sell
                if (sells_placed + 1) * self.order_qty > self.available_base:
                    continue
                self.pending_orders.append((price, OrderSide.SELL))
                sells_placed += 1
        
        if self.log_level >= 1:
            self.log.info(
                f"GRID PLACED | Buys: {buys_placed} | Sells: {sells_placed}",
                color=LogColor.GREEN,
            )
        
        self._process_order_queue()
    
    def _place_order(self, price: float, side: OrderSide) -> ClientOrderId | None:
        """Place a single grid order."""
        if not self.instrument:
            return None
        
        # Check min order value
        order_value = price * self.order_qty
        if order_value < self.min_order_value:
            return None
        
        try:
            order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(self.order_qty),
                price=self.instrument.make_price(price),
                time_in_force=self.tif,
                post_only=self.post_only,
            )
            self.submit_order(order)
            
            if self.log_level >= 2:
                side_str = "BUY" if side == OrderSide.BUY else "SELL"
                self.log.debug(f"Order: {side_str} {self.order_qty} @ ${price:.4f}")
            
            return order.client_order_id
        except Exception as e:
            self.log.error(f"Order failed: {e}")
            return None
    
    def _rebuild_grid(self, new_mid: float) -> None:
        """Cancel all orders and rebuild grid around new mid price."""
        self.cancel_all_orders(self.instrument_id)
        self.grid_orders.clear()
        self.order_to_level.clear()
        self.pending_orders.clear()
        self._setup_grid(new_mid)
    
    def _process_order_queue(self) -> None:
        """Submit a limited number of pending grid orders per tick."""
        if self.out_of_range or not self.pending_orders:
            return
        
        if self._now_ns() - self.last_submit_ts_ns < self.min_submit_interval_ms * 1_000_000:
            return
        
        to_submit = min(self.max_orders_per_tick, len(self.pending_orders))
        for _ in range(to_submit):
            price, side = self.pending_orders.pop(0)
            order_id = self._place_order(price, side)
            if order_id:
                self.grid_orders[price] = (order_id, side)
                self.order_to_level[order_id] = price
        
        self.last_submit_ts_ns = self._now_ns()

    def _now_ns(self) -> int:
        """Get current clock time in nanoseconds."""
        return self.clock.timestamp_ns()
    
    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle order fill."""
        order_id = event.client_order_id
        
        if order_id not in self.order_to_level:
            return
        
        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)
        fill_value = fill_price * fill_qty
        filled_level = self.order_to_level[order_id]
        
        # Update position
        if event.order_side == OrderSide.BUY:
            self.net_position += fill_qty
            self.total_buys += 1
        else:
            self.net_position -= fill_qty
            self.total_sells += 1
        
        if self.log_level >= 1:
            side_str = "BUY" if event.order_side == OrderSide.BUY else "SELL"
            color = LogColor.GREEN if event.order_side == OrderSide.BUY else LogColor.MAGENTA
            self.log.info(
                f"FILL | {side_str} {fill_qty} @ ${fill_price:.4f} (${fill_value:.2f}) | "
                f"Pos: {self.net_position:.4f}",
                color=color,
            )
        
        # Remove from tracking
        del self.order_to_level[order_id]
        if filled_level in self.grid_orders:
            del self.grid_orders[filled_level]
        
        # Place counter order at opposite grid level
        self._place_counter_order(filled_level, event.order_side)
    
    def _place_counter_order(self, filled_level: float, filled_side: OrderSide) -> None:
        """Place counter order after a fill."""
        if self.out_of_range and not self.rebuild_on_out_of_range:
            return
        if filled_side == OrderSide.BUY:
            # Bought - place sell one grid up
            counter_price = filled_level + self.grid_interval
            counter_side = OrderSide.SELL
            self.grid_cycles += 0.5  # Half cycle
        else:
            # Sold - place buy one grid down
            counter_price = filled_level - self.grid_interval
            counter_side = OrderSide.BUY
            self.grid_cycles += 0.5  # Half cycle
        
        # Validate price in range
        if counter_price < self.lower_price or counter_price > self.upper_price:
            if self.log_level >= 2:
                self.log.debug(f"Counter ${counter_price:.4f} outside range, skipping")
            return
        
        # Check position limits
        if counter_side == OrderSide.BUY and self.net_position >= self.max_position_qty:
            return
        if counter_side == OrderSide.SELL and self.net_position <= -self.max_position_qty:
            return
        
        self.pending_orders.append((counter_price, counter_side))
    
    def on_order_rejected(self, event) -> None:
        """Handle order rejection."""
        if self.log_level >= 1:
            self.log.warning(f"Order rejected: {event.reason}")
        
        if event.client_order_id in self.order_to_level:
            level = self.order_to_level[event.client_order_id]
            del self.order_to_level[event.client_order_id]
            if level in self.grid_orders:
                del self.grid_orders[level]
    
    def on_order_canceled(self, event) -> None:
        """Handle order cancellation."""
        if event.client_order_id in self.order_to_level:
            level = self.order_to_level[event.client_order_id]
            del self.order_to_level[event.client_order_id]
            if level in self.grid_orders:
                del self.grid_orders[level]

    def _refresh_balance(self) -> None:
        """Read live balance from cache and apply quote_budget_pct."""
        if not self.instrument:
            return
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
                    self.available_base = float(base_balance.total.as_decimal())
                elif hasattr(base_balance, "as_decimal"):
                    self.available_base = float(base_balance.as_decimal())
                else:
                    self.available_base = float(base_balance)

            raw_quote = 0.0
            if quote_balance is not None:
                if hasattr(quote_balance, "total"):
                    raw_quote = float(quote_balance.total.as_decimal())
                elif hasattr(quote_balance, "as_decimal"):
                    raw_quote = float(quote_balance.as_decimal())
                else:
                    raw_quote = float(quote_balance)

            # Apply budget percentage — this strategy only claims its share
            self.available_quote = raw_quote * self.quote_budget_pct

            if self.log_level >= 1:
                pct_str = f" ({self.quote_budget_pct:.0%} of {raw_quote:.2f})" if self.quote_budget_pct < 1.0 else ""
                self.log.info(
                    f"Balance: {self.available_base:.6f} {base_currency} | "
                    f"{self.available_quote:.2f} {quote_currency}{pct_str}",
                    color=LogColor.BLUE,
                )
        except Exception as exc:
            self.log.warning(f"Balance refresh error: {exc}")

    def on_account_state(self, event) -> None:
        """Refresh available capital whenever Bybit pushes an account update."""
        self._refresh_balance()