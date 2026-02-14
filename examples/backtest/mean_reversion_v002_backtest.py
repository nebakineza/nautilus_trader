#!/usr/bin/env python3
"""Backtest Mean Reversion v002 on ZRO using Hyperliquid historical candles.

Downloads candles from Hyperliquid's free API across multiple timeframes,
converts to NautilusTrader Bar format, and runs the backtest.

KEY CONCEPT FROM THE TRADER:
    "Test every timeframe for each asset and pick the highest win rate."
    BTC 8H = 92.74%, BTC 4H = 85.7%, BTC 15m = 91.95%
    SOL Daily = 100%, SOL 4H = 88.4%

    We sweep across timeframes AND parameter configs to find optimal.

USAGE:
    .venv/bin/python examples/backtest/mean_reversion_v002_backtest.py
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
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    AccountType,
    BarAggregation,
    BookType,
    OmsType,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from strategy.hl_mean_reversion_v002 import HLMeanReversion, HLMeanReversionConfig


# =============================================================================
# CONSTANTS
# =============================================================================
HYPERLIQUID_VENUE = Venue("HYPERLIQUID")
DATA_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "hl_candles"

# Timeframe mapping: (HL API interval, NautilusTrader bar type suffix, approx max days for 5000 candles)
TIMEFRAMES = {
    "5m":  ("5m",  "5-MINUTE-LAST-EXTERNAL",    17),
    "15m": ("15m", "15-MINUTE-LAST-EXTERNAL",    52),
    "1h":  ("1h",  "60-MINUTE-LAST-EXTERNAL",   208),
    "4h":  ("4h",  "240-MINUTE-LAST-EXTERNAL",  833),
}


def download_candles(
    coin: str,
    interval: str = "5m",
    days: int = 14,
) -> pd.DataFrame:
    """Download candles from Hyperliquid's free public API."""
    cache_file = DATA_CACHE_DIR / f"{coin}_{interval}_{days}d.parquet"

    # Use cache if less than 1 hour old
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < 3600:
            print(f"  📂 Cached: {cache_file} ({age/60:.0f}m old)")
            return pd.read_parquet(cache_file)

    print(f"  📡 Downloading {coin} {interval} × {days}d from Hyperliquid...")
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
        last_t = batch[-1]["t"]
        if last_t <= current_start:
            break
        current_start = last_t + 1

        if len(batch) < 5000:
            break
        time.sleep(0.3)

    if not all_candles:
        raise ValueError(f"No candle data for {coin} {interval}")

    df = pd.DataFrame(all_candles)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df["open"] = df["o"].astype(float)
    df["high"] = df["h"].astype(float)
    df["low"] = df["l"].astype(float)
    df["close"] = df["c"].astype(float)
    df["volume"] = df["v"].astype(float)
    df = df.sort_values("timestamp").drop_duplicates(subset="t").reset_index(drop=True)

    DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_file)
    print(f"  💾 Cached {len(df)} candles → {cache_file}")

    return df


def create_instrument(coin: str) -> CryptoPerpetual:
    """Create a crypto perpetual instrument."""
    instrument_id_str = f"{coin}-USD-PERP.HYPERLIQUID"

    # Precision lookup for common coins
    precision_map = {
        "ZRO":  (4, 1, "0.0001", "0.1"),
        "SUI":  (4, 1, "0.0001", "0.1"),
        "LINK": (3, 1, "0.001",  "0.1"),
        "AVAX": (3, 1, "0.001",  "0.1"),
        "ARB":  (4, 1, "0.0001", "0.1"),
        "SEI":  (4, 0, "0.0001", "1.0"),
        "APT":  (3, 1, "0.001",  "0.1"),
        "NEAR": (3, 1, "0.001",  "0.1"),
        "ENA":  (4, 0, "0.0001", "1.0"),
        "ONDO": (4, 0, "0.0001", "1.0"),
        "BTC":  (1, 4, "0.1",    "0.0001"),
        "ETH":  (2, 3, "0.01",   "0.001"),
        "SOL":  (3, 1, "0.001",  "0.1"),
    }

    price_prec, size_prec, price_inc, size_inc = precision_map.get(
        coin, (4, 1, "0.0001", "0.1")
    )

    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(instrument_id_str),
        raw_symbol=Symbol(f"{coin}-USD-PERP"),
        base_currency=Currency.from_str(coin),
        quote_currency=USD,
        settlement_currency=USD,
        is_inverse=False,
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=Price.from_str(price_inc),
        size_increment=Quantity.from_str(size_inc),
        max_quantity=None,
        min_quantity=Quantity.from_str(size_inc),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=Price.from_str(price_inc),
        margin_init=Decimal("0.2"),
        margin_maint=Decimal("0.1"),
        maker_fee=Decimal("0.00015"),
        taker_fee=Decimal("0.00045"),
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_str("1"),
    )


