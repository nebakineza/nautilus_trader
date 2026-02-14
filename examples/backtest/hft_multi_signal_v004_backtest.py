#!/usr/bin/env python3
"""HFT Multi-Signal Scalper v004 Backtest - Mean-Reversion Dip-Buyer.

Data-driven strategy based on microstructure analysis:
  - Mean-reversion after drawdown from rolling high
  - Entry at bid (passive, save spread)
  - Short hold times (10-60s)
  - Tight targets (+5 to +8 bps)
  - Fee configurable (0% for signal validation)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.model.venues import Venue

from examples.backtest.binance_orderbook_loader import BinanceOrderBookLoader
from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from strategy.hft_multi_signal_scalper_v004 import HftMultiSignalV4, HftMultiSignalV4Config


DATA_DIR = Path(__file__).parent.parent.parent / "data" / "ob_data"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "backtest_results" / "hft_multi_signal_v004"


def load_ob_data(engine, instrument, file_path, loader_cls, max_updates=None):
    count = 0
    batch = []
    batch_size = 10000
    print(f"  Loading {file_path.name}...")
    for delta in loader_cls.load_file(file_path, instrument):
        batch.append(delta)
        count += 1
        if max_updates and count >= max_updates:
            break
        if len(batch) >= batch_size:
            engine.add_data(batch)
            batch = []
            if count % 100_000 == 0:
                print(f"    {count:,} updates...")
    if batch:
        engine.add_data(batch)
    print(f"    Total: {count:,} updates")
    return count


def run_backtest(
    symbol: str = "SOLUSDT",
    date_str: str = "2026-01-29",
    starting_usdt: float = 500.0,
    latency_ms: int = 10,
    max_updates: int | None = None,
    # Fee
    fee_pct: float = 0.10,
    # Signal params
    dip_threshold_bps: float = 15.0,
    dip_recovery_bps: float = 2.0,
    dip_lookback: int = 100,
    wick_drop_bps: float = 25.0,
    # Target / Risk
    target_bps: float = 8.0,
    stop_loss_bps: float = 20.0,
    max_hold_ms: int = 60_000,
    time_stall_ms: int = 30_000,
    # Entry
    entry_at_bid: bool = True,
    # Regime
    regime_trend_bps: float = 2.0,
) -> dict:

    # Base/quote currencies
    base_str = symbol.replace("USDT", "")
    BASE = Currency.from_str(base_str)
    maker_fee = Decimal(str(fee_pct / 100))
    taker_fee = Decimal(str(fee_pct / 100))

    # Instrument precision lookup (price_precision, size_precision)
    PRECISION = {
        "SOL": (2, 2),
        "ETH": (2, 3),
        "BTC": (2, 5),
    }
    price_prec, size_prec = PRECISION.get(base_str, (2, 2))
    size_str = f"0.{'0' * (size_prec - 1)}1" if size_prec > 0 else "1"
    price_str = f"0.{'0' * (price_prec - 1)}1" if price_prec > 0 else "1"

    bybit_instrument = CurrencyPair(
        instrument_id=InstrumentId(Symbol(f"{symbol}-SPOT"), BYBIT_VENUE),
        raw_symbol=Symbol(symbol),
        base_currency=BASE,
        quote_currency=USDT,
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=Price.from_str(price_str),
        size_increment=Quantity.from_str(size_str),
        lot_size=Quantity.from_str(size_str),
        max_quantity=Quantity.from_str("100000"),
        min_quantity=Quantity.from_str(size_str),
        max_price=Price.from_str("1000000"),
        min_price=Price.from_str(price_str),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )

    binance_venue = Venue("BINANCE_SPOT")
    binance_instrument = CurrencyPair(
        instrument_id=InstrumentId(Symbol(symbol), binance_venue),
        raw_symbol=Symbol(symbol),
        base_currency=BASE,
        quote_currency=USDT,
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=Price.from_str(price_str),
        size_increment=Quantity.from_str(size_str),
        lot_size=Quantity.from_str(size_str),
        max_quantity=Quantity.from_str("100000"),
        min_quantity=Quantity.from_str(size_str),
        max_price=Price.from_str("1000000"),
        min_price=Price.from_str(price_str),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )

    # ── Engine ──
    fee_label = f"{fee_pct:.2f}%" if fee_pct > 0 else "0% (SIGNAL TEST)"
    print(f"\n{'=' * 60}")
    print(f"HFT v004 BACKTEST - Mean-Reversion Dip-Buyer")
    print(f"{'=' * 60}")
    print(f"Symbol: {symbol} | Date: {date_str} | Capital: ${starting_usdt:.0f}")
    print(f"Fees: {fee_label} | Entry: {'BID(passive)' if entry_at_bid else 'ASK(aggressive)'}")
    print(f"Dip: -{dip_threshold_bps:.0f}bps threshold | Target: +{target_bps:.0f}bps | Stop: -{stop_loss_bps:.0f}bps")
    print(f"Hold: max {max_hold_ms // 1000}s | Stall: {time_stall_ms // 1000}s | Latency: {latency_ms}ms")
    print(f"{'=' * 60}\n")

    config = BacktestEngineConfig(
        trader_id=TraderId("HFT-V4-BT"),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory=str(OUTPUT_DIR),
            log_file_name="hft_v004_backtest.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)
    latency_model = LatencyModel(base_latency_nanos=latency_ms * 1_000_000)

    starting_balances = [
        Money(starting_usdt, USDT),
        Money(Decimal("0.5"), BASE),
    ]

    engine.add_venue(
        venue=BYBIT_VENUE, oms_type=OmsType.NETTING,
        account_type=AccountType.CASH, base_currency=None,
        starting_balances=starting_balances, book_type=BookType.L2_MBP,
        latency_model=latency_model,
    )
    engine.add_venue(
        venue=binance_venue, oms_type=OmsType.NETTING,
        account_type=AccountType.CASH, base_currency=None,
        starting_balances=starting_balances, book_type=BookType.L2_MBP,
        latency_model=latency_model,
    )

    engine.add_instrument(bybit_instrument)
    engine.add_instrument(binance_instrument)

    # ── Strategy ──
    strategy_config = HftMultiSignalV4Config(
        strategy_id="HFT-V4",
        follower_instrument_id=bybit_instrument.id,
        leader_instrument_id=binance_instrument.id,
        book_depth=50,
        # Dip signal
        dip_lookback_ms=60_000,
        dip_threshold_bps=dip_threshold_bps,
        dip_recovery_bps=dip_recovery_bps,
        dip_obi_confirm=True,
        # Wick
        wick_drop_bps=wick_drop_bps,
        wick_recovery_bps=5.0,
        # Execution
        capital=starting_usdt,
        capital_pct=1.0,
        min_order_value_usd=5.50,
        entry_at_bid=entry_at_bid,
        entry_patience_ms=2000,
        fee_pct=fee_pct,
        # Target / Risk
        target_bps=target_bps,
        stop_loss_bps=stop_loss_bps,
        max_hold_ms=max_hold_ms,
        time_stall_ms=time_stall_ms,
        time_stall_target_bps=3.0,
        # Downtrend
        dt_capital_pct=0.20,
        dt_dip_threshold_bps=20.0,
        dt_target_bps=5.0,
        dt_max_hold_ms=30_000,
        dt_stop_loss_bps=15.0,
        dt_cooldown_ms=3_000,
        # Regime
        regime_trend_bps=regime_trend_bps,
        # Circuit breaker
        cb_window=10,
        cb_min_wr_pct=30.0,
        cb_pause_ms=1_800_000,
        cb_min_trades=5,
        # Risk
        max_daily_loss_usd=50.0,
        long_only=True,
        cooldown_ms=1_000,
        # Logging
        log_signals=True,
        log_interval_ms=30_000,
    )

    engine.add_strategy(HftMultiSignalV4(config=strategy_config))

    # ── Data ──
    bybit_file = DATA_DIR / f"{symbol}_Spot" / f"{date_str}_{symbol}_bybit_ob50.data"
    binance_file = DATA_DIR / f"{symbol}_Spot" / f"{date_str}_{symbol}_binance_ob50.data"

    if not bybit_file.exists():
        print(f"ERROR: {bybit_file} not found")
        return {}
    if not binance_file.exists():
        print(f"ERROR: {binance_file} not found")
        return {}

    print("Loading data:")
    bybit_count = load_ob_data(engine, bybit_instrument, bybit_file, BybitOrderBookLoader, max_updates)
    binance_count = load_ob_data(engine, binance_instrument, binance_file, BinanceOrderBookLoader, max_updates)
    total_updates = bybit_count + binance_count
    print(f"\nTotal: {total_updates:,} updates loaded")

    # ── Run ──
    print("\nRunning backtest...")
    t0 = time.time()
    engine.run()
    elapsed = time.time() - t0
    print(f"Completed in {elapsed:.1f}s ({total_updates / max(elapsed, 0.1):,.0f} updates/s)")

    # ── Reports ──
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fills = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()

    if fills is not None and not fills.empty:
        fills.to_csv(OUTPUT_DIR / "fills_report.csv")
        print(f"\nFills: {len(fills)} rows")
    else:
        print("\nNo fills generated.")

    if positions is not None and not positions.empty:
        positions.to_csv(OUTPUT_DIR / "positions_report.csv")

    buy_fills = sell_fills = 0
    if fills is not None and not fills.empty and "side" in fills.columns:
        buy_fills = len(fills[fills["side"] == "BUY"])
        sell_fills = len(fills[fills["side"] == "SELL"])

    summary = {
        "symbol": symbol,
        "date": date_str,
        "capital": starting_usdt,
        "fee_pct": fee_pct,
        "latency_ms": latency_ms,
        "total_updates": total_updates,
        "elapsed_s": round(elapsed, 1),
        "fills": len(fills) if fills is not None else 0,
        "buy_fills": buy_fills,
        "sell_fills": sell_fills,
        "round_trips": min(buy_fills, sell_fills),
        "entry_at_bid": entry_at_bid,
        "dip_threshold_bps": dip_threshold_bps,
        "target_bps": target_bps,
        "stop_loss_bps": stop_loss_bps,
        "max_hold_ms": max_hold_ms,
    }

    with open(OUTPUT_DIR / "summary.json", "w") as fj:
        json.dump(summary, fj, indent=2)

    print(f"\n{'=' * 60}")
    print(f"RESULTS")
    print(f"{'=' * 60}")
    print(f"  Round trips: {min(buy_fills, sell_fills)}")
    print(f"  Buy fills:   {buy_fills}")
    print(f"  Sell fills:  {sell_fills}")
    print(f"  Fee mode:    {fee_label}")
    print(f"{'=' * 60}\n")

    engine.dispose()
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="HFT v004 Backtest")
    p.add_argument("--symbol", default="SOLUSDT")
    p.add_argument("--date", default="2026-01-29")
    p.add_argument("--capital", type=float, default=500.0)
    p.add_argument("--latency", type=int, default=10)
    p.add_argument("--max-updates", type=int, default=None)
    # Fees
    p.add_argument("--fee", type=float, default=0.10, help="Fee per side in %% (0 = no fees)")
    # Signal
    p.add_argument("--dip-thresh", type=float, default=15.0)
    p.add_argument("--dip-recovery", type=float, default=2.0)
    p.add_argument("--dip-lookback", type=int, default=100)
    p.add_argument("--wick-drop", type=float, default=25.0)
    # Target
    p.add_argument("--target", type=float, default=8.0, help="Target in bps")
    p.add_argument("--stop", type=float, default=20.0, help="Stop loss in bps")
    p.add_argument("--max-hold", type=int, default=60000)
    p.add_argument("--stall", type=int, default=30000)
    # Entry mode
    p.add_argument("--ioc", action="store_true", help="IOC at ask instead of limit at bid")
    # Regime
    p.add_argument("--trend-bps", type=float, default=2.0)
    args = p.parse_args()

    run_backtest(
        symbol=args.symbol,
        date_str=args.date,
        starting_usdt=args.capital,
        latency_ms=args.latency,
        max_updates=args.max_updates,
        fee_pct=args.fee,
        dip_threshold_bps=args.dip_thresh,
        dip_recovery_bps=args.dip_recovery,
        dip_lookback=args.dip_lookback,
        wick_drop_bps=args.wick_drop,
        target_bps=args.target,
        stop_loss_bps=args.stop,
        max_hold_ms=args.max_hold,
        time_stall_ms=args.stall,
        entry_at_bid=not args.ioc,
        regime_trend_bps=args.trend_bps,
    )
