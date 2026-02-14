#!/usr/bin/env python3
"""Optuna optimizer for Mean Reversion v003 "JAMES".

Two-phase optimization:
  Phase 1: Multi-objective (WR% + P&L) — find the Pareto frontier
  Phase 2: Single-objective composite — find the single best config

Search space covers all James-relevant knobs:
  - Entry bands (inner, outer, exit)
  - BB period (the SMA lookback for Z-score)
  - Trend EMA periods + tightness
  - Trend-turn bonus
  - Noise suppression quality ratio
  - RSI thresholds + turning point lookback
  - Z-score lookback (for green/red dot detection)
  - Cooldown, max hold, hard stop

Runs across multiple timeframes and coins for robustness.

USAGE:
    .venv/bin/python examples/backtest/mean_reversion_v003_optuna.py
    .venv/bin/python examples/backtest/mean_reversion_v003_optuna.py --trials 500
    .venv/bin/python examples/backtest/mean_reversion_v003_optuna.py --coin SUI --interval 15m
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import optuna
from optuna.samplers import TPESampler

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
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money

from strategy.hl_mean_reversion_v003 import HLMeanReversion, HLMeanReversionConfig


# ─────────────────────────────────────────────────────────────
# Data cache — download once, reuse across all trials
# ─────────────────────────────────────────────────────────────
_DATA_CACHE: dict[str, tuple] = {}


def get_data(coin: str, interval: str, days: int):
    """Download and cache data + instrument + bars."""
    key = f"{coin}_{interval}_{days}"
    if key not in _DATA_CACHE:
        tf_info = TIMEFRAMES[interval]
        hl_interval, bar_suffix, max_days = tf_info
        actual_days = min(days, max_days)
        df = download_candles(coin, hl_interval, actual_days)
        instrument = create_instrument(coin)
        instrument_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
        bar_type_str = f"{instrument_id_str}-{bar_suffix}"
        bars = candles_to_bars(df, instrument, bar_type_str)
        _DATA_CACHE[key] = (instrument, instrument_id_str, bar_type_str, bars)
    return _DATA_CACHE[key]


# ─────────────────────────────────────────────────────────────
# Run a single backtest
# ─────────────────────────────────────────────────────────────
def run_backtest(
    coin: str,
    interval: str,
    days: int,
    params: dict,
) -> dict:
    """Run one backtest with given params, return metrics."""
    instrument, instrument_id_str, bar_type_str, bars = get_data(coin, interval, days)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("OPTUNA-003"),
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
        strategy_id="OPTUNA",
        instrument_id=instrument_id_str,
        bar_type=bar_type_str,
        **params,
    )
    strategy = HLMeanReversion(config=config)
    engine.add_strategy(strategy)
    engine.run()

    wins = strategy._wins
    losses = strategy._losses
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0.0
    pnl = strategy._total_pnl
    avg_pnl = (pnl / total) if total > 0 else 0.0

    engine.dispose()

    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "pnl": pnl,
        "avg_pnl": avg_pnl,
    }


# ─────────────────────────────────────────────────────────────
# Optuna objective
# ─────────────────────────────────────────────────────────────
def create_objective(coin: str, interval: str, days: int, min_trades: int = 8):
    """Create an Optuna objective function.

    Returns a composite score that balances win rate and P&L:
      score = WR_weight * win_rate + PNL_weight * normalized_pnl

    We heavily reward WR since James targets 85%+ consistently.
    Trades below min_trades are penalized (not enough signal = overfitting).
    """

    def objective(trial: optuna.Trial) -> float:
        # ── MEAN REVERSION MODEL ──
        bb_period = trial.suggest_int("bb_period", 14, 40, step=2)

        # Entry bands — inner must be < outer
        entry_band = trial.suggest_float("entry_band", 0.8, 2.5, step=0.1)
        # Outer is at least 0.5σ wider than inner
        outer_min = entry_band + 0.5
        entry_band_outer = trial.suggest_float("entry_band_outer", outer_min, 4.0, step=0.1)

        # Exit band
        exit_opposite_band = trial.suggest_float("exit_opposite_band", 0.0, 2.0, step=0.1)

        # ── TREND MODEL ──
        # James: "very tight" trend settings → small fast EMA, moderate slow
        trend_ema_fast = trial.suggest_int("trend_ema_fast", 8, 55, step=1)
        # Slow must be > fast
        slow_min = trend_ema_fast + 10
        trend_ema_slow = trial.suggest_int("trend_ema_slow", slow_min, max(slow_min, 200), step=1)
        trend_min_sep = trial.suggest_float("trend_min_separation_pct", 0.01, 0.15, step=0.01)

        # ── TREND TURN PREFERENCE ──
        trend_turn_lookback = trial.suggest_int("trend_turn_lookback", 2, 10)
        trend_turn_z_bonus = trial.suggest_float("trend_turn_z_bonus", 0.0, 0.8, step=0.05)

        # ── CONFIDENCE MODEL (RSI) ──
        rsi_period = trial.suggest_int("rsi_period", 7, 21)
        # RSI thresholds — symmetric around 0.50
        rsi_offset = trial.suggest_float("rsi_offset", 0.05, 0.25, step=0.01)
        rsi_oversold = 0.50 - rsi_offset
        rsi_overbought = 0.50 + rsi_offset

        # ── TURNING POINT DETECTION ──
        zscore_lookback = trial.suggest_int("zscore_lookback", 2, 6)
        rsi_lookback = trial.suggest_int("rsi_lookback", 2, 6)

        # ── NOISE SUPPRESSION ──
        noise_suppression_window = trial.suggest_int("noise_suppression_window", 5, 40, step=5)
        noise_suppression_ratio = trial.suggest_float("noise_suppression_ratio", 0.3, 0.95, step=0.05)

        # ── RISK / HOLD ──
        cooldown_bars = trial.suggest_int("cooldown_bars", 2, 12)
        hard_stop_pct = trial.suggest_float("hard_stop_pct", 0.8, 3.0, step=0.1)
        max_hold_bars = trial.suggest_int("max_hold_bars", 15, 80, step=5)

        # ── EMERGENCY ──
        max_adverse_sigma = trial.suggest_float("max_adverse_sigma", 3.0, 6.0, step=0.5)

        params = dict(
            bb_period=bb_period,
            entry_band=entry_band,
            entry_band_outer=entry_band_outer,
            exit_opposite_band=exit_opposite_band,
            trend_ema_fast=trend_ema_fast,
            trend_ema_slow=trend_ema_slow,
            trend_min_separation_pct=trend_min_sep,
            trend_turn_lookback=trend_turn_lookback,
            trend_turn_z_bonus=trend_turn_z_bonus,
            rsi_period=rsi_period,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            zscore_lookback=zscore_lookback,
            rsi_lookback=rsi_lookback,
            noise_suppression_window=noise_suppression_window,
            noise_suppression_ratio=noise_suppression_ratio,
            cooldown_bars=cooldown_bars,
            hard_stop_pct=hard_stop_pct,
            max_hold_bars=max_hold_bars,
            max_adverse_sigma=max_adverse_sigma,
            # Fixed params
            base_trade_size_usd=150.0,
            max_position_usd=200.0,
            require_strict_trend=True,
        )

        try:
            result = run_backtest(coin, interval, days, params)
        except Exception as e:
            return -100.0  # Penalize errors

        trades = result["trades"]
        wr = result["win_rate"]
        pnl = result["pnl"]

        # ── Store metrics for analysis ──
        trial.set_user_attr("trades", trades)
        trial.set_user_attr("wins", result["wins"])
        trial.set_user_attr("losses", result["losses"])
        trial.set_user_attr("win_rate", wr)
        trial.set_user_attr("pnl", pnl)
        trial.set_user_attr("avg_pnl", result["avg_pnl"])

        # ── Composite score ──
        # Penalize configs with too few trades (overfitting risk)
        if trades < min_trades:
            # Proportional penalty: 4 trades out of 8 min → 50% score
            trade_penalty = trades / min_trades
            return (wr * 0.5 + max(pnl, 0) * 0.1) * trade_penalty - 50.0

        # Main score: heavily weight WR (James targets 85%+), but also
        # reward P&L to avoid degenerate solutions.
        #
        # WR component: 0-100 scale → contributes 0-70 points
        # P&L component: normalized per trade, contributes 0-30 points
        # Trade volume bonus: more trades = more confidence (small bonus)
        wr_score = wr * 0.70                          # 85% WR → 59.5 pts
        pnl_score = min(max(pnl, -20), 50) * 0.60     # $+20 → 12 pts
        vol_bonus = min(trades / 50.0, 1.0) * 5.0     # 50+ trades → 5 pts

        score = wr_score + pnl_score + vol_bonus
        return score

    return objective


# ─────────────────────────────────────────────────────────────
# Multi-coin/timeframe robustness check
# ─────────────────────────────────────────────────────────────
def robustness_check(params: dict, configs: list[tuple[str, str, int]]) -> list[dict]:
    """Run best params across multiple coin/timeframe combinations."""
    results = []
    for coin, interval, days in configs:
        try:
            r = run_backtest(coin, interval, days, params)
            r["coin"] = coin
            r["interval"] = interval
            results.append(r)
        except Exception as e:
            results.append({"coin": coin, "interval": interval, "error": str(e)})
    return results


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Optuna optimizer for MR v003")
    parser.add_argument("--coin", default="ZRO", help="Primary coin to optimize on")
    parser.add_argument("--interval", default="5m", help="Primary timeframe")
    parser.add_argument("--days", type=int, default=14, help="Days of data")
    parser.add_argument("--trials", type=int, default=300, help="Number of Optuna trials")
    parser.add_argument("--min-trades", type=int, default=8, help="Minimum trades to qualify")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    print("=" * 110)
    print(f"🧬 OPTUNA OPTIMIZATION — Mean Reversion v003 JAMES")
    print(f"   Coin={args.coin} | TF={args.interval} | Days={args.days} | Trials={args.trials}")
    print(f"   Goal: Find the James-level 85%+ win rate with maximum P&L")
    print("=" * 110)

    # ── Pre-download data ──
    print(f"\n📥 Pre-loading data...")
    t0 = time.time()
    get_data(args.coin, args.interval, args.days)
    print(f"   Done in {time.time() - t0:.1f}s\n")

    # ── Phase 1: Main optimization ──
    print("🔬 PHASE 1: OPTIMIZATION")
    print("-" * 60)

    study = optuna.create_study(
        direction="maximize",
        sampler=TPESampler(seed=args.seed, n_startup_trials=30),
        study_name=f"mr_v003_{args.coin}_{args.interval}",
    )

    objective = create_objective(args.coin, args.interval, args.days, args.min_trades)

    # Seed with some known-good configurations
    study.enqueue_trial({
        "bb_period": 20, "entry_band": 1.0, "entry_band_outer": 2.0,
        "exit_opposite_band": 1.5, "trend_ema_fast": 34, "trend_ema_slow": 89,
        "trend_min_separation_pct": 0.05, "trend_turn_lookback": 5,
        "trend_turn_z_bonus": 0.3, "rsi_period": 14, "rsi_offset": 0.15,
        "zscore_lookback": 3, "rsi_lookback": 3,
        "noise_suppression_window": 10, "noise_suppression_ratio": 0.7,
        "cooldown_bars": 5, "hard_stop_pct": 1.5, "max_hold_bars": 40,
        "max_adverse_sigma": 4.0,
    })
    study.enqueue_trial({
        "bb_period": 20, "entry_band": 2.0, "entry_band_outer": 3.0,
        "exit_opposite_band": 1.5, "trend_ema_fast": 34, "trend_ema_slow": 89,
        "trend_min_separation_pct": 0.05, "trend_turn_lookback": 5,
        "trend_turn_z_bonus": 0.3, "rsi_period": 14, "rsi_offset": 0.15,
        "zscore_lookback": 3, "rsi_lookback": 3,
        "noise_suppression_window": 10, "noise_suppression_ratio": 0.7,
        "cooldown_bars": 5, "hard_stop_pct": 1.5, "max_hold_bars": 40,
        "max_adverse_sigma": 4.0,
    })

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    t0 = time.time()
    trial_count = [0]

    def callback(study, trial):
        trial_count[0] += 1
        if trial_count[0] % 25 == 0 or trial_count[0] <= 5:
            best = study.best_trial
            wr = best.user_attrs.get("win_rate", 0)
            pnl = best.user_attrs.get("pnl", 0)
            trades = best.user_attrs.get("trades", 0)
            print(
                f"  Trial {trial_count[0]:>4}/{args.trials} | "
                f"best: {wr:.1f}% WR, ${pnl:+.2f} P&L, {trades} trades | "
                f"score={best.value:.1f} | "
                f"this: {trial.user_attrs.get('win_rate', 0):.1f}% WR, "
                f"${trial.user_attrs.get('pnl', 0):+.2f}"
            )

    study.optimize(objective, n_trials=args.trials, callbacks=[callback], show_progress_bar=False)
    elapsed = time.time() - t0

    # ── Results ──
    print(f"\n{'=' * 110}")
    print(f"🏆 OPTIMIZATION COMPLETE — {args.trials} trials in {elapsed:.0f}s ({elapsed/args.trials:.2f}s/trial)")
    print(f"{'=' * 110}")

    best = study.best_trial
    print(f"\n📊 BEST TRIAL #{best.number}:")
    print(f"   Score:    {best.value:.2f}")
    print(f"   Win Rate: {best.user_attrs['win_rate']:.1f}%")
    print(f"   P&L:      ${best.user_attrs['pnl']:+.2f}")
    print(f"   Trades:   {best.user_attrs['trades']} ({best.user_attrs['wins']}W/{best.user_attrs['losses']}L)")
    print(f"   Avg P&L:  ${best.user_attrs['avg_pnl']:+.3f}/trade")

    print(f"\n🎛️  BEST PARAMETERS:")
    for k, v in sorted(best.params.items()):
        print(f"   {k:<30} = {v}")

    # ── Top 10 by score ──
    print(f"\n{'=' * 110}")
    print("🏅 TOP 15 TRIALS (by composite score)")
    print(f"{'=' * 110}")
    print(f"{'#':>4} {'Score':>7} {'WR%':>6} {'P&L':>9} {'Trades':>6} {'Avg':>8} {'Inner':>5} {'Outer':>5} {'Exit':>5} {'EMA':>7} {'BB':>3} {'NS':>4}")
    print("-" * 110)

    sorted_trials = sorted(
        [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE],
        key=lambda t: t.value,
        reverse=True,
    )

    for t in sorted_trials[:15]:
        ua = t.user_attrs
        p = t.params
        c = "\033[92m" if ua.get("pnl", 0) > 0 else "\033[91m"
        e = "\033[0m"
        ema_fast = p.get("trend_ema_fast", "?")
        ema_slow = p.get("trend_ema_slow", "?")
        print(
            f"{t.number:>4} {t.value:>7.1f} {ua.get('win_rate', 0):>5.1f}% "
            f"{c}${ua.get('pnl', 0):>+7.2f}{e} {ua.get('trades', 0):>6} "
            f"${ua.get('avg_pnl', 0):>+7.3f} "
            f"{p.get('entry_band', 0):>5.1f} {p.get('entry_band_outer', 0):>5.1f} "
            f"{p.get('exit_opposite_band', 0):>5.1f} "
            f"{ema_fast}/{ema_slow:>3} "
            f"{p.get('bb_period', 0):>3} "
            f"{p.get('noise_suppression_window', 0):>4}"
        )

    # ── WR ≥ 80% filter ──
    high_wr = [
        t for t in sorted_trials
        if t.user_attrs.get("win_rate", 0) >= 80.0
        and t.user_attrs.get("trades", 0) >= args.min_trades
    ]
    if high_wr:
        print(f"\n{'=' * 110}")
        print(f"⭐ HIGH WIN RATE CONFIGS (≥80% WR, ≥{args.min_trades} trades) — {len(high_wr)} found")
        print(f"{'=' * 110}")

        # Sort by P&L among high-WR configs
        high_wr.sort(key=lambda t: t.user_attrs.get("pnl", 0), reverse=True)
        for t in high_wr[:10]:
            ua = t.user_attrs
            p = t.params
            c = "\033[92m" if ua["pnl"] > 0 else "\033[91m"
            e = "\033[0m"
            print(
                f"  #{t.number:>3}: {ua['win_rate']:>5.1f}% WR | "
                f"{c}${ua['pnl']:>+7.2f}{e} | "
                f"{ua['trades']} trades ({ua['wins']}W/{ua['losses']}L) | "
                f"inner={p['entry_band']:.1f}σ outer={p['entry_band_outer']:.1f}σ "
                f"exit={p['exit_opposite_band']:.1f}σ | "
                f"EMA={p['trend_ema_fast']}/{p['trend_ema_slow']} | "
                f"BB={p['bb_period']} | "
                f"NS={p['noise_suppression_window']}@{p['noise_suppression_ratio']:.2f}"
            )
    else:
        print(f"\n⚠️  No configs found with ≥80% WR and ≥{args.min_trades} trades")
        # Show the closest ones
        close = [
            t for t in sorted_trials
            if t.user_attrs.get("trades", 0) >= args.min_trades
        ]
        close.sort(key=lambda t: t.user_attrs.get("win_rate", 0), reverse=True)
        print("   Closest configs:")
        for t in close[:5]:
            ua = t.user_attrs
            print(
                f"   #{t.number}: {ua['win_rate']:.1f}% WR, "
                f"${ua['pnl']:+.2f}, {ua['trades']} trades"
            )

    # ── Phase 2: Robustness check on best config ──
    print(f"\n{'=' * 110}")
    print("🔒 PHASE 2: ROBUSTNESS CHECK (best params across coins/timeframes)")
    print(f"{'=' * 110}")

    # Build params dict from best trial
    bp = best.params
    rsi_off = bp.pop("rsi_offset", 0.15)
    best_params = {
        **bp,
        "rsi_oversold": 0.50 - rsi_off,
        "rsi_overbought": 0.50 + rsi_off,
        "base_trade_size_usd": 150.0,
        "max_position_usd": 200.0,
        "require_strict_trend": True,
    }

    # Test across multiple coins and timeframes
    robustness_configs = [
        (args.coin, args.interval, args.days),  # Primary (same as optimized)
    ]
    # Add other timeframes for the same coin
    for tf in ["5m", "15m", "1h"]:
        if tf != args.interval:
            robustness_configs.append((args.coin, tf, args.days))

    # Add other coins on the primary timeframe
    for other_coin in ["SUI", "LINK", "SOL"]:
        if other_coin != args.coin:
            robustness_configs.append((other_coin, args.interval, args.days))

    print(f"   Testing {len(robustness_configs)} configurations...")
    rob_results = robustness_check(best_params, robustness_configs)

    print(f"\n   {'Coin':<6} {'TF':<4} {'Trades':>6} {'W':>4} {'L':>4} {'WR%':>6} {'P&L':>9}")
    print("   " + "-" * 55)
    for r in rob_results:
        if "error" in r:
            print(f"   {r['coin']:<6} {r['interval']:<4} ERROR: {r['error']}")
        else:
            c = "\033[92m" if r["pnl"] > 0 else "\033[91m"
            e = "\033[0m"
            print(
                f"   {r['coin']:<6} {r['interval']:<4} {r['trades']:>6} "
                f"{r['wins']:>4} {r['losses']:>4} {r['win_rate']:>5.1f}% "
                f"{c}${r['pnl']:>+7.2f}{e}"
            )

    # ── Save results ──
    output_dir = Path(__file__).resolve().parent.parent.parent / "backtest_results"
    output_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "study_name": study.study_name,
        "n_trials": args.trials,
        "coin": args.coin,
        "interval": args.interval,
        "days": args.days,
        "best_score": best.value,
        "best_trial": best.number,
        "best_metrics": best.user_attrs,
        "best_params": best.params,
        "robustness": rob_results,
        "top_10": [
            {
                "trial": t.number,
                "score": t.value,
                "params": t.params,
                "metrics": t.user_attrs,
            }
            for t in sorted_trials[:10]
        ],
        "high_wr_configs": [
            {
                "trial": t.number,
                "params": t.params,
                "metrics": t.user_attrs,
            }
            for t in (high_wr[:10] if high_wr else [])
        ],
    }

    results_file = output_dir / f"optuna_v003_{args.coin}_{args.interval}.json"
    with open(results_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n💾 Full results saved to {results_file}")

    # ── Print the winner config for copy-paste ──
    print(f"\n{'=' * 110}")
    print("📋 COPY-PASTE CONFIG (best params):")
    print(f"{'=' * 110}")
    print("config = HLMeanReversionConfig(")
    print(f'    strategy_id="MR-v003-{args.coin}",')
    print(f'    instrument_id="{args.coin}-USD-PERP.HYPERLIQUID",')
    print(f'    bar_type="{args.coin}-USD-PERP.HYPERLIQUID-{TIMEFRAMES[args.interval][1]}",')
    for k, v in sorted(best_params.items()):
        if isinstance(v, float):
            print(f"    {k}={v:.4f},")
        elif isinstance(v, bool):
            print(f"    {k}={v},")
        else:
            print(f"    {k}={v},")
    print(")")


if __name__ == "__main__":
    main()
