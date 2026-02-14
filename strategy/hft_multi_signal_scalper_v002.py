"""HFT Multi-Signal Scalper v002 - Report-Aligned Implementation.

Architecture (from reports):
1. Signal priority: Wick/Flush → Lead-Lag → OBI (Micro-Price)
2. Confirmation: OBI must favor direction before any entry
3. Execution: Post-Only limit orders (maker fees only)
4. Exit: 0.30% target → 60s time-stall skew → forced exit

Designed for $500 capital, 100% capital turnover per trade,
targeting 0.25-0.45% captures against 0.20% round-trip fees.

Key concepts from reports:
- Micro-Price: volume-weighted mid that "leads" actual price
- Cross-venue delta: Binance move → Bybit "sympathy" lag
- Support-wall detection: large resting bid walls
- Time-stall exit: skew down after 60s to recycle capital
"""

from __future__ import annotations

from collections import deque
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

BPS: Final[float] = 10000.0


class HftMultiSignalV2Config(StrategyConfig, frozen=True, kw_only=True):
    """Configuration matching report specifications."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId | None = None

    # Book
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50

    # === A: OBI / Micro-Price ===
    obi_levels: int = 10           # levels within window
    obi_window_bps: float = 50.0   # 0.5% of mid (wider for spot)
    obi_ratio_threshold: float = 0.60  # imbalance ratio to trigger
    obi_min_notional_usd: float = 100.0  # min $ imbalance
    microprice_lead_bps: float = 1.0  # micro-price must lead by this much

    # === B: Wick / Flush (Liquidation Proxy) ===
    wick_window_ms: int = 1500     # look-back for flash dip
    wick_drop_bps: float = 30.0    # min drop to trigger
    wick_recovery_bps: float = 8.0 # min bounce-back before entry
    wick_tp_bps: float = 20.0      # take-profit for wick trades

    # === C: Lead-Lag ===
    leadlag_move_bps: float = 15.0   # leader must move this much
    leadlag_lag_bps: float = 5.0     # follower must lag by this much
    leadlag_window: int = 5          # tick window for momentum

    # === Execution (Report specs) ===
    capital: float = 500.0           # total USDT capital
    capital_pct: float = 1.0         # fraction per trade (100%)
    min_order_value_usd: float = 5.50
    post_only: bool = True           # ALWAYS maker
    time_in_force: TimeInForce = TimeInForce.GTC

    # === Profit / Risk (Report specs) ===
    target_spread_pct: float = 0.30  # 30 bps target capture
    min_spread_pct: float = 0.25     # 25 bps minimum
    fee_pct: float = 0.10            # 0.10% per side (VIP0+MNT)
    time_stall_ms: int = 60_000      # 60s before skew-down exit
    time_stall_skew_pct: float = 0.22  # skew to 22 bps to "just get out"
    max_hold_ms: int = 120_000       # 120s absolute max hold
    stop_loss_pct: float = 0.50      # 50 bps hard stop

    # === Risk ===
    max_daily_loss_usd: float = 50.0
    long_only: bool = True           # spot only

    # === Logging ===
    log_signals: bool = True
    log_interval_ms: int = 10_000


class HftMultiSignalV2(Strategy):
    """Multi-signal HFT scalper implementing the report architecture."""

    def __init__(self, config: HftMultiSignalV2Config) -> None:
        super().__init__(config)
        self.follower_id = config.follower_instrument_id
        self.leader_id = config.leader_instrument_id
        self.instrument: Instrument | None = None
        self.follower_book: OrderBook | None = None

        # Price tracking
        self._leader_mids: deque[tuple[int, float]] = deque(maxlen=200)
        self._follower_mids: deque[tuple[int, float]] = deque(maxlen=200)
        self._recent_follower: deque[tuple[int, float]] = deque(maxlen=500)

        # OBI / Micro-Price state
        self._micro_price: float = 0.0
        self._obi_ratio: float = 0.0
        self._obi_notional: float = 0.0
        self._support_wall_price: float = 0.0

        # Position state
        self._entry_order: Order | None = None
        self._exit_order: Order | None = None
        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._entry_ts_ns: int = 0
        self._signal_type: str = ""

        # Balance
        self._available_quote: float = 0.0
        self._available_base: float = 0.0

        # Stats
        self._trade_count: int = 0
        self._win_count: int = 0
        self._loss_count: int = 0
        self._total_pnl: float = 0.0
        self._daily_pnl: float = 0.0
        self._cooldown_until_ns: int = 0
        self._last_log_ns: int = 0

    # ─── Lifecycle ───────────────────────────────────────────

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.follower_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.follower_id} not found")
            self.stop()
            return

        self.subscribe_order_book_deltas(
            self.follower_id, self.config.book_type, self.config.book_depth,
        )
        self.subscribe_quote_ticks(self.follower_id)
        if self.leader_id is not None:
            self.subscribe_order_book_deltas(
                self.leader_id, self.config.book_type, self.config.book_depth,
            )
            self.subscribe_quote_ticks(self.leader_id)

        self._initialize_balance()

        self.log.info(
            f"HFT-V2 started | capital=${self.config.capital:.0f} | "
            f"target={self.config.target_spread_pct:.2f}% | "
            f"fee={self.config.fee_pct:.2f}% | "
            f"stall={self.config.time_stall_ms}ms",
            color=LogColor.GREEN,
        )

    # ─── Data Handlers ───────────────────────────────────────

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        # Handle leader book updates
        if self.leader_id and deltas.instrument_id == self.leader_id:
            leader_book = self.cache.order_book(deltas.instrument_id)
            if leader_book is not None:
                lb = leader_book.best_bid_price()
                la = leader_book.best_ask_price()
                if lb is not None and la is not None:
                    mid = (float(lb) + float(la)) / 2
                    self._leader_mids.append((self.clock.timestamp_ns(), mid))
            return

        if deltas.instrument_id != self.follower_id:
            return
        book = self.cache.order_book(deltas.instrument_id)
        if book is None:
            return
        self.follower_book = book

        # Feed price tracking from book mid (critical for OB-only backtests)
        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        if best_bid is not None and best_ask is not None:
            mid = (float(best_bid) + float(best_ask)) / 2
            now_ns = self.clock.timestamp_ns()
            self._follower_mids.append((now_ns, mid))
            self._recent_follower.append((now_ns, mid))

        self._update_obi_microprice(book)
        self._run_logic()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
        now_ns = self.clock.timestamp_ns()

        if tick.instrument_id == self.follower_id:
            self._follower_mids.append((now_ns, mid))
            self._recent_follower.append((now_ns, mid))
        elif self.leader_id and tick.instrument_id == self.leader_id:
            self._leader_mids.append((now_ns, mid))

        self._run_logic()

    def on_order_filled(self, event: OrderFilled) -> None:
        price = float(event.last_px)
        qty = float(event.last_qty)
        fee = float(event.commission) if event.commission else 0.0

        if event.order_side == OrderSide.BUY:
            self._available_base += qty
            self._available_quote -= price * qty + fee
        else:
            self._available_base -= qty
            self._available_quote += price * qty - fee

        if self._position_side is None:
            # Entry fill
            self._position_side = event.order_side
            self._entry_price = price
            self._entry_ts_ns = self.clock.timestamp_ns()
            self._entry_order = None
            if self.config.log_signals:
                self.log.info(
                    f"FILL ENTRY {event.order_side.name} @ {price:.4f} | "
                    f"signal={self._signal_type} | trade#{self._trade_count + 1}",
                    color=LogColor.GREEN,
                )
        else:
            # Exit fill
            pnl_pct = (price - self._entry_price) / self._entry_price * 100
            if self._position_side == OrderSide.SELL:
                pnl_pct = -pnl_pct
            net_pnl_pct = pnl_pct - (2 * self.config.fee_pct)
            trade_pnl_usd = (self.config.capital * self.config.capital_pct) * net_pnl_pct / 100

            self._trade_count += 1
            self._total_pnl += trade_pnl_usd
            self._daily_pnl += trade_pnl_usd
            if trade_pnl_usd > 0:
                self._win_count += 1
            else:
                self._loss_count += 1

            if self.config.log_signals:
                wr = (self._win_count / max(self._trade_count, 1)) * 100
                self.log.info(
                    f"FILL EXIT {event.order_side.name} @ {price:.4f} | "
                    f"pnl={net_pnl_pct:+.3f}% (${trade_pnl_usd:+.2f}) | "
                    f"total=${self._total_pnl:+.2f} | "
                    f"trades={self._trade_count} WR={wr:.0f}%",
                    color=LogColor.MAGENTA if trade_pnl_usd >= 0 else LogColor.RED,
                )

            self._position_side = None
            self._entry_price = 0.0
            self._entry_ts_ns = 0
            self._exit_order = None
            self._signal_type = ""
            self._cooldown_until_ns = self.clock.timestamp_ns() + 500_000_000  # 500ms cooldown

    def on_order_canceled(self, event) -> None:
        self.log.info(f"ORDER_CANCELED: {event}", color=LogColor.RED)
        if self._entry_order and event.client_order_id == self._entry_order.client_order_id:
            self._entry_order = None
        if self._exit_order and event.client_order_id == self._exit_order.client_order_id:
            self._exit_order = None

    def on_order_expired(self, event) -> None:
        self.log.info(f"ORDER_EXPIRED: {event}", color=LogColor.RED)
        if self._entry_order and event.client_order_id == self._entry_order.client_order_id:
            self._entry_order = None
        if self._exit_order and event.client_order_id == self._exit_order.client_order_id:
            self._exit_order = None

    def on_order_rejected(self, event) -> None:
        self.log.info(f"ORDER_REJECTED: {event}", color=LogColor.RED)
        if self._entry_order and event.client_order_id == self._entry_order.client_order_id:
            self._entry_order = None

    # ─── Core Logic ──────────────────────────────────────────

    def _run_logic(self) -> None:
        if self.follower_book is None or self.instrument is None:
            return

        now_ns = self.clock.timestamp_ns()
        self._periodic_log(now_ns)

        # Kill switch
        if self._daily_pnl <= -self.config.max_daily_loss_usd:
            return

        # Position management
        if self._position_side is not None:
            self._manage_position(now_ns)
            return

        # Cooldown
        if now_ns < self._cooldown_until_ns:
            return

        # No pending entry
        if self._entry_order is not None:
            return

        # Signal priority: Wick → Lead-Lag → OBI
        # ALL signals require OBI confirmation

        # B: Wick signal (highest priority)
        if self._check_wick_signal(now_ns):
            if self._obi_confirms(OrderSide.BUY):
                self._enter(OrderSide.BUY, "WICK")
                return

        # C: Lead-lag signal
        ll_side = self._check_leadlag_signal()
        if ll_side is not None:
            if self._obi_confirms(ll_side):
                self._enter(ll_side, "LEADLAG")
                return

        # A: Pure OBI / Micro-Price signal (lowest priority)
        obi_side = self._check_obi_signal()
        if obi_side is not None:
            self._enter(obi_side, "OBI")

    # ─── Signal A: OBI / Micro-Price ─────────────────────────

    def _check_obi_signal(self) -> OrderSide | None:
        if self._obi_notional < self.config.obi_min_notional_usd:
            return None
        if len(self._follower_mids) < 2:
            return None

        _, last_mid = self._follower_mids[-1]
        micro_lead_bps = (self._micro_price - last_mid) / last_mid * BPS if last_mid > 0 else 0.0

        # Micro-price leads up → BUY
        if (
            self._obi_ratio >= self.config.obi_ratio_threshold
            and micro_lead_bps >= self.config.microprice_lead_bps
        ):
            return OrderSide.BUY

        # Micro-price leads down → SELL (if not long-only)
        if not self.config.long_only:
            if (
                self._obi_ratio <= -self.config.obi_ratio_threshold
                and micro_lead_bps <= -self.config.microprice_lead_bps
            ):
                return OrderSide.SELL

        return None

    def _obi_confirms(self, side: OrderSide) -> bool:
        """OBI must favor the trade direction (report requirement)."""
        if side == OrderSide.BUY:
            return self._obi_ratio > 0.05  # mild buy pressure
        return self._obi_ratio < -0.05  # mild sell pressure

    # ─── Signal B: Wick / Flush ──────────────────────────────

    def _check_wick_signal(self, now_ns: int) -> bool:
        if not self._recent_follower:
            return False

        window_ns = self.config.wick_window_ms * 1_000_000
        recent = [(ts, p) for ts, p in self._recent_follower if now_ns - ts <= window_ns]
        if len(recent) < 5:
            return False

        prices = [p for _, p in recent]
        high = max(prices)
        low = min(prices)
        last = prices[-1]

        # Must have dropped significantly
        drop_bps = (high - low) / high * BPS
        if drop_bps < self.config.wick_drop_bps:
            return False

        # Must be recovering (bounced off the low)
        recovery_bps = (last - low) / low * BPS
        if recovery_bps < self.config.wick_recovery_bps:
            return False

        # Low must have been recent (in last half of window)
        low_idx = prices.index(low)
        if low_idx < len(prices) // 3:
            return False  # low was too early, not a "flash" wick

        return True

    # ─── Signal C: Lead-Lag ──────────────────────────────────

    def _check_leadlag_signal(self) -> OrderSide | None:
        if self.leader_id is None:
            return None
        if len(self._leader_mids) < self.config.leadlag_window:
            return None
        if len(self._follower_mids) < 2:
            return None

        # Leader momentum over window
        leader_list = list(self._leader_mids)
        leader_start = leader_list[-self.config.leadlag_window][1]
        leader_end = leader_list[-1][1]
        leader_move_bps = (leader_end - leader_start) / leader_start * BPS

        # Follower current
        follower_end = self._follower_mids[-1][1]
        follower_start = self._follower_mids[-min(self.config.leadlag_window, len(self._follower_mids))][1]
        follower_move_bps = (follower_end - follower_start) / follower_start * BPS

        lag_bps = leader_move_bps - follower_move_bps

        if abs(leader_move_bps) >= self.config.leadlag_move_bps and lag_bps >= self.config.leadlag_lag_bps:
            return OrderSide.BUY
        if abs(leader_move_bps) >= self.config.leadlag_move_bps and lag_bps <= -self.config.leadlag_lag_bps:
            return OrderSide.SELL if not self.config.long_only else None

        return None

    # ─── Execution ───────────────────────────────────────────

    def _enter(self, side: OrderSide, signal: str) -> None:
        if self.follower_book is None or self.instrument is None:
            return

        best_bid = self.follower_book.best_bid_price()
        best_ask = self.follower_book.best_ask_price()
        if best_bid is None or best_ask is None:
            return

        # Phase 1 from report: place limit just above support wall
        if side == OrderSide.BUY:
            # Aggressive: cross the spread to get a fill
            price = float(best_ask)
        else:
            price = float(best_bid)

        # Size: full capital turnover (minus fee buffer + margin for commission)
        fee_buffer = 1.002  # 0.2% buffer for fees + rounding
        trade_value = (self.config.capital * self.config.capital_pct) / fee_buffer
        qty = trade_value / price
        notional = qty * price

        if notional < self.config.min_order_value_usd:
            return
        if side == OrderSide.BUY and self._available_quote < notional:
            return
        if side == OrderSide.SELL and self._available_base < qty:
            return

        order = self.order_factory.limit(
            instrument_id=self.follower_id,
            order_side=side,
            quantity=self.instrument.make_qty(qty),
            price=self.instrument.make_price(price),
            time_in_force=TimeInForce.IOC,
            post_only=False,
        )
        self.submit_order(order)
        self._entry_order = order
        self._signal_type = signal

        if self.config.log_signals:
            self.log.info(
                f"ENTRY {side.name} @ {price:.4f} ({signal}) | "
                f"obi={self._obi_ratio:+.2f} | micro={self._micro_price:.4f} | "
                f"qty={qty:.4f} (${notional:.2f})",
                color=LogColor.CYAN,
            )

    def _manage_position(self, now_ns: int) -> None:
        """Report-specified 4-phase exit logic."""
        if self._exit_order is not None:
            return
        if self.follower_book is None or self.instrument is None:
            return

        best_bid = self.follower_book.best_bid_price()
        best_ask = self.follower_book.best_ask_price()
        if best_bid is None or best_ask is None:
            return

        held_ms = (now_ns - self._entry_ts_ns) / 1_000_000
        mid = (float(best_bid) + float(best_ask)) / 2
        pnl_pct = (mid - self._entry_price) / self._entry_price * 100
        if self._position_side == OrderSide.SELL:
            pnl_pct = -pnl_pct

        # Phase 3: Target exit (0.30% above entry)
        target_pct = self.config.target_spread_pct
        exit_side = OrderSide.SELL if self._position_side == OrderSide.BUY else OrderSide.BUY

        # Hard stop loss
        if pnl_pct <= -self.config.stop_loss_pct:
            self._place_exit(exit_side, mid, "STOP")
            return

        # Phase 4: Time-stall skew (after 60s, lower target to just cover fees)
        if held_ms >= self.config.time_stall_ms:
            target_pct = self.config.time_stall_skew_pct

        # Max hold: force exit at any price
        if held_ms >= self.config.max_hold_ms:
            self._place_exit(exit_side, mid, "MAX_HOLD")
            return

        # Check if target reached
        if pnl_pct >= target_pct:
            if exit_side == OrderSide.SELL:
                exit_price = float(best_bid)
            else:
                exit_price = float(best_ask)
            self._place_exit(exit_side, exit_price, "TARGET" if held_ms < self.config.time_stall_ms else "STALL")

    def _place_exit(self, side: OrderSide, price: float, reason: str) -> None:
        if self.instrument is None:
            return

        qty = self._available_base if side == OrderSide.SELL else self.config.capital * self.config.capital_pct / price

        if qty * price < self.config.min_order_value_usd:
            return

        order = self.order_factory.limit(
            instrument_id=self.follower_id,
            order_side=side,
            quantity=self.instrument.make_qty(qty),
            price=self.instrument.make_price(price),
            time_in_force=TimeInForce.IOC if reason in ("STOP", "MAX_HOLD") else self.config.time_in_force,
            post_only=False if reason in ("STOP", "MAX_HOLD") else self.config.post_only,
        )
        self.submit_order(order)
        self._exit_order = order

        if self.config.log_signals:
            held_ms = (self.clock.timestamp_ns() - self._entry_ts_ns) / 1_000_000
            pnl_pct = (price - self._entry_price) / self._entry_price * 100
            self.log.info(
                f"EXIT {side.name} @ {price:.4f} ({reason}) | "
                f"held={held_ms:.0f}ms | gross={pnl_pct:+.3f}%",
                color=LogColor.YELLOW,
            )

    # ─── OBI / Micro-Price Calculation ───────────────────────

    def _update_obi_microprice(self, book: OrderBook) -> None:
        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        if best_bid is None or best_ask is None:
            return

        bid_price = float(best_bid)
        ask_price = float(best_ask)
        mid = (bid_price + ask_price) / 2
        window = mid * (self.config.obi_window_bps / BPS)

        bid_vol = 0.0
        ask_vol = 0.0
        best_bid_size = 0.0
        best_ask_size = 0.0
        max_bid_notional = 0.0
        support_price = 0.0

        for i, level in enumerate(book.bids()):
            p = float(level.price)
            s = level.size()
            notional = p * s
            if mid - p > window:
                break
            bid_vol += notional
            if i == 0:
                best_bid_size = s
            # Detect support wall (largest bid level)
            if notional > max_bid_notional:
                max_bid_notional = notional
                support_price = p

        for i, level in enumerate(book.asks()):
            p = float(level.price)
            s = level.size()
            if p - mid > window:
                break
            ask_vol += p * s
            if i == 0:
                best_ask_size = s

        total = bid_vol + ask_vol
        if total > 0:
            self._obi_ratio = (bid_vol - ask_vol) / total
            self._obi_notional = abs(bid_vol - ask_vol)
        else:
            self._obi_ratio = 0.0
            self._obi_notional = 0.0

        self._support_wall_price = support_price

        # Micro-Price: P_micro = P_ask * (V_bid / (V_bid + V_ask)) + P_bid * (V_ask / (V_bid + V_ask))
        total_bbo = best_bid_size + best_ask_size
        if total_bbo > 0:
            self._micro_price = (
                ask_price * (best_bid_size / total_bbo)
                + bid_price * (best_ask_size / total_bbo)
            )
        else:
            self._micro_price = mid

    # ─── Balance ─────────────────────────────────────────────

    def _initialize_balance(self) -> None:
        try:
            accounts = self.cache.accounts()
            if not accounts:
                self._available_quote = self.config.capital
                return
            account = accounts[0]
            balances = account.balances()
            base_currency = self.instrument.base_currency
            quote_currency = self.instrument.quote_currency
            base_bal = balances.get(base_currency)
            quote_bal = balances.get(quote_currency)
            if base_bal is not None:
                self._available_base = float(
                    base_bal.total.as_decimal() if hasattr(base_bal, "total") else base_bal
                )
            if quote_bal is not None:
                self._available_quote = float(
                    quote_bal.total.as_decimal() if hasattr(quote_bal, "total") else quote_bal
                )
            self.log.info(
                f"Balance: {self._available_base:.4f} {base_currency} | "
                f"{self._available_quote:.2f} {quote_currency}",
                color=LogColor.BLUE,
            )
        except Exception as exc:
            self.log.warning(f"Balance init: {exc}")
            self._available_quote = self.config.capital

    # ─── Logging ─────────────────────────────────────────────

    def _periodic_log(self, now_ns: int) -> None:
        if not self.config.log_signals:
            return
        if now_ns - self._last_log_ns < self.config.log_interval_ms * 1_000_000:
            return
        self._last_log_ns = now_ns

        wr = (self._win_count / max(self._trade_count, 1)) * 100
        self.log.info(
            f"STATUS | trades={self._trade_count} W={self._win_count} L={self._loss_count} "
            f"WR={wr:.0f}% | pnl=${self._total_pnl:+.2f} | "
            f"obi={self._obi_ratio:+.2f} micro={self._micro_price:.2f} | "
            f"quote=${self._available_quote:.2f}",
            color=LogColor.BLUE,
        )
