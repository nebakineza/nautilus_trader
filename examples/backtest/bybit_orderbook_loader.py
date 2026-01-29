"""Bybit order book data loader for high-frequency backtesting.

This module handles loading Bybit's JSON-based order book snapshots and deltas
and converting them to Nautilus OrderBookDeltas for backtesting.

Format:
- Snapshots: Full 200-level order book at a timestamp
- Deltas: Incremental updates to the order book
- All prices/sizes are strings in Bybit format
"""

import json
from pathlib import Path
from typing import Generator

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder, OrderBookDelta, OrderBookDeltas
from nautilus_trader.model.enums import BookAction, BookType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity


class BybitOrderBookLoader:
    """Loads Bybit order book data from JSON lines format."""

    @staticmethod
    def load_file(
        file_path: str | Path,
        instrument: Instrument,
    ) -> Generator[OrderBookDeltas, None, None]:
        """
        Load order book data from a Bybit JSON lines file.

        Parameters
        ----------
        file_path : str | Path
            Path to the .data file
        instrument : Instrument
            The instrument for the order book

        Yields
        ------
        OrderBookDeltas
            Order book delta objects
        """
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
                        # Process snapshot: replace entire book
                        timestamp_ns = int(data["ts"]) * 1_000_000
                        book.clear(ts_event=timestamp_ns, sequence=0)
                        deltas = BybitOrderBookLoader._process_snapshot(
                            data, instrument
                        )
                        if deltas.deltas:
                            yield deltas
                    elif data.get("type") == "delta":
                        # Process delta: apply updates
                        deltas = BybitOrderBookLoader._process_delta(
                            data, instrument
                        )
                        if deltas.deltas:
                            yield deltas

                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON at line {line_num}: {e}")
                except Exception as e:
                    raise ValueError(f"Error processing line {line_num}: {e}")

    @staticmethod
    def _process_snapshot(
        data: dict,
        instrument: Instrument,
    ) -> OrderBookDeltas:
        """
        Convert Bybit snapshot to OrderBookDeltas.

        Parameters
        ----------
        data : dict
            Snapshot data with structure:
            {
                "ts": timestamp_ms,
                "data": {
                    "s": "BTCUSDT",
                    "b": [["price", "size"], ...],  # Bids
                    "a": [["price", "size"], ...]   # Asks
                }
            }
        instrument : Instrument
            The instrument

        Returns
        -------
        OrderBookDeltas
            Order book delta(s)
        """
        timestamp_ns = int(data["ts"]) * 1_000_000  # Convert ms to ns
        ob_data = data["data"]

        deltas = []

        # Process bids (descending price order)
        for price_str, size_str in ob_data.get("b", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647  # Keep within int32 range

            order = BookOrder(
                side=OrderSide.BUY,
                price=price,
                size=size,
                order_id=order_id,
            )

            action = BookAction.ADD if size > 0 else BookAction.DELETE
            delta = OrderBookDelta(
                instrument_id=instrument.id,
                action=action,
                order=order,
                flags=0,
                sequence=0,
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
            deltas.append(delta)

        # Process asks (ascending price order)
        for price_str, size_str in ob_data.get("a", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647

            order = BookOrder(
                side=OrderSide.SELL,
                price=price,
                size=size,
                order_id=order_id,
            )

            action = BookAction.ADD if size > 0 else BookAction.DELETE
            delta = OrderBookDelta(
                instrument_id=instrument.id,
                action=action,
                order=order,
                flags=0,
                sequence=0,
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
            deltas.append(delta)

        # Return OrderBookDeltas containing all deltas
        return OrderBookDeltas(
            instrument_id=instrument.id,
            deltas=deltas,
        )

    @staticmethod
    def _process_delta(
        data: dict,
        instrument: Instrument,
    ) -> OrderBookDeltas:
        """
        Convert Bybit delta to OrderBookDeltas.

        Parameters
        ----------
        data : dict
            Delta data with same structure as snapshot
        instrument : Instrument
            The instrument

        Returns
        -------
        OrderBookDeltas
            First delta (rest can be iterated)
        """
        timestamp_ns = int(data["ts"]) * 1_000_000
        ob_data = data["data"]

        deltas = []

        # Process bid updates
        for price_str, size_str in ob_data.get("b", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647

            order = BookOrder(
                side=OrderSide.BUY,
                price=price,
                size=size,
                order_id=order_id,
            )

            action = BookAction.UPDATE if size > 0 else BookAction.DELETE
            delta = OrderBookDelta(
                instrument_id=instrument.id,
                action=action,
                order=order,
                flags=0,
                sequence=0,
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
            deltas.append(delta)

        # Process ask updates
        for price_str, size_str in ob_data.get("a", []):
            price = Price(float(price_str), precision=instrument.price_precision)
            size = Quantity(float(size_str), precision=instrument.size_precision)
            order_id = hash(price_str) % 2147483647

            order = BookOrder(
                side=OrderSide.SELL,
                price=price,
                size=size,
                order_id=order_id,
            )

            action = BookAction.UPDATE if size > 0 else BookAction.DELETE
            delta = OrderBookDelta(
                instrument_id=instrument.id,
                action=action,
                order=order,
                flags=0,
                sequence=0,
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
            deltas.append(delta)

        # Return OrderBookDeltas containing all deltas
        return OrderBookDeltas(
            instrument_id=instrument.id,
            deltas=deltas,
        )
