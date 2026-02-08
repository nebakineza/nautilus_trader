#!/usr/bin/env python3
"""VIP1 Acquisition Progress Analyzer

Reads backtest sweep results and determines optimal portfolio configuration.

Decision Logic:
1. Filter all runs where Total PnL > $0 (ZERO LOSS RULE)
2. For each pair, select "Best Green Config" = MAX(volume) WHERE pnl > 0
3. Sum top volumes across all pairs
4. If Sum >= $33,333: SUCCESS ✅
5. If Sum < $33,333: Recommend "ADD PAIRS" 🔄

Output: portfolio_summary.json with optimal config for each pair
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any


VIP1_TARGET = 33_333.0  # Daily volume target in USD


def load_sweep_results(sweep_file: Path) -> list[dict[str, Any]]:
    """Load sweep results from JSON file"""
    
    if not sweep_file.exists():
        raise FileNotFoundError(f"Sweep results not found: {sweep_file}")
    
    with open(sweep_file) as f:
        results = json.load(f)
    
    print(f"✅ Loaded {len(results)} sweep results from {sweep_file}")
    return results


def filter_green_configs(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter configs that meet ZERO LOSS RULE (PnL > 0)"""
    
    green_configs = [r for r in results if r.get("is_green", False)]
    
    print(f"🟢 Green configs: {len(green_configs)} / {len(results)} "
          f"({100 * len(green_configs) / len(results):.1f}%)")
    
    return green_configs


