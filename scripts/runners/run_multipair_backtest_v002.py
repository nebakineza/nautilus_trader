#!/usr/bin/env python3
"""
Backtest runner for Multi-Pair Low-Capital OBI Market Maker v002.

Tests dual-instrument (BTC + ETH) operation with simulated order book data.
"""

from decimal import Decimal
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel, LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.identifiers import Venue

# Import the v002 strategy
import sys
sys.path.insert(0, '/home/seb/nebakineza/nautilus_trader')
from strategy.hft_obi_bybit_spot_mm_lowcap_multipair_v002 import (
    MultiPairLowCapitalOBIMarketMaker,
    MultiPairMMConfig,
)


def run_multipair_backtest():
    """Run backtest for multi-pair strategy."""
    
    print("\n" + "="*80)
    print("MULTI-PAIR OBI MARKET MAKER v002 - BACKTEST")
    print("="*80 + "\n")
    
    # Configure backtest engine
    engine_config = BacktestEngineConfig(
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
        ),
    )
    
    # Create engine
    engine = BacktestEngine(config=engine_config)
    
    # Add BYBIT venue
    venue = Venue("BYBIT")
    
    # Configure strategy
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
        emergency_liquidation_loss_usd=-100.0,
        obi_entry_threshold=0.15,
        quote_refresh_interval_ms=50,
    )
    
    # Create strategy
    strategy = MultiPairLowCapitalOBIMarketMaker(config=strategy_config)
    
    # Add strategy to engine
    engine.add_strategy(strategy)
    
    # Add venue with latency and fill models
    engine.add_venue(
        venue=venue,
        oms_type="NETTING",
        account_type="MARGIN",
        base_currency=None,
        starting_balances=["500 USDT"],
        fill_model=FillModel(
            prob_fill_on_limit=0.4,  # 40% fill probability
            prob_fill_on_stop=1.0,
            prob_slippage=0.1,
            random_seed=42,
        ),
        latency_model=LatencyModel(
            base_latency_nanos=10_000_000,  # 10ms base latency
            insert_latency_nanos=2_000_000,   # 2ms insert
            update_latency_nanos=1_000_000,   # 1ms update
            cancel_latency_nanos=1_000_000,   # 1ms cancel
        ),
    )
    
    print("✓ Engine configured")
    print(f"  Venue: {venue}")
    print(f"  Starting capital: $500 USDT")
    print(f"  Instruments: BTC-USDT + ETH-USDT")
    print(f"  Fill probability: 40%")
    print(f"  Latency: 10-15ms")
    print("\n" + "-"*80)
    print("NOTE: This backtest requires order book data.")
    print("      Load BTC and ETH order book data before running.")
    print("      See: examples/backtest/bybit_orderbook_loader.py")
    print("-"*80 + "\n")
    
    # TODO: Load order book data for both BTC and ETH
    # For now, this is a skeleton - data loading comes next
    
    print("\n[Phase 1 Implementation Complete]")
    print("Next steps:")
    print("  1. Create order book data loader for dual instruments")
    print("  2. Run full backtest with historical data")
    print("  3. Validate capital allocation across both pairs")
    print("  4. Proceed to Phase 2: Inventory Management")
    

if __name__ == "__main__":
    run_multipair_backtest()
