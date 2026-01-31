#!/usr/bin/env python3
"""Backtest SpotMatrix strategy using QuestDB orderbook deltas."""

from __future__ import annotations

import argparse
from decimal import Decimal
from datetime import datetime, timedelta
from pathlib import Path

from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.model.data import Bar, BarType

from examples.backtest.questdb_orderbook_loader import QuestDbConfig, _iter_time_ranges, _query_json, load_questdb
from strategy.spot_matrix_v1 import SpotMatrix, SpotMatrixConfig


def _step_from_precision(precision: int) -> str:
    if precision <= 0:
        return "1"
    return "0." + ("0" * (precision - 1)) + "1"


def _precision_from_decimal(value: Decimal) -> int:
    exp = value.normalize().as_tuple().exponent
    return int(-exp) if exp < 0 else 0


def _infer_precision_from_questdb(cfg: QuestDbConfig, venue: str, symbol: str) -> tuple[int, int]:
    sql = (
        "select price, size from orderbook_deltas "
        f"where venue='{venue}' and symbol='{symbol}' limit 1"
    )
    data = load_questdb.__globals__["_query_json"](cfg, sql)
    dataset = data.get("dataset", [])
    if not dataset:
        raise ValueError(f"No QuestDB data for {symbol} {venue}")
    price, size = dataset[0]
    price_precision = _precision_from_decimal(Decimal(str(price)))
    size_precision = _precision_from_decimal(Decimal(str(size)))
    return price_precision, size_precision


def _create_instrument(
    instrument_id: str,
    symbol: str,
    price_precision: int,
    size_precision: int,
) -> CurrencyPair:
    if not symbol.endswith("USDT"):
        raise ValueError(f"Unsupported symbol (expected USDT quote): {symbol}")
    base_code = symbol[:-4]
    base_currency = Currency.from_str(base_code)

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


def _deltas_for_symbol(
    cfg: QuestDbConfig,
    instrument: CurrencyPair,
    symbol: str,
    date_str: str,
    max_updates: int | None,
):
    count = 0
    for deltas in load_questdb(cfg, instrument, venue="BYBIT", symbol=symbol, date_str=date_str):
        yield deltas
        count += 1
        if max_updates and count >= max_updates:
            break


def _bars_from_deltas(
    deltas_iter,
    instrument: CurrencyPair,
    bar_type: BarType,
    book_type: BookType,
):
    book = OrderBook(instrument_id=instrument.id, book_type=book_type)
    bucket = None
    o = h = l = c = None
    last_ts = None

    for deltas in deltas_iter:
        book.apply_deltas(deltas)
        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            continue
        mid = (bid.as_decimal() + ask.as_decimal()) / Decimal("2")

        ts_ns = getattr(deltas, "ts_event", None) or getattr(deltas, "ts_init", None)
        if ts_ns is None:
            continue
        ts_ns = int(ts_ns)
        minute_bucket = ts_ns // 60_000_000_000

        if bucket is None:
            bucket = minute_bucket
            o = h = l = c = mid
            last_ts = ts_ns
            continue

        if minute_bucket != bucket:
            yield Bar(
                bar_type=bar_type,
                open=instrument.make_price(o),
                high=instrument.make_price(h),
                low=instrument.make_price(l),
                close=instrument.make_price(c),
                volume=instrument.make_qty(Decimal("0")),
                ts_event=last_ts,
                ts_init=last_ts,
            )
            bucket = minute_bucket
            o = h = l = c = mid
            last_ts = ts_ns
            continue

        if mid > h:
            h = mid
        if mid < l:
            l = mid
        c = mid
        last_ts = ts_ns

    if bucket is not None and last_ts is not None:
        yield Bar(
            bar_type=bar_type,
            open=instrument.make_price(o),
            high=instrument.make_price(h),
            low=instrument.make_price(l),
            close=instrument.make_price(c),
            volume=instrument.make_qty(Decimal("0")),
            ts_event=last_ts,
            ts_init=last_ts,
        )


def _bars_from_questdb(
    cfg: QuestDbConfig,
    table: str,
    instrument: CurrencyPair,
    symbol: str,
    date_str: str,
    bar_seconds: int,
) -> list[Bar]:
    bars: list[Bar] = []
    for start_ts, end_ts in _iter_time_ranges(date_str, cfg.step_seconds):
        sql = (
            "select timestamp, open, high, low, close, volume "
            f"from {table} "
            f"where venue='BYBIT' and symbol='{symbol}' "
            f"and timestamp >= '{start_ts}' and timestamp < '{end_ts}' "
            "order by timestamp"
        )
        data = _query_json(cfg, sql)
        dataset = data.get("dataset", [])
        if not dataset:
            continue

        bar_type = BarType.from_str(
            f"{instrument.id}-{bar_seconds // 60}-MINUTE-MID-EXTERNAL"
        )
        for ts, o, h, l, c, v in dataset:
            if isinstance(ts, str):
                ts_ns = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
            else:
                ts_ns = int(ts) * 1_000_000
            bars.append(
                Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(Decimal(str(o))),
                    high=instrument.make_price(Decimal(str(h))),
                    low=instrument.make_price(Decimal(str(l))),
                    close=instrument.make_price(Decimal(str(c))),
                    volume=instrument.make_qty(Decimal(str(v))),
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
            )
    return bars


