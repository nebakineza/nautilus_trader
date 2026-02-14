"""HFT Multi-Signal Scalper v001.

Combines:
A) Order Book Imbalance (OBI) pressure trades
B) Wick/flush mean-reversion (liquidation proxy)
C) Lead-lag correlation (leader vs follower)
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


@dataclass(frozen=True)
class HftMultiSignalConfig(StrategyConfig, kw_only=True):
    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId | None = None
    futures_instrument_id: InstrumentId | None = None

    # Order book
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 20
    obi_window_bps: float = 10.0
    obi_ratio_threshold: float = 0.65
    obi_min_notional_usd: float = 50_000.0

    # Wick/flush detection (liquidation proxy)
    wick_window_ms: int = 1500
    wick_drop_bps: float = 40.0
    wick_reversion_bps: float = 20.0
    wick_hold_ms: int = 15000

    # Lead-lag
    leadlag_move_bps: float = 20.0
    leadlag_lag_bps: float = 8.0
    leadlag_cooldown_ms: int = 500

    # Execution
    order_qty: float = 1.0
    max_position_qty: float = 10.0
    min_order_value_usd: float = 5.50
    time_in_force_taker: TimeInForce = TimeInForce.IOC
    time_in_force_maker: TimeInForce = TimeInForce.GTC
    post_only: bool = True

    # Risk
    take_profit_bps: float = 15.0
    stop_loss_bps: float = 12.0
    max_hold_ms: int = 15000
    min_edge_bps: float = 5.0
    long_only: bool = True

    # Logging
    log_signals: bool = True


class HftMultiSignalScalper(Strategy):
    def __init__(self, config: HftMultiSignalConfig) -> None:
        super().__init__(config)
        self.follower_id = config.follower_instrument_id
        self.leader_id = config.leader_instrument_id
        self.futures_id = config.futures_instrument_id

        self.follower_instrument: Instrument | None = None
        self.follower_book: OrderBook | None = None

        self._leader_mids: deque[float] = deque(maxlen=3)
        self._follower_mids: deque[float] = deque(maxlen=3)
        self._futures_mids: deque[float] = deque(maxlen=3)

        self._obi_ratio: float = 0.0
        self._obi_notional: float = 0.0

        self._entry_order: Order | None = None
        self._exit_order: Order | None = None
        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._entry_ts_ns: int = 0
        self._tp_bps_override: float | None = None

        self._available_base: float = 0.0
        self._available_quote: float = 0.0

        self._recent_mids: deque[tuple[int, float]] = deque(maxlen=100)
        self._cooldown_until_ns: int = 0

    def on_start(self) -> None:
        self.follower_instrument = self.cache.instrument(self.follower_id)
        if self.follower_instrument is None:
            self.log.error(f"Instrument {self.follower_id} not found")
            self.stop()
            return

        self.subscribe_order_book_deltas(self.follower_id, self.config.book_type, self.config.book_depth)
        self.subscribe_quote_ticks(self.follower_id)
        if self.leader_id is not None:
            self.subscribe_quote_ticks(self.leader_id)
        if self.futures_id is not None:
            self.subscribe_quote_ticks(self.futures_id)

        self._initialize_balance()

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if deltas.instrument_id != self.follower_id:
            return
        book = self.cache.order_book(deltas.instrument_id)
        if book is None:
            return
        self.follower_book = book
        self._update_obi(book)
        self._run_logic()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
        if tick.instrument_id == self.follower_id:
            self._follower_mids.append(mid)
            self._recent_mids.append((self.clock.timestamp_ns(), mid))
        elif self.leader_id and tick.instrument_id == self.leader_id:
            self._leader_mids.append(mid)
        elif self.futures_id and tick.instrument_id == self.futures_id:
            self._futures_mids.append(mid)

        self._run_logic()

    def on_order_filled(self, event: OrderFilled) -> None:
        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)

        if event.order_side == OrderSide.BUY:
            self._available_base += fill_qty
            self._available_quote -= fill_price * fill_qty
        else:
            self._available_base -= fill_qty
            self._available_quote += fill_price * fill_qty

        if self._position_side is None:
            self._position_side = event.order_side
            self._entry_price = fill_price
            self._entry_ts_ns = self.clock.timestamp_ns()
            self._entry_order = None
            if self.config.log_signals:
                self.log.info(f"ENTRY FILLED {event.order_side.name} @ {fill_price:.4f}", LogColor.GREEN)
        else:
            self._position_side = None
            self._entry_price = 0.0
            self._entry_ts_ns = 0
            self._exit_order = None
            self._tp_bps_override = None
            self._cooldown_until_ns = self.clock.timestamp_ns() + (self.config.leadlag_cooldown_ms * 1_000_000)
            if self.config.log_signals:
                self.log.info(f"EXIT FILLED {event.order_side.name} @ {fill_price:.4f}", LogColor.MAGENTA)

    # --- Core logic ---
    def _run_logic(self) -> None:
        if self.follower_book is None or self.follower_instrument is None:
            return
        now_ns = self.clock.timestamp_ns()
        if now_ns < self._cooldown_until_ns:
            return

        if self._position_side is not None:
            self._check_exit(now_ns)
            return

        if self._entry_order is not None:
            return

        # Priority: Wick -> LeadLag -> OBI
        if self._wick_signal():
            self._enter(OrderSide.BUY, tp_override=self.config.wick_reversion_bps, use_taker=True)
            return

        leadlag_side = self._leadlag_signal()
        if leadlag_side is not None:
            self._enter(leadlag_side, use_taker=True)
            return

        obi_side = self._obi_signal()
        if obi_side is not None:
            self._enter(obi_side, use_taker=False)

    # --- Signals ---
    def _obi_signal(self) -> OrderSide | None:
        if self._obi_notional < self.config.obi_min_notional_usd:
            return None
        if self._obi_ratio >= self.config.obi_ratio_threshold:
            return OrderSide.BUY
        if self._obi_ratio <= -self.config.obi_ratio_threshold:
            return OrderSide.SELL if not self.config.long_only else None
        return None

    def _wick_signal(self) -> bool:
        if not self._recent_mids:
            return False
        now_ns = self.clock.timestamp_ns()
        window_ns = self.config.wick_window_ms * 1_000_000
        recent = [p for (ts, p) in self._recent_mids if now_ns - ts <= window_ns]
        if len(recent) < 3:
            return False
        high = max(recent)
        last = recent[-1]
        drop_bps = (high - last) / high * BPS_MULTIPLIER
        if drop_bps < self.config.wick_drop_bps:
            return False
        # Futures confirmation (proxy)
        if self.futures_id and len(self._futures_mids) >= 2:
            fut_move = (self._futures_mids[-2] - self._futures_mids[-1]) / self._futures_mids[-2] * BPS_MULTIPLIER
            if fut_move < (self.config.wick_drop_bps / 2):
                return False
        return True

    def _leadlag_signal(self) -> OrderSide | None:
        if self.leader_id is None:
            return None
        if len(self._leader_mids) < 2 or len(self._follower_mids) < 2:
            return None
        leader_move_bps = (self._leader_mids[-1] - self._leader_mids[-2]) / self._leader_mids[-2] * BPS_MULTIPLIER
        follower_move_bps = (self._follower_mids[-1] - self._follower_mids[-2]) / self._follower_mids[-2] * BPS_MULTIPLIER
        lag = leader_move_bps - follower_move_bps
        if abs(leader_move_bps) >= self.config.leadlag_move_bps and abs(lag) >= self.config.leadlag_lag_bps:
            return OrderSide.BUY if leader_move_bps > 0 else OrderSide.SELL
        return None

    # --- Execution ---
    def _enter(self, side: OrderSide, tp_override: float | None = None, use_taker: bool = False) -> None:
        if self.follower_book is None or self.follower_instrument is None:
            return
        best_bid = self.follower_book.best_bid_price()
        best_ask = self.follower_book.best_ask_price()
        if best_bid is None or best_ask is None:
            return
        price = float(best_ask) if side == OrderSide.BUY else float(best_bid)
        notional = price * self.config.order_qty
        if notional < self.config.min_order_value_usd:
            return
        if side == OrderSide.SELL and self.config.long_only:
            return

        if (tp_override or self.config.take_profit_bps) < self.config.min_edge_bps:
            return

        order = self.order_factory.limit(
            instrument_id=self.follower_id,
            order_side=side,
            quantity=self.follower_instrument.make_qty(self.config.order_qty),
            price=self.follower_instrument.make_price(price),
            time_in_force=self.config.time_in_force_taker if use_taker else self.config.time_in_force_maker,
            post_only=False if use_taker else self.config.post_only,
        )
        self.submit_order(order)
        self._entry_order = order
        self._tp_bps_override = tp_override
        if self.config.log_signals:
            self.log.info(f"ENTRY {side.name} @ {price:.4f} via {'TAKER' if use_taker else 'MAKER'}", LogColor.YELLOW)

    def _check_exit(self, now_ns: int) -> None:
        if self.follower_book is None:
            return
        best_bid = self.follower_book.best_bid_price()
        best_ask = self.follower_book.best_ask_price()
        if best_bid is None or best_ask is None:
            return
        price = float(best_bid) if self._position_side == OrderSide.BUY else float(best_ask)
        pnl_bps = (price - self._entry_price) / self._entry_price * BPS_MULTIPLIER
        if self._position_side == OrderSide.SELL:
            pnl_bps = -pnl_bps

        tp_bps = self._tp_bps_override or self.config.take_profit_bps
        held_ms = (now_ns - self._entry_ts_ns) / 1_000_000

        if pnl_bps >= tp_bps or pnl_bps <= -self.config.stop_loss_bps or held_ms >= self.config.max_hold_ms:
            exit_side = OrderSide.SELL if self._position_side == OrderSide.BUY else OrderSide.BUY
            order = self.order_factory.limit(
                instrument_id=self.follower_id,
                order_side=exit_side,
                quantity=self.follower_instrument.make_qty(self.config.order_qty),
                price=self.follower_instrument.make_price(price),
                time_in_force=self.config.time_in_force_taker,
                post_only=False,
            )
            self.submit_order(order)
            self._exit_order = order

    # --- OBI ---
    def _update_obi(self, book: OrderBook) -> None:
        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        if best_bid is None or best_ask is None:
            return
        mid = (float(best_bid) + float(best_ask)) / 2
        window = mid * (self.config.obi_window_bps / BPS_MULTIPLIER)
        bid_vol = 0.0
        ask_vol = 0.0
        for level in book.bids():
            price = float(level.price())
            if mid - price > window:
                break
            bid_vol += price * float(level.size())
        for level in book.asks():
            price = float(level.price())
            if price - mid > window:
                break
            ask_vol += price * float(level.size())
        total = bid_vol + ask_vol
        if total <= 0:
            return
        self._obi_ratio = (bid_vol - ask_vol) / total
        self._obi_notional = abs(bid_vol - ask_vol)

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
        except Exception as exc:
            self.log.warning(f"Balance init error: {exc}")
