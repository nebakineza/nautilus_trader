#!/usr/bin/env python3
"""Signal-Based Directional Trader for Hyperliquid — v001.

DESIGN PHILOSOPHY:
    This is NOT a market maker. It does NOT place orders on both sides.
    It WAITS for high-confidence signals, enters ONE directional position
    with a stop-loss and take-profit, then waits for exit before re-entering.

    "Be like water" — Bruce Lee. Wait. Strike. Retreat. Repeat.

STRATEGY MODES:
    1. TREND SCALPER — Enter on pullbacks within a confirmed trend
       - EMA 9/21 cross determines trend direction
       - RSI pullback into oversold/overbought within trend = entry
       - Stochastics K/D cross confirms momentum reversal
       - Bollinger Band touch/pierce confirms overextension

    2. MEAN REVERSION — Enter on extreme readings in ranging markets
       - RSI oversold/overbought = potential reversal
       - Price touches Bollinger Band = overextension
       - Stochastics confirms reversal direction
       - BB width narrow = ranging market (good for mean reversion)

    The strategy auto-selects mode based on BB width:
       - Wide BB (trending) → Trend Scalper mode
       - Narrow BB (ranging) → Mean Reversion mode

RISK MANAGEMENT:
    - Every entry has a HARD stop-loss (ATR-based)
    - Every entry has a HARD take-profit (reward:risk ratio)
    - Maximum 1 position at a time
    - Cooldown between trades (no revenge trading)
    - Daily loss limit (kill switch)
    - Position sizing in USD (fixed notional)

SIGNAL CONFLUENCE:
    Entry requires 3+ of 4 indicators agreeing:
    ✓ RSI: Oversold (< 0.30) or Overbought (> 0.70)
    ✓ Bollinger: Price at/beyond band
    ✓ Stochastics: K crosses D in reversal direction
    ✓ EMA: Trend alignment (for scalper) or flat (for mean reversion)

    This multi-confirmation approach is directly from the Fast RSI
    Reversal Strategy concept: multiple filters reduce false signals.

INSTRUMENTS:
    ZRO-USD-PERP.HYPERLIQUID (or any Hyperliquid perp)

VERSION: v001.0
CREATED: February 2026
"""

from __future__ import annotations

import time
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
    Stochastics,
)
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.events import OrderFilled, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
BPS: Final[float] = 0.0001
LOG_INTERVAL_BARS: Final[int] = 5  # Log status every N bars


