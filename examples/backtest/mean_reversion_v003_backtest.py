#!/usr/bin/env python3
"""Backtest Mean Reversion v003 "JAMES" — faithful to James's methodology.

Compares v003 against v002.1 baseline across parameter sweeps.

USAGE:
    .venv/bin/python examples/backtest/mean_reversion_v003_backtest.py
"""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from examples.backtest.mean_reversion_v002_backtest import (
    HYPERLIQUID_VENUE,
    candles_to_bars,
    create_instrument,
    download_candles,
    TIMEFRAMES,
)
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money

from strategy.hl_mean_reversion_v003 import HLMeanReversion, HLMeanReversionConfig


def run_v003(
    coin: str = "ZRO",
    interval: str = "5m",
    days: int = 14,
    # Entry bands
    entry_band: float = 1.5,
    entry_band_outer: float = 2.5,
    exit_opposite_band: float = 1.5,
    # Trend
    trend_ema_fast: int = 34,
    trend_ema_slow: int = 89,
    trend_turn_lookback: int = 5,
    trend_turn_z_bonus: float = 0.3,
    # Noise
    noise_suppression_window: int = 10,
    noise_suppression_ratio: float = 0.7,
    # Risk
    hard_stop_pct: float = 1.5,
    max_hold_bars: int = 40,
    cooldown_bars: int = 5,
    # Sizing
    base_trade_size_usd: float = 150.0,
    max_position_usd: float = 200.0,
    # Label
    label: str = "default",
) -> dict:
    """Run a single v003 backtest and return results."""
    tf_info = TIMEFRAMES.get(interval)
    if not tf_info:
        raise ValueError(f"Unsupported: {interval}")

    hl_interval, bar_suffix, max_days = tf_info
    actual_days = min(days, max_days)
    df = download_candles(coin, hl_interval, actual_days)

    instrument_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
    bar_type_str = f"{instrument_id_str}-{bar_suffix}"
    instrument = create_instrument(coin)
    bars = candles_to_bars(df, instrument, bar_type_str)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-003"),
        logging=LoggingConfig(log_level="ERROR", use_pyo3=False),
    ))
    engine.add_venue(
        venue=HYPERLIQUID_VENUE, oms_type=OmsType.NETTING,
        book_type=BookType.L1_MBP, account_type=AccountType.MARGIN,
        base_currency=USD, starting_balances=[Money(1000.0, USD)],
        bar_execution=True,
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    config = HLMeanReversionConfig(
        strategy_id=f"BT-MR-{coin}-003",
        instrument_id=instrument_id_str,
        bar_type=bar_type_str,
        entry_band=entry_band,
        entry_band_outer=entry_band_outer,
        exit_opposite_band=exit_opposite_band,
        trend_ema_fast=trend_ema_fast,
        trend_ema_slow=trend_ema_slow,
        trend_turn_lookback=trend_turn_lookback,
        trend_turn_z_bonus=trend_turn_z_bonus,
        noise_suppression_window=noise_suppression_window,
        noise_suppression_ratio=noise_suppression_ratio,
        hard_stop_pct=hard_stop_pct,
        max_hold_bars=max_hold_bars,
        cooldown_bars=cooldown_bars,
        base_trade_size_usd=base_trade_size_usd,
        max_position_usd=max_position_usd,
    )
    strategy = HLMeanReversion(config=config)
    engine.add_strategy(strategy)

    t0 = time.time()
    engine.run()
    elapsed = time.time() - t0

    total = strategy._wins + strategy._losses
    wr = (strategy._wins / total * 100) if total > 0 else 0
    avg = (strategy._total_pnl / total) if total > 0 else 0

    result = {
        "label": label,
        "coin": coin,
        "interval": interval,
        "bars": len(bars),
        "trades": total,
        "wins": strategy._wins,
        "losses": strategy._losses,
        "win_rate": wr,
        "total_pnl": strategy._total_pnl,
        "avg_pnl": avg,
        "entry_band": entry_band,
        "outer_band": entry_band_outer,
        "exit_band": exit_opposite_band,
        "trend_ema": f"{trend_ema_fast}/{trend_ema_slow}",
        "noise_ratio": noise_suppression_ratio,
        "elapsed": elapsed,
    }
    engine.dispose()
    return result


def print_table(results: list[dict], title: str = "RESULTS") -> None:
    print(f"\n{'=' * 115}")
    print(f"📊 {title}")
    print(f"{'=' * 115}")
    print(
        f"{'Label':<32} {'TF':<4} {'Trades':>6} {'W':>4} {'L':>4} "
        f"{'WR%':>6} {'P&L':>10} {'Avg':>8} {'Inner':>5} {'Outer':>5} {'Exit':>5}"
    )
    print("-" * 115)

    for r in results:
        c = "\033[92m" if r["total_pnl"] > 0 else "\033[91m"
        e = "\033[0m"
        print(
            f"{r['label']:<32} {r['interval']:<4} {r['trades']:>6} "
            f"{r['wins']:>4} {r['losses']:>4} {r['win_rate']:>5.1f}% "
            f"{c}${r['total_pnl']:>+8.2f}{e} ${r['avg_pnl']:>+7.3f} "
            f"{r['entry_band']:>5.1f} {r['outer_band']:>5.1f} {r['exit_band']:>5.1f}"
        )
    print("=" * 115)


def main():
    coin = "ZRO"
    days = 14

    print("=" * 115)
    print(f"🎯 Mean Reversion v003 JAMES — {coin} Backtest")
    print(f"   Five improvements: Double-tap LIFO, Quality noise suppression,")
    print(f"   Trend-turn preference, Two-zone entry, Z-score-scaled sizing")
    print("=" * 115)

    all_results = []

    # ═══════════════════════════════════════════════════
    # PHASE 1: BAND SWEEP (inner × outer combinations)
    # ═══════════════════════════════════════════════════
    print("\n🔬 PHASE 1: INNER × OUTER BAND SWEEP")
    print("-" * 70)

    band_combos = [
        (1.0, 2.0, "in=1.0 out=2.0"),
        (1.2, 2.0, "in=1.2 out=2.0"),
        (1.5, 2.5, "in=1.5 out=2.5"),
        (1.5, 3.0, "in=1.5 out=3.0"),
        (2.0, 3.0, "in=2.0 out=3.0"),
        (2.0, 3.5, "in=2.0 out=3.5"),
    ]

    for inner, outer, name in band_combos:
        try:
            r = run_v003(coin=coin, days=days, entry_band=inner, entry_band_outer=outer, label=name)
            all_results.append(r)
            print(f"  ✅ {name}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.2f}")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    print_table(all_results, "BAND SWEEP")

    # ═══════════════════════════════════════════════════
    # PHASE 2: TREND-TURN BONUS SWEEP
    # ═══════════════════════════════════════════════════
    print("\n🔬 PHASE 2: TREND-TURN BONUS SWEEP")
    print("-" * 70)

    turn_results = []
    for bonus in [0.0, 0.2, 0.3, 0.5, 0.8]:
        for lookback in [3, 5, 8]:
            name = f"bonus={bonus} look={lookback}"
            try:
                r = run_v003(
                    coin=coin, days=days,
                    entry_band=1.5, entry_band_outer=2.5,
                    trend_turn_z_bonus=bonus, trend_turn_lookback=lookback,
                    label=name,
                )
                turn_results.append(r)
                all_results.append(r)
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    # Best 5 by WR
    turn_results.sort(key=lambda x: x["win_rate"], reverse=True)
    for r in turn_results[:5]:
        print(f"  {r['label']}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.2f}")

    # ═══════════════════════════════════════════════════
    # PHASE 3: NOISE SUPPRESSION RATIO SWEEP
    # ═══════════════════════════════════════════════════
    print("\n🔬 PHASE 3: NOISE SUPPRESSION RATIO")
    print("-" * 70)

    noise_results = []
    for ratio in [0.0, 0.5, 0.7, 0.9]:
        name = f"ratio={ratio}"
        try:
            r = run_v003(
                coin=coin, days=days,
                entry_band=1.5, entry_band_outer=2.5,
                noise_suppression_ratio=ratio,
                label=name,
            )
            noise_results.append(r)
            all_results.append(r)
            print(f"  ✅ {name}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.2f}")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    # ═══════════════════════════════════════════════════
    # PHASE 4: TIMEFRAME SWEEP
    # ═══════════════════════════════════════════════════
    print("\n🔬 PHASE 4: TIMEFRAME SWEEP (best params)")
    print("-" * 70)

    tf_results = []
    for tf in ["5m", "15m", "1h", "4h"]:
        try:
            r = run_v003(
                coin=coin, interval=tf, days=days,
                entry_band=1.5, entry_band_outer=2.5,
                label=f"TF={tf}",
            )
            tf_results.append(r)
            all_results.append(r)
            print(f"  ✅ {tf}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.2f}")
        except Exception as e:
            print(f"  ❌ {tf}: {e}")

    print_table(tf_results, "TIMEFRAME SWEEP")

    # ═══════════════════════════════════════════════════
    # GRAND SUMMARY
    # ═══════════════════════════════════════════════════
    seen = set()
    unique = []
    for r in all_results:
        key = r["label"] + r.get("interval", "5m")
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(key=lambda x: x["total_pnl"], reverse=True)
    print_table(unique[:15], "TOP 15 BY P&L")

    tradeable = [r for r in unique if r["trades"] >= 5]
    if tradeable:
        tradeable.sort(key=lambda x: x["win_rate"], reverse=True)
        print_table(tradeable[:10], "TOP 10 BY WIN RATE (≥5 trades)")

    # Save
    results_file = Path(__file__).resolve().parent.parent.parent / "backtest_results" / "mr_v003_sweep.json"
    results_file.parent.mkdir(parents=True, exist_ok=True)
    with open(results_file, "w") as f:
        json.dump(unique, f, indent=2)
    print(f"\n💾 Results saved to {results_file}")


if __name__ == "__main__":
    main()
