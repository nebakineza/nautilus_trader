#!/usr/bin/env python3
"""Specialized low-capital strategy backtest for $500 accounts.

This runner tests the optimized low-capital strategy against the scaled-down
institutional strategy to compare performance.

Usage:
    python run_specialized_lowcap_backtest.py [--max-updates 50000]
"""

import argparse
import sys
import time
from decimal import Decimal
from pathlib import Path
from datetime import datetime

import pandas as pd

# Import Nautilus components
from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, InstrumentId, Symbol
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

# Import specialized low-capital strategy
from strategy.hft_obi_bybit_spot_mm_lowcap_v001 import LowCapitalOBIMarketMaker, LowCapitalMMConfig
from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from examples.backtest.institutional_models import create_institutional_backtest_models


def create_btcusdt_bybit_instrument() -> CurrencyPair:
    """Create BTCUSDT-SPOT.BYBIT instrument."""
    return CurrencyPair(
        instrument_id=InstrumentId.from_str("BTCUSDT-SPOT.BYBIT"),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=1,
        size_precision=6,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=Quantity.from_str("1000.0"),
        min_quantity=Quantity.from_str("0.000001"),
        max_notional=None,
        min_notional=None,
        max_price=Price.from_str("1000000.0"),
        min_price=Price.from_str("0.1"),
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        maker_fee=Decimal("-0.0001"),
        taker_fee=Decimal("0.0006"),
        ts_event=0,
        ts_init=0,
    )


def setup_backtest_engine(
    capital_usd: float = 500.0,
    latency_model=None,
    fill_model=None,
) -> tuple[BacktestEngine, CurrencyPair]:
    """Configure backtest engine for specialized low-capital strategy."""
    print("\n" + "=" * 70)
    print("SPECIALIZED LOW-CAPITAL STRATEGY BACKTEST")
    print("=" * 70)
    print(f"Account Capital: ${capital_usd:,.2f} USD")
    print(f"Strategy: Optimized for micro-capital accounts")
    
    # Configure engine
    config = BacktestEngineConfig(
        trader_id=TraderId("LOWCAP-SPECIALIST-001"),
    )
    
    engine = BacktestEngine(config=config)
    print(f"✓ BacktestEngine created: {config.trader_id}")
    
    # Create instrument
    instrument = create_btcusdt_bybit_instrument()
    print(f"✓ Instrument created: {instrument.id}")
    
    # Add venue
    print(f"\n✓ Adding venue: {BYBIT_VENUE}")
    
    btc_allocation = capital_usd / 100_000 * 0.1
    
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[
            Money(capital_usd, USDT),
            Money(btc_allocation, BTC),
        ],
        base_currency=None,
        book_type=BookType.L2_MBP,
        latency_model=latency_model,
        fill_model=fill_model,
    )
    
    if latency_model:
        print(f"  ✓ Latency model: CoLocation (250μs base)")
    if fill_model:
        print(f"  ✓ Fill model: Institutional (85% queue, 30% liquidity)")
    
    engine.add_instrument(instrument)
    print(f"✓ Instrument registered")
    
    return engine, instrument


def load_orderbook_data(
    engine: BacktestEngine,
    instrument: CurrencyPair,
    date_str: str = "2026-01-15",
    max_updates: int = None,
) -> int:
    """Load order book data."""
    print("\n" + "=" * 70)
    print("LOADING ORDER BOOK DATA")
    print("=" * 70)
    
    ob_file = Path(f"data/ob_data/BTCUSDT_Spot/{date_str}_BTCUSDT_ob200.data")
    
    if not ob_file.exists():
        print(f"✗ Order book file not found: {ob_file}")
        return 0
    
    file_size_mb = ob_file.stat().st_size / 1e6
    print(f"✓ Order book file: {ob_file}")
    print(f"  Size: {file_size_mb:.1f} MB")
    print(f"  Max Updates: {max_updates if max_updates else 'All'}")
    
    print(f"\nLoading deltas...", end="", flush=True)
    start_time = time.time()
    
    try:
        delta_count = 0
        for delta in BybitOrderBookLoader.load_file(ob_file, instrument):
            engine.add_data([delta])
            delta_count += 1
            
            if max_updates and delta_count >= max_updates:
                break
            
            if delta_count % 10000 == 0:
                elapsed = time.time() - start_time
                rate = delta_count / elapsed
                print(f"\r✓ Loaded {delta_count:,} deltas ({rate:,.0f}/sec)", end="", flush=True)
        
        elapsed = time.time() - start_time
        rate = delta_count / elapsed if elapsed > 0 else 0
        print(f"\r✓ Loaded {delta_count:,} deltas ({rate:,.0f}/sec) in {elapsed:.1f}s")
        
        return delta_count
        
    except Exception as e:
        print(f"\n✗ Error loading data: {e}")
        import traceback
        traceback.print_exc()
        return 0


