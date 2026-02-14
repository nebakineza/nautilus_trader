#!/usr/bin/env python3
"""HFT Multi-Signal Scalper v003 Backtest - Regime-Adaptive Sniper.

Tests the downtrend-adaptive logic on SOL/USDT $500 scenario:
- Regime detection: EMA slope → UPTREND / DOWNTREND / RANGING
- Downtrend: 20% capital, 0.22% target, 15s max hold
- Circuit breaker: WR < 40% over 10 trades → 30 min pause
- Vol over-extension snipe: buys micro-price dips only
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
from strategy.hft_multi_signal_scalper_v003 import HftMultiSignalV3, HftMultiSignalV3Config


DATA_DIR = Path(__file__).parent.parent.parent / "data" / "ob_data"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "backtest_results" / "hft_multi_signal_v003"


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
    obi_ratio_threshold: float = 0.60,
    leadlag_move_bps: float = 15.0,
    wick_drop_bps: float = 30.0,
    # Regime overrides
    regime_ema_period: int = 100,
    regime_trend_bps: float = 2.0,
    # Circuit breaker
    cb_min_wr_pct: float = 40.0,
    cb_pause_ms: int = 1_800_000,
) -> dict:

    # ── Instrument specs ──
    SOL = Currency.from_str("SOL")
    maker_fee = Decimal("0.001")
    taker_fee = Decimal("0.001")

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
    print(f"HFT MULTI-SIGNAL v003 BACKTEST (Regime-Adaptive)")
    print(f"{'=' * 60}")
    print(f"Symbol: {symbol}")
    print(f"Date: {date_str}")
    print(f"Capital: ${starting_usdt:.0f}")
    print(f"Fees: {float(maker_fee) * 100:.2f}% maker / {float(taker_fee) * 100:.2f}% taker")
    print(f"Regime: EMA({regime_ema_period}) slope > {regime_trend_bps}bps = trend")
    print(f"  Uptrend:   100% cap, 0.30% target, 120s max, 60s stall")
    print(f"  Downtrend: 20% cap,  0.22% target, 15s max,  8s stall")
    print(f"Circuit breaker: WR < {cb_min_wr_pct:.0f}% → {cb_pause_ms // 60000}min pause")
    print(f"Latency: {latency_ms}ms")
    print(f"{'=' * 60}\n")

    config = BacktestEngineConfig(
        trader_id=TraderId("HFT-V3-BT"),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory=str(OUTPUT_DIR),
            log_file_name="hft_multi_v003_backtest.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)

    latency_model = LatencyModel(base_latency_nanos=latency_ms * 1_000_000)

    starting_balances = [
        Money(starting_usdt, USDT),
        Money(Decimal("0.5"), SOL),
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
    strategy_config = HftMultiSignalV3Config(
        strategy_id="HFT-SOL-V3",
        follower_instrument_id=bybit_instrument.id,
        leader_instrument_id=binance_instrument.id,
        book_depth=50,
        # OBI
        obi_window_bps=50.0,
        obi_ratio_threshold=obi_ratio_threshold,
        obi_min_notional_usd=100.0,
        microprice_lead_bps=1.0,
        # Wick
        wick_drop_bps=wick_drop_bps,
        wick_recovery_bps=8.0,
        vol_overext_bps=40.0,
        vol_overext_window_ms=3000,
        # Lead-Lag
        leadlag_move_bps=leadlag_move_bps,
        leadlag_lag_bps=5.0,
        leadlag_window=5,
        # Execution
        capital=starting_usdt,
        capital_pct=1.0,
        min_order_value_usd=5.50,
        fee_pct=0.10,
        # Uptrend params
        target_spread_pct=0.30,
        time_stall_ms=60_000,
        time_stall_skew_pct=0.22,
        max_hold_ms=120_000,
        stop_loss_pct=0.50,
        # Downtrend params
        dt_capital_pct=0.20,
        dt_target_spread_pct=0.22,
        dt_max_hold_ms=15_000,
        dt_time_stall_ms=8_000,
        dt_stop_loss_pct=0.30,
        dt_cooldown_ms=2_000,
        # Regime
        regime_ema_period=regime_ema_period,
        regime_slope_window=20,
        regime_trend_bps=regime_trend_bps,
        regime_update_interval_ms=5_000,
        # Circuit breaker
        cb_window=10,
        cb_min_wr_pct=cb_min_wr_pct,
        cb_pause_ms=cb_pause_ms,
        cb_min_trades=5,
        # Risk
        max_daily_loss_usd=50.0,
        long_only=True,
        # Logging
        log_signals=True,
        log_interval_ms=30_000,
    )

    engine.add_strategy(HftMultiSignalV3(config=strategy_config))

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
    buy_fills = sell_fills = 0
    if fills is not None and not fills.empty and "side" in fills.columns:
        buy_fills = len(fills[fills["side"] == "BUY"])
        sell_fills = len(fills[fills["side"] == "SELL"])

    summary = {
        "symbol": symbol,
        "date": date_str,
        "capital": starting_usdt,
        "latency_ms": latency_ms,
        "total_updates": total_updates,
        "elapsed_s": round(elapsed, 1),
        "fills": len(fills) if fills is not None else 0,
        "buy_fills": buy_fills,
        "sell_fills": sell_fills,
        "round_trips": sell_fills,
        "positions": len(positions) if positions is not None else 0,
    }

    with open(OUTPUT_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Round trips: {sell_fills}")
    print(f"  Buy fills:   {buy_fills}")
    print(f"  Sell fills:  {sell_fills}")
    print(f"{'=' * 60}\n")

    engine.dispose()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HFT Multi-Signal v003 Backtest")
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--date", default="2026-01-29")
    parser.add_argument("--capital", type=float, default=500.0)
    parser.add_argument("--latency", type=int, default=10)
    parser.add_argument("--max-updates", type=int, default=None)
    # Signal thresholds
    parser.add_argument("--obi-threshold", type=float, default=0.60)
    parser.add_argument("--leadlag-move", type=float, default=15.0)
    parser.add_argument("--wick-drop", type=float, default=30.0)
    # Regime
    parser.add_argument("--ema-period", type=int, default=100)
    parser.add_argument("--trend-bps", type=float, default=2.0)
    # Circuit breaker
    parser.add_argument("--cb-wr", type=float, default=40.0)
    parser.add_argument("--cb-pause", type=int, default=1800000)
    args = parser.parse_args()

    run_backtest(
        symbol=args.symbol,
        date_str=args.date,
        starting_usdt=args.capital,
        latency_ms=args.latency,
        max_updates=args.max_updates,
        obi_ratio_threshold=args.obi_threshold,
        leadlag_move_bps=args.leadlag_move,
        wick_drop_bps=args.wick_drop,
        regime_ema_period=args.ema_period,
        regime_trend_bps=args.trend_bps,
        cb_min_wr_pct=args.cb_wr,
        cb_pause_ms=args.cb_pause,
    )
