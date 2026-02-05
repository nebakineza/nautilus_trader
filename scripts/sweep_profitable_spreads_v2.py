#!/usr/bin/env python3
"""
Profitable Spread Sweep using LLMMv3 Primer Backtest

Wraps the existing llmmv3_primer_backtest.py to sweep spread configurations.
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Spread levels to test (in bps)
SPREAD_LEVELS = [25, 30, 35, 40, 45, 50, 55, 60]

# Pairs to test
PAIRS = {
    "SUIUSDT": {"order_qty": "60.0", "min_order_qty": "15.0"},
    "DOGEUSDT": {"order_qty": "1500.0", "min_order_qty": "375.0"},
    "AVAXUSDT": {"order_qty": "3.0", "min_order_qty": "0.75"},
}


def run_backtest(pair: str, spread_bps: int, date: str, output_dir: Path) -> Dict:
    """Run single backtest using primer script"""
    
    pair_config = PAIRS[pair]
    run_name = f"{pair}_{spread_bps}bps"
    run_dir = output_dir / run_name
    
    cmd = [
        sys.executable,  # Use same Python
        "examples/backtest/llmmv3_primer_backtest.py",
        "--date", date,
        "--symbols", pair,
        "--questdb",
        "--profile", "accuracy",  # Use accurate fill model
        "--fee-profile", "vip0",  # VIP0 fees
        "--mnt-discount",  # Apply MNT discount
        "--override-symbol", pair,
        "--override-order-qty", pair_config["order_qty"],
        "--override-min-order-qty", pair_config["min_order_qty"],
        "--override-spread-bps", str(spread_bps),
        "--override-guard-threshold-bps", "15.0",
        "--override-min-profit-bps", "1.0",
        "--override-ofi-max-bps", "0.0",  # Disable OFI
        "--override-max-drawdown-pct", "1.0",  # Disable killswitch for testing
        "--out-dir", str(run_dir),
    ]
    
    print(f"  Running: {' '.join(cmd[-10:])}")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout
        )
        
        if result.returncode != 0:
            print(f"  ❌ Backtest failed:")
            print(result.stderr[:500])
            return {"error": "Backtest failed"}
        
    except subprocess.TimeoutExpired:
        return {"error": "Timeout"}
    except Exception as e:
        return {"error": str(e)}
    
    # Parse results
    try:
        # Check for different possible output files
        analysis_file = run_dir / "analysis_summary.json"
        if not analysis_file.exists():
            analysis_file = run_dir / "summary.json"
        
        if not analysis_file.exists():
            # Try to parse from account report
            account_file = run_dir / "account_report.csv"
            fills_file = run_dir / "fills_report.csv"
            
            if not account_file.exists():
                return {"error": "No output files found"}
            
            # Parse CSV files directly
            import pandas as pd
            
            account = pd.read_csv(account_file)
            orders = pd.read_csv(fills_file) if fills_file.stat().st_size > 1 else pd.DataFrame()
            
            # Calculate total equity (USDT + marked-to-market positions)
            usdt_balance = 0.0
            base_qty = 0.0
            base_currency = pair.replace("USDT", "")
            
            for _, row in account.tail(20).iterrows():
                if row["currency"] == "USDT":
                    usdt_balance = float(row["total"])
                elif row["currency"] == base_currency:
                    base_qty = float(row["total"])
            
            # Mark position to market using last fill price
            filled_orders = orders[orders["filled_qty"] > 0] if len(orders) > 0 else pd.DataFrame()
            fill_count = len(filled_orders)
            total_volume = 0.0
            base_value = 0.0
            
            if fill_count > 0:
                last_price = float(filled_orders.iloc[-1]["avg_px"])
                base_value = base_qty * last_price
                # Calculate volume from filled orders
                filled_orders["volume"] = filled_orders["filled_qty"] * filled_orders["avg_px"]
                total_volume = filled_orders["volume"].sum()
            
            total_equity = usdt_balance + base_value
            net_pnl = total_equity - 2000.0
            
            result_data = {
                "pair": pair,
                "spread_bps": spread_bps,
                "net_pnl": net_pnl,
                "usdt_balance": usdt_balance,
                "base_position": base_qty,
                "base_value": base_value,
                "total_equity": total_equity,
                "fill_count": fill_count,
                "volume": total_volume,
                "is_profitable": net_pnl > 0,
                "date": date,
            }
        
        else:
            # Parse JSON
            with open(analysis_file) as f:
                analysis = json.load(f)
            
            # Extract key metrics
            result_data = {
                "pair": pair,
                "spread_bps": spread_bps,
                "net_pnl": analysis.get("final_balance", 0) - 2000.0,
                "realized_pnl": analysis.get("realized_pnl", 0),
                "total_fees": analysis.get("total_fees", 0),
                "fill_count": analysis.get("fill_count", 0),
                "volume": analysis.get("total_volume", 0),
                "is_profitable": (analysis.get("final_balance", 0) - 2000.0) > 0,
                "date": date,
            }
        
        # Calculate derived metrics
        if result_data["fill_count"] > 0:
            result_data["profit_per_fill"] = result_data["net_pnl"] / result_data["fill_count"]
        else:
            result_data["profit_per_fill"] = 0.0
        
        return result_data
        
    except Exception as e:
        return {"error": f"Failed to parse results: {e}"}


def run_sweep(pairs: List[str], date: str, output_dir: Path):
    """Run full sweep"""
    
    results = []
    total_runs = len(pairs) * len(SPREAD_LEVELS)
    current_run = 0
    
    print("=" * 80)
    print("PROFITABLE SPREAD OPTIMIZATION SWEEP")
    print("=" * 80)
    print(f"Pairs: {', '.join(pairs)}")
    print(f"Spreads: {SPREAD_LEVELS} bps")
    print(f"Date: {date}")
    print(f"Total runs: {total_runs}")
    print("=" * 80)
    print()
    
    for pair in pairs:
        print(f"\n{'─' * 80}")
        print(f"PAIR: {pair}")
        print(f"{'─' * 80}")
        
        for spread_bps in SPREAD_LEVELS:
            current_run += 1
            print(f"\n[{current_run}/{total_runs}] Testing {pair} @ {spread_bps} bps...")
            
            result = run_backtest(
                pair=pair,
                spread_bps=spread_bps,
                date=date,
                output_dir=output_dir,
            )
            
            if "error" in result:
                print(f"  ❌ ERROR: {result['error']}")
                continue
            
            results.append(result)
            
            # Print summary
            status = "✅ GREEN" if result["is_profitable"] else "❌ RED"
            print(f"  {status}: Net P&L = {result['net_pnl']:>8.2f} USDT | "
                  f"Fills = {result['fill_count']:>4} | "
                  f"Volume = ${result['volume']:>10,.0f}")
    
    return results


def analyze_results(results: List[Dict], output_dir: Path):
    """Analyze and print results"""
    
    print("\n" + "=" * 80)
    print("SWEEP RESULTS ANALYSIS")
    print("=" * 80)
    print()
    
    # Group by pair
    by_pair = {}
    for r in results:
        pair = r["pair"]
        if pair not in by_pair:
            by_pair[pair] = []
        by_pair[pair].append(r)
    
    # Find best config for each pair
    optimal_configs = {}
    
    for pair, pair_results in by_pair.items():
        print(f"\n{'─' * 80}")
        print(f"{pair} - Spread Optimization Results")
        print(f"{'─' * 80}")
        print(f"{'Spread':<10} {'Net P&L':<12} {'Fills':<8} {'Volume':<14} {'Status':<10}")
        print(f"{'─' * 80}")
        
        profitable_configs = []
        
        for r in sorted(pair_results, key=lambda x: x["spread_bps"]):
            status = "✅ GREEN" if r["is_profitable"] else "❌ RED"
            print(f"{r['spread_bps']:>3} bps    "
                  f"{r['net_pnl']:>10.2f}   "
                  f"{r['fill_count']:>6}   "
                  f"${r['volume']:>12,.0f}  "
                  f"{status}")
            
            if r["is_profitable"]:
                profitable_configs.append(r)
        
        # Find best profitable config (highest volume)
        if profitable_configs:
            best = max(profitable_configs, key=lambda x: x["volume"])
            optimal_configs[pair] = best
            print(f"\n✅ OPTIMAL: {best['spread_bps']} bps → ${best['volume']:,.0f} volume, +${best['net_pnl']:.2f} P&L")
        else:
            print(f"\n❌ NO PROFITABLE SPREAD FOUND")
    
    # Portfolio summary
    print(f"\n{'═' * 80}")
    print("PORTFOLIO OPTIMIZATION SUMMARY")
    print(f"{'═' * 80}")
    
    if optimal_configs:
        total_volume = sum(c["volume"] for c in optimal_configs.values())
        total_pnl = sum(c["net_pnl"] for c in optimal_configs.values())
        
        print(f"\nOptimal Configuration (1 day):")
        print(f"{'─' * 80}")
        for pair, config in optimal_configs.items():
            print(f"  {pair:<12}: {config['spread_bps']:>3} bps  "
                  f"→  ${config['volume']:>10,.0f}  "
                  f"(+${config['net_pnl']:>8.2f} P&L)")
        print(f"{'─' * 80}")
        print(f"  {'TOTAL':<12}:           "
              f"→  ${total_volume:>10,.0f}  "
              f"(+${total_pnl:>8.2f} P&L)")
        print()
        
        target_volume = 33333
        coverage = (total_volume / target_volume) * 100
        print(f"📊 Volume Coverage: ${total_volume:,.0f} / ${target_volume:,.0f} ({coverage:.1f}%)")
        print(f"💰 Total P&L: +${total_pnl:,.2f} ({'GREEN ✅' if total_pnl > 0 else 'RED ❌'})")
        
        if total_volume < target_volume:
            shortfall = target_volume - total_volume
            print(f"\n⚠️  Volume Shortfall: ${shortfall:,.0f}/day")
            print(f"💡 Add {int(shortfall / 6000) + 1} more profitable pairs to hit target")
    
    # Save summary
    summary = {
        "optimal_configs": optimal_configs,
        "all_results": results,
    }
    
    with open(output_dir / "sweep_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    return optimal_configs


def main():
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="2026-01-28", help="Backtest date")
    parser.add_argument("--pair", type=str, help="Test single pair only")
    
    args = parser.parse_args()
    
    # Pairs to test
    pairs = [args.pair] if args.pair else list(PAIRS.keys())
    
    # Output directory
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = Path(__file__).parent.parent / "sweep_results" / f"profitable_sweep_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Run sweep
    results = run_sweep(
        pairs=pairs,
        date=args.date,
        output_dir=output_dir,
    )
    
    # Analyze
    if results:
        analyze_results(results, output_dir)
        print(f"\n📁 Results saved to: {output_dir}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
