#!/usr/bin/env python3
"""Mean Reversion Trader for Hyperliquid — v003 "JAMES".

PHILOSOPHY — Faithful replication of James's 33-year methodology.
==================================================================

    "Everything mean reverts." — The core premise.

    This version implements ALL FIVE of James's techniques that were
    missing from v002:

    1. ENTRY BAND as a VISUAL ZONE, not a binary threshold
       "only trade when price is OUTSIDE the band... you can ignore
        the green dots INSIDE the green band"
       He showed moving bands from ±0.8σ to ±5/±6.5σ pushed Google
       from ~85% to 99.08% win rate. The wider the bands, the purer
       the signal. v003 adds an OUTER band: the nibble zone (inner)
       and the conviction zone (outer).

    2. DOUBLE-TAP IN — 30% nibble then add
       "Yesterday I took a nibble of Tesla, today I took a big chunk"
       "30% in the first trade maybe 30 in the second"
       We enter 30% at the inner band, then add 70% if price reaches
       the outer band. This gives a better average entry and means
       the hard stop rarely fires on partial positions.

    3. LIFO EXIT — Last In, First Out
       "LIFO means last in first out so the last one in is the first
        one I sell"
       We track each entry leg (price, qty, Z-score, bar) internally.
       On exit, we close the most recent leg first. This preserves
       the initial entry at a better average price.

    4. TRUE NOISE SUPPRESSION — Quality, not just cooldown
       "suppresses less optimal signals if stronger ones exist within
        the specified time horizon"
       v002 used a simple cooldown timer. v003 compares Z-score
       magnitudes: skip a Z=-1.1σ signal if Z=-2.0σ was available
       within the suppression window. Keep only the strongest signal.

    5. TREND TURN PREFERENCE — Wait for the flip
       "you don't buy till it actually turns... Trend turns and then
        you buy"
       Repeated 4+ times in the transcript. The trader specifically
       buys at the TURN moment — the flip from bearish to bullish —
       not during an already-established bull trend. v003 adds a
       bonus Z-score reduction when the trend JUST turned (fresher
       signal = higher conviction).

THREE-MODEL CONFLUENCE SYSTEM (unchanged from v002)
====================================================
    1. MEAN REVERSION MODEL — Z-score oscillator via Bollinger Bands
    2. TREND MODEL — EMA cross gates entries and exits
    3. CONFIDENCE MODEL — RSI turning point confirmation

POSITION MANAGEMENT — Double-tap LIFO
=======================================
    Each position has up to max_legs entries (default 3).
    Leg 1: "nibble" — 30% of budget at inner band
    Leg 2: "chunk"  — 40% of budget at outer band (if reached)
    Leg 3: "slam"   — 30% of budget at extreme band (if reached)

    Exit is LIFO: close most recent leg first when:
    - Z-score reaches opposite band → TP
    - Trend turns → close ALL legs (trend exit)
    - Hard stop hit → close ALL legs (risk exit)
    - Time stop hit → close ALL legs (thesis broken)

EXIT LOGIC
===========
    1. HARD % STOP → close everything (risk control)
    2. TP: opposite band reached → close last leg (LIFO)
    3. TREND TURNS → close everything (trend keeps you in, trend takes you out)
    4. TIME STOP → close everything (thesis broken)
    5. EMERGENCY σ stop → close everything

VERSION: v003.0 — "JAMES" (faithful to James's methodology)
CREATED: February 2026
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Final

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.message import Event
from nautilus_trader.indicators import (
    AverageTrueRange,
    BollingerBands,
    ExponentialMovingAverage,
    RelativeStrengthIndex,
)
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    OrderSide,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.events import OrderFilled, PositionChanged, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
LOG_INTERVAL_BARS: Final[int] = 10


# =============================================================================
# LEG TRACKING (for LIFO double-tap)
# =============================================================================
@dataclass
class EntryLeg:
    """Track a single entry leg for LIFO exit."""
    price: float          # Entry price
    quantity: float       # Quantity in this leg (raw float, not Decimal)
    zscore: float         # Z-score at entry
    bar_number: int       # Bar number when entered
    leg_index: int        # 0=nibble, 1=chunk, 2=slam


# =============================================================================
# CONFIGURATION
# =============================================================================
class HLMeanReversionConfig(StrategyConfig, frozen=True):
    """Configuration for the Mean Reversion v003 strategy.

    TUNING GUIDE
    =============
    James's approach: "Test every timeframe for each asset and pick the
    highest win rate." Then dial in the bands.

    KEY PRINCIPLE: "the more standard deviations from the mean, the higher
    your win rate" — wider bands = fewer trades but much higher quality.

    DOUBLE-TAP ZONES:
    - inner band (entry_band):  first nibble — 30% of budget
    - outer band (entry_band_outer): big chunk — 40% of budget
    - extreme (entry_band_outer * 1.5): slam — 30% of budget

    These correspond to James's visual: "you can ignore the green dots
    inside the green band and you only trade for those ones that are
    outside the band."
    """

    instrument_id: str = "ZRO-USD-PERP.HYPERLIQUID"
    bar_type: str = "ZRO-USD-PERP.HYPERLIQUID-5-MINUTE-LAST-EXTERNAL"

    # === MEAN REVERSION MODEL ===
    bb_period: int = 32               # SMA + σ lookback (Optuna: 32 > 20 = smoother mean, less noise)
    bb_std_k: float = 2.0             # Standard deviation multiplier for BB bands

    # ENTRY BANDS — Two-zone system
    # Inner band: "nibble" entry (first 30% of position)
    # Outer band: "chunk" entry (next 40% — only if price goes further)
    #
    # James: "moving those bands up moved the win rate to 99.08%"
    # Optuna (500 trials, 4 coins): tighter bands + BB=32 gives 74% WR across
    # ZRO/SUI/LINK/SOL. The longer BB period makes the mean more stable,
    # so even 0.8σ deviations are genuine signals — not noise.
    entry_band: float = 0.8           # Inner band: nibble zone (in σ)
    entry_band_outer: float = 1.4     # Outer band: chunk zone (in σ)
    #                                   Extreme zone computed as outer * 1.5

    # === TREND MODEL (the gate) ===
    # "The trend will keep you either out of the trade or in the trade"
    # Optuna: slow EMA ~164 (vs 89) = much smoother macro trend, matching
    # James's "very tight" setting. Wider separation (0.14) = only trade
    # when trend is clear and strong.
    trend_ema_fast: int = 32          # Fast EMA for trend
    trend_ema_slow: int = 164         # Slow EMA for trend (macro filter)
    trend_min_separation_pct: float = 0.14  # Min EMA spread to confirm trend

    # === TREND TURN PREFERENCE ===
    # "you don't buy till it actually turns... Trend turns and then you buy"
    # The trader specifically waits for the FLIP moment. When the trend JUST
    # turned (within N bars), we apply a Z-score bonus — effectively lowering
    # the entry band. This makes trend-turn entries easier to qualify for,
    # matching James's behavior of buying at the turn.
    trend_turn_lookback: int = 7      # How many bars the "just turned" window lasts
    trend_turn_z_bonus: float = 0.3   # Reduce required Z by this when trend just turned
    #                                   e.g., inner band 0.8σ becomes 0.5σ at turn

    # === STRICT TREND GATE ===
    # "you don't buy till the trend turns" — only trade WITH the trend.
    # No flat-trend entries allowed.
    require_strict_trend: bool = True

    # === CONFIDENCE MODEL (momentum confirmation) ===
    rsi_period: int = 14
    rsi_oversold: float = 0.25        # RSI below this = oversold (0-1 scale)
    rsi_overbought: float = 0.75      # RSI above this = overbought

    # === TURNING POINT DETECTION ===
    zscore_lookback: int = 3          # Z-score derivative window
    rsi_lookback: int = 3             # RSI derivative window

    # === NOISE SUPPRESSION (TRUE — quality-based) ===
    # James: "suppresses less optimal signals if stronger ones exist
    # within the specified time horizon"
    #
    # v002 was just a cooldown timer. v003 compares Z-score magnitudes:
    # - Track the peak |Z| within the suppression window
    # - New signal must have |Z| >= peak_z * suppression_ratio to qualify
    # - This means: if we just saw Z=-2.5σ, skip Z=-1.1σ (noise)
    noise_suppression_window: int = 30  # Window size in bars (Optuna: 3× wider)
    noise_suppression_ratio: float = 0.90  # New signal must be ≥ 90% of peak |Z| in window
    #                                       Optuna: much stricter — only trade near-peak signals

    # === POSITION SIZING — Double-tap LIFO ===
    # "30% in the first trade maybe 30 in the second"
    # "LIFO means last in first out"
    #
    # base_trade_size_usd = TOTAL position budget for the entire trade cycle.
    # Each leg gets its allocation fraction of this total:
    #   Nibble = 30% of $150 = $45
    #   Chunk  = 40% of $150 = $60
    #   Slam   = 30% of $150 = $45
    # Total potential position = $150 (if all 3 legs fire)
    # Most trades are single-leg (nibble only) ≈ $45, comparable to v002's $50.
    base_trade_size_usd: float = 150.0  # TOTAL budget for entire position cycle
    max_position_usd: float = 200.0     # Absolute safety cap across all legs
    leg_allocation: tuple = (0.30, 0.40, 0.30)  # Nibble, Chunk, Slam
    max_legs: int = 3                   # Maximum double-tap entries
    scale_with_zscore: bool = True      # Scale each leg by its Z-score magnitude

    # === EXIT RULES ===
    exit_opposite_band: float = 1.0   # Exit when Z reaches opposite band (take profit quicker)
    max_adverse_sigma: float = 5.0    # Emergency exit (wider = more room to breathe)

    # === HARD % STOP ===
    # Applied to the OVERALL position average price, not individual legs.
    hard_stop_pct: float = 2.9        # Hard stop at -2.9% from avg entry (Optuna: wider = fewer stop-outs)

    # === MAX HOLD TIME ===
    max_hold_bars: int = 40           # Force exit after 40 bars (200min on 5m)

    # ATR for emergency stop calculation
    atr_period: int = 14

    # === RISK CONTROLS ===
    cooldown_bars: int = 3            # Minimum bars between entries (Optuna: tighter)
    max_daily_loss_usd: float = 500.0 # Fixed USD floor — always enforced
    max_daily_loss_pct: float = 5.0   # % of last known equity — scales with capital
    #                                   Effective limit = max(usd_floor, equity × pct/100)
    #                                   At $2,400:  max(500, 120) = $500
    #                                   At $10,000: max(500, 500) = $500
    #                                   At $25,000: max(500, 1250) = $1,250
    #                                   At $67,000: max(500, 3350) = $3,350
    max_daily_trades: int = 20        # Higher since we have multiple legs
    max_open_positions: int = 1

    # === AUTO-SCALING (compound growth) ===
    # When enabled, base_trade_size_usd and max_position_usd are dynamically
    # computed as a percentage of account equity at each new trade entry.
    # This implements the compounding plan: as capital grows from deposits +
    # trading profits, position sizes grow proportionally.
    auto_scale: bool = False            # Enable dynamic sizing from equity
    auto_scale_leverage: float = 5.0    # Effective leverage (equity × lev = max_pos)
    auto_scale_base_frac: float = 0.75  # base_trade_size = max_pos × this
    auto_scale_min_usd: float = 10.0    # Floor: don't trade below this size
    auto_scale_max_usd: float = 500_000.0  # Ceiling: hard cap on position size


class HLMeanReversion(Strategy):
    """Mean reversion v003 — James's methodology, faithfully implemented.

    Five improvements over v002:
    1. Two-zone entry bands (nibble + chunk + slam)
    2. Double-tap LIFO position management
    3. True noise suppression (quality, not just cooldown)
    4. Trend-turn preference (Z-score bonus at flip moment)
    5. Proper LIFO exit (last in, first out)
    """

    # Class-level registry: tracks all instances by instrument coin.
    # Used by _reconcile_orphaned_position to detect sibling strategies
    # that trade the same coin in the same node.
    _instances_by_coin: dict[str, list[str]] = {}

    def __init__(self, config: HLMeanReversionConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Register this instance in the class-level coin registry
        coin = config.instrument_id.split("-")[0]
        if coin not in HLMeanReversion._instances_by_coin:
            HLMeanReversion._instances_by_coin[coin] = []
        HLMeanReversion._instances_by_coin[coin].append(config.strategy_id)

        # ── Model 1: Mean Reversion (BB → Z-score) ──
        self.bb = BollingerBands(config.bb_period, config.bb_std_k)

        # ── Model 2: Trend (EMA cross) ──
        self.ema_fast = ExponentialMovingAverage(config.trend_ema_fast)
        self.ema_slow = ExponentialMovingAverage(config.trend_ema_slow)

        # ── Model 3: Confidence (RSI) ──
        self.rsi = RelativeStrengthIndex(config.rsi_period)

        # ── ATR for emergency stops ──
        self.atr = AverageTrueRange(config.atr_period)

        # ── Z-score history (for turning point + noise suppression) ──
        max_lookback = max(config.zscore_lookback, config.noise_suppression_window) + 5
        self._zscore_history: deque[float] = deque(maxlen=max_lookback)
        self._rsi_history: deque[float] = deque(maxlen=config.rsi_lookback + 5)

        # ── Noise suppression: track peak |Z| within window ──
        self._recent_signal_z: deque[float] = deque(maxlen=config.noise_suppression_window)

        # ── Trend state ──
        self._prev_trend_bullish: bool | None = None
        self._bars_since_trend_turn: int = 999  # Bars since last trend flip

        # ── Trade tracking ──
        self._bar_count: int = 0
        self._bars_since_entry: int = 999  # Bars since LAST entry (any leg)
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._last_day: str = ""
        self._total_pnl: float = 0.0
        self._wins: int = 0
        self._losses: int = 0
        self._last_signal: str = "WAIT"

        # ── LIFO Double-tap position tracking ──
        self._legs: list[EntryLeg] = []        # Active entry legs (ordered by entry time)
        self._entry_side: str = "FLAT"         # "LONG", "SHORT", "FLAT"
        self._bars_in_trade: int = 0           # Bars since FIRST entry
        self._position_budget_used: float = 0.0  # USD allocated so far

        # Auto-scaling: effective sizes (updated before each new trade)
        self._effective_base_size: float = config.base_trade_size_usd
        self._effective_max_pos: float = config.max_position_usd
        self._last_equity: float = 0.0

        # ── EXIT SAFETY: retry tracking ──
        self._exit_pending: bool = False          # An exit order is in flight
        self._exit_retry_count: int = 0            # Consecutive failed exit attempts
        self._exit_reason: str = ""                # Why we're exiting (for retry log)
        self._max_exit_retries: int = 5            # After this, halt strategy
        self._startup_reconciled: bool = False     # Have we checked HL for orphaned positions?

    # =========================================================================
    # LIFECYCLE
    # =========================================================================
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.bar_type, self.bb)
        self.register_indicator_for_bars(self.bar_type, self.ema_fast)
        self.register_indicator_for_bars(self.bar_type, self.ema_slow)
        self.register_indicator_for_bars(self.bar_type, self.rsi)
        self.register_indicator_for_bars(self.bar_type, self.atr)

        self.request_bars(
            self.bar_type,
            start=self._clock.utc_now() - timedelta(hours=12),
        )
        self.subscribe_bars(self.bar_type)

        # Initial sizing refresh
        if self.config.auto_scale:
            self._refresh_sizing()

        # ── STARTUP RECONCILIATION: check for orphaned positions on HL ──
        self._reconcile_orphaned_position()

        alloc_str = "/".join(f"{a*100:.0f}%" for a in self.config.leg_allocation)
        scale_str = (
            f"AUTO({self.config.auto_scale_leverage}x eq=${self._last_equity:,.0f})"
            if self.config.auto_scale
            else f"${self.config.base_trade_size_usd} (max ${self.config.max_position_usd})"
        )
        self.log.info(
            f"🚀 Mean Reversion v003 JAMES started | {self.config.instrument_id} | "
            f"inner=±{self.config.entry_band}σ outer=±{self.config.entry_band_outer}σ | "
            f"exit={self.config.exit_opposite_band}σ | "
            f"EMA={self.config.trend_ema_fast}/{self.config.trend_ema_slow} strict={self.config.require_strict_trend} | "
            f"legs={self.config.max_legs} alloc={alloc_str} LIFO | "
            f"noise=quality(win={self.config.noise_suppression_window}, ratio={self.config.noise_suppression_ratio}) | "
            f"hard_stop={self.config.hard_stop_pct}% max_hold={self.config.max_hold_bars}bars | "
            f"size={scale_str}",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)
        # Use our own exit logic (close_all_positions may not work without
        # a registered Account object for HYPERLIQUID-master)
        if self._legs and self.instrument:
            total_qty = sum(leg.quantity for leg in self._legs)
            if total_qty > 0:
                close_side = OrderSide.SELL if self._entry_side == "LONG" else OrderSide.BUY
                try:
                    # Use 1% slippage for on_stop — must fill
                    close_price = float(self.bb.middle) if self.bb.middle > 0 else self._legs[0].price
                    aggressive_px = close_price * (1.01 if close_side == OrderSide.BUY else 0.99)
                    limit_price = self.instrument.make_price(Decimal(str(round(aggressive_px, 4))))
                    quantity = self.instrument.make_qty(Decimal(str(total_qty)))
                    order = self.order_factory.limit(
                        instrument_id=self.instrument_id,
                        order_side=close_side,
                        quantity=quantity,
                        price=limit_price,
                        time_in_force=TimeInForce.IOC,
                        reduce_only=True,
                    )
                    self.submit_order(order)
                    self.log.warning(f"🛑 ON_STOP: Emergency exit {close_side} {total_qty} submitted")
                except Exception as e:
                    self.log.error(f"🛑 ON_STOP: Failed to exit! Manual close required: {e}")
        # Also try the Nautilus method as fallback
        self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    # =========================================================================
    # Z-SCORE COMPUTATION
    # =========================================================================
    def _compute_zscore(self, close: float) -> float:
        """Z = (close - SMA) / σ, using Bollinger Bands."""
        middle = self.bb.middle
        upper = self.bb.upper

        if upper <= middle or middle <= 0:
            return 0.0

        sigma = (upper - middle) / self.config.bb_std_k
        if sigma <= 0:
            return 0.0

        return (close - middle) / sigma

    # =========================================================================
    # TURNING POINT DETECTION
    # =========================================================================
    def _zscore_is_turning_up(self) -> bool:
        """Z was falling and is now rising (green dot)."""
        if len(self._zscore_history) < self.config.zscore_lookback + 1:
            return False
        return self._zscore_history[-1] > self._zscore_history[-(self.config.zscore_lookback + 1)]

    def _zscore_is_turning_down(self) -> bool:
        """Z was rising and is now falling (red dot)."""
        if len(self._zscore_history) < self.config.zscore_lookback + 1:
            return False
        return self._zscore_history[-1] < self._zscore_history[-(self.config.zscore_lookback + 1)]

    def _rsi_is_turning_up(self) -> bool:
        if len(self._rsi_history) < self.config.rsi_lookback + 1:
            return False
        return self._rsi_history[-1] > self._rsi_history[-(self.config.rsi_lookback + 1)]

    def _rsi_is_turning_down(self) -> bool:
        if len(self._rsi_history) < self.config.rsi_lookback + 1:
            return False
        return self._rsi_history[-1] < self._rsi_history[-(self.config.rsi_lookback + 1)]

    # =========================================================================
    # TRUE NOISE SUPPRESSION (quality-based)
    # =========================================================================
    def _should_suppress_signal(self, current_z: float) -> bool:
        """Suppress weak signals if a stronger one exists in the window.

        James: "suppresses less optimal signals if stronger ones exist
        within the specified time horizon."

        We track |Z| values of recent signals. A new signal must have
        |Z| >= peak_recent * suppression_ratio to qualify.
        This means: after a Z=-2.5σ signal, a Z=-1.1σ is suppressed
        (it's only 44% as strong — below the 70% ratio threshold).
        """
        if self.config.noise_suppression_window <= 0:
            return False

        # Cooldown: always enforce minimum bars between entries
        if self._bars_since_entry < self.config.cooldown_bars:
            return True

        # Quality check: compare against recent peak
        abs_current = abs(current_z)
        if len(self._recent_signal_z) > 0:
            peak_recent = max(self._recent_signal_z)
            if peak_recent > 0 and abs_current < peak_recent * self.config.noise_suppression_ratio:
                return True  # Too weak compared to recent signal

        return False

    def _record_signal_strength(self, zscore: float) -> None:
        """Record a fired signal's |Z| for noise suppression comparison."""
        self._recent_signal_z.append(abs(zscore))

    # =========================================================================
    # TREND MODEL
    # =========================================================================
    def _trend_is_bullish(self, close: float) -> bool:
        spread = self.ema_fast.value - self.ema_slow.value
        min_spread = close * self.config.trend_min_separation_pct / 100.0
        return spread > min_spread

    def _trend_is_bearish(self, close: float) -> bool:
        spread = self.ema_slow.value - self.ema_fast.value
        min_spread = close * self.config.trend_min_separation_pct / 100.0
        return spread > min_spread

    def _trend_just_turned_bullish(self) -> bool:
        """Trend JUST flipped from bearish to bullish within lookback window."""
        return self._bars_since_trend_turn <= self.config.trend_turn_lookback

    def _get_effective_entry_band(self, is_trend_turn: bool, band: float) -> float:
        """Apply Z-score bonus when trend just turned.

        James specifically buys at the TURN moment. This bonus makes
        the entry band effectively tighter at the flip, matching his
        behavior of entering more readily when the trend confirms.
        """
        if is_trend_turn and self.config.trend_turn_z_bonus > 0:
            return max(band - self.config.trend_turn_z_bonus, 0.5)
        return band

    # =========================================================================
    # DOUBLE-TAP ENTRY ZONES
    # =========================================================================
    def _get_entry_zone(self, abs_z: float, is_trend_turn: bool) -> int | None:
        """Determine which entry zone the current Z-score falls in.

        Returns:
            0 = nibble zone (inner band)
            1 = chunk zone (outer band)
            2 = slam zone (extreme)
            None = not in any entry zone

        James's visual zones:
        - "outside the green band" = inner band → nibble
        - Even further out = outer band → chunk (bigger position)
        - Extreme deviation (2+ std devs further) = slam
        """
        inner = self._get_effective_entry_band(is_trend_turn, self.config.entry_band)
        outer = self._get_effective_entry_band(is_trend_turn, self.config.entry_band_outer)
        extreme = outer * 1.5

        if abs_z >= extreme:
            return 2  # Slam zone
        elif abs_z >= outer:
            return 1  # Chunk zone
        elif abs_z >= inner:
            return 0  # Nibble zone
        return None

    def _refresh_sizing(self) -> None:
        """Update effective position sizes from account equity.

        Called once at the start of each NEW trade (not on add-to-position).
        Queries Hyperliquid unified account equity via REST API.

        Unified Trading is enabled — spot and perps share one margin pool.
        The API still separates them into two clearinghouse endpoints:
          - clearinghouseState   → perp margin (accountValue)
          - spotClearinghouseState → spot USDC balance
        We sum both to get the true unified equity.
        """
        if not self.config.auto_scale:
            return

        try:
            import requests
            import os

            wallet = os.environ.get("HYPERLIQUID_WALLET", "")
            if not wallet:
                self.log.warning("AUTO_SCALE: No HYPERLIQUID_WALLET set, using config sizes")
                return

            # ── Perp clearinghouse (margin + unrealized P&L) ──
            resp_perp = requests.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "clearinghouseState", "user": wallet},
                timeout=5,
            )
            perp_state = resp_perp.json()
            perp_equity = float(
                perp_state.get("marginSummary", {}).get("accountValue", 0)
            )

            # ── Spot clearinghouse (USDC balance — unified with perps) ──
            resp_spot = requests.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "spotClearinghouseState", "user": wallet},
                timeout=5,
            )
            spot_state = resp_spot.json()
            spot_usdc = 0.0
            for bal in spot_state.get("balances", []):
                if bal.get("coin") == "USDC":
                    spot_usdc = float(bal.get("total", 0))
                    break

            # Unified equity = perp account value + spot USDC
            # (perp accountValue already includes margin allocated to perps,
            #  spot USDC is the unallocated balance — together they are the
            #  full unified account equity)
            equity = perp_equity + spot_usdc

            if equity <= 0:
                self.log.warning(f"AUTO_SCALE: equity=${equity:.2f}, using config sizes")
                return

            self._last_equity = equity

            # Compute dynamic sizes
            max_pos = equity * self.config.auto_scale_leverage
            max_pos = max(max_pos, self.config.auto_scale_min_usd)
            max_pos = min(max_pos, self.config.auto_scale_max_usd)

            base_size = max_pos * self.config.auto_scale_base_frac

            # Update effective sizes
            old_base = self._effective_base_size
            self._effective_base_size = base_size
            self._effective_max_pos = max_pos

            self.log.info(
                f"📐 AUTO_SCALE: equity=${equity:,.2f} × {self.config.auto_scale_leverage}x "
                f"→ base=${base_size:,.0f} max=${max_pos:,.0f} "
                f"(was ${old_base:,.0f})",
                LogColor.CYAN,
            )

        except Exception as e:
            self.log.warning(f"AUTO_SCALE failed (using last sizes): {e}")

    def _get_leg_size_usd(self, zone: int, zscore: float) -> float:
        """Compute position size for this leg.

        James: "30% in the first trade maybe 30 in the second"
        We use the leg_allocation tuple to define budget splits.
        """
        # Which leg number are we on?
        leg_num = len(self._legs)
        if leg_num >= self.config.max_legs:
            return 0.0

        # Get allocation fraction for this leg
        alloc = self.config.leg_allocation
        if leg_num < len(alloc):
            fraction = alloc[leg_num]
        else:
            fraction = alloc[-1]  # Use last allocation for extra legs

        base_size = self._effective_base_size * fraction

        # Scale with Z-score magnitude (bigger deviation = more conviction)
        if self.config.scale_with_zscore:
            abs_z = abs(zscore)
            # Multiplier: 1.0 at inner band, up to 2.0 at extreme
            multiplier = min(0.5 * (abs_z - self.config.entry_band) + 1.0, 2.5)
            multiplier = max(multiplier, 1.0)
            base_size *= multiplier

        # Don't exceed max position
        remaining = self._effective_max_pos - self._position_budget_used
        return min(base_size, remaining)

    # =========================================================================
    # BAR HANDLER — The core decision loop
    # =========================================================================
    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return

        self._bar_count += 1
        self._bars_since_entry += 1

        # ── Daily reset ──
        today = str(bar.ts_event)[:10]
        if today != self._last_day:
            if self._last_day:
                self.log.info(
                    f"📅 Day end {self._last_day}: P&L=${self._daily_pnl:+.2f} | "
                    f"trades={self._daily_trades}",
                    LogColor.NORMAL,
                )
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._last_day = today

        # ── Track hold time ──
        if self._entry_side != "FLAT":
            self._bars_in_trade += 1

        # ── Extract data ──
        close = float(bar.close)

        # ── Compute Z-score ──
        zscore = self._compute_zscore(close)
        self._zscore_history.append(zscore)

        # ── Track RSI history ──
        rsi = self.rsi.value
        self._rsi_history.append(rsi)

        # ── Trend state ──
        trend_bullish = self._trend_is_bullish(close)
        trend_bearish = self._trend_is_bearish(close)

        # Track trend flips for trend-turn preference
        self._bars_since_trend_turn += 1
        if self._prev_trend_bullish is not None:
            if not self._prev_trend_bullish and trend_bullish:
                self._bars_since_trend_turn = 0  # Just turned bullish
            elif self._prev_trend_bullish and trend_bearish:
                self._bars_since_trend_turn = 0  # Just turned bearish

        trend_is_turn = self._bars_since_trend_turn <= self.config.trend_turn_lookback

        # Determine trend direction at the turn
        trend_turned_bull = self._bars_since_trend_turn <= self.config.trend_turn_lookback and trend_bullish
        trend_turned_bear = self._bars_since_trend_turn <= self.config.trend_turn_lookback and trend_bearish

        atr = self.atr.value

        # ── Decay noise suppression window (one entry per bar tick) ──
        # We push 0.0 each bar to age out old signals from the deque
        self._recent_signal_z.append(0.0)

        # ── Periodic status log ──
        if self._bar_count % LOG_INTERVAL_BARS == 0:
            total_qty = sum(leg.quantity for leg in self._legs)
            avg_entry = (sum(leg.price * leg.quantity for leg in self._legs) / total_qty) if total_qty > 0 else 0
            trend_str = "🟢BULL" if trend_bullish else ("🔴BEAR" if trend_bearish else "⚪FLAT")
            turn_str = " 🔄TURN" if trend_is_turn else ""
            if self._legs:
                legs_str = f"{self._entry_side} {total_qty:.2f}@{avg_entry:.2f} {len(self._legs)}legs"
            else:
                legs_str = "FLAT"
            self.log.info(
                f"📊 close={close:.4f} | Z={zscore:+.2f}σ | "
                f"RSI={rsi:.2f} | trend={trend_str}{turn_str} | "
                f"pos={legs_str} | "
                f"pnl=${self._total_pnl:+.2f} (today=${self._daily_pnl:+.2f}) | "
                f"W/L={self._wins}/{self._losses}",
                LogColor.NORMAL,
            )

        # ══════════════════════════════════════════════════════════════
        # PART 0: EXIT RETRY — if a previous exit IOC missed, retry NOW
        # ══════════════════════════════════════════════════════════════
        if self._exit_pending and self._entry_side != "FLAT" and self._legs:
            self._exit_retry_count += 1
            if self._exit_retry_count > self._max_exit_retries:
                self.log.error(
                    f"🛑 EXIT FAILED {self._max_exit_retries} TIMES — "
                    f"HALTING STRATEGY. Manual close required! "
                    f"Reason: {self._exit_reason}",
                )
                self.stop()
                return
            self.log.warning(
                f"⚠️ EXIT RETRY {self._exit_retry_count}/{self._max_exit_retries}: "
                f"Previous exit did not fill — retrying with wider slippage. "
                f"Reason: {self._exit_reason}",
            )
            self._exit_all(close, reason=self._exit_reason)
            return

        # ══════════════════════════════════════════════════════════════
        # PART 1: EXIT LOGIC (check exits BEFORE entries)
        # ══════════════════════════════════════════════════════════════
        if self._entry_side != "FLAT":
            exit_result = self._check_exit(zscore, close, trend_bullish, trend_bearish,
                                            trend_turned_bull, trend_turned_bear)
            if exit_result:
                exit_reason, exit_mode = exit_result
                self.log.info(
                    f"🚪 EXIT {self._entry_side}: {exit_reason} | "
                    f"Z={zscore:+.2f}σ | close={close:.4f} | "
                    f"legs={len(self._legs)} mode={exit_mode}",
                    LogColor.YELLOW,
                )
                if exit_mode == "LIFO_ONE":
                    self._exit_lifo_one(close)
                else:
                    self._exit_all(close, reason=exit_reason)

        # Update trend tracking AFTER exit check
        self._prev_trend_bullish = trend_bullish

        # ══════════════════════════════════════════════════════════════
        # PART 2: ENTRY LOGIC (or ADD-TO-POSITION logic)
        # ══════════════════════════════════════════════════════════════

        # Safety checks
        daily_loss_limit = self.config.max_daily_loss_usd
        if self._last_equity > 0:
            pct_limit = self._last_equity * self.config.max_daily_loss_pct / 100.0
            daily_loss_limit = max(daily_loss_limit, pct_limit)
        if self._daily_pnl <= -daily_loss_limit:
            return
        if self._daily_trades >= self.config.max_daily_trades:
            return
        if atr <= 0:
            return

        # If we're already in a position, check for double-tap ADD
        if self._entry_side != "FLAT":
            self._check_add_to_position(zscore, close, rsi, trend_bullish, trend_bearish,
                                        trend_turned_bull, trend_turned_bear)
            return

        # Fresh entry — refresh sizing from account equity (if auto_scale)
        self._refresh_sizing()

        # ── CHECK LONG ENTRY ──
        long_signal, long_reasons = self._check_entry(
            zscore, close, rsi, trend_bullish, trend_bearish,
            trend_turned_bull, trend_is_turn, side="LONG",
        )
        if long_signal:
            zone = self._get_entry_zone(abs(zscore), trend_is_turn)
            size_usd = self._get_leg_size_usd(zone if zone is not None else 0, zscore)
            if size_usd > 0 and close > 0:
                qty_raw = size_usd / close
                quantity = self.instrument.make_qty(Decimal(str(qty_raw)))
                if quantity > 0:
                    self._last_signal = f"LONG Z={zscore:+.2f}σ zone={zone}"
                    self.log.info(
                        f"🟢 LONG ENTRY (leg 1/{self.config.max_legs}): "
                        f"{' + '.join(long_reasons)} | "
                        f"Z={zscore:+.2f}σ | close={close:.4f} | "
                        f"RSI={rsi:.2f} | zone={zone} | size=${size_usd:.0f} ({quantity})",
                        LogColor.GREEN,
                    )
                    self._submit_entry(OrderSide.BUY, quantity, close, zscore, zone if zone is not None else 0)

        # ── CHECK SHORT ENTRY ──
        short_signal, short_reasons = self._check_entry(
            zscore, close, rsi, trend_bullish, trend_bearish,
            trend_turned_bear, trend_is_turn, side="SHORT",
        )
        if short_signal:
            zone = self._get_entry_zone(abs(zscore), trend_is_turn)
            size_usd = self._get_leg_size_usd(zone if zone is not None else 0, zscore)
            if size_usd > 0 and close > 0:
                qty_raw = size_usd / close
                quantity = self.instrument.make_qty(Decimal(str(qty_raw)))
                if quantity > 0:
                    self._last_signal = f"SHORT Z={zscore:+.2f}σ zone={zone}"
                    self.log.info(
                        f"🔴 SHORT ENTRY (leg 1/{self.config.max_legs}): "
                        f"{' + '.join(short_reasons)} | "
                        f"Z={zscore:+.2f}σ | close={close:.4f} | "
                        f"RSI={rsi:.2f} | zone={zone} | size=${size_usd:.0f} ({quantity})",
                        LogColor.RED,
                    )
                    self._submit_entry(OrderSide.SELL, quantity, close, zscore, zone if zone is not None else 0)

    # =========================================================================
    # SIGNAL CHECKS
    # =========================================================================
    def _check_entry(
        self,
        zscore: float,
        close: float,
        rsi: float,
        trend_bullish: bool,
        trend_bearish: bool,
        trend_turned: bool,
        trend_is_turn: bool,
        side: str,
    ) -> tuple[bool, list[str]]:
        """Check three-model confluence for entry.

        This is the INITIAL entry — leg 1 (nibble).
        Uses inner band as the threshold.
        """
        reasons = []
        abs_z = abs(zscore)

        # ── Model 1: Mean Reversion — Z-score at extreme ──
        effective_band = self._get_effective_entry_band(trend_is_turn, self.config.entry_band)

        if side == "LONG":
            if zscore >= -effective_band:
                return False, []
            reasons.append(f"Z={zscore:+.2f}σ < -{effective_band:.1f}")
        else:  # SHORT
            if zscore <= effective_band:
                return False, []
            reasons.append(f"Z={zscore:+.2f}σ > +{effective_band:.1f}")

        # ── Turning point: Z must be reversing ──
        if side == "LONG":
            if not self._zscore_is_turning_up():
                return False, []
            reasons.append("Z↑turning")
        else:
            if not self._zscore_is_turning_down():
                return False, []
            reasons.append("Z↓turning")

        # ── Model 2: Trend gate ──
        # "you don't buy till the trend turns"
        if self.config.require_strict_trend:
            if side == "LONG":
                if not (trend_bullish or trend_turned):
                    return False, []
            else:
                if not (trend_bearish or trend_turned):
                    return False, []
        else:
            if side == "LONG" and trend_bearish:
                return False, []
            if side == "SHORT" and trend_bullish:
                return False, []

        if trend_turned:
            reasons.append("TREND_JUST_TURNED 🔄")
        elif trend_bullish if side == "LONG" else trend_bearish:
            reasons.append("TREND_✓")

        # ── Model 3: RSI confirms ──
        if side == "LONG":
            rsi_extreme = rsi < self.config.rsi_oversold
            rsi_turning = self._rsi_is_turning_up()
        else:
            rsi_extreme = rsi > self.config.rsi_overbought
            rsi_turning = self._rsi_is_turning_down()

        if not (rsi_extreme or rsi_turning):
            return False, []

        if rsi_extreme and rsi_turning:
            reasons.append(f"RSI={rsi:.2f}STRONG")
        elif rsi_extreme:
            reasons.append(f"RSI={rsi:.2f}extreme")
        else:
            reasons.append(f"RSI={rsi:.2f}turning")

        # ── TRUE noise suppression ──
        if self._should_suppress_signal(zscore):
            return False, []

        # ── Entry zone ──
        zone = self._get_entry_zone(abs_z, trend_is_turn)
        if zone is not None:
            zone_names = {0: "nibble", 1: "chunk", 2: "slam"}
            reasons.append(f"zone={zone_names.get(zone, zone)}")

        return True, reasons

    # =========================================================================
    # DOUBLE-TAP: ADD TO EXISTING POSITION
    # =========================================================================
    def _check_add_to_position(
        self,
        zscore: float,
        close: float,
        rsi: float,
        trend_bullish: bool,
        trend_bearish: bool,
        trend_turned: bool,
        trend_turned_bear: bool,
    ) -> None:
        """Check if we should add another leg (double-tap).

        James: "Yesterday I took a nibble, today I took a big chunk."

        Rules for adding:
        1. Must be in the SAME direction as current position
        2. Must be at a HIGHER zone than the last leg entered
           (price must have moved further against us to qualify)
        3. Z-score must still be turning (green/red dot)
        4. Trend must still confirm
        5. Not at max legs yet
        6. Cooldown between legs
        """
        if len(self._legs) >= self.config.max_legs:
            return
        if self._bars_since_entry < self.config.cooldown_bars:
            return

        abs_z = abs(zscore)
        trend_is_turn = self._bars_since_trend_turn <= self.config.trend_turn_lookback

        # Must be in a higher entry zone than the last leg
        last_zone = self._legs[-1].leg_index if self._legs else -1
        current_zone = self._get_entry_zone(abs_z, trend_is_turn)
        if current_zone is None or current_zone <= last_zone:
            return

        # Must be same direction and deeper (further from mean)
        if self._entry_side == "LONG":
            # Price must be LOWER (more oversold) than our last entry
            if zscore >= -self.config.entry_band:
                return
            if not self._zscore_is_turning_up():
                return
            # Trend still bullish?
            if self.config.require_strict_trend and not trend_bullish:
                return
            # RSI still confirms
            if not (rsi < self.config.rsi_oversold or self._rsi_is_turning_up()):
                return

            side = OrderSide.BUY

        elif self._entry_side == "SHORT":
            if zscore <= self.config.entry_band:
                return
            if not self._zscore_is_turning_down():
                return
            if self.config.require_strict_trend and not trend_bearish:
                return
            if not (rsi > self.config.rsi_overbought or self._rsi_is_turning_down()):
                return

            side = OrderSide.SELL

        else:
            return

        # Compute size for this leg
        size_usd = self._get_leg_size_usd(current_zone, zscore)
        if size_usd <= 0 or close <= 0:
            return

        qty_raw = size_usd / close
        quantity = self.instrument.make_qty(Decimal(str(qty_raw)))
        if quantity <= 0:
            return

        leg_num = len(self._legs) + 1
        zone_names = {0: "nibble", 1: "chunk", 2: "slam"}
        self.log.info(
            f"📥 DOUBLE-TAP {self._entry_side} (leg {leg_num}/{self.config.max_legs}): "
            f"Z={zscore:+.2f}σ zone={zone_names.get(current_zone, current_zone)} | "
            f"close={close:.4f} | RSI={rsi:.2f} | "
            f"size=${size_usd:.0f} ({quantity}) | "
            f"total_legs={leg_num}",
            LogColor.CYAN,
        )

        self._submit_add_leg(side, quantity, close, zscore, current_zone)

    # =========================================================================
    # EXIT LOGIC
    # =========================================================================
    def _check_exit(
        self,
        zscore: float,
        close: float,
        trend_bullish: bool,
        trend_bearish: bool,
        trend_turned_bull: bool,
        trend_turned_bear: bool,
    ) -> tuple[str, str] | None:
        """Check exit conditions. Returns (reason, mode) or None.

        mode = "LIFO_ONE" for take-profit (exit last leg only)
        mode = "EXIT_ALL" for risk/trend exits (dump everything)

        Exit priority:
        1. HARD % STOP → EXIT_ALL
        2. TP: opposite band → LIFO_ONE (only if multiple legs)
        3. TREND TURNS → EXIT_ALL
        4. TIME STOP → EXIT_ALL
        5. EMERGENCY → EXIT_ALL
        """
        if not self._legs:
            return None

        # Compute average entry price across all legs
        total_qty = sum(leg.quantity for leg in self._legs)
        if total_qty <= 0:
            return None
        avg_entry = sum(leg.price * leg.quantity for leg in self._legs) / total_qty

        if self._entry_side == "LONG":
            # ── 1. HARD % STOP ──
            if avg_entry > 0:
                drawdown_pct = (avg_entry - close) / avg_entry * 100.0
                if drawdown_pct >= self.config.hard_stop_pct:
                    return (
                        f"HARD_STOP: price {close:.4f} is -{drawdown_pct:.1f}% "
                        f"from avg_entry {avg_entry:.4f} (limit -{self.config.hard_stop_pct}%)",
                        "EXIT_ALL",
                    )

            # ── 2. Take profit: opposite band ──
            if zscore >= self.config.exit_opposite_band:
                mode = "LIFO_ONE" if len(self._legs) > 1 else "EXIT_ALL"
                return (
                    f"TP: Z={zscore:+.2f}σ ≥ {self.config.exit_opposite_band}σ (opposite band)",
                    mode,
                )

            # ── 3. Trend stop ──
            if trend_turned_bear:
                return ("TREND_STOP: Trend turned 🔴BEARISH", "EXIT_ALL")

            # ── 4. Time stop ──
            if self.config.max_hold_bars > 0 and self._bars_in_trade >= self.config.max_hold_bars:
                return (
                    f"TIME_STOP: held {self._bars_in_trade} bars "
                    f"(max {self.config.max_hold_bars})",
                    "EXIT_ALL",
                )

            # ── 5. Emergency sigma stop ──
            if zscore < -(self.config.max_adverse_sigma):
                return (f"EMERGENCY: Z={zscore:+.2f}σ", "EXIT_ALL")

        elif self._entry_side == "SHORT":
            # ── 1. HARD % STOP ──
            if avg_entry > 0:
                drawdown_pct = (close - avg_entry) / avg_entry * 100.0
                if drawdown_pct >= self.config.hard_stop_pct:
                    return (
                        f"HARD_STOP: price {close:.4f} is +{drawdown_pct:.1f}% "
                        f"from avg_entry {avg_entry:.4f} (limit +{self.config.hard_stop_pct}%)",
                        "EXIT_ALL",
                    )

            # ── 2. Take profit ──
            if zscore <= -self.config.exit_opposite_band:
                mode = "LIFO_ONE" if len(self._legs) > 1 else "EXIT_ALL"
                return (
                    f"TP: Z={zscore:+.2f}σ ≤ -{self.config.exit_opposite_band}σ (opposite band)",
                    mode,
                )

            # ── 3. Trend stop ──
            if trend_turned_bull:
                return ("TREND_STOP: Trend turned 🟢BULLISH", "EXIT_ALL")

            # ── 4. Time stop ──
            if self.config.max_hold_bars > 0 and self._bars_in_trade >= self.config.max_hold_bars:
                return (
                    f"TIME_STOP: held {self._bars_in_trade} bars "
                    f"(max {self.config.max_hold_bars})",
                    "EXIT_ALL",
                )

            # ── 5. Emergency ──
            if zscore > self.config.max_adverse_sigma:
                return (f"EMERGENCY: Z={zscore:+.2f}σ", "EXIT_ALL")

        return None

    # =========================================================================
    # POSITION MANAGEMENT — LIFO
    # =========================================================================
    def _make_aggressive_limit_price(self, side: OrderSide, close: float) -> Price:
        """Create an aggressive limit price for immediate fill.

        Hyperliquid doesn't support true market orders — they must be sent as
        limit orders priced through the book.  We add 0.5% slippage to ensure
        fill, then snap to the instrument's tick size.
        """
        SLIPPAGE_PCT = 0.005  # 0.5% — aggressive enough for immediate fill
        if side == OrderSide.BUY:
            aggressive_px = close * (1.0 + SLIPPAGE_PCT)
        else:
            aggressive_px = close * (1.0 - SLIPPAGE_PCT)
        return self.instrument.make_price(Decimal(str(round(aggressive_px, 4))))

    def _submit_entry(
        self,
        side: OrderSide,
        quantity,
        entry_price: float,
        zscore: float,
        zone: int,
    ) -> None:
        """Submit the first leg of a new position."""
        if not self.instrument:
            return

        try:
            limit_price = self._make_aggressive_limit_price(side, entry_price)
            order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=quantity,
                price=limit_price,
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self._entry_side = "LONG" if side == OrderSide.BUY else "SHORT"
            self._legs = [EntryLeg(
                price=entry_price,
                quantity=float(quantity),
                zscore=zscore,
                bar_number=self._bar_count,
                leg_index=zone,
            )]
            self._position_budget_used = float(quantity) * entry_price
            self._bars_since_entry = 0
            self._bars_in_trade = 0
            self._daily_trades += 1
            self._record_signal_strength(zscore)

        except Exception as e:
            self.log.error(f"Failed to submit entry: {e}")

    def _submit_add_leg(
        self,
        side: OrderSide,
        quantity,
        entry_price: float,
        zscore: float,
        zone: int,
    ) -> None:
        """Submit an additional leg (double-tap)."""
        if not self.instrument:
            return

        try:
            limit_price = self._make_aggressive_limit_price(side, entry_price)
            order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=quantity,
                price=limit_price,
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)

            self._legs.append(EntryLeg(
                price=entry_price,
                quantity=float(quantity),
                zscore=zscore,
                bar_number=self._bar_count,
                leg_index=zone,
            ))
            self._position_budget_used += float(quantity) * entry_price
            self._bars_since_entry = 0
            self._daily_trades += 1
            self._record_signal_strength(zscore)

        except Exception as e:
            self.log.error(f"Failed to submit add-leg: {e}")

    def _exit_lifo_one(self, close: float) -> None:
        """Exit the most recent leg only (LIFO).

        "LIFO means last in first out — the last one in is the first one I sell."

        We close just the quantity of the last leg, preserving earlier legs
        at their better average prices.
        """
        if not self.instrument or not self._legs:
            return

        # Cancel any pending add-leg orders before LIFO exit
        self.cancel_all_orders(self.instrument_id)

        last_leg = self._legs[-1]
        qty_to_close = last_leg.quantity

        try:
            # Close only the last leg's quantity
            close_side = OrderSide.SELL if self._entry_side == "LONG" else OrderSide.BUY
            quantity = self.instrument.make_qty(Decimal(str(qty_to_close)))

            if quantity > 0:
                limit_price = self._make_aggressive_limit_price(close_side, close)
                order = self.order_factory.limit(
                    instrument_id=self.instrument_id,
                    order_side=close_side,
                    quantity=quantity,
                    price=limit_price,
                    time_in_force=TimeInForce.IOC,
                    reduce_only=True,
                )
                self.submit_order(order)
                self._exit_pending = True

                self.log.info(
                    f"📤 LIFO EXIT submitted: "
                    f"qty={qty_to_close:.4f} @ ~{close:.4f} "
                    f"(entry was {last_leg.price:.4f} Z={last_leg.zscore:+.2f}σ) | "
                    f"legs={len(self._legs)}",
                    LogColor.YELLOW,
                )
                # Leg removal happens in on_order_filled when fill comes back

        except Exception as e:
            self.log.error(f"‼️ Failed LIFO exit: {e} — WILL RETRY NEXT BAR")
            self._exit_pending = True

    def _exit_all(self, close: float, reason: str = "") -> None:
        """Exit all legs at once (risk exit, trend exit, time exit).

        Uses a manual aggressive limit order instead of close_all_positions()
        because the Nautilus portfolio may not track our position correctly
        when the HYPERLIQUID-master account isn't registered.

        Safety features:
        - Cancels ALL pending orders first (prevents double-tap after exit)
        - Sets _exit_pending flag for retry tracking
        - Uses 1% slippage on retries (more aggressive than 0.5%)
        """
        if not self.instrument or not self._legs:
            return

        total_qty = sum(leg.quantity for leg in self._legs)
        if total_qty <= 0:
            return

        # Cancel ALL pending orders BEFORE exit (prevents new legs during exit)
        self.cancel_all_orders(self.instrument_id)

        try:
            close_side = OrderSide.SELL if self._entry_side == "LONG" else OrderSide.BUY
            quantity = self.instrument.make_qty(Decimal(str(total_qty)))

            if quantity > 0:
                # Use wider slippage on retries
                slippage = 0.005 if self._exit_retry_count == 0 else 0.01
                if close_side == OrderSide.BUY:
                    aggressive_px = close * (1.0 + slippage)
                else:
                    aggressive_px = close * (1.0 - slippage)
                limit_price = self.instrument.make_price(Decimal(str(round(aggressive_px, 4))))

                order = self.order_factory.limit(
                    instrument_id=self.instrument_id,
                    order_side=close_side,
                    quantity=quantity,
                    price=limit_price,
                    time_in_force=TimeInForce.IOC,
                    reduce_only=True,
                )
                self.submit_order(order)
                self._exit_pending = True
                self._exit_reason = reason

                retry_str = f" (retry {self._exit_retry_count})" if self._exit_retry_count > 0 else ""
                self.log.info(
                    f"🚪 EXIT ALL submitted{retry_str}: {close_side} {total_qty:.4f} "
                    f"@ ~{close:.4f} (slip={slippage*100:.1f}%) | "
                    f"legs={len(self._legs)} | reason={reason}",
                    LogColor.YELLOW,
                )
                # State cleanup happens in on_order_filled when fill comes back

        except Exception as e:
            self.log.error(f"‼️ Failed to exit all: {e} — WILL RETRY NEXT BAR")
            self._exit_pending = True  # Force retry on next bar

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================
    def on_order_filled(self, event) -> None:
        """Self-managed fill tracking.

        The Nautilus HL adapter may not register an Account object for
        HYPERLIQUID-master, which breaks Portfolio.net_position and
        all PositionOpened/Closed/Changed events.  We therefore track
        position state ourselves, exactly like the grid strategy does.
        """
        fill_qty = float(event.last_qty)
        fill_px = float(event.last_px)
        side = event.order_side

        self.log.info(
            f"📍 FILL: {side} {fill_qty:.4f} @ ${fill_px:.4f} "
            f"(entry_side={self._entry_side}, legs={len(self._legs)})",
            LogColor.CYAN,
        )

        # ── EXIT fill: side is opposite of _entry_side ──
        is_exit_fill = (
            (self._entry_side == "SHORT" and side == OrderSide.BUY) or
            (self._entry_side == "LONG" and side == OrderSide.SELL)
        )

        if is_exit_fill and self._legs:
            # Compute realized P&L for this exit fill
            total_qty = sum(leg.quantity for leg in self._legs)
            avg_entry = sum(leg.price * leg.quantity for leg in self._legs) / total_qty if total_qty > 0 else fill_px
            if self._entry_side == "LONG":
                pnl = (fill_px - avg_entry) * fill_qty
            else:
                pnl = (avg_entry - fill_px) * fill_qty

            # Remove closed quantity from legs (LIFO)
            remaining = fill_qty
            while remaining > 1e-9 and self._legs:
                last_leg = self._legs[-1]
                if last_leg.quantity <= remaining + 1e-9:
                    remaining -= last_leg.quantity
                    self._position_budget_used -= last_leg.quantity * last_leg.price
                    self._legs.pop()
                else:
                    last_leg.quantity -= remaining
                    self._position_budget_used -= remaining * last_leg.price
                    remaining = 0.0

            if not self._legs:
                # Fully closed — reset exit safety flags
                self._total_pnl += pnl
                self._daily_pnl += pnl
                self._entry_side = "FLAT"
                self._bars_in_trade = 0
                self._position_budget_used = 0.0
                self._exit_pending = False
                self._exit_retry_count = 0
                self._exit_reason = ""

                if pnl > 0:
                    self._wins += 1
                    emoji = "✅"
                else:
                    self._losses += 1
                    emoji = "❌"

                total_trades = self._wins + self._losses
                win_rate = (self._wins / total_trades * 100) if total_trades > 0 else 0

                self.log.info(
                    f"{emoji} CLOSED: pnl=${pnl:+.4f} | "
                    f"total=${self._total_pnl:+.2f} | today=${self._daily_pnl:+.2f} | "
                    f"W/L={self._wins}/{self._losses} ({win_rate:.0f}%) | "
                    f"signal was {self._last_signal}",
                    LogColor.GREEN if pnl > 0 else LogColor.RED,
                )
            else:
                self.log.info(
                    f"📤 LIFO partial exit: pnl=${pnl:+.4f} | "
                    f"remaining_legs={len(self._legs)} | "
                    f"budget=${self._position_budget_used:.2f}",
                    LogColor.CYAN,
                )

    def on_order_rejected(self, event) -> None:
        """Handle rejected exit orders — mark for retry on next bar.

        If an exit IOC is rejected (rate limit, network, insufficient margin
        for reduce_only, etc.), the position is still open. We MUST retry.
        """
        self.log.warning(
            f"⚠️ ORDER REJECTED: {event.reason if hasattr(event, 'reason') else event} | "
            f"exit_pending={self._exit_pending} entry_side={self._entry_side}",
        )
        # If we were trying to exit and the order was rejected, force retry
        if self._exit_pending and self._entry_side != "FLAT":
            self.log.warning(
                f"🔴 EXIT ORDER REJECTED — will retry on next bar "
                f"(retry {self._exit_retry_count}/{self._max_exit_retries})",
            )

    def on_order_canceled(self, event) -> None:
        """Handle canceled orders (including IOC that didn't fill)."""
        # IOC orders that don't fill get canceled — if this was an exit,
        # the _exit_pending flag stays True and triggers retry on next bar.
        if self._exit_pending and self._entry_side != "FLAT" and self._legs:
            self.log.warning(
                f"⚠️ EXIT IOC CANCELED (no fill) — will retry on next bar "
                f"(retry {self._exit_retry_count}/{self._max_exit_retries})",
            )

    def on_order_expired(self, event) -> None:
        """Handle expired orders — same as canceled for IOC exits."""
        if self._exit_pending and self._entry_side != "FLAT" and self._legs:
            self.log.warning(
                f"⚠️ EXIT ORDER EXPIRED — will retry on next bar "
                f"(retry {self._exit_retry_count}/{self._max_exit_retries})",
            )

    # =========================================================================
    # STARTUP RECONCILIATION — detect orphaned positions
    # =========================================================================
    def _reconcile_orphaned_position(self) -> None:
        """On startup, check HL for open positions that our _legs don't know about.

        If the process crashed mid-trade, we restart with _legs=[] (FLAT) but
        HL still has the open position. This method detects that and force-closes it.

        MULTI-STRATEGY SAFETY: When multiple strategies trade the same instrument
        in the same TradingNode (e.g. 5m workhorse + 1h sniper), we cannot know
        which strategy "owns" the orphaned position. We log a warning but only
        force-close if this is the ONLY strategy for this instrument in the node.
        If multiple strategies exist, we skip auto-close and let the operator decide.
        """
        try:
            import requests
            import os

            wallet = os.environ.get("HYPERLIQUID_WALLET", "")
            if not wallet:
                self.log.warning("RECONCILIATION: No HYPERLIQUID_WALLET — skipping orphan check")
                return

            resp = requests.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "clearinghouseState", "user": wallet},
                timeout=5,
            )
            state = resp.json()
            positions = state.get("assetPositions", [])

            # Extract our instrument's coin (e.g., "SOL" from "SOL-USD-PERP.HYPERLIQUID")
            coin = self.config.instrument_id.split("-")[0]

            # Check if other strategies in this node also trade this instrument
            # Uses class-level registry populated in __init__
            sibling_ids = [
                sid for sid in HLMeanReversion._instances_by_coin.get(coin, [])
                if sid != self.config.strategy_id
            ]

            for pos in positions:
                pos_info = pos.get("position", {})
                if pos_info.get("coin") == coin:
                    szi = float(pos_info.get("szi", 0))
                    entry_px = float(pos_info.get("entryPx", 0))

                    if abs(szi) > 1e-9:
                        # There's an orphaned position!
                        side_str = "LONG" if szi > 0 else "SHORT"
                        notional = abs(szi) * entry_px

                        if sibling_ids:
                            # Another strategy in this node also trades this coin.
                            # We can't know who owns the position — DON'T auto-close.
                            siblings = ", ".join(sibling_ids)
                            self.log.warning(
                                f"⚠️ ORPHANED {side_str} {abs(szi):.4f} {coin} "
                                f"@ ${entry_px:.4f} (${notional:.2f}) detected, but "
                                f"sibling strategies [{siblings}] also trade {coin} — "
                                f"SKIPPING auto-close (operator must reconcile manually)",
                            )
                            return

                        self.log.warning(
                            f"🔴 ORPHANED POSITION DETECTED: {side_str} "
                            f"{abs(szi):.4f} {coin} @ ${entry_px:.4f} "
                            f"(${notional:.2f} notional) — FORCE CLOSING",
                        )

                        # Reconstruct legs so exit logic works
                        self._entry_side = side_str
                        self._legs = [EntryLeg(
                            price=entry_px,
                            quantity=abs(szi),
                            zscore=0.0,
                            bar_number=0,
                            leg_index=0,
                        )]

                        # Force exit on next bar (can't exit yet — indicators not ready)
                        self._exit_pending = True
                        self._exit_reason = "ORPHANED_POSITION_FROM_CRASH"
                        return

            self.log.info(f"✅ RECONCILIATION: No orphaned {coin} positions on HL")

        except Exception as e:
            self.log.warning(f"RECONCILIATION check failed (non-fatal): {e}")

    def on_event(self, event: Event) -> None:
        # Keep as fallback — if Portfolio events DO fire, log them
        if isinstance(event, (PositionClosed, PositionOpened, PositionChanged)):
            self.log.info(
                f"📌 Portfolio event (info only): {type(event).__name__}",
                LogColor.NORMAL,
            )

    def on_reset(self) -> None:
        self.bb.reset()
        self.ema_fast.reset()
        self.ema_slow.reset()
        self.rsi.reset()
        self.atr.reset()
        self._zscore_history.clear()
        self._rsi_history.clear()
        self._recent_signal_z.clear()

    def on_save(self) -> dict[str, bytes]:
        return {}

    def on_load(self, state: dict[str, bytes]) -> None:
        pass

    def on_dispose(self) -> None:
        pass