def candles_to_bars(
    df: pd.DataFrame,
    instrument: CryptoPerpetual,
    bar_type_str: str,
) -> list[Bar]:
    """Convert candle DataFrame to NautilusTrader Bar objects."""
    bar_type = BarType.from_str(bar_type_str)
    bars = []

    for _, row in df.iterrows():
        ts_ns = int(row["timestamp"].timestamp() * 1e9)

        # Clamp volume to avoid precision issues
        vol = max(round(row["volume"], instrument.size_precision), float(instrument.size_increment))

        bar = Bar(
            bar_type=bar_type,
            open=instrument.make_price(Decimal(str(row["open"]))),
            high=instrument.make_price(Decimal(str(row["high"]))),
            low=instrument.make_price(Decimal(str(row["low"]))),
            close=instrument.make_price(Decimal(str(row["close"]))),
            volume=instrument.make_qty(Decimal(str(vol))),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        bars.append(bar)

    return bars


def run_backtest(
    coin: str = "ZRO",
    interval: str = "5m",
    days: int = 14,
    # Mean Reversion params
    entry_band: float = 2.0,
    exit_opposite_band: float = 0.0,
    bb_period: int = 20,
    # Trend params
    trend_ema_fast: int = 21,
    trend_ema_slow: int = 55,
    trend_min_sep_pct: float = 0.05,
    # Confidence params
    rsi_period: int = 14,
    rsi_oversold: float = 0.35,
    rsi_overbought: float = 0.65,
    # Turning + noise
    zscore_lookback: int = 3,
    noise_suppression_window: int = 30,
    # Position sizing
    base_trade_size_usd: float = 50.0,
    scale_with_zscore: bool = True,
    # Risk
    max_adverse_sigma: float = 4.0,
    cooldown_bars: int = 5,
    max_daily_loss_usd: float = 30.0,
    # Label
    label: str = "default",
) -> dict:
    """Run a single backtest configuration and return results."""

    # ── Download data ──
    tf_info = TIMEFRAMES.get(interval)
    if not tf_info:
        raise ValueError(f"Unsupported interval: {interval}. Use: {list(TIMEFRAMES.keys())}")

    hl_interval, bar_suffix, max_days = tf_info
    actual_days = min(days, max_days)

    df = download_candles(coin, hl_interval, actual_days)

    # ── Build IDs ──
    instrument_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
    bar_type_str = f"{instrument_id_str}-{bar_suffix}"

    # ── Create instrument ──
    instrument = create_instrument(coin)

    # ── Convert to bars ──
    bars = candles_to_bars(df, instrument, bar_type_str)

    # ── Configure engine ──
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-002"),
        logging=LoggingConfig(
            log_level="ERROR",
            log_colors=True,
            use_pyo3=False,
        ),
    )
    engine = BacktestEngine(config=engine_config)

    engine.add_venue(
        venue=HYPERLIQUID_VENUE,
        oms_type=OmsType.NETTING,
        book_type=BookType.L1_MBP,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(1000.0, USD)],
        bar_execution=True,
    )

    engine.add_instrument(instrument)
    engine.add_data(bars)

    # ── Strategy config ──
    strategy_config = HLMeanReversionConfig(
        strategy_id=f"BT-MR-{coin}-002",
        instrument_id=instrument_id_str,
        bar_type=bar_type_str,

        bb_period=bb_period,
        bb_std_k=2.0,
        entry_band=entry_band,

        trend_ema_fast=trend_ema_fast,
        trend_ema_slow=trend_ema_slow,
        trend_min_separation_pct=trend_min_sep_pct,

        rsi_period=rsi_period,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,

        zscore_lookback=zscore_lookback,
        rsi_lookback=3,
        noise_suppression_window=noise_suppression_window,

        base_trade_size_usd=base_trade_size_usd,
        max_position_usd=150.0,
        scale_with_zscore=scale_with_zscore,

        exit_opposite_band=exit_opposite_band,
        max_adverse_sigma=max_adverse_sigma,
        atr_period=14,

        cooldown_bars=cooldown_bars,
        max_daily_loss_usd=max_daily_loss_usd,
        max_daily_trades=30,
        max_open_positions=1,
    )

    strategy = HLMeanReversion(config=strategy_config)
    engine.add_strategy(strategy)

    # ── Run ──
    t0 = time.time()
    engine.run()
    elapsed = time.time() - t0

    # ── Extract results ──
    total_trades = strategy._wins + strategy._losses
    win_rate = (strategy._wins / total_trades * 100) if total_trades > 0 else 0
    avg_pnl = (strategy._total_pnl / total_trades) if total_trades > 0 else 0

    results = {
        "label": label,
        "coin": coin,
        "interval": interval,
        "days": actual_days,
        "bars": len(bars),
        "trades": total_trades,
        "wins": strategy._wins,
        "losses": strategy._losses,
        "win_rate": win_rate,
        "total_pnl": strategy._total_pnl,
        "avg_pnl": avg_pnl,
        "entry_band": entry_band,
        "exit_band": exit_opposite_band,
        "trend_ema": f"{trend_ema_fast}/{trend_ema_slow}",
        "rsi_os_ob": f"{rsi_oversold}/{rsi_overbought}",
        "noise_window": noise_suppression_window,
        "elapsed": elapsed,
    }

    engine.dispose()
    return results


