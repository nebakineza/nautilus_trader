"""
Triangular Arbitrage Strategy v002 (Multi-Path).
Implements multiple concurrent 3-leg arbitrage cycles.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import List, Dict, Optional
from dataclasses import dataclass
import time

from nautilus_trader.model.events import OrderFilled, OrderRejected

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import OrderSide, TimeInForce, OrderType, BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

@dataclass
class ArbPathConfig:
    id: str
    leg1_instrument_id: InstrumentId
    leg2_instrument_id: InstrumentId
    leg3_instrument_id: InstrumentId
    leg1_side: OrderSide
    leg2_side: OrderSide
    leg3_side: OrderSide
    order_qty: Decimal 

class TriangularArbConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for Multi-Path Triangular Arbitrage."""
    
    paths: List[ArbPathConfig]
    min_profit_bps: Decimal = Decimal("5.0")
    min_profit_usdt: Decimal | None = None
    fee_pct: Decimal = Decimal("0.075")
    max_slippage_bps: Decimal = Decimal("2.0")
    sequential_execution: bool = True
    max_data_staleness_ms: int = 2000
    reject_cooldown_ms: int = 5000
    halt_on_reject: bool = True
    require_synced_books: bool = False
    trend_bias: bool = True
    trend_ema_period: int = 20
    trend_epsilon_bps: Decimal = Decimal("1.0")
    
    # Safety
    max_inflight_per_path: int = 1


@dataclass
class CycleState:
    cycle_id: int
    path_id: str
    leg1_qty: Decimal
    leg2_qty: Decimal
    leg3_qty: Decimal
    leg1_filled: Decimal = Decimal("0")
    leg2_filled: Decimal = Decimal("0")
    leg3_filled: Decimal = Decimal("0")
    leg1_order_id: str | None = None
    leg2_order_id: str | None = None
    leg3_order_id: str | None = None

