#!/usr/bin/env python3
"""Backtest Triangular Arb v002 with REAL Data and Balances."""

from __future__ import annotations

import argparse
import os
import time
import heapq
import json
import gzip
from decimal import Decimal
from typing import Iterator

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT, BTC, ETH, SOL, USDC
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, BookAction
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.data import OrderBookDeltas, OrderBookDelta, BookOrder

from strategy.triangular_arb_v002 import TriangularArb, TriangularArbConfig, ArbPathConfig
from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb

def _step_from_precision(precision: int) -> str:
    if precision <= 0:
        return "1"
    return "0." + ("0" * (precision - 1)) + "1"

def _create_instrument(
    instrument_id: str,
    symbol: str, 
    base_currency: object,
    quote_currency: object,
    price_precision: int,
    size_precision: int,
) -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=quote_currency,
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

def load_jsonl_updates(file_path: str, instrument: CurrencyPair) -> Iterator[OrderBookDeltas]:
    """Parses JSONL (Nautilus/Bybit capture) into OrderBookDeltas."""
    print(f"Loading {file_path} for {instrument.id}...")
    
    # Check if gzip
    open_func = gzip.open if file_path.endswith(".gz") else open
    
    has_snapshot = False

    with open_func(file_path, "rt") as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
                
            # Extract fields
            # Supports both "type" at top level or inside data? 
            # Based on head: top level "type", "ts", "data"
            
            msg_type = msg.get("type", "").lower() # "snapshot" or "delta"
            ts_ms = msg.get("ts", 0)
            data = msg.get("data", {})
            
            if not data: continue
            
            # ts is ms, convert to ns
            ts_ns = ts_ms * 1_000_000 
            
            # Treat first update as snapshot if no explicit snapshot exists
            if not has_snapshot and msg_type != "snapshot":
                msg_type = "snapshot"
                has_snapshot = True

            # Parse Bids/Asks
            levels_b = data.get("b", [])
            levels_a = data.get("a", [])
            
            deltas = []
            
            # Helper to parse list [price_str, size_str]
            def parse_levels(levels, is_bid):
                side = OrderSide.BUY if is_bid else OrderSide.SELL
                for p_str, s_str in levels:
                    # Use float conversion + explicit precision to ensure match with Instrument
                    p_val = float(p_str)
                    s_val = float(s_str)

                    # Skip invalid/placeholder price levels
                    if p_val <= 0:
                        continue

                    price = Price(p_val, precision=instrument.price_precision)
                    size = Quantity(s_val, precision=instrument.size_precision)
                    
                    if size == 0:
                        if msg_type == "snapshot":
                            continue
                        action = BookAction.DELETE
                    else:
                        action = BookAction.ADD if msg_type == "snapshot" else BookAction.UPDATE
                        
                    # L2 books typically don't track Order IDs. 
                    # Nautilus requires one for BookOrder. We can use a hash.
                    order_id = hash((side, price)) % 2147483647
                    
                    order = BookOrder(
                        side=side,
                        price=price,
                        size=size,
                        order_id=order_id
                    )
                    
                    deltas.append(OrderBookDelta(
                        instrument_id=instrument.id,
                        action=action,
                        order=order,
                        flags=0,
                        sequence=0,
                        ts_event=ts_ns,
                        ts_init=ts_ns,
                    ))

            parse_levels(levels_b, True)
            parse_levels(levels_a, False)
            
            if not deltas:
                continue

            yield OrderBookDeltas(
                instrument_id=instrument.id,
                deltas=deltas,
            )

