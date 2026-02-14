#!/usr/bin/env python3
"""Backtest for HFT Multi-Signal Scalper v001.

Requires local orderbook data for Binance and Bybit in data/ob_data/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import LatencyModel, FillModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money
from nautilus_trader.model.venues import Venue

from examples.backtest.binance_orderbook_loader import BinanceOrderBookLoader
from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from strategy.hft_multi_signal_scalper_v001 import HftMultiSignalConfig, HftMultiSignalScalper


def run_backtest(symbol: str, data_dir: str) -> None:
    bybit_venue = Venue("BYBIT")
    binance_venue = Venue("BINANCE_SPOT")

    follower_id = InstrumentId.from_str(f"{symbol}-SPOT.BYBIT")
    leader_id = InstrumentId.from_str(f"{symbol}.BINANCE_SPOT")

    instrument = CurrencyPair(
        symbol=Symbol(symbol),
        base_currency=symbol.replace("USDT", ""),
        quote_currency=USDT,
        price_precision=4,
        size_precision=4,
        price_increment=Decimal("0.0001"),
        size_increment=Decimal("0.0001"),
        lot_size=Decimal("0.0001"),
        max_quantity=Decimal("1000000"),
        min_quantity=Decimal("0.0001"),
    )

    config = BacktestEngineConfig(
        trader_id=TraderId("BACKTEST-HFT-MULTI"),
        logging=LoggingConfig(log_level="INFO"),
        account_type=AccountType.CASH,
        oms_type=OmsType.NETTING,
        base_currency=USDT,
        starting_balances=[Money(1000, USDT)],
        latency_model=LatencyModel.fixed(10),
        fill_model=FillModel.trades(),
    )

    engine = BacktestEngine(config=config)
    engine.add_instrument(instrument)

    bybit_loader = BybitOrderBookLoader(Path(data_dir))
    binance_loader = BinanceOrderBookLoader(Path(data_dir))

    bybit_loader.load_orderbook(engine, symbol, book_type=BookType.L2_MBP, venue=bybit_venue)
    binance_loader.load_orderbook(engine, symbol, book_type=BookType.L2_MBP, venue=binance_venue)

    strategy_config = HftMultiSignalConfig(
        strategy_id="HFT-MULTI-BACKTEST",
        follower_instrument_id=follower_id,
        leader_instrument_id=leader_id,
        order_qty=0.001,
        max_position_qty=0.01,
    )

    engine.add_strategy(HftMultiSignalScalper(config=strategy_config))
    engine.run()
    engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HFT Multi-Signal backtest")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--data-dir", default="data/ob_data")
    args = parser.parse_args()
    run_backtest(args.symbol, args.data_dir)
