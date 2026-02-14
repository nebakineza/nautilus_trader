#!/usr/bin/env python3
"""
SNIPER v009 - Multi-Pair Sweep
Find profitable configurations for all pairs with orderbook data
"""

import subprocess
import pandas as pd
from pathlib import Path
from dataclasses import dataclass

@dataclass
class PairResult:
    spread: int
    fills: int
    volume: float
    pnl: float
    status: str

# Pairs with orderbook data
PAIRS_TO_TEST = {
    # Pair: (date, initial_base_price_estimate)
    "LINKUSDT": ("2026-02-01", 21.0),
    "AVAXUSDT": ("2026-02-01", 35.0),
    "SOLUSDT": ("2026-01-30", 200.0),
    "XRPUSDT": ("2026-01-28", 2.5),
    "DOGEUSDT": ("2026-01-30", 0.40),
}

SPREADS_TO_TEST = [5, 8, 10, 12, 15, 20, 25]

def run_backtest(symbol: str, date: str, spread: int) -> dict:
    """Run single backtest."""
    cmd = [
        ".venv/bin/python",
        "examples/backtest/sniper_v009_backtest.py",
        "--symbol", symbol,
        "--date", date,
        "--breakout-confirm-ticks", "5",
        "--range-confirm-ticks", "2",
        "--max-hold-secs", "300",
        "--max-updates", "30000",
        "--ranging-spread-bps", str(spread),
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}
    
    # Read results
    output_dir = Path(f"backtest_results/sniper_v009/{symbol}/{date}")
    fills_file = output_dir / "fills_report.csv"
    accounts_file = output_dir / "account_report.csv"
    
    if not fills_file.exists() or not accounts_file.exists():
        return {"fills": 0, "volume": 0, "pnl": 0}
    
    try:
        fills = pd.read_csv(fills_file)
        accounts = pd.read_csv(accounts_file)
        
        if len(fills) == 0:
            return {"fills": 0, "volume": 0, "pnl": 0}
        
        total_value = (fills['filled_qty'] * fills['avg_px']).sum()
        final_usdt = accounts[accounts.currency == 'USDT'].iloc[-1]['total']
        
        # Simple P&L: just USDT change (ignores base currency value change)
        pnl = final_usdt - 10000
        
        return {
            "fills": len(fills),
            "volume": total_value,
            "pnl": pnl,
        }
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 70)
    print("SNIPER v009 MULTI-PAIR SWEEP")
    print("VIP1 Fees: 13.5 bps roundtrip")
    print("=" * 70)
    
    all_results = {}
    
    for symbol, (date, base_price) in PAIRS_TO_TEST.items():
        print(f"\n{'='*70}")
        print(f"=== {symbol} (date: {date}) ===")
        print(f"{'='*70}")
        
        results = []
        
        for spread in SPREADS_TO_TEST:
            result = run_backtest(symbol, date, spread)
            
            if 'error' in result:
                print(f"  {spread:2d} bps: ERROR - {result['error']}")
                continue
            
            status = "✅" if result['pnl'] > 0 else "❌"
            print(f"  {spread:2d} bps: {result['fills']:3d} fills | Vol: ${result['volume']:7.0f} | PnL: ${result['pnl']:+7.2f} {status}")
            
            results.append(PairResult(
                spread=spread,
                fills=result['fills'],
                volume=result['volume'],
                pnl=result['pnl'],
                status=status
            ))
        
        all_results[symbol] = results
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY - OPTIMAL CONFIGURATIONS")
    print("=" * 70)
    
    for symbol, results in all_results.items():
        profitable = [r for r in results if r.pnl > 0]
        if profitable:
            # Find best volume among profitable
            best = max(profitable, key=lambda x: x.fills)
            print(f"✅ {symbol}: {best.spread} bps → {best.fills} fills, ${best.volume:.0f} vol, ${best.pnl:+.2f}")
        else:
            # Find least negative
            if results:
                best = max(results, key=lambda x: x.pnl)
                print(f"❌ {symbol}: Best was {best.spread} bps → {best.fills} fills, ${best.pnl:+.2f} (still losing)")
            else:
                print(f"❌ {symbol}: No valid results")
    
    print("=" * 70)


if __name__ == "__main__":
    main()
