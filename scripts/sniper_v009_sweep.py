#!/usr/bin/env python3
"""
SNIPER v009 Parameter Sweep - Find Profitable Configurations
VIP1 Fees: 0.0675% maker/taker (13.5 bps roundtrip)
"""

import subprocess
import pandas as pd
from pathlib import Path
import sys

# Pairs with data available
PAIRS_CONFIG = {
    "LINKUSDT": {
        "date": "2026-02-01",
        "spreads": [8, 10, 11, 12, 13, 14, 15, 18, 20],
        "base_price": 21.0,
        "initial_base": 20.0,
    },
    "SUIUSDT": {
        "date": "2026-01-30", 
        "spreads": [4, 5, 6, 8, 10, 12, 15, 20],
        "base_price": 1.32,
        "initial_base": 100.0,
    },
    "AVAXUSDT": {
        "date": "2026-01-29",
        "spreads": [15, 20, 25, 30, 40, 50, 60],
        "base_price": 35.0,
        "initial_base": 28.57,
    },
}

def run_backtest(symbol: str, date: str, spread: int, max_updates: int = 30000) -> dict:
    """Run a single backtest and return results."""
    cmd = [
        sys.executable,
        "examples/backtest/sniper_v009_backtest.py",
        "--symbol", symbol,
        "--date", date,
        "--breakout-confirm-ticks", "5",
        "--range-confirm-ticks", "2", 
        "--max-hold-secs", "300",
        "--max-updates", str(max_updates),
        "--ranging-spread-bps", str(spread),
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    
    # Read results
    output_dir = Path(f"backtest_results/sniper_v009/{symbol}/{date}")
    fills_file = output_dir / "fills_report.csv"
    accounts_file = output_dir / "account_report.csv"
    
    if not fills_file.exists() or not accounts_file.exists():
        return {"error": "no_output"}
    
    try:
        fills = pd.read_csv(fills_file)
        accounts = pd.read_csv(accounts_file)
        
        if len(fills) == 0:
            return {"fills": 0, "volume": 0, "pnl": 0}
        
        total_value = (fills['filled_qty'] * fills['avg_px']).sum()
        
        config = PAIRS_CONFIG[symbol]
        base_currency = symbol[:-4]  # Remove 'USDT'
        
        final_usdt = accounts[accounts.currency == 'USDT'].iloc[-1]['total']
        final_base = accounts[accounts.currency == base_currency].iloc[-1]['total']
        
        initial_value = 10000 + config['initial_base'] * config['base_price']
        final_value = final_usdt + final_base * config['base_price']
        pnl = final_value - initial_value
        
        return {
            "fills": len(fills),
            "volume": total_value,
            "pnl": pnl,
        }
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 70)
    print("SNIPER v009 PARAMETER SWEEP - VIP1 Fees (13.5 bps RT)")
    print("=" * 70)
    
    results = {}
    
    for symbol, config in PAIRS_CONFIG.items():
        print(f"\n=== {symbol} ===")
        results[symbol] = []
        
        for spread in config['spreads']:
            result = run_backtest(symbol, config['date'], spread)
            
            if 'error' in result:
                print(f"  {spread:2d} bps: ERROR - {result['error']}")
            else:
                status = "✅" if result['pnl'] > 0 else "❌"
                print(f"  {spread:2d} bps: {result['fills']:3d} fills | Vol: ${result['volume']:7.0f} | PnL: ${result['pnl']:+7.2f} {status}")
                results[symbol].append({
                    "spread": spread,
                    **result
                })
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY - Optimal Configurations")
    print("=" * 70)
    
    for symbol, runs in results.items():
        profitable = [r for r in runs if r.get('pnl', -1) > 0]
        if profitable:
            # Find best balance of profit and volume
            best = max(profitable, key=lambda x: x['fills'])
            print(f"{symbol}: {best['spread']} bps → {best['fills']} fills, ${best['volume']:.0f} vol, ${best['pnl']:+.2f} ✅")
        else:
            print(f"{symbol}: NO PROFITABLE CONFIG ❌")


if __name__ == "__main__":
    main()
