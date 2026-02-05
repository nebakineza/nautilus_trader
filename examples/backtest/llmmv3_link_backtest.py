#!/usr/bin/env python3
"""Quick backtest for LINKUSDT using LLMMv3Primer strategy."""
import sys
from decimal import Decimal
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT, LINK
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb
from nautilus_trader.backtest.models import CompetitionAwareFillModel, LatencyModel
from strategy.lead_lag_bybit_binance_mm_v003_primer import LeadLagMMv3Primer, LeadLagMMv3PrimerConfig

# LINK config
LINK_CONFIG = {
    "symbol": "LINKUSDT",
    "order_qty": Decimal("20.0"),
    "min_order_qty": Decimal("10.0"),
    "max_position_qty": Decimal("200.0"),
    "guard_threshold_bps": Decimal("15.0"),
    "spread_bps": Decimal("20.0"),
    "min_profit_bps": Decimal("1.5"),
    "ofi_max_bps": Decimal("3.0"),
    "internal_price_delta_limit": Decimal("0.0"),
}

def main():
    # Create instruments
    link_binance = CurrencyPair(
        instrument_id=InstrumentId.from_str("LINKUSDT.BINANCE"),
        raw_symbol=Symbol("LINKUSDT"),
        base_currency=LINK,
        quote_currency=USDT,
        price_precision=4,
        size_precision=2,
        price_increment=Price.from_str("0.0001"),
        size_increment=Quantity.from_str("0.01"),
        lot_size=Quantity.from_str("0.01"),
        max_quantity=Quantity.from_str("1000000"),
        min_quantity=Quantity.from_str("0.01"),
        max_price=Price.from_str("100000"),
        min_price=Price.from_str("0.0001"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )
    
    link_bybit = CurrencyPair(
        instrument_id=InstrumentId.from_str("LINKUSDT-SPOT.BYBIT"),
        raw_symbol=Symbol("LINKUSDT"),
        base_currency=LINK,
        quote_currency=USDT,
        price_precision=4,
        size_precision=2,
        price_increment=Price.from_str("0.0001"),
        size_increment=Quantity.from_str("0.01"),
        lot_size=Quantity.from_str("0.01"),
        max_quantity=Quantity.from_str("1000000"),
        min_quantity=Quantity.from_str("0.01"),
        max_price=Price.from_str("100000"),
        min_price=Price.from_str("0.0001"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.000675"),  # VIP1
        taker_fee=Decimal("0.0008"),
        ts_event=0,
        ts_init=0,
    )
    
    # Load data from QuestDB
    print("Loading Binance LINKUSDT data...")
    qdb_cfg = QuestDbConfig()
    leader_deltas = list(load_questdb(qdb_cfg, link_binance, "BINANCE", "LINKUSDT", "2026-02-02"))
    print(f"Loaded {len(leader_deltas)} leader delta batches")
    
    print("Loading Bybit LINKUSDT data...")
    follower_deltas = list(load_questdb(qdb_cfg, link_bybit, "BYBIT", "LINKUSDT", "2026-02-02"))
    print(f"Loaded {len(follower_deltas)} follower delta batches")
    
    if not leader_deltas or not follower_deltas:
        print("ERROR: No data loaded!")
        return
    
    # Create backtest engine
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("LINK-BACKTEST-001"),
            logging=LoggingConfig(log_level="INFO"),
        )
    )
    
    # Add venues (multi-currency CASH accounts with base_currency=None)
    engine.add_venue(
        venue=Venue("BINANCE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(1, USDT)],
    )
    
    engine.add_venue(
        venue=Venue("BYBIT"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(5000, USDT)],
        fill_model=CompetitionAwareFillModel(),
        latency_model=LatencyModel(10),  # 10ms latency
    )
    
    # Add instruments
    engine.add_instrument(link_binance)
    engine.add_instrument(link_bybit)
    
    # Add orderbook data
    print("Adding orderbook data to engine...")
    all_data = []
    all_data.extend(leader_deltas)
    all_data.extend(follower_deltas)
    
    # Sort by timestamp
    all_data.sort(key=lambda d: d.ts_init)
    
    engine.add_data(all_data)
    
    # Create strategy
    strategy_config = LeadLagMMv3PrimerConfig(
        leader_instrument_id=InstrumentId.from_str("LINKUSDT.BINANCE"),
        follower_instrument_id=InstrumentId.from_str("LINKUSDT-SPOT.BYBIT"),
        order_qty=LINK_CONFIG["order_qty"],
        min_order_qty=LINK_CONFIG["min_order_qty"],
        max_position_qty=LINK_CONFIG["max_position_qty"],
        guard_threshold_bps=LINK_CONFIG["guard_threshold_bps"],
        spread_bps=LINK_CONFIG["spread_bps"],
        min_profit_bps=LINK_CONFIG["min_profit_bps"],
        ofi_enabled=True,
        ofi_max_bps=LINK_CONFIG["ofi_max_bps"],
        internal_price_delta_limit=LINK_CONFIG["internal_price_delta_limit"],
        quote_refresh_interval_ms=3000,
        min_quote_lifetime_ms=1000,
    )
    
    strategy = LeadLagMMv3Primer(config=strategy_config)
    engine.add_strategy(strategy)
    
    # Run backtest
    print("Running backtest...")
    engine.run()
    
    # Print results
    print("\n" + "="*80)
    print("BACKTEST RESULTS - LINKUSDT")
    print("="*80)
    
    account = engine.trader.generate_account_report(Venue("BYBIT"))
    print(account)
    
    fills = engine.trader.generate_order_fills_report()
    print(f"\nTotal fills: {len(fills)}")
    
    print("\nDone!")

if __name__ == "__main__":
    main()
