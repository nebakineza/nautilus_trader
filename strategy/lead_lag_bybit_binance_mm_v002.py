"""Lead-Lag Market Maker v002 with King's Guard and balance-aware sizing."""

from __future__ import annotations

import gc
import random
import time
from decimal import Decimal
from typing import Optional

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from strategy.inventory.risk_manager import InventoryRiskManager


class LeadLagMMv2Config(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for ``LeadLagMMv2``."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId
    global_guard_id: InstrumentId | None = None

    order_qty: Decimal
    max_position_qty: Decimal

    # Quoting
    spread_bps: Decimal = Decimal("16.0")
    quote_refresh_interval_ms: int = 100
    quote_refresh_jitter_ms: int = 30
    min_quote_lifetime_ms: int = 50
    min_requote_ticks: int = 1

    # Guard (lead-lag protection)
    guard_threshold_bps: Decimal = Decimal("8.0")
    guard_hysteresis_bps: Decimal = Decimal("5.0")

    # Global guard (BTC King)
    global_guard_threshold_bps: Decimal = Decimal("8.0")
    global_guard_window_ms: int = 1000

    # Data quality
    max_data_staleness_ms: int = 5000

    # Book subscriptions
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50

    # Execution
    post_only: bool = True
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None
    use_modify_orders: bool = True

    # Inventory
    risk_aversion: float = 0.8
    volatility: float = 0.02
    inventory_time_horizon_secs: float = 60.0

    # Balance protection
    min_balance_ratio: Decimal = Decimal("0.95")

    # Logging
    log_guard_events: bool = True
    log_leader_updates: bool = False


class LeadLagMMv2(Strategy):
    """Lead-lag market maker with global guard and balance-aware quoting."""

    def __init__(self, config: LeadLagMMv2Config) -> None:
        super().__init__(config)

        self.follower_instrument: Instrument | None = None
        self.leader_instrument: Instrument | None = None
        self.guard_instrument: Instrument | None = None

        self.follower_book: OrderBook | None = None
        self.leader_book: OrderBook | None = None
        self.guard_book: OrderBook | None = None

        self.leader_mid: Decimal | None = None
        self.guard_mid: Decimal | None = None
        self.follower_mid: Decimal | None = None

        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0
        self._bid_order: object | None = None
        self._ask_order: object | None = None

        self._tick_size: Decimal = Decimal("0")
        self._order_qty: Quantity | None = None
        self._last_quote_ts_ns: int = 0
        self._last_leader_ts_ns: int = 0
        self._last_guard_ts_ns: int = 0
        self._last_follower_ts_ns: int = 0

        self._last_guard_mid: Decimal | None = None

        self._guard_block_buy: bool = False
        self._guard_block_sell: bool = False
        self._global_guard_active: bool = False

        self._net_position: Decimal = Decimal("0")
        self._inventory_manager = InventoryRiskManager(
            max_position=self.config.max_position_qty,
            risk_aversion=self.config.risk_aversion,
            volatility=self.config.volatility,
            time_horizon=self.config.inventory_time_horizon_secs,
        )

        self.client_id = config.client_id

    def on_start(self) -> None:
        gc.disable()
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

    def on_stop(self) -> None:
        gc.enable()

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
        if self.leader_book is None:
            return

        self.leader_book.apply_deltas(deltas)
        self._last_leader_ts_ns = self._event_ts_ns(deltas)
        mid = self._book_mid(self.leader_book)
        if mid is None:
            return

        if self.leader_mid != mid and self.config.log_leader_updates:
            self.log.info(f"Leader mid update: {mid}", LogColor.CYAN)
        self.leader_mid = mid
        self._update_guard()

    def _handle_guard_deltas(self, deltas: OrderBookDeltas) -> None:
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
        if self.follower_book is None:
            return

        self.follower_book.apply_deltas(deltas)
        self._last_follower_ts_ns = self._event_ts_ns(deltas)
        mid = self._book_mid(self.follower_book)
        if mid is None:
            return

        self.follower_mid = mid
        self._update_guard()
        self._refresh_quotes()

    def _book_mid(self, book: OrderBook) -> Decimal | None:
        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            return None
        return Decimal((bid + ask) / 2)

    def _update_guard(self) -> None:
        if self.leader_mid is None or self.follower_mid is None:
            return

        if self._is_data_stale():
            return

        leader_mid = float(self.leader_mid)
        follower_mid = float(self.follower_mid)
        diff_bps = (leader_mid - follower_mid) / follower_mid * 10000.0
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
                    self.log.warning(
                        f"TOXIC DUMP: diff_bps={diff_bps:.2f} -> cancel buys",
                        LogColor.RED,
                    )
                self._cancel_side_orders(OrderSide.BUY)
            if block_sell:
                if self.config.log_guard_events:
                    self.log.warning(
                        f"TOXIC PUMP: diff_bps={diff_bps:.2f} -> cancel sells",
                        LogColor.RED,
                    )
                self._cancel_side_orders(OrderSide.SELL)

    def _update_global_guard(self) -> None:
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
            self.log.warning(
                f"GLOBAL GUARD: diff_bps={diff_bps:.2f} -> cancel quotes",
                LogColor.RED,
            )
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)

    def _cancel_side_orders(self, side: OrderSide) -> None:
        open_orders = self.cache.orders_open(
            instrument_id=self.config.follower_instrument_id,
            strategy_id=self.id,
        )
        for order in open_orders:
            if order.side == side:
                self.cancel_order(order, client_id=self.client_id)

    def _refresh_quotes(self) -> None:
        if self.follower_instrument is None or self.follower_mid is None:
            return

        if self._guard_block_buy or self._guard_block_sell or self._global_guard_active:
            return

        if self._is_data_stale():
            return

        now_ns = self._now_ns()
        jitter = random.randint(0, max(0, self.config.quote_refresh_jitter_ms)) * 1_000_000
        min_interval = (self.config.quote_refresh_interval_ms * 1_000_000) + jitter
        if now_ns - self._last_quote_ts_ns < min_interval:
            return

        self._last_quote_ts_ns = now_ns

        if self._bid_order and getattr(self._bid_order, "is_closed", False):
            self._bid_order = None
        if self._ask_order and getattr(self._ask_order, "is_closed", False):
            self._ask_order = None

        inventory_skew, bid_qty, ask_qty = self._inventory_adjustments()

        spread_half = self.follower_mid * (self.config.spread_bps / Decimal("20000"))
        if self.leader_mid is None:
            fair_price = self.follower_mid
        else:
            fair_price = (self.follower_mid * Decimal("0.1")) + (self.leader_mid * Decimal("0.9"))
        raw_bid = (fair_price - spread_half) + inventory_skew
        raw_ask = (fair_price + spread_half) + inventory_skew

        best_bid = self.follower_book.best_bid_price() if self.follower_book else None
        best_ask = self.follower_book.best_ask_price() if self.follower_book else None
        if best_bid is None or best_ask is None:
            return

        tick = self._tick_size if self._tick_size else Decimal("0")
        best_bid_dec = best_bid.as_decimal()
        best_ask_dec = best_ask.as_decimal()
        clamp_bid = best_ask_dec - tick if tick else best_ask_dec
        clamp_ask = best_bid_dec + tick if tick else best_bid_dec

        desired_bid = min(raw_bid, clamp_bid)
        desired_ask = max(raw_ask, clamp_ask)

        if desired_bid >= desired_ask:
            mid = (best_bid_dec + best_ask_dec) / Decimal("2")
            desired_bid = mid - (tick * 2 if tick else Decimal("0.01"))
            desired_ask = mid + (tick * 2 if tick else Decimal("0.01"))

        if bid_qty is not None:
            self._place_or_replace(OrderSide.BUY, desired_bid, now_ns, bid_qty)
        if ask_qty is not None:
            self._place_or_replace(OrderSide.SELL, desired_ask, now_ns, ask_qty)

    def _place_or_replace(
        self,
        side: OrderSide,
        desired_price: Decimal,
        now_ns: int,
        desired_qty: Quantity,
    ) -> None:
        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts_ns = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns

        if self._should_replace(order, order_ts_ns, desired_price, desired_qty, now_ns):
            if order is not None and not getattr(order, "is_closed", False):
                if self.config.use_modify_orders:
                    price = self.follower_instrument.make_price(desired_price)
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

            price = self.follower_instrument.make_price(desired_price)
            new_order = self.order_factory.limit(
                instrument_id=self.config.follower_instrument_id,
                order_side=side,
                price=price,
                quantity=desired_qty,
                time_in_force=self.config.time_in_force,
                post_only=self.config.post_only,
            )
            self.submit_order(new_order)

            if side == OrderSide.BUY:
                self._bid_order = new_order
                self._bid_order_ts_ns = now_ns
            else:
                self._ask_order = new_order
                self._ask_order_ts_ns = now_ns

    def _should_replace(
        self,
        order: object | None,
        order_ts_ns: int,
        desired_price: Decimal,
        desired_qty: Quantity,
        now_ns: int,
    ) -> bool:
        if order is None or getattr(order, "is_closed", False):
            return True

        age_ms = (now_ns - order_ts_ns) / 1_000_000
        if age_ms < self.config.min_quote_lifetime_ms:
            return False

        current_price = order.price.as_decimal()
        ticks_delta = (abs(desired_price - current_price) / self._tick_size) if self._tick_size else Decimal("0")

        quantity_delta = Decimal("0")
        if hasattr(order, "quantity") and order.quantity is not None:
            quantity_delta = abs(order.quantity.as_decimal() - desired_qty.as_decimal())

        return ticks_delta >= self.config.min_requote_ticks or quantity_delta > Decimal("0")

    def _inventory_adjustments(self) -> tuple[Decimal, Quantity | None, Quantity | None]:
        if self.follower_mid is None or self._order_qty is None:
            return Decimal("0"), None, None

        optimal_target = Decimal("0")
        skew_bps = Decimal(
            str(
                self._inventory_manager.calculate_skew(
                    position=self._net_position,
                    optimal_target=optimal_target,
                    mid_price=float(self.follower_mid),
                ),
            ),
        )
        skew_px = self.follower_mid * (skew_bps / Decimal("10000"))

        bid_size, ask_size = self._inventory_manager.calculate_sizes(
            position=self._net_position,
            optimal_target=optimal_target,
            base_size=self.config.order_qty,
        )

        bid_qty = self._apply_balance_limits(bid_size, OrderSide.BUY)
        ask_qty = self._apply_balance_limits(ask_size, OrderSide.SELL)

        return skew_px, bid_qty, ask_qty

    def _apply_balance_limits(self, base_qty: Decimal, side: OrderSide) -> Quantity | None:
        if self.follower_instrument is None:
            return None

        if self.follower_mid is None:
            return None

        base_currency = self.follower_instrument.base_currency
        quote_currency = self.follower_instrument.quote_currency

        if side == OrderSide.BUY:
            quote_balance = self._get_available_balance(quote_currency)
            max_buy = (quote_balance * self.config.min_balance_ratio) / self.follower_mid
            qty = min(base_qty, max_buy)
        else:
            base_balance = self._get_available_balance(base_currency)
            max_sell = base_balance * self.config.min_balance_ratio
            qty = min(base_qty, max_sell)

        if qty <= Decimal("0"):
            return None

        return self.follower_instrument.make_qty(qty)

    def _get_available_balance(self, currency: Currency) -> Decimal:
        account = self.cache.account_for_venue(self.config.follower_instrument_id.venue)
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

    def on_event(self, event) -> None:
        if hasattr(event, "last_qty") and hasattr(event, "order_side"):
            if event.order_side == OrderSide.BUY:
                self._net_position += event.last_qty.as_decimal()
            elif event.order_side == OrderSide.SELL:
                self._net_position -= event.last_qty.as_decimal()

    def _now_ns(self) -> int:
        try:
            return self.clock.timestamp_ns()
        except Exception:
            return time.time_ns()

    def _event_ts_ns(self, deltas: OrderBookDeltas) -> int:
        ts_event = getattr(deltas, "ts_event", None)
        if ts_event is None:
            return self._now_ns()
        ts_value = int(ts_event)
        if ts_value < 1_000_000_000_000:  # seconds
            return ts_value * 1_000_000_000
        if ts_value < 1_000_000_000_000_000:  # milliseconds
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
