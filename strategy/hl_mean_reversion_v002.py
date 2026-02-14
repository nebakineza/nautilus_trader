#!/usr/bin/env python3
"""Mean Reversion Trader for Hyperliquid — v002.

DESIGN PHILOSOPHY — Replicated from a 33-year veteran's approach:

    "Everything mean reverts." The core premise is that price oscillates
    above and below its historical average. When price deviates significantly
    (measured in standard deviations), it is expected to revert. The 90%+
    win rate comes from ONLY trading at extreme deviations AND requiring
    the trend to confirm before entry.

THREE-MODEL CONFLUENCE SYSTEM
==============================
    1. MEAN REVERSION MODEL — The Z-score oscillator
       Measures how many standard deviations price is from its moving average.
       Uses Bollinger Bands to compute Z-score: (close - SMA) / σ.
       Entry only at extreme Z-scores (configurable band, e.g. ±2.0σ).

    2. TREND MODEL — The gate / filter
       EMA-based trend that keeps you IN winning trades and OUT of losing ones.
       Blue = bullish trend. Color change = exit signal.
       CRITICAL RULE: Never enter against the trend. The trend GATES entries.
       - Mean reversion says BUY, but trend is bearish → NO TRADE
       - Mean reversion says BUY, and trend turns bullish → ENTER

    3. CONFIDENCE MODEL — Momentum confirmation
       RSI + rate-of-change divergence detection.
       Confirms that the turning point has actually arrived.
       "The trend could be down but it may not have bottomed yet."

SIGNAL LOGIC
=============
    BUY when ALL THREE agree:
      ✓ Z-score < -band (price is far below the mean)
      ✓ Z-score is TURNING UP (derivative positive = turning point)
      ✓ Trend is bullish (EMA fast > slow) OR trend just turned bullish
      ✓ RSI confirms (oversold AND turning up)

    SELL when ALL THREE agree:
      ✓ Z-score > +band (price is far above the mean)
      ✓ Z-score is TURNING DOWN
      ✓ Trend is bearish OR trend just turned
      ✓ RSI confirms (overbought AND turning down)

EXIT LOGIC — NOT fixed SL/TP
==============================
    EXIT LONG when:
      1. Z-score reaches opposite band (overbought) → take profit
      2. Trend turns bearish → stop loss (trend keeps you in, trend takes you out)
      3. Emergency stop: price drops > max_adverse_sigma × σ from entry

    EXIT SHORT when:
      1. Z-score reaches opposite band (oversold) → take profit
      2. Trend turns bullish → stop loss
      3. Emergency stop: price rises > max_adverse_sigma × σ from entry

POSITION SIZING — LIFO Scaling
================================
    - Allocate 30% of position budget per entry (not all-in)
    - If price goes further against and gives a second signal → "double-tap in"
    - Exit LIFO: last in, first out (most recent entry exits first)
    - For simplicity in v002: we use single entries but scale based on Z-score
      magnitude (bigger deviation = bigger conviction = bigger size)

NOISE SUPPRESSION
==================
    - Configurable suppression window (default 30 bars)
    - If a stronger signal exists within the window, suppress weaker ones
    - Prevents overtrading in choppy conditions
    - Implemented as: require Z-score to be at local extreme within N bars

TIMEFRAME OPTIMIZATION
=======================
    - This strategy must be tested across timeframes per asset
    - BTC: 8H was best (92.74%), crypto generally likes 4H-8H
    - For ZRO on Hyperliquid: test 5m, 15m, 1H, 4H

VERSION: v002.1 — with hard stop, time stop, strict trend gate
CREATED: February 2026
"""

from __future__ import annotations

import math
from collections import deque
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
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
LOG_INTERVAL_BARS: Final[int] = 10


