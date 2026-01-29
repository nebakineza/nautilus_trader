#!/usr/bin/env python3
"""Professional HFT backtest runner for Bybit BTCUSDT-SPOT.

This runner executes institutional-grade order book imbalance market making
backtests with full latency modeling, realistic fill simulation, and comprehensive
performance analysis.

Usage:
    python run_institutional_hft_backtest.py [--test] [--date 2026-01-15] [--max-updates 10000]
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
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# Import custom components
from examples.professional_hft_mm import InstitutionalOBIMarketMaker, InstitutionalMMConfig
from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from examples.backtest.institutional_models import create_institutional_backtest_models


def create_btcusdt_bybit_instrument() -> CurrencyPair:
    """Create BTCUSDT-SPOT.BYBIT instrument with correct parameters."""
    return CurrencyPair(
        instrument_id=InstrumentId.from_str("BTCUSDT-SPOT.BYBIT"),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=1,      # Bybit quotes to 1 decimal
        size_precision=6,       # Bybit quotes to 6 decimals
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
        maker_fee=Decimal("-0.0001"),  # -0.01% rebate (VIP 0)
        taker_fee=Decimal("0.0006"),   # 0.06% fee (VIP 0)
        ts_event=0,
        ts_init=0,
    )


def setup_backtest_engine(
    max_notional: float = 100_000.0,
    latency_model=None,
    fill_model=None,
) -> tuple[BacktestEngine, CurrencyPair]:
    """
    Configure and create backtest engine.
    
    Parameters
    ----------
    max_notional : float
        Maximum notional exposure in USD

    
    Returns
    -------
    tuple[BacktestEngine, CurrencyPair]
        Configured engine and instrument
    """
    print("\n" + "=" * 70)
    print("SETTING UP BACKTEST ENGINE")
    print("=" * 70)
    
    # Configure engine
    config = BacktestEngineConfig(
        trader_id=TraderId("HFT-INSTITUTIONAL-001"),
    )
    
    engine = BacktestEngine(config=config)
    print(f"✓ BacktestEngine created: {config.trader_id}")
    
    # Create instrument
    instrument = create_btcusdt_bybit_instrument()
    print(f"✓ Instrument created: {instrument.id}")
    print(f"  Price Precision: {instrument.price_precision}")
    print(f"  Size Precision: {instrument.size_precision}")
    print(f"  Maker Fee: {instrument.maker_fee}")
    print(f"  Taker Fee: {instrument.taker_fee}")
    
    # Add venue with professional configuration
    print(f"\n✓ Adding venue: {BYBIT_VENUE}")
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[
            Money(50_000, USDT),  # Professional MM capital
            Money(0.5, BTC),      # Initial hedge inventory
        ],
        base_currency=None,  # Multi-currency account
        book_type=BookType.L2_MBP,  # Full order book depth
        latency_model=latency_model,  # AWS Singapore co-location
        fill_model=fill_model,        # Institutional fill simulation
    )
    
    if latency_model:
        print(f"  ✓ Latency model: CoLocation (250μs base + jitter)")
    if fill_model:
        print(f"  ✓ Fill model: Institutional (85% queue, 30% liquidity)")
    
    # Add instrument
    engine.add_instrument(instrument)
    print(f"✓ Instrument registered with engine")
    
    return engine, instrument


def load_orderbook_data(
    engine: BacktestEngine,
    instrument: CurrencyPair,
    date_str: str = "2026-01-15",
    max_updates: int = None,
) -> int:
    """
    Load order book data from Bybit JSON files.
    
    Parameters
    ----------
    engine : BacktestEngine
        Backtest engine
    instrument : CurrencyPair
        Target instrument
    date_str : str
        Date in format YYYY-MM-DD
    max_updates : int, optional
        Maximum number of updates to load
    
    Returns
    -------
    int
        Number of updates loaded
    """
    print("\n" + "=" * 70)
    print("LOADING ORDER BOOK DATA")
    print("=" * 70)
    
    ob_file = Path(f"ob_data/BTCUSDT_Spot/{date_str}_BTCUSDT_ob200.data")
    
    if not ob_file.exists():
        print(f"✗ Order book file not found: {ob_file}")
        return 0
    
    file_size_mb = ob_file.stat().st_size / 1e6
    print(f"✓ Order book file: {ob_file}")
    print(f"  Size: {file_size_mb:.1f} MB")
    print(f"  Max Updates: {max_updates if max_updates else 'All'}")
    
    # Load deltas
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
    config_overrides: dict = None,
) -> InstitutionalOBIMarketMaker:
    """
    Create and add strategy to engine.
    
    Parameters
    ----------
    engine : BacktestEngine
        Backtest engine
    instrument : CurrencyPair
        Target instrument
    config_overrides : dict, optional
        Configuration overrides
    
    Returns
    -------
    InstitutionalOBIMarketMaker
        Configured strategy
    """
    print("\n" + "=" * 70)
    print("SETTING UP STRATEGY")
    print("=" * 70)
    
    # Create config with overrides
    config_dict = {
        "instrument_id": str(instrument.id),
        "base_qty": Decimal("0.01"),
        "max_position_qty": Decimal("0.5"),
        "obi_levels": 10,
        "obi_ema_period": 20,
        "obi_entry_threshold": 0.20,
        "min_spread_bps": 2,
        "max_spread_bps": 10,
    }
    
    if config_overrides:
        config_dict.update(config_overrides)
    
    config = InstitutionalMMConfig(**config_dict)
    
    print(f"✓ Strategy Config: InstitutionalOBIMarketMaker")
    print(f"  Instrument: {config.instrument_id}")
    print(f"  Base Qty: {config.base_qty} BTC")
    print(f"  Max Position: {config.max_position_qty} BTC")
    print(f"  OBI Levels: {config.obi_levels}")
    print(f"  OBI Entry Threshold: {config.obi_entry_threshold:.2%}")
    print(f"  Min/Max Spread: {config.min_spread_bps}/{config.max_spread_bps} bps")
    
    # Create and add strategy
    strategy = InstitutionalOBIMarketMaker(config)
    engine.add_strategy(strategy)
    
    print(f"✓ Strategy added to engine: {strategy.__class__.__name__}")
    
    return strategy


def run_backtest(engine: BacktestEngine) -> None:
    """
    Execute backtest.
    
    Parameters
    ----------
    engine : BacktestEngine
        Configured backtest engine
    """
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
    """
    Generate performance reports.
    
    Parameters
    ----------
    engine : BacktestEngine
        Completed backtest engine
    output_dir : Path, optional
        Directory for report files
    """
    print("\n" + "=" * 70)
    print("GENERATING REPORTS")
    print("=" * 70)
    
    if output_dir is None:
        output_dir = Path("backtest_results")
    
    output_dir.mkdir(exist_ok=True)
    print(f"✓ Output directory: {output_dir}")
    
    # Generate account report
    print(f"\nGenerating account report...", end="", flush=True)
    try:
        with pd.option_context(
            "display.max_rows", 100,
            "display.max_columns", None,
            "display.width", 300,
        ):
            account_report = engine.trader.generate_account_report(BYBIT_VENUE)
            print(f"\r✓ Account Report:")
            print(account_report)
            
            # Save to CSV
            account_path = output_dir / "account_report.csv"
            account_report.to_csv(account_path)
            print(f"  Saved: {account_path}")
    except Exception as e:
        print(f"\n✗ Error generating account report: {e}")
    
    # Generate order fills report
    print(f"\nGenerating fills report...", end="", flush=True)
    try:
        with pd.option_context(
            "display.max_rows", 100,
            "display.max_columns", None,
            "display.width", 300,
        ):
            fills_report = engine.trader.generate_order_fills_report()
            print(f"\r✓ Fills Report ({len(fills_report)} fills):")
            if len(fills_report) > 0:
                print(fills_report.head(10))
            
            # Save to CSV
            fills_path = output_dir / "fills_report.csv"
            fills_report.to_csv(fills_path)
            print(f"  Saved: {fills_path}")
    except Exception as e:
        print(f"\n✗ Error generating fills report: {e}")
    
    # Generate positions report
    print(f"\nGenerating positions report...", end="", flush=True)
    try:
        with pd.option_context(
            "display.max_rows", 100,
            "display.max_columns", None,
            "display.width", 300,
        ):
            positions_report = engine.trader.generate_positions_report()
            print(f"\r✓ Positions Report:")
            print(positions_report)
            
            # Save to CSV
            positions_path = output_dir / "positions_report.csv"
            positions_report.to_csv(positions_path)
            print(f"  Saved: {positions_path}")
    except Exception as e:
        print(f"\n✗ Error generating positions report: {e}")


def main():
    """Main backtest runner."""
    parser = argparse.ArgumentParser(
        description="Institutional HFT backtest runner",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run in test mode (limited data)",
    )
    parser.add_argument(
        "--date",
        default="2026-01-15",
        help="Date in format YYYY-MM-DD (default: 2026-01-15)",
    )
    parser.add_argument(
        "--max-updates",
        type=int,
        default=None,
        help="Maximum number of order book updates to process",
    )
    
    args = parser.parse_args()
    
    # Override for test mode
    if args.test:
        args.max_updates = args.max_updates or 10000
        print("⚠ Running in TEST mode - limited data")
    
    print("\n" + "=" * 70)
    print("INSTITUTIONAL HFT BACKTEST RUNNER")
    print("=" * 70)
    print(f"Date: {args.date}")
    print(f"Max Updates: {args.max_updates if args.max_updates else 'All'}")
    print(f"Mode: {'TEST' if args.test else 'FULL'}")
    
    try:
        # Create models
        latency_model, fill_model = create_institutional_backtest_models(random_seed=42)
        print(f"\n✓ Models created:")
        print(f"  Latency: CoLocation (250μs + jitter)")
        print(f"  Fill: Institutional (85% queue, 30% liquidity)")
        
        # Setup engine
        engine, instrument = setup_backtest_engine(
            latency_model=latency_model,
            fill_model=fill_model,
        )
        
        # Load data
        delta_count = load_orderbook_data(
            engine,
            instrument,
            date_str=args.date,
            max_updates=args.max_updates,
        )
        
        if delta_count == 0:
            print("\n✗ No data loaded, cannot proceed")
            return 1
        
        # Setup strategy
        strategy = setup_strategy(engine, instrument)
        
        # Run backtest
        run_backtest(engine)
        
        # Generate reports
        generate_reports(engine)
        
        # Summary
        print("\n" + "=" * 70)
        print("BACKTEST COMPLETE")
        print("=" * 70)
        print(f"✓ Backtest executed successfully")
        print(f"  Order Book Updates: {delta_count:,}")
        print(f"  Results saved to: backtest_results/")
        
        return 0
        
    except Exception as e:
        print(f"\n✗ Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
