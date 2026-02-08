#!/usr/bin/env python3
"""VIP1 Acquisition Green Sweep - Multi-pair Parameter Optimizer

Runs LLMMv4 backtests for 5-pair portfolio (ETHUSDT, SUIUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT)
across parameter ranges, optimizing for MAXIMUM VOLUME subject to PnL > $0 constraint.

Core Principle: "ZERO LOSS RULE"
- Every pair must be individually profitable after fees (VIP0: ~15bps roundtrip)
- Volume is secondary to profitability
- If a pair cannot be green, widen spreads/guards until profitable

Target: $33,333 daily volume (VIP1 qualification: $1M/30 days)
Expansion: If 5 pairs insufficient, add SOL/XRP/ARB instead of loosening parameters
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


# VIP1 Acquisition Portfolio (5 pairs initially)
VIP1_PAIRS = {
    "ETHUSDT": {
        "order_qty": "0.005",
        "min_order_qty": "0.005",
        "max_position_qty": "0.05",
    },
    "SUIUSDT": {
        "order_qty": "80.0",
        "min_order_qty": "20.0",
        "max_position_qty": "800.0",
    },
    "DOGEUSDT": {
        "order_qty": "1500.0",
        "min_order_qty": "375.0",
        "max_position_qty": "15000.0",
    },
    "AVAXUSDT": {
        "order_qty": "2.0",
        "min_order_qty": "0.5",
        "max_position_qty": "20.0",
    },
    "LINKUSDT": {
        "order_qty": "2.0",
        "min_order_qty": "0.5",
        "max_position_qty": "20.0",
    },
}

# Expansion candidates (if initial 5 pairs insufficient)
EXPANSION_PAIRS = {
    "SOLUSDT": {
        "order_qty": "0.2",
        "min_order_qty": "0.05",
        "max_position_qty": "2.0",
    },
    "XRPUSDT": {
        "order_qty": "20.0",
        "min_order_qty": "5.0",
        "max_position_qty": "200.0",
    },
    "ARBUSDT": {
        "order_qty": "50.0",
        "min_order_qty": "12.5",
        "max_position_qty": "500.0",
    },
}

# Parameter sweep ranges
SWEEP_CONFIG = {
    "spread_bps": [15, 20, 25, 30, 35, 40, 45, 50],  # Start at profitability threshold
    "guard_threshold_bps": [10, 15, 20, 25],  # Toxic flow protection
    "min_profit_bps": [1, 2, 3, 5],  # Minimum profit per fill
    "ofi_max_bps": [0, 3, 5, 8],  # Order Flow Imbalance threshold (0 = disabled)
    "refresh_interval_ms": [3000, 5000, 8000],  # Quote refresh latency
}


def run_backtest(
    pair: str,
    date: str,
    spread_bps: int,
    guard_threshold_bps: int,
    min_profit_bps: int,
    ofi_max_bps: int,
    refresh_interval_ms: int,
    output_dir: Path,
    questdb: bool = True,
    profile: str = "balanced",
) -> dict[str, Any]:
    """Run single LLMMv4 backtest"""
    
    pair_config = VIP1_PAIRS.get(pair) or EXPANSION_PAIRS.get(pair)
    if not pair_config:
        return {"error": f"Unknown pair: {pair}"}
    
    run_name = f"{pair}_{spread_bps}bps_g{guard_threshold_bps}_mp{min_profit_bps}_ofi{ofi_max_bps}_r{refresh_interval_ms}"
    run_dir = output_dir / run_name
    
    cmd = [
        sys.executable,
        "examples/backtest/llmmv4_primer_backtest.py",
        "--date", date,
        "--symbols", pair,
        "--profile", profile,
        "--fee-profile", "vip0",
        "--mnt-discount",  # 7.5bps maker/taker (roundtrip 15bps)
        "--override-symbol", pair,
        "--override-order-qty", pair_config["order_qty"],
        "--override-min-order-qty", pair_config["min_order_qty"],
        "--override-max-position-qty", pair_config["max_position_qty"],
        "--override-spread-bps", str(spread_bps),
        "--override-guard-threshold-bps", str(guard_threshold_bps),
        "--override-min-profit-bps", str(min_profit_bps),
        "--override-ofi-max-bps", str(ofi_max_bps),
        "--override-refresh-interval-ms", str(refresh_interval_ms),
        "--override-max-drawdown-pct", "1.0",  # Disable killswitch for backtesting
        "--out-dir", str(run_dir),
    ]
    
    if questdb:
        cmd.append("--questdb")
    
    print(f"  🔬 Testing: {run_name}")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min timeout
        )
        
        if result.returncode != 0:
            print(f"  ❌ Backtest failed: {result.stderr[:200]}")
            return {"error": "Backtest execution failed"}
    
    except subprocess.TimeoutExpired:
        return {"error": "Timeout (>10 minutes)"}
    except Exception as e:
        return {"error": str(e)}
    
    # Parse results
    return _parse_backtest_results(run_dir, pair, spread_bps, guard_threshold_bps, 
                                    min_profit_bps, ofi_max_bps, refresh_interval_ms, date)


def _parse_backtest_results(
    run_dir: Path,
    pair: str,
    spread_bps: int,
    guard_threshold_bps: int,
    min_profit_bps: int,
    ofi_max_bps: int,
    refresh_interval_ms: int,
    date: str,
) -> dict[str, Any]:
    """Parse backtest output files and calculate key metrics"""
    
    wap_file = run_dir / "wap_summary.json"
    
    if wap_file.exists():
        try:
            with open(wap_file) as f:
                wap_data = json.load(f)
            
            total = wap_data.get("TOTAL", {})
            
            # Get fill count from fills file
            fills_file = run_dir / "fills_report.csv"
            fill_count = 0
            total_volume = 0.0
            
            if fills_file.exists():
                import pandas as pd
                fills = pd.read_csv(fills_file)
                fill_count = len(fills) - 1  # Subtract header
                if fill_count > 0:
                    fills["volume"] = fills["filled_qty"] * fills["avg_px"]
                    total_volume = fills["volume"].sum()
            
            # Extract core metrics from WAP summary
            net_pnl = float(total.get("total_pnl", 0))
            realized_pnl = float(total.get("realized_pnl", 0))
            unrealized_pnl = float(total.get("unrealized_pnl", 0))
            fees_paid = float(total.get("fees_paid", 0))
            
            is_green = net_pnl > 0
            
            return {
                "pair": pair,
                "spread_bps": spread_bps,
                "guard_threshold_bps": guard_threshold_bps,
                "min_profit_bps": min_profit_bps,
                "ofi_max_bps": ofi_max_bps,
                "refresh_interval_ms": refresh_interval_ms,
                "net_pnl": net_pnl,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "fees_paid": fees_paid,
                "fill_count": fill_count,
                "volume": total_volume,
                "is_green": is_green,
                "profit_per_fill": net_pnl / fill_count if fill_count > 0 else 0.0,
                "date": date,
                "run_dir": str(run_dir),
            }
        except Exception as e:
            pass  # Fall back to CSV parsing
    
    # Fallback to CSV parsing
    return _parse_from_csv(run_dir, pair, spread_bps, guard_threshold_bps,
                           min_profit_bps, ofi_max_bps, refresh_interval_ms, date)


def _parse_from_csv(
    run_dir: Path,
    pair: str,
    spread_bps: int,
    guard_threshold_bps: int,
    min_profit_bps: int,
    ofi_max_bps: int,
    refresh_interval_ms: int,
    date: str,
) -> dict[str, Any]:
    """Fallback: parse results from CSV reports"""
    
    import pandas as pd
    
    account_file = run_dir / "account_report.csv"
    fills_file = run_dir / "fills_report.csv"
    
    if not account_file.exists():
        return {"error": "No output files found"}
    
    try:
        account = pd.read_csv(account_file)
        fills = pd.read_csv(fills_file) if fills_file.exists() and fills_file.stat().st_size > 1 else pd.DataFrame()
        
        # Calculate final equity
        base_currency = pair.replace("USDT", "")
        usdt_balance = 0.0
        base_qty = 0.0
        
        for _, row in account.tail(20).iterrows():
            if row["currency"] == "USDT":
                usdt_balance = float(row["total"])
            elif row["currency"] == base_currency:
                base_qty = float(row["total"])
        
        # Mark position to market
        base_value = 0.0
        fill_count = 0
        total_volume = 0.0
        total_fees = 0.0
        
        if len(fills) > 0:
            filled = fills[fills["filled_qty"] > 0]
            fill_count = len(filled)
            
            if fill_count > 0:
                last_price = float(filled.iloc[-1]["avg_px"])
                base_value = base_qty * last_price
                
                filled["volume"] = filled["filled_qty"] * filled["avg_px"]
                total_volume = filled["volume"].sum()
                total_fees = filled["commission"].sum() if "commission" in filled.columns else 0.0
        
        total_equity = usdt_balance + base_value
        net_pnl = total_equity - 2000.0  # Assuming $2000 starting capital
        
        is_green = net_pnl > 0
        
        return {
            "pair": pair,
            "spread_bps": spread_bps,
            "guard_threshold_bps": guard_threshold_bps,
            "min_profit_bps": min_profit_bps,
            "ofi_max_bps": ofi_max_bps,
            "refresh_interval_ms": refresh_interval_ms,
            "net_pnl": net_pnl,
            "realized_pnl": net_pnl,  # Approximation
            "unrealized_pnl": 0.0,
            "fees_paid": total_fees,
            "fill_count": fill_count,
            "volume": total_volume,
            "is_green": is_green,
            "profit_per_fill": net_pnl / fill_count if fill_count > 0 else 0.0,
            "date": date,
            "run_dir": str(run_dir),
        }
    
    except Exception as e:
        return {"error": f"CSV parsing failed: {e}"}


def run_green_sweep(
    pairs: list[str],
    date: str,
    output_dir: Path,
    questdb: bool = True,
    profile: str = "balanced",
    quick_mode: bool = False,
) -> list[dict[str, Any]]:
    """Run comprehensive parameter sweep for all pairs
    
    Optimization target: MAX(volume) WHERE pnl > 0
    """
    
    results = []
    
    # Adjust sweep ranges for quick mode
    spreads = SWEEP_CONFIG["spread_bps"] if not quick_mode else [25, 35, 50]
    guards = SWEEP_CONFIG["guard_threshold_bps"] if not quick_mode else [15, 25]
    min_profits = SWEEP_CONFIG["min_profit_bps"] if not quick_mode else [1, 3]
    ofi_maxes = SWEEP_CONFIG["ofi_max_bps"] if not quick_mode else [0, 5]
    refreshes = SWEEP_CONFIG["refresh_interval_ms"] if not quick_mode else [5000]
    
    total_runs = len(pairs) * len(spreads) * len(guards) * len(min_profits) * len(ofi_maxes) * len(refreshes)
    current_run = 0
    
    print("=" * 100)
    print("🎯 VIP1 ACQUISITION GREEN SWEEP")
    print("=" * 100)
    print(f"Pairs:          {', '.join(pairs)}")
    print(f"Date:           {date}")
    print(f"Profile:        {profile}")
    print(f"Total Runs:     {total_runs:,}")
    print(f"Mode:           {'QUICK' if quick_mode else 'FULL'}")
    print(f"Target:         $33,333 daily volume @ PnL > $0")
    print("=" * 100)
    print()
    
    for pair in pairs:
        print(f"\n{'═' * 100}")
        print(f"📊 PAIR: {pair}")
        print(f"{'═' * 100}")
        
        for spread_bps in spreads:
            for guard_bps in guards:
                for min_profit in min_profits:
                    for ofi_max in ofi_maxes:
                        for refresh_ms in refreshes:
                            current_run += 1
                            
                            print(f"[{current_run}/{total_runs}] ", end="")
                            
                            result = run_backtest(
                                pair=pair,
                                date=date,
                                spread_bps=spread_bps,
                                guard_threshold_bps=guard_bps,
                                min_profit_bps=min_profit,
                                ofi_max_bps=ofi_max,
                                refresh_interval_ms=refresh_ms,
                                output_dir=output_dir,
                                questdb=questdb,
                                profile=profile,
                            )
                            
                            if "error" in result:
                                print(f"  ❌ {result['error']}")
                                continue
                            
                            results.append(result)
                            
                            # Print immediate feedback
                            status = "✅ GREEN" if result["is_green"] else "❌ RED"
                            print(f"  {status} | PnL: ${result['net_pnl']:>8.2f} | "
                                  f"Fills: {result['fill_count']:>4} | "
                                  f"Vol: ${result['volume']:>10,.0f}")
    
    return results


def save_results(results: list[dict[str, Any]], output_file: Path) -> None:
    """Save sweep results to JSON"""
    
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\n💾 Results saved to: {output_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VIP1 Acquisition Green Sweep - Multi-pair Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full sweep on 5-pair portfolio
  %(prog)s --date 2026-01-28 --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT
  
  # Quick sweep for testing
  %(prog)s --date 2026-01-28 --pairs SUIUSDT --quick
  
  # Add expansion pairs
  %(prog)s --date 2026-01-28 --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,SOLUSDT,XRPUSDT
        """,
    )
    
    parser.add_argument("--date", required=True, help="Backtest date (YYYY-MM-DD)")
    parser.add_argument(
        "--pairs",
        default=None,
        help="Comma-separated pair list (default: VIP1 5-pair portfolio)",
    )
    parser.add_argument(
        "--questdb",
        action="store_true",
        default=True,
        help="Use QuestDB for orderbook data (default: True)",
    )
    parser.add_argument(
        "--profile",
        default="balanced",
        choices=["accuracy", "balanced", "speed"],
        help="Fill model profile (default: balanced)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: reduced parameter grid for testing",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: sweep_results/vip_acquisition_TIMESTAMP)",
    )
    
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    # Determine pair list
    if args.pairs:
        pairs = [p.strip() for p in args.pairs.split(",")]
    else:
        pairs = list(VIP1_PAIRS.keys())
    
    # Create output directory
    if args.out_dir:
        output_dir = Path(args.out_dir)
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_dir = Path("sweep_results") / f"vip_acquisition_{timestamp}"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Run sweep
    results = run_green_sweep(
        pairs=pairs,
        date=args.date,
        output_dir=output_dir,
        questdb=args.questdb,
        profile=args.profile,
        quick_mode=args.quick,
    )
    
    # Save results
    results_file = output_dir / "sweep_results.json"
    save_results(results, results_file)
    
    # Print summary
    green_count = sum(1 for r in results if r.get("is_green", False))
    total_count = len(results)
    
    print("\n" + "=" * 100)
    print("📈 SWEEP SUMMARY")
    print("=" * 100)
    print(f"Total Runs:        {total_count}")
    print(f"Green Configs:     {green_count} ({100 * green_count / total_count:.1f}%)")
    print(f"Red Configs:       {total_count - green_count}")
    print(f"\nNext step: Run analyze_vip_progress.py to determine optimal portfolio configuration")
    print("=" * 100)


if __name__ == "__main__":
    main()
