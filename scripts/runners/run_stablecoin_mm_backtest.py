#!/usr/bin/env python3
"""Backtest Stablecoin MM v001 (using SOLUSDT as proxy)."""

from __future__ import annotations

import argparse
import time
from decimal import Decimal

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT, SOL
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb
from strategy.stablecoin_mm_v001 import StablecoinMM, StablecoinMMConfig

def _step_from_precision(precision: int) -> str:
    if precision <= 0:
        return "1"
    return "0." + ("0" * (precision - 1)) + "1"

def _create_instrument(
    instrument_id: str,
    symbol: str,
    base_currency: object,
    price_precision: int,
    size_precision: int,
) -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=USDT,
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
        trader_id=TraderId("STABLE-BACKTEST"),
        logging=LoggingConfig(log_level="INFO"),
    )
    engine = BacktestEngine(config=engine_config)

    symbol_str = "SOLUSDT"
    instrument_id_str = f"{symbol_str}-SPOT.BYBIT"
    instrument = _create_instrument(instrument_id_str, symbol_str, SOL, 2, 2)
    
    # ADD VENUE FIRST
    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=OmsType.HEDGING, 
        account_type=AccountType.CASH,
        starting_balances=[
            Money(Decimal("10000.0"), USDT),
            Money(Decimal("100.0"), SOL), 
        ],
    )
    # THEN INSTRUMENT
    engine.add_instrument(instrument)

    # Config mimicking Stablecoin behavior on SOL prices (mid tracking)
    config = StablecoinMMConfig(
        instrument_id=instrument.id,
        order_qty=Decimal("1.0"),
        max_position_qty=Decimal("10.0"),
        center_price=Decimal("124.0"), 
        spread_ticks=10, 
        min_balance_ratio=Decimal("0.95"),
        max_divergence_bps=Decimal("1000.0"), # Wide tolerance for vol proxy
        quote_refresh_interval_ms=2000,
        use_mid_price=True, # Enable mid price tracking for volatile proxy
    )
    
    strategy = StablecoinMM(config=config)
    engine.add_strategy(strategy)

    questdb = QuestDbConfig(host=questdb_host, port=questdb_port)
    print(f"Loading {symbol_str} from QuestDB...")
    
    deltas_gen = load_questdb(
        cfg=questdb,
        instrument=instrument,
        venue="BYBIT",
        symbol=symbol_str,
        date_str=date_str,
    )
    
    count = 0
    start = time.time()
    for deltas in deltas_gen:
        engine.add_data([deltas])
        count += 1
        if count >= max_updates:
            break
            
    print(f"Loaded {count} updates in {time.time() - start:.2f}s")
    
    engine.run()

    account = engine.cache.account_for_venue(instrument.id.venue)
    if account:
        print("Final Balances:")
        print(f"USDT: {account.balance_total(USDT)}")
        print(f"SOL: {account.balance_total(SOL)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="2026-01-29")
    parser.add_argument("--max-updates", type=int, default=20000)
    args = parser.parse_args()
    
    run_backtest(args.date, max_updates=args.max_updates)
