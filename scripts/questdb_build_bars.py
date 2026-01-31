#!/usr/bin/env python3
"""Build 1-minute MID bars from QuestDB orderbook deltas and store them in QuestDB.

Example:
  python scripts/questdb_build_bars.py \
    --date 2026-01-29 --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --source-table orderbook_deltas --bars-table bars_1m_mid
"""

from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from nautilus_trader.model.data import BookOrder, OrderBookDelta, OrderBookDeltas
from nautilus_trader.model.enums import BookAction, BookType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.book import OrderBook

from examples.backtest.questdb_orderbook_loader import QuestDbConfig, _iter_time_ranges, _query_json


@dataclass(frozen=True)
class BarsConfig:
    venue: str = "BYBIT"
    bar_seconds: int = 60


def _precision_from_decimal(value: Decimal) -> int:
    exp = value.normalize().as_tuple().exponent
    return int(-exp) if exp < 0 else 0


def _infer_precision(cfg: QuestDbConfig, table: str, venue: str, symbol: str, skip_failures: bool) -> tuple[int, int]:
    sql = (
        "select price, size from "
        f"{table} where venue='{venue}' and symbol='{symbol}' limit 1"
    )
    data = _query_json_retry(cfg, sql, skip_failures=skip_failures)
    dataset = data.get("dataset", [])
    if not dataset:
        raise ValueError(f"No QuestDB data for {symbol} {venue}")
    price, size = dataset[0]
    return _precision_from_decimal(Decimal(str(price))), _precision_from_decimal(Decimal(str(size)))


