"""HFT Multi-Signal Scalper v003 - Regime-Adaptive "Sniper" Mode.

v002 → v003 changelog (from downtrend report):
───────────────────────────────────────────────
1. REGIME DETECTION   - EMA slope classifies UPTREND / DOWNTREND / RANGING
2. ADAPTIVE PARAMS    - In downtrend: 20% capital, 0.22% target, 15s max hold
3. CIRCUIT BREAKER    - WR < 40% over last N trades → pause 30 min
4. VOLATILITY SNIPE   - Enhanced wick: detects over-extension below micro-price
5. LEAN INVENTORY     - Downtrend targets $0 SOL; desperate exit at fee-cover

Architecture (unchanged from v002):
- Signal priority: Wick/Flush → Lead-Lag → OBI (Micro-Price)
- OBI must confirm direction before entry
- Dual-venue: Binance (leader) → Bybit (follower)

Regime-adaptive parameters:
┌─────────────┬───────────────┬───────────────┐
│ Parameter   │ Uptrend/Range │ Downtrend     │
├─────────────┼───────────────┼───────────────┤
│ Capital %   │ 100%          │ 20%           │
│ Target      │ 0.30%         │ 0.22%         │
│ Max hold    │ 120s          │ 15s           │
│ Time stall  │ 60s           │ 8s            │
│ Buy trigger │ OBI + LL      │ Wick/Vol only │
│ Cooldown    │ 500ms         │ 2000ms        │
│ Stop loss   │ 0.50%         │ 0.30%         │
└─────────────┴───────────────┴───────────────┘
"""

from __future__ import annotations

from collections import deque
from enum import Enum
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


# ─── Regime ──────────────────────────────────────────────

