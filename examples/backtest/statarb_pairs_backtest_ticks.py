#!/usr/bin/env python3
"""Backtest StatArb pairs using tick_data CSVs.

Example:
  python examples/backtest/statarb_pairs_backtest_ticks.py \
        --data-dir /home/ubuntu/trading/data/tick_data \
    --max-ticks 50000
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import DOT, LINK, USDT
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity, Currency
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler

from strategy.statarb.pairs_strategy import StatArbPairsConfig, StatArbPairsStrategy
from strategy.statarb.configs import DOT_LINK_CONFIG


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="StatArb backtest from tick_data CSVs.")
    parser.add_argument("--data-dir", required=True, help="tick_data base directory")
    parser.add_argument("--max-ticks", type=int, default=50000, help="Max ticks per symbol")
    parser.add_argument("--entry-z", type=float, default=2.0, help="Entry z-score for test")
    parser.add_argument("--min-profit-bps", type=float, default=10.0, help="Min profit bps for test")
    parser.add_argument("--taker-fee-bps", type=float, default=7.5, help="Taker fee bps for test")
    return parser.parse_args()


def iter_csv_files(base_dir: Path, symbol: str) -> list[Path]:
    folder = base_dir / f"{symbol}_Spot"
    if not folder.exists():
        return []
    return sorted(list(folder.glob("*.csv")) + list(folder.glob("*.csv.gz")))


def load_ticks(paths: Iterable[Path], max_ticks: int) -> pd.DataFrame:
    chunks = []
    count = 0
    for path in paths:
        try:
            df = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            continue
        if df.empty:
            continue
        remaining = max_ticks - count
        if remaining <= 0:
            break
        df = df.head(remaining)
        count += len(df)
        chunks.append(df)
        if count >= max_ticks:
            break
    if not chunks:
        return pd.DataFrame()
    df_all = pd.concat(chunks, ignore_index=True)
    df_all = df_all.rename(columns={"volume": "quantity"})
    df_all["trade_id"] = df_all["id"].astype(str)
    df_all["side"] = df_all["side"].str.upper()
    df_all["timestamp"] = pd.to_datetime(df_all["timestamp"], unit="ms", utc=True)
    df_all = df_all.set_index("timestamp")
    return df_all[["price", "quantity", "side", "trade_id"]]


def infer_precision(series: pd.Series, default: int) -> int:
    if series.empty:
        return default
    value = str(series.iloc[0])
    if "." not in value:
        return 0
    return len(value.split(".")[1].rstrip("0")) or default


def build_currency_pair(symbol: str, base: Currency, quote: Currency, price_prec: int, size_prec: int) -> CurrencyPair:
    venue = Venue("BYBIT")
    instrument_id = InstrumentId(symbol=Symbol(f"{symbol}-SPOT"), venue=venue)
    raw_symbol = Symbol(symbol)
    price_increment = Price(Decimal(10) ** Decimal(-price_prec), price_prec)
    size_increment = Quantity(Decimal(10) ** Decimal(-size_prec), size_prec)
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        base_currency=base,
        quote_currency=quote,
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=price_increment,
        size_increment=size_increment,
        ts_event=0,
        ts_init=0,
        min_notional=Money(Decimal("1"), quote),
    )


def main() -> None:
    args = parse_args()
    base_dir = Path(args.data_dir).expanduser().resolve()

    symbols = {
        "DOTUSDT": DOT,
        "LINKUSDT": LINK,
    }

    data_frames: dict[str, pd.DataFrame] = {}
    for symbol, base_ccy in symbols.items():
        files = iter_csv_files(base_dir, symbol)
        if not files:
            continue
        df = load_ticks(files, args.max_ticks)
        if df.empty:
            continue
        data_frames[symbol] = df

    if "DOTUSDT" not in data_frames or "LINKUSDT" not in data_frames:
        raise SystemExit("Missing DOTUSDT or LINKUSDT tick data for backtest")

    config = BacktestEngineConfig(
        trader_id="BACKTEST-STATARB-PAIRS",
        logging=LoggingConfig(log_level="INFO"),
    )
    engine = BacktestEngine(config=config)

    bybit = Venue("BYBIT")
    engine.add_venue(
        venue=bybit,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[
            Money(Decimal("10000"), USDT),
            Money(Decimal("100"), DOT),
            Money(Decimal("100"), LINK),
        ],
    )

    instruments = {}
    for symbol, base_ccy in symbols.items():
        if symbol not in data_frames:
            continue
        df = data_frames[symbol]
        price_prec = infer_precision(df["price"], 3)
        size_prec = infer_precision(df["quantity"], 4)
        instrument = build_currency_pair(symbol, base_ccy, USDT, price_prec, size_prec)
        engine.add_instrument(instrument)
        wrangler = TradeTickDataWrangler(instrument=instrument)
        ticks = wrangler.process(df)
        engine.add_data(ticks)
        instruments[symbol] = instrument

    dot_link_cfg = StatArbPairsConfig(**{
        **DOT_LINK_CONFIG.dict(),
        "entry_z_score": args.entry_z,
        "min_profit_bps": Decimal(str(args.min_profit_bps)),
        "taker_fee_bps": Decimal(str(args.taker_fee_bps)),
    })
    engine.add_strategy(StatArbPairsStrategy(config=dot_link_cfg))

    engine.run()

    print(engine.trader.generate_account_report(bybit))
    print(engine.trader.generate_order_fills_report())
    print(engine.trader.generate_positions_report())


if __name__ == "__main__":
    main()