def setup_strategy(
    engine: BacktestEngine,
    instrument: CurrencyPair,
) -> LowCapitalOBIMarketMaker:
    """Create and add specialized low-capital strategy."""
    print("\n" + "=" * 70)
    print("SETTING UP SPECIALIZED LOW-CAPITAL STRATEGY")
    print("=" * 70)
    
    config = LowCapitalMMConfig(
        instrument_id=str(instrument.id),
        base_qty=Decimal("0.0001"),
        max_position_qty=Decimal("0.0005"),
        obi_levels=5,
        obi_ema_period=15,
        obi_entry_threshold=0.15,
        min_spread_bps=1,
        max_spread_bps=5,
        max_notional_usd=500.0,
        emergency_liquidation_loss_usd=-100.0,
        quote_refresh_interval_ms=50,
        max_inventory_age_seconds=120.0,
    )
    
    print(f"✓ Strategy Config: LowCapitalOBIMarketMaker (SPECIALIZED)")
    print(f"  Instrument: {config.instrument_id}")
    print(f"  Base Qty: {config.base_qty} BTC")
    print(f"  Max Position: {config.max_position_qty} BTC")
    print(f"  OBI Levels: {config.obi_levels} (vs 10 institutional)")
    print(f"  OBI EMA Period: {config.obi_ema_period} (vs 20 institutional)")
    print(f"  OBI Threshold: {config.obi_entry_threshold:.0%} (vs 20% institutional)")
    print(f"  Min Spread: {config.min_spread_bps} bps (vs 2 bps institutional)")
    print(f"  Max Spread: {config.max_spread_bps} bps (vs 10 bps institutional)")
    print(f"  Quote Refresh: {config.quote_refresh_interval_ms}ms (vs 100ms institutional)")
    print(f"  Max Position Age: {config.max_inventory_age_seconds}s (vs 300s institutional)")
    print(f"  Emergency Stop: ${config.emergency_liquidation_loss_usd} (20% of capital)")
    
    strategy = LowCapitalOBIMarketMaker(config)
    engine.add_strategy(strategy)
    print(f"✓ Strategy added: {strategy.id}")
    
    return strategy


def run_backtest(engine: BacktestEngine) -> None:
    """Run the backtest."""
    print("\n" + "=" * 70)
    print("RUNNING BACKTEST")
    print("=" * 70)
    
    start_time = time.time()
    print(f"Start time: {datetime.now().isoformat()}")
    print(f"\nProcessing events...", end="", flush=True)
    
    try:
        engine.run()
        elapsed = time.time() - start_time
        print(f"\r✓ Backtest completed in {elapsed:.1f} seconds")
        
    except Exception as e:
        print(f"\n✗ Backtest error: {e}")
        import traceback
        traceback.print_exc()
        return


def generate_reports(engine: BacktestEngine, output_dir: Path = None) -> None:
    """Generate performance reports."""
    print("\n" + "=" * 70)
    print("GENERATING REPORTS")
    print("=" * 70)
    
    if output_dir is None:
        output_dir = Path("outputs/backtests/backtest_results_specialized_lowcap")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"✓ Output directory: {output_dir}")
    
    # Account report
    print(f"\nGenerating account report...", end="", flush=True)
    try:
        with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
            account_report = engine.trader.generate_account_report(BYBIT_VENUE)
            print(f"\r✓ Account Report:")
            print(account_report)
            account_path = output_dir / "account_report.csv"
            account_report.to_csv(account_path)
            print(f"  Saved: {account_path}")
    except Exception as e:
        print(f"\n✗ Error: {e}")
    
    # Fills report
    print(f"\nGenerating fills report...", end="", flush=True)
    try:
        with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
            fills_report = engine.trader.generate_order_fills_report()
            print(f"\r✓ Fills Report ({len(fills_report)} fills):")
            if len(fills_report) > 0:
                print(fills_report.head(10))
            fills_path = output_dir / "fills_report.csv"
            fills_report.to_csv(fills_path)
            print(f"  Saved: {fills_path}")
    except Exception as e:
        print(f"\n✗ Error: {e}")
    
    # Positions report
    print(f"\nGenerating positions report...", end="", flush=True)
    try:
        with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
            positions_report = engine.trader.generate_positions_report()
            print(f"\r✓ Positions Report:")
            print(positions_report)
            positions_path = output_dir / "positions_report.csv"
            positions_report.to_csv(positions_path)
            print(f"  Saved: {positions_path}")
    except Exception as e:
        print(f"\n✗ Error: {e}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Specialized low-capital strategy backtest"
    )
    parser.add_argument(
        "--max-updates",
        type=int,
        default=50000,
        help="Maximum order book updates to process",
    )
    parser.add_argument(
        "--date",
        type=str,
        default="2026-01-15",
        help="Backtest date in format YYYY-MM-DD",
    )
    
    args = parser.parse_args()
    
    print("\n" + "╔" + "=" * 68 + "╗")
    print("║" + " SPECIALIZED LOW-CAPITAL STRATEGY BACKTEST ".center(68) + "║")
    print("║" + " Optimized for $500 Micro-Capital Accounts ".center(68) + "║")
    print("╚" + "=" * 68 + "╝")
    
    # Setup models
    latency_model, fill_model = create_institutional_backtest_models()
    
    # Setup engine
    engine, instrument = setup_backtest_engine(
        capital_usd=500.0,
        latency_model=latency_model,
        fill_model=fill_model,
    )
    
    # Load data
    updates_loaded = load_orderbook_data(
        engine,
        instrument,
        args.date,
        args.max_updates,
    )
    
    # Setup strategy
    strategy = setup_strategy(engine, instrument)
    
    # Run backtest
    run_backtest(engine)
    
    # Generate reports
    output_dir = Path("outputs/backtests/backtest_results_specialized_lowcap")
    generate_reports(engine, output_dir)
    
    print("\n" + "=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)
    print(f"✓ Specialized low-capital strategy backtest complete")
    print(f"  Order Book Updates: {updates_loaded:,}")
    print(f"  Results: {output_dir}/")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
