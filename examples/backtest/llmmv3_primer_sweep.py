#!/usr/bin/env python3
"""Sweep runner for LLMMv3 Primer backtests.

Runs multiple profile/fee combinations and stores results in separate folders.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLMMv3 Primer sweep runner")
    parser.add_argument("--date", required=True, help="Date string YYYY-MM-DD")
    parser.add_argument("--questdb", action="store_true")
    parser.add_argument("--questdb-host", default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-table", default="orderbook_deltas")
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--data-dir", default="data/ob_data")
    parser.add_argument("--leader-data-dir", default=None)
    parser.add_argument("--follower-data-dir", default=None)
    parser.add_argument("--profiles", default="accuracy,balanced,speed")
    parser.add_argument("--fee-profiles", default="default,vip1")
    parser.add_argument("--mnt-discount", action="store_true")
    parser.add_argument("--mm-tier", default="none", choices=("none", "mm1", "mm2", "mm3"))
    parser.add_argument("--out-root", default="backtest_results/llmmv3_primer_sweeps")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    fees = [f.strip() for f in args.fee_profiles.split(",") if f.strip()]

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.out_root) / stamp
    out_root.mkdir(parents=True, exist_ok=True)

    runner = Path(__file__).with_name("llmmv3_primer_backtest.py")

    results = []
    for profile in profiles:
        for fee in fees:
            out_dir = out_root / f"{profile}_{fee}{'_mnt' if args.mnt_discount else ''}"
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
                    "--questdb-step-seconds",
                    str(args.questdb_step_seconds),
                ]
            if args.leader_data_dir:
                cmd += ["--leader-data-dir", args.leader_data_dir]
            if args.follower_data_dir:
                cmd += ["--follower-data-dir", args.follower_data_dir]
            if args.mnt_discount:
                cmd.append("--mnt-discount")

            print("RUN:", " ".join(cmd))
            subprocess.run(cmd, check=True)

            results.append(
                {
                    "profile": profile,
                    "fee_profile": fee,
                    "mnt_discount": args.mnt_discount,
                    "mm_tier": args.mm_tier,
                    "out_dir": str(out_dir),
                }
            )

    (out_root / "sweep_index.json").write_text(json.dumps(results, indent=2))
    print(f"Sweep complete: {out_root}")


if __name__ == "__main__":
    main()
