"""Stablecoin Market Maker v001 (USDC/USDT specialized)."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Currency, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy


class StablecoinMMConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for Stablecoin MM."""

    instrument_id: InstrumentId
    order_qty: Decimal
    max_position_qty: Decimal

    # Pricing (Fixed logic for 1.0000 peg)
    center_price: Decimal = Decimal("1.0000")
    spread_ticks: int = 1  # 1 tick away from center
    use_mid_price: bool = False  # If True, centers around current mid (for volatile proxies or tracking)

    # Execution
    quote_refresh_interval_ms: int = 10000
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = True
    client_id: ClientId | None = None

    # Balance
    min_balance_ratio: Decimal = Decimal("0.95")
    
    # Safety
    max_divergence_bps: Decimal = Decimal("10.0")  # Tolerance for depeg before stopping

    # Book
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 10


class StablecoinMM(Strategy):
    """
    Fixed-range market maker for stablecoin pairs (e.g. USDC/USDT).
    Places bids at center - spread and asks at center + spread.
    """

    def __init__(self, config: StablecoinMMConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.book: OrderBook | None = None

        self._bid_order: Order | None = None
        self._ask_order: Order | None = None
        self._last_quote_ts_ns: int = 0
        
        self._tick_size: Decimal = Decimal("0")
        self._net_position: Decimal = Decimal("0")
        self.client_id = config.client_id

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument {self.config.instrument_id}")
            self.stop()
            return

        self.book = OrderBook(
            instrument_id=self.instrument.id,
            book_type=self.config.book_type,
        )
        self._tick_size = self.instrument.price_increment.as_decimal()

        self.subscribe_order_book_deltas(
            instrument_id=self.config.instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )
        
        # Initial Position Sync
        # (Simplified: assume 0 or would need account sync logic)
        self._net_position = Decimal("0")

        self.log.info(f"StablecoinMM started for {self.instrument.id}")

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if deltas.instrument_id != self.config.instrument_id:
            return

        self.book.apply_deltas(deltas)
        self._check_peg_health()
        self._refresh_quotes(self._event_ts_ns(deltas))

    def on_event(self, event) -> None:
        # Track position changes from fills
        if hasattr(event, "last_qty") and hasattr(event, "order_side"):
             qty = event.last_qty.as_decimal()
             if event.order_side == OrderSide.BUY:
                 self._net_position += qty
             elif event.order_side == OrderSide.SELL:
                 self._net_position -= qty

    def _refresh_quotes(self, now_ns: int) -> None:
        # Rate limit
        if now_ns - self._last_quote_ts_ns < self.config.quote_refresh_interval_ms * 1_000_000:
            return
        self._last_quote_ts_ns = now_ns

        # Calculate Prices
        if self.config.use_mid_price:
             best_bid = self.book.best_bid_price()
             best_ask = self.book.best_ask_price()
             if best_bid and best_ask:
                 center = (best_bid.as_decimal() + best_ask.as_decimal()) / Decimal("2")
             else:
                 center = self.config.center_price
        else:
             center = self.config.center_price

        offset = self.config.spread_ticks * self._tick_size
        bid_price = center - offset
        ask_price = center + offset

        # Place/Maintain Orders
        self._maintain_order(OrderSide.BUY, bid_price, self.config.order_qty)
        self._maintain_order(OrderSide.SELL, ask_price, self.config.order_qty)

    def _maintain_order(self, side: OrderSide, price: Decimal, qty: Decimal) -> None:
        current_order = self._bid_order if side == OrderSide.BUY else self._ask_order
        
        # Inventory Safety Check
        if side == OrderSide.BUY and self._net_position >= self.config.max_position_qty:
            if current_order and not current_order.is_closed:
                self.cancel_order(current_order, client_id=self.client_id)
            return
        
        if side == OrderSide.SELL and self._net_position <= -self.config.max_position_qty:
            if current_order and not current_order.is_closed:
                self.cancel_order(current_order, client_id=self.client_id)
            return

        # Check existing order
        if current_order and not current_order.is_closed:
            if current_order.price.as_decimal() == price and current_order.quantity.as_decimal() == qty:
                return # All good
            
            # Need to update (Simple cancel/replace for now)
            self.cancel_order(current_order, client_id=self.client_id)
        
        # Balance Check
        safe_qty = self._apply_balance_limits(qty, side, price)
        if safe_qty is None:
            return

        # Place New
        new_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=side,
            price=self.instrument.make_price(price),
            quantity=safe_qty,
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
            tags=["smp_type=CancelMaker"], # Attempt to pass SMP via tags
        )
        self.submit_order(new_order)
        
        if side == OrderSide.BUY:
            self._bid_order = new_order
        else:
            self._ask_order = new_order

    def _apply_balance_limits(self, base_qty: Decimal, side: OrderSide, price: Decimal) -> Quantity | None:
        if self.instrument is None:
             return None
             
        base_currency = self.instrument.base_currency
        quote_currency = self.instrument.quote_currency

        if side == OrderSide.BUY:
            quote_balance = self._get_available_balance(quote_currency)
            max_buy = (quote_balance * self.config.min_balance_ratio) / price
            qty = min(base_qty, max_buy)
        else:
            base_balance = self._get_available_balance(base_currency)
            max_sell = base_balance * self.config.min_balance_ratio
            qty = min(base_qty, max_sell)

        if qty < (base_qty * Decimal("0.1")): # Too small
            return None
            
        return self.instrument.make_qty(qty)

    def _get_available_balance(self, currency: Currency) -> Decimal:
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if not account:
            # Fallback scan
            for acc in self.cache.accounts().values():
                if acc.venue == self.config.instrument_id.venue:
                    account = acc
                    break
        
        if account:
            return account.balance_free(currency).as_decimal()
        return Decimal("0")

    def _check_peg_health(self) -> None:
        # Safety: Stop if market deviates too far from center_price (depeg)
        best_bid = self.book.best_bid_price()
        best_ask = self.book.best_ask_price()
        
        if not best_bid or not best_ask:
            return
            
        mid = (best_bid.as_decimal() + best_ask.as_decimal()) / 2
        deviation = abs(mid - self.config.center_price) / self.config.center_price * 10000
        
        if deviation > self.config.max_divergence_bps:
             self.log.error(f"KILLSWITCH: Depeg detected! Market at {mid}, deviation {deviation} bps.")
             self.stop()

    def _event_ts_ns(self, deltas: OrderBookDeltas) -> int:
        ts = getattr(deltas, "ts_event", None)
        if ts is None: return 0
        # Normalize to ns (heuristics)
        if ts < 1_000_000_000_000: return int(ts * 1_000_000_000)
        if ts < 1_000_000_000_000_000: return int(ts * 1_000_000)
        return int(ts)