def _iter_deltas(
    cfg: QuestDbConfig,
    table: str,
    instrument_id: InstrumentId,
    price_precision: int,
    size_precision: int,
    venue: str,
    symbol: str,
    date_str: str,
    skip_failures: bool,
) -> Iterable[OrderBookDeltas]:
    for start_ts, end_ts in _iter_time_ranges(date_str, cfg.step_seconds):
        sql = (
            "select timestamp, side, price, size, snapshot "
            f"from {table} "
            f"where venue='{venue}' and symbol='{symbol}' "
            f"and timestamp >= '{start_ts}' and timestamp < '{end_ts}' "
            "order by timestamp"
        )
        data = _query_json_retry(cfg, sql, skip_failures=skip_failures)
        dataset = data.get("dataset", [])
        if not dataset:
            continue

        current_ts = None
        current_deltas: list[OrderBookDelta] = []

        for ts, side, price, size, snapshot in dataset:
            if isinstance(ts, str):
                ts_ns = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
            else:
                ts_ns = int(ts) * 1_000_000

            if current_ts is None:
                current_ts = ts_ns
            if ts_ns != current_ts:
                if current_deltas:
                    yield OrderBookDeltas(instrument_id=instrument_id, deltas=current_deltas)
                current_deltas = []
                current_ts = ts_ns

            order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
            price_obj = Price(float(price), precision=price_precision)
            qty_obj = Quantity(float(size), precision=size_precision)
            order = BookOrder(
                side=order_side,
                price=price_obj,
                size=qty_obj,
                order_id=hash((side, price)) % 2147483647,
            )
            if snapshot:
                action = BookAction.ADD if qty_obj > 0 else BookAction.DELETE
            else:
                action = BookAction.UPDATE if qty_obj > 0 else BookAction.DELETE
            current_deltas.append(
                OrderBookDelta(
                    instrument_id=instrument_id,
                    action=action,
                    order=order,
                    flags=0,
                    sequence=0,
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
            )

        if current_deltas:
            yield OrderBookDeltas(instrument_id=instrument_id, deltas=current_deltas)


def _build_mid_bars(
    deltas_iter: Iterable[OrderBookDeltas],
    instrument_id: InstrumentId,
    price_precision: int,
    bar_seconds: int,
) -> Iterable[tuple[int, Decimal, Decimal, Decimal, Decimal, Decimal]]:
    book = OrderBook(instrument_id=instrument_id, book_type=BookType.L2_MBP)
    bucket = None
    o = h = l = c = None
    last_ts = None
    bar_ns = bar_seconds * 1_000_000_000

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
        bar_bucket = ts_ns // bar_ns

        if bucket is None:
            bucket = bar_bucket
            o = h = l = c = mid
            last_ts = ts_ns
            continue

        if bar_bucket != bucket:
            yield last_ts, o, h, l, c, Decimal("0")
            bucket = bar_bucket
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
        yield last_ts, o, h, l, c, Decimal("0")


def _query_json_retry(
    cfg: QuestDbConfig,
    sql: str,
    retries: int = 5,
    backoff: float = 1.0,
    skip_failures: bool = False,
) -> dict:
    for attempt in range(1, retries + 1):
        try:
            return _query_json(cfg, sql)
        except Exception as exc:  # noqa: BLE001 - QuestDB connection issues
            if attempt >= retries:
                if skip_failures:
                    return {"dataset": []}
                raise
            time.sleep(backoff * attempt)


def _ilp_line(
    table: str,
    venue: str,
    symbol: str,
    bar_seconds: int,
    ts_ns: int,
    o: Decimal,
    h: Decimal,
    l: Decimal,
    c: Decimal,
    v: Decimal,
) -> str:
    tags = f"venue={venue},symbol={symbol},interval={bar_seconds}s,price_type=MID,source=QUESTDB"
    fields = (
        f"open={o},high={h},low={l},close={c},volume={v}"
    )
    return f"{table},{tags} {fields} {ts_ns}\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build MID bars from QuestDB orderbook deltas")
    parser.add_argument("--date", help="Single date in YYYY-MM-DD format")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    parser.add_argument("--venue", default="BYBIT", help="Venue tag in QuestDB")
    parser.add_argument("--source-table", default="orderbook_deltas")
    parser.add_argument("--bars-table", default="bars_1m_mid")
    parser.add_argument("--bar-seconds", type=int, default=60)
    parser.add_argument("--questdb-host", default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--ilp-host", default="127.0.0.1")
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-failures", action="store_true", help="Skip failed QuestDB ranges")
    return parser.parse_args()


def _iter_dates(start: date, end: date) -> Iterable[str]:
    cursor = start
    while cursor <= end:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def main() -> None:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.date:
        dates = [args.date]
    elif args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        if end < start:
            raise SystemExit("--end must be >= --start")
        dates = list(_iter_dates(start, end))
    else:
        raise SystemExit("Provide --date or --start/--end")

    cfg = QuestDbConfig(
        host=args.questdb_host,
        port=args.questdb_port,
        table=args.source_table,
        step_seconds=args.questdb_step_seconds,
    )

    sock: socket.socket | None = None
    if not args.dry_run:
        sock = socket.create_connection((args.ilp_host, args.ilp_port), timeout=10)

    total = 0
    for symbol in symbols:
        price_precision, size_precision = _infer_precision(cfg, args.source_table, args.venue, symbol, args.skip_failures)
        instrument_id = InstrumentId.from_str(f"{symbol}-SPOT.{args.venue}")
        for date_str in dates:
            deltas_iter = _iter_deltas(
                cfg,
                args.source_table,
                instrument_id,
                price_precision,
                size_precision,
                args.venue,
                symbol,
                date_str,
                skip_failures=args.skip_failures,
            )
            for ts_ns, o, h, l, c, v in _build_mid_bars(
                deltas_iter,
                instrument_id,
                price_precision,
                args.bar_seconds,
            ):
                line = _ilp_line(
                    args.bars_table,
                    args.venue,
                    symbol,
                    args.bar_seconds,
                    ts_ns,
                    o,
                    h,
                    l,
                    c,
                    v,
                )
                total += 1
                if args.dry_run:
                    if total <= 5:
                        print(line.strip())
                    continue
                if sock:
                    sock.sendall(line.encode("utf-8"))

    if sock:
        sock.close()

    print(f"Wrote {total:,} bars into {args.bars_table}")


if __name__ == "__main__":
    main()
