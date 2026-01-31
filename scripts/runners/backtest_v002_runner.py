#!/usr/bin/env python3
"""Run v002 multi-pair backtest."""

import sys
from pathlib import Path

import pandas as pd

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import BTC, ETH, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# Import v002 strategy
sys.path.insert(0, str(Path(__file__).parent))
from strategy.hft_obi_bybit_spot_mm_lowcap_multipair_v002 import (
    MultiPairLowCapitalOBIMarketMaker,
    MultiPairMMConfig,
)


def main():
    """Run backtest for v002 multi-pair strategy."""
    
    # Configuration
    TRADER_ID = TraderId("V002-BACKTESTER-001")
    
    print("=" * 80)
    print("V002 MULTI-PAIR MARKET MAKER BACKTEST")
    print("=" * 80)
    print("Instruments: BTCUSDT + ETHUSDT")
    print("Venue: BYBIT SPOT")
    print("Data: Order Book L2")
    print("=" * 80)
    
    # Configure backtest engine
    engine_config = BacktestEngineConfig(
        trader_id=TRADER_ID,
    )
    
    # Build backtest engine
    engine = BacktestEngine(config=engine_config)
    
    # Add BYBIT venue
    print("\nAdding BYBIT venue...")
    bybit_venue = Venue(BYBIT)
    engine.add_venue(
        venue=bybit_venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # Multi-currency
        starting_balances=[
            Money(500.0, USDT),   # Starting capital
            Money(0.0, BTC),
            Money(0.0, ETH),
        ],
        book_type=BookType.L2_MBP,  # Level 2 order book
    )
    
    # Add instruments
    print("Adding instruments...")
    BTCUSDT = TestInstrumentProvider.btcusdt_binance()  # Use as proxy
    ETHUSDT = TestInstrumentProvider.ethusdt_binance()  # Use as proxy
    
    engine.add_instrument(BTCUSDT)
    engine.add_instrument(ETHUSDT)
    
    # Configure strategy
    print("Configuring v002 strategy...")
    strategy_config = MultiPairMMConfig(
        instruments=[
            {
                'instrument_id': 'BTCUSDT-SPOT.BYBIT',
                'base_qty': '0.0001',
                'max_position_qty': '0.0003',
                'weight': 0.6,
                'obi_sensitivity': 1.0,
                'min_spread_bps': 1,
                'max_spread_bps': 5,
            },
            {
                'instrument_id': 'ETHUSDT-SPOT.BYBIT',
                'base_qty': '0.005',
                'max_position_qty': '0.015',
                'weight': 0.4,
                'obi_sensitivity': 1.2,
                'min_spread_bps': 1,
                'max_spread_bps': 6,
            },
        ],
        total_capital_usd=500.0,
        max_total_exposure_pct=0.15,
        emergency_liquidation_loss_usd=-100.0,
        obi_levels=5,
        obi_ema_period=15,
        obi_entry_threshold=0.15,
        risk_aversion=0.5,
        inventory_half_life_seconds=30.0,
    )
    
    # Create strategy instance
    strategy = MultiPairLowCapitalOBIMarketMaker(config=strategy_config)
    engine.add_strategy(strategy)
    
    # Note: Would need to add actual order book data here
    # For now, just validate setup
    print("\n✓ Backtest engine configured successfully!")
    print(f"  - Strategy: {strategy.id}")
    print(f"  - Instruments: 2 (BTC, ETH)")
    print(f"  - Starting capital: $500 USDT")
    print(f"  - Risk aversion: {strategy_config.risk_aversion}")
    print(f"  - OBI threshold: {strategy_config.obi_entry_threshold}")
    
    # NOTE: To run full backtest, need to add order book data using:
    # engine.add_data(...) for both BTCUSDT and ETHUSDT order books
    
    print("\n" + "=" * 80)
    print("Backtest setup complete. To run full backtest:")
    print("1. Load order book data for BTCUSDT and ETHUSDT")
    print("2. Call engine.run()")
    print("3. Analyze results with engine.trader.generate_account_report()")
    print("=" * 80)
    
    return engine, strategy


if __name__ == "__main__":
    engine, strategy = main()
    print("\nBacktest engine ready. Data loading required for execution.")
