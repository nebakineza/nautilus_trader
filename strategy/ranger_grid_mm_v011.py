"""Ranger Grid MM v011 - Low-frequency wide grid market maker.

Designed for API-constrained spot trading:
- Wide fixed range
- Few grid levels
- Throttled order submission
- Pause when price exits range
"""

from __future__ import annotations

from typing import Final

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

BPS_MULTIPLIER: Final[float] = 10000.0


class RangerGridMMConfig(StrategyConfig, frozen=True):
    """Configuration for Ranger Grid MM v011."""

    instrument_id: str

    # Fixed grid range
    fixed_lower_price: float
    fixed_upper_price: float
    total_grids: int = 20  # Total grid levels across the range

    # Sizing
    order_qty: float = 1.0
    max_position_qty: float = 10.0
    min_order_value_usd: float = 5.50

    # Execution
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = True

    # Capital allocation (for shared accounts)
    quote_budget_pct: float = 1.0  # Fraction of USDT balance this strategy may use (0.0-1.0)

    # Behavior
    pause_on_out_of_range: bool = True
    warmup_ticks: int = 5
    max_orders_per_tick: int = 1
    min_submit_interval_ms: int = 500

    # Logging
    log_level: int = 1


class RangerGridMM(Strategy):
    """Low-frequency grid maker with fixed range and throttled placement."""

    def __init__(self, config: RangerGridMMConfig) -> None:
        super().__init__(config)

        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.instrument: Instrument | None = None

        self.lower_price = config.fixed_lower_price
        self.upper_price = config.fixed_upper_price
        self.total_grids = max(2, config.total_grids)

        self.order_qty = config.order_qty
        self.max_position_qty = config.max_position_qty
        self.min_order_value = config.min_order_value_usd

        self.tif = config.time_in_force
        self.post_only = config.post_only

        self.quote_budget_pct = max(0.01, min(1.0, config.quote_budget_pct))

        self.pause_on_out_of_range = config.pause_on_out_of_range
        self.warmup_ticks = config.warmup_ticks
        self.max_orders_per_tick = max(1, config.max_orders_per_tick)
        self.min_submit_interval_ms = max(50, config.min_submit_interval_ms)

        self.log_level = config.log_level

        self.tick_count = 0
        self.grid_placed = False
        self.out_of_range = False

        self.grid_interval = (self.upper_price - self.lower_price) / self.total_grids
        self.mid_price = (self.upper_price + self.lower_price) / 2

        self.grid_orders: dict[float, tuple[ClientOrderId, OrderSide]] = {}
        self.order_to_level: dict[ClientOrderId, float] = {}
        self.pending_orders: list[tuple[float, OrderSide]] = []

        self.net_position = 0.0
        self.available_base = 0.0
        self.available_quote = 0.0
        self.total_buys = 0
        self.total_sells = 0
        self.grid_cycles = 0.0
        self.last_submit_ts_ns = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if not self.instrument:
            self.log.error(f"Instrument {self.instrument_id} not found")
            return

        self.subscribe_quote_ticks(self.instrument_id)
        self._initialize_balance()

        grid_bps = (self.grid_interval / self.mid_price) * BPS_MULTIPLIER
        if self.log_level >= 1:
            self.log.info(
                f"RangerGridMM v011 started | Inst: {self.instrument_id} | "
                f"Range: ${self.lower_price:.2f}-${self.upper_price:.2f} | "
                f"Grids: {self.total_grids} | Interval: {grid_bps:.1f} bps",
                color=LogColor.GREEN,
            )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)

        if self.log_level >= 1:
            self.log.info(
                f"FINAL | Cycles: {self.grid_cycles:.1f} | "
                f"Buys: {self.total_buys} | Sells: {self.total_sells}",
                color=LogColor.CYAN,
            )

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if tick.instrument_id != self.instrument_id:
            return

        self.tick_count += 1
        mid = (float(tick.bid_price) + float(tick.ask_price)) / 2

        self._process_order_queue()

        if not self.grid_placed:
            if self.tick_count >= self.warmup_ticks:
                self._place_grid_orders(mid)
                self.grid_placed = True
            return

        if mid < self.lower_price or mid > self.upper_price:
            if self.pause_on_out_of_range:
                self.out_of_range = True
                if self.log_level >= 1:
                    self.log.warning(
                        f"Price ${mid:.4f} out of range [{self.lower_price:.4f}, {self.upper_price:.4f}] | Pausing",
                        color=LogColor.YELLOW,
                    )
                return

        if self.out_of_range and self.lower_price <= mid <= self.upper_price:
            self.out_of_range = False
            if self.log_level >= 1:
                self.log.info(
                    f"Price ${mid:.4f} back in range | Resuming",
                    color=LogColor.GREEN,
                )

    def _place_grid_orders(self, current_price: float) -> None:
        if not self.instrument:
            return

        self.pending_orders.clear()

        buys_placed = 0
        sells_placed = 0

        levels = [self.lower_price + (i * self.grid_interval) for i in range(self.total_grids + 1)]
        for price in levels:
            if abs(price - current_price) < (self.grid_interval * 0.1):
                continue
            if price < current_price:
                potential_pos = self.net_position + (buys_placed + 1) * self.order_qty
                if potential_pos > self.max_position_qty:
                    continue
                if price * self.order_qty < self.min_order_value:
                    continue
                if self.available_quote < price * self.order_qty:
                    continue
                self.pending_orders.append((price, OrderSide.BUY))
                buys_placed += 1
            elif price > current_price:
                potential_pos = self.net_position - (sells_placed + 1) * self.order_qty
                if potential_pos < -self.max_position_qty:
                    continue
                if price * self.order_qty < self.min_order_value:
                    continue
                if self.available_base < self.order_qty:
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
        if not self.instrument:
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
            return order.client_order_id
        except Exception as exc:
            self.log.error(f"Order failed: {exc}")
            return None

    def _process_order_queue(self) -> None:
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

    def on_order_filled(self, event: OrderFilled) -> None:
        order_id = event.client_order_id
        if order_id not in self.order_to_level:
            return

        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)
        filled_level = self.order_to_level[order_id]

        if event.order_side == OrderSide.BUY:
            self.net_position += fill_qty
            self.available_base += fill_qty
            self.available_quote -= fill_price * fill_qty
            self.total_buys += 1
        else:
            self.net_position -= fill_qty
            self.available_base -= fill_qty
            self.available_quote += fill_price * fill_qty
            self.total_sells += 1

        if self.log_level >= 1:
            side_str = "BUY" if event.order_side == OrderSide.BUY else "SELL"
            color = LogColor.GREEN if event.order_side == OrderSide.BUY else LogColor.MAGENTA
            self.log.info(
                f"FILL | {side_str} {fill_qty} @ ${fill_price:.4f} | Pos: {self.net_position:.4f}",
                color=color,
            )

        del self.order_to_level[order_id]
        if filled_level in self.grid_orders:
            del self.grid_orders[filled_level]

        self._place_counter_order(filled_level, event.order_side)

    def _place_counter_order(self, filled_level: float, filled_side: OrderSide) -> None:
        if self.out_of_range:
            return

        if filled_side == OrderSide.BUY:
            counter_price = filled_level + self.grid_interval
            counter_side = OrderSide.SELL
            self.grid_cycles += 0.5
        else:
            counter_price = filled_level - self.grid_interval
            counter_side = OrderSide.BUY
            self.grid_cycles += 0.5

        if counter_price < self.lower_price or counter_price > self.upper_price:
            return

        if counter_side == OrderSide.BUY and self.net_position >= self.max_position_qty:
            return
        if counter_side == OrderSide.SELL and self.net_position <= -self.max_position_qty:
            return

        self.pending_orders.append((counter_price, counter_side))

    def on_order_rejected(self, event) -> None:
        if self.log_level >= 1:
            self.log.warning(f"Order rejected: {event.reason}")

        if event.client_order_id in self.order_to_level:
            level = self.order_to_level[event.client_order_id]
            del self.order_to_level[event.client_order_id]
            if level in self.grid_orders:
                del self.grid_orders[level]

    def on_order_canceled(self, event) -> None:
        if event.client_order_id in self.order_to_level:
            level = self.order_to_level[event.client_order_id]
            del self.order_to_level[event.client_order_id]
            if level in self.grid_orders:
                del self.grid_orders[level]

    def _now_ns(self) -> int:
        return self.clock.timestamp_ns()

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
                    f"Balance: {self.available_base:.4f} {base_currency} | "
                    f"{self.available_quote:.2f} {quote_currency}{pct_str}",
                    color=LogColor.BLUE,
                )
        except Exception as exc:
            self.log.warning(f"Balance refresh error: {exc}")

    def _initialize_balance(self) -> None:
        """Initialize balances from cache (alias for first call)."""
        self._refresh_balance()

    def on_account_state(self, event) -> None:
        """Refresh available capital whenever Bybit pushes an account update."""
        self._refresh_balance()