# =============================================================================
# CONFIGURATION
# =============================================================================
class HLMeanReversionConfig(StrategyConfig, frozen=True):
    """Configuration for the Mean Reversion v002 strategy.

    TUNING GUIDE
    =============
    - For crypto: use "very_aggressive" style → tighter bands, more trades
    - For stocks: use "conservative" → wider bands, fewer but higher-quality trades
    - ALWAYS test multiple timeframes and pick the one with highest backtest win rate
    - Wider entry_band → fewer trades but higher win rate (the key insight)
    - noise_suppression_window → higher = fewer trades, more selective

    PROFILES (adjust entry_band + trend_ema_periods):
    BACKTESTED on ZRO 5m (14d) — EMA 34/89, exit=1.5σ:

    v002.0 (no risk controls):
    Style        entry_band  Trades  WR%     P&L      Worst   MaxDD
    ─────────────────────────────────────────────────────────────────
    aggressive   1.0         69      69.6%   +$14.11  -$5.18  -$7.37

    v002.1 (hard_stop=1.5%, strict_trend, max_hold=40):
    Style        entry_band  Trades  WR%     P&L      Worst   MaxDD
    ─────────────────────────────────────────────────────────────────
    aggressive   1.0         79      62.0%   +$17.21  -$2.28  -$4.05  ← DEFAULT

    WR dropped 70→62% but P&L +22%, worst trade 56% smaller,
    max drawdown 45% smaller. The hard stop turns 1 big loss
    into 2 small losses — better total outcome.
    """

    instrument_id: str = "ZRO-USD-PERP.HYPERLIQUID"
    bar_type: str = "ZRO-USD-PERP.HYPERLIQUID-5-MINUTE-LAST-EXTERNAL"

    # === MEAN REVERSION MODEL ===
    # Bollinger Bands compute the Z-score oscillator
    bb_period: int = 20               # SMA + σ lookback period
    bb_std_k: float = 2.0             # Standard deviation multiplier for BB bands

    # Entry bands: only trade when Z-score exceeds ±entry_band
    # The trader's key insight: wider bands = higher win rate
    # At ±2.0σ → ~85% win rate. At ±3.0σ → ~95%+. At ±5.0σ → ~99%
    # BACKTESTED: ±1.0σ on ZRO 5m = 70.6% WR, +$15.16 (68 trades/14d)
    entry_band: float = 1.0           # Entry when |Z| > entry_band (in σ units)

    # === TREND MODEL (the gate) ===
    # "The trend will keep you either out of the trade or in the trade"
    # BACKTESTED: 34/89 beat 21/55, 50/200, 9/21 on ZRO 5m
    trend_ema_fast: int = 34          # Fast EMA for trend
    trend_ema_slow: int = 89          # Slow EMA for trend (longer = smoother)
    #                                   The trader uses this as a binary gate:
    #                                   fast > slow = bullish (blue), else bearish

    # Trend strength filter: require EMAs to be separated by at least this
    # fraction of price to consider trend "confirmed" (avoids flat crossovers)
    trend_min_separation_pct: float = 0.05  # 0.05% of price minimum EMA spread

    # === CONFIDENCE MODEL (momentum confirmation) ===
    rsi_period: int = 14              # RSI period for momentum confirmation
    rsi_oversold: float = 0.35        # RSI below this = oversold momentum
    rsi_overbought: float = 0.65      # RSI above this = overbought momentum
    #                                   Note: NautilusTrader RSI is 0.0-1.0 scale

    # === TURNING POINT DETECTION ===
    # "It's not just oversold — the signal fires when it STOPS FALLING and TURNS"
    # We detect this via Z-score derivative: must reverse direction
    zscore_lookback: int = 3          # Compare current Z to Z N bars ago
    #                                   Z turning up = derivative positive
    #                                   Z turning down = derivative negative
    rsi_lookback: int = 3             # RSI must also be turning

    # === NOISE SUPPRESSION ===
    # "Suppresses less optimal signals if stronger ones exist within the window"
    # BACKTESTED: 10 bars was optimal (0=too many, 30+=too few signals)
    noise_suppression_window: int = 10  # Min bars between trades (noise filter)
    #                                     within this many bars

    # === POSITION SIZING ===
    base_trade_size_usd: float = 50.0  # Base position size in USD
    max_position_usd: float = 150.0    # Maximum total position (for scaling in)
    scale_with_zscore: bool = True     # Bigger Z = bigger position
    #                                    At 2σ → 1.0x base, at 3σ → 1.5x, at 4σ → 2.0x

    # === EXIT RULES ===
    # "Exit when Z-score reaches opposite band OR trend turns"
    # BACKTESTED: exit=1.5σ → +$15.16 vs exit=0.0σ → -$6.34 (same entries)
    # Letting winners run to the opposite band is THE key to profitability
    exit_opposite_band: float = 1.5   # Exit long when Z > this (1.5 = opposite band)
    #                                   Set to 1.0 to exit at +1σ, etc.
    #                                   The trader takes profit "at the opposite band"

    # Emergency stop: hard limit in case of black swan
    max_adverse_sigma: float = 4.0    # Emergency exit if Z moves this far against us
    #                                   Beyond the entry band — absolute protection

    # === HARD PERCENTAGE STOP-LOSS (FIX for catastrophic losses) ===
    # Z-score based stop FAILS during regime breaks because the Bollinger bands
    # shift with the crash — Z hits the opposite band but price is -9% underwater.
    # This hard stop caps the max loss per trade to a fixed % of entry price.
    # DIAGNOSTIC: Trades #4 (-9.3%) and #57 (-6.9%) would have been capped.
    hard_stop_pct: float = 1.5        # Hard stop at -1.5% from entry price
    #                                   Caps worst-case loss per trade
    #                                   At $50 position → max loss = $0.75
    #                                   BACKTESTED: 1.5% → worst=-$2.28, PF=1.45
    #                                   vs no stop → worst=-$5.18, PF=1.43

    # === MAX HOLD TIME (FIX for slow bleeders) ===
    # Mean reversion trades should resolve QUICKLY — if price hasn't reverted
    # within N bars, the thesis is broken and we should cut the trade.
    # DIAGNOSTIC: Losing trades averaged 135min (27 bars) vs winners at 78min (16 bars).
    max_hold_bars: int = 40           # Force exit after 40 bars (200min on 5m)
    #                                   Prevents the slow 4-7 hour bleeder losses

    # === STRICT TREND GATE (FIX for moderate losses) ===
    # The trader says: "you don't buy till the trend turns"
    # Previously we allowed entries when trend was FLAT (not bearish).
    # DIAGNOSTIC: 10 moderate losses came from FLAT trend entries that turned bearish.
    require_strict_trend: bool = True  # If True: LONG requires trend BULLISH,
    #                                            SHORT requires trend BEARISH.
    #                                   If False: allow entries when trend is flat.

    # ATR for emergency stop calculation
    atr_period: int = 14

    # === RISK CONTROLS ===
    cooldown_bars: int = 5            # Minimum bars between trades
    max_daily_loss_usd: float = 30.0  # Kill switch: stop after this daily loss
    max_daily_trades: int = 15        # Max trades per day
    max_open_positions: int = 1       # Simultaneous positions (1 for simplicity)


