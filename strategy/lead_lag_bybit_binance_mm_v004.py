"""Lead-Lag Market Maker v004 - Latency Optimized (Gemini3 Critical Optimizations)."""

from __future__ import annotations

import gc
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
from nautilus_trader.model.objects import Currency, Quantity
from nautilus_trader.model.orders import Order, OrderList
from nautilus_trader.trading.strategy import Strategy

from strategy.inventory.risk_manager import InventoryRiskManager
from strategy.metrics.questdb_writer import QuestDbILPWriter


class LeadLagMMv4Config(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for ``LeadLagMMv4``."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId
    global_guard_id: InstrumentId | None = None

    order_qty: Decimal
    max_position_qty: Decimal

    # Quoting
    spread_bps: Decimal = Decimal("16.0")
    quote_refresh_interval_ms: int = 30
    quote_refresh_jitter_ms: int = 30
    quote_refresh_offset_ms: int = 0
    min_quote_lifetime_ms: int = 20
    min_requote_ticks: int = 1

    # Guard (lead-lag protection)
    guard_threshold_bps: Decimal = Decimal("10.0")
    guard_hysteresis_bps: Decimal = Decimal("5.0")

    # Global guard (BTC King)
    global_guard_threshold_bps: Decimal = Decimal("15.0")
    global_guard_window_ms: int = 500

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

    # Dynamic sizing
    min_order_qty: Decimal = Decimal("0.00001")
    max_order_qty: Decimal = Decimal("1.0")
    vol_scalar_threshold_bps: float = 5.0
    vol_scalar_reduction: Decimal = Decimal("0.5")
    liquidity_high_qty: Decimal = Decimal("5.0")
    liquidity_low_qty: Decimal = Decimal("0.1")
    liquidity_high_scalar: Decimal = Decimal("1.5")
    liquidity_low_scalar: Decimal = Decimal("0.5")

    # Edge-based requote
    edge_min_ratio: Decimal = Decimal("0.3")
    edge_max_ratio: Decimal = Decimal("0.8")

    # Local OFI (order flow imbalance)
    ofi_enabled: bool = False
    ofi_max_bps: Decimal = Decimal("3.0")
    ofi_depth: int | None = 10

    # Dynamic markout-based risk
    markout_window_ms: int = 1000
    markout_ema_alpha: Decimal = Decimal("0.2")
    markout_widen_bps: Decimal = Decimal("5.0")
    markout_max_spread_multiplier: Decimal = Decimal("2.0")
    log_markout_events: bool = False

    # Inventory skew caps
    internal_price_delta_limit: Decimal = Decimal("50.0")

    # Balance protection
    min_balance_ratio: Decimal = Decimal("0.95")
    min_quote_reserve_ratio: Decimal = Decimal("0.2")
    min_quote_reserve_usdt: Decimal = Decimal("0")

    # Fees & profit floor (bps)
    maker_fee_bps: Decimal = Decimal("7.5")
    min_profit_bps: Decimal = Decimal("5.0")

    # Metrics
    metrics_snapshot_interval_secs: int = 5

    # Equity protection
    max_drawdown_pct: Decimal = Decimal("0.10")

    # Logging
    log_guard_events: bool = True
    log_leader_updates: bool = False


class LeadLagMMv4(Strategy):
    """Lead-lag market maker v004 - Latency optimized with Gemini3 critical optimizations.
    
    Optimizations applied:
    1. Clock function caching - eliminates FFI overhead
    2. Float-based mid price calculations - 20-100x faster than Decimal in hot path
    3. Account object caching - removes dictionary lookup overhead
    4. Direct attribute access - removes getattr() overhead
    """

    def __init__(self, config: LeadLagMMv4Config) -> None:
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
        
        # OPTIMIZATION: Float caches for hot path (20-100x faster than Decimal)
        self.leader_mid_f: float = 0.0
        self.follower_mid_f: float = 0.0
        self.guard_threshold_f: float = float(config.guard_threshold_bps)

        self._bid_order_ts_ns: int = 0
        self._ask_order_ts_ns: int = 0
        self._bid_order: object | None = None
        self._ask_order: object | None = None

        self._tick_size: Decimal = Decimal("0")
        self._order_qty: Quantity | None = None
        self._last_quote_ts_ns: int = 0
        self._next_refresh_ts_ns: int = 0
        self._last_leader_ts_ns: int = 0
        self._last_guard_ts_ns: int = 0
        self._last_follower_ts_ns: int = 0
        self._last_sync_ts_ns: int = 0

        self._last_guard_mid: Decimal | None = None
        self._current_diff_bps: float = 0.0

        self._guard_block_buy: bool = False
        self._guard_block_sell: bool = False
        self._global_guard_active: bool = False

        self._net_position: Decimal = Decimal("0")
        self._markout_pending: list[tuple[int, OrderSide, Decimal]] = []
        self._markout_ema_bps: Decimal = Decimal("0")
        self._starting_equity: Decimal | None = None
        self._killswitch_triggered: bool = False
        self._wap_inventory: Decimal = Decimal("0")
        self._wap_price: Decimal = Decimal("0")
        self._realized_pnl: Decimal = Decimal("0")
        self._fees_paid: Decimal = Decimal("0")
        self._metrics = QuestDbILPWriter.from_env("LLMMv4")
        self._last_metrics_ts_ns: int = 0
        self._inventory_manager = InventoryRiskManager(
            max_position=self.config.max_position_qty,
            risk_aversion=self.config.risk_aversion,
            volatility=self.config.volatility,
            time_horizon=self.config.inventory_time_horizon_secs,
        )

        self._local_quote_budget: Decimal | None = None

        self.client_id = config.client_id
        
        # Cross-pair inventory coordinator (injected after init)
        self._inventory_coordinator = None
        
        # OPTIMIZATION 1: Clock function cache (eliminates try/except and attribute lookup)
        self._get_now_ns = None
        
        # OPTIMIZATION 3: Account object cache (eliminates dictionary lookup every refresh)
        self._cached_account = None

    def set_inventory_coordinator(self, coordinator) -> None:
        """Inject cross-pair inventory coordinator."""
        self._inventory_coordinator = coordinator
        if self.follower_instrument is not None:
            self.log.info(
                f"Cross-pair inventory coordinator injected for {self.follower_instrument.id.symbol.value}",
                color=LogColor.BLUE,
            )

    def on_start(self) -> None:
        gc.disable()
        
        # OPTIMIZATION 1: Cache clock function handle (removes try/except overhead)
        self._get_now_ns = self.clock.timestamp_ns
        
        self._next_refresh_ts_ns = self._now_ns() + (self.config.quote_refresh_offset_ms * 1_000_000)
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

        self._sync_position_with_exchange()
        self._sync_quote_budget()
        
        # OPTIMIZATION 3: Cache Account object (eliminates dictionary lookup)
        self._cached_account = self.cache.account_for_venue(self.config.follower_instrument_id.venue)
        if self._cached_account is None:
            accounts = self.cache.accounts()
            if accounts:
                self._cached_account = accounts[0]  # accounts() returns a list
        
        self._last_sync_ts_ns = self._now_ns()

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
        
        # OPTIMIZATION 2: Get float mid directly for hot path
        mid_f = self._book_mid_float(self.leader_book)
        if mid_f is None:
            return

        # Cache both for compatibility
        self.leader_mid = Decimal(str(mid_f))
        self.leader_mid_f = mid_f
        
        if self.config.log_leader_updates:
            self.log.info(f"Leader mid update: {self.leader_mid}", LogColor.CYAN)
            
        self._update_guard()

        # Follow the leader immediately (rate-limited)
        now_ns = self._now_ns()
        if now_ns >= self._next_refresh_ts_ns:
            next_refresh = self._refresh_quotes(now_ns)
            if next_refresh:
                self._next_refresh_ts_ns = next_refresh

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
        
        # OPTIMIZATION 2: Get float mid directly for hot path
        mid_f = self._book_mid_float(self.follower_book)
        if mid_f is None:
            return

        # Cache both for compatibility
        self.follower_mid = Decimal(str(mid_f))
        self.follower_mid_f = mid_f
        
        self._update_guard()
        now_ns = self._now_ns()
        self._update_markout(now_ns)

        if now_ns < self._next_refresh_ts_ns:
            return

        next_refresh_ts_ns = self._refresh_quotes(now_ns)
        if next_refresh_ts_ns:
            self._next_refresh_ts_ns = next_refresh_ts_ns

    def _book_mid(self, book: OrderBook) -> Decimal | None:
        """Legacy Decimal version for non-hot-path usage."""
        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            return None
        return Decimal((bid + ask) / 2)

    def _book_mid_float(self, book: OrderBook) -> float | None:
        """OPTIMIZATION 2: Float version for hot path (20-100x faster than Decimal)."""
        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            return None
        return (float(bid) + float(ask)) / 2.0

    def _update_guard(self) -> None:
        """OPTIMIZATION 2: Use cached float values for guard logic (critical hot path)."""
        if self.leader_mid_f == 0.0 or self.follower_mid_f == 0.0:
            return

        if self._is_data_stale():
            return

        # All float arithmetic - 20-100x faster than Decimal
        diff_bps = (self.leader_mid_f - self.follower_mid_f) / self.follower_mid_f * 10000.0
        self._current_diff_bps = diff_bps

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

    def _refresh_quotes(self, now_ns: int) -> int:
        if now_ns - self._last_sync_ts_ns > 5 * 1_000_000_000:
            self._sync_position_with_exchange()
            self._sync_quote_budget()
            self._last_sync_ts_ns = now_ns

        if now_ns - self._last_metrics_ts_ns > self.config.metrics_snapshot_interval_secs * 1_000_000_000:
            self._emit_account_snapshot(now_ns)
            self._last_metrics_ts_ns = now_ns

        if self.follower_instrument is None or self.follower_mid is None:
            return 0

        if self._check_killswitch():
            return 0

        # OPTIMIZATION 4: Direct attribute access instead of getattr
        pending_statuses = (OrderStatus.PENDING_CANCEL, OrderStatus.PENDING_UPDATE)
        if self._bid_order is not None and self._bid_order.status in pending_statuses:
            return 0
        if self._ask_order is not None and self._ask_order.status in pending_statuses:
            return 0

        if self._guard_block_buy or self._guard_block_sell or self._global_guard_active:
            return 0

        if self._is_data_stale():
            return 0

        jitter = random.randint(0, max(0, self.config.quote_refresh_jitter_ms)) * 1_000_000
        min_interval = (self.config.quote_refresh_interval_ms * 1_000_000) + jitter
        if now_ns - self._last_quote_ts_ns < min_interval:
            return 0

        self._last_quote_ts_ns = now_ns

        self._update_markout(now_ns)

        # OPTIMIZATION 4: Direct attribute access
        if self._bid_order and self._bid_order.is_closed:
            self._bid_order = None
        if self._ask_order and self._ask_order.is_closed:
            self._ask_order = None

        inventory_skew, bid_qty, ask_qty = self._inventory_adjustments()

        spread_bps = self._current_spread_bps()
        spread_half = self.follower_mid * (spread_bps / Decimal("20000"))
        if self.leader_mid is None:
            fair_price = self.follower_mid
        else:
            fair_price = (self.follower_mid * Decimal("0.1")) + (self.leader_mid * Decimal("0.9"))

        if self.config.ofi_enabled and self.follower_book is not None:
            ofi = self._calculate_ofi(self.follower_book)
            if ofi is not None:
                ofi_shift_bps = self.config.ofi_max_bps * ofi
                fair_price += self.follower_mid * (ofi_shift_bps / Decimal("10000"))
                if abs(ofi_shift_bps) > Decimal("1.0"):
                    self.log.info(
                        f"OFI ACTIVE: {self.config.follower_instrument_id.symbol} | "
                        f"OFI={ofi:.4f} | Shift={ofi_shift_bps:.2f} bps",
                        LogColor.CYAN,
                    )
        raw_bid = (fair_price - spread_half) + inventory_skew
        raw_ask = (fair_price + spread_half) + inventory_skew

        best_bid = self.follower_book.best_bid_price() if self.follower_book else None
        best_ask = self.follower_book.best_ask_price() if self.follower_book else None
        if best_bid is None or best_ask is None:
            return 0

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

        new_orders = []

        if bid_qty is not None:
             order = self._place_or_replace(OrderSide.BUY, desired_bid, fair_price, spread_bps, now_ns, bid_qty, defer_submit=True)
             if order:
                 new_orders.append(order)

        if ask_qty is not None:
             order = self._place_or_replace(OrderSide.SELL, desired_ask, fair_price, spread_bps, now_ns, ask_qty, defer_submit=True)
             if order:
                 new_orders.append(order)

        if new_orders:
            if len(new_orders) > 1:
                # Batch submit
                order_list = self.order_factory.create_list(new_orders)
                self.submit_order_list(order_list)
            else:
                self.submit_order(new_orders[0])

        return now_ns + min_interval

    def _place_or_replace(
        self,
        side: OrderSide,
        desired_price: Decimal,
        fair_price: Decimal,
        spread_bps: Decimal,
        now_ns: int,
        desired_qty: Quantity,
        defer_submit: bool = False,
    ) -> Order | None:
        if desired_qty.as_decimal() < self.config.min_order_qty:
            return None

        order = self._bid_order if side == OrderSide.BUY else self._ask_order
        order_ts_ns = self._bid_order_ts_ns if side == OrderSide.BUY else self._ask_order_ts_ns

        # OPTIMIZATION 4: Direct attribute access
        if order is not None and not order.is_closed:
            current_price = order.price.as_decimal()
            current_qty = order.quantity.as_decimal() if order.quantity else None
            if current_qty is not None:
                if desired_price == current_price and desired_qty.as_decimal() == current_qty:
                    return None

        if self._should_replace(
            order,
            order_ts_ns,
            desired_price,
            fair_price,
            spread_bps,
            desired_qty,
            now_ns,
        ):
            # OPTIMIZATION 4: Direct attribute access
            if order is not None and not order.is_closed:
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
                    return None

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

            if side == OrderSide.BUY:
                self._bid_order = new_order
                self._bid_order_ts_ns = now_ns
            else:
                self._ask_order = new_order
                self._ask_order_ts_ns = now_ns

            if defer_submit:
                return new_order
            
            self.submit_order(new_order)
        
        return None

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
        # OPTIMIZATION 4: Direct attribute access
        if order is None or order.is_closed:
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

            min_edge = spread_bps * self.config.edge_min_ratio
            max_edge = spread_bps * self.config.edge_max_ratio
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
        """Calculate Weighted Order Book Imbalance (WOBI) with exponential decay."""
        bid_levels = book.bids()
        ask_levels = book.asks()
        
        if not bid_levels or not ask_levels:
            return None
        
        # Use float for speed in hot path
        decay = 0.5
        w_bid_sum = 0.0
        w_ask_sum = 0.0
        
        depth = min(len(bid_levels), len(ask_levels), self.config.ofi_depth or 5)
        
        for i in range(depth):
            weight = 2.71828 ** (-decay * i)
            w_bid_sum += float(bid_levels[i].size()) * weight
            w_ask_sum += float(ask_levels[i].size()) * weight
        
        total = w_bid_sum + w_ask_sum
        if total <= 0.0:
            return None
        
        return Decimal(str((w_bid_sum - w_ask_sum) / total))

    def _current_spread_bps(self) -> Decimal:
        multiplier = Decimal("1")
        if self.config.markout_widen_bps > Decimal("0"):
            widen = self._markout_ema_bps / self.config.markout_widen_bps
            widen = min(widen, self.config.markout_max_spread_multiplier - Decimal("1"))
            if widen > Decimal("0"):
                multiplier += widen
        dynamic_spread = self.config.spread_bps * multiplier
        fee_floor = (self.config.maker_fee_bps * Decimal("2")) + self.config.min_profit_bps
        return max(dynamic_spread, fee_floor)

    def _update_markout(self, now_ns: int) -> None:
        if self.follower_mid is None or not self._markout_pending:
            return

        window_ns = self.config.markout_window_ms * 1_000_000
        remaining: list[tuple[int, OrderSide, Decimal]] = []
        for ts_ns, side, price in self._markout_pending:
            if now_ns - ts_ns < window_ns:
                remaining.append((ts_ns, side, price))
                continue

            if price <= Decimal("0"):
                continue

            if side == OrderSide.BUY:
                markout_bps = (self.follower_mid - price) / price * Decimal("10000")
            else:
                markout_bps = (price - self.follower_mid) / price * Decimal("10000")

            raw_val = -markout_bps
            
            alpha = self.config.markout_ema_alpha
            self._markout_ema_bps = (alpha * raw_val) + ((Decimal("1") - alpha) * self._markout_ema_bps)
            self._markout_ema_bps = max(Decimal("0"), self._markout_ema_bps)

            if self.config.log_markout_events:
                self.log.info(
                    f"Markout bps={markout_bps:.2f}, ema={self._markout_ema_bps:.2f}",
                    LogColor.MAGENTA,
                )

        self._markout_pending = remaining

    def _inventory_adjustments(self) -> tuple[Decimal, Quantity | None, Quantity | None]:
        if self.follower_mid is None or self._order_qty is None:
            return Decimal("0"), None, None

        optimal_target = self.config.max_position_qty / Decimal("2")
        skew_bps = Decimal(
            str(
                self._inventory_manager.calculate_skew(
                    position=self._net_position,
                    optimal_target=optimal_target,
                    mid_price=float(self.follower_mid),
                ),
            ),
        )
        limit = self.config.internal_price_delta_limit
        if limit > Decimal("0"):
            skew_bps = max(-limit, min(limit, skew_bps))
        skew_px = self.follower_mid * (skew_bps / Decimal("10000"))

        bid_size, ask_size = self._inventory_manager.calculate_sizes(
            position=self._net_position,
            optimal_target=optimal_target,
            base_size=self.config.order_qty,
        )

        # Apply cross-pair inventory coordinator scaling
        if self._inventory_coordinator is not None:
            quote_currency = Currency.from_str("USDT")
            
            if self._inventory_coordinator.should_skip_pair(
                self.follower_instrument.base_currency,
                quote_currency,
            ):
                return skew_px, None, None
            
            size_scalar = self._inventory_coordinator.get_size_scalar(
                self.follower_instrument.base_currency,
                quote_currency,
                bid_size,
            )
            
            bid_size = bid_size * size_scalar
            ask_size = ask_size * size_scalar

        bid_size = self._calculate_dynamic_size(bid_size, OrderSide.BUY)
        ask_size = self._calculate_dynamic_size(ask_size, OrderSide.SELL)

        bid_qty = self._apply_balance_limits(bid_size, OrderSide.BUY)
        ask_qty = self._apply_balance_limits(ask_size, OrderSide.SELL)

        return skew_px, bid_qty, ask_qty

    def _calculate_dynamic_size(self, base_qty: Decimal, side: OrderSide) -> Decimal:
        abs_diff = abs(self._current_diff_bps)
        
        if abs_diff < 2.0:
            vol_scalar = Decimal("1.0")
        elif abs_diff > 10.0:
            vol_scalar = Decimal("0.1")
        else:
            decay_factor = (abs_diff - 2.0) / 8.0
            vol_scalar = Decimal(str(1.0 - (0.9 * decay_factor)))

        depth_scalar = Decimal("1.0")
        if self.leader_book is not None:
            if side == OrderSide.BUY:
                best_bid_size = self.leader_book.best_bid_size()
                if best_bid_size is not None:
                    best_bid_qty = best_bid_size.as_decimal()
                    if best_bid_qty > self.config.liquidity_high_qty:
                        depth_scalar = self.config.liquidity_high_scalar
                    elif best_bid_qty < self.config.liquidity_low_qty:
                        depth_scalar = self.config.liquidity_low_scalar
            elif side == OrderSide.SELL:
                best_ask_size = self.leader_book.best_ask_size()
                if best_ask_size is not None:
                    best_ask_qty = best_ask_size.as_decimal()
                    if best_ask_qty > self.config.liquidity_high_qty:
                        depth_scalar = self.config.liquidity_high_scalar
                    elif best_ask_qty < self.config.liquidity_low_qty:
                        depth_scalar = self.config.liquidity_low_scalar

        final_size = base_qty * vol_scalar * depth_scalar
        final_size = min(final_size, self.config.max_order_qty)
        min_floor = self.config.order_qty * Decimal("0.2")
        final_size = max(final_size, min_floor, self.config.min_order_qty)
        return final_size

    def _apply_balance_limits(self, base_qty: Decimal, side: OrderSide) -> Quantity | None:
        if self.follower_instrument is None:
            return None

        if self.follower_mid is None:
            return None

        base_currency = self.follower_instrument.base_currency
        quote_currency = self.follower_instrument.quote_currency

        if side == OrderSide.BUY:
            quote_balance = self._get_available_balance(quote_currency)
            if self._local_quote_budget is None:
                budget = quote_balance
            else:
                budget = max(self._local_quote_budget, quote_balance)
            reserve_ratio = budget * self.config.min_quote_reserve_ratio
            reserve_abs = self.config.min_quote_reserve_usdt
            reserve = max(reserve_ratio, reserve_abs)
            available = max(budget - reserve, Decimal("0"))
            max_buy = (available * self.config.min_balance_ratio) / self.follower_mid
            qty = min(base_qty, max_buy)
        else:
            base_balance = self._get_available_balance(base_currency)
            max_sell = base_balance * self.config.min_balance_ratio
            qty = min(base_qty, max_sell)

        if qty < self.config.min_order_qty:
            return None

        if qty <= Decimal("0"):
            return None

        return self.follower_instrument.make_qty(qty)

    def _calculate_total_equity(self) -> Decimal:
        if self.follower_instrument is None:
            return Decimal("0")

        quote_balance = self._get_available_balance(self.follower_instrument.quote_currency)
        base_balance = self._get_available_balance(self.follower_instrument.base_currency)
        mid = self.follower_mid if self.follower_mid is not None else Decimal("0")
        return quote_balance + (base_balance * mid)

    def _check_killswitch(self) -> bool:
        if self._killswitch_triggered:
            return True

        if self._starting_equity is None:
            equity = self._calculate_total_equity()
            if equity > Decimal("0"):
                self._starting_equity = equity
                self.log.info(
                    f"KILLSWITCH ARMED: Starting Equity = {self._starting_equity} USDT",
                    LogColor.YELLOW,
                )
            return False

        current_equity = self._calculate_total_equity()
        if current_equity <= Decimal("0"):
            return False

        drawdown = (self._starting_equity - current_equity) / self._starting_equity
        if drawdown > self.config.max_drawdown_pct:
            self.log.error(
                f"KILLSWITCH TRIGGERED: Drawdown {drawdown:.2%} > Limit {self.config.max_drawdown_pct:.2%}",
                LogColor.RED,
            )
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
            self._killswitch_triggered = True
            self.stop()
            return True

        return False

    def _get_available_balance(self, currency: Currency) -> Decimal:
        """OPTIMIZATION 3: Use cached account object instead of dictionary lookup."""
        if self._cached_account is None:
            return Decimal("0")

        balance = self._cached_account.balance_free(currency)
        if balance is None:
            return Decimal("0")
        return balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)

    def _sync_quote_budget(self) -> None:
        if self.follower_instrument is None:
            return

        if self._cached_account is None:
            return

        quote_currency = self.follower_instrument.quote_currency
        quote_balance = self._cached_account.balance_total(quote_currency)
        if quote_balance is None:
            return

        self._local_quote_budget = (
            quote_balance.as_decimal() if hasattr(quote_balance, "as_decimal") else Decimal(quote_balance)
        )

    def _sync_position_with_exchange(self) -> None:
        """Force-syncs the algorithm's inventory tracking with the actual wallet balance."""
        if self.follower_instrument is None:
            return

        if self._cached_account is None:
            return

        base_currency = self.follower_instrument.base_currency
        wallet_balance = self._cached_account.balance_total(base_currency)
        if wallet_balance is None:
            return

        real_qty = wallet_balance.as_decimal() if hasattr(wallet_balance, "as_decimal") else Decimal(wallet_balance)
        drift = real_qty - self._net_position
        if abs(drift) > self.config.min_order_qty:
            self.log.warning(
                f"DRIFT DETECTED: Algo={self._net_position:.4f} vs Wallet={real_qty:.4f} -> Syncing.",
                LogColor.YELLOW,
            )
            self._net_position = real_qty

    def _emit_account_snapshot(self, now_ns: int) -> None:
        if self.follower_instrument is None:
            return

        if self._cached_account is None:
            return

        base_currency = self.follower_instrument.base_currency
        quote_currency = self.follower_instrument.quote_currency

        base_balance = self._cached_account.balance_total(base_currency)
        quote_balance = self._cached_account.balance_total(quote_currency)
        if base_balance is None or quote_balance is None:
            return

        base_qty = base_balance.as_decimal() if hasattr(base_balance, "as_decimal") else Decimal(base_balance)
        quote_qty = quote_balance.as_decimal() if hasattr(quote_balance, "as_decimal") else Decimal(quote_balance)
        mid = self.follower_mid if self.follower_mid is not None else Decimal("0")
        equity = quote_qty + (base_qty * mid)

        self._metrics.send(
            table="live_account_snapshot",
            tags={
                "strategy": "LLMMv4",
                "venue": self.config.follower_instrument_id.venue.value,
                "symbol": self.config.follower_instrument_id.symbol.value,
            },
            fields={
                "base_qty": base_qty,
                "quote_qty": quote_qty,
                "mid": mid,
                "equity": equity,
                "equity_usd": equity,
                "net_position": self._net_position,
            },
            ts_ns=now_ns,
        )

    def on_event(self, event) -> None:
        if hasattr(event, "last_qty") and hasattr(event, "order_side"):
            if event.order_side == OrderSide.BUY:
                self._net_position += event.last_qty.as_decimal()
            elif event.order_side == OrderSide.SELL:
                self._net_position -= event.last_qty.as_decimal()

            price = None
            if hasattr(event, "last_px") and event.last_px is not None:
                price = event.last_px.as_decimal()
            elif hasattr(event, "price") and event.price is not None:
                price = event.price.as_decimal()

            if price is not None:
                if self._local_quote_budget is not None:
                    notional = event.last_qty.as_decimal() * price
                    if event.order_side == OrderSide.BUY:
                        self._local_quote_budget = max(self._local_quote_budget - notional, Decimal("0"))
                    elif event.order_side == OrderSide.SELL:
                        self._local_quote_budget += notional

                commission = getattr(event, "commission", None)
                commission_val = commission.as_decimal() if hasattr(commission, "as_decimal") else commission
                self._update_wap_ledger(
                    event.order_side,
                    price,
                    event.last_qty.as_decimal(),
                    Decimal(str(commission_val)) if commission_val is not None else Decimal("0"),
                )

                ts_event = getattr(event, "ts_event", None)
                ts_ns = self._event_ts_ns_from_event(ts_event)
                self._markout_pending.append((ts_ns, event.order_side, price))

                liquidity_side = getattr(event, "liquidity_side", None)
                self._metrics.send(
                    table="live_fills",
                    tags={
                        "strategy": "LLMMv4",
                        "venue": self.config.follower_instrument_id.venue.value,
                        "symbol": self.config.follower_instrument_id.symbol.value,
                        "side": event.order_side.name,
                        "liquidity": getattr(liquidity_side, "name", None),
                    },
                    fields={
                        "qty": event.last_qty.as_decimal(),
                        "price": price,
                        "commission": commission_val,
                    },
                    ts_ns=ts_ns,
                )

    def _update_wap_ledger(
        self,
        side: OrderSide,
        price: Decimal,
        qty: Decimal,
        fee: Decimal,
    ) -> None:
        self._fees_paid += fee
        self._realized_pnl -= fee

        if side == OrderSide.BUY:
            if self._wap_inventory >= 0:
                total_cost = (self._wap_inventory * self._wap_price) + (qty * price)
                self._wap_inventory += qty
                if self._wap_inventory != 0:
                    self._wap_price = total_cost / self._wap_inventory
            else:
                remaining_short = abs(self._wap_inventory)
                if qty <= remaining_short:
                    pnl = (self._wap_price - price) * qty
                    self._realized_pnl += pnl
                    self._wap_inventory += qty
                else:
                    pnl = (self._wap_price - price) * remaining_short
                    self._realized_pnl += pnl
                    excess_qty = qty - remaining_short
                    self._wap_inventory = excess_qty
                    self._wap_price = price
        elif side == OrderSide.SELL:
            if self._wap_inventory <= 0:
                total_cost = (abs(self._wap_inventory) * self._wap_price) + (qty * price)
                self._wap_inventory -= qty
                if self._wap_inventory != 0:
                    self._wap_price = total_cost / abs(self._wap_inventory)
            else:
                if qty <= self._wap_inventory:
                    pnl = (price - self._wap_price) * qty
                    self._realized_pnl += pnl
                    self._wap_inventory -= qty
                else:
                    pnl = (price - self._wap_price) * self._wap_inventory
                    self._realized_pnl += pnl
                    excess_qty = qty - self._wap_inventory
                    self._wap_inventory = -excess_qty
                    self._wap_price = price

    def _get_realized_pnl(self) -> Decimal:
        return self._realized_pnl

    def _event_ts_ns_from_event(self, ts_event) -> int:
        if ts_event is None:
            return self._now_ns()
        ts_value = int(ts_event)
        if ts_value < 1_000_000_000_000:
            return ts_value * 1_000_000_000
        if ts_value < 1_000_000_000_000_000:
            return ts_value * 1_000_000
        return ts_value

    def _now_ns(self) -> int:
        """OPTIMIZATION 1: Direct call to cached function (eliminates try/except overhead)."""
        return self._get_now_ns()

    def _event_ts_ns(self, deltas: OrderBookDeltas) -> int:
        ts_event = getattr(deltas, "ts_event", None)
        if ts_event is None:
            return self._now_ns()
        ts_value = int(ts_event)
        if ts_value < 1_000_000_000_000:
            return ts_value * 1_000_000_000
        if ts_value < 1_000_000_000_000_000:
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
