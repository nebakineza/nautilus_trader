#!/usr/bin/env python3
"""Backtest Signal Trader v001 on ZRO using Hyperliquid historical candles.

Downloads 5-minute candles from Hyperliquid's free API,
converts to NautilusTrader Bar format, and runs the backtest.

USAGE:
    .venv/bin/python examples/backtest/signal_trader_v001_backtest.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import requests

# Add project root for strategy import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD, USDT
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    BarAggregation,
    BookType,
    OmsType,
    PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from strategy.hl_signal_trader_v001 import HLSignalTrader, HLSignalTraderConfig


# =============================================================================
# CONSTANTS
# =============================================================================
HYPERLIQUID_VENUE = Venue("HYPERLIQUID")
COIN = "ZRO"
INSTRUMENT_ID_STR = f"{COIN}-USD-PERP.HYPERLIQUID"
BAR_TYPE_STR = f"{INSTRUMENT_ID_STR}-5-MINUTE-LAST-EXTERNAL"
DATA_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "hl_candles"


def download_candles(coin: str, interval: str = "5m", days: int = 14) -> pd.DataFrame:
    """Download candles from Hyperliquid's free public API.

    API returns max 5000 candles per request.
    5m × 5000 = ~17.4 days, so we can get 14 days in one call.
    """
    cache_file = DATA_CACHE_DIR / f"{coin}_{interval}_{days}d.parquet"

    # Use cache if less than 1 hour old
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < 3600:
            print(f"📂 Using cached data: {cache_file} (age: {age/60:.0f}m)")
            return pd.read_parquet(cache_file)

    print(f"📡 Downloading {coin} {interval} candles for {days} days from Hyperliquid...")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    all_candles = []
    current_start = start_ms

    while current_start < end_ms:
        resp = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": current_start,
                    "endTime": end_ms,
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        all_candles.extend(batch)
        # Move start past the last candle
        last_t = batch[-1]["t"]
        if last_t <= current_start:
            break
        current_start = last_t + 1

        print(f"   Fetched {len(batch)} candles (total: {len(all_candles)})")

        if len(batch) < 5000:
            break  # Got everything
        time.sleep(0.5)  # Rate limit courtesy

    if not all_candles:
        print("❌ No candle data returned!")
        sys.exit(1)

    df = pd.DataFrame(all_candles)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df["open"] = df["o"].astype(float)
    df["high"] = df["h"].astype(float)
    df["low"] = df["l"].astype(float)
    df["close"] = df["c"].astype(float)
    df["volume"] = df["v"].astype(float)
    df = df.sort_values("timestamp").drop_duplicates(subset="t").reset_index(drop=True)

    # Cache
    DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_file)
    print(f"💾 Cached {len(df)} candles to {cache_file}")
    print(f"   Range: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print(f"   Price: ${df['close'].iloc[0]:.4f} → ${df['close'].iloc[-1]:.4f}")

    return df


def create_instrument() -> CryptoPerpetual:
    """Create a ZRO-USD perpetual instrument matching Hyperliquid's spec."""
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(INSTRUMENT_ID_STR),
        raw_symbol=Symbol(f"{COIN}-USD-PERP"),
        base_currency=Currency.from_str(COIN),
        quote_currency=USD,
        settlement_currency=USD,
        is_inverse=False,
        price_precision=4,     # ZRO prices like 2.0834
        size_precision=1,      # szDecimals=1 for ZRO
        price_increment=Price.from_str("0.0001"),
        size_increment=Quantity.from_str("0.1"),
        max_quantity=None,
        min_quantity=Quantity.from_str("0.1"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=Price.from_str("0.0001"),
        margin_init=Decimal("0.2"),       # 5x max leverage
        margin_maint=Decimal("0.1"),
        maker_fee=Decimal("0.00015"),     # 1.5 bps
        taker_fee=Decimal("0.00045"),     # 4.5 bps
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_str("1"),
    )


