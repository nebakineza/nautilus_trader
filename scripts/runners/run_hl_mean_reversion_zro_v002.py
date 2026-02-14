#!/usr/bin/env python3
"""Live runner for Mean Reversion v002 on Hyperliquid.

Runs the three-model confluence strategy (Z-score + Trend Gate + RSI) on
ZRO-USD-PERP via Hyperliquid mainnet.

BACKTESTED OPTIMAL PARAMS (ZRO 5m, 14d):
    EMA 34/89, entry ±1.0σ, exit ±1.5σ → 70.6% WR, +$15.16 (68 trades)

USAGE:
    # Set your wallet private key
    export HYPERLIQUID_WALLET_API=0x...

    # Run live
    .venv/bin/python scripts/runners/run_hl_mean_reversion_zro_v002.py

    # Or with custom params
    .venv/bin/python scripts/runners/run_hl_mean_reversion_zro_v002.py \\
        --entry-band 1.5 --exit-band 1.0 --size 30

ENVIRONMENT VARIABLES:
    HYPERLIQUID_WALLET_API  — Private key for trading (required)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add project root for strategy import
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
from nautilus_trader.model.identifiers import InstrumentId, TraderId

from strategy.hl_mean_reversion_v002 import HLMeanReversion, HLMeanReversionConfig


# =============================================================================
# DEFAULTS — from backtest optimization
# =============================================================================
COIN = "ZRO"
INSTRUMENT_ID = f"{COIN}-USD-PERP.HYPERLIQUID"
BAR_TYPE = f"{INSTRUMENT_ID}-5-MINUTE-LAST-EXTERNAL"


def parse_args():
    p = argparse.ArgumentParser(description="Mean Reversion v002 Live Runner")
    p.add_argument("--coin", default=COIN, help="Coin to trade (default: ZRO)")
    p.add_argument("--bar-interval", default="5m", choices=["1m", "5m", "15m", "1h", "4h"],
                    help="Bar interval (default: 5m)")

    # Strategy params
    p.add_argument("--entry-band", type=float, default=1.0,
                    help="Entry band in sigma (default: 1.0)")
    p.add_argument("--exit-band", type=float, default=1.5,
                    help="Exit opposite band in sigma (default: 1.5)")
    p.add_argument("--ema-fast", type=int, default=34,
                    help="Trend EMA fast period (default: 34)")
    p.add_argument("--ema-slow", type=int, default=89,
                    help="Trend EMA slow period (default: 89)")
    p.add_argument("--rsi-oversold", type=float, default=0.35)
    p.add_argument("--rsi-overbought", type=float, default=0.65)
    p.add_argument("--noise-window", type=int, default=10)
    p.add_argument("--cooldown", type=int, default=5)

    # Position sizing
    p.add_argument("--size", type=float, default=50.0,
                    help="Base trade size in USD (default: 50)")
    p.add_argument("--max-size", type=float, default=150.0,
                    help="Max position in USD (default: 150)")

    # Risk controls (v002.1)
    p.add_argument("--hard-stop-pct", type=float, default=1.5,
                    help="Hard stop-loss %% from entry (default: 1.5)")
    p.add_argument("--max-hold-bars", type=int, default=40,
                    help="Force exit after N bars (default: 40)")
    p.add_argument("--strict-trend", action="store_true", default=True,
                    help="Require strict trend alignment (default: True)")
    p.add_argument("--no-strict-trend", dest="strict_trend", action="store_false",
                    help="Allow flat-trend entries")
    p.add_argument("--max-adverse-sigma", type=float, default=4.0)
    p.add_argument("--max-daily-loss", type=float, default=30.0)
    p.add_argument("--max-daily-trades", type=int, default=30)

    # Infra
    p.add_argument("--testnet", action="store_true", help="Use testnet")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    return p.parse_args()


def main():
    args = parse_args()

    # Validate private key
    pk_env = "HYPERLIQUID_TESTNET_PK" if args.testnet else "HYPERLIQUID_WALLET_API"
    if not os.environ.get(pk_env):
        print(f"❌ Set {pk_env} environment variable with your private key")
        sys.exit(1)

    # Build bar type string from interval
    interval_map = {
        "1m":  "1-MINUTE-LAST-EXTERNAL",
        "5m":  "5-MINUTE-LAST-EXTERNAL",
        "15m": "15-MINUTE-LAST-EXTERNAL",
        "1h":  "60-MINUTE-LAST-EXTERNAL",
        "4h":  "240-MINUTE-LAST-EXTERNAL",
    }
    instrument_id_str = f"{args.coin}-USD-PERP.HYPERLIQUID"
    bar_type_str = f"{instrument_id_str}-{interval_map[args.bar_interval]}"

    print("=" * 70)
    print(f"\ud83c\udfaf Mean Reversion v002.1 \u2014 LIVE on {args.coin}")
    print(f"   Three-Model Confluence: Z-score + Trend Gate + RSI")
    print("=" * 70)
    print(f"   Instrument:   {instrument_id_str}")
    print(f"   Bar Type:     {bar_type_str}")
    print(f"   Entry Band:   \u00b1{args.entry_band}\u03c3")
    print(f"   Exit Band:    \u00b1{args.exit_band}\u03c3")
    print(f"   Trend EMA:    {args.ema_fast}/{args.ema_slow} strict={args.strict_trend}")
    print(f"   RSI:          {args.rsi_oversold}/{args.rsi_overbought}")
    print(f"   Noise Window: {args.noise_window} bars")
    print(f"   Hard Stop:    {args.hard_stop_pct}%")
    print(f"   Max Hold:     {args.max_hold_bars} bars")
    print(f"   Size:         ${args.size} (max ${args.max_size})")
    print(f"   Testnet:      {args.testnet}")
    print("=" * 70)

    # ── Trading Node Config ──
    node_config = TradingNodeConfig(
        trader_id=TraderId(f"MR-{args.coin}-002"),
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
                private_key=None,  # Loaded from env var
                vault_address=None,
                instrument_provider=InstrumentProviderConfig(load_all=True),
                testnet=args.testnet,
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
        strategy_id=f"MR-{args.coin}-002",
        instrument_id=instrument_id_str,
        bar_type=bar_type_str,

        # Mean Reversion
        bb_period=20,
        bb_std_k=2.0,
        entry_band=args.entry_band,

        # Trend
        trend_ema_fast=args.ema_fast,
        trend_ema_slow=args.ema_slow,
        trend_min_separation_pct=0.05,

        # Confidence
        rsi_period=14,
        rsi_oversold=args.rsi_oversold,
        rsi_overbought=args.rsi_overbought,

        # Turning point
        zscore_lookback=3,
        rsi_lookback=3,

        # Noise suppression
        noise_suppression_window=args.noise_window,

        # Position sizing
        base_trade_size_usd=args.size,
        max_position_usd=args.max_size,
        scale_with_zscore=True,

        # Exits
        exit_opposite_band=args.exit_band,
        max_adverse_sigma=args.max_adverse_sigma,
        hard_stop_pct=args.hard_stop_pct,
        max_hold_bars=args.max_hold_bars,
        require_strict_trend=args.strict_trend,
        atr_period=14,

        # Risk
        cooldown_bars=args.cooldown,
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

    print(f"\n🚀 Starting Mean Reversion v002 on {args.coin}...")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
