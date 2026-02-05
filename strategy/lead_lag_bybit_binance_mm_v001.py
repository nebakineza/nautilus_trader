"""Lead-Lag Market Maker using Binance (leader) vs Bybit (follower).

Version: v001
Strategy ID: lead_lag_bybit_binance_mm_v001
Venue: BYBIT SPOT (execution), BINANCE SPOT (data)
Asset Pair: Configurable (e.g., BTCUSDT)
Created: January 2026

Core idea:
- Use Binance mid-price as the leader signal.
- Cancel/avoid Bybit quotes when Binance moves away from Bybit (guard).
- Quote on Bybit only when leader/follower prices are aligned.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from strategy.inventory.risk_manager import InventoryRiskManager


class LeadLagMMConfig(StrategyConfig, frozen=True):
    """
    Configuration for ``LeadLagMM``.
    """

    leader_instrument_id: InstrumentId
    follower_instrument_id: InstrumentId
    order_qty: Decimal

    # Quoting
    spread_bps: Decimal = Decimal("3.0")
    quote_refresh_interval_ms: int = 50
    min_quote_lifetime_ms: int = 50
    min_requote_ticks: int = 1

    # Inventory management
    max_position_qty: Decimal = Decimal("0.001")
    max_total_exposure_usd: Decimal = Decimal("500")
    risk_aversion: float = 0.5
    volatility: float = 0.02
    inventory_time_horizon_secs: float = 120.0
    portfolio_id: str = "LEADLAG-PORTFOLIO"
    portfolio_skew_max_bps: Decimal = Decimal("5.0")

    # Guard (lead-lag protection)
    guard_threshold_bps: Decimal = Decimal("8.0")

    # Data quality
    max_data_staleness_ms: int = 5000


    # Book subscriptions
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 5

    # Execution
    post_only: bool = True
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None

    # Logging
    log_guard_events: bool = True
    log_leader_updates: bool = False

    # Recording (JSON lines compatible with BybitOrderBookLoader)
    record_orderbook: bool = False
    record_path: str = "data/ob_data_live"
    record_depth: int = 50
    record_snapshot_interval_secs: int = 10


class LeadLagMM(Strategy):
    """
    Lead-lag market maker that uses Binance as the leader price oracle
    to protect Bybit quotes from toxic flow.
    """

    _portfolio_positions: dict[str, dict[InstrumentId, Decimal]] = {}
    _portfolio_mids: dict[str, dict[InstrumentId, Decimal]] = {}

    def __init__(self, config: LeadLagMMConfig) -> None:
        super().__init__(config)

        self.leader_instrument: Instrument | None = None
        self.follower_instrument: Instrument | None = None

        self.leader_book: OrderBook | None = None
        self.follower_book: OrderBook | None = None

        self.leader_mid: Decimal | None = None
        self.follower_mid: Decimal | None = None

        self._bid_order: LimitOrder | None = None
        self._ask_order: LimitOrder | None = None
        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0

        self._tick_size: Decimal = Decimal("0")
        self._order_qty: Quantity | None = None
        self._last_quote_ts_ns: int = 0
        self._last_leader_ts_ns: int = 0
        self._last_follower_ts_ns: int = 0

        self._guard_block_buy: bool = False
        self._guard_block_sell: bool = False

        self.fill_count: int = 0
        self.toxic_events: int = 0
        self.guard_trigger_count: int = 0

        self._net_position: Decimal = Decimal("0")
        self._inventory_manager = InventoryRiskManager(
            max_position=self.config.max_position_qty,
            risk_aversion=self.config.risk_aversion,
            volatility=self.config.volatility,
            time_horizon=self.config.inventory_time_horizon_secs,
        )
        self._portfolio_id = self.config.portfolio_id
        self._portfolio_positions.setdefault(self._portfolio_id, {})
        self._portfolio_mids.setdefault(self._portfolio_id, {})

        self._record_leader_path: Path | None = None
        self._record_follower_path: Path | None = None
        self._last_snapshot_ts_ns: int = 0

        self.client_id = config.client_id

    def on_start(self) -> None:
        self.leader_instrument = self.cache.instrument(self.config.leader_instrument_id)
        if self.leader_instrument is None:
            self.log.error(f"Could not find leader instrument {self.config.leader_instrument_id}")
            self.stop()
            return

        self.follower_instrument = self.cache.instrument(self.config.follower_instrument_id)
        if self.follower_instrument is None:
            self.log.error(f"Could not find follower instrument {self.config.follower_instrument_id}")
            self.stop()
            return

        self.leader_book = OrderBook(
            instrument_id=self.leader_instrument.id,
            book_type=self.config.book_type,
        )
        self.follower_book = OrderBook(
            instrument_id=self.follower_instrument.id,
            book_type=self.config.book_type,
        )

        self._tick_size = self.follower_instrument.price_increment.as_decimal()
        self._order_qty = self.follower_instrument.make_qty(self.config.order_qty)

        self.subscribe_order_book_deltas(
            instrument_id=self.config.leader_instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )
        self.subscribe_order_book_deltas(
            instrument_id=self.config.follower_instrument_id,
            book_type=self.config.book_type,
            depth=self.config.book_depth,
        )

        if self.config.record_orderbook:
            self._init_recording_paths()

        self._sync_position_with_exchange()

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if deltas.instrument_id == self.config.leader_instrument_id:
            self._handle_leader_deltas(deltas)
            return

        if deltas.instrument_id == self.config.follower_instrument_id:
            self._handle_follower_deltas(deltas)
            return

    def _sync_position_with_exchange(self) -> None:
        if self.follower_instrument is None:
            return

        account = self.cache.account_for_venue(self.config.follower_instrument_id.venue)
        if account is None:
            return

        base_currency = self.follower_instrument.base_currency
        wallet_balance = account.balance_total(base_currency)
        if wallet_balance is None:
            return

        real_qty = wallet_balance.as_decimal() if hasattr(wallet_balance, "as_decimal") else Decimal(wallet_balance)
        drift = real_qty - self._net_position
        if drift != Decimal("0"):
            self.log.warning(
                f"START SYNC: Algo={self._net_position:.4f} vs Wallet={real_qty:.4f} -> Syncing.",
                LogColor.YELLOW,
            )
            self._net_position = real_qty

    def _handle_leader_deltas(self, deltas: OrderBookDeltas) -> None:
        if self.leader_book is None:
            return

        self.leader_book.apply_deltas(deltas)
        self._last_leader_ts_ns = self._event_ts_ns(deltas)
        self._record_deltas(deltas, is_leader=True)
        self._maybe_record_snapshot(is_leader=True)
        mid = self._book_mid(self.leader_book)
        if mid is None:
            return

        if self.leader_mid != mid and self.config.log_leader_updates:
            self.log.info(
                f"Leader mid update: {mid}",
                LogColor.CYAN,
            )
        self.leader_mid = mid
        self._update_guard()

    def _handle_follower_deltas(self, deltas: OrderBookDeltas) -> None:
        if self.follower_book is None:
            return

        self.follower_book.apply_deltas(deltas)
        self._last_follower_ts_ns = self._event_ts_ns(deltas)
        self._record_deltas(deltas, is_leader=False)
        self._maybe_record_snapshot(is_leader=False)
        mid = self._book_mid(self.follower_book)
        if mid is None:
            return

        self.follower_mid = mid
        self._portfolio_mids[self._portfolio_id][self.config.follower_instrument_id] = mid
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

        diff_bps = (self.leader_mid - self.follower_mid) / self.follower_mid * Decimal("10000")
        threshold = Decimal(self.config.guard_threshold_bps)

        block_buy = diff_bps < -threshold
        block_sell = diff_bps > threshold

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
                self.toxic_events += 1
                self.guard_trigger_count += 1
                self._cancel_side_orders(OrderSide.BUY)
            if block_sell:
                if self.config.log_guard_events:
                    self.log.warning(
                        f"TOXIC PUMP: diff_bps={diff_bps:.2f} -> cancel sells",
                        LogColor.RED,
                    )
                self.toxic_events += 1
                self.guard_trigger_count += 1
                self._cancel_side_orders(OrderSide.SELL)

    def _cancel_side_orders(self, side: OrderSide) -> None:
        if self.follower_instrument is None:
            return

        open_orders = self.cache.orders_open(
            instrument_id=self.follower_instrument.id,
            strategy_id=self.id,
        )
        for order in open_orders:
            if order.side == side:
                self.cancel_order(order, client_id=self.client_id)
                if side == OrderSide.BUY and self._bid_order:
                    if order.client_order_id == self._bid_order.client_order_id:
                        self._bid_order = None
                if side == OrderSide.SELL and self._ask_order:
                    if order.client_order_id == self._ask_order.client_order_id:
                        self._ask_order = None

    def _refresh_quotes(self) -> None:
        if self.follower_instrument is None or self.follower_mid is None:
            return

        if self._is_data_stale():
            return

        now_ns = self._now_ns()
        if now_ns - self._last_quote_ts_ns < self.config.quote_refresh_interval_ms * 1_000_000:
            return

        self._last_quote_ts_ns = now_ns

        if self._bid_order and self._bid_order.is_closed:
            self._bid_order = None
        if self._ask_order and self._ask_order.is_closed:
            self._ask_order = None

        spread_half = self.follower_mid * (Decimal(self.config.spread_bps) / Decimal("20000"))
        inventory_skew, bid_qty, ask_qty = self._inventory_adjustments()
        raw_bid = (self.follower_mid - spread_half) + inventory_skew
        raw_ask = (self.follower_mid + spread_half) + inventory_skew

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

        if self._guard_block_buy:
            if self._bid_order is not None:
                self.cancel_order(self._bid_order, client_id=self.client_id)
                self._bid_order = None
        else:
            self._place_or_replace(
                side=OrderSide.BUY,
                desired_price=desired_bid,
                now_ns=now_ns,
                desired_qty=bid_qty,
            )

        if self._guard_block_sell:
            if self._ask_order is not None:
                self.cancel_order(self._ask_order, client_id=self.client_id)
                self._ask_order = None
        else:
            self._place_or_replace(
                side=OrderSide.SELL,
                desired_price=desired_ask,
                now_ns=now_ns,
                desired_qty=ask_qty,
            )

    def _place_or_replace(
        self,
        side: OrderSide,
        desired_price: Decimal,
        now_ns: int,
        desired_qty: Quantity,
    ) -> None:
        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts_ns = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns

        if self._should_replace(order, order_ts_ns, desired_price, now_ns):
            if order is not None:
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
        order: LimitOrder | None,
        order_ts_ns: int,
        desired_price: Decimal,
        now_ns: int,
    ) -> bool:
        if order is None or order.is_closed:
            return True

        age_ms = (now_ns - order_ts_ns) / 1_000_000
        if age_ms < self.config.min_quote_lifetime_ms:
            return False

        current_price = order.price.as_decimal()
        ticks_delta = (abs(desired_price - current_price) / self._tick_size) if self._tick_size else Decimal("0")
        return ticks_delta >= self.config.min_requote_ticks

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

    def _has_recent_price(self, instrument_id: InstrumentId) -> bool:
        tick = self.cache.quote_tick(instrument_id) or self.cache.trade_tick(instrument_id)
        if tick is None:
            self.log.warning(
                f"GUARD: No price data for {instrument_id}. Skipping IOC/market action.",
                LogColor.YELLOW,
            )
            self.guard_trigger_count += 1
            return False
        ts_event = getattr(tick, "ts_event", None)
        if ts_event is None:
            self.log.warning(
                f"GUARD: Missing timestamp for {instrument_id}. Skipping IOC/market action.",
                LogColor.YELLOW,
            )
            self.guard_trigger_count += 1
            return False
        max_age_ns = self.config.max_data_staleness_ms * 1_000_000
        if self._now_ns() - int(ts_event) > max_age_ns:
            self.log.warning(
                f"GUARD: Stale price for {instrument_id}. Skipping IOC/market action.",
                LogColor.YELLOW,
            )
            self.guard_trigger_count += 1
            return False
        return True

    def on_event(self, event) -> None:
        if isinstance(event, OrderFilled | OrderRejected):
            if self._bid_order and event.client_order_id == self._bid_order.client_order_id:
                self._bid_order = None
            if self._ask_order and event.client_order_id == self._ask_order.client_order_id:
                self._ask_order = None
        if isinstance(event, OrderFilled):
            self.fill_count += 1
            if event.order_side == OrderSide.BUY:
                self._net_position += event.last_qty.as_decimal()
            else:
                self._net_position -= event.last_qty.as_decimal()
            self._portfolio_positions[self._portfolio_id][self.config.follower_instrument_id] = self._net_position

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
        if self._has_recent_price(self.config.follower_instrument_id):
            self.close_all_positions(self.config.follower_instrument_id, client_id=self.client_id)
        self._bid_order = None
        self._ask_order = None
        self.log.info("=" * 30 + " SESSION SUMMARY " + "=" * 30)
        self.log.info(f"Total Fills:      {self.fill_count}")
        self.log.info(f"Toxic Events:     {self.toxic_events} (Binance Signal Saved Us)")
        self.log.info(f"Guard Blocks:     {self.guard_trigger_count} (Data Gaps Avoided)")
        self.log.info("=" * 77)

    def _init_recording_paths(self) -> None:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        symbol = self.config.follower_instrument_id.symbol.value.split("-")[0]
        base_dir = Path(self.config.record_path) / f"{symbol}_Spot"
        base_dir.mkdir(parents=True, exist_ok=True)
        depth = self.config.record_depth

        self._record_follower_path = base_dir / f"{date_str}_{symbol}_bybit_ob{depth}.data"
        self._record_leader_path = base_dir / f"{date_str}_{symbol}_binance_ob{depth}.data"

    def _record_deltas(self, deltas: OrderBookDeltas, *, is_leader: bool) -> None:
        if not self.config.record_orderbook:
            return

        path = self._record_leader_path if is_leader else self._record_follower_path
        if path is None:
            return

        bids: list[list[str]] = []
        asks: list[list[str]] = []
        ts_ms = int(self._event_ts_ns(deltas) / 1_000_000)

        for delta in deltas.deltas:
            order = delta.order
            price = f"{order.price.as_decimal()}"
            size = "0" if getattr(delta.action, "name", "") == "DELETE" else f"{order.size.as_decimal()}"
            if order.side == OrderSide.BUY:
                bids.append([price, size])
            else:
                asks.append([price, size])

        if not bids and not asks:
            return

        record = {
            "type": "delta",
            "ts": ts_ms,
            "data": {"s": deltas.instrument_id.symbol.value, "b": bids, "a": asks},
        }

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _maybe_record_snapshot(self, *, is_leader: bool) -> None:
        if not self.config.record_orderbook:
            return

        if self.config.record_snapshot_interval_secs <= 0:
            return

        now_ns = self._now_ns()
        interval_ns = self.config.record_snapshot_interval_secs * 1_000_000_000
        if self._last_snapshot_ts_ns and now_ns - self._last_snapshot_ts_ns < interval_ns:
            return
        self._last_snapshot_ts_ns = now_ns

        book = self.leader_book if is_leader else self.follower_book
        path = self._record_leader_path if is_leader else self._record_follower_path
        if book is None or path is None:
            return

        depth = self.config.record_depth
        bids = [[f"{o.price.as_decimal()}", f"{o.size.as_decimal()}"] for o in list(book.bids())[:depth]]
        asks = [[f"{o.price.as_decimal()}", f"{o.size.as_decimal()}"] for o in list(book.asks())[:depth]]

        record = {
            "type": "snapshot",
            "ts": int(now_ns / 1_000_000),
            "data": {"s": book.instrument_id.symbol.value, "b": bids, "a": asks},
        }

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def on_reset(self) -> None:
        self._bid_order = None
        self._ask_order = None
        self._bid_order_ts_ns = 0
        self._ask_order_ts_ns = 0
        self._last_quote_ts_ns = 0
        self._last_leader_ts_ns = 0
        self._last_follower_ts_ns = 0
        self.leader_mid = None
        self.follower_mid = None
        self._guard_block_buy = False
        self._guard_block_sell = False
        self.fill_count = 0
        self.toxic_events = 0
        self.guard_trigger_count = 0
        self._net_position = Decimal("0")
        self._portfolio_positions[self._portfolio_id][self.config.follower_instrument_id] = self._net_position

    def _inventory_adjustments(self) -> tuple[Decimal, Quantity, Quantity]:
        if self.follower_mid is None:
            return Decimal("0"), self._order_qty, self._order_qty

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

        portfolio_skew_bps = self._portfolio_skew_bps()
        total_skew_bps = skew_bps + portfolio_skew_bps
        skew_px = self.follower_mid * (total_skew_bps / Decimal("10000"))

        bid_size, ask_size = self._inventory_manager.calculate_sizes(
            position=self._net_position,
            optimal_target=optimal_target,
            base_size=self.config.order_qty,
        )
        bid_qty = self.follower_instrument.make_qty(bid_size)
        ask_qty = self.follower_instrument.make_qty(ask_size)

        return skew_px, bid_qty, ask_qty

    def _portfolio_skew_bps(self) -> Decimal:
        mids = self._portfolio_mids.get(self._portfolio_id, {})
        positions = self._portfolio_positions.get(self._portfolio_id, {})

        if not mids or not positions:
            return Decimal("0")

        net_usd = Decimal("0")
        for instrument_id, position in positions.items():
            mid = mids.get(instrument_id)
            if mid is None:
                continue
            net_usd += position * mid

        if self.config.max_total_exposure_usd <= 0:
            return Decimal("0")

        exposure_ratio = net_usd / self.config.max_total_exposure_usd
        max_bps = self.config.portfolio_skew_max_bps
        skew_bps = -max_bps * exposure_ratio

        if skew_bps > max_bps:
            return max_bps
        if skew_bps < -max_bps:
            return -max_bps
        return skew_bps