def candles_to_bars(df: pd.DataFrame, instrument: CryptoPerpetual) -> list[Bar]:
    """Convert Hyperliquid candle DataFrame to NautilusTrader Bar objects."""
    bar_type = BarType.from_str(BAR_TYPE_STR)
    bars = []

    for _, row in df.iterrows():
        ts_ns = int(row["timestamp"].timestamp() * 1e9)
        bar = Bar(
            bar_type=bar_type,
            open=instrument.make_price(Decimal(str(row["open"]))),
            high=instrument.make_price(Decimal(str(row["high"]))),
            low=instrument.make_price(Decimal(str(row["low"]))),
            close=instrument.make_price(Decimal(str(row["close"]))),
            volume=instrument.make_qty(Decimal(str(round(row["volume"], 1)))),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        bars.append(bar)

    return bars


def run_backtest(
    days: int = 14,
    trade_size_usd: float = 50.0,
    sl_mult: float = 2.0,
    tp_mult: float = 3.0,
    min_signals: int = 3,
    rsi_oversold: float = 0.30,
    rsi_overbought: float = 0.70,
    cooldown_bars: int = 3,
) -> dict:
    """Run the backtest and return results."""

    # ── Download data ──
    df = download_candles(COIN, "5m", days)

    # ── Create instrument ──
    instrument = create_instrument()

    # ── Convert to bars ──
    bars = candles_to_bars(df, instrument)
    print(f"\n📊 Converted {len(bars)} bars for backtest")

    # ── Configure engine ──
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(
            log_level="WARNING",  # Reduce noise during backtest
            log_colors=True,
            use_pyo3=False,
        ),
    )
    engine = BacktestEngine(config=engine_config)

    # ── Add venue ──
    engine.add_venue(
        venue=HYPERLIQUID_VENUE,
        oms_type=OmsType.NETTING,
        book_type=BookType.L1_MBP,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(500.0, USD)],  # Start with $500
        bar_execution=True,  # Bars move the market (trigger fills, SL, TP)
    )

    # ── Add instrument & data ──
    engine.add_instrument(instrument)
    engine.add_data(bars)

    # ── Configure strategy ──
    strategy_config = HLSignalTraderConfig(
        strategy_id="BT-SIG-ZRO-001",
        instrument_id=INSTRUMENT_ID_STR,
        bar_type=BAR_TYPE_STR,

        trade_size_usd=trade_size_usd,
        max_simultaneous=1,

        rsi_period=14,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,
        rsi_extreme_oversold=0.20,
        rsi_extreme_overbought=0.80,

        ema_fast_period=9,
        ema_slow_period=21,

        bb_period=20,
        bb_std=2.0,

        stoch_k_period=14,
        stoch_d_period=3,
        stoch_oversold=20.0,
        stoch_overbought=80.0,

        atr_period=14,
        sl_atr_multiplier=sl_mult,
        tp_atr_multiplier=tp_mult,

        min_signals=min_signals,

        bb_width_trending_pct=1.5,
        bb_width_ranging_pct=0.8,

        cooldown_bars=cooldown_bars,
        max_daily_loss_usd=20.0,
        max_daily_trades=20,

        body_filter_enabled=True,
        body_filter_pct=0.20,
        body_avg_period=20,
    )

    strategy = HLSignalTrader(config=strategy_config)
    engine.add_strategy(strategy)

    # ── Run ──
    print(f"\n🚀 Running backtest...")
    print(f"   Period: {df['timestamp'].iloc[0].strftime('%Y-%m-%d')} → {df['timestamp'].iloc[-1].strftime('%Y-%m-%d')}")
    print(f"   Bars:   {len(bars)}")
    print(f"   Params: SL={sl_mult}×ATR TP={tp_mult}×ATR min_signals={min_signals}")
    print(f"   Size:   ${trade_size_usd}")
    print()

    t0 = time.time()
    engine.run()
    elapsed = time.time() - t0

    # ── Extract results ──
    print(f"\n✅ Backtest completed in {elapsed:.1f}s")
    print("=" * 70)

    # Account report
    with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
        account_report = engine.trader.generate_account_report(HYPERLIQUID_VENUE)
        fills_report = engine.trader.generate_order_fills_report()
        positions_report = engine.trader.generate_positions_report()

        print("\n📈 ACCOUNT REPORT")
        print(account_report)

        print("\n📝 FILLS REPORT")
        if len(fills_report) > 0:
            print(fills_report)
        else:
            print("   No fills")

        print("\n📍 POSITIONS REPORT")
        if len(positions_report) > 0:
            print(positions_report)
        else:
            print("   No positions")

    # ── Summary statistics ──
    print("\n" + "=" * 70)
    print("📊 STRATEGY SUMMARY")
    print("=" * 70)

    total_trades = strategy._wins + strategy._losses
    win_rate = (strategy._wins / total_trades * 100) if total_trades > 0 else 0
    avg_pnl = (strategy._total_pnl / total_trades) if total_trades > 0 else 0

    results = {
        "period_days": days,
        "total_bars": len(bars),
        "total_trades": total_trades,
        "wins": strategy._wins,
        "losses": strategy._losses,
        "win_rate_pct": win_rate,
        "total_pnl": strategy._total_pnl,
        "avg_pnl_per_trade": avg_pnl,
        "sl_mult": sl_mult,
        "tp_mult": tp_mult,
        "min_signals": min_signals,
        "trade_size_usd": trade_size_usd,
        "elapsed_secs": elapsed,
    }

    print(f"   Period:         {days} days ({len(bars)} bars)")
    print(f"   Total Trades:   {total_trades}")
    print(f"   Wins:           {strategy._wins}")
    print(f"   Losses:         {strategy._losses}")
    print(f"   Win Rate:       {win_rate:.1f}%")
    print(f"   Total P&L:      ${strategy._total_pnl:+.4f}")
    print(f"   Avg P&L/Trade:  ${avg_pnl:+.4f}")
    print(f"   R:R Ratio:      {tp_mult/sl_mult:.1f}:1")
    print(f"   SL/TP:          {sl_mult}×ATR / {tp_mult}×ATR")
    print(f"   Min Signals:    {min_signals}/4")
    print(f"   Trade Size:     ${trade_size_usd}")

    if total_trades > 0:
        # Expected value per trade
        ev = (win_rate / 100 * tp_mult) - ((1 - win_rate / 100) * sl_mult)
        print(f"\n   📐 Expected Value:  {ev:+.2f} × ATR per trade")
        print(f"   {'✅ POSITIVE EV' if ev > 0 else '❌ NEGATIVE EV'}")

        # Breakeven win rate for this R:R
        breakeven_wr = sl_mult / (sl_mult + tp_mult) * 100
        print(f"   Breakeven WR:     {breakeven_wr:.1f}% (actual: {win_rate:.1f}%)")

    print("=" * 70)

    # Clean up
    engine.dispose()

    return results


