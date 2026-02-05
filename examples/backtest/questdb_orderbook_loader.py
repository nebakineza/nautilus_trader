"""QuestDB order book loader for Nautilus backtests."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generator, Iterable

from nautilus_trader.model.data import BookOrder, OrderBookDelta, OrderBookDeltas
from nautilus_trader.model.enums import BookAction, OrderSide
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity


@dataclass(frozen=True)
class QuestDbConfig:
    host: str = "127.0.0.1"
    port: int = 9000
    table: str = "orderbook_deltas"
    step_seconds: int = 300

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/exec?query="


def _query_json(cfg: QuestDbConfig, sql: str) -> dict:
    url = cfg.base_url + urllib.parse.quote_plus(sql)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def _iter_time_ranges(date_str: str, step_seconds: int) -> Iterable[tuple[str, str]]:
    start = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    step = timedelta(seconds=step_seconds)
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        yield cursor.isoformat().replace("+00:00", "Z"), nxt.isoformat().replace("+00:00", "Z")
        cursor = nxt


def load_questdb(
    cfg: QuestDbConfig,
    instrument: Instrument,
    venue: str,
    symbol: str,
    date_str: str,
) -> Generator[OrderBookDeltas, None, None]:
    """Load order book deltas from QuestDB for a single date.

    Parameters
    ----------
    cfg : QuestDbConfig
        QuestDB connection settings.
    instrument : Instrument
        Instrument to build order book deltas for.
    venue : str
        Venue tag stored in QuestDB (e.g., "BYBIT" or "BINANCE").
    symbol : str
        Symbol tag stored in QuestDB (e.g., "BTCUSDT").
    date_str : str
        Date in YYYY-MM-DD format.
    """

    for start_ts, end_ts in _iter_time_ranges(date_str, cfg.step_seconds):
        sql = (
            "select timestamp, side, price, size, snapshot "
            f"from {cfg.table} "
            f"where venue='{venue}' and \"symbol\"='{symbol}' "
            f"and timestamp >= '{start_ts}' and timestamp < '{end_ts}' "
            "order by timestamp"
        )
        data = _query_json(cfg, sql)
        dataset = data.get("dataset", [])
        if not dataset:
            continue

        current_ts = None
        current_deltas: list[OrderBookDelta] = []

        for ts, side, price, size, snapshot in dataset:
            if isinstance(ts, str):
                ts_ns = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
            else:
                ts_ns = int(ts) * 1_000_000  # QuestDB timestamps are ms
            if current_ts is None:
                current_ts = ts_ns
            if ts_ns != current_ts:
                if current_deltas:
                    yield OrderBookDeltas(instrument_id=instrument.id, deltas=current_deltas)
                current_deltas = []
                current_ts = ts_ns

            order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
            price_obj = Price(float(price), precision=instrument.price_precision)
            qty_obj = Quantity(float(size), precision=instrument.size_precision)
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
                    instrument_id=instrument.id,
                    action=action,
                    order=order,
                    flags=0,
                    sequence=0,
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
            )

        if current_deltas:
            yield OrderBookDeltas(instrument_id=instrument.id, deltas=current_deltas)
