#!/usr/bin/env python3
"""Backtest Triangular Arb v002 (Multi-Path) with Tuned Balances."""

from __future__ import annotations

import argparse
import time
import heapq
from decimal import Decimal

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
from strategy.triangular_arb_v002 import TriangularArb, TriangularArbConfig, ArbPathConfig

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
    max_updates: int = 10000,
) -> None:
    
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("TRIARB-MULTIPATH"),
        logging=LoggingConfig(log_level="INFO"),
    )
    engine = BacktestEngine(config=engine_config)

    # 1. Define Instruments
    # We use available USDT pairs as proxies for cross/arb legs to simulate the ENGINE LOGIC.
    # Logic: 3 independent triangles running simultaneously.
    
    # Triangle A: ETH Loop (Proxy: ETHUSDT, SOLUSDT, BTCUSDT)
    eth_usdt = _create_instrument("ETHUSDT-SPOT.BYBIT", "ETHUSDT", ETH, USDT, 2, 3)
    sol_usdt = _create_instrument("SOLUSDT-SPOT.BYBIT", "SOLUSDT", SOL, USDT, 2, 2)
    btc_usdt = _create_instrument("BTCUSDT-SPOT.BYBIT", "BTCUSDT", BTC, USDT, 2, 5)

    # Triangle B: SOL Loop (Proxy: SOLUSDT, ETHUSDT, BTCUSDT) - just reusing instruments for disjoint logic check
    # To truly simulate "different" triangles with SAME instruments, we just create different Config Paths sharing instruments.
    # The requirement is "Run all three arb sets".
    # Since we lack distinct data for DOT/LINK etc in the small sample, we will simulate 
    # the 3 sets using the SAME data but distinct Path IDs and Configs to prove they fire independently.
    
    # 2. Configure Account (Tuned to ~1000 USDT Total)
    # We need inventory to fire Legs 2 & 3 in parallel (IOC).
    # Total Value ~ 1000 USDT.
    # Split: 400 USDT Cash. 
    #        0.1 ETH (~300 USDT)
    #        0.003 BTC (~300 USDT)
    #        SOL?
    
    engine.add_venue(
        venue=eth_usdt.id.venue,
        oms_type=OmsType.HEDGING, 
        account_type=AccountType.CASH,
        starting_balances=[
            Money(Decimal("983.84777621"), USDT),
            Money(Decimal("0.10450529"), ETH),
            Money(Decimal("1.85697254"), SOL),
            Money(Decimal("0.00252926"), BTC),
        ],
    )
    
    engine.add_instrument(eth_usdt)
    engine.add_instrument(sol_usdt)
    engine.add_instrument(btc_usdt)

    # 3. Strategy Config (Multi-Path)
    # We split the 1000 USDT capacity into "slots". 
    # Each path gets small size to fit in the balance.
    
    paths = [
        # Path 1: USDT -> ETH -> SOL(Proxy) -> USDT
        ArbPathConfig(
            id="ETH_TRI",
            leg1_instrument_id=eth_usdt.id, leg1_side=OrderSide.BUY,
            leg2_instrument_id=sol_usdt.id, leg2_side=OrderSide.BUY, # Proxy Leg
            leg3_instrument_id=btc_usdt.id, leg3_side=OrderSide.SELL,
            order_qty=Decimal("0.01"), # Small size
        ),
        # Path 2: USDT -> SOL -> ETH(Proxy) -> USDT
        ArbPathConfig(
            id="SOL_TRI",
            leg1_instrument_id=sol_usdt.id, leg1_side=OrderSide.BUY,
            leg2_instrument_id=eth_usdt.id, leg2_side=OrderSide.BUY, # Proxy Leg
            leg3_instrument_id=btc_usdt.id, leg3_side=OrderSide.SELL,
            order_qty=Decimal("0.1"), # Small size
        ),
        # Path 3: The "VIP Fast Track" Loop (USDT->BTC->USDC->USDT) (Simulated with BTC->ETH proxy)
        ArbPathConfig(
            id="BTC_TRI",
            leg1_instrument_id=btc_usdt.id, leg1_side=OrderSide.BUY, # Buy BTC
            leg2_instrument_id=eth_usdt.id, leg2_side=OrderSide.SELL, # Sell BTC for ETH (Proxy)
            leg3_instrument_id=sol_usdt.id, leg3_side=OrderSide.SELL, # Sell ETH for USDT (Proxy)
            order_qty=Decimal("0.001"), 
        ),
    ]

    config = TriangularArbConfig(
        paths=paths,
        min_profit_bps=Decimal("-99999"), # Negative trigger for volume test
    )
    
    strategy = TriangularArb(config=config)
    engine.add_strategy(strategy)

    # 4. Data Loading
    questdb = QuestDbConfig(host=questdb_host, port=questdb_port)
    print(f"Loading data from QuestDB for date {date_str}...")
    
    gen1 = load_questdb(cfg=questdb, instrument=eth_usdt, venue="BYBIT", symbol="ETHUSDT", date_str=date_str)
    gen2 = load_questdb(cfg=questdb, instrument=sol_usdt, venue="BYBIT", symbol="SOLUSDT", date_str=date_str)
    gen3 = load_questdb(cfg=questdb, instrument=btc_usdt, venue="BYBIT", symbol="BTCUSDT", date_str=date_str)

    def get_ts(d: OrderBookDeltas):
        return d.ts_event

    merged_gen = heapq.merge(gen1, gen2, gen3, key=get_ts)
    
    # 5. Run
    count = 0
    start = time.time()
    batch = []
    
    print("Feeding engine...")
    for deltas in merged_gen:
        batch.append(deltas)
        count += 1
        if len(batch) >= 100:
             engine.add_data(batch)
             batch = []
        if count >= max_updates:
            break
            
    if batch:
        engine.add_data(batch)
            
    print(f"Loaded {count} updates in {time.time() - start:.2f}s")
    engine.run()
    
    # Report
    account = engine.cache.account_for_venue(eth_usdt.id.venue)
    print("\nFinal Balances:")
    if account:
        for bal in account.balances():
            print(bal)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="2026-01-29")
    parser.add_argument("--max-updates", type=int, default=10000)
    args = parser.parse_args()
    
    run_backtest(args.date, max_updates=args.max_updates)
