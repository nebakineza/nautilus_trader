#!/usr/bin/env python3
"""
Spread Sweep Backtest for VIP0 + MNT Discount Fee Tier
Tests each pair at 20/25/30/35 bps to find optimal spread
"""
import subprocess
import json
from pathlib import Path
from datetime import datetime

# Fee-aware spread configurations to test
SPREAD_CONFIGS = [20, 25, 30, 35]

# Pairs to optimize
PAIRS = ["SUIUSDT", "DOGEUSDT", "ETHUSDT", "LINKUSDT", "AVAXUSDT"]

# Backtest date (single day - script uses --date not --start-date)
TEST_DATE = "2026-01-30"

# Output directory
OUTPUT_DIR = Path("backtest_results/spread_sweep")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def run_backtest(pair: str, spread_bps: int) -> dict:
    """Run single backtest for pair at given spread"""
    
    print(f"\n{'='*60}")
    print(f"Testing {pair} @ {spread_bps} bps")
    print(f"{'='*60}")
    
    output_dir = OUTPUT_DIR / f"{pair}_{spread_bps}bps"
    output_dir.mkdir(exist_ok=True)
    
    cmd = [
        "/home/seb/nebakineza/nautilus_trader/.venv/bin/python", "examples/backtest/llmmv3_primer_backtest.py",
        "--date", TEST_DATE,
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
            timeout=600  # 10 minute timeout
        )
        
        # Parse results from analysis_summary.json
        summary_file = output_dir / "analysis_summary.json"
        if summary_file.exists():
            with open(summary_file) as f:
                summary = json.load(f)
                
            return {
                "pair": pair,
                "spread_bps": spread_bps,
                "realized_pnl": summary.get("total_pnl", 0),
                "total_volume": summary.get("total_volume", 0),
                "num_fills": summary.get("num_fills", 0),
                "sharpe_ratio": summary.get("sharpe_ratio", 0),
                "success": True
            }
        else:
            print(f"❌ No summary file found for {pair} @ {spread_bps}bps")
            return {
                "pair": pair,
                "spread_bps": spread_bps,
                "success": False,
                "error": "No summary file"
            }
            
    except subprocess.TimeoutExpired:
        print(f"❌ Timeout for {pair} @ {spread_bps}bps")
        return {
            "pair": pair,
            "spread_bps": spread_bps,
            "success": False,
            "error": "Timeout"
        }
    except Exception as e:
        print(f"❌ Error for {pair} @ {spread_bps}bps: {e}")
        return {
            "pair": pair,
            "spread_bps": spread_bps,
            "success": False,
            "error": str(e)
        }

def main():
    """Run sweep across all pairs and spreads"""
    
    print(f"\n{'#'*60}")
    print("SPREAD SWEEP BACKTEST")
    print(f"{'#'*60}")
    print(f"Date: {TEST_DATE}")
    print(f"Pairs: {', '.join(PAIRS)}")
    print(f"Spreads: {', '.join(map(str, SPREAD_CONFIGS))} bps")
    print(f"Fee Tier: VIP0 + MNT Discount (15 bps round trip)")
    print(f"{'#'*60}\n")
    
    results = []
    
    # Run all backtests
    for pair in PAIRS:
        for spread in SPREAD_CONFIGS:
            result = run_backtest(pair, spread)
            results.append(result)
    
    # Save all results
    results_file = OUTPUT_DIR / "sweep_results.json"
    with open(results_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "config": {
                "pairs": PAIRS,
                "spreads": SPREAD_CONFIGS,
                "date": TEST_DATE,
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
    for pair in PAIRS:
        print(f"\n{pair}:")
        print("-" * 60)
        
        pair_results = [r for r in results if r["pair"] == pair and r["success"]]
        if not pair_results:
            print("  ❌ No successful backtests")
            continue
        
        # Sort by realized P&L
        pair_results.sort(key=lambda x: x.get("realized_pnl", -999999), reverse=True)
        
        for r in pair_results:
            pnl = r.get("realized_pnl", 0)
            volume = r.get("total_volume", 0)
            fills = r.get("num_fills", 0)
            sharpe = r.get("sharpe_ratio", 0)
            
            status = "✅" if pnl > 0 else "❌"
            
            print(f"  {status} {r['spread_bps']:2d} bps: "
                  f"P&L=${pnl:>8.2f} | "
                  f"Volume=${volume:>10,.0f} | "
                  f"Fills={fills:>4} | "
                  f"Sharpe={sharpe:>5.2f}")
        
        # Recommend optimal spread
        profitable = [r for r in pair_results if r.get("realized_pnl", 0) > 0]
        if profitable:
            # Choose tightest profitable spread with good Sharpe
            best = min(profitable, key=lambda x: x["spread_bps"])
            print(f"\n  ✅ RECOMMENDED: {best['spread_bps']} bps "
                  f"(P&L=${best.get('realized_pnl', 0):.2f}, "
                  f"Sharpe={best.get('sharpe_ratio', 0):.2f})")
        else:
            print(f"\n  ⚠️  RECOMMENDED: Use widest tested (35 bps) or wider")
    
    print("\n" + "="*60)

if __name__ == "__main__":
    main()
