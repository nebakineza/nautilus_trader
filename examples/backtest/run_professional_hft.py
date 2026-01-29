#!/usr/bin/env python3
"""Backtest runner for Professional HFT strategy with custom data."""

import pandas as pd
from pathlib import Path

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from examples.professional_hft import InstitutionalHFT, ProfessionalHFTConfig
from examples.backtest.custom_trade_loader import CustomTradeLoader


def main():
    TRADER_ID = TraderId("HFT-BACKTESTER-001")
    INSTRUMENT_ID = "BTCUSDT-SPOT.BYBIT"
    TICK_DATA_DIR = Path("tick_data/BTCUSDT_Spot")
    OB_DATA_DIR = Path("ob_data/BTCUSDT_Spot")
    
    MAX_TRADE_ROWS = 100000
    
    STRATEGY_CONFIG = ProfessionalHFTConfig(
        instrument_id=INSTRUMENT_ID,
        base_qty=0.01,
        num_layers=5,
        max_notional_exposure=5000.0,
        inventory_alpha=0.1,
        toxic_flow_threshold=2.5,
        update_interval_ms=20,
    )
    
    print("=" * 80)
    print("Professional HFT Backtest Runner")
    print("=" * 80)
    print(f"Instrument: {INSTRUMENT_ID}")
    print(f"Trade data dir: {TICK_DATA_DIR}")
    print(f"OB data dir: {OB_DATA_DIR}")
    print("=" * 80)
    
    config = BacktestEngineConfig(
        trader_id=TRADER_ID,
        ref_currency=USDT,
        initial_balance=[Money(10000, USDT)],
    )
    
    engine = BacktestEngine(config=config)
    
    print("\nAdding BYBIT venue...")
    engine.add_venue(
        venue=BYBIT,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(10000, USDT), Money(0.1, BTC)],
        book_type=BookType.L2_MBP,
        oms_latency_ns=5000000,
    )
    
    print("Adding BTCUSDT instrument...")
    BTCUSDT = TestInstrumentProvider.btcusdt_binance()
    BTCUSDT.id = InstrumentId.from_str(INSTRUMENT_ID)
    engine.add_instrument(BTCUSDT)
    
    print("\nLoading trade data...")
    trade_files = sorted(TICK_DATA_DIR.glob("*.csv.gz"))
    
    if not trade_files:
        print(f"Warning: No trade files found in {TICK_DATA_DIR}")
    else:
        for trade_file in trade_files[:1]:
            print(f"  Loading: {trade_file.name}")
            
            df_trades = CustomTradeLoader.load(trade_file, INSTRUMENT_ID)
            
            if MAX_TRADE_ROWS is not None and len(df_trades) > MAX_TRADE_ROWS:
                df_trades = df_trades.head(MAX_TRADE_ROWS)
                print(f"    Limited to {MAX_TRADE_ROWS} rows")
            
            print(f"    {len(df_trades)} trades loaded")
            
            wrangler = TradeTickDataWrangler(instrument=BTCUSDT)
            trades = wrangler.process(df_trades)
            engine.add_data(trades)
    
    print("\nAdding InstitutionalHFT strategy...")
    strategy = InstitutionalHFT(config=STRATEGY_CONFIG)
    engine.add_strategy(strategy=strategy)
    
    print("\n" + "=" * 80)
    print("Running backtest...")
    print("=" * 80)
    engine.run()
    
    print("\n" + "=" * 80)
    print("BACKTEST RESULTS")
    print("=" * 80)
    
    with pd.option_context(
        "display.max_rows", 100,
        "display.max_columns", None,
        "display.width", 300,
    ):
        print("\n--- Account Report ---")
        print(engine.trader.generate_account_report(BYBIT))
        
        print("\n--- Order Fills Report ---")
        print(engine.trader.generate_order_fills_report())
        
        print("\n--- Positions Report ---")
        print(engine.trader.generate_positions_report())
    
    results_dir = Path("backtest_results")
    results_dir.mkdir(exist_ok=True)
    
    account_df = engine.trader.generate_account_report(BYBIT)
    account_df.to_csv(results_dir / "account_report.csv")
    print(f"\nAccount report saved to {results_dir / 'account_report.csv'}")
    
    fills_df = engine.trader.generate_order_fills_report()
    fills_df.to_csv(results_dir / "fills_report.csv")
    print(f"Fills report saved to {results_dir / 'fills_report.csv'}")
    
    positions_df = engine.trader.generate_positions_report()
    positions_df.to_csv(results_dir / "positions_report.csv")
    print(f"Positions report saved to {results_dir / 'positions_report.csv'}")
    
    engine.reset()
    engine.dispose()
    
    print("\n" + "=" * 80)
    print("Backtest completed!")
    print("=" * 80)


if __name__ == "__main__":
    main()