# =============================================================================
# CONFIGURATION
# =============================================================================
class HLSignalTraderConfig(StrategyConfig, frozen=True):
    """Configuration for Signal-Based Directional Trader.

    PROFILES
    ========
    Profile       SL mult  TP mult  RSI OS  RSI OB  Cooldown  Notes
    ------------- -------  -------  ------  ------  --------  -----
    conservative  2.5      2.0      0.25    0.75    5 bars    Fewer trades, wider stops
    balanced      2.0      1.5      0.30    0.70    3 bars    Default — good R:R
    aggressive    1.5      1.0      0.35    0.65    2 bars    More trades, tighter stops
    """

    instrument_id: str = "ZRO-USD-PERP.HYPERLIQUID"

    # === BAR TIMEFRAME ===
    # The primary signal bar. 5m is the sweet spot:
    # - Fast enough for scalping (entries every 5-30 min)
    # - Slow enough to filter noise (not whipsawed by tick noise)
    bar_type: str = "ZRO-USD-PERP.HYPERLIQUID-5-MINUTE-LAST-EXTERNAL"

    # === POSITION SIZING ===
    trade_size_usd: float = 50.0      # Fixed notional per trade in USD
    max_simultaneous: int = 1         # Only 1 position at a time

    # === INDICATOR PERIODS ===
    # RSI
    rsi_period: int = 14              # Standard RSI period
    rsi_oversold: float = 0.30        # RSI < 0.30 = oversold (NautilusTrader uses 0-1 scale)
    rsi_overbought: float = 0.70      # RSI > 0.70 = overbought
    rsi_extreme_oversold: float = 0.20  # RSI < 0.20 = extreme oversold (stronger signal)
    rsi_extreme_overbought: float = 0.80  # RSI > 0.80 = extreme overbought

    # EMA (trend direction)
    ema_fast_period: int = 9          # Fast EMA for trend
    ema_slow_period: int = 21         # Slow EMA for trend

    # Bollinger Bands
    bb_period: int = 20               # BB period
    bb_std: float = 2.0               # BB standard deviation multiplier

    # Stochastics (KDJ equivalent)
    stoch_k_period: int = 14          # %K period
    stoch_d_period: int = 3           # %D smoothing period
    stoch_oversold: float = 20.0      # %K < 20 = oversold
    stoch_overbought: float = 80.0    # %K > 80 = overbought

    # ATR (for stop-loss/take-profit sizing)
    atr_period: int = 14              # ATR period

    # === STOP-LOSS & TAKE-PROFIT ===
    sl_atr_multiplier: float = 2.0    # Stop-loss = entry ± (ATR × multiplier)
    tp_atr_multiplier: float = 3.0    # Take-profit = entry ± (ATR × multiplier)
    #                                   R:R ratio = tp/sl = 3.0/2.0 = 1.5:1
    #                                   Need >40% win rate to be profitable

    # === SIGNAL CONFLUENCE ===
    min_signals: int = 3              # Minimum indicator signals required (out of 4)

    # === REGIME DETECTION ===
    # BB width determines trending vs ranging:
    # - Wide BB = trending market → Trend Scalper mode
    # - Narrow BB = ranging market → Mean Reversion mode
    bb_width_trending_pct: float = 1.5   # BB width > 1.5% of mid = trending
    bb_width_ranging_pct: float = 0.8    # BB width < 0.8% of mid = ranging

    # === RISK CONTROLS ===
    cooldown_bars: int = 3            # Wait N bars after a trade before re-entering
    max_daily_loss_usd: float = 20.0  # Stop trading for the day after $20 loss
    max_daily_trades: int = 20        # Maximum trades per day

    # === CANDLE BODY FILTER (from Fast RSI strategy) ===
    # Require meaningful candle body to confirm signal
    # Body must be > body_filter_pct of the average body size
    body_filter_enabled: bool = True
    body_filter_pct: float = 0.20     # Body > 20% of avg body (1/5 ratio from article)
    body_avg_period: int = 20         # Average body size lookback


