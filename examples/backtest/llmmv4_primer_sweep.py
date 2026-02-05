#!/usr/bin/env python3
"""Sweep runner for LLMMv4 Primer backtests.

Runs multiple profile/fee combinations and stores results in separate folders.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLMMv4 Primer sweep runner")
    parser.add_argument("--date", required=True, help="Date string YYYY-MM-DD")
    parser.add_argument("--questdb", action="store_true")
    parser.add_argument("--questdb-host", default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-table", default="orderbook_deltas")
    parser.add_argument("--leader-table", default=None)
    parser.add_argument("--follower-table", default=None)
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--data-dir", default="data/ob_data")
    parser.add_argument("--leader-data-dir", default=None)
    parser.add_argument("--follower-data-dir", default=None)
    parser.add_argument("--profiles", default="accuracy,balanced,speed")
    parser.add_argument("--fee-profiles", default="default,vip1")
    parser.add_argument("--mnt-discount", action="store_true")
    parser.add_argument("--mm-tier", default="none", choices=("none", "mm1", "mm2", "mm3"))
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--override-symbol", default=None)
    parser.add_argument("--spread-bps-values", default="")
    parser.add_argument("--min-profit-bps-values", default="")
    parser.add_argument("--ofi-max-bps-values", default="")
    parser.add_argument("--max-position-qty-values", default="")
    parser.add_argument("--internal-price-delta-limit-values", default="")
    parser.add_argument("--max-updates", type=int, default=None)
    parser.add_argument("--out-root", default="backtest_results/llmmv4_primer_sweeps")
    parser.add_argument("--metrics-host", default="127.0.0.1")
    parser.add_argument("--metrics-port", type=int, default=9009)
    parser.add_argument("--metrics-lake-table", default="backtest_results_lake")
    parser.add_argument("--metrics-wh-table", default="backtest_metrics_wh")
    parser.add_argument("--emit-metrics", action="store_true")
    return parser.parse_args()


def _ilp_line(table: str, tags: dict[str, str], fields: dict[str, str], ts_ns: int) -> str:
    tag_str = ",".join([f"{k}={v}" for k, v in tags.items()])
    field_parts = []
    for key, value in fields.items():
        if value.replace(".", "", 1).isdigit():
            field_parts.append(f"{key}={value}")
        else:
            field_parts.append(f"{key}=\"{value}\"")
    return f"{table},{tag_str} {','.join(field_parts)} {ts_ns}\n"


def _emit_metrics(
    host: str,
    port: int,
    lake_table: str,
    wh_table: str,
    run_meta: dict[str, str],
    out_dir: Path,
    ts_ns: int,
) -> None:
    summary_path = out_dir / "summary.json"
    wap_path = out_dir / "wap_summary.json"
    if not summary_path.exists():
        return

    summary = json.loads(summary_path.read_text())
    wap = json.loads(wap_path.read_text()) if wap_path.exists() else {}
    total = wap.get("TOTAL", {})

    tags = {
        "profile": run_meta.get("profile", ""),
        "fee_profile": run_meta.get("fee_profile", ""),
        "mm_tier": run_meta.get("mm_tier", ""),
        "override_symbol": run_meta.get("override_symbol", ""),
        "run_id": run_meta.get("run_id", ""),
    }

    lake_fields = {
        "out_dir": str(out_dir),
        "summary_path": str(summary_path),
        "wap_path": str(wap_path),
        "date": str(summary.get("date", "")),
    }
    wh_fields = {
        "total_pnl": str(total.get("total_pnl", "0")),
        "realized_pnl": str(total.get("realized_pnl", "0")),
        "unrealized_pnl": str(total.get("unrealized_pnl", "0")),
        "fees_paid": str(total.get("fees_paid", "0")),
        "avg_pnl_per_fill": str(total.get("avg_pnl_per_fill", "0")),
    }

    try:
        sock = socket.create_connection((host, port), timeout=10)
        sock.sendall(_ilp_line(lake_table, tags, lake_fields, ts_ns).encode("utf-8"))
        sock.sendall(_ilp_line(wh_table, tags, wh_fields, ts_ns).encode("utf-8"))
    finally:
        try:
            sock.close()
        except Exception:
            pass


def main() -> None:
    args = parse_args()
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    fees = [f.strip() for f in args.fee_profiles.split(",") if f.strip()]
    spreads = [s.strip() for s in args.spread_bps_values.split(",") if s.strip()]
    min_profits = [s.strip() for s in args.min_profit_bps_values.split(",") if s.strip()]
    ofi_maxes = [s.strip() for s in args.ofi_max_bps_values.split(",") if s.strip()]
    max_positions = [s.strip() for s in args.max_position_qty_values.split(",") if s.strip()]
    internal_limits = [s.strip() for s in args.internal_price_delta_limit_values.split(",") if s.strip()]

    if not spreads:
        spreads = [None]
    if not min_profits:
        min_profits = [None]
    if not ofi_maxes:
        ofi_maxes = [None]
    if not max_positions:
        max_positions = [None]
    if not internal_limits:
        internal_limits = [None]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.out_root) / stamp
    out_root.mkdir(parents=True, exist_ok=True)

    runner = Path(__file__).with_name("llmmv4_primer_backtest.py")

    results = []
    for profile in profiles:
        for fee in fees:
            for spread in spreads:
                for min_profit in min_profits:
                    for ofi_max in ofi_maxes:
                        for max_position in max_positions:
                            for internal_limit in internal_limits:
                                override_bits = []
                                if spread is not None:
                                    override_bits.append(f"spread{spread}")
                                if min_profit is not None:
                                    override_bits.append(f"minp{min_profit}")
                                if ofi_max is not None:
                                    override_bits.append(f"ofi{ofi_max}")
                                if max_position is not None:
                                    override_bits.append(f"maxpos{max_position}")
                                if internal_limit is not None:
                                    override_bits.append(f"ipdl{internal_limit}")
                                suffix = "_".join(override_bits) if override_bits else "base"
                                run_id = f"{profile}_{fee}{'_mnt' if args.mnt_discount else ''}_{suffix}"
                                out_dir = out_root / run_id
                                cmd = [
                                    sys.executable,
                                    str(runner),
                                    "--date",
                                    args.date,
                                    "--profile",
                                    profile,
                                    "--fee-profile",
                                    fee,
                                    "--mm-tier",
                                    args.mm_tier,
                                    "--out-dir",
                                    str(out_dir),
                                    "--data-dir",
                                    args.data_dir,
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
                                if args.leader_data_dir:
                                    cmd += ["--leader-data-dir", args.leader_data_dir]
                                if args.follower_data_dir:
                                    cmd += ["--follower-data-dir", args.follower_data_dir]
                                if args.max_updates is not None:
                                    cmd += ["--max-updates", str(args.max_updates)]
                                if args.symbols:
                                    cmd += ["--symbols", args.symbols]
                                if args.override_symbol:
                                    cmd += ["--override-symbol", args.override_symbol]
                                if spread is not None:
                                    cmd += ["--override-spread-bps", spread]
                                if min_profit is not None:
                                    cmd += ["--override-min-profit-bps", min_profit]
                                if ofi_max is not None:
                                    cmd += ["--override-ofi-max-bps", ofi_max]
                                if max_position is not None:
                                    cmd += ["--override-max-position-qty", max_position]
                                if internal_limit is not None:
                                    cmd += ["--override-internal-price-delta-limit", internal_limit]
                                if args.mnt_discount:
                                    cmd.append("--mnt-discount")
                                cmd += ["--run-id", run_id]

                                print("RUN:", " ".join(cmd))
                                subprocess.run(cmd, check=True)

                                results.append(
                                    {
                                        "profile": profile,
                                        "fee_profile": fee,
                                        "mnt_discount": args.mnt_discount,
                                        "mm_tier": args.mm_tier,
                                        "override_symbol": args.override_symbol,
                                        "spread_bps": spread,
                                        "min_profit_bps": min_profit,
                                        "ofi_max_bps": ofi_max,
                                        "max_position_qty": max_position,
                                        "internal_price_delta_limit": internal_limit,
                                        "out_dir": str(out_dir),
                                        "run_id": run_id,
                                    }
                                )
                                if args.emit_metrics:
                                    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
                                    run_meta = {
                                        "profile": profile,
                                        "fee_profile": fee,
                                        "mm_tier": args.mm_tier,
                                        "override_symbol": args.override_symbol or "",
                                        "run_id": run_id,
                                    }
                                    _emit_metrics(
                                        args.metrics_host,
                                        args.metrics_port,
                                        args.metrics_lake_table,
                                        args.metrics_wh_table,
                                        run_meta,
                                        out_dir,
                                        ts_ns,
                                    )

    (out_root / "sweep_index.json").write_text(json.dumps(results, indent=2))
    print(f"Sweep complete: {out_root}")


if __name__ == "__main__":
    main()