class Regime(Enum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGING = "RANGING"


class HftMultiSignalV3Config(StrategyConfig, frozen=True, kw_only=True):
    """Configuration with regime-adaptive defaults."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId | None = None

    # Book
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50

    # === A: OBI / Micro-Price ===
    obi_levels: int = 10
    obi_window_bps: float = 50.0
    obi_ratio_threshold: float = 0.60
    obi_min_notional_usd: float = 100.0
    microprice_lead_bps: float = 1.0

    # === B: Wick / Flush (Liquidation Snipe) ===
    wick_window_ms: int = 1500
    wick_drop_bps: float = 30.0
    wick_recovery_bps: float = 8.0
    wick_tp_bps: float = 20.0
    # Downtrend-enhanced: volatility over-extension
    vol_overext_bps: float = 40.0    # price must be 40bps below micro-price
    vol_overext_window_ms: int = 3000  # 3s window for measuring vol spike

    # === C: Lead-Lag ===
    leadlag_move_bps: float = 15.0
    leadlag_lag_bps: float = 5.0
    leadlag_window: int = 5

    # === Execution (Uptrend defaults) ===
    capital: float = 500.0
    capital_pct: float = 1.0       # 100% in uptrend
    min_order_value_usd: float = 5.50
    post_only: bool = True
    time_in_force: TimeInForce = TimeInForce.GTC
    fee_pct: float = 0.10

    # === Profit / Risk (Uptrend defaults) ===
    target_spread_pct: float = 0.30
    min_spread_pct: float = 0.25
    time_stall_ms: int = 60_000
    time_stall_skew_pct: float = 0.22
    max_hold_ms: int = 120_000
    stop_loss_pct: float = 0.50

    # === Downtrend overrides ===
    dt_capital_pct: float = 0.20       # 20% of capital per trade
    dt_target_spread_pct: float = 0.22  # just cover fees
    dt_max_hold_ms: int = 15_000       # 15s max hold
    dt_time_stall_ms: int = 8_000      # 8s stall
    dt_stop_loss_pct: float = 0.30     # tighter stop
    dt_cooldown_ms: int = 2_000        # 2s cooldown (sniper, not sprayer)

    # === Regime Detection ===
    regime_ema_period: int = 100       # EMA over N mid-price ticks
    regime_slope_window: int = 20      # slope measured over last N EMA values
    regime_trend_bps: float = 2.0      # > 2 bps slope = trending
    regime_update_interval_ms: int = 5_000  # re-evaluate every 5s

    # === Circuit Breaker ===
    cb_window: int = 10              # last N trades
    cb_min_wr_pct: float = 40.0      # pause if WR < 40%
    cb_pause_ms: int = 1_800_000     # 30 minutes pause
    cb_min_trades: int = 5           # need at least 5 trades before CB activates

    # === Risk ===
    max_daily_loss_usd: float = 50.0
    long_only: bool = True

    # === Logging ===
    log_signals: bool = True
    log_interval_ms: int = 10_000


class HftMultiSignalV3(Strategy):
    """Regime-adaptive HFT scalper: Market-Maker in uptrend, Sniper in downtrend."""

    def __init__(self, config: HftMultiSignalV3Config) -> None:
        super().__init__(config)
        self.follower_id = config.follower_instrument_id
        self.leader_id = config.leader_instrument_id
        self.instrument: Instrument | None = None
        self.follower_book: OrderBook | None = None

        # Price tracking
        self._leader_mids: deque[tuple[int, float]] = deque(maxlen=200)
        self._follower_mids: deque[tuple[int, float]] = deque(maxlen=200)
        self._recent_follower: deque[tuple[int, float]] = deque(maxlen=1000)

        # OBI / Micro-Price state
        self._micro_price: float = 0.0
        self._obi_ratio: float = 0.0
        self._obi_notional: float = 0.0
        self._support_wall_price: float = 0.0

        # Regime detection
        self._regime: Regime = Regime.RANGING
        self._ema: float = 0.0
        self._ema_alpha: float = 2.0 / (config.regime_ema_period + 1)
        self._ema_initialized: bool = False
        self._ema_history: deque[float] = deque(maxlen=config.regime_slope_window + 1)
        self._regime_last_update_ns: int = 0
        self._mid_count: int = 0

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

        # Circuit breaker
        self._recent_results: deque[bool] = deque(maxlen=config.cb_window)  # True=win
        self._cb_paused_until_ns: int = 0

    # ── Active parameters (regime-dependent) ──

    @property
    def _active_capital_pct(self) -> float:
        if self._regime == Regime.DOWNTREND:
            return self.config.dt_capital_pct
        return self.config.capital_pct

    @property
    def _active_target_pct(self) -> float:
        if self._regime == Regime.DOWNTREND:
            return self.config.dt_target_spread_pct
        return self.config.target_spread_pct

    @property
    def _active_max_hold_ms(self) -> int:
        if self._regime == Regime.DOWNTREND:
            return self.config.dt_max_hold_ms
        return self.config.max_hold_ms

    @property
    def _active_time_stall_ms(self) -> int:
        if self._regime == Regime.DOWNTREND:
            return self.config.dt_time_stall_ms
        return self.config.time_stall_ms

    @property
    def _active_stop_loss_pct(self) -> float:
        if self._regime == Regime.DOWNTREND:
            return self.config.dt_stop_loss_pct
        return self.config.stop_loss_pct

    @property
    def _active_cooldown_ns(self) -> int:
        if self._regime == Regime.DOWNTREND:
            return self.config.dt_cooldown_ms * 1_000_000
        return 500_000_000  # 500ms default

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
            f"HFT-V3 started | capital=${self.config.capital:.0f} | "
            f"regime=RANGING | fee={self.config.fee_pct:.2f}% | "
            f"CB: WR<{self.config.cb_min_wr_pct:.0f}% → pause {self.config.cb_pause_ms // 1000}s",
            color=LogColor.GREEN,
        )

    # ─── Data Handlers ───────────────────────────────────────

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        # Leader book → update leader mids
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

        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        if best_bid is not None and best_ask is not None:
            mid = (float(best_bid) + float(best_ask)) / 2
            now_ns = self.clock.timestamp_ns()
            self._follower_mids.append((now_ns, mid))
            self._recent_follower.append((now_ns, mid))
            self._update_ema(mid, now_ns)

        self._update_obi_microprice(book)
        self._run_logic()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
        now_ns = self.clock.timestamp_ns()

        if tick.instrument_id == self.follower_id:
            self._follower_mids.append((now_ns, mid))
            self._recent_follower.append((now_ns, mid))
            self._update_ema(mid, now_ns)
        elif self.leader_id and tick.instrument_id == self.leader_id:
            self._leader_mids.append((now_ns, mid))

        self._run_logic()

    # ─── Order Events ────────────────────────────────────────

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
                regime_tag = self._regime.value[0]  # U/D/R
                self.log.info(
                    f"FILL ENTRY {event.order_side.name} @ {price:.4f} | "
                    f"[{regime_tag}] signal={self._signal_type} | trade#{self._trade_count + 1}",
                    color=LogColor.GREEN,
                )
        else:
            # Exit fill → compute P&L
            pnl_pct = (price - self._entry_price) / self._entry_price * 100
            if self._position_side == OrderSide.SELL:
                pnl_pct = -pnl_pct
            net_pnl_pct = pnl_pct - (2 * self.config.fee_pct)
            notional = self.config.capital * self._active_capital_pct
            trade_pnl_usd = notional * net_pnl_pct / 100

            self._trade_count += 1
            self._total_pnl += trade_pnl_usd
            self._daily_pnl += trade_pnl_usd
            is_win = trade_pnl_usd > 0
            if is_win:
                self._win_count += 1
            else:
                self._loss_count += 1
            self._recent_results.append(is_win)

            if self.config.log_signals:
                wr = (self._win_count / max(self._trade_count, 1)) * 100
                regime_tag = self._regime.value[0]
                held_ms = (self.clock.timestamp_ns() - self._entry_ts_ns) / 1_000_000
                self.log.info(
                    f"FILL EXIT {event.order_side.name} @ {price:.4f} | "
                    f"[{regime_tag}] pnl={net_pnl_pct:+.3f}% (${trade_pnl_usd:+.2f}) held={held_ms:.0f}ms | "
                    f"total=${self._total_pnl:+.2f} trades={self._trade_count} WR={wr:.0f}%",
                    color=LogColor.MAGENTA if is_win else LogColor.RED,
                )

            self._position_side = None
            self._entry_price = 0.0
            self._entry_ts_ns = 0
            self._exit_order = None
            self._signal_type = ""
            self._cooldown_until_ns = self.clock.timestamp_ns() + self._active_cooldown_ns

    def on_order_canceled(self, event) -> None:
        if self._entry_order and event.client_order_id == self._entry_order.client_order_id:
            self._entry_order = None
        if self._exit_order and event.client_order_id == self._exit_order.client_order_id:
            self._exit_order = None

    def on_order_expired(self, event) -> None:
        if self._entry_order and event.client_order_id == self._entry_order.client_order_id:
            self._entry_order = None
        if self._exit_order and event.client_order_id == self._exit_order.client_order_id:
            self._exit_order = None

    def on_order_rejected(self, event) -> None:
        self.log.warning(f"ORDER_REJECTED: {event}")
        if self._entry_order and event.client_order_id == self._entry_order.client_order_id:
            self._entry_order = None

    # ─── Regime Detection ────────────────────────────────────

    def _update_ema(self, mid: float, now_ns: int) -> None:
        """Update EMA of mid-price and periodically re-classify regime."""
        self._mid_count += 1
        if not self._ema_initialized:
            self._ema = mid
            self._ema_initialized = True
        else:
            self._ema = self._ema_alpha * mid + (1 - self._ema_alpha) * self._ema
        self._ema_history.append(self._ema)

        # Re-evaluate regime on schedule
        interval_ns = self.config.regime_update_interval_ms * 1_000_000
        if now_ns - self._regime_last_update_ns < interval_ns:
            return
        self._regime_last_update_ns = now_ns
        self._classify_regime()

    def _classify_regime(self) -> None:
        """Classify market regime from EMA slope with hysteresis.

        Hysteresis prevents flicker:
        - To ENTER a trend regime, slope must exceed ±threshold
        - To EXIT a trend regime, slope must cross zero (opposite sign)
        This prevents DOWNTREND→RANGING→DOWNTREND within seconds.
        """
        if len(self._ema_history) < self.config.regime_slope_window:
            return

        ema_list = list(self._ema_history)
        old_ema = ema_list[-self.config.regime_slope_window]
        new_ema = ema_list[-1]
        if old_ema == 0:
            return

        slope_bps = (new_ema - old_ema) / old_ema * BPS
        threshold = self.config.regime_trend_bps
        old_regime = self._regime

        if self._regime == Regime.RANGING:
            # Must exceed full threshold to enter a trend
            if slope_bps >= threshold:
                self._regime = Regime.UPTREND
            elif slope_bps <= -threshold:
                self._regime = Regime.DOWNTREND
        elif self._regime == Regime.DOWNTREND:
            # Must turn positive to exit downtrend (hysteresis)
            if slope_bps >= threshold * 0.5:
                self._regime = Regime.UPTREND if slope_bps >= threshold else Regime.RANGING
        elif self._regime == Regime.UPTREND:
            # Must turn negative to exit uptrend (hysteresis)
            if slope_bps <= -threshold * 0.5:
                self._regime = Regime.DOWNTREND if slope_bps <= -threshold else Regime.RANGING

        if self._regime != old_regime and self.config.log_signals:
            self.log.info(
                f"REGIME: {old_regime.value} → {self._regime.value} | "
                f"slope={slope_bps:+.2f}bps EMA={new_ema:.4f}",
                color=LogColor.YELLOW,
            )

    # ─── Circuit Breaker ─────────────────────────────────────

    def _check_circuit_breaker(self, now_ns: int) -> bool:
        """Returns True if trading is paused by circuit breaker."""
        # Still in pause period?
        if now_ns < self._cb_paused_until_ns:
            return True

        # Not enough trades to evaluate
        if len(self._recent_results) < self.config.cb_min_trades:
            return False

        wins = sum(1 for r in self._recent_results if r)
        wr = wins / len(self._recent_results) * 100

        if wr < self.config.cb_min_wr_pct:
            self._cb_paused_until_ns = now_ns + self.config.cb_pause_ms * 1_000_000
            pause_min = self.config.cb_pause_ms / 60_000
            self.log.warning(
                f"CIRCUIT BREAKER: WR={wr:.0f}% < {self.config.cb_min_wr_pct:.0f}% "
                f"over last {len(self._recent_results)} trades | "
                f"PAUSING {pause_min:.0f} min",
            )
            return True

        return False

    # ─── Core Logic ──────────────────────────────────────────

    def _run_logic(self) -> None:
        if self.follower_book is None or self.instrument is None:
            return

        now_ns = self.clock.timestamp_ns()
        self._periodic_log(now_ns)

        # Kill switch: daily loss limit
        if self._daily_pnl <= -self.config.max_daily_loss_usd:
            return

        # Position management (always active regardless of regime/CB)
        if self._position_side is not None:
            self._manage_position(now_ns)
            return

        # Circuit breaker
        if self._check_circuit_breaker(now_ns):
            return

        # Cooldown
        if now_ns < self._cooldown_until_ns:
            return

        # Pending entry still in flight
        if self._entry_order is not None:
            return

        # ── Signal routing depends on regime ──
        if self._regime == Regime.DOWNTREND:
            self._run_downtrend_signals(now_ns)
        else:
            self._run_normal_signals(now_ns)

    def _run_normal_signals(self, now_ns: int) -> None:
        """Uptrend / Ranging: full signal stack (Wick → Lead-Lag → OBI)."""
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

    def _run_downtrend_signals(self, now_ns: int) -> None:
        """Downtrend: SNIPER mode. Only wick/volatility over-extension signals.

        Report: "The bot ignores 90% of the price action. It waits for a
        volatility spike that pushes the price significantly below the
        Micro-Price."

        Disabled in downtrend: Lead-Lag, OBI (too many false positives
        when order book liquidity is fake / being pulled).
        """
        # Enhanced wick: over-extension below micro-price
        if self._check_volatility_overextension(now_ns):
            self._enter(OrderSide.BUY, "VOL_SNIPE")
            return

        # Standard wick still active (liquidation cascade)
        if self._check_wick_signal(now_ns):
            # In downtrend, wick doesn't need OBI confirmation
            # (report: "order book imbalances become fake")
            self._enter(OrderSide.BUY, "WICK_DT")

    # ─── Signal A: OBI / Micro-Price ─────────────────────────

    def _check_obi_signal(self) -> OrderSide | None:
        if self._obi_notional < self.config.obi_min_notional_usd:
            return None
        if len(self._follower_mids) < 2:
            return None

        _, last_mid = self._follower_mids[-1]
        micro_lead_bps = (self._micro_price - last_mid) / last_mid * BPS if last_mid > 0 else 0.0

        if (
            self._obi_ratio >= self.config.obi_ratio_threshold
            and micro_lead_bps >= self.config.microprice_lead_bps
        ):
            return OrderSide.BUY

        if not self.config.long_only:
            if (
                self._obi_ratio <= -self.config.obi_ratio_threshold
                and micro_lead_bps <= -self.config.microprice_lead_bps
            ):
                return OrderSide.SELL

        return None

    def _obi_confirms(self, side: OrderSide) -> bool:
        """OBI must mildly favor the trade direction."""
        if side == OrderSide.BUY:
            return self._obi_ratio > 0.05
        return self._obi_ratio < -0.05

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

        drop_bps = (high - low) / high * BPS
        if drop_bps < self.config.wick_drop_bps:
            return False

        recovery_bps = (last - low) / low * BPS
        if recovery_bps < self.config.wick_recovery_bps:
            return False

        low_idx = prices.index(low)
        if low_idx < len(prices) // 3:
            return False

        return True

    # ─── Signal B+: Volatility Over-Extension (Downtrend) ───

    def _check_volatility_overextension(self, now_ns: int) -> bool:
        """Report: 'waits for a volatility spike that pushes the price
        significantly below the Micro-Price.'

        Triggers when:
        1. Current mid is vol_overext_bps below micro-price (over-extended)
        2. Price has dropped rapidly within vol_overext_window_ms
        3. Price is starting to recover (early bounce detection)
        """
        if self._micro_price <= 0 or not self._recent_follower:
            return False

        window_ns = self.config.vol_overext_window_ms * 1_000_000
        recent = [(ts, p) for ts, p in self._recent_follower if now_ns - ts <= window_ns]
        if len(recent) < 3:
            return False

        prices = [p for _, p in recent]
        last = prices[-1]
        low = min(prices)
        high = max(prices)

        # 1. Price must be significantly below micro-price
        gap_bps = (self._micro_price - last) / self._micro_price * BPS
        if gap_bps < self.config.vol_overext_bps:
            return False

        # 2. There was a rapid drop in the window
        drop_bps = (high - low) / high * BPS
        if drop_bps < self.config.vol_overext_bps * 0.8:
            return False

        # 3. Price is bouncing (current > low, early recovery)
        if last <= low:
            return False  # still falling, don't catch the knife

        recovery_pct = (last - low) / (high - low) if high > low else 0
        if recovery_pct < 0.10:
            return False  # too early, need at least 10% of the drop recovered

        return True

    # ─── Signal C: Lead-Lag ──────────────────────────────────

    def _check_leadlag_signal(self) -> OrderSide | None:
        if self.leader_id is None:
            return None
        if len(self._leader_mids) < self.config.leadlag_window:
            return None
        if len(self._follower_mids) < 2:
            return None

        leader_list = list(self._leader_mids)
        leader_start = leader_list[-self.config.leadlag_window][1]
        leader_end = leader_list[-1][1]
        leader_move_bps = (leader_end - leader_start) / leader_start * BPS

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

        # Aggressive entry: cross the spread
        if side == OrderSide.BUY:
            price = float(best_ask)
        else:
            price = float(best_bid)

        # Size: regime-adaptive capital percentage
        fee_buffer = 1.002
        trade_value = (self.config.capital * self._active_capital_pct) / fee_buffer
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
            regime_tag = self._regime.value[0]
            self.log.info(
                f"ENTRY {side.name} @ {price:.4f} ({signal}) [{regime_tag}] | "
                f"obi={self._obi_ratio:+.2f} | micro={self._micro_price:.4f} | "
                f"qty={qty:.4f} (${notional:.2f}) cap_pct={self._active_capital_pct:.0%}",
                color=LogColor.CYAN,
            )

    # ─── Position Management ─────────────────────────────────

    def _manage_position(self, now_ns: int) -> None:
        """Regime-adaptive exit logic.

        Uptrend: 4-phase (target → stall → max_hold → stop)
        Downtrend: desperate exit — lean inventory, get back to cash ASAP.

        Report: "If the bot accidentally fills a buy order, it becomes
        desperate to sell. It will lower its Profit Target from 0.3% to
        0.21% to get back to cash immediately."
        """
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

        exit_side = OrderSide.SELL if self._position_side == OrderSide.BUY else OrderSide.BUY

        # Hard stop loss (regime-adaptive)
        if pnl_pct <= -self._active_stop_loss_pct:
            self._place_exit(exit_side, mid, "STOP")
            return

        # Max hold: force exit (regime-adaptive)
        max_hold = self._active_max_hold_ms
        if held_ms >= max_hold:
            self._place_exit(exit_side, mid, "MAX_HOLD")
            return

        # Target: regime-adaptive
        target_pct = self._active_target_pct

        # Time-stall skew (regime-adaptive)
        stall_ms = self._active_time_stall_ms
        if held_ms >= stall_ms:
            # In downtrend, stall target = just cover fees (desperate exit)
            if self._regime == Regime.DOWNTREND:
                target_pct = self.config.fee_pct * 2 + 0.01  # 0.21% minimum
            else:
                target_pct = self.config.time_stall_skew_pct

        # Check if target reached
        if pnl_pct >= target_pct:
            if exit_side == OrderSide.SELL:
                exit_price = float(best_bid)
            else:
                exit_price = float(best_ask)

            reason = "TARGET"
            if held_ms >= stall_ms:
                reason = "STALL_DT" if self._regime == Regime.DOWNTREND else "STALL"
            self._place_exit(exit_side, exit_price, reason)

    def _place_exit(self, side: OrderSide, price: float, reason: str) -> None:
        if self.instrument is None:
            return

        if side == OrderSide.SELL:
            qty = self._available_base
        else:
            qty = self.config.capital * self._active_capital_pct / price

        if qty * price < self.config.min_order_value_usd:
            return

        # Forced exits use IOC (STOP, MAX_HOLD); targets can use GTC
        is_forced = reason in ("STOP", "MAX_HOLD")
        order = self.order_factory.limit(
            instrument_id=self.follower_id,
            order_side=side,
            quantity=self.instrument.make_qty(qty),
            price=self.instrument.make_price(price),
            time_in_force=TimeInForce.IOC if is_forced else self.config.time_in_force,
            post_only=False if is_forced else self.config.post_only,
        )
        self.submit_order(order)
        self._exit_order = order

        if self.config.log_signals:
            held_ms = (self.clock.timestamp_ns() - self._entry_ts_ns) / 1_000_000
            pnl_pct = (price - self._entry_price) / self._entry_price * 100
            regime_tag = self._regime.value[0]
            self.log.info(
                f"EXIT {side.name} @ {price:.4f} ({reason}) [{regime_tag}] | "
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
        recent_wr = 0.0
        if self._recent_results:
            recent_wr = sum(1 for r in self._recent_results if r) / len(self._recent_results) * 100

        cb_active = now_ns < self._cb_paused_until_ns
        cb_tag = " ⛔CB" if cb_active else ""

        self.log.info(
            f"STATUS [{self._regime.value}]{cb_tag} | "
            f"trades={self._trade_count} W={self._win_count} L={self._loss_count} "
            f"WR={wr:.0f}% (recent={recent_wr:.0f}%) | pnl=${self._total_pnl:+.2f} | "
            f"obi={self._obi_ratio:+.2f} ema={self._ema:.2f} | "
            f"quote=${self._available_quote:.2f}",
            color=LogColor.BLUE,
        )
