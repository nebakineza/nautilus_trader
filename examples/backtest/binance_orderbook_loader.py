"""Binance order book data loader for high-frequency backtesting.

Assumes JSON lines format matching the Bybit recorder structure:
- type: snapshot|delta
- ts: timestamp in ms
- data: {"s": symbol, "b": [[price, size], ...], "a": [[price, size], ...]}
"""

import json
from pathlib import Path
from typing import Generator

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder, OrderBookDelta, OrderBookDeltas
from nautilus_trader.model.enums import BookAction, BookType, OrderSide
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity


class BinanceOrderBookLoader:
    """Loads Binance order book data from JSON lines format."""

    @staticmethod
    def load_file(
        file_path: str | Path,
        instrument: Instrument,
    ) -> Generator[OrderBookDeltas, None, None]:
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Order book file not found: {file_path}")

        book = OrderBook(
            instrument_id=instrument.id,
            book_type=BookType.L2_MBP,
        )

        with open(file_path, "r") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())

                    if data.get("type") == "snapshot":
                        timestamp_ns = int(data["ts"]) * 1_000_000
                        book.clear(ts_event=timestamp_ns, sequence=0)
                        deltas = BinanceOrderBookLoader._process_snapshot(data, instrument)
                        if deltas.deltas:
                            yield deltas
                    elif data.get("type") == "delta":
                        deltas = BinanceOrderBookLoader._process_delta(data, instrument)
                        if deltas.deltas:
                            yield deltas

                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON at line {line_num}: {exc}")
                except Exception as exc:
                    raise ValueError(f"Error processing line {line_num}: {exc}")

    @staticmethod
    def _process_snapshot(data: dict, instrument: Instrument) -> OrderBookDeltas:
        timestamp_ns = int(data["ts"]) * 1_000_000
        ob_data = data["data"]
        deltas = []

        for price_str, size_str in ob_data.get("b", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647
            order = BookOrder(side=OrderSide.BUY, price=price, size=size, order_id=order_id)
            action = BookAction.ADD if size > 0 else BookAction.DELETE
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument.id,
                    action=action,
                    order=order,
                    flags=0,
                    sequence=0,
                    ts_event=timestamp_ns,
                    ts_init=timestamp_ns,
                )
            )

        for price_str, size_str in ob_data.get("a", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647
            order = BookOrder(side=OrderSide.SELL, price=price, size=size, order_id=order_id)
            action = BookAction.ADD if size > 0 else BookAction.DELETE
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument.id,
                    action=action,
                    order=order,
                    flags=0,
                    sequence=0,
                    ts_event=timestamp_ns,
                    ts_init=timestamp_ns,
                )
            )

        return OrderBookDeltas(instrument_id=instrument.id, deltas=deltas)

    @staticmethod
    def _process_delta(data: dict, instrument: Instrument) -> OrderBookDeltas:
        timestamp_ns = int(data["ts"]) * 1_000_000
        ob_data = data["data"]
        deltas = []

        for price_str, size_str in ob_data.get("b", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647
            order = BookOrder(side=OrderSide.BUY, price=price, size=size, order_id=order_id)
            action = BookAction.UPDATE if size > 0 else BookAction.DELETE
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument.id,
                    action=action,
                    order=order,
                    flags=0,
                    sequence=0,
                    ts_event=timestamp_ns,
                    ts_init=timestamp_ns,
                )
            )

        for price_str, size_str in ob_data.get("a", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647
            order = BookOrder(side=OrderSide.SELL, price=price, size=size, order_id=order_id)
            action = BookAction.UPDATE if size > 0 else BookAction.DELETE
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument.id,
                    action=action,
                    order=order,
                    flags=0,
                    sequence=0,
                    ts_event=timestamp_ns,
                    ts_init=timestamp_ns,
                )
            )

        return OrderBookDeltas(instrument_id=instrument.id, deltas=deltas)
