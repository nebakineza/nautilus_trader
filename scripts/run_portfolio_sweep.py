#!/usr/bin/env python3
"""
VIP1 Portfolio Builder - "Stacking Slices"
1. SOL: Refine efficiency (Find low-loss volume).
2. DOGE: Discovery (Find break-even volume).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from scripts.run_walk_forward_tuning import _score_from_fills

REPO_DIR = Path("/home/seb/nebakineza/nautilus_trader")
PYTHON_BIN = REPO_DIR / ".venv" / "bin" / "python"
RUNNER = REPO_DIR / "examples" / "backtest" / "llmmv3_primer_backtest.py"
OUT_BASE = REPO_DIR / "outputs" / "llmmv3_portfolio_sweep_1d"
DATE = "2026-01-29"
PROFILE = "balanced"
FEE_PROFILE = "vip0"


@dataclass(frozen=True)
class SweepConfig:
    symbol: str
    spreads: list[Decimal]
    qtys: list[Decimal]
    guard_bps_list: list[Decimal]
    ofi_bps_list: list[Decimal]
    skew_limits: list[Decimal]
    refresh_ms_list: list[int]


SWEEPS = [
    SweepConfig(
        symbol="DOGEUSDT",
        spreads=[Decimal("19.0")],
        qtys=[Decimal("2000.0")],
        guard_bps_list=[Decimal("20.0")],
        ofi_bps_list=[Decimal("5.0")],
        skew_limits=[Decimal("2.0")],
        refresh_ms_list=[1000],
    ),
    SweepConfig(
        symbol="AVAXUSDT",
        spreads=[Decimal("30.0"), Decimal("32.0")],
        qtys=[Decimal("3.0")],
        guard_bps_list=[Decimal("15.0")],
        ofi_bps_list=[Decimal("3.0")],
        skew_limits=[Decimal("0.0")],
        refresh_ms_list=[3000],
    ),
    SweepConfig(
        symbol="SUIUSDT",
        spreads=[Decimal("26.0"), Decimal("30.0")],
        qtys=[Decimal("80.0")],
        guard_bps_list=[Decimal("15.0")],
        ofi_bps_list=[Decimal("3.0")],
        skew_limits=[Decimal("0.0")],
        refresh_ms_list=[3000],
    ),
]


def _run_one(
    symbol: str,
    spread: Decimal,
    qty: Decimal,
    guard_bps: Decimal,
    ofi_bps: Decimal,
    skew_limit: Decimal,
    refresh_ms: int,
) -> dict:
    run_id = (
        f"portfolio_{symbol.lower()}_s{str(spread).replace('.', 'p')}"
        f"_q{str(qty).replace('.', 'p')}"
        f"_g{str(guard_bps).replace('.', 'p')}"
        f"_o{str(ofi_bps).replace('.', 'p')}"
        f"_k{str(skew_limit).replace('.', 'p')}"
        f"_r{refresh_ms}"
    )
    out_dir = OUT_BASE / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(PYTHON_BIN),
        str(RUNNER),
        "--date",
        DATE,
        "--symbols",
        symbol,
        "--profile",
        PROFILE,
        "--fee-profile",
        FEE_PROFILE,
        "--mnt-discount",
        "--questdb",
        "--questdb-host",
        "127.0.0.1",
        "--questdb-port",
        "9000",
        "--questdb-table",
        "data_wh_orderbook_deltas",
        "--leader-table",
        "data_wh_orderbook_deltas",
        "--follower-table",
        "data_wh_orderbook_deltas_bybit",
        "--questdb-step-seconds",
        "300",
        "--out-dir",
        str(out_dir),
        "--override-symbol",
        symbol,
        "--override-order-qty",
        f"{qty}",
        "--override-min-order-qty",
        f"{qty}",
        "--override-guard-threshold-bps",
        f"{guard_bps}",
        "--override-spread-bps",
        f"{spread}",
        "--override-min-profit-bps",
        "1.0",
        "--override-ofi-max-bps",
        f"{ofi_bps}",
        "--override-internal-price-delta-limit",
        f"{skew_limit}",
        "--override-refresh-interval-ms",
        f"{refresh_ms}",
        "--override-max-drawdown-pct",
        "1.0",
        "--override-daily-loss-limit-usdt",
        "50.0",
        "--run-id",
        run_id,
    ]

    subprocess.run(cmd, check=True)

    res = _score_from_fills(out_dir / "fills_report.csv", bucket_seconds=300, metric="sharpe")
    summary = {
        "symbol": symbol,
        "spread_bps": float(spread),
        "qty": float(qty),
        "guard_bps": float(guard_bps),
        "ofi_bps": float(ofi_bps),
        "skew_limit": float(skew_limit),
        "refresh_ms": int(refresh_ms),
        "fills": res.fills,
        "volume": res.volume,
        "total_pnl": res.total_pnl,
        "sharpe": res.sharpe,
        "sortino": res.sortino,
        "run_id": run_id,
        "out_dir": str(out_dir),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    summaries: list[dict] = []

    for sweep in SWEEPS:
        for spread in sweep.spreads:
            for qty in sweep.qtys:
                for guard_bps in sweep.guard_bps_list:
                    for ofi_bps in sweep.ofi_bps_list:
                        for skew_limit in sweep.skew_limits:
                            for refresh_ms in sweep.refresh_ms_list:
                                summaries.append(
                                    _run_one(
                                        sweep.symbol,
                                        spread,
                                        qty,
                                        guard_bps,
                                        ofi_bps,
                                        skew_limit,
                                        refresh_ms,
                                    )
                                )

    (OUT_BASE / "portfolio_summary.json").write_text(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
