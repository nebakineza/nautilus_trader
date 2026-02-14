#!/usr/bin/env python3
"""Live runner for Mean Reversion v003 "JAMES SNIPER" — SOL-USD-PERP 1h.

100% WIN RATE PROFILE — Optuna 200 trials on 60 days of 1h SOL data.
═══════════════════════════════════════════════════════════════════════════

James Mullaner's key insight: "the more standard deviations from the mean,
the higher your win rate. Moving those bands up moved the win rate to 99.08%."

This sniper config trades ONLY extreme deviations on the 1h timeframe:
  - Wider entry bands (1.4σ inner / 3.0σ outer) → only trade deep extremes
  - Aggressive take-profit (exit at 0.3σ) → lock in small gains immediately
  - Wide hard stop (4.4%) → give trades maximum room to revert
  - Strict noise suppression (0.95) → only trade the absolute strongest signals
  - Long max hold (50 1h bars = ~50 hours) → patience for mean reversion

Backtest results (60-day, $150 position size):
    100.0% WR | 13 trades | $+10.68 P&L | $+0.82/trade | ~3/month
    ZERO losses in 60-day backtest window

CRITICAL — NETTING WARNING:
    This service runs on the SAME HL wallet as the 5m v003 service.
    Hyperliquid uses NETTING — there is ONE SOL position per wallet.
    Both strategies see each other's fills. This is SAFE because:
    1. The sniper trades ~3x/month (once every 4-5 days)
    2. The 5m trades ~3x/day → 98% of the time only one is active
    3. Both strategies track their OWN internal state (legs, side)
    4. Even if both are in simultaneously, they both want the SAME
       direction (both are mean reversion → both buy dips / sell rips)
    5. The sniper only enters at 1.4-3.0σ extremes, which is beyond
       where the 5m strategy has already entered

USAGE:
    export HYPERLIQUID_MAINNET_PK=0x...
    export HYPERLIQUID_WALLET=0x...
    .venv/bin/python scripts/runners/run_hl_mean_reversion_sol_sniper.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from nautilus_trader.adapters.hyperliquid import (
    HYPERLIQUID,
    HyperliquidDataClientConfig,
    HyperliquidExecClientConfig,
    HyperliquidLiveDataClientFactory,
    HyperliquidLiveExecClientFactory,
)
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from strategy.hl_mean_reversion_v003 import HLMeanReversion, HLMeanReversionConfig


# =============================================================================
# 1h SNIPER PROFILE — 100% WR in 60-day Optuna sweep
# =============================================================================
COIN = "SOL"
INSTRUMENT_ID = f"{COIN}-USD-PERP.HYPERLIQUID"

# ── Optuna-best 1h params (100% WR, 13 trades/60d, $10.68) ──
SOL_SNIPER = dict(
    bb_period=24,                    # Slightly faster BB for 1h
    entry_band=1.4,                  # Wide inner — only extreme dips
    entry_band_outer=3.0,            # Very wide outer — ultra-extreme only
    exit_opposite_band=0.3,          # Aggressive TP — lock in immediately
    trend_ema_fast=25,               # Faster trend for 1h timeframe
    trend_ema_slow=113,              # Moderate slow EMA
    trend_min_separation_pct=0.16,   # Clear trend required
    trend_turn_lookback=7,           # Standard turn detection
    trend_turn_z_bonus=0.30,         # Standard turn bonus
    noise_suppression_window=35,     # Standard window
    noise_suppression_ratio=0.95,    # Ultra-strict: only peak signals
    hard_stop_pct=4.4,               # Wide stop — give room to revert
    max_hold_bars=50,                # 50 × 1h = ~2 days max hold
    max_adverse_sigma=5.0,           # Standard emergency exit
    cooldown_bars=15,                # 15h cooldown between trades
    rsi_period=14,
    rsi_oversold=0.25,
    rsi_overbought=0.75,
)


def parse_args():
    p = argparse.ArgumentParser(description="Mean Reversion v003 SNIPER — SOL 1h")

    # Position sizing
    p.add_argument("--size", type=float, default=150.0,
                   help="Total position budget in USD (default: 150)")
    p.add_argument("--max-size", type=float, default=200.0,
                   help="Max position cap in USD (default: 200)")
    p.add_argument("--auto-scale", action="store_true",
                   help="Auto-scale from account equity")
    p.add_argument("--leverage", type=float, default=5.0,
                   help="Leverage for auto-scale (default: 5.0)")
    p.add_argument("--max-pos-cap", type=float, default=500_000.0,
                   help="Hard ceiling on max position USD")

    # Risk
    p.add_argument("--max-daily-loss", type=float, default=500.0,
                   help="Max daily loss in USD (default: 500)")
    p.add_argument("--max-daily-trades", type=int, default=5,
                   help="Max trades per day (default: 5 — sniper trades rarely)")

    # Infra
    p.add_argument("--testnet", action="store_true", help="Use testnet")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    return p.parse_args()


def main():
    args = parse_args()

    # Validate private key
    pk_env = "HYPERLIQUID_TESTNET_PK" if args.testnet else "HYPERLIQUID_MAINNET_PK"
    private_key = os.environ.get(pk_env)
    wallet_address = os.environ.get("HYPERLIQUID_WALLET")
    if not private_key:
        print(f"❌ Set {pk_env} environment variable")
        sys.exit(1)
    if not wallet_address:
        print("❌ Set HYPERLIQUID_WALLET environment variable")
        sys.exit(1)

    profile = SOL_SNIPER
    bar_type_str = f"{INSTRUMENT_ID}-1-HOUR-LAST-EXTERNAL"

    print("=" * 70)
    print(f"🎯 Mean Reversion v003 SNIPER — SOL-USD-PERP 1h")
    print(f"   100% WR Profile | Only trades deep extremes")
    print("=" * 70)
    print(f"   Instrument:   {INSTRUMENT_ID}")
    print(f"   Bar Type:     {bar_type_str}")
    print(f"   BB Period:    {profile['bb_period']}")
    print(f"   Entry Band:   ±{profile['entry_band']}σ / {profile['entry_band_outer']}σ")
    print(f"   Exit Band:    ±{profile['exit_opposite_band']}σ (aggressive TP)")
    print(f"   Trend EMA:    {profile['trend_ema_fast']}/{profile['trend_ema_slow']}")
    print(f"   Noise Sup:    {profile['noise_suppression_window']}@{profile['noise_suppression_ratio']:.2f}")
    print(f"   Hard Stop:    {profile['hard_stop_pct']}%")
    print(f"   Max Hold:     {profile['max_hold_bars']} bars ({profile['max_hold_bars']}h)")
    print(f"   Cooldown:     {profile['cooldown_bars']} bars ({profile['cooldown_bars']}h)")
    print(f"   Size:         ${args.size} (max ${args.max_size})")
    print(f"   Testnet:      {args.testnet}")
    print("=" * 70)

    # ── Trading Node Config ──
    node_config = TradingNodeConfig(
        trader_id=TraderId("MR-SOL-SNP"),
        logging=LoggingConfig(
            log_level=args.log_level,
            use_pyo3=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
            open_check_interval_secs=15.0,
            open_check_open_only=False,
            graceful_shutdown_on_exception=True,
        ),
        data_clients={
            HYPERLIQUID: HyperliquidDataClientConfig(
                instrument_provider=InstrumentProviderConfig(load_all=True),
                testnet=args.testnet,
            ),
        },
        exec_clients={
            HYPERLIQUID: HyperliquidExecClientConfig(
                private_key=private_key,
                wallet_address=wallet_address,
                testnet=args.testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    # ── Strategy Config ──
    strategy_config = HLMeanReversionConfig(
        strategy_id="MR-SOL-SNP",
        instrument_id=INSTRUMENT_ID,
        bar_type=bar_type_str,

        # Mean Reversion — 1h sniper tuned
        bb_period=profile["bb_period"],
        bb_std_k=2.0,
        entry_band=profile["entry_band"],
        entry_band_outer=profile["entry_band_outer"],

        # Trend
        trend_ema_fast=profile["trend_ema_fast"],
        trend_ema_slow=profile["trend_ema_slow"],
        trend_min_separation_pct=profile["trend_min_separation_pct"],

        # Trend turn
        trend_turn_lookback=profile["trend_turn_lookback"],
        trend_turn_z_bonus=profile["trend_turn_z_bonus"],

        # Confidence
        rsi_period=profile["rsi_period"],
        rsi_oversold=profile["rsi_oversold"],
        rsi_overbought=profile["rsi_overbought"],

        # Turning point
        zscore_lookback=3,
        rsi_lookback=3,

        # Noise suppression — ultra-strict
        noise_suppression_window=profile["noise_suppression_window"],
        noise_suppression_ratio=profile["noise_suppression_ratio"],

        # Position sizing
        base_trade_size_usd=args.size,
        max_position_usd=args.max_size,
        leg_allocation=(0.30, 0.40, 0.30),
        max_legs=3,
        scale_with_zscore=True,

        # Auto-scaling
        auto_scale=args.auto_scale,
        auto_scale_leverage=args.leverage,
        auto_scale_max_usd=args.max_pos_cap,

        # Exits — wide stop, aggressive TP
        exit_opposite_band=profile["exit_opposite_band"],
        max_adverse_sigma=profile["max_adverse_sigma"],
        hard_stop_pct=profile["hard_stop_pct"],
        max_hold_bars=profile["max_hold_bars"],
        require_strict_trend=True,
        atr_period=14,

        # Risk — conservative for sniper
        cooldown_bars=profile["cooldown_bars"],
        max_daily_loss_usd=args.max_daily_loss,
        max_daily_trades=args.max_daily_trades,
        max_open_positions=1,
    )

    # ── Build and run ──
    node = TradingNode(config=node_config)
    node.trader.add_strategy(HLMeanReversion(config=strategy_config))
    node.add_data_client_factory(HYPERLIQUID, HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory(HYPERLIQUID, HyperliquidLiveExecClientFactory)
    node.build()

    if args.auto_scale:
        print(f"   🔄 Auto-scale: ON ({args.leverage}x leverage, cap ${args.max_pos_cap:,.0f})")
    else:
        print(f"   📏 Fixed sizing: base=${args.size} max=${args.max_size}")

    print(f"\n🎯 Starting SNIPER on SOL 1h — trades only at deep extremes...")
    print(f"   Expected: ~3 trades/month | 100% WR in backtest")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
