#!/usr/bin/env python3
"""Run Professional HFT backtest with custom data."""

from pathlib import Path
import pandas as pd

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, InstrumentId, Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from examples.professional_hft import InstitutionalHFT, ProfessionalHFTConfig
from examples.backtest.custom_trade_loader import CustomTradeLoader


def main():
    # Configuration
    TRADER_ID = TraderId("HFT-BACKTESTER-001")
    INSTRUMENT_ID = "BTCUSDT.BINANCE"  # Use Binance to match test provider
    TICK_DATA_DIR = Path("data/tick_data/BTCUSDT_Spot")
    
    MAX_TRADE_ROWS = 100000  # Limit for faster testing
    
    print("=" * 80)
    print("Professional HFT Backtest Runner")
    print("=" * 80)
    print(f"Instrument: {INSTRUMENT_ID}")
    print(f"Trade data dir: {TICK_DATA_DIR}")
    print(f"Max trade rows: {MAX_TRADE_ROWS}")
    print("=" * 80)
    
    # Configure backtest engine
    config = BacktestEngineConfig(
        trader_id=TRADER_ID,
    )
    
    # Build backtest engine
    engine = BacktestEngine(config=config)
    
    # Add BINANCE venue
    print("\nAdding BINANCE venue...")
    from nautilus_trader.adapters.binance import BINANCE
    binance_venue = Venue(BINANCE)
    engine.add_venue(
        venue=binance_venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # Multi-currency account
        starting_balances=[Money(10000.0, USDT), Money(0.1, BTC)],
        book_type=BookType.L1_MBP,  # L1 for trade-only data
    )
    
    # Add instrument - use test provider's BTCUSDT
    print("Adding BTCUSDT instrument...")
    BTCUSDT = TestInstrumentProvider.btcusdt_binance()
    engine.add_instrument(BTCUSDT)
    
    # Load and add trade data
    print("\nLoading trade data...")
    trade_files = sorted(TICK_DATA_DIR.glob("*.csv.gz"))
    
    if not trade_files:
        print(f"ERROR: No trade files found in {TICK_DATA_DIR}")
        return
    else:
        for trade_file in trade_files[:1]:  # Load first day for testing
            print(f"  Loading: {trade_file.name}")
            
            # Load using custom loader
            df_trades = CustomTradeLoader.load(trade_file, INSTRUMENT_ID)
            
            # Limit rows if specified
            if MAX_TRADE_ROWS is not None and len(df_trades) > MAX_TRADE_ROWS:
                df_trades = df_trades.head(MAX_TRADE_ROWS)
                print(f"    Limited to {MAX_TRADE_ROWS} rows")
            
            print(f"    {len(df_trades)} trades loaded")
            print(f"    Time range: {df_trades.index.min()} to {df_trades.index.max()}")
            
            # Create wrangler and process
            wrangler = TradeTickDataWrangler(instrument=BTCUSDT)
            trades = wrangler.process(df_trades)
            engine.add_data(trades)
    
    # Add HFT strategy - use the actual instrument ID from the test provider
    print("\nAdding InstitutionalHFT strategy...")
    STRATEGY_CONFIG = ProfessionalHFTConfig(
        instrument_id=str(BTCUSDT.id),  # Use actual instrument ID
        base_qty=0.001000,  # Use 6 decimal places to match instrument precision
        num_layers=5,
        max_notional_exposure=5000.0,
        inventory_alpha=0.1,
        toxic_flow_threshold=2.5,
        update_interval_ms=20,
    )
    print(f"  Config: instrument_id={STRATEGY_CONFIG.instrument_id}, base_qty={STRATEGY_CONFIG.base_qty}, "
          f"num_layers={STRATEGY_CONFIG.num_layers}, "
          f"max_notional={STRATEGY_CONFIG.max_notional_exposure}")
    
    strategy = InstitutionalHFT(config=STRATEGY_CONFIG)
    engine.add_strategy(strategy=strategy)
    
    # Run backtest
    print("\n" + "=" * 80)
    print("Running backtest...")
    print("=" * 80)
    engine.run()
    
    # Generate and display reports
    print("\n" + "=" * 80)
    print("BACKTEST RESULTS")
    print("=" * 80)
    
    with pd.option_context(
        "display.max_rows", 100,
        "display.max_columns", None,
        "display.width", 300,
    ):
        print("\n--- Account Report ---")
        account_report = engine.trader.generate_account_report(binance_venue)
        print(account_report)
        
        print("\n--- Order Fills Report ---")
        fills_report = engine.trader.generate_order_fills_report()
        print(fills_report)
        
        print("\n--- Positions Report ---")
        positions_report = engine.trader.generate_positions_report()
        print(positions_report)
    
    # Save results to CSV
    results_dir = Path("outputs/backtests/backtest_results")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    account_df = engine.trader.generate_account_report(binance_venue)
    account_path = results_dir.joinpath("account_report.csv")
    account_df.to_csv(account_path)
    print(f"\nAccount report saved to {account_path}")
    
    fills_df = engine.trader.generate_order_fills_report()
    fills_path = results_dir.joinpath("fills_report.csv")
    fills_df.to_csv(fills_path)
    print(f"Fills report saved to {fills_path}")
    
    positions_df = engine.trader.generate_positions_report()
    positions_path = results_dir.joinpath("positions_report.csv")
    positions_df.to_csv(positions_path)
    print(f"Positions report saved to {positions_path}")
    
    # Cleanup
    engine.reset()
    engine.dispose()
    
    print("\n" + "=" * 80)
    print("Backtest completed!")
    print("=" * 80)


if __name__ == "__main__":
    main()
