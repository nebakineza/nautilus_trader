#!/usr/bin/env python3
"""
Spread Sweep Backtest v2 - For pairs with available data
Tests ETH, LINK, AVAX at 30/40/50/60 bps spreads
"""
import subprocess
import json
from pathlib import Path
from datetime import datetime

# Spread configurations to test (in bps)
SPREAD_CONFIGS = [30, 40, 50, 60]

# Test configurations per pair (based on data availability)
PAIR_CONFIGS = {
    "ETHUSDT": {
        "dates": ["2026-01-29", "2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"],
        "enabled": True
    },
    "LINKUSDT": {
        "dates": ["2026-01-31", "2026-02-01", "2026-02-02"],
        "enabled": True
    },
    "AVAXUSDT": {
        "dates": ["2026-02-02"],
        "enabled": True  # Only 1 day of overlap
    }
}

# Output directory
OUTPUT_DIR = Path("backtest_results/spread_sweep_v2")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def run_backtest(pair: str, date: str, spread_bps: int) -> dict:
    """Run single backtest for pair at given spread and date"""
    
    run_id = f"{pair}_{date}_{spread_bps}bps"
    print(f"\n{'='*60}")
    print(f"Testing {pair} @ {spread_bps} bps on {date}")
    print(f"{'='*60}")
    
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(exist_ok=True)
    
    cmd = [
        "/home/seb/nebakineza/nautilus_trader/.venv/bin/python",
        "examples/backtest/llmmv3_primer_backtest.py",
        "--date", date,
        "--symbols", pair,
        "--override-spread-bps", str(spread_bps),
        "--override-min-profit-bps", "1.0",
        "--fee-profile", "vip0",
        "--mnt-discount",
        "--questdb",
        "--out-dir", str(output_dir),
        "--fill-model", "competition",
        "--latency-ms", "10",
        "--liquidity-consumption"
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout per day
        )
        
        # Check for fills
        fills_file = output_dir / "fills_report.csv"
        if fills_file.exists():
            with open(fills_file) as f:
                num_fills = len(f.readlines()) - 1  # Subtract header
        else:
            num_fills = 0
        
        # Check account balance change
        account_file = output_dir / "account_report.csv"
        pnl = 0.0
        if account_file.exists():
            with open(account_file) as f:
                lines = f.readlines()
                if len(lines) > 1:
                    # First data line (starting balance)
                    start_balance = float(lines[1].split(',')[0])
                    # Last data line (ending balance)
                    end_balance = float(lines[-1].split(',')[0])
                    pnl = end_balance - start_balance
        
        return {
            "pair": pair,
            "date": date,
            "spread_bps": spread_bps,
            "num_fills": num_fills,
            "pnl": pnl,
            "success": True
        }
            
    except subprocess.TimeoutExpired:
        print(f"❌ Timeout for {pair} @ {spread_bps}bps on {date}")
        return {
            "pair": pair,
            "date": date,
            "spread_bps": spread_bps,
            "success": False,
            "error": "Timeout"
        }
    except Exception as e:
        print(f"❌ Error for {pair} @ {spread_bps}bps on {date}: {e}")
        return {
            "pair": pair,
            "date": date,
            "spread_bps": spread_bps,
            "success": False,
            "error": str(e)
        }

def main():
    """Run sweep across all pairs, dates, and spreads"""
    
    print(f"\n{'#'*60}")
    print("SPREAD SWEEP BACKTEST V2")
    print(f"{'#'*60}")
    print(f"Pairs: {', '.join([p for p, c in PAIR_CONFIGS.items() if c['enabled']])}")
    print(f"Spreads: {', '.join(map(str, SPREAD_CONFIGS))} bps")
    print(f"Fee Tier: VIP0 + MNT Discount (15 bps round trip)")
    print(f"{'#'*60}\n")
    
    results = []
    total_tests = sum(
        len(config["dates"]) * len(SPREAD_CONFIGS) 
        for config in PAIR_CONFIGS.values() 
        if config["enabled"]
    )
    current_test = 0
    
    # Run all backtests
    for pair, config in PAIR_CONFIGS.items():
        if not config["enabled"]:
            continue
            
        for date in config["dates"]:
            for spread in SPREAD_CONFIGS:
                current_test += 1
                print(f"\n[{current_test}/{total_tests}] ", end="")
                result = run_backtest(pair, date, spread)
                results.append(result)
    
    # Save all results
    results_file = OUTPUT_DIR / "sweep_results.json"
    with open(results_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "config": {
                "pairs": {k: v for k, v in PAIR_CONFIGS.items() if v["enabled"]},
                "spreads": SPREAD_CONFIGS,
                "fee_tier": "VIP0 + MNT Discount"
            },
            "results": results
        }, f, indent=2)
    
    print(f"\n{'='*60}")
    print("SWEEP COMPLETE")
    print(f"{'='*60}")
    print(f"Results saved to: {results_file}")
    
    # Generate summary report
    generate_report(results)

def generate_report(results: list):
    """Generate human-readable summary report"""
    
    print("\n" + "="*60)
    print("SUMMARY REPORT")
    print("="*60 + "\n")
    
    # Group by pair
    for pair in sorted(set(r["pair"] for r in results if r["success"])):
        print(f"\n{pair}:")
        print("-" * 60)
        
        pair_results = [r for r in results if r["pair"] == pair and r["success"]]
        if not pair_results:
            print("  ❌ No successful backtests")
            continue
        
        # Aggregate by spread (across all dates)
        spread_summary = {}
        for spread in SPREAD_CONFIGS:
            spread_results = [r for r in pair_results if r["spread_bps"] == spread]
            if spread_results:
                total_fills = sum(r["num_fills"] for r in spread_results)
                total_pnl = sum(r["pnl"] for r in spread_results)
                num_days = len(spread_results)
                spread_summary[spread] = {
                    "fills": total_fills,
                    "pnl": total_pnl,
                    "days": num_days,
                    "avg_fills_per_day": total_fills / num_days if num_days > 0 else 0,
                    "avg_pnl_per_day": total_pnl / num_days if num_days > 0 else 0
                }
        
        # Sort by spread
        for spread in sorted(spread_summary.keys()):
            s = spread_summary[spread]
            status = "✅" if s["pnl"] >= 0 else "❌"
            
            print(f"  {status} {spread:2d} bps: "
                  f"P&L=${s['pnl']:>8.2f} ({s['days']} days) | "
                  f"Fills={s['fills']:>4} ({s['avg_fills_per_day']:.1f}/day) | "
                  f"Avg P&L/day=${s['avg_pnl_per_day']:>7.2f}")
        
        # Recommend optimal spread
        profitable = [(s, d) for s, d in spread_summary.items() if d["pnl"] >= 0]
        if profitable:
            # Choose tightest profitable spread
            best_spread = min(s for s, _ in profitable)
            best = spread_summary[best_spread]
            print(f"\n  ✅ RECOMMENDED: {best_spread} bps "
                  f"(P&L=${best['pnl']:.2f} over {best['days']} days, "
                  f"{best['fills']} fills)")
        else:
            print(f"\n  ⚠️  RECOMMENDED: All spreads unprofitable - test wider (70-100 bps)")
    
    print("\n" + "="*60)

if __name__ == "__main__":
    main()
