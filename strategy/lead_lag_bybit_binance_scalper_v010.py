"""Lead-Lag Scalper v010 - Spot scalping for micro-impulses.

Design:
- No resting quotes
- Detect short-term momentum + OFI confirmation
- Enter with IOC limit at best price
- Exit on TP/SL or max hold
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Final

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas, QuoteTick
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy

BPS_MULTIPLIER: Final[float] = 10000.0


class LeadLagScalperConfig(StrategyConfig, frozen=True):
    """Configuration for Lead-Lag Scalper v010."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId

    book_type: BookType = BookType.L2_MBP
    book_depth: int = 10

    # Entry signal
    momentum_window: int = 5
    entry_momentum_bps: float = 12.0
    entry_ofi_threshold: float = 0.15
    entry_requires_both: bool = True
    long_only: bool = False

    # Execution
    order_qty: float = 2.0
    max_position_qty: float = 40.0
    min_order_value_usd: float = 5.50
    time_in_force: TimeInForce = TimeInForce.IOC
    post_only: bool = False

    # Fee-aware edge filter
    maker_fee_bps: float = 7.5
    taker_fee_bps: float = 10.0
    min_edge_bps: float = 25.0  # Require expected move > fees + buffer

    # Exit
    take_profit_bps: float = 20.0
    stop_loss_bps: float = 18.0
    max_hold_ms: int = 3000
    cooldown_ms: int = 1500
    take_profit_tif: TimeInForce = TimeInForce.GTC
    take_profit_post_only: bool = True

    # Logging
    log_signals: bool = True
    debug_log_interval_ms: int = 5000


