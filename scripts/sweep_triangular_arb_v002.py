#!/usr/bin/env python3
"""Parameter sweep for TriangularArb v002 using QuestDB data."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path

from scripts.runners.run_v003_real_data_backtest import run_backtest
from examples.backtest.questdb_orderbook_loader import QuestDbConfig


def _parse_decimal_list(value: str) -> list[Decimal]:
    return [Decimal(v.strip()) for v in value.split(",") if v.strip()]


def _parse_int_list(value: str) -> list[int]:
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="2026-01-29")
    parser.add_argument("--max-hours", type=float, default=8.5)
    parser.add_argument("--max-updates", type=int, default=3000000)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--stride", type=int, default=1)

    parser.add_argument("--min-profit-usdt", type=str, default="0.02,0.01,0.00,-0.0001")
    parser.add_argument("--min-profit-bps", type=str, default="0.0,-1.0")
    parser.add_argument("--max-slippage-bps", type=str, default="2.0,5.0,10.0")
    parser.add_argument("--trend-ema-periods", type=str, default="10,20,50")
    parser.add_argument("--trend-epsilon-bps", type=str, default="0.5,1.0,2.0")
    parser.add_argument("--trend-bias", type=str, default="true,false")
    parser.add_argument("--max-data-staleness-ms", type=str, default="2000,10000,30000")
    parser.add_argument("--require-synced-books", type=str, default="false")

    parser.add_argument("--questdb-host", type=str, default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-table", type=str, default="orderbook_deltas")
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--use-questdb", action="store_true")

    parser.add_argument("--out", type=str, default="/home/seb/nebakineza/nautilus_trader/sweep_results/triangular_arb_v002_sweep.csv")
    args = parser.parse_args()

    questdb = None
    if args.use_questdb:
        questdb = QuestDbConfig(
            host=args.questdb_host,
            port=args.questdb_port,
            table=args.questdb_table,
            step_seconds=args.questdb_step_seconds,
        )

    min_profit_usdt_vals = _parse_decimal_list(args.min_profit_usdt)
    min_profit_bps_vals = _parse_decimal_list(args.min_profit_bps)
    max_slippage_vals = _parse_decimal_list(args.max_slippage_bps)
    ema_periods = _parse_int_list(args.trend_ema_periods)
    epsilon_vals = _parse_decimal_list(args.trend_epsilon_bps)
    trend_bias_vals = [v.strip().lower() == "true" for v in args.trend_bias.split(",") if v.strip()]
    staleness_vals = _parse_int_list(args.max_data_staleness_ms)
    require_synced_vals = [v.strip().lower() == "true" for v in args.require_synced_books.split(",") if v.strip()]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not out_path.exists()
    with out_path.open("a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "date",
                "max_hours",
                "min_profit_usdt",
                "min_profit_bps",
                "max_slippage_bps",
                "trend_ema_period",
                "trend_epsilon_bps",
                "fills",
                "rejections",
                "cycles_started",
                "cycles_completed",
                "nav_usdt",
                "trend_bias",
                "max_data_staleness_ms",
                "require_synced_books",
            ])

        for min_profit_usdt in min_profit_usdt_vals:
            for min_profit_bps in min_profit_bps_vals:
                for max_slippage_bps in max_slippage_vals:
                    for ema in ema_periods:
                        for epsilon in epsilon_vals:
                            for trend_bias in trend_bias_vals:
                                for staleness in staleness_vals:
                                    for require_synced in require_synced_vals:
                                        summary = run_backtest(
                                            date_str=args.date,
                                            max_updates=args.max_updates,
                                            fast_mode=args.fast,
                                            stride=args.stride,
                                            max_hours=args.max_hours,
                                            questdb=questdb,
                                            min_profit_bps=min_profit_bps,
                                            min_profit_usdt=min_profit_usdt,
                                            max_slippage_bps=max_slippage_bps,
                                            trend_ema_period=ema,
                                            trend_epsilon_bps=epsilon,
                                            trend_bias=trend_bias,
                                            max_data_staleness_ms=staleness,
                                            require_synced_books=require_synced,
                                        )
                                        stats = summary.get("stats", {})
                                        writer.writerow([
                                            args.date,
                                            args.max_hours,
                                            str(min_profit_usdt),
                                            str(min_profit_bps),
                                            str(max_slippage_bps),
                                            ema,
                                            str(epsilon),
                                            stats.get("fills"),
                                            stats.get("rejections"),
                                            stats.get("cycles_started"),
                                            stats.get("cycles_completed"),
                                            summary.get("nav_usdt"),
                                            str(trend_bias),
                                            staleness,
                                            str(require_synced),
                                        ])
                                        f.flush()


if __name__ == "__main__":
    main()
