from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas, TradeTick, OrderBookDelta
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class BybitCaptureConfig(StrategyConfig, frozen=True, kw_only=True):
    instrument_ids: list[InstrumentId]
    book_depth: int = 50
    ob_data_dir: str = "/home/ubuntu/trading/data/ob_data_live"
    tick_data_dir: str = "/home/ubuntu/trading/data/tick_data"


@dataclass
class _FileHandle:
    path: Path
    handle: object
    current_date: str
    next_id: int


class BybitObTickCapture(Strategy):
    def __init__(self, config: BybitCaptureConfig) -> None:
        super().__init__(config)
        self._ob_handles: dict[str, _FileHandle] = {}
        self._tick_handles: dict[str, _FileHandle] = {}

    def on_start(self) -> None:
        for instrument_id in self.config.instrument_ids:
            self.subscribe_order_book_deltas(
                instrument_id=instrument_id,
                depth=self.config.book_depth,
            )
            self.subscribe_trade_ticks(instrument_id)

        self.log.info(
            f"Capture started for: {[str(i) for i in self.config.instrument_ids]}",
            LogColor.CYAN,
        )

    def on_stop(self) -> None:
        for handle in list(self._ob_handles.values()) + list(self._tick_handles.values()):
            try:
                handle.handle.close()
            except Exception:
                pass

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        ts_ms = self._ts_event_ms(deltas.ts_event)
        if ts_ms is None:
            return

        symbol = self._base_symbol(deltas.instrument_id)
        date_str = self._date_str(ts_ms)
        ob_handle = self._get_ob_handle(symbol, date_str)

        bids: list[list[str]] = []
        asks: list[list[str]] = []
        for delta in deltas.deltas:
            d = OrderBookDelta.to_dict(delta)
            order = d.get("order")
            if not order:
                continue
            side = order.get("side")
            price = order.get("price")
            size = order.get("size")
            action = d.get("action")
            if action in ("DELETE", "CLEAR"):
                size = "0"
            if side == "BUY":
                bids.append([price, size])
            elif side == "SELL":
                asks.append([price, size])

        record = {
            "type": "snapshot" if deltas.is_snapshot else "delta",
            "ts": ts_ms,
            "data": {"s": f"{symbol}-SPOT", "b": bids, "a": asks},
        }
        ob_handle.handle.write(json.dumps(record) + "\n")

    def on_trade_tick(self, tick: TradeTick) -> None:
        ts_ms = self._ts_event_ms(tick.ts_event)
        if ts_ms is None:
            return

        symbol = self._base_symbol(tick.instrument_id)
        date_str = self._date_str(ts_ms)
        tick_handle = self._get_tick_handle(symbol, date_str)

        d = TradeTick.to_dict(tick)
        price = d.get("price")
        size = d.get("size")
        side = d.get("aggressor_side", "UNKNOWN").lower()
        if side not in ("buy", "sell"):
            side = "unknown"

        line = f"{tick_handle.next_id},{ts_ms},{price},{size},{side},0\n"
        tick_handle.handle.write(line)
        tick_handle.next_id += 1

    def _get_ob_handle(self, symbol: str, date_str: str) -> _FileHandle:
        handle = self._ob_handles.get(symbol)
        if handle and handle.current_date == date_str:
            return handle

        if handle:
            handle.handle.close()

        dir_path = Path(self.config.ob_data_dir) / f"{symbol}_Spot"
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{date_str}_{symbol}_bybit_ob{self.config.book_depth}.data"
        fp = file_path.open("a", encoding="utf-8")
        new_handle = _FileHandle(path=file_path, handle=fp, current_date=date_str, next_id=1)
        self._ob_handles[symbol] = new_handle
        return new_handle

    def _get_tick_handle(self, symbol: str, date_str: str) -> _FileHandle:
        handle = self._tick_handles.get(symbol)
        if handle and handle.current_date == date_str:
            return handle

        if handle:
            handle.handle.close()

        dir_path = Path(self.config.tick_data_dir) / f"{symbol}_Spot"
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{symbol}_{date_str}.csv"

        is_new = not file_path.exists()
        fp = file_path.open("a", encoding="utf-8")
        if is_new:
            fp.write("id,timestamp,price,volume,side,rpi\n")

        new_handle = _FileHandle(path=file_path, handle=fp, current_date=date_str, next_id=1)
        self._tick_handles[symbol] = new_handle
        return new_handle

    @staticmethod
    def _date_str(ts_ms: int) -> str:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _ts_event_ms(ts_event: int | None) -> int | None:
        if ts_event is None:
            return None
        ts_value = int(ts_event)
        if ts_value < 10_000_000_000:
            return ts_value * 1000
        if ts_value < 10_000_000_000_000:
            return ts_value
        return ts_value // 1_000_000

    @staticmethod
    def _base_symbol(instrument_id: InstrumentId) -> str:
        return instrument_id.value.split("-")[0]
