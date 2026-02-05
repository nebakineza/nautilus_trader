#!/usr/bin/env python3
"""
Profitable Spread Optimization Sweep

Backtests Lead/Lag MM strategy across multiple spread configurations to find
the optimal spread that maximizes profit while maintaining volume targets.

PRIME DIRECTIVE: ALL PAIRS MUST BE GREEN (Positive P&L after fees)

Usage:
    python scripts/sweep_profitable_spreads.py
    python scripts/sweep_profitable_spreads.py --pair SUIUSDT
    python scripts/sweep_profitable_spreads.py --quick  # Reduced date range
"""

import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
from examples.backtest.questdb_orderbook_loader import load_questdb_orderbook_deltas


# Spread levels to test (in bps)
SPREAD_LEVELS = [25, 30, 35, 40, 45, 50, 55, 60]

# Pairs to test (all available in QuestDB)
ALL_PAIRS = ["SUIUSDT", "DOGEUSDT", "AVAXUSDT"]  # We have good data for these

# Base configuration (fixed parameters)
BASE_CONFIG = {
    "refresh_interval": 2000,  # 2 seconds
    "min_profit_bps": Decimal("1.0"),
    "guard_threshold_bps": Decimal("15.0"),
    "ofi_enabled": False,  # Disable OFI for clarity
    "ofi_max_bps": Decimal("0.0"),
    "min_quote_lifetime_ms": 800,
    "internal_price_delta_limit": Decimal("0.0"),
    "liquidity_high_qty": Decimal("5000.0"),
    "liquidity_low_qty": Decimal("100.0"),
}

# Order sizes per pair (conservative to avoid inventory risk)
ORDER_SIZES = {
    "SUIUSDT": Decimal("60.0"),
    "DOGEUSDT": Decimal("1500.0"),
    "AVAXUSDT": Decimal("3.0"),
    "ETHUSDT": Decimal("0.0075"),
    "LINKUSDT": Decimal("3.0"),
}

# Max position sizes
MAX_POSITIONS = {
    "SUIUSDT": Decimal("600.0"),
    "DOGEUSDT": Decimal("15000.0"),
    "AVAXUSDT": Decimal("30.0"),
    "ETHUSDT": Decimal("0.075"),
    "LINKUSDT": Decimal("30.0"),
}