def print_results_table(results: list[dict], title: str = "RESULTS") -> None:
    """Print a formatted results table."""
    print(f"\n{'=' * 110}")
    print(f"📊 {title}")
    print(f"{'=' * 110}")
    print(
        f"{'Label':<22} {'TF':<5} {'Bars':>5} {'Trades':>6} "
        f"{'W':>4} {'L':>4} {'WR%':>6} {'P&L':>10} "
        f"{'Avg':>8} {'Band':>5} {'Exit':>5} {'Noise':>5} {'EMA':>7}"
    )
    print("-" * 110)

    for r in results:
        pnl_color = "\033[92m" if r["total_pnl"] > 0 else "\033[91m"
        reset = "\033[0m"
        print(
            f"{r['label']:<22} {r['interval']:<5} {r['bars']:>5} {r['trades']:>6} "
            f"{r['wins']:>4} {r['losses']:>4} {r['win_rate']:>5.1f}% "
            f"{pnl_color}${r['total_pnl']:>+8.4f}{reset} "
            f"${r['avg_pnl']:>+7.4f} {r['entry_band']:>5.1f} "
            f"{r['exit_band']:>5.1f} {r['noise_window']:>5} {r['trend_ema']:>7}"
        )

    print("=" * 110)

    # Best by win rate (min 5 trades)
    tradeable = [r for r in results if r["trades"] >= 5]
    if tradeable:
        best_wr = max(tradeable, key=lambda x: x["win_rate"])
        best_pnl = max(tradeable, key=lambda x: x["total_pnl"])
        print(f"\n🏆 Best Win Rate: {best_wr['label']} — {best_wr['win_rate']:.1f}% ({best_wr['trades']} trades, ${best_wr['total_pnl']:+.4f})")
        print(f"🏆 Best P&L:      {best_pnl['label']} — ${best_pnl['total_pnl']:+.4f} ({best_pnl['win_rate']:.1f}% WR, {best_pnl['trades']} trades)")