def run_backtest(
    date_str: str = "2026-01-29",
    max_updates: int = 1000000,
    fast_mode: bool = False,
    stride: int = 1,
    max_hours: float = 0.0,
    questdb: QuestDbConfig | None = None,
    min_profit_bps: Decimal = Decimal("-1.0"),
    min_profit_usdt: Decimal = Decimal("-0.0001"),
    max_slippage_bps: Decimal = Decimal("2.0"),
    trend_ema_period: int = 20,
    trend_epsilon_bps: Decimal = Decimal("1.0"),
    trend_bias: bool = True,
    max_data_staleness_ms: int = 2000,
    require_synced_books: bool = False,
) -> dict:
    
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("TRIARB-REAL"),
        logging=LoggingConfig(log_level="INFO"),
    )
    engine = BacktestEngine(config=engine_config)

    # 1. Define Instruments (REAL PAIRS)
    # Tri A: BTCUSDT, USDCUSDT, BTCUSDC
    btcusdt = _create_instrument("BTCUSDT-SPOT.BYBIT", "BTCUSDT", BTC, USDT, 2, 5)
    usdcusdt = _create_instrument("USDCUSDT-SPOT.BYBIT", "USDCUSDT", USDC, USDT, 4, 2)
    btcusdc = _create_instrument("BTCUSDC-SPOT.BYBIT", "BTCUSDC", BTC, USDC, 2, 5)
    
    # Tri B: ETHUSDT, ETHBTC, BTCUSDT
    ethusdt = _create_instrument("ETHUSDT-SPOT.BYBIT", "ETHUSDT", ETH, USDT, 2, 4)
    ethbtc  = _create_instrument("ETHBTC-SPOT.BYBIT",  "ETHBTC",  ETH, BTC,  6, 4)
    
    # Tri C: SOLUSDT, SOLBTC, BTCUSDT
    solusdt = _create_instrument("SOLUSDT-SPOT.BYBIT", "SOLUSDT", SOL, USDT, 3, 2)
    solbtc  = _create_instrument("SOLBTC-SPOT.BYBIT",  "SOLBTC",  SOL, BTC,  7, 2)

    # 2. Configure Account (REAL BALANCES from Bybit)
    # BTC: 0.00252926, ETH: 0.10450529, SOL: 1.85697254, USDT: 983.84777621
    engine.add_venue(
        venue=Venue("BYBIT"),
        oms_type=OmsType.HEDGING, 
        account_type=AccountType.CASH,
        starting_balances=[
            Money(Decimal("983.84777621"), USDT),
            Money(Decimal("0.10450529"), ETH),
            Money(Decimal("1.85697254"), SOL),
            Money(Decimal("0.00252926"), BTC),
        ],
    )
    
    all_instruments = [btcusdt, usdcusdt, btcusdc, ethusdt, ethbtc, solusdt, solbtc]
    for inst in all_instruments:
        engine.add_instrument(inst)

    # 3. Strategy Config (Real Multi-Path Triangles)
    paths = [
        # Path A: Stable Loop (Using BTC as bridge)
        # USDT -> BTC -> USDC -> USDT
        # Leg 1: Buy BTC/USDT (Sell USDT, Buy BTC)
        # Leg 2: Sell BTC/USDC (Sell BTC, Buy USDC)
        # Leg 3: Sell USDC/USDT (Sell USDC, Buy USDT)
        ArbPathConfig(
            id="STABLE_TRI",
            leg1_instrument_id=btcusdt.id, leg1_side=OrderSide.BUY,
            leg2_instrument_id=btcusdc.id, leg2_side=OrderSide.SELL,
            leg3_instrument_id=usdcusdt.id, leg3_side=OrderSide.SELL,
            order_qty=Decimal("0.001"), # ~90 USDT sized trade
        ),
        
        # Path B: ETH Loop
        # USDT -> ETH -> BTC -> USDT
        # Leg 1: Buy ETH/USDT
        # Leg 2: Sell ETH/BTC (Sell ETH, Buy BTC)
        # Leg 3: Sell BTC/USDT (Sell BTC, Buy USDT)
        ArbPathConfig(
            id="ETH_TRI",
            leg1_instrument_id=ethusdt.id, leg1_side=OrderSide.BUY,
            leg2_instrument_id=ethbtc.id,  leg2_side=OrderSide.SELL,
            leg3_instrument_id=btcusdt.id, leg3_side=OrderSide.SELL,
            order_qty=Decimal("0.03"), # ~90 USDT
        ),
        
        # Path C: SOL Loop
        # USDT -> SOL -> BTC -> USDT
        # Leg 1: Buy SOL/USDT
        # Leg 2: Sell SOL/BTC
        # Leg 3: Sell BTC/USDT
        ArbPathConfig(
            id="SOL_TRI",
            leg1_instrument_id=solusdt.id, leg1_side=OrderSide.BUY,
            leg2_instrument_id=solbtc.id,  leg2_side=OrderSide.SELL,
            leg3_instrument_id=btcusdt.id, leg3_side=OrderSide.SELL,
            order_qty=Decimal("0.6"), # ~80 USDT
        ),
    ]
    
    # Overriding min_profit to -500 to ensure we see execution flow logic on real data
    # (Since we are paying Taker fees in simulation, we will likely lose money, but we prove the Path works)
    # Target: >= 0.01 USDT net per cycle (after fees)
    config = TriangularArbConfig(
        paths=paths,
        min_profit_bps=min_profit_bps,
        min_profit_usdt=min_profit_usdt,
        require_synced_books=False,
        max_data_staleness_ms=2000,
        max_slippage_bps=max_slippage_bps,
        halt_on_reject=False,
        trend_bias=True,
        trend_ema_period=trend_ema_period,
        trend_epsilon_bps=trend_epsilon_bps,
    )
    
    strategy = TriangularArb(config=config)
    engine.add_strategy(strategy)

    # 4. Load Data Generators
    # Map instrument to filename
    # NOTE: File paths are hardcoded based on organization step
    base_data_dir = "/home/seb/nebakineza/nautilus_trader/data/ob_data"
    
    ## MAPPING OF FILES:
    ## btcusdt: ob_data/BTCUSDT_Spot/2026-01-29_BTCUSDT_bybit_ob50.data
    ## usdcusdt: ob_data/USDCUSDT_Spot/2026-01-29_USDCUSDT_ob200.data
    ## btcusdc: ob_data/BTCUSDC_Spot/2026-01-29_BTCUSDC_ob200.data
    ## ethusdt: ob_data/ETHUSDT_Spot/2026-01-29_ETHUSDT_bybit_ob50.data
    ## ethbtc: ob_data/ETHBTC_Spot/2026-01-29_ETHBTC_ob200.data
    ## solusdt: ob_data/SOLUSDT_Spot/2026-01-29_SOLUSDT_bybit_ob50.data (Use bybit if exists check)
    ## solbtc: ob_data/SOLBTC_Spot/2026-01-29_SOLBTC_ob200.data

    def _pick_file(candidates: list[str]) -> str:
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return candidates[0]

    files = {
        btcusdt.id: _pick_file([
            f"{base_data_dir}/BTCUSDT_Spot/{date_str}_BTCUSDT_ob200.data",
            f"{base_data_dir}/BTCUSDT_Spot/{date_str}_BTCUSDT_bybit_ob50.data",
            f"{base_data_dir}/BTCUSDT_Spot/{date_str}_BTCUSDT_binance_ob50.data",
        ]),
        usdcusdt.id: f"{base_data_dir}/USDCUSDT_Spot/{date_str}_USDCUSDT_ob200.data",
        btcusdc.id: f"{base_data_dir}/BTCUSDC_Spot/{date_str}_BTCUSDC_ob200.data",
        ethusdt.id: _pick_file([
            f"{base_data_dir}/ETHUSDT_Spot/{date_str}_ETHUSDT_ob200.data",
            f"{base_data_dir}/ETHUSDT_Spot/{date_str}_ETHUSDT_bybit_ob50.data",
            f"{base_data_dir}/ETHUSDT_Spot/{date_str}_ETHUSDT_binance_ob50.data",
        ]),
        ethbtc.id:  f"{base_data_dir}/ETHBTC_Spot/{date_str}_ETHBTC_ob200.data",
        solusdt.id: _pick_file([
            f"{base_data_dir}/SOLUSDT_Spot/{date_str}_SOLUSDT_ob200.data",
            f"{base_data_dir}/SOLUSDT_Spot/{date_str}_SOLUSDT_bybit_ob50.data",
        ]),
        solbtc.id:  f"{base_data_dir}/SOLBTC_Spot/{date_str}_SOLBTC_ob200.data",
    }

    symbols = {
        btcusdt.id: "BTCUSDT",
        usdcusdt.id: "USDCUSDT",
        btcusdc.id: "BTCUSDC",
        ethusdt.id: "ETHUSDT",
        ethbtc.id: "ETHBTC",
        solusdt.id: "SOLUSDT",
        solbtc.id: "SOLBTC",
    }
    
    generators = []
    print("Initializing generators...")
    for inst, fpath in files.items():
        instrument = next(i for i in all_instruments if i.id == inst)
        if questdb is not None:
            gen = load_questdb(
                questdb,
                instrument,
                venue="BYBIT",
                symbol=symbols[inst],
                date_str=date_str,
            )
        else:
            gen = load_jsonl_updates(fpath, instrument)
        generators.append(gen)

    # Align streams so all instruments start at or after the latest first timestamp
    first_batches = []
    for gen in generators:
        try:
            first = next(gen)
            first_batches.append((gen, first))
        except StopIteration:
            continue

    if not first_batches:
        raise RuntimeError("No data loaded from any generator")

    start_ts = max(d.ts_event for _, d in first_batches)
    end_ts = start_ts + int(max_hours * 3600 * 1_000_000_000) if max_hours > 0 else None

    def aligned_stream(gen, first):
        if first.ts_event >= start_ts:
            yield first
        for item in gen:
            if item.ts_event >= start_ts:
                yield item

    aligned_gens = [aligned_stream(gen, first) for gen, first in first_batches]

    # Merge
    def get_ts(d: OrderBookDeltas):
        return d.ts_event

    print("Merging data streams...")
    merged_gen = heapq.merge(*aligned_gens, key=get_ts)
    
    print("Feeding engine...")
    count = 0
    start = time.time()
    batch = []
    seen_instruments: set[InstrumentId] = set()
    
    for deltas in merged_gen:
        count += 1
        if deltas.instrument_id not in seen_instruments:
            seen_instruments.add(deltas.instrument_id)
        elif fast_mode and stride > 1 and (count % stride != 0):
            continue
        if end_ts is not None and deltas.ts_event > end_ts:
            break
        batch.append(deltas)
        if len(batch) >= 500:
            engine.add_data(batch)
            batch = []
        if count >= max_updates:
            break
            
    if batch:
        engine.add_data(batch)
            
    print(f"Loaded {count} updates in {time.time() - start:.2f}s")
    engine.run()
    
    # Report
    account = engine.cache.account_for_venue(Venue("BYBIT"))
    print("\nFinal Balances:")
    if account:
        for bal in account.balances():
            print(bal)

    stats = strategy.stats()
    print("\nStrategy Stats:")
    print(stats)

    if account:
        mids = {
            "BTCUSDT": strategy.mid_price(btcusdt.id),
            "ETHUSDT": strategy.mid_price(ethusdt.id),
            "SOLUSDT": strategy.mid_price(solusdt.id),
            "USDCUSDT": strategy.mid_price(usdcusdt.id),
        }
        balances: dict[str, Decimal] = {}
        for bal in account.balances():
            if hasattr(bal, "currency") and hasattr(bal, "total"):
                code = getattr(bal.currency, "code", None) or getattr(bal.currency, "symbol", None) or str(bal.currency)
                total = bal.total.as_decimal() if hasattr(bal.total, "as_decimal") else Decimal(bal.total)
                balances[code] = total

        if balances:
            usdt = balances.get("USDT", Decimal("0"))
            usdc = balances.get("USDC", Decimal("0"))
            btc = balances.get("BTC", Decimal("0"))
            eth = balances.get("ETH", Decimal("0"))
            sol = balances.get("SOL", Decimal("0"))

            usdc_usdt = mids.get("USDCUSDT") or Decimal("0")
            btc_usdt = mids.get("BTCUSDT") or Decimal("0")
            eth_usdt = mids.get("ETHUSDT") or Decimal("0")
            sol_usdt = mids.get("SOLUSDT") or Decimal("0")

            total_usdt = usdt + (usdc * usdc_usdt) + (btc * btc_usdt) + (eth * eth_usdt) + (sol * sol_usdt)
            print(f"\nEstimated NAV (USDT): {total_usdt:.6f}")
            return {"stats": stats, "nav_usdt": total_usdt, "balances": balances}

    return {"stats": stats, "nav_usdt": None, "balances": {}}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="2026-01-29")
    parser.add_argument("--max-updates", type=int, default=1000000)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--stride", type=int, default=50)
    parser.add_argument("--max-hours", type=float, default=2.0)
    parser.add_argument("--questdb-host", type=str, default="")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--questdb-table", type=str, default="orderbook_deltas")
    parser.add_argument("--questdb-step-seconds", type=int, default=300)
    parser.add_argument("--min-profit-bps", type=Decimal, default=Decimal("-1.0"))
    parser.add_argument("--min-profit-usdt", type=Decimal, default=Decimal("-0.0001"))
    parser.add_argument("--max-slippage-bps", type=Decimal, default=Decimal("2.0"))
    parser.add_argument("--trend-ema-period", type=int, default=20)
    parser.add_argument("--trend-epsilon-bps", type=Decimal, default=Decimal("1.0"))
    parser.add_argument("--trend-bias", action="store_true")
    parser.add_argument("--max-data-staleness-ms", type=int, default=2000)
    parser.add_argument("--require-synced-books", action="store_true")
    args = parser.parse_args()

    questdb = None
    if args.questdb_host:
        questdb = QuestDbConfig(
            host=args.questdb_host,
            port=args.questdb_port,
            table=args.questdb_table,
            step_seconds=args.questdb_step_seconds,
        )

    run_backtest(
        date_str=args.date,
        max_updates=args.max_updates,
        fast_mode=args.fast,
        stride=args.stride,
        max_hours=args.max_hours,
        questdb=questdb,
        min_profit_bps=args.min_profit_bps,
        min_profit_usdt=args.min_profit_usdt,
        max_slippage_bps=args.max_slippage_bps,
        trend_ema_period=args.trend_ema_period,
        trend_epsilon_bps=args.trend_epsilon_bps,
        trend_bias=args.trend_bias,
        max_data_staleness_ms=args.max_data_staleness_ms,
        require_synced_books=args.require_synced_books,
    )