class TriangularArb(Strategy):
    """
    Executes multiple triangular arbitrage loops simultaneously.
    """

    def __init__(self, config: TriangularArbConfig) -> None:
        super().__init__(config)

        # Map InstrumentId -> List[PathIDs] for fast lookup on tick
        self.instrument_to_paths: Dict[InstrumentId, List[str]] = {}
        
        # Map PathID -> PathConfig
        self.paths: Dict[str, ArbPathConfig] = {}
        
        # Map InstrumentId -> OrderBook
        self.books: Dict[InstrumentId, OrderBook] = {}
        
        # State
        self.path_inflight: Dict[str, int] = {}
        self._path_logged_ready: Dict[str, bool] = {}
        self._cycle_id = 0
        self._cycles: Dict[int, CycleState] = {}
        self._order_to_cycle: Dict[str, tuple[int, int]] = {}
        self._last_book_ts_ns: Dict[InstrumentId, int] = {}
        self._last_event_ts_ns: int = 0
        self._path_pause_until_ns: Dict[str, int] = {}
        self._net_positions: Dict[InstrumentId, Decimal] = {}
        self._ema_mid: Dict[InstrumentId, Decimal] = {}
        self._fills: int = 0
        self._rejections: int = 0
        self._cycles_started: int = 0
        self._cycles_completed: int = 0

    def on_start(self) -> None:
        for path in self.config.paths:
            self.paths[path.id] = path
            self.path_inflight[path.id] = 0
            
            # Register instruments
            for inst_id in [path.leg1_instrument_id, path.leg2_instrument_id, path.leg3_instrument_id]:
                if inst_id not in self.instrument_to_paths:
                    self.instrument_to_paths[inst_id] = []
                    self.subscribe_order_book_deltas(inst_id)
                    # Initialize book
                    self.books[inst_id] = OrderBook(inst_id, BookType.L2_MBP)
                
                self.instrument_to_paths[inst_id].append(path.id)

        self.log.info(f"TriangularArb started with {len(self.paths)} paths.")

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        self._last_event_ts_ns = self._event_ts_ns(deltas)
        # Update book
        if deltas.instrument_id in self.books:
            self.books[deltas.instrument_id].apply_deltas(deltas)
            self._last_book_ts_ns[deltas.instrument_id] = self._last_event_ts_ns
            self._update_mid_ema(deltas.instrument_id)
            
        # Trigger logic for dependent paths
        if deltas.instrument_id in self.instrument_to_paths:
            for path_id in self.instrument_to_paths[deltas.instrument_id]:
                self._check_path(path_id, self._last_event_ts_ns)

    def _check_path(self, path_id: str, now_ns: int) -> None:
        pause_until = self._path_pause_until_ns.get(path_id, 0)
        if now_ns < pause_until:
            return

        if self.path_inflight[path_id] >= self.config.max_inflight_per_path:
            return

        path = self.paths[path_id]

        if self._has_open_exposure(path):
            return

        if not self._books_time_aligned(path, now_ns):
            return

        if self.config.trend_bias and not self._trend_allows(path):
            return

        if not self._exchange_books_ready(path):
            return
        
        # Get prices
        p1 = self._get_price(path.leg1_instrument_id, path.leg1_side)
        p2 = self._get_price(path.leg2_instrument_id, path.leg2_side)
        p3 = self._get_price(path.leg3_instrument_id, path.leg3_side)
        
        if not all([p1, p2, p3]):
            # Waiting for all three legs to have valid prices
            return

        qty1 = path.order_qty
        qty2 = qty1
        qty3 = qty2 * p2

        avg1 = self._avg_price_for_qty(path.leg1_instrument_id, path.leg1_side, qty1, now_ns)
        avg2 = self._avg_price_for_qty(path.leg2_instrument_id, path.leg2_side, qty2, now_ns)
        avg3 = self._avg_price_for_qty(path.leg3_instrument_id, path.leg3_side, qty3, now_ns)
        if not all([avg1, avg2, avg3]):
            return

        avg_p1, bbo1 = avg1
        avg_p2, bbo2 = avg2
        avg_p3, bbo3 = avg3

        if not self._slippage_ok(path.leg1_side, avg_p1, bbo1):
            return
        if not self._slippage_ok(path.leg2_side, avg_p2, bbo2):
            return
        if not self._slippage_ok(path.leg3_side, avg_p3, bbo3):
            return

        factor1 = Decimal(1) / avg_p1 if path.leg1_side == OrderSide.BUY else avg_p1
        factor2 = Decimal(1) / avg_p2 if path.leg2_side == OrderSide.BUY else avg_p2
        factor3 = Decimal(1) / avg_p3 if path.leg3_side == OrderSide.BUY else avg_p3

        gross_return = factor1 * factor2 * factor3
        costs = Decimal(1) - (Decimal(1) - self.config.fee_pct / 100) ** 3
        net_return = gross_return - costs

        net_bps = (net_return - 1) * 10000

        if self.config.min_profit_usdt is not None:
            notional_usdt = self._estimate_notional_usdt(path, avg_p1)
            if notional_usdt is not None:
                net_profit_usdt = (net_return - 1) * notional_usdt
                if net_profit_usdt >= self.config.min_profit_usdt:
                    self.log.info(
                        f"ARB {path.id}: net_usdt={net_profit_usdt:.5f} (min={self.config.min_profit_usdt}) "
                        f"Prices: {avg_p1}, {avg_p2}, {avg_p3}"
                    )
                    self._execute_cycle(path, avg_p1, avg_p2, avg_p3)
                return

        if net_bps > self.config.min_profit_bps:
            self.log.info(f"ARB {path.id}: {net_bps:.2f} bps. Prices: {avg_p1}, {avg_p2}, {avg_p3}")
            self._execute_cycle(path, avg_p1, avg_p2, avg_p3)

    def _get_price(self, instrument_id: InstrumentId, side: OrderSide) -> Optional[Decimal]:
        book = self.books.get(instrument_id)
        if not book: return None
        price = book.best_ask_price() if side == OrderSide.BUY else book.best_bid_price()
        return price.as_decimal() if price else None

    def _exchange_books_ready(self, path: ArbPathConfig) -> bool:
        for inst_id in [path.leg1_instrument_id, path.leg2_instrument_id, path.leg3_instrument_id]:
            book = self.cache.order_book(inst_id)
            if not book or not book.spread():
                return False
            bid_size = book.best_bid_size()
            ask_size = book.best_ask_size()
            if (bid_size is None or bid_size <= 0) or (ask_size is None or ask_size <= 0):
                return False
        return True

    def _update_mid_ema(self, instrument_id: InstrumentId) -> None:
        book = self.books.get(instrument_id)
        if not book:
            return

        mid = book.midpoint()
        if mid is None or mid <= 0:
            return

        mid_dec = Decimal(str(mid))
        prev = self._ema_mid.get(instrument_id)
        alpha = Decimal("2") / (Decimal(self.config.trend_ema_period) + Decimal("1"))
        if prev is None:
            self._ema_mid[instrument_id] = mid_dec
        else:
            self._ema_mid[instrument_id] = (mid_dec - prev) * alpha + prev

    def _trend_allows(self, path: ArbPathConfig) -> bool:
        book = self.books.get(path.leg1_instrument_id)
        if not book:
            return False

        mid = book.midpoint()
        if mid is None or mid <= 0:
            return False

        ema = self._ema_mid.get(path.leg1_instrument_id)
        if ema is None or ema <= 0:
            return True

        mid_dec = Decimal(str(mid))
        diff_bps = (mid_dec - ema) / ema * Decimal("10000")
        if abs(diff_bps) < self.config.trend_epsilon_bps:
            return True

        if path.leg1_side == OrderSide.BUY:
            return diff_bps > 0
        return diff_bps < 0

    def _execute_cycle(self, path: ArbPathConfig, p1: Decimal, p2: Decimal, p3: Decimal) -> None:
        self.path_inflight[path.id] += 1
        self._cycles_started += 1
        
        # Exec logic (Atomic-ish IOC) -> Simplified for simulation
        qty1 = path.order_qty
        
        # Approximate derived quantities
        # Leg 1: BUY ETH (Base) -> get qty1
        # Leg 2: SELL ETH (Base) -> sell qty1
        # ... this assumes standard loop structure. For generic, we need currency matching.
        # Assuming standard loop structure for backtest now.
        qty2 = qty1
        qty3 = qty2 * p2 
        
        cycle_id = self._next_cycle_id()
        cycle = CycleState(
            cycle_id=cycle_id,
            path_id=path.id,
            leg1_qty=qty1,
            leg2_qty=qty2,
            leg3_qty=qty3,
        )
        self._cycles[cycle_id] = cycle

        o1 = self.order_factory.market(
            instrument_id=path.leg1_instrument_id,
            order_side=path.leg1_side,
            quantity=self.cache.instrument(path.leg1_instrument_id).make_qty(qty1),
            time_in_force=TimeInForce.IOC,
            tags=["smp_type=CancelMaker"],
        )
        cycle.leg1_order_id = str(o1.client_order_id)
        self._order_to_cycle[cycle.leg1_order_id] = (cycle_id, 1)
        self.submit_order(o1)

        if not self.config.sequential_execution:
            o2 = self.order_factory.market(
                instrument_id=path.leg2_instrument_id,
                order_side=path.leg2_side,
                quantity=self.cache.instrument(path.leg2_instrument_id).make_qty(qty2),
                time_in_force=TimeInForce.IOC,
                tags=["smp_type=CancelMaker"],
            )
            o3 = self.order_factory.market(
                instrument_id=path.leg3_instrument_id,
                order_side=path.leg3_side,
                quantity=self.cache.instrument(path.leg3_instrument_id).make_qty(qty3),
                time_in_force=TimeInForce.IOC,
                tags=["smp_type=CancelMaker"],
            )
            cycle.leg2_order_id = str(o2.client_order_id)
            cycle.leg3_order_id = str(o3.client_order_id)
            self._order_to_cycle[cycle.leg2_order_id] = (cycle_id, 2)
            self._order_to_cycle[cycle.leg3_order_id] = (cycle_id, 3)
            self.submit_order(o2)
            self.submit_order(o3)

    def _estimate_notional_usdt(self, path: ArbPathConfig, p1: Decimal) -> Optional[Decimal]:
        instrument = self.cache.instrument(path.leg1_instrument_id)
        if instrument is None:
            return None

        notional_quote = path.order_qty * p1
        quote_currency = getattr(instrument.quote_currency, "code", None) or getattr(
            instrument.quote_currency, "symbol", None
        ) or str(instrument.quote_currency)

        if quote_currency == "USDT":
            return notional_quote

        if quote_currency == "USDC":
            usdc_usdt = self._get_usdc_usdt_price()
            if usdc_usdt is None:
                return None
            return notional_quote * usdc_usdt

        return None

    def _get_usdc_usdt_price(self) -> Optional[Decimal]:
        for inst_id, book in self.books.items():
            instrument = self.cache.instrument(inst_id)
            if instrument is None:
                continue
            base = getattr(instrument.base_currency, "code", None) or getattr(
                instrument.base_currency, "symbol", None
            ) or str(instrument.base_currency)
            quote = getattr(instrument.quote_currency, "code", None) or getattr(
                instrument.quote_currency, "symbol", None
            ) or str(instrument.quote_currency)
            if base == "USDC" and quote == "USDT":
                price = book.best_bid_price()
                return price.as_decimal() if price else None
        return None

    def stats(self) -> dict:
        return {
            "fills": self._fills,
            "rejections": self._rejections,
            "cycles_started": self._cycles_started,
            "cycles_completed": self._cycles_completed,
        }

    def mid_price(self, instrument_id: InstrumentId) -> Optional[Decimal]:
        book = self.books.get(instrument_id)
        if not book:
            return None
        mid = book.midpoint()
        return Decimal(str(mid)) if mid else None

    def on_order_filled(self, event: OrderFilled) -> None:
        self._fills += 1
        key = str(event.client_order_id)
        cycle_info = self._order_to_cycle.get(key)
        if cycle_info is None:
            self._update_net_position(event)
            return

        cycle_id, leg = cycle_info
        cycle = self._cycles.get(cycle_id)
        if cycle is None:
            return

        qty = event.last_qty.as_decimal() if hasattr(event.last_qty, "as_decimal") else Decimal(event.last_qty)
        self._update_net_position(event)

        if leg == 1:
            cycle.leg1_filled += qty
            if self.config.sequential_execution:
                path = self.paths[cycle.path_id]
                cycle.leg2_qty = cycle.leg1_filled
                o2 = self.order_factory.market(
                    instrument_id=path.leg2_instrument_id,
                    order_side=path.leg2_side,
                    quantity=self._make_qty_floor(path.leg2_instrument_id, cycle.leg2_qty),
                    time_in_force=TimeInForce.IOC,
                    tags=["smp_type=CancelMaker"],
                )
                cycle.leg2_order_id = str(o2.client_order_id)
                self._order_to_cycle[cycle.leg2_order_id] = (cycle_id, 2)
                self.submit_order(o2)
        elif leg == 2:
            cycle.leg2_filled += qty
            if self.config.sequential_execution:
                path = self.paths[cycle.path_id]
                px = event.last_px.as_decimal() if hasattr(event.last_px, "as_decimal") else Decimal(event.last_px)
                cycle.leg3_qty = cycle.leg2_filled * px
                o3 = self.order_factory.market(
                    instrument_id=path.leg3_instrument_id,
                    order_side=path.leg3_side,
                    quantity=self._make_qty_floor(path.leg3_instrument_id, cycle.leg3_qty),
                    time_in_force=TimeInForce.IOC,
                    tags=["smp_type=CancelMaker"],
                )
                cycle.leg3_order_id = str(o3.client_order_id)
                self._order_to_cycle[cycle.leg3_order_id] = (cycle_id, 3)
                self.submit_order(o3)
        elif leg == 3:
            cycle.leg3_filled += qty

        if cycle.leg1_filled > 0 and cycle.leg2_filled > 0 and cycle.leg3_filled > 0:
            self._finalize_cycle(cycle)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._rejections += 1
        key = str(event.client_order_id)
        cycle_info = self._order_to_cycle.get(key)
        if cycle_info is None:
            return

        cycle_id, leg = cycle_info
        cycle = self._cycles.get(cycle_id)
        if cycle is None:
            return

        if cycle.leg1_filled > 0:
            path = self.paths[cycle.path_id]
            hedge_side = OrderSide.SELL if path.leg1_side == OrderSide.BUY else OrderSide.BUY
            hedge_order = self.order_factory.market(
                instrument_id=path.leg1_instrument_id,
                order_side=hedge_side,
                quantity=self.cache.instrument(path.leg1_instrument_id).make_qty(cycle.leg1_filled),
                time_in_force=TimeInForce.IOC,
                tags=["smp_type=CancelMaker"],
            )
            self.submit_order(hedge_order)

        now_ns = self._event_ts_ns_from_event(getattr(event, "ts_event", None))
        self._path_pause_until_ns[cycle.path_id] = now_ns + (self.config.reject_cooldown_ms * 1_000_000)
        self._finalize_cycle(cycle)

        if self.config.halt_on_reject:
            self.log.error("Order rejected; halting strategy to prevent cascading losses.")
            self.stop()

    def _finalize_cycle(self, cycle: CycleState) -> None:
        if cycle.leg1_filled > 0 and cycle.leg2_filled > 0 and cycle.leg3_filled > 0:
            self._cycles_completed += 1
        path_id = cycle.path_id
        if self.path_inflight.get(path_id, 0) > 0:
            self.path_inflight[path_id] -= 1

        for order_id in (cycle.leg1_order_id, cycle.leg2_order_id, cycle.leg3_order_id):
            if order_id and order_id in self._order_to_cycle:
                self._order_to_cycle.pop(order_id, None)

        self._cycles.pop(cycle.cycle_id, None)

    def _next_cycle_id(self) -> int:
        self._cycle_id += 1
        return self._cycle_id

    def _avg_price_for_qty(
        self,
        instrument_id: InstrumentId,
        side: OrderSide,
        qty: Decimal,
        now_ns: int,
    ) -> Optional[tuple[Decimal, Decimal]]:
        book = self.books.get(instrument_id)
        if not book or qty <= 0:
            return None

        last_ts = self._last_book_ts_ns.get(instrument_id, 0)
        if last_ts == 0:
            return None
        if now_ns - last_ts > self.config.max_data_staleness_ms * 1_000_000:
            return None

        bbo = book.best_ask_price() if side == OrderSide.BUY else book.best_bid_price()
        if bbo is None:
            return None

        remaining = qty
        cost = Decimal("0")
        levels = book.asks() if side == OrderSide.BUY else book.bids()
        for level in levels:
            level_qty = Decimal(str(level.size()))
            if level_qty <= 0:
                continue
            take_qty = level_qty if level_qty < remaining else remaining
            level_price = level.price.as_decimal()
            cost += take_qty * level_price
            remaining -= take_qty
            if remaining <= 0:
                break

        if remaining > 0:
            return None

        avg = cost / qty
        return avg, bbo.as_decimal()

    def _slippage_ok(self, side: OrderSide, avg: Decimal, bbo: Decimal) -> bool:
        if bbo <= 0:
            return False

        if side == OrderSide.BUY:
            slippage = (avg - bbo) / bbo * Decimal("10000")
        else:
            slippage = (bbo - avg) / bbo * Decimal("10000")

        return slippage <= self.config.max_slippage_bps

    def _books_time_aligned(self, path: ArbPathConfig, now_ns: int) -> bool:
        leg_ids = [path.leg1_instrument_id, path.leg2_instrument_id, path.leg3_instrument_id]
        last_ts = [self._last_book_ts_ns.get(inst_id, 0) for inst_id in leg_ids]
        if any(ts == 0 for ts in last_ts):
            return False

        min_ts = min(last_ts)
        max_ts = max(last_ts)
        max_age_ns = self.config.max_data_staleness_ms * 1_000_000

        if now_ns - min_ts > max_age_ns:
            return False
        if max_ts - min_ts > max_age_ns:
            return False

        if self.config.require_synced_books and (min_ts != max_ts or max_ts != now_ns):
            return False

        return True

    def _has_open_exposure(self, path: ArbPathConfig) -> bool:
        for inst_id in [path.leg1_instrument_id, path.leg2_instrument_id, path.leg3_instrument_id]:
            if self._net_positions.get(inst_id, Decimal("0")) != Decimal("0"):
                return True
        return False

    def _update_net_position(self, event: OrderFilled) -> None:
        inst_id = event.instrument_id
        qty = event.last_qty.as_decimal() if hasattr(event.last_qty, "as_decimal") else Decimal(event.last_qty)
        delta = qty if event.order_side == OrderSide.BUY else -qty
        self._net_positions[inst_id] = self._net_positions.get(inst_id, Decimal("0")) + delta

    def _make_qty_floor(self, instrument_id: InstrumentId, qty: Decimal):
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            return None
        step = instrument.size_increment.as_decimal()
        if step <= 0:
            return instrument.make_qty(qty)
        steps = (qty / step).to_integral_value(rounding=ROUND_DOWN)
        floored = steps * step
        if floored <= 0:
            floored = Decimal("0")
        return instrument.make_qty(floored)

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

    def _now_ns(self) -> int:
        return time.time_ns()
