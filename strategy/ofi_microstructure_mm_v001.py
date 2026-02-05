"""Market Microstructure OFI Market Maker v001 (single-venue, toxicity-aware)."""

from __future__ import annotations

import random
import time
from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, OrderStatus, TimeInForce
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy


class OFIMicrostructureMMConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for OFI microstructure market maker."""

    instrument_id: InstrumentId
    order_qty: Decimal
    max_position_qty: Decimal

    # Quoting
    spread_bps: Decimal = Decimal("20.0")
    min_profit_bps: Decimal = Decimal("1.0")
    quote_refresh_interval_ms: int = 2000
    quote_refresh_jitter_ms: int = 200
    min_quote_lifetime_ms: int = 1000
    min_requote_ticks: int = 1

    # OFI shield
    ofi_depth: int = 10
    ofi_max_bps: Decimal = Decimal("5.0")
    ofi_pause_ms: int = 2000

    # Inventory skew
    inventory_skew_bps: Decimal = Decimal("3.0")

    # Book subscriptions
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50

    # Execution
    post_only: bool = True
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None
    use_modify_orders: bool = True

    # Data quality
    max_data_staleness_ms: int = 5000

    # Fees & balance
    maker_fee_bps: Decimal = Decimal("7.5")
    min_balance_ratio: Decimal = Decimal("0.95")

    # Logging
    log_guard_events: bool = True


class OFIMicrostructureMM(Strategy):
    """Single-venue OFI market maker with toxicity shields and inventory skew."""

    def __init__(self, config: OFIMicrostructureMMConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.book: OrderBook | None = None
        self.mid: Decimal | None = None

        self._tick_size: Decimal = Decimal("0")
        self._order_qty: Quantity | None = None
        self._bid_order: Order | None = None
        self._ask_order: Order | None = None
        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0

        self._last_quote_ts_ns: int = 0
        self._last_book_ts_ns: int = 0
        self._pause_until_ns: int = 0

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
        self._order_qty = self.instrument.make_qty(self.config.order_qty)

        self.subscribe_order_book_deltas(
            instrument_id=self.config.instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if deltas.instrument_id != self.config.instrument_id:
            return

        if self.book is None:
            return

        self.book.apply_deltas(deltas)
        self._last_book_ts_ns = self._event_ts_ns(deltas)
        self.mid = self._book_mid(self.book)
        if self.mid is None:
            return

        self._refresh_quotes(self._last_book_ts_ns)

    def on_event(self, event) -> None:
        if hasattr(event, "last_qty") and hasattr(event, "order_side"):
            if event.order_side == OrderSide.BUY:
                self._net_position += event.last_qty.as_decimal()
            elif event.order_side == OrderSide.SELL:
                self._net_position -= event.last_qty.as_decimal()

    def _refresh_quotes(self, now_ns: int) -> None:
        if self.instrument is None or self.book is None or self.mid is None:
            return

        if self._is_data_stale(now_ns):
            return

        if now_ns < self._pause_until_ns:
            return

        jitter = random.randint(0, max(0, self.config.quote_refresh_jitter_ms)) * 1_000_000
        min_interval = (self.config.quote_refresh_interval_ms * 1_000_000) + jitter
        if now_ns - self._last_quote_ts_ns < min_interval:
            return
        self._last_quote_ts_ns = now_ns

        ofi = self._calculate_ofi(self.book)
        if ofi is None:
            return

        ofi_bps = ofi * Decimal("10000")
        if abs(ofi_bps) >= self.config.ofi_max_bps:
            if self.config.log_guard_events:
                self.log.warning(
                    f"OFI SHIELD: ofi_bps={ofi_bps:.2f} -> pause quotes",
                    LogColor.YELLOW,
                )
            self._pause_until_ns = now_ns + (self.config.ofi_pause_ms * 1_000_000)
            self._cancel_open_orders()
            return

        inventory_ratio = Decimal("0")
        if self.config.max_position_qty > Decimal("0"):
            inventory_ratio = self._net_position / self.config.max_position_qty
        skew_bps = -inventory_ratio * self.config.inventory_skew_bps
        fair_price = self.mid * (Decimal("1") + (skew_bps / Decimal("10000")))

        min_edge_bps = self.config.min_profit_bps + (self.config.maker_fee_bps * Decimal("2"))
        spread_bps = max(self.config.spread_bps, min_edge_bps)
        spread_half = self.mid * (spread_bps / Decimal("20000"))

        desired_bid = fair_price - spread_half
        desired_ask = fair_price + spread_half

        best_bid = self.book.best_bid_price()
        best_ask = self.book.best_ask_price()
        if best_bid is None or best_ask is None:
            return

        tick = self._tick_size if self._tick_size else Decimal("0")
        desired_bid = min(desired_bid, best_ask.as_decimal() - (tick if tick else Decimal("0")))
        desired_ask = max(desired_ask, best_bid.as_decimal() + (tick if tick else Decimal("0")))

        if desired_bid >= desired_ask:
            mid = (best_bid.as_decimal() + best_ask.as_decimal()) / Decimal("2")
            desired_bid = mid - (tick * 2 if tick else Decimal("0.01"))
            desired_ask = mid + (tick * 2 if tick else Decimal("0.01"))

        bid_qty = self._apply_balance_limits(self.config.order_qty, OrderSide.BUY)
        ask_qty = self._apply_balance_limits(self.config.order_qty, OrderSide.SELL)

        if self._net_position >= self.config.max_position_qty:
            bid_qty = None
        if self._net_position <= -self.config.max_position_qty:
            ask_qty = None

        if bid_qty is not None:
            self._place_or_replace(OrderSide.BUY, desired_bid, fair_price, spread_bps, now_ns, bid_qty)

        if ask_qty is not None:
            self._place_or_replace(OrderSide.SELL, desired_ask, fair_price, spread_bps, now_ns, ask_qty)

    def _place_or_replace(
        self,
        side: OrderSide,
        desired_price: Decimal,
        fair_price: Decimal,
        spread_bps: Decimal,
        now_ns: int,
        desired_qty: Quantity,
    ) -> None:
        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts_ns = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns

        if order is not None and not getattr(order, "is_closed", False):
            current_price = order.price.as_decimal()
            current_qty = order.quantity.as_decimal() if getattr(order, "quantity", None) else None
            if current_qty is not None and desired_price == current_price and desired_qty.as_decimal() == current_qty:
                return

        if self._should_replace(order, order_ts_ns, desired_price, fair_price, spread_bps, desired_qty, now_ns):
            if order is not None and not getattr(order, "is_closed", False):
                if self.config.use_modify_orders:
                    price = self.instrument.make_price(desired_price)
                    self.modify_order(
                        order=order,
                        price=price,
                        quantity=desired_qty,
                        client_id=self.client_id,
                    )
                    if side == OrderSide.BUY:
                        self._bid_order_ts_ns = now_ns
                    else:
                        self._ask_order_ts_ns = now_ns
                    return

                self.cancel_order(order, client_id=self.client_id)

            price = self.instrument.make_price(desired_price)
            new_order = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=side,
                price=price,
                quantity=desired_qty,
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

    def _should_replace(
        self,
        order: object | None,
        order_ts_ns: int,
        desired_price: Decimal,
        fair_price: Decimal,
        spread_bps: Decimal,
        desired_qty: Quantity,
        now_ns: int,
    ) -> bool:
        if order is None or getattr(order, "is_closed", False):
            return True

        age_ms = (now_ns - order_ts_ns) / 1_000_000
        if age_ms < self.config.min_quote_lifetime_ms:
            return False

        current_price = order.price.as_decimal()
        if fair_price > Decimal("0"):
            if order.side == OrderSide.BUY:
                current_edge_bps = (fair_price - current_price) / fair_price * Decimal("10000")
            else:
                current_edge_bps = (current_price - fair_price) / fair_price * Decimal("10000")

            min_edge = spread_bps * Decimal("0.3")
            max_edge = spread_bps * Decimal("0.8")
            if current_edge_bps < min_edge:
                return True
            if current_edge_bps > max_edge:
                return True

        ticks_delta = (abs(desired_price - current_price) / self._tick_size) if self._tick_size else Decimal("0")
        quantity_delta = Decimal("0")
        if hasattr(order, "quantity") and order.quantity is not None:
            quantity_delta = abs(order.quantity.as_decimal() - desired_qty.as_decimal())

        return ticks_delta >= self.config.min_requote_ticks or quantity_delta > Decimal("0")

    def _calculate_ofi(self, book: OrderBook) -> Decimal | None:
        bid_levels = book.bids()[: self.config.ofi_depth]
        ask_levels = book.asks()[: self.config.ofi_depth]

        bid_qty = Decimal("0")
        ask_qty = Decimal("0")
        for level in bid_levels:
            bid_qty += Decimal(str(level.size()))
        for level in ask_levels:
            ask_qty += Decimal(str(level.size()))

        total = bid_qty + ask_qty
        if total <= Decimal("0"):
            return None
        return (bid_qty - ask_qty) / total

    def _book_mid(self, book: OrderBook) -> Decimal | None:
        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        if best_bid is None or best_ask is None:
            return None
        return (best_bid.as_decimal() + best_ask.as_decimal()) / Decimal("2")

    def _get_available_balance(self, currency: Currency) -> Decimal:
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            accounts = self.cache.accounts()
            if accounts:
                account = next(iter(accounts.values()))

        if account is None:
            return Decimal("0")

        balance = account.balance_free(currency)
        if balance is None:
            return Decimal("0")
        return balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)

    def _apply_balance_limits(self, base_qty: Decimal, side: OrderSide) -> Quantity | None:
        if self.instrument is None or self.mid is None:
            return None

        base_currency = self.instrument.base_currency
        quote_currency = self.instrument.quote_currency

        if side == OrderSide.BUY:
            quote_balance = self._get_available_balance(quote_currency)
            max_buy = (quote_balance * self.config.min_balance_ratio) / self.mid
            qty = min(base_qty, max_buy)
        else:
            base_balance = self._get_available_balance(base_currency)
            max_sell = base_balance * self.config.min_balance_ratio
            qty = min(base_qty, max_sell)

        if qty <= Decimal("0"):
            return None
        return self.instrument.make_qty(qty)

    def _cancel_open_orders(self) -> None:
        open_orders = self.cache.orders_open(
            instrument_id=self.config.instrument_id,
            strategy_id=self.id,
        )
        for order in open_orders:
            if order.status in (OrderStatus.PENDING_CANCEL, OrderStatus.PENDING_UPDATE):
                continue
            self.cancel_order(order, client_id=self.client_id)

    def _event_ts_ns(self, deltas: OrderBookDeltas) -> int:
        ts_event = getattr(deltas, "ts_event", None)
        return self._event_ts_ns_from_event(ts_event)

    def _event_ts_ns_from_event(self, ts_event) -> int:
        if ts_event is None:
            return self._now_ns()
        if ts_event < 1_000_000_000_000:
            return int(ts_event * 1_000_000_000)
        if ts_event < 1_000_000_000_000_000:
            return int(ts_event * 1_000_000)
        return int(ts_event)

    def _is_data_stale(self, now_ns: int) -> bool:
        if self._last_book_ts_ns == 0:
            return True
        max_age_ns = self.config.max_data_staleness_ms * 1_000_000
        return now_ns - self._last_book_ts_ns > max_age_ns
