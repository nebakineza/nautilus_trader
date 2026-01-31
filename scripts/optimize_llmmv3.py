#!/usr/bin/env python3
"""Optuna optimizer for Lead-Lag MM v003 parameters (per-symbol studies)."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from datetime import datetime

import optuna

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
class SymbolSpec:
    symbol: str
    leader_id: str
    follower_id: str
    base_currency: object
    order_qty: Decimal
    max_position_qty: Decimal
    spread_min: float
    spread_max: float
    ofi_min: float
    ofi_max: float
    risk_min: float
    risk_max: float


SYMBOL_SPECS = {
    "BTCUSDT": SymbolSpec(
        symbol="BTCUSDT",
        leader_id="BTCUSDT.BINANCE",
        follower_id="BTCUSDT-SPOT.BYBIT",
        base_currency=BTC,
        order_qty=Decimal("0.0001"),
        max_position_qty=Decimal("0.001"),
        spread_min=2.0,
        spread_max=15.0,
        ofi_min=1.0,
        ofi_max=5.0,
        risk_min=0.1,
        risk_max=2.0,
    ),
    "ETHUSDT": SymbolSpec(
        symbol="ETHUSDT",
        leader_id="ETHUSDT.BINANCE",
        follower_id="ETHUSDT-SPOT.BYBIT",
        base_currency=ETH,
        order_qty=Decimal("0.002"),
        max_position_qty=Decimal("0.02"),
        spread_min=5.0,
        spread_max=25.0,
        ofi_min=2.0,
        ofi_max=10.0,
        risk_min=0.5,
        risk_max=3.0,
    ),
    "SOLUSDT": SymbolSpec(
        symbol="SOLUSDT",
        leader_id="SOLUSDT.BINANCE",
        follower_id="SOLUSDT-SPOT.BYBIT",
        base_currency=SOL,
        order_qty=Decimal("0.1"),
        max_position_qty=Decimal("1.0"),
        spread_min=15.0,
        spread_max=50.0,
        ofi_min=5.0,
        ofi_max=25.0,
        risk_min=1.0,
        risk_max=5.0,
    ),
}


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


def _load_orderbook_data(
    engine: BacktestEngine,
    instrument: CurrencyPair,
    date_str: str,
    max_updates: int | None,
    data_dir: Path | None,
    depth: int,
    questdb: QuestDbConfig | None,
    venue: str | None,
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


def _build_engine(
    spec: SymbolSpec,
    questdb: QuestDbConfig | None,
    spread_bps: float,
    ofi_max_bps: float,
    risk_aversion: float,
    min_profit_bps: float,
) -> tuple[BacktestEngine, CurrencyPair, CurrencyPair, LeadLagMMv3]:
    log_dir = Path("outputs/optuna/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    config = BacktestEngineConfig(
        trader_id=TraderId(f"OPTIMIZE-LLMMV3-{spec.symbol}"),
        logging=LoggingConfig(
            log_level="ERROR",
            log_level_file="INFO",
            log_directory=str(log_dir),
            log_file_name=f"optuna_{spec.symbol.lower()}.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)

    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(0.0, USDT), Money(0.0, spec.base_currency)],
        book_type=BookType.L2_MBP,
    )
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(500.0, USDT), Money(Decimal("0.0"), spec.base_currency)],
        book_type=BookType.L2_MBP,
    )

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

    strategy = LeadLagMMv3(
        config=LeadLagMMv3Config(
            leader_instrument_id=leader_instrument.id,
            follower_instrument_id=follower_instrument.id,
            order_qty=spec.order_qty,
            max_position_qty=spec.max_position_qty,
            spread_bps=Decimal(f"{spread_bps:.2f}"),
            guard_threshold_bps=Decimal("8.0"),
            global_guard_threshold_bps=Decimal("15.0"),
            global_guard_window_ms=500,
            quote_refresh_interval_ms=30,
            min_quote_lifetime_ms=20,
            min_requote_ticks=1,
            book_type=BookType.L2_MBP,
            book_depth=50,
            post_only=True,
            ofi_enabled=True,
            ofi_max_bps=Decimal(f"{ofi_max_bps:.2f}"),
            risk_aversion=risk_aversion,
            min_profit_bps=Decimal(f"{min_profit_bps:.2f}"),
            min_order_qty=min_order_qty,
            max_order_qty=spec.order_qty * Decimal("2.0"),
            log_guard_events=False,
            log_markout_events=False,
        )
    )

    engine.add_strategy(strategy)
    return engine, leader_instrument, follower_instrument, strategy


def _objective_factory(
    spec: SymbolSpec,
    date_str: str,
    max_updates: int,
    data_dir: Path | None,
    depth: int,
    questdb: QuestDbConfig | None,
    objective: str,
):
    def objective_fn(trial: optuna.Trial) -> float:
        spread_bps = trial.suggest_float("spread_bps", spec.spread_min, spec.spread_max, step=1.0)
        ofi_max_bps = trial.suggest_float("ofi_max_bps", spec.ofi_min, spec.ofi_max, step=1.0)
        risk_aversion = trial.suggest_float("risk_aversion", spec.risk_min, spec.risk_max, step=0.1)
        min_profit_bps = trial.suggest_float("min_profit_bps", 1.0, 10.0, step=1.0)

        engine, leader_instrument, follower_instrument, strategy = _build_engine(
            spec,
            questdb,
            spread_bps,
            ofi_max_bps,
            risk_aversion,
            min_profit_bps,
        )

        _load_orderbook_data(
            engine,
            leader_instrument,
            date_str,
            max_updates,
            data_dir,
            depth,
            questdb=questdb,
            venue="BINANCE",
        )
        _load_orderbook_data(
            engine,
            follower_instrument,
            date_str,
            max_updates,
            data_dir,
            depth,
            questdb=questdb,
            venue="BYBIT",
        )

        engine.run()

        account_report = engine.trader.generate_account_report(BYBIT_VENUE)
        usdt_total = None
        base_total = None

        if account_report is not None and not account_report.empty:
            report = account_report
            if "currency" in report.columns:
                usdt_rows = report[report["currency"] == "USDT"]
                base_rows = report[report["currency"] == spec.base_currency.code]
            else:
                usdt_rows = report
                base_rows = report.iloc[0:0]

            if not usdt_rows.empty:
                last = usdt_rows.iloc[-1]
                usdt_total = float(last.get("total")) if last.get("total") is not None else None

            if not base_rows.empty:
                last_base = base_rows.iloc[-1]
                base_total = float(last_base.get("total")) if last_base.get("total") is not None else None

        if base_total is None:
            base_total = 0.0

        if usdt_total is None:
            engine.reset()
            engine.dispose()
            return float("-inf")

        equity = None
        if strategy.follower_mid is not None:
            mid = float(strategy.follower_mid)
            equity = usdt_total + (base_total * mid)

        engine.reset()
        engine.dispose()

        if equity is None:
            return float("-inf")

        if objective == "pnl":
            return equity - 500.0
        return equity

    return objective_fn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optuna optimizer for LLMMv3")
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    parser.add_argument("--max-updates", type=int, default=50000, help="Max deltas per venue")
    parser.add_argument("--data-dir", default="data/ob_data", help="Order book data directory")
    parser.add_argument("--depth", type=int, default=50, help="Order book depth")
    parser.add_argument("--questdb", action="store_true", help="Load order book deltas from QuestDB")
    parser.add_argument("--questdb-host", default="127.0.0.1", help="QuestDB host")
    parser.add_argument("--questdb-port", type=int, default=9000, help="QuestDB HTTP port")
    parser.add_argument("--questdb-table", default="orderbook_deltas", help="QuestDB table name")
    parser.add_argument("--questdb-step-seconds", type=int, default=300, help="QuestDB step seconds")
    parser.add_argument("--n-trials", type=int, default=100, help="Optuna trials per symbol")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT", help="Comma-separated symbols")
    parser.add_argument("--objective", default="equity", choices=["equity", "pnl"], help="Objective metric")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    output_root = Path("outputs/optuna")
    studies_dir = output_root / "studies"
    output_root.mkdir(parents=True, exist_ok=True)
    studies_dir.mkdir(parents=True, exist_ok=True)
    questdb = None
    if args.questdb:
        questdb = QuestDbConfig(
            host=args.questdb_host,
            port=args.questdb_port,
            table=args.questdb_table,
            step_seconds=args.questdb_step_seconds,
        )

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    for symbol in symbols:
        spec = SYMBOL_SPECS.get(symbol)
        if spec is None:
            raise ValueError(f"Unsupported symbol: {symbol}")

        print("\n" + "=" * 60)
        print(f"Optimizing {symbol} ({args.n_trials} trials)")
        print("=" * 60)

        study = optuna.create_study(direction="maximize", study_name=symbol)
        objective_fn = _objective_factory(
            spec,
            args.date,
            args.max_updates,
            data_dir,
            args.depth,
            questdb,
            args.objective,
        )

        start = time.time()
        study.optimize(objective_fn, n_trials=args.n_trials)
        elapsed = time.time() - start

        print("\nBest parameters:")
        for key, value in study.best_params.items():
            print(f"  {key}: {value}")
        print(f"Best objective: {study.best_value:.6f}")
        print(f"Elapsed: {elapsed:.1f}s")

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        summary_path = studies_dir / f"{symbol}_{timestamp}_best.json"
        summary = {
            "symbol": symbol,
            "timestamp": timestamp,
            "objective": args.objective,
            "best_value": study.best_value,
            "best_params": study.best_params,
            "n_trials": args.n_trials,
            "date": args.date,
            "max_updates": args.max_updates,
        }
        summary_path.write_text(json.dumps(summary, indent=2))
        print(f"Summary written to: {summary_path}")


if __name__ == "__main__":
    main()
