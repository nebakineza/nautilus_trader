#!/usr/bin/env python3
"""LLMMv3 Primer multi-pair backtest using QuestDB or local OB files.

Supports SOL/DOGE/AVAX with Binance leader + Bybit follower data.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import hmac
import hashlib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

from examples.backtest.binance_orderbook_loader import BinanceOrderBookLoader
from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb
from nautilus_trader.backtest.models import (
    BestPriceFillModel,
    CompetitionAwareFillModel,
    LatencyModel,
    LimitOrderPartialFillModel,
    OneTickSlippageFillModel,
    SizeAwareFillModel,
    ThreeTierFillModel,
    TwoTierFillModel,
    VolumeSensitiveFillModel,
)
from strategy.lead_lag_bybit_binance_mm_v003_primer import LeadLagMMv3Primer, LeadLagMMv3PrimerConfig


@dataclass(frozen=True)
class PairConfig:
    symbol: str
    order_qty: Decimal
    min_order_qty: Decimal
    max_position_qty: Decimal
    guard_threshold_bps: Decimal
    spread_bps: Decimal
    min_profit_bps: Decimal
    refresh_interval_ms: int
    refresh_offset_ms: int
    liquidity_high_qty: Decimal
    liquidity_low_qty: Decimal
    ofi_enabled: bool
    ofi_max_bps: Decimal
    min_quote_lifetime_ms: int


DEFAULT_PAIRS = [
    PairConfig(
        symbol="SOLUSDT",
        order_qty=Decimal("0.2"),
        min_order_qty=Decimal("0.2"),
        max_position_qty=Decimal("2.0"),
        guard_threshold_bps=Decimal("10.0"),
        spread_bps=Decimal("24.0"),
        min_profit_bps=Decimal("3.0"),
        refresh_interval_ms=3000,
        refresh_offset_ms=0,
        liquidity_high_qty=Decimal("1000.0"),
        liquidity_low_qty=Decimal("50.0"),
        ofi_enabled=True,
        ofi_max_bps=Decimal("3.0"),
        min_quote_lifetime_ms=1000,
    ),
    PairConfig(
        symbol="DOGEUSDT",
        order_qty=Decimal("150"),
        min_order_qty=Decimal("150"),
        max_position_qty=Decimal("1500.0"),
        guard_threshold_bps=Decimal("12.0"),
        spread_bps=Decimal("26.0"),
        min_profit_bps=Decimal("2.0"),
        refresh_interval_ms=5000,
        refresh_offset_ms=33,
        liquidity_high_qty=Decimal("200000"),
        liquidity_low_qty=Decimal("20000"),
        ofi_enabled=True,
        ofi_max_bps=Decimal("5.0"),
        min_quote_lifetime_ms=2000,
    ),
    PairConfig(
        symbol="AVAXUSDT",
        order_qty=Decimal("1.0"),
        min_order_qty=Decimal("1.0"),
        max_position_qty=Decimal("10.0"),
        guard_threshold_bps=Decimal("12.0"),
        spread_bps=Decimal("24.0"),
        min_profit_bps=Decimal("2.0"),
        refresh_interval_ms=5000,
        refresh_offset_ms=66,
        liquidity_high_qty=Decimal("5000.0"),
        liquidity_low_qty=Decimal("200.0"),
        ofi_enabled=True,
        ofi_max_bps=Decimal("5.0"),
        min_quote_lifetime_ms=2000,
    ),
]


class WapLedger:
    def __init__(self) -> None:
        self.inventory = Decimal("0")
        self.wap = Decimal("0")
        self.realized_pnl = Decimal("0")
        self.fees_paid = Decimal("0")

    def on_fill(self, side: str, price: Decimal, qty: Decimal, fee: Decimal) -> None:
        self.fees_paid += fee
        self.realized_pnl -= fee

        if side == "BUY":
            if self.inventory >= 0:
                total_cost = (self.inventory * self.wap) + (qty * price)
                self.inventory += qty
                if self.inventory != 0:
                    self.wap = total_cost / self.inventory
            else:
                remaining_short = abs(self.inventory)
                if qty <= remaining_short:
                    pnl = (self.wap - price) * qty
                    self.realized_pnl += pnl
                    self.inventory += qty
                else:
                    pnl = (self.wap - price) * remaining_short
                    self.realized_pnl += pnl
                    excess_qty = qty - remaining_short
                    self.inventory = excess_qty
                    self.wap = price
        elif side == "SELL":
            if self.inventory <= 0:
                total_cost = (abs(self.inventory) * self.wap) + (qty * price)
                self.inventory -= qty
                if self.inventory != 0:
                    self.wap = total_cost / abs(self.inventory)
            else:
                if qty <= self.inventory:
                    pnl = (price - self.wap) * qty
                    self.realized_pnl += pnl
                    self.inventory -= qty
                else:
                    pnl = (price - self.wap) * self.inventory
                    self.realized_pnl += pnl
                    excess_qty = qty - self.inventory
                    self.inventory = -excess_qty
                    self.wap = price


def _compute_wap_from_fills(fills_report) -> dict:
    ledgers: dict[str, WapLedger] = {}
    last_price: dict[str, Decimal] = {}

    def pick(row, *names):
        for name in names:
            if name in row and row[name] is not None:
                return row[name]
        return None

    for _, row in fills_report.iterrows():
        inst = pick(row, "instrument_id", "instrument", "symbol")
        side = pick(row, "order_side", "side")
        qty = pick(row, "last_qty", "qty", "quantity", "fill_qty")
        price = pick(row, "last_px", "price", "fill_price")
        fee = pick(row, "commission", "fee", "commission_amount")

        if inst is None or side is None or qty is None or price is None:
            continue

        inst = str(inst)
        side = str(side).upper()
        qty = Decimal(str(qty))
        price = Decimal(str(price))
        fee_val = Decimal(str(fee)) if fee is not None else Decimal("0")

        ledger = ledgers.setdefault(inst, WapLedger())
        ledger.on_fill(side, price, qty, fee_val)
        last_price[inst] = price

    summary: dict[str, dict[str, str]] = {}
    total_realized = Decimal("0")
    total_unrealized = Decimal("0")
    total_fees = Decimal("0")

    for inst, ledger in ledgers.items():
        mark = last_price.get(inst, Decimal("0"))
        unrealized = Decimal("0")
        if ledger.inventory > 0:
            unrealized = (mark - ledger.wap) * ledger.inventory
        elif ledger.inventory < 0:
            unrealized = (ledger.wap - mark) * abs(ledger.inventory)

        total_realized += ledger.realized_pnl
        total_unrealized += unrealized
        total_fees += ledger.fees_paid

        summary[inst] = {
            "inventory": str(ledger.inventory),
            "wap": str(ledger.wap),
            "realized_pnl": str(ledger.realized_pnl),
            "unrealized_pnl": str(unrealized),
            "total_pnl": str(ledger.realized_pnl + unrealized),
            "fees_paid": str(ledger.fees_paid),
        }

    summary["TOTAL"] = {
        "realized_pnl": str(total_realized),
        "unrealized_pnl": str(total_unrealized),
        "total_pnl": str(total_realized + total_unrealized),
        "fees_paid": str(total_fees),
    }

    return summary


def _precision_from_tick(tick: Decimal) -> int:
    if tick <= 0:
        return 6
    exp = abs(tick.as_tuple().exponent)
    return max(0, exp)


def _query_questdb_json(cfg: QuestDbConfig, sql: str) -> dict:
    url = cfg.base_url + urllib.parse.quote_plus(sql)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def _fetch_bybit_balances(assets: list[str]) -> dict[str, Decimal]:
    api_key = os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("BYBIT_API_KEY/BYBIT_API_SECRET not set")

    endpoint = "/v5/account/wallet-balance"
    params = {"accountType": "UNIFIED"}
    query = urllib.parse.urlencode(params)
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    payload = ts + api_key + recv_window + query
    sig = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    url = "https://api.bybit.com" + endpoint + "?" + query

    req = urllib.request.Request(url)
    req.add_header("X-BAPI-API-KEY", api_key)
    req.add_header("X-BAPI-SIGN", sig)
    req.add_header("X-BAPI-SIGN-TYPE", "2")
    req.add_header("X-BAPI-TIMESTAMP", ts)
    req.add_header("X-BAPI-RECV-WINDOW", recv_window)

    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode())

    balances: dict[str, Decimal] = {}
    coins = payload.get("result", {}).get("list", [{}])[0].get("coin", [])
    asset_set = {a.upper() for a in assets}
    for coin in coins:
        symbol = coin.get("coin", "").upper()
        if symbol in asset_set:
            balances[symbol] = Decimal(str(coin.get("walletBalance", "0")))

    return balances


def _load_instrument_meta(
    cfg: QuestDbConfig, venue: str, symbol: str
) -> dict[str, Decimal] | None:
    sql = (
        "select price_tick, qty_step, min_qty, max_qty, min_notional "
        "from instrument_meta "
        f"where venue='{venue}' and \"symbol\"='{symbol}' "
        "order by timestamp desc limit 1"
    )
    payload = _query_questdb_json(cfg, sql)
    dataset = payload.get("dataset", [])
    if not dataset:
        return None
    price_tick, qty_step, min_qty, max_qty, min_notional = dataset[0]
    return {
        "price_tick": Decimal(str(price_tick)),
        "qty_step": Decimal(str(qty_step)),
        "min_qty": Decimal(str(min_qty)),
        "max_qty": Decimal(str(max_qty)),
        "min_notional": Decimal(str(min_notional)),
    }


def _create_currency(symbol: str) -> Currency:
    return Currency.from_internal_map(symbol)


def _create_instrument(
    venue_suffix: str,
    symbol: str,
    meta: dict[str, Decimal] | None,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> CurrencyPair:
    base = symbol.replace("USDT", "")
    base_currency = _create_currency(base)

    price_tick = meta.get("price_tick") if meta else None
    qty_step = meta.get("qty_step") if meta else None
    min_qty = meta.get("min_qty") if meta else None
    max_qty = meta.get("max_qty") if meta else None
    min_notional = meta.get("min_notional") if meta else None

    price_tick = price_tick if price_tick and price_tick > 0 else Decimal("0.01")
    qty_step = qty_step if qty_step and qty_step > 0 else Decimal("0.0001")

    price_precision = _precision_from_tick(price_tick)
    size_precision = _precision_from_tick(qty_step)

    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}{venue_suffix}"),
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=USDT,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=Price.from_str(str(price_tick)),
        size_increment=Quantity.from_str(str(qty_step)),
        lot_size=None,
        max_quantity=Quantity.from_str(str(max_qty)) if max_qty else None,
        min_quantity=Quantity.from_str(str(min_qty)) if min_qty else None,
        max_notional=None,
        min_notional=None if min_notional is None else Money(float(min_notional), USDT),
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )


def _load_deltas(
    engine: BacktestEngine,
    instrument: CurrencyPair,
    symbol: str,
    venue: str,
    date_str: str,
    questdb: QuestDbConfig | None,
    data_dir: Path | None,
    depth: int,
    max_updates: int | None,
    batch_size: int,
) -> int:
    count = 0
    batch = []

    if questdb is not None:
        for deltas in load_questdb(questdb, instrument, venue=venue, symbol=symbol, date_str=date_str):
            batch.append(deltas)
            count += 1
            if max_updates and count >= max_updates:
                break
            if len(batch) >= batch_size:
                engine.add_data(batch)
                batch = []
    else:
        base_dir = data_dir if data_dir is not None else Path("data/ob_data")
        ob_file = base_dir / f"{symbol}_Spot" / f"{date_str}_{symbol}_ob{depth}.data"
        if not ob_file.exists():
            raise FileNotFoundError(f"Order book file not found: {ob_file}")
        loader = BinanceOrderBookLoader if venue.upper() == "BINANCE" else BybitOrderBookLoader
        for delta in loader.load_file(ob_file, instrument):
            batch.append(delta)
            count += 1
            if max_updates and count >= max_updates:
                break
            if len(batch) >= batch_size:
                engine.add_data(batch)
                batch = []

    if batch:
        engine.add_data(batch)

    return count


def _build_fill_model(name: str, liquidity_factor: float) -> object:
    name = name.lower()
    if name == "best":
        return BestPriceFillModel()
    if name == "competition":
        return CompetitionAwareFillModel(liquidity_factor=liquidity_factor)
    if name == "partial":
        return LimitOrderPartialFillModel()
    if name == "one-tick":
        return OneTickSlippageFillModel()
    if name == "size-aware":
        return SizeAwareFillModel()
    if name == "volume-sensitive":
        return VolumeSensitiveFillModel()
    if name == "two-tier":
        return TwoTierFillModel()
    if name == "three-tier":
        return ThreeTierFillModel()
    return BestPriceFillModel()


def _build_latency_model(latency_ms: int | None) -> LatencyModel | None:
    if latency_ms is None:
        return None
    nanos = int(latency_ms) * 1_000_000
    return LatencyModel(base_latency_nanos=nanos)


def _fee_profile(profile: str, mnt_discount: bool) -> tuple[Decimal, Decimal]:
    # Spot Crypto-Crypto fees from Bybit Trading Fee Structure (2026-01-10)
    profiles = {
        "default": (Decimal("0.001"), Decimal("0.001")),
        "vip0": (Decimal("0.001"), Decimal("0.001")),
        "vip1": (Decimal("0.000675"), Decimal("0.0008")),
        "vip2": (Decimal("0.00065"), Decimal("0.000775")),
        "vip3": (Decimal("0.000625"), Decimal("0.00075")),
        "vip4": (Decimal("0.0005"), Decimal("0.0006")),
        "vip5": (Decimal("0.0004"), Decimal("0.0005")),
        "supreme": (Decimal("0.0003"), Decimal("0.00045")),
    }
    maker, taker = profiles.get(profile, profiles["default"])
    if mnt_discount:
        maker *= Decimal("0.75")
        taker *= Decimal("0.75")
    return maker, taker


def _apply_mm_rebate(maker_fee: Decimal, mm_tier: str) -> Decimal:
    # Spot Market Maker rebate (maker fee rebate), taker fee based on Pro tier
    rebates = {
        "mm1": Decimal("-0.00001"),
        "mm2": Decimal("-0.00005"),
        "mm3": Decimal("-0.000075"),
    }
    rebate = rebates.get(mm_tier, Decimal("0"))
    return maker_fee + rebate


def _apply_profile(args: argparse.Namespace) -> None:
    if not args.profile:
        return
    profile = args.profile.lower()
    if profile == "accuracy":
        if args.fill_model is None:
            args.fill_model = "competition"
        if args.latency_ms is None:
            args.latency_ms = 5
        if args.liquidity_consumption is None:
            args.liquidity_consumption = True
        if args.batch_size is None:
            args.batch_size = 1000
    elif profile == "balanced":
        if args.fill_model is None:
            args.fill_model = "competition"
        if args.latency_ms is None:
            args.latency_ms = 2
        if args.liquidity_consumption is None:
            args.liquidity_consumption = True
        if args.batch_size is None:
            args.batch_size = 2500
    elif profile == "speed":
        if args.fill_model is None:
            args.fill_model = "best"
        if args.latency_ms is None:
            args.latency_ms = None
        if args.liquidity_consumption is None:
            args.liquidity_consumption = False
        if args.batch_size is None:
            args.batch_size = 5000


def build_engine(
    pairs: Iterable[PairConfig],
    questdb: QuestDbConfig | None,
    starting_usdt: Decimal,
    fill_model_name: str,
    liquidity_factor: float,
    latency_ms: int | None,
    liquidity_consumption: bool,
    maker_fee: Decimal,
    taker_fee: Decimal,
    starting_balances: dict[str, Decimal] | None,
) -> tuple[BacktestEngine, dict[str, CurrencyPair], dict[str, CurrencyPair]]:
    config = BacktestEngineConfig(
        trader_id=TraderId("BACKTEST-LLMMV3-PRIMER"),
        logging=LoggingConfig(
            log_level="WARN",
            log_level_file="INFO",
            log_directory=".",
            log_file_name="nautilus.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)

    base_currencies = []
    leader_instruments: dict[str, CurrencyPair] = {}
    follower_instruments: dict[str, CurrencyPair] = {}

    fill_model = _build_fill_model(fill_model_name, liquidity_factor)
    latency_model = _build_latency_model(latency_ms)

    for pair in pairs:
        meta_bybit = _load_instrument_meta(questdb, "BYBIT", pair.symbol) if questdb else None
        meta_binance = _load_instrument_meta(questdb, "BINANCE", pair.symbol) if questdb else None

        follower = _create_instrument("-SPOT.BYBIT", pair.symbol, meta_bybit, maker_fee, taker_fee)
        leader = _create_instrument(".BINANCE_SPOT", pair.symbol, meta_binance, maker_fee, taker_fee)

        leader_instruments[pair.symbol] = leader
        follower_instruments[pair.symbol] = follower
        base_currencies.append(follower.base_currency)

    balances = starting_balances or {}
    starting_balances_list = [Money(float(balances.get("USDT", starting_usdt)), USDT)]
    for cur in base_currencies:
        starting_balances_list.append(Money(float(balances.get(cur.code, Decimal("0"))), cur))

    engine.add_venue(
        venue=Venue("BINANCE_SPOT"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances_list,
        book_type=BookType.L2_MBP,
        fill_model=fill_model,
        latency_model=latency_model,
        liquidity_consumption=liquidity_consumption,
    )
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances_list,
        book_type=BookType.L2_MBP,
        fill_model=fill_model,
        latency_model=latency_model,
        liquidity_consumption=liquidity_consumption,
    )

    for pair in pairs:
        leader = leader_instruments[pair.symbol]
        follower = follower_instruments[pair.symbol]
        engine.add_instrument(leader)
        engine.add_instrument(follower)

    for pair in pairs:
        leader = leader_instruments[pair.symbol]
        follower = follower_instruments[pair.symbol]

        strategy = LeadLagMMv3Primer(
            config=LeadLagMMv3PrimerConfig(
                leader_instrument_id=leader.id,
                follower_instrument_id=follower.id,
                order_qty=pair.order_qty,
                max_position_qty=pair.max_position_qty,
                spread_bps=pair.spread_bps,
                guard_threshold_bps=pair.guard_threshold_bps,
                quote_refresh_interval_ms=pair.refresh_interval_ms,
                quote_refresh_offset_ms=pair.refresh_offset_ms,
                min_quote_lifetime_ms=pair.min_quote_lifetime_ms,
                min_requote_ticks=1,
                book_type=BookType.L2_MBP,
                book_depth=50,
                post_only=True,
                ofi_enabled=pair.ofi_enabled,
                ofi_max_bps=pair.ofi_max_bps,
                liquidity_high_qty=pair.liquidity_high_qty,
                liquidity_low_qty=pair.liquidity_low_qty,
                min_order_qty=pair.min_order_qty,
                max_order_qty=pair.order_qty * Decimal("3"),
                internal_price_delta_limit=Decimal("8.0"),
                maker_fee_bps=Decimal("7.5"),
                min_profit_bps=pair.min_profit_bps,
                min_quote_reserve_ratio=Decimal("0.5"),
                min_quote_reserve_usdt=Decimal("500"),
                max_drawdown_pct=Decimal("0.05"),
                daily_loss_limit_usdt=Decimal("200"),
            )
        )
        engine.add_strategy(strategy)

    return engine, leader_instruments, follower_instruments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLMMv3 Primer multi-pair backtest")
    parser.add_argument("--date", required=True, help="Date string YYYY-MM-DD")
    parser.add_argument("--max-updates", type=int, default=None, help="Max deltas per venue")
    parser.add_argument("--data-dir", default="data/ob_data", help="Base ob_data directory")
    parser.add_argument("--leader-data-dir", default=None, help="Override leader ob_data directory")
    parser.add_argument("--follower-data-dir", default=None, help="Override follower ob_data directory")
    parser.add_argument("--depth", type=int, default=50)
    parser.add_argument("--questdb", action="store_true", help="Load deltas from QuestDB")
    parser.add_argument("--questdb-host", default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-table", default="orderbook_deltas")
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--starting-usdt", type=Decimal, default=Decimal("1500"))
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--profile", choices=("accuracy", "balanced", "speed"), default=None)
    parser.add_argument(
        "--fill-model",
        default=None,
        choices=(
            "best",
            "competition",
            "partial",
            "one-tick",
            "size-aware",
            "volume-sensitive",
            "two-tier",
            "three-tier",
        ),
    )
    parser.add_argument("--liquidity-factor", type=float, default=0.3)
    parser.add_argument("--latency-ms", type=int, default=None)
    parser.add_argument("--liquidity-consumption", dest="liquidity_consumption", action="store_true")
    parser.add_argument("--no-liquidity-consumption", dest="liquidity_consumption", action="store_false")
    parser.set_defaults(liquidity_consumption=None)
    parser.add_argument(
        "--fee-profile",
        default="default",
        choices=("default", "vip0", "vip1", "vip2", "vip3", "vip4", "vip5", "supreme"),
    )
    parser.add_argument("--mnt-discount", action="store_true")
    parser.add_argument("--mm-tier", default="none", choices=("none", "mm1", "mm2", "mm3"))
    parser.add_argument("--out-dir", default="backtest_results/llmmv3_primer")
    parser.add_argument("--use-bybit-balance", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _apply_profile(args)
    questdb = None
    if args.questdb:
        questdb = QuestDbConfig(
            host=args.questdb_host,
            port=args.questdb_port,
            table=args.questdb_table,
            step_seconds=args.questdb_step_seconds,
        )

    if args.fill_model is None:
        args.fill_model = "competition"
    if args.batch_size is None:
        args.batch_size = 1000
    if args.liquidity_consumption is None:
        args.liquidity_consumption = False

    maker_fee, taker_fee = _fee_profile(args.fee_profile, args.mnt_discount)
    if args.mm_tier != "none":
        maker_fee = _apply_mm_rebate(maker_fee, args.mm_tier)

    starting_balances = None
    if args.use_bybit_balance:
        assets = ["USDT"] + [pair.symbol.replace("USDT", "") for pair in DEFAULT_PAIRS]
        starting_balances = _fetch_bybit_balances(assets)

    engine, leader_instruments, follower_instruments = build_engine(
        DEFAULT_PAIRS,
        questdb,
        args.starting_usdt,
        args.fill_model,
        args.liquidity_factor,
        args.latency_ms,
        args.liquidity_consumption,
        maker_fee,
        taker_fee,
        starting_balances,
    )

    data_dir = Path(args.data_dir)
    leader_dir = Path(args.leader_data_dir) if args.leader_data_dir else data_dir
    follower_dir = Path(args.follower_data_dir) if args.follower_data_dir else data_dir
    start = time.time()

    for pair in DEFAULT_PAIRS:
        leader = leader_instruments[pair.symbol]
        follower = follower_instruments[pair.symbol]

        leader_count = _load_deltas(
            engine,
            leader,
            pair.symbol,
            venue="BINANCE",
            date_str=args.date,
            questdb=questdb,
            data_dir=leader_dir,
            depth=args.depth,
            max_updates=args.max_updates,
            batch_size=args.batch_size,
        )
        follower_count = _load_deltas(
            engine,
            follower,
            pair.symbol,
            venue="BYBIT",
            date_str=args.date,
            questdb=questdb,
            data_dir=follower_dir,
            depth=args.depth,
            max_updates=args.max_updates,
            batch_size=args.batch_size,
        )
        print(f"{pair.symbol} leader deltas: {leader_count:,} | follower deltas: {follower_count:,}")

    print(f"Load time: {time.time() - start:.1f}s")

    engine.run()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    account_report = engine.trader.generate_account_report(BYBIT_VENUE)
    fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()
    orders_report = engine.trader.generate_orders_report()

    if account_report is not None:
        account_report.to_csv(out_dir / "account_report.csv", index=False)
    if fills_report is not None:
        fills_report.to_csv(out_dir / "fills_report.csv", index=False)
    if positions_report is not None:
        positions_report.to_csv(out_dir / "positions_report.csv", index=False)
    if orders_report is not None:
        orders_report.to_csv(out_dir / "orders_report.csv", index=False)

    if fills_report is not None and not fills_report.empty:
        wap = _compute_wap_from_fills(fills_report)
        (out_dir / "wap_summary.json").write_text(json.dumps(wap, indent=2))

    summary = {
        "date": args.date,
        "profile": args.profile,
        "fill_model": args.fill_model,
        "fee_profile": args.fee_profile,
        "mnt_discount": args.mnt_discount,
        "mm_tier": args.mm_tier,
        "maker_fee": str(maker_fee),
        "taker_fee": str(taker_fee),
        "latency_ms": args.latency_ms,
        "liquidity_consumption": args.liquidity_consumption,
        "batch_size": args.batch_size,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    main()