class HLSignalTrader(Strategy):
    """Signal-based directional trader using RSI, BB, EMA, Stochastics.

    Waits for high-confluence signals, enters with bracket orders
    (stop-loss + take-profit), exits on SL/TP hit or signal reversal.
    """

    def __init__(self, config: HLSignalTraderConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # ── Indicators ──
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.ema_fast = ExponentialMovingAverage(config.ema_fast_period)
        self.ema_slow = ExponentialMovingAverage(config.ema_slow_period)
        self.bb = BollingerBands(config.bb_period, config.bb_std)
        self.stoch = Stochastics(config.stoch_k_period, config.stoch_d_period)
        self.atr = AverageTrueRange(config.atr_period)

        # ── State ──
        self._bar_count: int = 0
        self._bars_since_trade: int = 999  # Start with no cooldown
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._last_day: str = ""
        self._prev_stoch_k: float = 0.0
        self._prev_stoch_d: float = 0.0
        self._total_pnl: float = 0.0
        self._wins: int = 0
        self._losses: int = 0
        self._last_signal: str = "NONE"
        self._regime: str = "UNKNOWN"

        # Candle body tracking
        self._body_sizes: list[float] = []

    # =========================================================================
    # LIFECYCLE
    # =========================================================================
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.instrument_id}")
            self.stop()
            return

        # Register indicators — framework auto-feeds bars to them
        self.register_indicator_for_bars(self.bar_type, self.rsi)
        self.register_indicator_for_bars(self.bar_type, self.ema_fast)
        self.register_indicator_for_bars(self.bar_type, self.ema_slow)
        self.register_indicator_for_bars(self.bar_type, self.bb)
        self.register_indicator_for_bars(self.bar_type, self.stoch)
        self.register_indicator_for_bars(self.bar_type, self.atr)

        # Request historical bars to warm up indicators
        self.request_bars(
            self.bar_type,
            start=self._clock.utc_now() - timedelta(hours=6),
        )

        # Subscribe to live bars
        self.subscribe_bars(self.bar_type)

        self.log.info(
            f"🚀 Signal Trader v001 started | {self.config.instrument_id} | "
            f"bar={self.config.bar_type} | size=${self.config.trade_size_usd} | "
            f"SL={self.config.sl_atr_multiplier}×ATR | TP={self.config.tp_atr_multiplier}×ATR | "
            f"R:R={self.config.tp_atr_multiplier/self.config.sl_atr_multiplier:.1f}:1",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)
        # Close all positions with market order on stop
        self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    # =========================================================================
    # BAR HANDLER — The core decision loop
    # =========================================================================
    def on_bar(self, bar: Bar) -> None:
        # ── Warmup check ──
        if not self.indicators_initialized():
            self.log.info(
                f"⏳ Warming up indicators [{self.cache.bar_count(self.bar_type)}]",
                color=LogColor.BLUE,
            )
            return

        self._bar_count += 1
        self._bars_since_trade += 1

        # ── Daily reset ──
        today = str(bar.ts_event)[:10]  # YYYY-MM-DD
        if today != self._last_day:
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._last_day = today

        # ── Extract bar data ──
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        open_price = float(bar.open)
        body = abs(close - open_price)

        # Track body sizes for candle filter
        self._body_sizes.append(body)
        if len(self._body_sizes) > self.config.body_avg_period:
            self._body_sizes.pop(0)

        # ── Read indicators ──
        rsi = self.rsi.value           # 0.0 - 1.0
        ema_f = self.ema_fast.value
        ema_s = self.ema_slow.value
        bb_upper = self.bb.upper
        bb_middle = self.bb.middle
        bb_lower = self.bb.lower
        stoch_k = self.stoch.value_k
        stoch_d = self.stoch.value_d
        atr = self.atr.value

        # ── Determine regime (trending vs ranging) ──
        bb_width_pct = 0.0
        if bb_middle > 0:
            bb_width_pct = ((bb_upper - bb_lower) / bb_middle) * 100.0

        if bb_width_pct > self.config.bb_width_trending_pct:
            self._regime = "TRENDING"
        elif bb_width_pct < self.config.bb_width_ranging_pct:
            self._regime = "RANGING"
        else:
            self._regime = "NEUTRAL"

        # ── Trend direction ──
        ema_spread = ema_f - ema_s
        trend_up = ema_f > ema_s
        trend_down = ema_f < ema_s

        # ── Log status periodically ──
        if self._bar_count % LOG_INTERVAL_BARS == 0:
            pos = self.portfolio.net_position(self.instrument_id)
            self.log.info(
                f"📊 close={close:.4f} | RSI={rsi:.2f} | "
                f"EMA={ema_f:.4f}/{ema_s:.4f} ({'+' if trend_up else '-'}{abs(ema_spread):.4f}) | "
                f"BB=[{bb_lower:.4f}/{bb_middle:.4f}/{bb_upper:.4f}] w={bb_width_pct:.2f}% | "
                f"K/D={stoch_k:.1f}/{stoch_d:.1f} | ATR={atr:.5f} | "
                f"regime={self._regime} | pos={pos} | "
                f"pnl=${self._total_pnl:+.2f} (today=${self._daily_pnl:+.2f}) | "
                f"W/L={self._wins}/{self._losses} | "
                f"signal={self._last_signal}",
                LogColor.NORMAL,
            )

        # ── Safety checks ──
        if self._daily_pnl <= -self.config.max_daily_loss_usd:
            if self._bar_count % LOG_INTERVAL_BARS == 0:
                self.log.warning(
                    f"🛑 Daily loss limit hit: ${self._daily_pnl:.2f} | "
                    f"Pausing until tomorrow",
                )
            return

        if self._daily_trades >= self.config.max_daily_trades:
            if self._bar_count % LOG_INTERVAL_BARS == 0:
                self.log.warning(
                    f"🛑 Daily trade limit hit: {self._daily_trades} trades | "
                    f"Pausing until tomorrow",
                )
            return

        # ── Already in position? Don't enter another ──
        if not self.portfolio.is_flat(self.instrument_id):
            return

        # ── Cooldown check ──
        if self._bars_since_trade < self.config.cooldown_bars:
            return

        # ── Candle body filter (from Fast RSI article) ──
        if self.config.body_filter_enabled and len(self._body_sizes) >= 5:
            avg_body = sum(self._body_sizes) / len(self._body_sizes)
            if avg_body > 0 and body < avg_body * self.config.body_filter_pct:
                # Candle body too small — low volatility, skip
                return

        # ── ATR sanity ──
        if atr <= 0:
            return

        # ════════════════════════════════════════════════════════════
        # SIGNAL GENERATION
        # ════════════════════════════════════════════════════════════

        # Stochastics crossover detection
        stoch_cross_up = (
            self._prev_stoch_k < self._prev_stoch_d
            and stoch_k > stoch_d
        )
        stoch_cross_down = (
            self._prev_stoch_k > self._prev_stoch_d
            and stoch_k < stoch_d
        )

        # Save for next bar
        self._prev_stoch_k = stoch_k
        self._prev_stoch_d = stoch_d

        # ── LONG SIGNALS ──
        long_signals = 0
        long_reasons = []

        # 1. RSI oversold
        if rsi < self.config.rsi_oversold:
            long_signals += 1
            extra = " (EXTREME)" if rsi < self.config.rsi_extreme_oversold else ""
            long_reasons.append(f"RSI={rsi:.2f}{extra}")

        # 2. Price at/below lower Bollinger Band
        if close <= bb_lower:
            long_signals += 1
            long_reasons.append(f"BB_LOW={bb_lower:.4f}")

        # 3. Stochastics K crosses above D in oversold zone
        if stoch_cross_up and stoch_k < self.config.stoch_overbought:
            long_signals += 1
            long_reasons.append(f"STOCH_CROSS_UP K={stoch_k:.1f}")

        # 4. Trend alignment
        if self._regime == "TRENDING" and trend_up:
            # In trend mode: go with trend (buy pullback in uptrend)
            long_signals += 1
            long_reasons.append("TREND_UP")
        elif self._regime == "RANGING":
            # In ranging mode: mean reversion — no trend required, count as signal
            long_signals += 1
            long_reasons.append("RANGE_LONG")
        elif self._regime == "NEUTRAL" and not trend_down:
            # Neutral regime, not actively trending down — mild positive
            long_signals += 1
            long_reasons.append("NEUTRAL_OK")

        # ── SHORT SIGNALS ──
        short_signals = 0
        short_reasons = []

        # 1. RSI overbought
        if rsi > self.config.rsi_overbought:
            short_signals += 1
            extra = " (EXTREME)" if rsi > self.config.rsi_extreme_overbought else ""
            short_reasons.append(f"RSI={rsi:.2f}{extra}")

        # 2. Price at/above upper Bollinger Band
        if close >= bb_upper:
            short_signals += 1
            short_reasons.append(f"BB_HIGH={bb_upper:.4f}")

        # 3. Stochastics K crosses below D in overbought zone
        if stoch_cross_down and stoch_k > self.config.stoch_oversold:
            short_signals += 1
            short_reasons.append(f"STOCH_CROSS_DN K={stoch_k:.1f}")

        # 4. Trend alignment
        if self._regime == "TRENDING" and trend_down:
            short_signals += 1
            short_reasons.append("TREND_DN")
        elif self._regime == "RANGING":
            short_signals += 1
            short_reasons.append("RANGE_SHORT")
        elif self._regime == "NEUTRAL" and not trend_up:
            short_signals += 1
            short_reasons.append("NEUTRAL_OK")

        # ════════════════════════════════════════════════════════════
        # ENTRY DECISION
        # ════════════════════════════════════════════════════════════

        if long_signals >= self.config.min_signals:
            self._last_signal = f"LONG({long_signals}/4)"
            sl_price = close - (atr * self.config.sl_atr_multiplier)
            tp_price = close + (atr * self.config.tp_atr_multiplier)

            self.log.info(
                f"🟢 LONG SIGNAL ({long_signals}/4): {', '.join(long_reasons)} | "
                f"entry={close:.4f} SL={sl_price:.4f} TP={tp_price:.4f} | "
                f"R:R={self.config.tp_atr_multiplier/self.config.sl_atr_multiplier:.1f}:1 | "
                f"ATR={atr:.5f}",
                LogColor.GREEN,
            )
            self._enter_position(OrderSide.BUY, close, sl_price, tp_price)

        elif short_signals >= self.config.min_signals:
            self._last_signal = f"SHORT({short_signals}/4)"
            sl_price = close + (atr * self.config.sl_atr_multiplier)
            tp_price = close - (atr * self.config.tp_atr_multiplier)

            self.log.info(
                f"🔴 SHORT SIGNAL ({short_signals}/4): {', '.join(short_reasons)} | "
                f"entry={close:.4f} SL={sl_price:.4f} TP={tp_price:.4f} | "
                f"R:R={self.config.tp_atr_multiplier/self.config.sl_atr_multiplier:.1f}:1 | "
                f"ATR={atr:.5f}",
                LogColor.RED,
            )
            self._enter_position(OrderSide.SELL, close, sl_price, tp_price)

        else:
            self._last_signal = f"WAIT(L={long_signals},S={short_signals})"

    # =========================================================================
    # ORDER MANAGEMENT
    # =========================================================================
    def _enter_position(
        self,
        side: OrderSide,
        entry_price: float,
        sl_price: float,
        tp_price: float,
    ) -> None:
        """Submit a bracket order: market entry + SL + TP."""
        if not self.instrument:
            return

        # Calculate quantity from USD notional
        qty_raw = self.config.trade_size_usd / entry_price
        quantity = self.instrument.make_qty(Decimal(str(qty_raw)))

        if quantity <= 0:
            self.log.warning(f"Computed quantity is zero (price={entry_price}, size=${self.config.trade_size_usd})")
            return

        # Submit bracket order (market entry + SL + TP)
        try:
            order_list = self.order_factory.bracket(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=quantity,
                entry_order_type=OrderType.MARKET,
                sl_trigger_price=self.instrument.make_price(Decimal(str(sl_price))),
                tp_price=self.instrument.make_price(Decimal(str(tp_price))),
            )

            self.submit_order_list(order_list)

            self._bars_since_trade = 0
            self._daily_trades += 1

            self.log.info(
                f"📤 Bracket submitted: {side.name} {quantity} @ MARKET | "
                f"SL={sl_price:.4f} | TP={tp_price:.4f} | "
                f"trade #{self._daily_trades} today",
                LogColor.CYAN,
            )

        except Exception as e:
            self.log.error(f"Failed to submit bracket order: {e}")

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================
    def on_event(self, event: Event) -> None:
        """Track P&L from position events."""
        if isinstance(event, PositionClosed):
            pnl = float(event.realized_pnl)
            self._total_pnl += pnl
            self._daily_pnl += pnl

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
                f"📍 POSITION OPENED: {event.entry} {event.signed_qty} @ {event.avg_px_open} | "
                f"SL/TP bracket active",
                LogColor.CYAN,
            )

    def on_reset(self) -> None:
        self.rsi.reset()
        self.ema_fast.reset()
        self.ema_slow.reset()
        self.bb.reset()
        self.stoch.reset()
        self.atr.reset()
        self._body_sizes.clear()

    def on_save(self) -> dict[str, bytes]:
        return {}

    def on_load(self, state: dict[str, bytes]) -> None:
        pass

    def on_dispose(self) -> None:
        pass