def main():
    coin = "ZRO"
    days = 14

    print("=" * 110)
    print(f"🎯 Mean Reversion v002 — {coin} Backtest")
    print(f"   Three-Model Confluence: Z-score + Trend Gate + RSI Confirmation")
    print("=" * 110)

    all_results = []

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: TIMEFRAME SWEEP
    # "Test every timeframe for each asset and pick the highest win rate"
    # ═══════════════════════════════════════════════════════════════
    print("\n\n🔬 PHASE 1: TIMEFRAME SWEEP (default params across timeframes)")
    print("-" * 70)

    for tf in ["5m", "15m", "1h", "4h"]:
        try:
            r = run_backtest(
                coin=coin, interval=tf, days=days,
                entry_band=2.0, exit_opposite_band=0.0,
                trend_ema_fast=21, trend_ema_slow=55,
                rsi_oversold=0.35, rsi_overbought=0.65,
                noise_suppression_window=30,
                cooldown_bars=5,
                label=f"TF={tf}",
            )
            all_results.append(r)
            print(f"  ✅ {tf}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.4f}")
        except Exception as e:
            print(f"  ❌ {tf}: {e}")

    print_results_table(all_results, "TIMEFRAME SWEEP")

    # Find best timeframe
    tradeable_tf = [r for r in all_results if r["trades"] >= 3]
    if not tradeable_tf:
        print("\n⚠️ No timeframe produced enough trades with default params.")
        print("   Loosening entry_band to 1.5σ for the parameter sweep...")
        best_tf = "5m"
        loosen_band = True
    else:
        best_tf_result = max(tradeable_tf, key=lambda x: x["win_rate"])
        best_tf = best_tf_result["interval"]
        loosen_band = False
        print(f"\n📌 Best timeframe: {best_tf} ({best_tf_result['win_rate']:.1f}% WR)")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: ENTRY BAND SWEEP
    # "Wider bands = fewer trades but higher win rate"
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n🔬 PHASE 2: ENTRY BAND SWEEP (on {best_tf})")
    print("-" * 70)

    band_results = []
    for band in [1.0, 1.5, 2.0, 2.5, 3.0]:
        try:
            r = run_backtest(
                coin=coin, interval=best_tf, days=days,
                entry_band=band,
                exit_opposite_band=0.0,
                noise_suppression_window=30,
                cooldown_bars=5,
                label=f"Band=±{band}σ",
            )
            band_results.append(r)
            all_results.append(r)
            print(f"  ✅ ±{band}σ: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.4f}")
        except Exception as e:
            print(f"  ❌ ±{band}σ: {e}")

    print_results_table(band_results, f"ENTRY BAND SWEEP ({best_tf})")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: EXIT BAND SWEEP
    # "Exit when Z-score reaches opposite band"
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n🔬 PHASE 3: EXIT BAND SWEEP (on {best_tf})")
    print("-" * 70)

    # Use the entry band that had the most trades for this sweep
    test_band = 1.5 if loosen_band else 2.0
    exit_results = []
    for exit_b in [-0.5, 0.0, 0.5, 1.0, 1.5]:
        try:
            r = run_backtest(
                coin=coin, interval=best_tf, days=days,
                entry_band=test_band,
                exit_opposite_band=exit_b,
                noise_suppression_window=30,
                cooldown_bars=5,
                label=f"Exit={exit_b}σ (in@{test_band})",
            )
            exit_results.append(r)
            all_results.append(r)
            print(f"  ✅ exit={exit_b}σ: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.4f}")
        except Exception as e:
            print(f"  ❌ exit={exit_b}σ: {e}")

    print_results_table(exit_results, f"EXIT BAND SWEEP ({best_tf})")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 4: TREND EMA SWEEP
    # "The trend model: very tight improved Meta from 71% to 93.75%"
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n🔬 PHASE 4: TREND EMA SWEEP (on {best_tf})")
    print("-" * 70)

    ema_results = []
    ema_pairs = [
        (9, 21,  "Very tight 9/21"),
        (13, 34, "Tight 13/34"),
        (21, 55, "Default 21/55"),
        (34, 89, "Loose 34/89"),
        (50, 200, "Very loose 50/200"),
    ]
    for fast, slow, name in ema_pairs:
        try:
            r = run_backtest(
                coin=coin, interval=best_tf, days=days,
                entry_band=test_band,
                exit_opposite_band=0.0,
                trend_ema_fast=fast,
                trend_ema_slow=slow,
                trend_min_sep_pct=0.05,
                noise_suppression_window=30,
                cooldown_bars=5,
                label=name,
            )
            ema_results.append(r)
            all_results.append(r)
            print(f"  ✅ EMA {fast}/{slow}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.4f}")
        except Exception as e:
            print(f"  ❌ EMA {fast}/{slow}: {e}")

    print_results_table(ema_results, f"TREND EMA SWEEP ({best_tf})")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 5: NOISE SUPPRESSION SWEEP
    # "Noise suppression 30... less signal but more pure signal"
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n🔬 PHASE 5: NOISE SUPPRESSION SWEEP (on {best_tf})")
    print("-" * 70)

    noise_results = []
    for ns in [0, 10, 20, 30, 50, 80]:
        # 0 = disabled
        try:
            r = run_backtest(
                coin=coin, interval=best_tf, days=days,
                entry_band=test_band,
                exit_opposite_band=0.0,
                noise_suppression_window=ns,
                cooldown_bars=5 if ns > 0 else 3,
                label=f"Noise={ns}" if ns > 0 else "NoSuppress",
            )
            noise_results.append(r)
            all_results.append(r)
            print(f"  ✅ noise={ns}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.4f}")
        except Exception as e:
            print(f"  ❌ noise={ns}: {e}")

    print_results_table(noise_results, f"NOISE SUPPRESSION SWEEP ({best_tf})")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 6: RSI SENSITIVITY
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n🔬 PHASE 6: RSI THRESHOLD SWEEP (on {best_tf})")
    print("-" * 70)

    rsi_results = []
    rsi_configs = [
        (0.25, 0.75, "RSI 25/75 (strict)"),
        (0.30, 0.70, "RSI 30/70"),
        (0.35, 0.65, "RSI 35/65 (default)"),
        (0.40, 0.60, "RSI 40/60 (loose)"),
        (0.45, 0.55, "RSI 45/55 (v.loose)"),
    ]
    for os, ob, name in rsi_configs:
        try:
            r = run_backtest(
                coin=coin, interval=best_tf, days=days,
                entry_band=test_band,
                exit_opposite_band=0.0,
                rsi_oversold=os,
                rsi_overbought=ob,
                noise_suppression_window=30,
                cooldown_bars=5,
                label=name,
            )
            rsi_results.append(r)
            all_results.append(r)
            print(f"  ✅ {name}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.4f}")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    print_results_table(rsi_results, f"RSI THRESHOLD SWEEP ({best_tf})")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 7: STYLE PRESETS
    # "Very aggressive for crypto, conservative for stocks"
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n🔬 PHASE 7: STYLE PRESETS (on {best_tf})")
    print("-" * 70)

    style_results = []
    styles = [
        # (label, entry_band, exit_band, noise, rsi_os, rsi_ob, ema_f, ema_s, cooldown, zscore_lb)
        ("V.Aggressive",  1.0,  0.0,  10, 0.40, 0.60,  9,  21, 3, 2),
        ("Aggressive",    1.5,  0.0,  20, 0.40, 0.60, 13,  34, 3, 3),
        ("Neutral",       2.0,  0.0,  30, 0.35, 0.65, 21,  55, 5, 3),
        ("Conservative",  2.5,  0.5,  40, 0.30, 0.70, 34,  89, 8, 4),
        ("V.Conservative", 3.0, 1.0,  60, 0.25, 0.75, 50, 200, 10, 5),
    ]
    for name, eb, exb, ns, ros, rob, ef, es, cd, zlb in styles:
        try:
            r = run_backtest(
                coin=coin, interval=best_tf, days=days,
                entry_band=eb,
                exit_opposite_band=exb,
                trend_ema_fast=ef,
                trend_ema_slow=es,
                rsi_oversold=ros,
                rsi_overbought=rob,
                noise_suppression_window=ns,
                zscore_lookback=zlb,
                cooldown_bars=cd,
                label=name,
            )
            style_results.append(r)
            all_results.append(r)
            print(f"  ✅ {name}: {r['trades']} trades, {r['win_rate']:.1f}% WR, ${r['total_pnl']:+.4f}")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    print_results_table(style_results, f"STYLE PRESETS ({best_tf})")

    # ═══════════════════════════════════════════════════════════════
    # GRAND SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "=" * 110)
    print("🏆 GRAND SUMMARY — ALL CONFIGURATIONS")
    print("=" * 110)

    # Deduplicate by label
    seen = set()
    unique_results = []
    for r in all_results:
        key = r["label"] + r["interval"]
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    # Sort by P&L
    unique_results.sort(key=lambda x: x["total_pnl"], reverse=True)
    print_results_table(unique_results, "ALL CONFIGS (sorted by P&L)")

    # Sort by win rate (min 5 trades)
    tradeable_all = [r for r in unique_results if r["trades"] >= 5]
    if tradeable_all:
        tradeable_all.sort(key=lambda x: x["win_rate"], reverse=True)
        print_results_table(tradeable_all, "TRADEABLE CONFIGS (≥5 trades, sorted by WR%)")

    # Save results to JSON
    results_file = Path(__file__).resolve().parent.parent.parent / "backtest_results" / "mr_v002_sweep.json"
    results_file.parent.mkdir(parents=True, exist_ok=True)
    with open(results_file, "w") as f:
        json.dump(unique_results, f, indent=2)
    print(f"\n💾 Results saved to {results_file}")


if __name__ == "__main__":
    main()
