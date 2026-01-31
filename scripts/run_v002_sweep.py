#!/usr/bin/env python3
"""Sweep runner for the v002 strategy (BTC/ETH, single-instrument data).

This script generates a finite grid of `MultiPairMMConfig` overrides, runs
`scripts/runners/run_v002_backtest.py` per config, then ranks results using
`scripts/analyze_v002_backtest.py`.

Design goals:
- Deterministic (stable config IDs)
- Dependency-free (stdlib only)
- Uses existing runner/analyzer so backtest logic stays in one place

Notes:
- The repo currently includes BTC order book data under `data/ob_data/BTCUSDT_Spot/`.
    If you add ETH order book data under `data/ob_data/ETHUSDT_Spot/`, the same sweep
  script can run ETH too via `--instrument-id ETHUSDT-SPOT.BYBIT`.

Example:
    python3 scripts/run_v002_sweep.py \
        --preset quick \
        --instrument-id BTCUSDT-SPOT.BYBIT \
        --date 2026-01-15 \
        --max-updates 50000 \
        --max-runs 24 \
        --shuffle --seed 7
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class SweepConfig:
    name: str
    overrides: dict[str, Any]


def _stable_id(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def _cartesian(product_lists: list[list[tuple[str, Any]]]) -> Iterable[list[tuple[str, Any]]]:
    if not product_lists:
        yield []
        return
    acc: list[list[tuple[str, Any]]] = [[]]
    for dim in product_lists:
        nxt: list[list[tuple[str, Any]]] = []
        for prefix in acc:
            for item in dim:
                nxt.append(prefix + [item])
        acc = nxt
    for x in acc:
        yield x


def _build_grid(preset: str, instrument_id: str) -> list[SweepConfig]:
    """Return a bounded grid.

    Presets:
    - quick: very small grid focused on spread + sensitivity
    - balanced: moderate grid adds inventory knobs
    - deep: larger grid (still bounded) adds OBI thresholds
    """

    is_btc = instrument_id.startswith("BTC")

    # Per-instrument defaults
    if is_btc:
        base_qty_default = "0.00010"
        max_pos_default = "0.00050"
        base_qtys_deep = ["0.00005", "0.00010", "0.00015"]
        max_pos_qtys_deep = ["0.00030", "0.00050", "0.00080"]
        max_spreads = [4, 6, 8]
        sensitivities = [0.8, 1.0, 1.2]
    else:
        base_qty_default = "0.0050"
        max_pos_default = "0.0150"
        base_qtys_deep = ["0.0025", "0.0050", "0.0075"]
        max_pos_qtys_deep = ["0.0075", "0.0150", "0.0250"]
        max_spreads = [6, 8, 12]
        sensitivities = [0.9, 1.1, 1.3]

    min_spreads = [1, 2]

    if preset == "quick":
        shared_dims = [
            [("min_requote_ticks", x) for x in (0, 1, 2)],
        ]
    elif preset == "balanced":
        shared_dims = [
            [("obi_entry_threshold", x) for x in (0.10, 0.15, 0.20)],
            [("risk_aversion", x) for x in (0.25, 0.50, 1.00)],
            [("inventory_half_life_seconds", x) for x in (15.0, 30.0, 60.0)],
            [("min_requote_ticks", x) for x in (0, 1, 2)],
            [("rebalance_ioc_min_position_ratio", x) for x in (0.80, 0.85, 0.90)],
            [("rebalance_ioc_max_slippage_bps", x) for x in (2.0, 3.0, 5.0)],
        ]
    elif preset == "deep":
        shared_dims = [
            [("obi_levels", x) for x in (5, 10)],
            [("obi_ema_period", x) for x in (10, 15, 20)],
            [("obi_entry_threshold", x) for x in (0.10, 0.15, 0.20)],
            [("obi_exit_threshold", x) for x in (0.02, 0.03, 0.05)],
            [("risk_aversion", x) for x in (0.25, 0.50, 1.00)],
            [("inventory_half_life_seconds", x) for x in (15.0, 30.0, 60.0)],
            [("min_requote_ticks", x) for x in (0, 1, 2)],
            [("max_inventory_age_seconds", x) for x in (60.0, 120.0, 240.0)],
            [("quote_refresh_interval_ms", x) for x in (20, 50, 100)],
            [("rebalance_ioc_min_position_ratio", x) for x in (0.80, 0.85, 0.90)],
            [("rebalance_ioc_max_slippage_bps", x) for x in (2.0, 3.0, 5.0)],
        ]
    else:
        raise ValueError(f"Unknown preset: {preset}")

    if preset in ("quick", "balanced"):
        base_qtys = [base_qty_default]
        max_pos_qtys = [max_pos_default]
    else:
        base_qtys = base_qtys_deep
        max_pos_qtys = max_pos_qtys_deep

    instr_dims = [
        [("base_qty", x) for x in base_qtys],
        [("max_position_qty", x) for x in max_pos_qtys],
        [("min_spread_bps", x) for x in min_spreads],
        [("max_spread_bps", x) for x in max_spreads],
        [("obi_sensitivity", x) for x in sensitivities],
    ]

    configs: list[SweepConfig] = []

    for shared in _cartesian(shared_dims):
        shared_overrides: dict[str, Any] = {k: v for k, v in shared}

        for instr in _cartesian(instr_dims):
            inst_overrides = {k: v for k, v in instr}
            overrides = dict(shared_overrides)
            overrides["instruments"] = [
                {
                    "instrument_id": instrument_id,
                    "weight": 1.0,
                    **inst_overrides,
                }
            ]
            overrides.setdefault("enable_taker_ioc_rebalance", True)
            overrides.setdefault("rebalance_ioc_min_position_ratio", 0.85)
            overrides.setdefault("rebalance_ioc_max_slippage_bps", 3.0)
            name = f"{preset}_{instrument_id.replace('.', '_')}"
            configs.append(SweepConfig(name=name, overrides=overrides))

    # Hard cap for sanity (keeps accidental explosion from killing machines)
    cap = 72 if preset == "quick" else 216 if preset == "balanced" else 512
    return configs[:cap]


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _safe_float(x: Any) -> float | None:
    try:
        return float(x)
    except Exception:
        return None


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(r: dict[str, Any]) -> tuple[float, float, float]:
        spread = _safe_float(r.get("spread_capture_mean_bps"))
        markout = _safe_float(r.get("markout_1s_mean_bps"))
        fee = _safe_float(r.get("commission_total_usdt"))
        # Maximize spread, maximize markout, minimize fees
        return (
            spread if spread is not None else -1e9,
            markout if markout is not None else -1e9,
            -(fee if fee is not None else 1e9),
        )

    ranked = sorted(rows, key=key, reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=["quick", "balanced", "deep"], default="quick")
    ap.add_argument("--instrument-id", type=str, default="BTCUSDT-SPOT.BYBIT")
    ap.add_argument("--date", type=str, default="2026-01-15")
    ap.add_argument("--max-updates", type=int, default=50000)
    ap.add_argument("--output-root", type=str, default=None)
    ap.add_argument("--max-runs", type=int, default=24)
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--python", type=str, default=None, help="Python interpreter to run backtests/analyzer (default: current)")
    ap.add_argument("--min-interval-ms", type=int, default=50)
    ap.add_argument("--markout-horizons-ms", type=str, default="250,1000,5000")
    ap.add_argument("--fill-mid-tolerance-ms", type=int, default=2000)
    ap.add_argument("--mid-csv", type=str, default=None, help="Optional precomputed mid CSV")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="Deprecated alias for --max-runs")
    args = ap.parse_args()

    py = args.python or sys.executable

    repo_root = Path(__file__).resolve().parents[1]
    if args.output_root:
        output_root = Path(args.output_root)
    else:
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        symbol = args.instrument_id.split("-")[0].lower()
        output_root = repo_root / "outputs/sweeps" / f"v002_{symbol}_{stamp}_{args.preset}"
    output_root.mkdir(parents=True, exist_ok=True)

    grid = _build_grid(args.preset, args.instrument_id)
    max_runs = int(args.max_runs)
    if args.limit is not None:
        max_runs = int(args.limit)

    if args.shuffle:
        rng = random.Random(int(args.seed))
        rng.shuffle(grid)

    grid = grid[: max(0, max_runs)]

    print(f"Grid size: {len(grid)} (preset={args.preset}, instrument={args.instrument_id})")
    if args.dry_run:
        for i, cfg in enumerate(grid[:20]):
            print(i, _stable_id(cfg.overrides), cfg.overrides)
        print("Dry run only.")
        return 0

    # Mid series for markouts
    symbol = args.instrument_id.split("-")[0]  # BTCUSDT / ETHUSDT
    ob_file = repo_root / f"data/ob_data/{symbol}_Spot/{args.date}_{symbol}_ob200.data"
    mid_csv = Path(args.mid_csv) if args.mid_csv else (output_root / f"mid_{symbol}_{args.date}.csv")
    if not args.mid_csv and ob_file.exists() and not mid_csv.exists():
        print(f"Generating mid CSV: {mid_csv}")
        _run(
            [
                py,
                str(repo_root / "scripts/extract_mid_from_bybit_ob.py"),
                "--ob-file",
                str(ob_file),
                "--output-csv",
                str(mid_csv),
                "--instrument-id",
                args.instrument_id,
                "--min-interval-ms",
                str(int(args.min_interval_ms)),
                "--max-lines",
                str(int(args.max_updates)),
            ],
            cwd=repo_root,
        )

    leaderboard_rows: list[dict[str, Any]] = []

    for idx, cfg in enumerate(grid, 1):
        cfg_id = _stable_id(cfg.overrides)
        run_dir = output_root / f"run_{idx:03d}_{cfg_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        cfg_path = run_dir / "config_overrides.json"
        cfg_path.write_text(json.dumps(cfg.overrides, indent=2, sort_keys=True), encoding="utf-8")

        print(f"[{idx}/{len(grid)}] backtest {cfg_id} -> {run_dir}")
        _run(
            [
                py,
                str(repo_root / "scripts/runners/run_v002_backtest.py"),
                "--date",
                args.date,
                "--max-updates",
                str(int(args.max_updates)),
                "--instrument-id",
                args.instrument_id,
                "--config-json",
                str(cfg_path),
                "--output-dir",
                str(run_dir),
            ],
            cwd=repo_root,
        )

        analysis_path = run_dir / "analysis_summary.json"
        analyze_cmd = [
            py,
            str(repo_root / "scripts/analyze_v002_backtest.py"),
            "--results-dir",
            str(run_dir),
            "--output-json",
            str(analysis_path),
        ]
        if mid_csv.exists():
            analyze_cmd += [
                "--mid-csv",
                str(mid_csv),
                "--markout-horizons-ms",
                args.markout_horizons_ms,
                "--fill-mid-tolerance-ms",
                str(int(args.fill_mid_tolerance_ms)),
            ]

        _run(analyze_cmd, cwd=repo_root)

        # Build leaderboard row
        try:
            summary = json.loads(analysis_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        fs = summary.get("fill_summary", {})
        ms = summary.get("markout_summary") or {}
        horizons = ms.get("horizons") or []

        # pull 1000ms horizon if present
        mo_1s = next((h for h in horizons if int(h.get("horizon_ms", -1)) == 1000), None)
        sc = ms.get("spread_capture") or {}

        row = {
            "run_id": cfg_id,
            "run_dir": str(run_dir),
            "fills": fs.get("fills"),
            "maker_fills": fs.get("maker_fills"),
            "taker_fills": fs.get("taker_fills"),
            "commission_total_usdt": fs.get("commission_total_usdt"),
            "spread_capture_mean_bps": sc.get("mean_bps"),
            "markout_1s_mean_bps": mo_1s.get("mean_bps") if mo_1s else None,
            "markout_1s_adverse_pct": mo_1s.get("adverse_pct") if mo_1s else None,
            "config": json.dumps(cfg.overrides, sort_keys=True),
        }
        leaderboard_rows.append(row)

        # Rank and snapshot leaderboard
        ranked = _rank_rows(leaderboard_rows)

        # Keep an always-updated leaderboard snapshot
        lb_path = output_root / "leaderboard.csv"
        with lb_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(ranked[0].keys()))
            writer.writeheader()
            writer.writerows(ranked)

    print(f"Wrote leaderboard: {output_root / 'leaderboard.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
