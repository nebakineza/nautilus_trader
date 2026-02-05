#!/usr/bin/env python3
"""Backtest Triangular Arb v001."""

from __future__ import annotations

import argparse
import time
import heapq
from decimal import Decimal
from typing import Iterator

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT, BTC, ETH, SOL
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.data import OrderBookDeltas

from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb
from strategy.triangular_arb_v001 import TriangularArb, TriangularArbConfig

def _step_from_precision(precision: int) -> str:
    if precision <= 0:
        return "1"
    return "0." + ("0" * (precision - 1)) + "1"

def _create_instrument(
    instrument_id: str,
    symbol: str,
    base_currency: object,
    quote_currency: object,
    price_precision: int,
    size_precision: int,
) -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=quote_currency,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=Price.from_str(_step_from_precision(price_precision)),
        size_increment=Quantity.from_str(_step_from_precision(size_precision)),
        lot_size=None,
        max_quantity=Quantity.from_str("1000000"),
        min_quantity=Quantity.from_str(_step_from_precision(size_precision)),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.0001"),
        taker_fee=Decimal("0.0001"),
        ts_event=0,
        ts_init=0,
    )

def run_backtest(
    date_str: str,
    questdb_host: str = "127.0.0.1",
    questdb_port: int = 9000,
    max_updates: int = 20000,
) -> None:
    
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("TRIARB-BACKTEST"),
        logging=LoggingConfig(log_level="INFO"),
    )
    engine = BacktestEngine(config=engine_config)

    # Instruments
    # Leg 1: ETHUSDT (Buy)
    inst1 = _create_instrument("ETHUSDT-SPOT.BYBIT", "ETHUSDT", ETH, USDT, 2, 3)
    # Leg 2: SOLUSDT (Buy - PROXY for Cross)
    inst2 = _create_instrument("SOLUSDT-SPOT.BYBIT", "SOLUSDT", SOL, USDT, 2, 2)
    # Leg 3: BTCUSDT (Sell - PROXY)
    inst3 = _create_instrument("BTCUSDT-SPOT.BYBIT", "BTCUSDT", BTC, USDT, 2, 5)

    
    engine.add_venue(
        venue=inst1.id.venue,
        oms_type=OmsType.HEDGING, 
        account_type=AccountType.CASH,
        starting_balances=[
            Money(Decimal("100000.0"), USDT),
            Money(Decimal("10.0"), ETH), 
            Money(Decimal("100.0"), SOL),
            Money(Decimal("1.0"), BTC),
        ],
    )
    
    engine.add_instrument(inst1)
    engine.add_instrument(inst2)
    engine.add_instrument(inst3)

    # Config
    # Uses negative profit threshold to force trades for logic testing
    config = TriangularArbConfig(
        leg1_instrument_id=inst1.id,
        leg2_instrument_id=inst2.id,
        leg3_instrument_id=inst3.id,
        leg1_side=OrderSide.BUY,
        leg2_side=OrderSide.BUY, # Proxy
        leg3_side=OrderSide.SELL, # Proxy
        order_qty=Decimal("0.1"), # 0.1 ETH
        min_profit_bps=Decimal("-99999"), 
        max_inflight_cycles=1,
    )
    
    strategy = TriangularArb(config=config)
    engine.add_strategy(strategy)

    questdb = QuestDbConfig(host=questdb_host, port=questdb_port)
    print(f"Loading data from QuestDB for date {date_str}...")
    
    # Load generators
    gen1 = load_questdb(cfg=questdb, instrument=inst1, venue="BYBIT", symbol="ETHUSDT", date_str=date_str)
    gen2 = load_questdb(cfg=questdb, instrument=inst2, venue="BYBIT", symbol="SOLUSDT", date_str=date_str)
    gen3 = load_questdb(cfg=questdb, instrument=inst3, venue="BYBIT", symbol="BTCUSDT", date_str=date_str)

    # Merge by timestamp (ts_event)
    def get_ts(d: OrderBookDeltas):
        return d.ts_event

    merged_gen = heapq.merge(gen1, gen2, gen3, key=get_ts)
    
    count = 0
    start = time.time()
    batch = []
    
    print("Feeding engine...")
    for deltas in merged_gen:
        batch.append(deltas)
        count += 1
        if len(batch) >= 100: # Batch feed
             engine.add_data(batch)
             batch = []
        if count >= max_updates:
            break
            
    if batch:
        engine.add_data(batch)
            
    print(f"Loaded {count} updates in {time.time() - start:.2f}s")
    
    engine.run()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="2026-01-29")
    parser.add_argument("--max-updates", type=int, default=20000)
    args = parser.parse_args()
    
    run_backtest(args.date, max_updates=args.max_updates)
