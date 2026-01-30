#!/usr/bin/env python3
"""
Backtest runner for DOT-LINK Statistical Arbitrage.
Note: This requires historical bar data to be present in the catalog or passed via loader.
Since we don't have the data file on this instance, this is a template implementation.
"""

from __future__ import annotations

from decimal import Decimal
import pandas as pd
from datetime import datetime

from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.identifiers import TraderId, InstrumentId, Venue
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.objects import Money
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy.statarb.pairs_strategy import StatArbPairsStrategy
from strategy.statarb.configs import DOT_LINK_CONFIG

def build_backtest(start_dt, end_dt):
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("BACKTEST-STATARB-001"),
            logging=LoggingConfig(log_level="INFO"),
        )
    )
    
    # Define Instruments manually since we are offline-ish
    # DOT
    dot = TestInstrumentProvider.default_currency_pair(
        symbol="DOTUSDT",
        venue="BYBIT",
        base_currency="DOT",
        quote_currency="USDT"
    )
    dot.price_precision = 3
    dot.size_precision = 2
    
    # LINK
    link = TestInstrumentProvider.default_currency_pair(
        symbol="LINKUSDT",
        venue="BYBIT",
        base_currency="LINK",
        quote_currency="USDT"
    )
    link.price_precision = 3
    link.size_precision = 2
    
    engine.add_instrument(dot)
    engine.add_instrument(link)
    
    # Load Strategy
    strategy = StatArbPairsStrategy(config=DOT_LINK_CONFIG)
    engine.add_strategy(strategy)
    
    return engine

if __name__ == "__main__":
    print("StatArb Backtest Runner Template")
    print("To run this, you must load data into the engine.")
    # engine = build_backtest(None, None)
    # engine.run()
