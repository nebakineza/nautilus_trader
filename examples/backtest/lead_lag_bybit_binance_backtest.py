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

"""Backtest for Lead-Lag MM using Binance data as leader and Bybit execution follower.

This backtest reuses Bybit order book data for both venues to validate wiring.
Replace the Binance data source with true Binance L2 data for realistic testing.
"""

from __future__ import annotations

import argparse
import time
from decimal import Decimal
from pathlib import Path

from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb
from strategy.lead_lag_bybit_binance_mm_v003 import LeadLagMMv3, LeadLagMMv3Config


def create_btcusdt_bybit_instrument() -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId.from_str("BTCUSDT-SPOT.BYBIT"),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=1,
        size_precision=6,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=Quantity.from_str("1000.0"),
        min_quantity=Quantity.from_str("0.000001"),
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


def create_btcusdt_binance_instrument() -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId.from_str("BTCUSDT.BINANCE"),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=2,
        size_precision=6,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=Quantity.from_str("1000.0"),
        min_quantity=Quantity.from_str("0.000001"),
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


def build_engine(
    ofi_enabled: bool,
    ofi_max_bps: Decimal,
    use_modify_orders: bool,
) -> tuple[BacktestEngine, CurrencyPair, CurrencyPair, LeadLagMMv3]:
    config = BacktestEngineConfig(
        trader_id=TraderId("BACKTEST-LEADLAG-001"),
        logging=LoggingConfig(
            log_level="WARN",
            log_level_file="INFO",
            log_directory=".",
            log_file_name="nautilus.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)

    leader_instrument = create_btcusdt_binance_instrument()
    follower_instrument = create_btcusdt_bybit_instrument()

    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(0.0, USDT), Money(0.0, BTC)],
        book_type=BookType.L2_MBP,
    )
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(500.0, USDT), Money(0.01, BTC)],
        book_type=BookType.L2_MBP,
    )

    engine.add_instrument(leader_instrument)
    engine.add_instrument(follower_instrument)

    strategy = LeadLagMMv3(
        config=LeadLagMMv3Config(
            leader_instrument_id=leader_instrument.id,
            follower_instrument_id=follower_instrument.id,
            order_qty=Decimal("0.0001"),
            max_position_qty=Decimal("0.001"),
            spread_bps=Decimal("3.0"),
            quote_refresh_interval_ms=50,
            min_quote_lifetime_ms=50,
            min_requote_ticks=1,
            guard_threshold_bps=Decimal("8.0"),
            book_type=BookType.L2_MBP,
            book_depth=5,
            post_only=True,
            ofi_enabled=ofi_enabled,
            ofi_max_bps=ofi_max_bps,
            use_modify_orders=use_modify_orders,
        )
    )
    engine.add_strategy(strategy)
    return engine, leader_instrument, follower_instrument, strategy


def run_once(
    date_str: str,
    max_updates: int,
    data_dir: Path | None,
    depth: int,
    questdb: QuestDbConfig | None,
    ofi_enabled: bool,
    ofi_max_bps: Decimal,
    use_modify_orders: bool,
) -> None:
    engine, leader_instrument, follower_instrument, strategy = build_engine(
        ofi_enabled,
        ofi_max_bps,
        use_modify_orders,
    )
    start = time.time()
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
    elapsed = time.time() - start

    print(f"Loaded leader deltas: {leader_count:,}")
    print(f"Loaded follower deltas: {follower_count:,}")
    print(f"Load time: {elapsed:.1f}s")

    engine.run()

    account_report = engine.trader.generate_account_report(BYBIT_VENUE)
    fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()

    fills_count = len(fills_report) if fills_report is not None else 0
    positions_count = len(positions_report) if positions_report is not None else 0
    print(f"Fills: {fills_count}")
    print(f"Positions: {positions_count}")

    usdt_total = None
    btc_total = None

    if account_report is not None and not account_report.empty:
        report = account_report
        if "currency" in report.columns:
            usdt_rows = report[report["currency"] == "USDT"]
            btc_rows = report[report["currency"] == "BTC"]
        else:
            usdt_rows = report
            btc_rows = report.iloc[0:0]

        if not usdt_rows.empty:
            last = usdt_rows.iloc[-1]
            usdt_total = float(last.get("total")) if last.get("total") is not None else None
            free = last.get("free")
            locked = last.get("locked")
            print(f"USDT total: {usdt_total} | free: {free} | locked: {locked}")

        if not btc_rows.empty:
            last_btc = btc_rows.iloc[-1]
            btc_total = float(last_btc.get("total")) if last_btc.get("total") is not None else None

    equity = None
    if strategy.follower_mid is not None and usdt_total is not None:
        mid = float(strategy.follower_mid)
        if btc_total is None:
            btc_total = 0.0
        equity = usdt_total + (btc_total * mid)
        print(f"Equity (USDT + BTC@mid): {equity}")

    engine.reset()
    engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lead-lag backtest with incremental lengths.")
    parser.add_argument("--date", default="2026-01-15", help="Date string to load data for.")
    parser.add_argument("--max-updates", type=int, default=5_000, help="Max deltas per venue.")
    parser.add_argument(
        "--data-dir",
        default="data/ob_data",
        help="Base directory for order book data (default: data/ob_data).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=50,
        help="Order book depth used in file naming (default: 50).",
    )
    parser.add_argument(
        "--questdb",
        action="store_true",
        help="Load order book deltas from QuestDB instead of local files.",
    )
    parser.add_argument("--questdb-host", default="127.0.0.1", help="QuestDB host.")
    parser.add_argument("--questdb-port", type=int, default=9000, help="QuestDB HTTP port.")
    parser.add_argument("--questdb-table", default="orderbook_deltas", help="QuestDB table name.")
    parser.add_argument(
        "--questdb-step-seconds",
        type=int,
        default=300,
        help="QuestDB query window size in seconds.",
    )
    parser.add_argument(
        "--ofi",
        action="store_true",
        help="Enable local OFI skew in fair price.",
    )
    parser.add_argument(
        "--ofi-max-bps",
        type=Decimal,
        default=Decimal("3.0"),
        help="Maximum OFI skew in bps.",
    )
    parser.add_argument(
        "--no-modify",
        action="store_true",
        help="Disable modify orders (use cancel/replace).",
    )
    parser.add_argument("--start", type=int, default=5_000, help="Start max-updates for sweep.")
    parser.add_argument("--step", type=int, default=5_000, help="Increment for sweep lengths.")
    parser.add_argument("--runs", type=int, default=3, help="Number of incremental runs.")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run incremental sweep using start/step/runs.",
    )
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

    if args.sweep:
        for i in range(args.runs):
            max_updates = args.start + (args.step * i)
            print("\n" + "=" * 60)
            print(f"RUN {i + 1}/{args.runs} | max_updates={max_updates:,} | date={args.date}")
            print("=" * 60)
            run_once(
                args.date,
                max_updates,
                data_dir,
                args.depth,
                questdb,
                args.ofi,
                args.ofi_max_bps,
                not args.no_modify,
            )
    else:
        run_once(
            args.date,
            args.max_updates,
            data_dir,
            args.depth,
            questdb,
            args.ofi,
            args.ofi_max_bps,
            not args.no_modify,
        )


if __name__ == "__main__":
    main()