def _bars_from_questdb_range(
    cfg: QuestDbConfig,
    table: str,
    instrument: CurrencyPair,
    symbol: str,
    start_date: str,
    end_date: str,
    bar_seconds: int,
) -> list[Bar]:
    bars: list[Bar] = []
    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    cursor = start
    while cursor <= end:
        bars.extend(
            _bars_from_questdb(
                cfg,
                table,
                instrument,
                symbol,
                cursor.isoformat(),
                bar_seconds,
            )
        )
        cursor = cursor + timedelta(days=1)
    return bars


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SpotMatrix QuestDB backtest")
    parser.add_argument("--date", help="Date in YYYY-MM-DD format")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT", help="Comma-separated symbols")
    parser.add_argument("--max-updates", type=int, default=200000, help="Max deltas per symbol")
    parser.add_argument("--lookback-window", type=int, default=500, help="Bars lookback window")
    parser.add_argument("--rebalance-mins", type=int, default=60, help="Rebalance interval minutes")
    parser.add_argument("--min-rebalance-bps", type=Decimal, default=Decimal("50"))
    parser.add_argument("--order-chunk-usd", type=Decimal, default=Decimal("20"))
    parser.add_argument("--questdb-host", default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-table", default="orderbook_deltas")
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--use-bars", action="store_true", help="Use precomputed bars table")
    parser.add_argument("--bars-table", default="bars_1m_mid")
    parser.add_argument("--bar-seconds", type=int, default=60)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    questdb = QuestDbConfig(
        host=args.questdb_host,
        port=args.questdb_port,
        table=args.questdb_table,
        step_seconds=args.questdb_step_seconds,
    )

    config = BacktestEngineConfig(
        trader_id=TraderId("BACKTEST-SPOTMATRIX-001"),
        logging=LoggingConfig(
            log_level="ERROR",
            log_level_file="INFO",
            log_directory=".",
            log_file_name="spot_matrix_backtest.log",
            clear_log_file=True,
        ),
    )

    engine = BacktestEngine(config=config)
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(5000.0, USDT)],
        book_type=BookType.L1_MBP,
    )

    instruments: list[CurrencyPair] = []
    for symbol in symbols:
        price_precision, size_precision = _infer_precision_from_questdb(
            questdb,
            venue="BYBIT",
            symbol=symbol,
        )
        instrument = _create_instrument(
            instrument_id=f"{symbol}-SPOT.BYBIT",
            symbol=symbol,
            price_precision=price_precision,
            size_precision=size_precision,
        )
        engine.add_instrument(instrument)
        instruments.append(instrument)

    strategy = SpotMatrix(
        config=SpotMatrixConfig(
            instrument_ids=[i.id for i in instruments],
            rebalance_interval_mins=args.rebalance_mins,
            lookback_window=args.lookback_window,
            min_rebalance_bps=args.min_rebalance_bps,
            order_size_chunk_usd=args.order_chunk_usd,
        )
    )
    engine.add_strategy(strategy)

    if not args.date and not (args.start and args.end):
        raise SystemExit("Provide --date or --start/--end")

    for instrument in instruments:
        if args.use_bars:
            if args.date:
                bars = _bars_from_questdb(
                    questdb,
                    table=args.bars_table,
                    instrument=instrument,
                    symbol=str(instrument.raw_symbol),
                    date_str=args.date,
                    bar_seconds=args.bar_seconds,
                )
            else:
                bars = _bars_from_questdb_range(
                    questdb,
                    table=args.bars_table,
                    instrument=instrument,
                    symbol=str(instrument.raw_symbol),
                    start_date=args.start,
                    end_date=args.end,
                    bar_seconds=args.bar_seconds,
                )
            if bars:
                engine.add_data(bars)
            continue

        bar_type = BarType.from_str(
            f"{instrument.id}-{strategy.config.bar_interval}-{strategy.config.bar_price_type}-{strategy.config.bar_source}"
        )
        deltas_iter = _deltas_for_symbol(
            questdb,
            instrument,
            symbol=str(instrument.raw_symbol),
            date_str=args.date,
            max_updates=args.max_updates,
        )
        for bar in _bars_from_deltas(deltas_iter, instrument, bar_type, BookType.L2_MBP):
            engine.add_data([bar])

    engine.run()

    account_report = engine.trader.generate_account_report(BYBIT_VENUE)
    if account_report is not None:
        print(account_report.tail())

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    main()