if __name__ == "__main__":
    print("=" * 70)
    print("🎯 Signal Trader v001 — ZRO Backtest")
    print("=" * 70)

    # Run main backtest
    results = run_backtest(
        days=14,
        trade_size_usd=50.0,
        sl_mult=2.0,
        tp_mult=3.0,
        min_signals=3,
    )

    # If main backtest worked, run a quick parameter sweep
    if results["total_trades"] > 0:
        print("\n\n" + "=" * 70)
        print("🔬 PARAMETER SENSITIVITY SWEEP")
        print("=" * 70)

        sweep_results = []

        configs = [
            # (name, sl, tp, min_sig, rsi_os, rsi_ob, cooldown)
            ("Default",        2.0, 3.0, 3, 0.30, 0.70, 3),
            ("Tight SL/TP",    1.5, 2.0, 3, 0.30, 0.70, 3),
            ("Wide SL/TP",     2.5, 4.0, 3, 0.30, 0.70, 3),
            ("2-signal",       2.0, 3.0, 2, 0.30, 0.70, 3),
            ("Extreme RSI",    2.0, 3.0, 3, 0.25, 0.75, 3),
            ("No cooldown",    2.0, 3.0, 3, 0.30, 0.70, 1),
            ("Aggressive",     1.5, 2.5, 2, 0.35, 0.65, 2),
            ("Conservative",   2.5, 3.5, 3, 0.25, 0.75, 5),
        ]

        for name, sl, tp, min_sig, rsi_os, rsi_ob, cd in configs:
            try:
                r = run_backtest(
                    days=14,
                    trade_size_usd=50.0,
                    sl_mult=sl,
                    tp_mult=tp,
                    min_signals=min_sig,
                    rsi_oversold=rsi_os,
                    rsi_overbought=rsi_ob,
                    cooldown_bars=cd,
                )
                r["name"] = name
                sweep_results.append(r)
            except Exception as e:
                print(f"   ❌ {name} failed: {e}")

        # Summary table
        print("\n\n" + "=" * 70)
        print("📊 SWEEP RESULTS")
        print("=" * 70)
        print(f"{'Config':<16} {'Trades':>6} {'Wins':>5} {'Loss':>5} {'WR%':>6} {'P&L':>10} {'Avg':>8} {'EV':>6}")
        print("-" * 70)

        for r in sweep_results:
            ev = 0
            if r["total_trades"] > 0:
                ev = (r["win_rate_pct"] / 100 * r["tp_mult"]) - ((1 - r["win_rate_pct"] / 100) * r["sl_mult"])

            print(
                f"{r['name']:<16} {r['total_trades']:>6} {r['wins']:>5} {r['losses']:>5} "
                f"{r['win_rate_pct']:>5.1f}% ${r['total_pnl']:>+8.4f} "
                f"${r['avg_pnl_per_trade']:>+7.4f} {ev:>+5.2f}"
            )

        print("=" * 70)
        best = max(sweep_results, key=lambda x: x["total_pnl"])
        print(f"\n🏆 Best config: {best['name']} — ${best['total_pnl']:+.4f} total P&L")