def find_best_config_per_pair(green_configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """For each pair, find config with MAXIMUM VOLUME while maintaining PnL > 0"""
    
    by_pair = defaultdict(list)
    
    for config in green_configs:
        pair = config["pair"]
        by_pair[pair].append(config)
    
    best_configs = {}
    
    for pair, configs in by_pair.items():
        # Sort by volume (descending)
        configs_sorted = sorted(configs, key=lambda x: x["volume"], reverse=True)
        best = configs_sorted[0]
        
        best_configs[pair] = best
        
        print(f"\n📊 {pair} - Best Green Config:")
        print(f"   Spread:        {best['spread_bps']} bps")
        print(f"   Guard:         {best['guard_threshold_bps']} bps")
        print(f"   Min Profit:    {best['min_profit_bps']} bps")
        print(f"   OFI Max:       {best['ofi_max_bps']} bps")
        print(f"   Refresh:       {best['refresh_interval_ms']} ms")
        print(f"   Net PnL:       ${best['net_pnl']:,.2f}")
        print(f"   Volume:        ${best['volume']:,.2f}")
        print(f"   Fills:         {best['fill_count']}")
        print(f"   P&L/Fill:      ${best['profit_per_fill']:.4f}")
    
    return best_configs


def calculate_portfolio_metrics(best_configs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Calculate total portfolio metrics"""
    
    total_volume = sum(c["volume"] for c in best_configs.values())
    total_pnl = sum(c["net_pnl"] for c in best_configs.values())
    total_fills = sum(c["fill_count"] for c in best_configs.values())
    total_fees = sum(c["fees_paid"] for c in best_configs.values())
    
    meets_target = total_volume >= VIP1_TARGET
    gap = VIP1_TARGET - total_volume
    gap_pct = (gap / VIP1_TARGET) * 100
    
    portfolio = {
        "total_volume": total_volume,
        "total_pnl": total_pnl,
        "total_fills": total_fills,
        "total_fees": total_fees,
        "avg_pnl_per_fill": total_pnl / total_fills if total_fills > 0 else 0,
        "pair_count": len(best_configs),
        "vip1_target": VIP1_TARGET,
        "meets_target": meets_target,
        "gap": gap,
        "gap_pct": gap_pct,
    }
    
    return portfolio


def recommend_next_steps(
    portfolio: dict[str, Any],
    best_configs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Generate recommendations for VIP1 acquisition"""
    
    recommendations = {
        "status": "",
        "actions": [],
        "expansion_candidates": [],
    }
    
    if portfolio["meets_target"]:
        recommendations["status"] = "SUCCESS"
        recommendations["actions"].append(
            f"✅ Target achieved! Portfolio generates ${portfolio['total_volume']:,.0f}/day "
            f"with Net PnL = ${portfolio['total_pnl']:,.2f}"
        )
        recommendations["actions"].append(
            "🚀 Deploy to production with these configurations"
        )
    else:
        recommendations["status"] = "EXPANSION_NEEDED"
        recommendations["actions"].append(
            f"🔄 Volume gap: ${abs(portfolio['gap']):,.0f} ({abs(portfolio['gap_pct']):.1f}% short of target)"
        )
        recommendations["actions"].append(
            "⚠️  DO NOT loosen parameters - maintain ZERO LOSS RULE"
        )
        recommendations["actions"].append(
            "➕ Add high-activity pairs to close the gap:"
        )
        
        # Recommend expansion pairs
        expansion_pairs = ["SOLUSDT", "XRPUSDT", "ARBUSDT", "ADAUSDT", "MATICUSDT"]
        recommendations["expansion_candidates"] = expansion_pairs
        
        for pair in expansion_pairs:
            recommendations["actions"].append(f"   • {pair}")
        
        recommendations["actions"].append(
            f"\n📋 Re-run sweep with expanded pair list to meet target"
        )
    
    return recommendations


def generate_deployment_config(best_configs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Generate production deployment configuration"""
    
    deployment = {
        "timestamp": str(Path.cwd()),  # Placeholder
        "pairs": {},
    }
    
    for pair, config in best_configs.items():
        deployment["pairs"][pair] = {
            "spread_bps": config["spread_bps"],
            "guard_threshold_bps": config["guard_threshold_bps"],
            "min_profit_bps": config["min_profit_bps"],
            "ofi_max_bps": config["ofi_max_bps"],
            "refresh_interval_ms": config["refresh_interval_ms"],
            "expected_volume": config["volume"],
            "expected_pnl": config["net_pnl"],
            "expected_fills": config["fill_count"],
        }
    
    return deployment


def print_summary(
    portfolio: dict[str, Any],
    best_configs: dict[str, dict[str, Any]],
    recommendations: dict[str, Any],
) -> None:
    """Print comprehensive summary report"""
    
    print("\n" + "=" * 100)
    print("🎯 VIP1 ACQUISITION PROGRESS SUMMARY")
    print("=" * 100)
    
    # Portfolio metrics
    print(f"\n📈 PORTFOLIO METRICS")
    print(f"{'─' * 100}")
    print(f"Active Pairs:      {portfolio['pair_count']}")
    print(f"Total Volume:      ${portfolio['total_volume']:,.2f} / ${portfolio['vip1_target']:,.2f} target")
    print(f"Total Net PnL:     ${portfolio['total_pnl']:,.2f}")
    print(f"Total Fills:       {portfolio['total_fills']:,}")
    print(f"Total Fees Paid:   ${portfolio['total_fees']:,.2f}")
    print(f"Avg P&L/Fill:      ${portfolio['avg_pnl_per_fill']:.4f}")
    
    # Progress bar
    progress = min(100, (portfolio['total_volume'] / portfolio['vip1_target']) * 100)
    bar_length = 50
    filled = int(bar_length * progress / 100)
    bar = "█" * filled + "░" * (bar_length - filled)
    print(f"\nProgress:          [{bar}] {progress:.1f}%")
    
    if portfolio["meets_target"]:
        print(f"\n✅ STATUS: TARGET ACHIEVED")
    else:
        print(f"\n⚠️  STATUS: GAP REMAINING (${abs(portfolio['gap']):,.0f} short)")
    
    # Recommendations
    print(f"\n{'─' * 100}")
    print(f"💡 RECOMMENDATIONS")
    print(f"{'─' * 100}")
    for action in recommendations["actions"]:
        print(action)
    
    print("\n" + "=" * 100)


def save_portfolio_summary(
    output_file: Path,
    portfolio: dict[str, Any],
    best_configs: dict[str, dict[str, Any]],
    recommendations: dict[str, Any],
    deployment: dict[str, Any],
) -> None:
    """Save comprehensive portfolio summary to JSON"""
    
    summary = {
        "portfolio_metrics": portfolio,
        "best_configs_per_pair": best_configs,
        "recommendations": recommendations,
        "deployment_config": deployment,
    }
    
    output_file.write_text(json.dumps(summary, indent=2))
    print(f"\n💾 Portfolio summary saved to: {output_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze VIP1 acquisition sweep results and generate optimal portfolio config",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze latest sweep results
  %(prog)s --sweep-file sweep_results/vip_acquisition_20260205_120000/sweep_results.json
  
  # Auto-find latest results
  %(prog)s --auto
        """,
    )
    
    parser.add_argument(
        "--sweep-file",
        type=Path,
        help="Path to sweep_results.json from run_vip_acquisition_sweep.py",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-detect latest sweep results in sweep_results/",
    )
    parser.add_argument(
        "--out-file",
        type=Path,
        default=None,
        help="Output file for portfolio summary (default: portfolio_summary.json in sweep dir)",
    )
    
    return parser.parse_args()


def find_latest_sweep_results() -> Path:
    """Auto-detect latest sweep results file"""
    
    sweep_root = Path("sweep_results")
    if not sweep_root.exists():
        raise FileNotFoundError("No sweep_results/ directory found")
    
    # Find all vip_acquisition_* directories
    vip_dirs = sorted(sweep_root.glob("vip_acquisition_*"), reverse=True)
    
    if not vip_dirs:
        raise FileNotFoundError("No vip_acquisition_* directories found in sweep_results/")
    
    # Use most recent
    latest_dir = vip_dirs[0]
    results_file = latest_dir / "sweep_results.json"
    
    if not results_file.exists():
        raise FileNotFoundError(f"No sweep_results.json found in {latest_dir}")
    
    print(f"🔍 Auto-detected: {results_file}")
    return results_file


def main() -> None:
    args = parse_args()
    
    # Determine input file
    if args.auto:
        sweep_file = find_latest_sweep_results()
    elif args.sweep_file:
        sweep_file = args.sweep_file
    else:
        print("❌ Error: Must specify --sweep-file or --auto")
        return
    
    # Determine output file
    if args.out_file:
        output_file = args.out_file
    else:
        output_file = sweep_file.parent / "portfolio_summary.json"
    
    print("=" * 100)
    print("📊 VIP1 ACQUISITION ANALYZER")
    print("=" * 100)
    print(f"Input:  {sweep_file}")
    print(f"Output: {output_file}")
    print("=" * 100)
    
    # Load and analyze
    results = load_sweep_results(sweep_file)
    green_configs = filter_green_configs(results)
    
    if not green_configs:
        print("\n❌ CRITICAL: No profitable configurations found!")
        print("   Check fee assumptions, spread ranges, or data quality")
        return
    
    best_configs = find_best_config_per_pair(green_configs)
    portfolio = calculate_portfolio_metrics(best_configs)
    recommendations = recommend_next_steps(portfolio, best_configs)
    deployment = generate_deployment_config(best_configs)
    
    # Print summary
    print_summary(portfolio, best_configs, recommendations)
    
    # Save results
    save_portfolio_summary(output_file, portfolio, best_configs, recommendations, deployment)
    
    print(f"\n🎯 Analysis complete!")


if __name__ == "__main__":
    main()
