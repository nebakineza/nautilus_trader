#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

"""Multi-symbol QuestDB backtest for Lead-Lag MM v003."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, ETH, SOL, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb
from strategy.lead_lag_bybit_binance_mm_v003 import LeadLagMMv3, LeadLagMMv3Config


@dataclass(frozen=True)
class PairSpec:
    symbol: str
    order_qty: Decimal
    max_position_qty: Decimal
    spread_bps: Decimal
    guard_threshold_bps: Decimal
    liquidity_high_qty: Decimal
    liquidity_low_qty: Decimal
    leader_id: str
    follower_id: str
    guard_id: str | None
    base_currency: object


PAIR_SPECS = [
    PairSpec(
        symbol="BTCUSDT",
        order_qty=Decimal("0.0001"),
        max_position_qty=Decimal("0.001"),
        spread_bps=Decimal("3.0"),
        guard_threshold_bps=Decimal("8.0"),
        liquidity_high_qty=Decimal("5.0"),
        liquidity_low_qty=Decimal("0.1"),
        leader_id="BTCUSDT.BINANCE",
        follower_id="BTCUSDT-SPOT.BYBIT",
        guard_id=None,
        base_currency=BTC,
    ),
    PairSpec(
        symbol="ETHUSDT",
        order_qty=Decimal("0.002"),
        max_position_qty=Decimal("0.02"),
        spread_bps=Decimal("3.5"),
        guard_threshold_bps=Decimal("8.0"),
        liquidity_high_qty=Decimal("80.0"),
        liquidity_low_qty=Decimal("5.0"),
        leader_id="ETHUSDT.BINANCE",
        follower_id="ETHUSDT-SPOT.BYBIT",
        guard_id="BTCUSDT.BINANCE",
        base_currency=ETH,
    ),
    PairSpec(
        symbol="SOLUSDT",
        order_qty=Decimal("0.1"),
        max_position_qty=Decimal("1.0"),
        spread_bps=Decimal("4.0"),
        guard_threshold_bps=Decimal("8.0"),
        liquidity_high_qty=Decimal("1000.0"),
        liquidity_low_qty=Decimal("50.0"),
        leader_id="SOLUSDT.BINANCE",
        follower_id="SOLUSDT-SPOT.BYBIT",
        guard_id="BTCUSDT.BINANCE",
        base_currency=SOL,
    ),
]


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
        f"where venue='{venue}' and \"symbol\"='{symbol}' limit 1"
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


def load_orderbook_data(
    engine: BacktestEngine,
    instrument: CurrencyPair,
    date_str: str,
    max_updates: int | None = None,
    data_dir: Path | None = None,
    depth: int = 50,
    questdb: QuestDbConfig | None = None,
    venue: str | None = None,
) -> int:
    count = 0
    symbol = str(instrument.raw_symbol)

    if questdb is not None:
        if venue is None:
            raise ValueError("QuestDB load requires venue tag")
        for deltas in load_questdb(questdb, instrument, venue=venue, symbol=symbol, date_str=date_str):
            engine.add_data([deltas])
            count += 1
            if max_updates and count >= max_updates:
                break
    else:
        base_dir = data_dir if data_dir is not None else Path("data/ob_data")
        ob_file = base_dir / f"{symbol}_Spot" / f"{date_str}_{symbol}_ob{depth}.data"
        if not ob_file.exists():
            raise FileNotFoundError(f"Order book file not found: {ob_file}")

        for delta in BybitOrderBookLoader.load_file(ob_file, instrument):
            engine.add_data([delta])
            count += 1
            if max_updates and count >= max_updates:
                break

    return count


def build_engine(questdb: QuestDbConfig | None) -> tuple[BacktestEngine, list[tuple[PairSpec, CurrencyPair, CurrencyPair]]]:
    config = BacktestEngineConfig(
        trader_id=TraderId("BACKTEST-LEADLAG-MULTI-003"),
        logging=LoggingConfig(
            log_level="ERROR",
            log_level_file="INFO",
            log_directory=".",
            log_file_name="nautilus_multi.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)

    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(0.0, USDT), Money(0.0, BTC), Money(0.0, ETH), Money(0.0, SOL)],
        book_type=BookType.L2_MBP,
    )
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(500.0, USDT), Money(0.01, BTC), Money(0.2, ETH), Money(1.0, SOL)],
        book_type=BookType.L2_MBP,
    )

    instruments: list[tuple[PairSpec, CurrencyPair, CurrencyPair]] = []
    for spec in PAIR_SPECS:
        if questdb is None:
            price_precision, size_precision = (2, 6)
        else:
            price_precision, size_precision = _infer_precision_from_questdb(questdb, venue="BINANCE", symbol=spec.symbol)
        min_order_qty = spec.order_qty * Decimal("0.25")
        size_precision = max(size_precision, _precision_from_decimal(min_order_qty))
        leader_instrument = _create_instrument(spec.leader_id, spec.symbol, spec.base_currency, price_precision, size_precision)
        follower_instrument = _create_instrument(spec.follower_id, spec.symbol, spec.base_currency, price_precision, size_precision)
        engine.add_instrument(leader_instrument)
        engine.add_instrument(follower_instrument)
        instruments.append((spec, leader_instrument, follower_instrument))

        strategy = LeadLagMMv3(
            config=LeadLagMMv3Config(
                leader_instrument_id=leader_instrument.id,
                follower_instrument_id=follower_instrument.id,
                global_guard_id=InstrumentId.from_str(spec.guard_id) if spec.guard_id else None,
                order_qty=spec.order_qty,
                max_position_qty=spec.max_position_qty,
                spread_bps=spec.spread_bps,
                guard_threshold_bps=spec.guard_threshold_bps,
                global_guard_threshold_bps=Decimal("15.0"),
                global_guard_window_ms=500,
                quote_refresh_interval_ms=30,
                min_quote_lifetime_ms=20,
                min_requote_ticks=1,
                book_type=BookType.L2_MBP,
                book_depth=50,
                post_only=True,
                liquidity_high_qty=spec.liquidity_high_qty,
                liquidity_low_qty=spec.liquidity_low_qty,
                min_order_qty=spec.order_qty * Decimal("0.25"),
                max_order_qty=spec.order_qty * Decimal("2.0"),
                log_guard_events=False,
            )
        )
        engine.add_strategy(strategy)

    return engine, instruments


def run_once(
    date_str: str,
    max_updates: int,
    data_dir: Path | None,
    depth: int,
    questdb: QuestDbConfig | None,
) -> None:
    engine, instruments = build_engine(questdb)
    start = time.time()

    for spec, leader_instrument, follower_instrument in instruments:
        leader_count = load_orderbook_data(
            engine,
            leader_instrument,
            date_str,
            max_updates,
            data_dir,
            depth,
            questdb=questdb,
            venue="BINANCE",
        )
        follower_count = load_orderbook_data(
            engine,
            follower_instrument,
            date_str,
            max_updates,
            data_dir,
            depth,
            questdb=questdb,
            venue="BYBIT",
        )
        print(f"{spec.symbol} leader deltas: {leader_count} | follower deltas: {follower_count}")

    print(f"Load time: {time.time() - start:.1f}s")

    engine.run()

    account = engine.cache.account_for_venue(BYBIT_VENUE)
    if account:
        totals = account.balances_total()
        free = account.balances_free()
        print(
            "USDT total:", totals.get(USDT),
            "| free:", free.get(USDT),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-symbol Lead-Lag MM v003 backtest")
    parser.add_argument("--date", type=str, required=True, help="Date in YYYY-MM-DD format.")
    parser.add_argument("--max-updates", type=int, default=50000, help="Max deltas per symbol per venue.")
    parser.add_argument("--data-dir", type=str, default="data/ob_data", help="Order book data directory.")
    parser.add_argument("--depth", type=int, default=50, help="Order book depth for file naming.")
    parser.add_argument("--questdb", action="store_true", help="Load order book deltas from QuestDB.")
    parser.add_argument("--questdb-host", default="127.0.0.1", help="QuestDB host.")
    parser.add_argument("--questdb-port", type=int, default=9000, help="QuestDB HTTP port.")
    parser.add_argument("--questdb-table", default="orderbook_deltas", help="QuestDB table name.")
    parser.add_argument("--questdb-step-seconds", type=int, default=300, help="QuestDB query window size in seconds.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    questdb = None
    if args.questdb:
        questdb = QuestDbConfig(
            host=args.questdb_host,
            port=args.questdb_port,
            table=args.questdb_table,
            step_seconds=args.questdb_step_seconds,
        )
    run_once(args.date, args.max_updates, data_dir, args.depth, questdb)


if __name__ == "__main__":
    main()