class LeadLagScalper(Strategy):
    """Spot scalper using lead/lag momentum + OFI confirmation."""

    def __init__(self, config: LeadLagScalperConfig) -> None:
        super().__init__(config)

        self.follower_id = config.follower_instrument_id
        self.leader_id = config.leader_instrument_id

        self.follower_instrument: Instrument | None = None
        self.leader_book: OrderBook | None = None
        self.follower_book: OrderBook | None = None

        self._leader_mids: deque[float] = deque(maxlen=config.momentum_window)
        self._follower_mids: deque[float] = deque(maxlen=config.momentum_window)
        self._current_ofi: float = 0.0

        self._entry_order_id = None
        self._exit_order_id = None
        self._take_profit_order: Order | None = None

        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._entry_ts_ns: int = 0
        self._net_position: float = 0.0

        self._available_base: float = 0.0
        self._available_quote: float = 0.0

        self._cooldown_until_ns: int = 0
        self._last_debug_ts_ns: int = 0

    def on_start(self) -> None:
        self.follower_instrument = self.cache.instrument(self.follower_id)
        if self.follower_instrument is None:
            self.log.error(f"Instrument {self.follower_id} not found")
            self.stop()
            return

        self.subscribe_order_book_deltas(self.leader_id, self.config.book_type, self.config.book_depth)
        self.subscribe_order_book_deltas(self.follower_id, self.config.book_type, self.config.book_depth)
        self.subscribe_quote_ticks(self.leader_id)
        self.subscribe_quote_ticks(self.follower_id)

        self._initialize_balance()

        self.log.info(
            "Scalper v010 started",
            color=LogColor.GREEN,
        )

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        book = self.cache.order_book(deltas.instrument_id)
        if book is None:
            return

        if deltas.instrument_id == self.leader_id:
            self.leader_book = book
            leader_mid = self._book_mid(book)
            if leader_mid is not None:
                self._leader_mids.append(leader_mid)
                self._update_ofi(book)
        elif deltas.instrument_id == self.follower_id:
            self.follower_book = book
            follower_mid = self._book_mid(book)
            if follower_mid is not None:
                self._follower_mids.append(follower_mid)

        self._run_logic()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if tick.instrument_id == self.leader_id:
            leader_mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
            self._leader_mids.append(leader_mid)
            bid_size = float(tick.bid_size) if tick.bid_size is not None else 0.0
            ask_size = float(tick.ask_size) if tick.ask_size is not None else 0.0
            self._update_ofi_from_sizes(bid_size, ask_size)
        elif tick.instrument_id == self.follower_id:
            follower_mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
            self._follower_mids.append(follower_mid)

        self._run_logic()

    def on_order_filled(self, event: OrderFilled) -> None:
        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)

        if event.order_side == OrderSide.BUY:
            self._net_position += fill_qty
            self._available_base += fill_qty
            self._available_quote -= fill_price * fill_qty
        else:
            self._net_position -= fill_qty
            self._available_base -= fill_qty
            self._available_quote += fill_price * fill_qty

        if self._position_side is None:
            # Entry filled
            self._position_side = event.order_side
            self._entry_price = fill_price
            self._entry_ts_ns = self.clock.timestamp_ns()
            self._entry_order_id = None
            self._place_take_profit()
            if self.config.log_signals:
                self.log.info(
                    f"ENTRY FILLED {event.order_side.name} @ {fill_price:.4f}",
                    color=LogColor.GREEN,
                )
        else:
            # Exit filled
            self._position_side = None
            self._entry_price = 0.0
            self._entry_ts_ns = 0
            self._exit_order_id = None
            self._take_profit_order = None
            self._cooldown_until_ns = self.clock.timestamp_ns() + (self.config.cooldown_ms * 1_000_000)
            if self.config.log_signals:
                self.log.info(
                    f"EXIT FILLED {event.order_side.name} @ {fill_price:.4f}",
                    color=LogColor.MAGENTA,
                )

    def _run_logic(self) -> None:
        if self.follower_book is None or self.leader_book is None:
            return

        now_ns = self.clock.timestamp_ns()
        self._maybe_log_debug(now_ns)

        if self._position_side is None and self._take_profit_order is None:
            if self._available_base >= self.config.order_qty:
                mid = self._book_mid(self.follower_book)
                if mid is not None:
                    self._position_side = OrderSide.BUY
                    self._entry_price = mid
                    self._entry_ts_ns = now_ns
                    self._net_position = self._available_base
                    self._place_take_profit()
                    return
        if now_ns < self._cooldown_until_ns:
            return

        if self._position_side is not None:
            self._check_exit(now_ns)
            return

        if self._entry_order_id is not None:
            return

        if len(self._leader_mids) < max(2, self.config.momentum_window):
            return

        momentum_bps = self._momentum_bps(self._leader_mids)
        ofi = self._current_ofi

        if self.config.entry_requires_both:
            buy_signal = momentum_bps >= self.config.entry_momentum_bps and ofi >= self.config.entry_ofi_threshold
            sell_signal = momentum_bps <= -self.config.entry_momentum_bps and ofi <= -self.config.entry_ofi_threshold
        else:
            buy_signal = momentum_bps >= self.config.entry_momentum_bps and ofi >= self.config.entry_ofi_threshold
            sell_signal = momentum_bps <= -self.config.entry_momentum_bps and ofi <= -self.config.entry_ofi_threshold

        if buy_signal:
            self._try_enter(OrderSide.BUY)
        elif sell_signal and not self.config.long_only:
            if self._available_base >= self.config.order_qty:
                self._try_enter(OrderSide.SELL)

    def _try_enter(self, side: OrderSide) -> None:
        if self.follower_book is None:
            return

        best_bid = self.follower_book.best_bid_price()
        best_ask = self.follower_book.best_ask_price()
        if best_bid is None or best_ask is None:
            return

        price = float(best_ask) if side == OrderSide.BUY else float(best_bid)
        notional = price * self.config.order_qty
        if notional < self.config.min_order_value_usd:
            return

        if self._expected_edge_bps(side) < self.config.min_edge_bps:
            return

        if side == OrderSide.BUY and self._available_quote < notional:
            return
        if side == OrderSide.SELL and self._available_base < self.config.order_qty:
            return

        if abs(self._net_position) + self.config.order_qty > self.config.max_position_qty:
            return

        order = self.order_factory.limit(
            instrument_id=self.follower_id,
            order_side=side,
            quantity=self.follower_instrument.make_qty(self.config.order_qty),
            price=self.follower_instrument.make_price(price),
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
        )
        self.submit_order(order)
        self._entry_order_id = order.client_order_id

        if self.config.log_signals:
            self.log.info(
                f"ENTRY SIGNAL {side.name} @ {price:.4f} | mom={self._momentum_bps(self._leader_mids):.1f}bps | ofi={self._current_ofi:.2f}",
                color=LogColor.CYAN,
            )

    def _check_exit(self, now_ns: int) -> None:
        if self.follower_book is None or self._position_side is None:
            return

        if self._exit_order_id is not None or self._take_profit_order is not None:
            return

        best_bid = self.follower_book.best_bid_price()
        best_ask = self.follower_book.best_ask_price()
        if best_bid is None or best_ask is None:
            return

        mid = (float(best_bid) + float(best_ask)) / 2
        pnl_bps = self._pnl_bps(mid)
        held_ms = (now_ns - self._entry_ts_ns) / 1_000_000

        if pnl_bps >= self.config.take_profit_bps or pnl_bps <= -self.config.stop_loss_bps or held_ms >= self.config.max_hold_ms:
            if self._take_profit_order is not None:
                self.cancel_order(self._take_profit_order)
                self._take_profit_order = None
            exit_side = OrderSide.SELL if self._position_side == OrderSide.BUY else OrderSide.BUY
            price = float(best_bid) if exit_side == OrderSide.SELL else float(best_ask)
            order = self.order_factory.limit(
                instrument_id=self.follower_id,
                order_side=exit_side,
                quantity=self.follower_instrument.make_qty(self.config.order_qty),
                price=self.follower_instrument.make_price(price),
                time_in_force=self.config.time_in_force,
                post_only=self.config.post_only,
            )
            self.submit_order(order)
            self._exit_order_id = order.client_order_id

            if self.config.log_signals:
                self.log.info(
                    f"EXIT SIGNAL {exit_side.name} @ {price:.4f} | pnl={pnl_bps:.1f}bps | held={held_ms:.0f}ms",
                    color=LogColor.YELLOW,
                )

    def _update_ofi(self, book: OrderBook) -> None:
        bid_qty = 0.0
        ask_qty = 0.0
        for level in book.bids()[:5]:
            size = level.size() if callable(level.size) else level.size
            bid_qty += float(size)
        for level in book.asks()[:5]:
            size = level.size() if callable(level.size) else level.size
            ask_qty += float(size)
        total = bid_qty + ask_qty
        if total > 0:
            self._current_ofi = (bid_qty - ask_qty) / total

    def _update_ofi_from_sizes(self, bid_size: float, ask_size: float) -> None:
        total = bid_size + ask_size
        if total > 0:
            self._current_ofi = (bid_size - ask_size) / total

    def _expected_edge_bps(self, side: OrderSide) -> float:
        if side == OrderSide.BUY:
            return self.config.take_profit_bps - (2 * self.config.taker_fee_bps)
        return self.config.take_profit_bps - (2 * self.config.taker_fee_bps)

    def _place_take_profit(self) -> None:
        if self._position_side is None or self.follower_instrument is None:
            return
        if self._entry_price <= 0:
            return

        if self._position_side == OrderSide.BUY:
            tp_price = self._entry_price * (1 + (self.config.take_profit_bps / BPS_MULTIPLIER))
            tp_side = OrderSide.SELL
        else:
            tp_price = self._entry_price * (1 - (self.config.take_profit_bps / BPS_MULTIPLIER))
            tp_side = OrderSide.BUY

        order = self.order_factory.limit(
            instrument_id=self.follower_id,
            order_side=tp_side,
            quantity=self.follower_instrument.make_qty(self.config.order_qty),
            price=self.follower_instrument.make_price(tp_price),
            time_in_force=self.config.take_profit_tif,
            post_only=self.config.take_profit_post_only,
        )
        self.submit_order(order)
        self._take_profit_order = order

        if self.config.log_signals:
            self.log.info(
                f"TP PLACED {tp_side.name} @ {tp_price:.4f}",
                color=LogColor.CYAN,
            )

    def _momentum_bps(self, mids: deque[float]) -> float:
        if len(mids) < 2:
            return 0.0
        start = mids[0]
        end = mids[-1]
        if start == 0:
            return 0.0
        return ((end - start) / start) * BPS_MULTIPLIER

    def _pnl_bps(self, mid: float) -> float:
        if self._entry_price == 0:
            return 0.0
        if self._position_side == OrderSide.BUY:
            return ((mid - self._entry_price) / self._entry_price) * BPS_MULTIPLIER
        return ((self._entry_price - mid) / self._entry_price) * BPS_MULTIPLIER

    def _book_mid(self, book: OrderBook) -> float | None:
        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        if best_bid is None or best_ask is None:
            return None
        return (float(best_bid) + float(best_ask)) / 2

    def _initialize_balance(self) -> None:
        try:
            accounts = self.cache.accounts()
            if not accounts:
                return
            account = accounts[0]
            balances = account.balances()

            base_currency = self.follower_instrument.base_currency
            quote_currency = self.follower_instrument.quote_currency

            base_balance = balances.get(base_currency)
            quote_balance = balances.get(quote_currency)

            if base_balance is not None:
                if hasattr(base_balance, "total"):
                    self._available_base = float(base_balance.total.as_decimal())
                elif hasattr(base_balance, "as_decimal"):
                    self._available_base = float(base_balance.as_decimal())
                else:
                    self._available_base = float(base_balance)

            if quote_balance is not None:
                if hasattr(quote_balance, "total"):
                    self._available_quote = float(quote_balance.total.as_decimal())
                elif hasattr(quote_balance, "as_decimal"):
                    self._available_quote = float(quote_balance.as_decimal())
                else:
                    self._available_quote = float(quote_balance)

            self.log.info(
                f"Balance: {self._available_base:.4f} {base_currency} | {self._available_quote:.2f} {quote_currency}",
                color=LogColor.BLUE,
            )
        except Exception as exc:
            self.log.warning(f"Balance init error: {exc}")

    def _maybe_log_debug(self, now_ns: int) -> None:
        if not self.config.log_signals:
            return
        if self.config.debug_log_interval_ms <= 0:
            return
        if now_ns - self._last_debug_ts_ns < self.config.debug_log_interval_ms * 1_000_000:
            return
        self._last_debug_ts_ns = now_ns

        if len(self._leader_mids) < 2:
            return

        bid_ask = None
        if self.follower_book is not None:
            best_bid = self.follower_book.best_bid_price()
            best_ask = self.follower_book.best_ask_price()
            if best_bid is not None and best_ask is not None:
                bid_ask = (float(best_bid), float(best_ask))
        if bid_ask is None and len(self._follower_mids) >= 1:
            mid = self._follower_mids[-1]
            bid_ask = (mid, mid)

        self.log.info(
            f"DEBUG | mom={self._momentum_bps(self._leader_mids):.2f}bps | "
            f"ofi={self._current_ofi:.3f} | bid={bid_ask[0]:.4f} | ask={bid_ask[1]:.4f}",
            color=LogColor.BLUE,
        )
