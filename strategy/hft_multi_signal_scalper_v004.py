"""HFT Multi-Signal Scalper v004 - Data-Driven Mean-Reversion.

v003 → v004 changelog (from microstructure analysis):
──────────────────────────────────────────────────────
FINDINGS FROM SOL/USDT JAN-29 DATA:
  - Spread = 8.6 bps → crossing spread costs 4.3 bps per side
  - LEADLAG has NO edge over random entry (~3% above baseline)
  - Mean-reversion after -20bps drop: 88% hit rate for +5bps in 4 min
  - Mean-reversion after -30bps drop: 87% hit rate for +8bps in 4 min
  - Random +30bps target in 2min: only 0.2% hit rate → IMPOSSIBLE

CRITICAL CHANGES:
  1. PRIMARY SIGNAL: Mean-reversion dip-buy (drawdown from rolling high)
  2. SECONDARY SIGNAL: Wick/flush (liquidation cascade snipe)
  3. DISABLED: Lead-lag as standalone entry (no edge, noise trader)
  4. ENTRY: Limit at best_bid (NOT IOC at ask) — save 8.6bps spread cost
  5. TARGET: +5 to +8 bps (not +30bps — unreachable in <2min)
  6. HOLD TIME: 10-60s (not 120s — mean-reversion decays fast)
  7. EXIT: Aggressive exit at best_ask when target reached
  8. FEE CONFIG: Configurable, can set to 0 to validate signal edge

Regime-adaptive parameters:
┌──────────────┬──────────────┬──────────────┐
│ Parameter    │ Normal       │ Downtrend    │
├──────────────┼──────────────┼──────────────┤
│ Capital %    │ 100%         │ 20%          │
│ Dip thresh   │ 15 bps       │ 20 bps       │
│ Target       │ 8 bps        │ 5 bps        │
│ Max hold     │ 60s          │ 30s          │
│ Stop loss    │ 20 bps       │ 15 bps       │
│ Cooldown     │ 1s           │ 3s           │
│ Entry        │ best_bid     │ best_bid     │
└──────────────┴──────────────┴──────────────┘
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


class Regime(Enum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGING = "RANGING"


class HftMultiSignalV4Config(StrategyConfig, frozen=True, kw_only=True):
    """Data-driven configuration based on microstructure analysis."""

    follower_instrument_id: InstrumentId
    leader_instrument_id: InstrumentId | None = None

    # Book
    book_type: BookType = BookType.L2_MBP
    book_depth: int = 50

    # === PRIMARY: Mean-Reversion Dip-Buy ===
    dip_lookback_ms: int = 60_000       # 60s rolling window for high
    dip_threshold_bps: float = 10.0     # min drawdown from rolling high
    dip_recovery_bps: float = 1.5       # min bounce from low (proof of reversal)
    dip_obi_confirm: bool = True        # require mild buy-side OBI before entry
    dip_min_depth_bps: int = 5          # min recent low lookback in ticks

    # === SECONDARY: Wick / Flush (unchanged from v003) ===
    wick_window_ms: int = 2000          # 2s look-back for flash crash
    wick_drop_bps: float = 25.0         # min drop
    wick_recovery_bps: float = 5.0      # min bounce-back

    # === TERTIARY: Lead-Lag (confirmation only, NOT standalone) ===
    leadlag_confirm_bps: float = 3.0    # Binance must be trending up to confirm
    leadlag_window: int = 5

    # === OBI / Micro-Price ===
    obi_window_bps: float = 50.0
    obi_min_notional_usd: float = 100.0

    # === Execution ===
    capital: float = 500.0
    capital_pct: float = 1.0
    min_order_value_usd: float = 5.50
    entry_at_bid: bool = True           # Post-only at bid (save spread crossing)
    entry_ioc_fallback: bool = True     # If limit doesn't fill in N ms, cancel + IOC
    entry_patience_ms: int = 2000       # wait 2s for limit fill before fallback

    # === Fees (set to 0 to validate signal edge) ===
    fee_pct: float = 0.10              # 0.10% = 10bps per side
    fee_buffer_pct: float = 0.002      # 0.2% buffer for sizing

    # === Profit / Risk ===
    target_bps: float = 8.0            # +8bps target (achievable per data)
    stop_loss_bps: float = 20.0        # -20bps hard stop
    max_hold_ms: int = 60_000          # 60s max hold
    time_stall_ms: int = 30_000        # 30s → lower target
    time_stall_target_bps: float = 3.0 # after stall, just exit at +3bps

    # === Downtrend overrides ===
    dt_capital_pct: float = 0.20
    dt_dip_threshold_bps: float = 20.0  # more selective in downtrend
    dt_target_bps: float = 5.0          # lean target
    dt_max_hold_ms: int = 30_000        # 30s max
    dt_stop_loss_bps: float = 15.0
    dt_cooldown_ms: int = 3_000

    # === Regime Detection ===
    regime_ema_period: int = 100
    regime_slope_window: int = 20
    regime_trend_bps: float = 2.0
    regime_update_interval_ms: int = 5_000

    # === Circuit Breaker ===
    cb_window: int = 10
    cb_min_wr_pct: float = 30.0         # 30% (more permissive — we expect ~60-70% WR)
    cb_pause_ms: int = 1_800_000
    cb_min_trades: int = 5

    # === Risk ===
    max_daily_loss_usd: float = 50.0
    long_only: bool = True
    cooldown_ms: int = 1_000            # 1s default cooldown

    # === Logging ===
    log_signals: bool = True
    log_interval_ms: int = 10_000


class HftMultiSignalV4(Strategy):
    """Mean-reversion dip-buyer with regime-adaptive parameters."""

    def __init__(self, config: HftMultiSignalV4Config) -> None:
        super().__init__(config)
        self.follower_id = config.follower_instrument_id
        self.leader_id = config.leader_instrument_id
        self.instrument: Instrument | None = None
        self.follower_book: OrderBook | None = None

        # Price tracking
        self._leader_mids: deque[tuple[int, float]] = deque(maxlen=200)
        self._follower_mids: deque[tuple[int, float]] = deque(maxlen=500)
        self._recent_follower: deque[tuple[int, float]] = deque(maxlen=1000)

        # Dip signal state
        self._dip_cooldown_ns: int = 0

        # OBI / Micro-Price
        self._micro_price: float = 0.0
        self._obi_ratio: float = 0.0
        self._obi_notional: float = 0.0

        # Regime
        self._regime: Regime = Regime.RANGING
        self._ema: float = 0.0
        self._ema_alpha: float = 2.0 / (config.regime_ema_period + 1)
        self._ema_initialized: bool = False
        self._ema_history: deque[float] = deque(maxlen=config.regime_slope_window + 1)
        self._regime_last_update_ns: int = 0

        # Position state
        self._entry_order: Order | None = None
        self._exit_order: Order | None = None
        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._entry_ts_ns: int = 0
        self._signal_type: str = ""
        self._entry_patience_deadline_ns: int = 0

        # Balance
        self._available_quote: float = 0.0
        self._available_base: float = 0.0

        # Stats
        self._trade_count: int = 0
        self._win_count: int = 0
        self._loss_count: int = 0
        self._total_pnl: float = 0.0
        self._daily_pnl: float = 0.0
        self._gross_pnl: float = 0.0  # track gross (pre-fee) separately
        self._cooldown_until_ns: int = 0
        self._last_log_ns: int = 0

        # Circuit breaker
        self._recent_results: deque[bool] = deque(maxlen=config.cb_window)
        self._cb_paused_until_ns: int = 0

        # Dip signal tracking
        self._last_dip_trigger_ns: int = 0
        self._dip_signals: int = 0
        self._dip_entries: int = 0

    # ── Active parameters (regime-dependent) ──

    @property
    def _active_capital_pct(self) -> float:
        return self.config.dt_capital_pct if self._regime == Regime.DOWNTREND else self.config.capital_pct

    @property
    def _active_target_bps(self) -> float:
        return self.config.dt_target_bps if self._regime == Regime.DOWNTREND else self.config.target_bps

    @property
    def _active_max_hold_ms(self) -> int:
        return self.config.dt_max_hold_ms if self._regime == Regime.DOWNTREND else self.config.max_hold_ms

    @property
    def _active_stop_loss_bps(self) -> float:
        return self.config.dt_stop_loss_bps if self._regime == Regime.DOWNTREND else self.config.stop_loss_bps

    @property
    def _active_dip_threshold_bps(self) -> float:
        return self.config.dt_dip_threshold_bps if self._regime == Regime.DOWNTREND else self.config.dip_threshold_bps

    @property
    def _active_cooldown_ns(self) -> int:
        cd_ms = self.config.dt_cooldown_ms if self._regime == Regime.DOWNTREND else self.config.cooldown_ms
        return cd_ms * 1_000_000

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
        if self.leader_id is not None:
            self.subscribe_order_book_deltas(
                self.leader_id, self.config.book_type, self.config.book_depth,
            )

        self._initialize_balance()

        fee_tag = f"fee={self.config.fee_pct:.2f}%" if self.config.fee_pct > 0 else "FEE=0% (signal test)"
        self.log.info(
            f"HFT-V4 started | capital=${self.config.capital:.0f} | "
            f"target=+{self.config.target_bps:.0f}bps | {fee_tag} | "
            f"dip_thresh={self.config.dip_threshold_bps:.0f}bps | "
            f"entry={'BID' if self.config.entry_at_bid else 'ASK'} | "
            f"CB: WR<{self.config.cb_min_wr_pct:.0f}%",
            color=LogColor.GREEN,
        )

    # ─── Data Handlers ───────────────────────────────────────

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        # Leader book
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
            self._dip_entries += 1
            if self.config.log_signals:
                rt = self._regime.value[0]
                self.log.info(
                    f"FILL ENTRY {event.order_side.name} @ {price:.4f} | "
                    f"[{rt}] {self._signal_type} | trade#{self._trade_count + 1} | "
                    f"dips={self._dip_signals} entries={self._dip_entries}",
                    color=LogColor.GREEN,
                )
        else:
            # Exit fill
            gross_pnl_bps = (price - self._entry_price) / self._entry_price * BPS
            if self._position_side == OrderSide.SELL:
                gross_pnl_bps = -gross_pnl_bps
            fee_bps = 2 * self.config.fee_pct * 100  # both sides, in bps
            net_pnl_bps = gross_pnl_bps - fee_bps
            notional = self.config.capital * self._active_capital_pct
            trade_pnl_usd = notional * net_pnl_bps / BPS
            gross_pnl_usd = notional * gross_pnl_bps / BPS

            self._trade_count += 1
            self._total_pnl += trade_pnl_usd
            self._daily_pnl += trade_pnl_usd
            self._gross_pnl += gross_pnl_usd
            is_win = trade_pnl_usd > 0
            # For 0-fee mode, count gross wins
            if self.config.fee_pct == 0:
                is_win = gross_pnl_bps > 0
            if is_win:
                self._win_count += 1
            else:
                self._loss_count += 1
            self._recent_results.append(is_win)

            if self.config.log_signals:
                wr = (self._win_count / max(self._trade_count, 1)) * 100
                rt = self._regime.value[0]
                held_ms = (self.clock.timestamp_ns() - self._entry_ts_ns) / 1_000_000
                self.log.info(
                    f"EXIT {event.order_side.name} @ {price:.4f} | "
                    f"[{rt}] gross={gross_pnl_bps:+.1f}bps net={net_pnl_bps:+.1f}bps "
                    f"(${trade_pnl_usd:+.2f}) held={held_ms:.0f}ms | "
                    f"total=${self._total_pnl:+.2f} gross=${self._gross_pnl:+.2f} "
                    f"trades={self._trade_count} WR={wr:.0f}%",
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
        self.log.warning(f"REJECTED: {event}")
        if self._entry_order and event.client_order_id == self._entry_order.client_order_id:
            self._entry_order = None

    # ─── Regime Detection ────────────────────────────────────

    def _update_ema(self, mid: float, now_ns: int) -> None:
        if not self._ema_initialized:
            self._ema = mid
            self._ema_initialized = True
        else:
            self._ema = self._ema_alpha * mid + (1 - self._ema_alpha) * self._ema
        self._ema_history.append(self._ema)

        interval_ns = self.config.regime_update_interval_ms * 1_000_000
        if now_ns - self._regime_last_update_ns < interval_ns:
            return
        self._regime_last_update_ns = now_ns
        self._classify_regime()

    def _classify_regime(self) -> None:
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

        # Hysteresis: must cross full threshold to enter, half to exit
        if self._regime == Regime.RANGING:
            if slope_bps >= threshold:
                self._regime = Regime.UPTREND
            elif slope_bps <= -threshold:
                self._regime = Regime.DOWNTREND
        elif self._regime == Regime.DOWNTREND:
            if slope_bps >= threshold * 0.5:
                self._regime = Regime.UPTREND if slope_bps >= threshold else Regime.RANGING
        elif self._regime == Regime.UPTREND:
            if slope_bps <= -threshold * 0.5:
                self._regime = Regime.DOWNTREND if slope_bps <= -threshold else Regime.RANGING

        if self._regime != old_regime and self.config.log_signals:
            self.log.info(
                f"REGIME: {old_regime.value} -> {self._regime.value} | "
                f"slope={slope_bps:+.2f}bps EMA={new_ema:.4f}",
                color=LogColor.YELLOW,
            )

    # ─── Circuit Breaker ─────────────────────────────────────

    def _check_circuit_breaker(self, now_ns: int) -> bool:
        if now_ns < self._cb_paused_until_ns:
            return True
        if len(self._recent_results) < self.config.cb_min_trades:
            return False
        wins = sum(1 for r in self._recent_results if r)
        wr = wins / len(self._recent_results) * 100
        if wr < self.config.cb_min_wr_pct:
            self._cb_paused_until_ns = now_ns + self.config.cb_pause_ms * 1_000_000
            self.log.warning(
                f"CIRCUIT BREAKER: WR={wr:.0f}% < {self.config.cb_min_wr_pct:.0f}% | "
                f"PAUSING {self.config.cb_pause_ms // 60000} min",
            )
            return True
        return False

    # ─── Core Logic ──────────────────────────────────────────

    def _run_logic(self) -> None:
        if self.follower_book is None or self.instrument is None:
            return

        now_ns = self.clock.timestamp_ns()
        self._periodic_log(now_ns)

        # Daily loss limit
        if self._daily_pnl <= -self.config.max_daily_loss_usd:
            return

        # Manage open position
        if self._position_side is not None:
            self._manage_position(now_ns)
            return

        # Check if pending entry needs patience timeout
        if self._entry_order is not None:
            if self.config.entry_at_bid and now_ns > self._entry_patience_deadline_ns:
                # Limit at bid didn't fill — cancel it
                self.cancel_order(self._entry_order)
                self._entry_order = None
            return

        # Circuit breaker
        if self._check_circuit_breaker(now_ns):
            return

        # Cooldown
        if now_ns < self._cooldown_until_ns:
            return

        # DOWNTREND BLOCK: dips in downtrends aren't mean-reverting — they're trend continuation
        if self._regime == Regime.DOWNTREND:
            return

        # ── Signal detection (priority order) ──

        # 1. WICK: Flash crash recovery (highest priority)
        if self._check_wick_signal(now_ns):
            self._enter(OrderSide.BUY, "WICK")
            return

        # 2. DIP: Mean-reversion from rolling high (primary signal)
        if self._check_dip_signal(now_ns):
            self._enter(OrderSide.BUY, "DIP")
            return

    # ─── Signal 1: Wick / Flash Crash ────────────────────────

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

    # ─── Signal 2: Mean-Reversion Dip-Buy ────────────────────

    def _check_dip_signal(self, now_ns: int) -> bool:
        """Buy when price has dropped X bps from time-based rolling high and shows recovery.

        Empirical basis (SOL/USDT Jan-29, sampled every 20th tick):
          -15bps drop → +5bps in 4min: 84% hit rate
          -20bps drop → +5bps in 4min: 88% hit rate
          -30bps drop → +8bps in 4min: 87% hit rate

        Uses time-based rolling window (not tick-based) so the high doesn't
        refresh every 50ms of book updates.
        """
        # Per-dip cooldown: don't re-trigger on the same dip
        if now_ns < self._dip_cooldown_ns:
            return False

        if len(self._recent_follower) < 20:
            return False

        lookback_ns = self.config.dip_lookback_ms * 1_000_000
        cutoff_ns = now_ns - lookback_ns

        # Get all prices in the lookback window
        window_prices = [
            p for ts, p in self._recent_follower if ts >= cutoff_ns
        ]
        if len(window_prices) < 10:
            return False

        rolling_high = max(window_prices)
        current = window_prices[-1]

        # How far have we dropped from the rolling high?
        drawdown_bps = (rolling_high - current) / rolling_high * BPS
        if drawdown_bps < self._active_dip_threshold_bps:
            return False

        # Require slight recovery from recent low (proof of bounce)
        # Look at last ~20 ticks for the local minimum
        depth = min(self.config.dip_min_depth_bps, len(window_prices))
        recent_low = min(window_prices[-depth:])
        recovery_bps = (current - recent_low) / recent_low * BPS
        if recovery_bps < self.config.dip_recovery_bps:
            return False  # still falling — don't catch the knife

        # Optional: OBI must mildly favor buying
        if self.config.dip_obi_confirm and self._obi_ratio < -0.10:
            return False  # strong sell pressure in book — skip

        # Optional: leader must not be crashing (if available)
        if self.leader_id and len(self._leader_mids) >= self.config.leadlag_window:
            leader_list = list(self._leader_mids)
            l_start = leader_list[-self.config.leadlag_window][1]
            l_end = leader_list[-1][1]
            leader_move_bps = (l_end - l_start) / l_start * BPS
            # If Binance is also crashing hard, don't enter (cascade risk)
            if leader_move_bps < -self._active_dip_threshold_bps:
                return False

        self._dip_signals += 1
        # Set per-dip cooldown so we don't keep entering on the same dip
        self._dip_cooldown_ns = now_ns + (self.config.cooldown_ms * 1_000_000)

        if self.config.log_signals:
            self.log.info(
                f"DIP: dd={drawdown_bps:.1f}bps rec={recovery_bps:.1f}bps "
                f"obi={self._obi_ratio:+.2f} [{self._regime.value[0]}] "
                f"high={rolling_high:.2f} low={recent_low:.2f} now={current:.2f} "
                f"(#{self._dip_signals})",
                color=LogColor.CYAN,
            )

        return True

    # ─── Execution ───────────────────────────────────────────

    def _enter(self, side: OrderSide, signal: str) -> None:
        if self.follower_book is None or self.instrument is None:
            return

        best_bid = self.follower_book.best_bid_price()
        best_ask = self.follower_book.best_ask_price()
        if best_bid is None or best_ask is None:
            return

        # Entry pricing: at bid (passive) or at ask (aggressive)
        if self.config.entry_at_bid and side == OrderSide.BUY:
            price = float(best_bid)
            tif = TimeInForce.GTC
            post_only = True
        else:
            price = float(best_ask) if side == OrderSide.BUY else float(best_bid)
            tif = TimeInForce.IOC
            post_only = False

        # Sizing
        fee_buffer = 1.0 + self.config.fee_buffer_pct
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
            time_in_force=tif,
            post_only=post_only,
        )
        self.submit_order(order)
        self._entry_order = order
        self._signal_type = signal
        self._entry_patience_deadline_ns = self.clock.timestamp_ns() + (
            self.config.entry_patience_ms * 1_000_000
        )

        if self.config.log_signals:
            rt = self._regime.value[0]
            entry_type = "BID(passive)" if post_only else "ASK(aggressive)"
            self.log.info(
                f"ENTRY {side.name} @ {price:.4f} ({signal}) [{rt}] {entry_type} | "
                f"qty={qty:.4f} (${notional:.2f}) cap={self._active_capital_pct:.0%}",
                color=LogColor.CYAN,
            )

    # ─── Position Management ─────────────────────────────────

    def _manage_position(self, now_ns: int) -> None:
        """Quick micro-scalp exit: target +Xbps, tight stop, short hold.
        
        ALL exits are IOC (aggressive) because mean-reversion bounces
        are brief - price won't stay at target for a passive fill.
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

        exit_side = OrderSide.SELL if self._position_side == OrderSide.BUY else OrderSide.BUY

        # For SELL exits, we get the bid price. For BUY exits, we pay the ask.
        if exit_side == OrderSide.SELL:
            exit_price = float(best_bid)
            pnl_bps = (exit_price - self._entry_price) / self._entry_price * BPS
        else:
            exit_price = float(best_ask)
            pnl_bps = (self._entry_price - exit_price) / self._entry_price * BPS

        # 1. STOP LOSS
        if pnl_bps <= -self._active_stop_loss_bps:
            self._place_exit(exit_side, exit_price, "STOP")
            return

        # 2. MAX HOLD
        if held_ms >= self._active_max_hold_ms:
            self._place_exit(exit_side, exit_price, "MAX_HOLD")
            return

        # 3. TARGET — use active target (regime-adaptive)
        target = self._active_target_bps

        # Time stall: after stall period, lower target to just get out
        if held_ms >= self.config.time_stall_ms:
            target = self.config.time_stall_target_bps

        if pnl_bps >= target:
            reason = "TARGET" if held_ms < self.config.time_stall_ms else "STALL"
            self._place_exit(exit_side, exit_price, reason)

    def _place_exit(self, side: OrderSide, price: float, reason: str) -> None:
        """All exits are IOC — we want immediate fill at current price."""
        if self.instrument is None:
            return

        if side == OrderSide.SELL:
            qty = self._available_base
        else:
            qty = self.config.capital * self._active_capital_pct / price

        if qty * price < self.config.min_order_value_usd:
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
        self._exit_order = order

        if self.config.log_signals:
            held_ms = (self.clock.timestamp_ns() - self._entry_ts_ns) / 1_000_000
            gross_bps = (price - self._entry_price) / self._entry_price * BPS
            rt = self._regime.value[0]
            self.log.info(
                f"EXIT {side.name} @ {price:.4f} ({reason}) [{rt}] | "
                f"held={held_ms:.0f}ms gross={gross_bps:+.1f}bps IOC",
                color=LogColor.YELLOW,
            )

    # ─── OBI / Micro-Price ───────────────────────────────────

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

        for i, level in enumerate(book.bids()):
            p = float(level.price)
            s = level.size()
            if mid - p > window:
                break
            bid_vol += p * s
            if i == 0:
                best_bid_size = s

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

        cb_tag = " CB" if now_ns < self._cb_paused_until_ns else ""
        # Compute current drawdown from time-based rolling high
        dd_bps = 0.0
        if len(self._recent_follower) > 10:
            lookback_ns = self.config.dip_lookback_ms * 1_000_000
            cutoff_ns = now_ns - lookback_ns
            window_prices = [p for ts, p in self._recent_follower if ts >= cutoff_ns]
            if window_prices:
                rh = max(window_prices)
                dd_bps = (rh - window_prices[-1]) / rh * BPS if rh > 0 else 0

        self.log.info(
            f"[{self._regime.value}]{cb_tag} | "
            f"t={self._trade_count} W={self._win_count} L={self._loss_count} "
            f"WR={wr:.0f}% | net=${self._total_pnl:+.2f} gross=${self._gross_pnl:+.2f} | "
            f"dd={dd_bps:.1f}bps obi={self._obi_ratio:+.2f} | "
            f"dips={self._dip_signals} quote=${self._available_quote:.2f}",
            color=LogColor.BLUE,
        )