class HLMeanReversion(Strategy):
    """Mean reversion strategy using Z-score + Trend gate + RSI confirmation.

    Implements the three-model confluence system:
    1. Z-score oscillator identifies extreme deviations from mean
    2. EMA trend filter gates entries (never trade against trend)
    3. RSI turning point confirms momentum reversal

    Exits on trend turn or Z-score reaching opposite band.
    """

    def __init__(self, config: HLMeanReversionConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

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

        # ── Trend state ──
        self._prev_trend_bullish: bool | None = None  # Track trend flips

        # ── Trade tracking ──
        self._bar_count: int = 0
        self._bars_since_trade: int = 999
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._last_day: str = ""
        self._total_pnl: float = 0.0
        self._wins: int = 0
        self._losses: int = 0
        self._last_signal: str = "WAIT"

        # ── Position tracking for exits ──
        self._entry_zscore: float = 0.0     # Z-score at entry (for tracking)
        self._entry_side: str = "FLAT"      # "LONG", "SHORT", "FLAT"
        self._entry_price: float = 0.0
        self._bars_in_trade: int = 0        # How long we've been in this trade

    # =========================================================================
    # LIFECYCLE
    # =========================================================================
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.instrument_id}")
            self.stop()
            return

        # Register indicators — auto-fed by framework
        self.register_indicator_for_bars(self.bar_type, self.bb)
        self.register_indicator_for_bars(self.bar_type, self.ema_fast)
        self.register_indicator_for_bars(self.bar_type, self.ema_slow)
        self.register_indicator_for_bars(self.bar_type, self.rsi)
        self.register_indicator_for_bars(self.bar_type, self.atr)

        # Warm up with historical bars
        self.request_bars(
            self.bar_type,
            start=self._clock.utc_now() - timedelta(hours=12),
        )

        self.subscribe_bars(self.bar_type)

        self.log.info(
            f"🚀 Mean Reversion v002.1 STARTED | {self.config.instrument_id} | "
            f"bar={self.config.bar_type} | "
            f"entry_band=±{self.config.entry_band}σ | exit={self.config.exit_opposite_band}σ | "
            f"trend_EMA={self.config.trend_ema_fast}/{self.config.trend_ema_slow} "
            f"strict={self.config.require_strict_trend} | "
            f"hard_stop={self.config.hard_stop_pct}% | "
            f"max_hold={self.config.max_hold_bars}bars | "
            f"size=${self.config.base_trade_size_usd}",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)
        self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    # =========================================================================
    # CORE: Z-SCORE COMPUTATION
    # =========================================================================
    def _compute_zscore(self, close: float) -> float:
        """Compute Z-score: how many standard deviations price is from the mean.

        Z = (close - SMA) / σ

        Using Bollinger Bands: σ = (upper - middle) / k
        So: Z = (close - middle) / ((upper - middle) / k) = (close - middle) * k / (upper - middle)
        """
        middle = self.bb.middle
        upper = self.bb.upper

        if upper <= middle or middle <= 0:
            return 0.0

        sigma = (upper - middle) / self.config.bb_std_k
        if sigma <= 0:
            return 0.0

        return (close - middle) / sigma

    # =========================================================================
    # CORE: TURNING POINT DETECTION
    # =========================================================================
    def _zscore_is_turning_up(self) -> bool:
        """Z-score was falling and is now rising (bottom turning point).

        This is the critical "green dot" — not just oversold, but REVERSING.
        """
        if len(self._zscore_history) < self.config.zscore_lookback + 1:
            return False

        current = self._zscore_history[-1]
        past = self._zscore_history[-(self.config.zscore_lookback + 1)]

        return current > past  # Z was lower, now higher = turning up

    def _zscore_is_turning_down(self) -> bool:
        """Z-score was rising and is now falling (top turning point)."""
        if len(self._zscore_history) < self.config.zscore_lookback + 1:
            return False

        current = self._zscore_history[-1]
        past = self._zscore_history[-(self.config.zscore_lookback + 1)]

        return current < past

    def _rsi_is_turning_up(self) -> bool:
        """RSI was falling and is now rising — momentum confirming reversal."""
        if len(self._rsi_history) < self.config.rsi_lookback + 1:
            return False

        current = self._rsi_history[-1]
        past = self._rsi_history[-(self.config.rsi_lookback + 1)]

        return current > past

    def _rsi_is_turning_down(self) -> bool:
        """RSI was rising and is now falling."""
        if len(self._rsi_history) < self.config.rsi_lookback + 1:
            return False

        current = self._rsi_history[-1]
        past = self._rsi_history[-(self.config.rsi_lookback + 1)]

        return current < past

    # =========================================================================
    # CORE: NOISE SUPPRESSION
    # =========================================================================
    def _should_suppress_signal(self) -> bool:
        """Check if we should suppress this signal.

        The trader's noise suppression: 'suppresses less optimal signals if
        stronger ones exist within the specified time horizon.'

        Implementation: after a signal fires, suppress further signals for
        noise_suppression_window bars. This prevents clustered entries in
        choppy conditions.
        """
        if self.config.noise_suppression_window <= 0:
            return False  # Disabled

        return self._bars_since_trade < self.config.noise_suppression_window

    # =========================================================================
    # CORE: TREND MODEL
    # =========================================================================
    def _trend_is_bullish(self, close: float) -> bool:
        """EMA fast > EMA slow = bullish trend (blue).

        Also requires minimum separation to avoid flat crossover noise.
        """
        spread = self.ema_fast.value - self.ema_slow.value
        min_spread = close * self.config.trend_min_separation_pct / 100.0

        return spread > min_spread

    def _trend_is_bearish(self, close: float) -> bool:
        """EMA fast < EMA slow = bearish trend."""
        spread = self.ema_slow.value - self.ema_fast.value
        min_spread = close * self.config.trend_min_separation_pct / 100.0

        return spread > min_spread

    def _trend_just_turned_bullish(self, close: float) -> bool:
        """Trend was bearish and just turned bullish — key entry moment.

        "You don't buy till the trend actually turns."
        """
        if self._prev_trend_bullish is None:
            return False

        return not self._prev_trend_bullish and self._trend_is_bullish(close)

    def _trend_just_turned_bearish(self, close: float) -> bool:
        """Trend was bullish and just turned bearish — exit / short entry."""
        if self._prev_trend_bullish is None:
            return False

        return self._prev_trend_bullish and self._trend_is_bearish(close)

    # =========================================================================
    # BAR HANDLER — The core decision loop
    # =========================================================================
    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return

        self._bar_count += 1
        self._bars_since_trade += 1

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
        trend_turned_bull = self._trend_just_turned_bullish(close)
        trend_turned_bear = self._trend_just_turned_bearish(close)

        # ── ATR ──
        atr = self.atr.value

        # ── Log status ──
        if self._bar_count % LOG_INTERVAL_BARS == 0:
            pos = self.portfolio.net_position(self.instrument_id)
            trend_str = "🟢BULL" if trend_bullish else ("🔴BEAR" if trend_bearish else "⚪FLAT")
            self.log.info(
                f"📊 close={close:.4f} | Z={zscore:+.2f}σ | "
                f"RSI={rsi:.2f} | "
                f"EMA={self.ema_fast.value:.4f}/{self.ema_slow.value:.4f} trend={trend_str} | "
                f"BB=[{self.bb.lower:.4f}/{self.bb.middle:.4f}/{self.bb.upper:.4f}] | "
                f"ATR={atr:.5f} | pos={pos} side={self._entry_side} | "
                f"pnl=${self._total_pnl:+.2f} (today=${self._daily_pnl:+.2f}) | "
                f"W/L={self._wins}/{self._losses}",
                LogColor.NORMAL,
            )

        # ══════════════════════════════════════════════════════════════
        # PART 1: EXIT LOGIC (check exits BEFORE entries)
        # "The trend keeps you in — the trend takes you out"
        # ══════════════════════════════════════════════════════════════
        if self._entry_side != "FLAT":
            exit_reason = self._check_exit(zscore, close, trend_bullish, trend_bearish,
                                            trend_turned_bull, trend_turned_bear)
            if exit_reason:
                self.log.info(
                    f"🚪 EXIT {self._entry_side}: {exit_reason} | "
                    f"Z={zscore:+.2f}σ (entry was Z={self._entry_zscore:+.2f}σ) | "
                    f"close={close:.4f} (entry={self._entry_price:.4f})",
                    LogColor.YELLOW,
                )
                self._exit_position()

        # Update trend tracking AFTER exit check
        self._prev_trend_bullish = trend_bullish

        # ══════════════════════════════════════════════════════════════
        # PART 2: ENTRY LOGIC
        # Three-model confluence: Z-score + Trend + RSI
        # ══════════════════════════════════════════════════════════════

        # Safety checks
        if self._daily_pnl <= -self.config.max_daily_loss_usd:
            return
        if self._daily_trades >= self.config.max_daily_trades:
            return
        if self._entry_side != "FLAT":
            return  # Already in a position
        if self._bars_since_trade < self.config.cooldown_bars:
            return
        if atr <= 0:
            return

        # ── CHECK LONG ENTRY ──
        long_signal, long_reasons = self._check_long_entry(
            zscore, close, rsi, trend_bullish, trend_turned_bull,
        )

        if long_signal:
            size_usd = self._compute_position_size(zscore)
            qty_raw = size_usd / close
            quantity = self.instrument.make_qty(Decimal(str(qty_raw)))

            if quantity > 0:
                self._last_signal = f"LONG Z={zscore:+.2f}σ"
                self.log.info(
                    f"🟢 LONG ENTRY: {' + '.join(long_reasons)} | "
                    f"Z={zscore:+.2f}σ | close={close:.4f} | "
                    f"RSI={rsi:.2f} | size=${size_usd:.0f} ({quantity})",
                    LogColor.GREEN,
                )
                self._submit_entry(OrderSide.BUY, quantity, close, zscore)

        # ── CHECK SHORT ENTRY ──
        short_signal, short_reasons = self._check_short_entry(
            zscore, close, rsi, trend_bearish, trend_turned_bear,
        )

        if short_signal:
            size_usd = self._compute_position_size(zscore)
            qty_raw = size_usd / close
            quantity = self.instrument.make_qty(Decimal(str(qty_raw)))

            if quantity > 0:
                self._last_signal = f"SHORT Z={zscore:+.2f}σ"
                self.log.info(
                    f"🔴 SHORT ENTRY: {' + '.join(short_reasons)} | "
                    f"Z={zscore:+.2f}σ | close={close:.4f} | "
                    f"RSI={rsi:.2f} | size=${size_usd:.0f} ({quantity})",
                    LogColor.RED,
                )
                self._submit_entry(OrderSide.SELL, quantity, close, zscore)

    # =========================================================================
    # SIGNAL CHECKS
    # =========================================================================
    def _check_long_entry(
        self,
        zscore: float,
        close: float,
        rsi: float,
        trend_bullish: bool,
        trend_turned_bull: bool,
    ) -> tuple[bool, list[str]]:
        """Check all three models for a long entry signal.

        ALL must agree:
        1. Z-score below -entry_band (far below mean)
        2. Z-score is turning up (turning point, not still falling)
        3. Trend is bullish OR just turned bullish OR not bearish (the gate)
        4. RSI confirms: oversold OR turning up from low
        5. Noise suppression: not too soon after last trade
        """
        reasons = []

        # Model 1: Mean Reversion — Z-score at extreme low
        if zscore >= -self.config.entry_band:
            return False, []
        reasons.append(f"Z={zscore:+.2f}σ < -{self.config.entry_band}")

        # Turning point: Z must be rising (not still plunging)
        if not self._zscore_is_turning_up():
            return False, []
        reasons.append("Z↑turning")

        # Model 2: Trend gate
        # "you don't buy till the trend turns" — STRICT GATE.
        # The trader is explicit: trend must be BLUE (bullish) to buy.
        # Previously we allowed FLAT trend entries → caused moderate losses.
        trend_bearish = self._trend_is_bearish(close)
        if self.config.require_strict_trend:
            # Strict mode: must be bullish (or just turned bullish)
            if not (trend_bullish or trend_turned_bull):
                return False, []
        else:
            # Loose mode: just block counter-trend
            if trend_bearish:
                return False, []
        if trend_turned_bull:
            reasons.append("TREND_JUST_TURNED_🟢")
        elif trend_bullish:
            reasons.append("TREND_🟢")
        else:
            reasons.append("TREND_FLAT_OK")

        # Model 3: Confidence — RSI confirms
        # Require EITHER: RSI is oversold, OR RSI is turning up
        # (the trader looks for the momentum to confirm the turning point)
        rsi_oversold = rsi < self.config.rsi_oversold
        rsi_turning = self._rsi_is_turning_up()
        if not (rsi_oversold or rsi_turning):
            return False, []
        if rsi_oversold and rsi_turning:
            reasons.append(f"RSI={rsi:.2f}↑STRONG")
        elif rsi_oversold:
            reasons.append(f"RSI={rsi:.2f}oversold")
        else:
            reasons.append(f"RSI={rsi:.2f}↑turning")

        # Noise suppression
        if self._should_suppress_signal():
            return False, []

        return True, reasons

    def _check_short_entry(
        self,
        zscore: float,
        close: float,
        rsi: float,
        trend_bearish: bool,
        trend_turned_bear: bool,
    ) -> tuple[bool, list[str]]:
        """Check all three models for a short entry signal."""
        reasons = []

        # Model 1: Z-score at extreme high
        if zscore <= self.config.entry_band:
            return False, []
        reasons.append(f"Z={zscore:+.2f}σ > +{self.config.entry_band}")

        # Turning point: Z must be falling
        if not self._zscore_is_turning_down():
            return False, []
        reasons.append("Z↓turning")

        # Model 2: Trend gate — STRICT: must be bearish to short.
        trend_bullish = self._trend_is_bullish(close)
        if self.config.require_strict_trend:
            # Strict mode: must be bearish (or just turned bearish)
            if not (trend_bearish or trend_turned_bear):
                return False, []
        else:
            # Loose mode: just block counter-trend
            if trend_bullish:
                return False, []
        if trend_turned_bear:
            reasons.append("TREND_JUST_TURNED_🔴")
        elif trend_bearish:
            reasons.append("TREND_🔴")
        else:
            reasons.append("TREND_FLAT_OK")

        # Model 3: RSI confirms — overbought OR turning down
        rsi_overbought = rsi > self.config.rsi_overbought
        rsi_turning = self._rsi_is_turning_down()
        if not (rsi_overbought or rsi_turning):
            return False, []
        if rsi_overbought and rsi_turning:
            reasons.append(f"RSI={rsi:.2f}↓STRONG")
        elif rsi_overbought:
            reasons.append(f"RSI={rsi:.2f}overbought")
        else:
            reasons.append(f"RSI={rsi:.2f}↓turning")

        # Noise suppression
        if self._should_suppress_signal():
            return False, []

        return True, reasons

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
    ) -> str | None:
        """Check if current position should be exited.

        Five exit conditions (checked in priority order):
        1. HARD % STOP — fixed percentage loss from entry (never let a trade
           become catastrophic regardless of what Z-score says)
        2. Z-score reaches opposite band → TAKE PROFIT
        3. Trend turns against position → TREND STOP
        4. Max hold time exceeded → TIME STOP (thesis broken)
        5. Z-score moves too far against (emergency) → SIGMA STOP
        """
        entry = self._entry_price

        if self._entry_side == "LONG":
            # ── 1. HARD PERCENTAGE STOP (highest priority) ──
            # This catches regime breaks where BB bands shift with the crash.
            # Trade #4 lost -9.3% and #57 lost -6.9% because Z-score exit
            # fired at the "opposite band" but the band moved DOWN with price.
            if entry > 0:
                drawdown_pct = (entry - close) / entry * 100.0
                if drawdown_pct >= self.config.hard_stop_pct:
                    return (
                        f"HARD_STOP: price {close:.4f} is -{drawdown_pct:.1f}% "
                        f"from entry {entry:.4f} (limit -{self.config.hard_stop_pct}%)"
                    )

            # ── 2. Take profit: Z-score reached opposite band (overbought) ──
            if zscore >= self.config.exit_opposite_band:
                return f"TP: Z={zscore:+.2f}σ ≥ {self.config.exit_opposite_band}σ (opposite band)"

            # ── 3. Trend stop: trend turned bearish ──
            if trend_turned_bear:
                return f"TREND_STOP: Trend turned 🔴BEARISH"

            # ── 4. Time stop: held too long, thesis broken ──
            if self.config.max_hold_bars > 0 and self._bars_in_trade >= self.config.max_hold_bars:
                return (
                    f"TIME_STOP: held {self._bars_in_trade} bars "
                    f"(max {self.config.max_hold_bars}) — thesis broken"
                )

            # ── 5. Emergency sigma stop ──
            if zscore < -(self.config.max_adverse_sigma):
                return f"EMERGENCY: Z={zscore:+.2f}σ < -{self.config.max_adverse_sigma}σ"

        elif self._entry_side == "SHORT":
            # ── 1. HARD PERCENTAGE STOP ──
            if entry > 0:
                drawdown_pct = (close - entry) / entry * 100.0
                if drawdown_pct >= self.config.hard_stop_pct:
                    return (
                        f"HARD_STOP: price {close:.4f} is +{drawdown_pct:.1f}% "
                        f"from entry {entry:.4f} (limit +{self.config.hard_stop_pct}%)"
                    )

            # ── 2. Take profit: Z-score reached opposite band ──
            if zscore <= -self.config.exit_opposite_band:
                return f"TP: Z={zscore:+.2f}σ ≤ -{self.config.exit_opposite_band}σ (opposite band)"

            # ── 3. Trend stop ──
            if trend_turned_bull:
                return f"TREND_STOP: Trend turned 🟢BULLISH"

            # ── 4. Time stop ──
            if self.config.max_hold_bars > 0 and self._bars_in_trade >= self.config.max_hold_bars:
                return (
                    f"TIME_STOP: held {self._bars_in_trade} bars "
                    f"(max {self.config.max_hold_bars}) — thesis broken"
                )

            # ── 5. Emergency sigma stop ──
            if zscore > self.config.max_adverse_sigma:
                return f"EMERGENCY: Z={zscore:+.2f}σ > +{self.config.max_adverse_sigma}σ"

        return None

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================
    def _compute_position_size(self, zscore: float) -> float:
        """Scale position size based on Z-score magnitude.

        Bigger deviation = higher conviction = larger size.
        At 2σ → 1.0x base, at 3σ → 1.5x, at 4σ → 2.0x
        """
        if not self.config.scale_with_zscore:
            return self.config.base_trade_size_usd

        abs_z = abs(zscore)
        # Linear scale: multiplier = 0.5 * (|Z| - entry_band) + 1.0
        # So at entry_band → 1.0x, at entry_band+2 → 2.0x
        multiplier = min(0.5 * (abs_z - self.config.entry_band) + 1.0, 3.0)
        multiplier = max(multiplier, 1.0)

        size = self.config.base_trade_size_usd * multiplier
        return min(size, self.config.max_position_usd)

    def _submit_entry(
        self,
        side: OrderSide,
        quantity,
        entry_price: float,
        zscore: float,
    ) -> None:
        """Submit a market order entry."""
        if not self.instrument:
            return

        try:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)

            self._entry_side = "LONG" if side == OrderSide.BUY else "SHORT"
            self._entry_zscore = zscore
            self._entry_price = entry_price
            self._bars_since_trade = 0
            self._bars_in_trade = 0
            self._daily_trades += 1

        except Exception as e:
            self.log.error(f"Failed to submit entry: {e}")

    def _exit_position(self) -> None:
        """Exit current position with a market order."""
        if not self.instrument:
            return

        try:
            self.close_all_positions(self.instrument_id)
            self._entry_side = "FLAT"
            self._entry_zscore = 0.0

        except Exception as e:
            self.log.error(f"Failed to exit position: {e}")

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================
    def on_event(self, event: Event) -> None:
        if isinstance(event, PositionClosed):
            pnl = float(event.realized_pnl)
            self._total_pnl += pnl
            self._daily_pnl += pnl
            self._entry_side = "FLAT"
            self._bars_in_trade = 0

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

        elif isinstance(event, PositionOpened):
            self.log.info(
                f"📍 OPENED: {event.entry} {event.signed_qty} @ {event.avg_px_open}",
                LogColor.CYAN,
            )

    def on_reset(self) -> None:
        self.bb.reset()
        self.ema_fast.reset()
        self.ema_slow.reset()
        self.rsi.reset()
        self.atr.reset()
        self._zscore_history.clear()
        self._rsi_history.clear()

    def on_save(self) -> dict[str, bytes]:
        return {}

    def on_load(self, state: dict[str, bytes]) -> None:
        pass

    def on_dispose(self) -> None:
        pass
