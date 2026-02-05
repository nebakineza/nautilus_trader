#!/usr/bin/env python3
"""Closed-loop self-improvement cycle for LLMMv3 Primer (SOL).

Cycle:
  1) Hunt on train day.
  2) Evaluate challenger vs champion on out-of-sample day.
  3) Promote if better.
  4) Repeat until time budget exhausted.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ScoreResult:
    score: float
    sharpe: float
    sortino: float
    volume: float
    fills: int


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
        return ScoreResult(score=-1e9, sharpe=0.0, sortino=0.0, volume=0.0, fills=0)

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
        return ScoreResult(score=-1e9, sharpe=0.0, sortino=0.0, volume=volume, fills=fills)

    mean_ret = statistics.mean(returns)
    stdev = statistics.pstdev(returns)
    sharpe = (mean_ret / stdev) * math.sqrt(len(returns)) if stdev > 0 else 0.0

    downside = [r for r in returns if r < 0]
    downside_var = statistics.mean([r * r for r in downside]) if downside else 0.0
    downside_std = math.sqrt(downside_var)
    sortino = (mean_ret / downside_std) * math.sqrt(len(returns)) if downside_std > 0 else 0.0

    risk_adj = sharpe if metric == "sharpe" else sortino
    score = risk_adj * math.log1p(volume)
    return ScoreResult(score=score, sharpe=sharpe, sortino=sortino, volume=volume, fills=fills)


def _run_backtest(args, out_dir: Path, overrides: dict[str, str], date: str, max_updates: int | None) -> None:
    runner = Path(__file__).resolve().parents[1] / "examples" / "backtest" / "llmmv3_primer_backtest.py"
    cmd = [
        sys.executable,
        str(runner),
        "--date",
        date,
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

    if max_updates is not None:
        cmd += ["--max-updates", str(max_updates)]

    if args.mnt_discount:
        cmd.append("--mnt-discount")

    for key, value in overrides.items():
        cmd += [key, value]

    subprocess.run(cmd, check=True)


def _latest_hunt_summary(root: Path) -> Path | None:
    if not root.exists():
        return None
    summaries = list(root.glob("*/study_summary.json"))
    if not summaries:
        return None
    summaries.sort(key=lambda p: p.parent.name)
    return summaries[-1]


def _load_baseline(args, root: Path) -> dict[str, float]:
    baseline_path = root / "baseline.json"
    if baseline_path.exists():
        return json.loads(baseline_path.read_text())

    latest = _latest_hunt_summary(Path(args.hunt_root))
    if latest:
        data = json.loads(latest.read_text())
        params = data.get("best_params", {})
        if params:
            baseline_path.write_text(json.dumps(params, indent=2))
            return params

    return {
        "spread_bps": 14.2,
        "min_profit_bps": 1.1,
        "ofi_max_bps": 3.0,
        "max_position_qty": 2.0,
        "internal_price_delta_limit": 7.0,
    }


def _window(value: float, span: float, min_val: float, max_val: float) -> tuple[float, float]:
    return max(min_val, value - span), min(max_val, value + span)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run closed-loop LLMMv3 Primer tuning")
    parser.add_argument("--train-date", required=True)
    parser.add_argument("--oos-date", required=True)
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
    parser.add_argument("--max-updates-train", type=int, default=100000)
    parser.add_argument("--max-updates-oos", type=int, default=50000)
    parser.add_argument("--trials-per-cycle", type=int, default=20)
    parser.add_argument("--bucket-seconds", type=int, default=300)
    parser.add_argument("--metric", choices=("sharpe", "sortino"), default="sharpe")
    parser.add_argument("--eval-repeats", type=int, default=3)
    parser.add_argument("--hours", type=float, default=7.0)
    parser.add_argument("--output-root", default="outputs/llmmv3_primer_cycle")
    parser.add_argument("--hunt-root", default="outputs/llmmv3_primer_hunt")
    args = parser.parse_args()

    out_root = Path(args.output_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root.mkdir(parents=True, exist_ok=True)

    baseline_params = _load_baseline(args, out_root)
    baseline_path = out_root / "baseline.json"
    baseline_path.write_text(json.dumps(baseline_params, indent=2))

    cycles_path = out_root / "cycle_log.csv"
    if not cycles_path.exists():
        cycles_path.write_text(
            "cycle,champ_score,chall_score,champ_params,chall_params,champ_fills,chall_fills\n"
        )

    start = time.time()
    cycle = 0
    while (time.time() - start) < (args.hours * 3600):
        cycle += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cycle_dir = out_root / f"cycle_{cycle:03d}_{stamp}"
        cycle_dir.mkdir(parents=True, exist_ok=True)

        spread_min, spread_max = _window(baseline_params["spread_bps"], 2.0, 10.0, 25.0)
        minp_min, minp_max = _window(baseline_params["min_profit_bps"], 0.6, 0.3, 3.0)
        ofi_min, ofi_max = _window(baseline_params["ofi_max_bps"], 1.0, 1.0, 8.0)
        ipdl_min, ipdl_max = _window(baseline_params["internal_price_delta_limit"], 1.0, 3.0, 8.0)
        maxpos_vals = [1.0, 3.0, 5.0, 10.0]

        hunt_cmd = [
            sys.executable,
            str(Path(__file__).with_name("hunt_llmmv3_primer.py")),
            "--date",
            args.train_date,
            "--symbol",
            args.symbol,
            "--profile",
            args.profile,
            "--fee-profile",
            args.fee_profile,
            "--mm-tier",
            args.mm_tier,
            "--output-root",
            str(cycle_dir / "hunt"),
            "--trials",
            str(args.trials_per_cycle),
            "--bucket-seconds",
            str(args.bucket_seconds),
            "--metric",
            args.metric,
            "--spread-min",
            f"{spread_min}",
            "--spread-max",
            f"{spread_max}",
            "--min-profit-min",
            f"{minp_min}",
            "--min-profit-max",
            f"{minp_max}",
            "--ofi-max-min",
            f"{ofi_min}",
            "--ofi-max-max",
            f"{ofi_max}",
            "--internal-limit-min",
            f"{ipdl_min}",
            "--internal-limit-max",
            f"{ipdl_max}",
            "--max-position-values",
            ",".join(str(v) for v in maxpos_vals),
            "--max-updates",
            str(args.max_updates_train),
        ]

        if args.mnt_discount:
            hunt_cmd.append("--mnt-discount")
        if args.questdb:
            hunt_cmd += [
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

        subprocess.run(hunt_cmd, check=True)
        hunt_summary = _latest_hunt_summary(cycle_dir / "hunt")
        if hunt_summary is None:
            break

        hunt_data = json.loads(hunt_summary.read_text())
        challenger = hunt_data.get("best_params", {})
        if not challenger:
            break

        def run_eval(params: dict[str, float], name: str) -> ScoreResult:
            repeats = max(1, args.eval_repeats)
            scores: list[ScoreResult] = []
            for rep in range(1, repeats + 1):
                run_id = f"{name}_{cycle:03d}_r{rep:02d}"
                out_dir = cycle_dir / name / f"rep_{rep:02d}"
                out_dir.mkdir(parents=True, exist_ok=True)
                overrides = {
                    "--override-spread-bps": f"{params['spread_bps']:.4f}",
                    "--override-min-profit-bps": f"{params['min_profit_bps']:.4f}",
                    "--override-ofi-max-bps": f"{params['ofi_max_bps']:.4f}",
                    "--override-max-position-qty": f"{params['max_position_qty']}",
                    "--override-internal-price-delta-limit": f"{params['internal_price_delta_limit']:.4f}",
                    "--run-id": run_id,
                }
                _run_backtest(args, out_dir, overrides, args.oos_date, args.max_updates_oos)
                scores.append(
                    _score_from_fills(out_dir / "fills_report.csv", args.bucket_seconds, args.metric)
                )

            mean_score = statistics.mean(s.score for s in scores)
            mean_sharpe = statistics.mean(s.sharpe for s in scores)
            mean_sortino = statistics.mean(s.sortino for s in scores)
            total_volume = sum(s.volume for s in scores)
            total_fills = sum(s.fills for s in scores)
            return ScoreResult(
                score=mean_score,
                sharpe=mean_sharpe,
                sortino=mean_sortino,
                volume=total_volume,
                fills=total_fills,
            )

        champ_score = run_eval(baseline_params, "champion")
        chall_score = run_eval(challenger, "challenger")

        if chall_score.score > champ_score.score:
            baseline_params = challenger
            baseline_path.write_text(json.dumps(baseline_params, indent=2))

        with cycles_path.open("a") as handle:
            handle.write(
                f"{cycle},{champ_score.score},{chall_score.score},"
                f"{json.dumps(baseline_params)},{json.dumps(challenger)},"
                f"{champ_score.fills},{chall_score.fills}\n"
            )

    print(f"Cycle complete: {out_root}")


if __name__ == "__main__":
    main()
