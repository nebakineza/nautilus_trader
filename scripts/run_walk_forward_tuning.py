#!/usr/bin/env python3
"""Walk-forward tuner for LLMMv3 Primer (1-day windows).

Trains on one date, validates on the next date using the same params.
Objective: risk_adjusted * log(1 + volume), with a minimum fills constraint.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import optuna


@dataclass
class ScoreResult:
    score: float
    sharpe: float
    sortino: float
    volume: float
    fills: int
    total_pnl: float


def _to_ts_ns(ts_value: str) -> int:
    if not ts_value:
        return 0
    dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _parse_fee(value: str) -> float:
    if not value:
        return 0.0
    digits = "".join(ch for ch in value if (ch.isdigit() or ch in ".-"))
    return float(digits) if digits else 0.0


def _score_from_fills(csv_path: Path, bucket_seconds: int, metric: str) -> ScoreResult:
    if not csv_path.exists():
        return ScoreResult(score=-1e9, sharpe=0.0, sortino=0.0, volume=0.0, fills=0, total_pnl=0.0)

    position = 0.0
    avg_cost = 0.0
    realized_pnl = 0.0
    volume = 0.0

    bucket_ns = bucket_seconds * 1_000_000_000
    bucket_pnl: dict[int, float] = {}
    fills = 0

    with csv_path.open() as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            side = (row.get("side") or "").upper()
            qty = float(row.get("quantity") or 0)
            price = float(row.get("price") or 0)
            fee = _parse_fee(row.get("commissions") or "0")
            ts_ns = _to_ts_ns(row.get("ts_last") or row.get("ts_init") or "")

            if qty == 0 or price == 0 or not side:
                continue

            volume += abs(price * qty)
            fills += 1

            realized_before = realized_pnl
            if side == "BUY":
                if position >= 0:
                    total_cost = position * avg_cost + qty * price
                    position += qty
                    avg_cost = total_cost / position if position else 0.0
                else:
                    cover_qty = min(qty, abs(position))
                    realized_pnl += (avg_cost - price) * cover_qty
                    position += cover_qty
                    remaining = qty - cover_qty
                    if remaining > 0:
                        position += remaining
                        avg_cost = price
            elif side == "SELL":
                if position <= 0:
                    total_cost = abs(position) * avg_cost + qty * price
                    position -= qty
                    avg_cost = total_cost / abs(position) if position else 0.0
                else:
                    close_qty = min(qty, position)
                    realized_pnl += (price - avg_cost) * close_qty
                    position -= close_qty
                    remaining = qty - close_qty
                    if remaining > 0:
                        position -= remaining
                        avg_cost = price

            realized_pnl -= fee
            delta = realized_pnl - realized_before
            bucket = ts_ns // bucket_ns if bucket_ns else ts_ns
            bucket_pnl[bucket] = bucket_pnl.get(bucket, 0.0) + delta

    returns = list(bucket_pnl.values())
    if len(returns) < 2:
        return ScoreResult(
            score=-1e9,
            sharpe=0.0,
            sortino=0.0,
            volume=volume,
            fills=fills,
            total_pnl=realized_pnl,
        )

    mean_ret = statistics.mean(returns)
    stdev = statistics.pstdev(returns)
    sharpe = (mean_ret / stdev) * math.sqrt(len(returns)) if stdev > 0 else 0.0

    downside = [r for r in returns if r < 0]
    downside_var = statistics.mean([r * r for r in downside]) if downside else 0.0
    downside_std = math.sqrt(downside_var)
    sortino = (mean_ret / downside_std) * math.sqrt(len(returns)) if downside_std > 0 else 0.0

    risk_adj = sharpe if metric == "sharpe" else sortino
    score = risk_adj * math.log1p(volume)
    return ScoreResult(
        score=score,
        sharpe=sharpe,
        sortino=sortino,
        volume=volume,
        fills=fills,
        total_pnl=realized_pnl,
    )


def _run_backtest(args, out_dir: Path, date_str: str, overrides: dict[str, str]) -> None:
    runner = Path(__file__).resolve().parents[1] / "examples" / "backtest" / "llmmv3_primer_backtest.py"
    cmd = [
        sys.executable,
        str(runner),
        "--date",
        date_str,
        "--profile",
        args.profile,
        "--fee-profile",
        args.fee_profile,
        "--mm-tier",
        args.mm_tier,
        "--out-dir",
        str(out_dir),
        "--data-dir",
        args.data_dir,
        "--symbols",
        args.symbol,
        "--override-symbol",
        args.symbol,
    ]

    if args.questdb:
        cmd += [
            "--questdb",
            "--questdb-host",
            args.questdb_host,
            "--questdb-port",
            str(args.questdb_port),
            "--questdb-table",
            args.questdb_table,
            "--leader-table",
            args.leader_table or args.questdb_table,
            "--follower-table",
            args.follower_table or args.questdb_table,
            "--questdb-step-seconds",
            str(args.questdb_step_seconds),
        ]

    if args.mnt_discount:
        cmd.append("--mnt-discount")

    for key, value in overrides.items():
        cmd += [key, value]

    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward tuner for LLMMv3 Primer")
    parser.add_argument("--train-date", required=True)
    parser.add_argument("--test-date", required=True)
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--profile", default="balanced")
    parser.add_argument("--fee-profile", default="vip1")
    parser.add_argument("--mm-tier", default="none")
    parser.add_argument("--mnt-discount", action="store_true")
    parser.add_argument("--data-dir", default="data/ob_data")
    parser.add_argument("--questdb", action="store_true")
    parser.add_argument("--questdb-host", default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-table", default="orderbook_deltas")
    parser.add_argument("--leader-table", default=None)
    parser.add_argument("--follower-table", default=None)
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--startup-trials", type=int, default=50)
    parser.add_argument("--n-jobs", type=int, default=4)
    parser.add_argument("--bucket-seconds", type=int, default=300)
    parser.add_argument("--metric", choices=("sharpe", "sortino"), default="sharpe")
    parser.add_argument("--spread-min", type=float, default=10.0)
    parser.add_argument("--spread-max", type=float, default=20.0)
    parser.add_argument("--spread-step", type=float, default=0.1)
    parser.add_argument("--min-profit-min", type=float, default=0.0)
    parser.add_argument("--min-profit-max", type=float, default=1.0)
    parser.add_argument("--min-profit-step", type=float, default=0.1)
    parser.add_argument("--ofi-max-min", type=float, default=3.0)
    parser.add_argument("--ofi-max-max", type=float, default=8.0)
    parser.add_argument("--ofi-max-step", type=float, default=0.1)
    parser.add_argument("--max-position-values", default="5")
    parser.add_argument("--internal-limit-min", type=float, default=5.0)
    parser.add_argument("--internal-limit-max", type=float, default=5.0)
    parser.add_argument("--internal-limit-step", type=float, default=0.1)
    parser.add_argument("--min-fills", type=int, default=50)
    parser.add_argument("--min-fills-penalty", type=float, default=-500.0)
    parser.add_argument("--max-loss", type=float, default=30.0)
    parser.add_argument("--run-oos", action="store_true")
    parser.add_argument("--max-drop", type=float, default=0.30)
    parser.add_argument("--storage", default="sqlite:///optuna_walk_forward.db")
    parser.add_argument("--output-root", default="outputs/llmmv3_walk_forward")
    args = parser.parse_args()

    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)

    max_positions = [float(v.strip()) for v in args.max_position_values.split(",") if v.strip()]

    def objective(trial: optuna.Trial) -> float:
        spread = trial.suggest_float("spread_bps", args.spread_min, args.spread_max, step=args.spread_step)
        min_profit = trial.suggest_float(
            "min_profit_bps",
            args.min_profit_min,
            args.min_profit_max,
            step=args.min_profit_step,
        )
        ofi_max = trial.suggest_float("ofi_max_bps", args.ofi_max_min, args.ofi_max_max, step=args.ofi_max_step)
        max_position = trial.suggest_categorical("max_position_qty", max_positions)
        internal_limit = trial.suggest_float(
            "internal_price_delta_limit",
            args.internal_limit_min,
            args.internal_limit_max,
            step=args.internal_limit_step,
        )

        run_id = (
            f"train_{trial.number:04d}_spread{spread:.3f}_minp{min_profit:.3f}_"
            f"ofi{ofi_max:.3f}_maxpos{max_position}_ipdl{internal_limit:.2f}"
        )
        out_dir = out_root / "train" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        overrides = {
            "--override-spread-bps": f"{spread:.4f}",
            "--override-min-profit-bps": f"{min_profit:.4f}",
            "--override-ofi-max-bps": f"{ofi_max:.4f}",
            "--override-max-position-qty": f"{max_position}",
            "--override-internal-price-delta-limit": f"{internal_limit:.4f}",
            "--run-id": run_id,
        }

        _run_backtest(args, out_dir, args.train_date, overrides)
        fills_path = out_dir / "fills_report.csv"
        result = _score_from_fills(fills_path, args.bucket_seconds, args.metric)
        trial.set_user_attr("volume", result.volume)
        trial.set_user_attr("fills", result.fills)
        trial.set_user_attr("sharpe", result.sharpe)
        trial.set_user_attr("sortino", result.sortino)
        if result.total_pnl < -args.max_loss:
            return -1_000_000.0 + result.total_pnl
        if result.fills < args.min_fills:
            return args.min_fills_penalty
        return result.volume

    study = optuna.create_study(
        direction="maximize",
        study_name=f"LLMMv3Primer-WF-{args.symbol}",
        storage=args.storage,
        load_if_exists=True,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=args.startup_trials, n_warmup_steps=0),
    )
    study.optimize(objective, n_trials=args.trials, n_jobs=args.n_jobs)

    best_trial = study.best_trial
    best_params = best_trial.params

    oos_result = None
    oos_score = None
    drop_off = None
    passed = False
    if args.run_oos:
        test_run_id = "oos_champion"
        test_out_dir = out_root / "oos" / test_run_id
        test_out_dir.mkdir(parents=True, exist_ok=True)

        overrides = {
            "--override-spread-bps": f"{best_params['spread_bps']:.4f}",
            "--override-min-profit-bps": f"{best_params['min_profit_bps']:.4f}",
            "--override-ofi-max-bps": f"{best_params['ofi_max_bps']:.4f}",
            "--override-max-position-qty": f"{best_params['max_position_qty']}",
            "--override-internal-price-delta-limit": f"{best_params['internal_price_delta_limit']:.4f}",
            "--run-id": test_run_id,
        }

        _run_backtest(args, test_out_dir, args.test_date, overrides)
        oos_result = _score_from_fills(test_out_dir / "fills_report.csv", args.bucket_seconds, args.metric)
        if oos_result.total_pnl < -args.max_loss:
            oos_score = -1_000_000.0 + oos_result.total_pnl
        elif oos_result.fills < args.min_fills:
            oos_score = args.min_fills_penalty
        else:
            oos_score = oos_result.volume

        train_score = best_trial.value
        if train_score and train_score > 0:
            drop_off = (train_score - oos_score) / train_score
            passed = drop_off <= args.max_drop

    summary = {
        "symbol": args.symbol,
        "train_date": args.train_date,
        "test_date": args.test_date,
        "train_score": best_trial.value,
        "oos_score": oos_score,
        "drop_off": drop_off,
        "passed": passed,
        "min_fills": args.min_fills,
        "max_loss": args.max_loss,
        "metric": args.metric,
        "best_params": best_params,
        "train_attrs": best_trial.user_attrs,
        "oos_attrs": (
            {
                "fills": oos_result.fills,
                "volume": oos_result.volume,
                "sharpe": oos_result.sharpe,
                "sortino": oos_result.sortino,
                "total_pnl": oos_result.total_pnl,
            }
            if oos_result is not None
            else None
        ),
    }

    (out_root / "walk_forward_summary.json").write_text(json.dumps(summary, indent=2))
    (out_root / "champion_config.json").write_text(json.dumps(best_params, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