def run_single_backtest(
    pair: str,
    spread_bps: int,
    start_date: str,
    end_date: str,
    output_dir: Path,
) -> Dict:
    """Run a single backtest for one pair at one spread level"""
    
    from strategy.lead_lag_bybit_binance_mm_v003 import LeadLagMMv3Primer, LeadLagMMv3PrimerConfig
    
    # Create config
    config = LeadLagMMv3PrimerConfig(
        instrument_id_leader=f"{pair}-SPOT.BINANCE",
        instrument_id_follower=f"{pair}-SPOT.BYBIT",
        order_qty=ORDER_SIZES.get(pair, Decimal("10.0")),
        min_order_qty=ORDER_SIZES.get(pair, Decimal("10.0")) * Decimal("0.25"),
        max_position_qty=MAX_POSITIONS.get(pair, Decimal("100.0")),
        spread_bps=Decimal(str(spread_bps)),
        guard_threshold_bps=BASE_CONFIG["guard_threshold_bps"],
        min_profit_bps=BASE_CONFIG["min_profit_bps"],
        global_guard_id=None,
        refresh_interval=BASE_CONFIG["refresh_interval"],
        refresh_offset=0,
        liquidity_high_qty=BASE_CONFIG["liquidity_high_qty"],
        liquidity_low_qty=BASE_CONFIG["liquidity_low_qty"],
        ofi_enabled=BASE_CONFIG["ofi_enabled"],
        ofi_max_bps=BASE_CONFIG["ofi_max_bps"],
        min_quote_lifetime_ms=BASE_CONFIG["min_quote_lifetime_ms"],
        internal_price_delta_limit=BASE_CONFIG["internal_price_delta_limit"],
    )
    
    # Create backtest engine
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level="ERROR"),  # Quiet
        )
    )
    
    # Add venues
    engine.add_venue(
        venue=Venue("BINANCE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USDT,
        starting_balances=[Money(2000, USDT)],
        modules=[],
    )
    
    engine.add_venue(
        venue=Venue("BYBIT"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USDT,
        starting_balances=[Money(2000, USDT)],
        modules=[],
        book_type="L2_MBP",  # Use L2_MBP for orderbook
    )
    
    # Load data from QuestDB
    print(f"  Loading data for {pair} ({start_date} to {end_date})...")
    
    try:
        # Leader (Binance) - load as quotes
        leader_quotes = load_questdb_orderbook_deltas(
            venue="BINANCE",
            symbol=pair,
            start_date=start_date,
            end_date=end_date,
            instrument_id=f"{pair}-SPOT.BINANCE",
        )
        
        # Follower (Bybit) - load as orderbook
        follower_books = load_questdb_orderbook_deltas(
            venue="BYBIT",
            symbol=pair,
            start_date=start_date,
            end_date=end_date,
            instrument_id=f"{pair}-SPOT.BYBIT",
            as_quotes=False,  # Load as L2 orderbook
        )
        
        if not leader_quotes or not follower_books:
            return {"error": "No data loaded"}
        
        # Add data to engine
        engine.add_data(leader_quotes)
        engine.add_data(follower_books)
        
    except Exception as e:
        return {"error": f"Data loading failed: {e}"}
    
    # Add strategy
    strategy = LeadLagMMv3Primer(config=config)
    engine.add_strategy(strategy)
    
    # Run backtest
    print(f"  Running backtest...")
    engine.run()
    
    # Get results
    account = engine.trader.generate_account_report(Venue("BYBIT"))
    fills = engine.trader.generate_fills_report()
    positions = engine.trader.generate_positions_report()
    
    # Calculate metrics
    total_pnl = float(account.iloc[-1]["total"]) - 2000.0 if len(account) > 0 else 0.0
    fill_count = len(fills)
    
    # Calculate volume from fills
    volume = 0.0
    total_fees = 0.0
    realized_pnl = 0.0
    
    if fill_count > 0:
        for _, fill in fills.iterrows():
            qty = float(fill.get("last_qty", 0))
            px = float(fill.get("last_px", 0))
            commission = float(fill.get("commission", 0))
            
            volume += qty * px
            total_fees += commission
    
    # Get realized PnL from closed positions
    if len(positions) > 0:
        realized_pnl = positions["realized_pnl"].sum() if "realized_pnl" in positions.columns else 0.0
    
    net_pnl = realized_pnl - total_fees
    
    # Calculate daily volume (annualized)
    days = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days
    daily_volume = volume / max(days, 1)
    
    result = {
        "pair": pair,
        "spread_bps": spread_bps,
        "net_pnl": round(net_pnl, 4),
        "realized_pnl": round(realized_pnl, 4),
        "total_fees": round(total_fees, 4),
        "fill_count": fill_count,
        "volume": round(volume, 2),
        "daily_volume": round(daily_volume, 2),
        "is_profitable": net_pnl > 0,
        "profit_per_fill": round(net_pnl / fill_count, 4) if fill_count > 0 else 0.0,
        "start_date": start_date,
        "end_date": end_date,
    }
    
    # Save detailed results
    run_name = f"{pair}_{spread_bps}bps"
    run_dir = output_dir / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    
    account.to_csv(run_dir / "account_report.csv", index=False)
    fills.to_csv(run_dir / "fills_report.csv", index=False)
    positions.to_csv(run_dir / "positions_report.csv", index=False)
    
    with open(run_dir / "result.json", "w") as f:
        json.dump(result, f, indent=2)
    
    return result


def run_sweep(
    pairs: List[str],
    start_date: str,
    end_date: str,
    output_dir: Path,
):
    """Run full sweep across all pairs and spread levels"""
    
    results = []
    total_runs = len(pairs) * len(SPREAD_LEVELS)
    current_run = 0
    
    print("=" * 80)
    print("PROFITABLE SPREAD OPTIMIZATION SWEEP")
    print("=" * 80)
    print(f"Pairs: {', '.join(pairs)}")
    print(f"Spreads: {SPREAD_LEVELS} bps")
    print(f"Period: {start_date} to {end_date}")
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
            
            result = run_single_backtest(
                pair=pair,
                spread_bps=spread_bps,
                start_date=start_date,
                end_date=end_date,
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
                  f"Volume = ${result['volume']:>10,.0f} | "
                  f"Daily = ${result['daily_volume']:>8,.0f}")
    
    return results


def analyze_results(results: List[Dict], output_dir: Path):
    """Analyze sweep results and find optimal configuration"""
    
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
        print(f"{'Spread':<10} {'Net P&L':<12} {'Fills':<8} {'Volume':<14} {'Daily Vol':<12} {'Status':<10}")
        print(f"{'─' * 80}")
        
        profitable_configs = []
        
        for r in sorted(pair_results, key=lambda x: x["spread_bps"]):
            status = "✅ GREEN" if r["is_profitable"] else "❌ RED"
            print(f"{r['spread_bps']:>3} bps    "
                  f"{r['net_pnl']:>10.2f}   "
                  f"{r['fill_count']:>6}   "
                  f"${r['volume']:>12,.0f}  "
                  f"${r['daily_volume']:>10,.0f}  "
                  f"{status}")
            
            if r["is_profitable"]:
                profitable_configs.append(r)
        
        # Find best profitable config (highest volume among profitable)
        if profitable_configs:
            best = max(profitable_configs, key=lambda x: x["daily_volume"])
            optimal_configs[pair] = best
            print(f"\n✅ OPTIMAL: {best['spread_bps']} bps → ${best['daily_volume']:,.0f}/day, +${best['net_pnl']:.2f} P&L")
        else:
            print(f"\n❌ NO PROFITABLE SPREAD FOUND - Consider dropping {pair}")
    
    # Portfolio summary
    print(f"\n{'═' * 80}")
    print("PORTFOLIO OPTIMIZATION SUMMARY")
    print(f"{'═' * 80}")
    
    if optimal_configs:
        total_daily_volume = sum(c["daily_volume"] for c in optimal_configs.values())
        total_pnl = sum(c["net_pnl"] for c in optimal_configs.values())
        
        print(f"\nOptimal Configuration:")
        print(f"{'─' * 80}")
        for pair, config in optimal_configs.items():
            print(f"  {pair:<12}: {config['spread_bps']:>3} bps  "
                  f"→  ${config['daily_volume']:>8,.0f}/day  "
                  f"(+${config['net_pnl']:>6.2f} P&L)")
        print(f"{'─' * 80}")
        print(f"  {'TOTAL':<12}:           "
              f"→  ${total_daily_volume:>8,.0f}/day  "
              f"(+${total_pnl:>6.2f} P&L)")
        print()
        
        target_volume = 33333
        coverage = (total_daily_volume / target_volume) * 100
        print(f"📊 Volume Coverage: ${total_daily_volume:,.0f} / ${target_volume:,.0f} ({coverage:.1f}%)")
        print(f"💰 Total P&L: ${total_pnl:,.2f} ({'GREEN ✅' if total_pnl > 0 else 'RED ❌'})")
        
        if total_daily_volume < target_volume:
            shortfall = target_volume - total_daily_volume
            print(f"\n⚠️  Volume Shortfall: ${shortfall:,.0f}/day")
            print(f"💡 Recommendation: Add {int(shortfall / 6000) + 1} more profitable pairs")
    else:
        print("\n❌ NO PROFITABLE CONFIGURATIONS FOUND")
        print("💡 Recommendation: Increase spreads further or change pairs")
    
    # Save summary
    summary = {
        "optimal_configs": optimal_configs,
        "total_daily_volume": total_daily_volume if optimal_configs else 0,
        "total_pnl": total_pnl if optimal_configs else 0,
        "target_volume": 33333,
        "all_results": results,
    }
    
    with open(output_dir / "sweep_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    return optimal_configs


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Sweep profitable spread configurations")
    parser.add_argument("--pair", type=str, help="Test single pair only")
    parser.add_argument("--quick", action="store_true", help="Quick test (1 day only)")
    parser.add_argument("--start", type=str, default="2026-01-28", help="Start date")
    parser.add_argument("--end", type=str, default="2026-01-31", help="End date")
    
    args = parser.parse_args()
    
    # Determine pairs to test
    pairs = [args.pair] if args.pair else ALL_PAIRS
    
    # Date range
    if args.quick:
        start_date = "2026-01-28"
        end_date = "2026-01-28"
    else:
        start_date = args.start
        end_date = args.end
    
    # Output directory
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = Path(__file__).parent.parent / "sweep_results" / f"profitable_spread_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Run sweep
    results = run_sweep(
        pairs=pairs,
        start_date=start_date,
        end_date=end_date,
        output_dir=output_dir,
    )
    
    # Analyze
    if results:
        optimal_configs = analyze_results(results, output_dir)
        
        print(f"\n📁 Results saved to: {output_dir}")
        print(f"📊 Summary: {output_dir / 'sweep_summary.json'}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
