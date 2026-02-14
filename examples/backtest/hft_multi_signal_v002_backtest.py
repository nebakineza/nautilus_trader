#!/usr/bin/env python3
"""HFT Multi-Signal Scalper v002 Backtest - SOL/USDT $500 Scenario.

Implements the 24-hour test from the report:
- $500 USDT capital, 100% turnover per trade
- SOL/USDT on Bybit (follower) with Binance (leader)
- 0.1% maker/taker fees (VIP0 + MNT)
- Target: 0.30% capture, 60s time-stall, 120s max hold
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
from strategy.hft_multi_signal_scalper_v002 import HftMultiSignalV2, HftMultiSignalV2Config


DATA_DIR = Path(__file__).parent.parent.parent / "data" / "ob_data"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "backtest_results" / "hft_multi_signal_v002"


def load_ob_data(engine, instrument, file_path, loader_cls, max_updates=None):
    """Load orderbook data into engine."""
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
    # Strategy overrides
    target_spread_pct: float = 0.30,
    time_stall_ms: int = 60_000,
    max_hold_ms: int = 120_000,
    obi_ratio_threshold: float = 0.60,
    leadlag_move_bps: float = 15.0,
    wick_drop_bps: float = 30.0,
) -> dict:

    # ── Instrument specs ──
    SOL = Currency.from_str("SOL")
    maker_fee = Decimal("0.001")   # 0.1%
    taker_fee = Decimal("0.001")   # 0.1%

    bybit_instrument = CurrencyPair(
        instrument_id=InstrumentId(Symbol("SOLUSDT-SPOT"), BYBIT_VENUE),
        raw_symbol=Symbol("SOLUSDT"),
        base_currency=SOL,
        quote_currency=USDT,
        price_precision=2,
        size_precision=2,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.01"),
        lot_size=Quantity.from_str("0.01"),
        max_quantity=Quantity.from_str("100000"),
        min_quantity=Quantity.from_str("0.01"),
        max_price=Price.from_str("10000"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )

    binance_venue = Venue("BINANCE_SPOT")
    binance_instrument = CurrencyPair(
        instrument_id=InstrumentId(Symbol("SOLUSDT"), binance_venue),
        raw_symbol=Symbol("SOLUSDT"),
        base_currency=SOL,
        quote_currency=USDT,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        lot_size=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("100000"),
        min_quantity=Quantity.from_str("0.001"),
        max_price=Price.from_str("10000"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )

    # ── Engine ──
    print(f"\n{'=' * 60}")
    print(f"HFT MULTI-SIGNAL v002 BACKTEST")
    print(f"{'=' * 60}")
    print(f"Symbol: {symbol}")
    print(f"Date: {date_str}")
    print(f"Capital: ${starting_usdt:.0f}")
    print(f"Fees: {float(maker_fee) * 100:.2f}% maker / {float(taker_fee) * 100:.2f}% taker")
    print(f"Target spread: {target_spread_pct:.2f}%")
    print(f"Time stall: {time_stall_ms}ms → max hold: {max_hold_ms}ms")
    print(f"Latency model: {latency_ms}ms")
    print(f"{'=' * 60}\n")

    config = BacktestEngineConfig(
        trader_id=TraderId("HFT-MULTI-V2-BT"),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory=str(OUTPUT_DIR),
            log_file_name="hft_multi_v002_backtest.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)

    latency_model = LatencyModel(base_latency_nanos=latency_ms * 1_000_000)

    # Starting balances: $500 USDT + small SOL float
    starting_balances = [
        Money(starting_usdt, USDT),
        Money(Decimal("0.5"), SOL),  # small float for sells
    ]

    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances,
        book_type=BookType.L2_MBP,
        latency_model=latency_model,
    )
    engine.add_venue(
        venue=binance_venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances,
        book_type=BookType.L2_MBP,
        latency_model=latency_model,
    )

    engine.add_instrument(bybit_instrument)
    engine.add_instrument(binance_instrument)

    # ── Strategy ──
    strategy_config = HftMultiSignalV2Config(
        strategy_id="HFT-SOL-V2",
        follower_instrument_id=bybit_instrument.id,
        leader_instrument_id=binance_instrument.id,
        book_depth=50,
        # OBI / Micro-Price
        obi_levels=10,
        obi_window_bps=50.0,
        obi_ratio_threshold=obi_ratio_threshold,
        obi_min_notional_usd=100.0,
        microprice_lead_bps=1.0,
        # Wick
        wick_window_ms=1500,
        wick_drop_bps=wick_drop_bps,
        wick_recovery_bps=8.0,
        wick_tp_bps=20.0,
        # Lead-Lag
        leadlag_move_bps=leadlag_move_bps,
        leadlag_lag_bps=5.0,
        leadlag_window=5,
        # Execution
        capital=starting_usdt,
        capital_pct=1.0,
        min_order_value_usd=5.50,
        post_only=True,
        # Profit / Risk (report specs)
        target_spread_pct=target_spread_pct,
        min_spread_pct=0.25,
        fee_pct=0.10,
        time_stall_ms=time_stall_ms,
        time_stall_skew_pct=0.22,
        max_hold_ms=max_hold_ms,
        stop_loss_pct=0.50,
        max_daily_loss_usd=50.0,
        long_only=True,
        # Logging
        log_signals=True,
        log_interval_ms=30_000,
    )

    engine.add_strategy(HftMultiSignalV2(config=strategy_config))

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
        print(f"\nFills: {len(fills)} rows → {OUTPUT_DIR / 'fills_report.csv'}")
    else:
        print("\nNo fills generated.")

    if positions is not None and not positions.empty:
        positions.to_csv(OUTPUT_DIR / "positions_report.csv")
        print(f"Positions: {len(positions)} rows → {OUTPUT_DIR / 'positions_report.csv'}")

    # Summary
    summary = {
        "symbol": symbol,
        "date": date_str,
        "capital": starting_usdt,
        "latency_ms": latency_ms,
        "total_updates": total_updates,
        "elapsed_s": round(elapsed, 1),
        "fills": len(fills) if fills is not None else 0,
        "positions": len(positions) if positions is not None else 0,
        "target_spread_pct": target_spread_pct,
        "time_stall_ms": time_stall_ms,
        "max_hold_ms": max_hold_ms,
    }

    with open(OUTPUT_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary: {OUTPUT_DIR / 'summary.json'}")

    # Print report-style summary
    print(f"\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")
    if fills is not None and not fills.empty:
        buy_fills = fills[fills["side"] == "BUY"] if "side" in fills.columns else fills
        sell_fills = fills[fills["side"] == "SELL"] if "side" in fills.columns else fills
        print(f"  Buy fills:  {len(buy_fills)}")
        print(f"  Sell fills: {len(sell_fills)}")
    print(f"{'=' * 60}\n")

    engine.dispose()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HFT Multi-Signal v002 Backtest")
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--date", default="2026-01-29")
    parser.add_argument("--capital", type=float, default=500.0)
    parser.add_argument("--latency", type=int, default=10)
    parser.add_argument("--max-updates", type=int, default=None)
    parser.add_argument("--target-spread", type=float, default=0.30)
    parser.add_argument("--time-stall", type=int, default=60000)
    parser.add_argument("--max-hold", type=int, default=120000)
    parser.add_argument("--obi-threshold", type=float, default=0.60)
    parser.add_argument("--leadlag-move", type=float, default=15.0)
    parser.add_argument("--wick-drop", type=float, default=30.0)
    args = parser.parse_args()

    run_backtest(
        symbol=args.symbol,
        date_str=args.date,
        starting_usdt=args.capital,
        latency_ms=args.latency,
        max_updates=args.max_updates,
        target_spread_pct=args.target_spread,
        time_stall_ms=args.time_stall,
        max_hold_ms=args.max_hold,
        obi_ratio_threshold=args.obi_threshold,
        leadlag_move_bps=args.leadlag_move,
        wick_drop_bps=args.wick_drop,
    )
