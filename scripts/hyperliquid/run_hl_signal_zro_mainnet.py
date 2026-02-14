#!/usr/bin/env python3
"""Run Signal Trader v001 on Hyperliquid — ZRO MAINNET.

Signal-based directional strategy using RSI, Bollinger Bands, EMA, Stochastics.
Waits for confluent signals, enters with bracket orders (SL + TP).

ZRO-USD-PERP: LayerZero token
    szDecimals=1, maxLeverage=5
    5m ATR: ~$0.005-0.010 (~25-50 bps)

STRATEGY:
    - 5-minute bars for signal generation
    - RSI + BB + EMA + Stochastics confluence (3/4 required)
    - Market entry with ATR-based stop-loss and take-profit
    - R:R = 1.5:1 (TP=3×ATR, SL=2×ATR) → need >40% win rate
    - Max 1 position at a time
    - Cooldown between trades

RISK:
    trade_size=$50 per position
    SL ~$0.010-0.020 per trade (~$0.50-1.00 risk)
    TP ~$0.015-0.030 per trade (~$0.75-1.50 reward)
    Daily loss limit: $20

USAGE:
    source .env
    .venv/bin/python scripts/hyperliquid/run_hl_signal_zro_mainnet.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from nautilus_trader.adapters.hyperliquid import HYPERLIQUID
from nautilus_trader.adapters.hyperliquid import HyperliquidDataClientConfig
from nautilus_trader.adapters.hyperliquid import HyperliquidExecClientConfig
from nautilus_trader.adapters.hyperliquid import HyperliquidLiveDataClientFactory
from nautilus_trader.adapters.hyperliquid import HyperliquidLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from strategy.hl_signal_trader_v001 import HLSignalTrader, HLSignalTraderConfig


def main():
    # Use mainnet key
    private_key = os.environ.get("HYPERLIQUID_MAINNET_PK")
    wallet_address = os.environ.get("HYPERLIQUID_WALLET")
    if not private_key:
        print("❌ Set HYPERLIQUID_MAINNET_PK in environment")
        sys.exit(1)
    if not wallet_address:
        print("❌ Set HYPERLIQUID_WALLET in environment")
        sys.exit(1)

    IS_TESTNET = False
    PAIR = "ZRO"
    INSTRUMENT = f"{PAIR}-USD-PERP.HYPERLIQUID"
    BAR_TYPE = f"{INSTRUMENT}-5-MINUTE-LAST-EXTERNAL"

    print("=" * 60)
    print(f"🎯 MAINNET — Signal Trader v001 — {PAIR}")
    print(f"   Time:        {datetime.now(timezone.utc).isoformat()}")
    print(f"   Instrument:  {INSTRUMENT}")
    print(f"   Bar Type:    {BAR_TYPE}")
    print(f"   Mode:        SIGNAL-BASED (RSI+BB+EMA+Stoch)")
    print(f"   Trade Size:  $50 per entry")
    print(f"   SL/TP:       2×ATR / 3×ATR (R:R 1.5:1)")
    print(f"   Daily Limit: $20 max loss")
    print("=" * 60)

    node_config = TradingNodeConfig(
        trader_id=TraderId(f"HL-SIG-{PAIR}-001"),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=False,
        ),
        data_clients={
            HYPERLIQUID: HyperliquidDataClientConfig(
                testnet=IS_TESTNET,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            HYPERLIQUID: HyperliquidExecClientConfig(
                private_key=private_key,
                wallet_address=wallet_address,
                testnet=IS_TESTNET,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    strategy = HLSignalTrader(config=HLSignalTraderConfig(
        strategy_id=f"HL-SIG-{PAIR}-001",
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,

        # --- Position sizing ---
        trade_size_usd=50.0,          # $50 per trade — conservative start
        max_simultaneous=1,           # Only 1 position at a time

        # --- RSI ---
        rsi_period=14,
        rsi_oversold=0.30,            # RSI < 30 (on 0-1 scale)
        rsi_overbought=0.70,          # RSI > 70
        rsi_extreme_oversold=0.20,    # RSI < 20 (strong signal bonus)
        rsi_extreme_overbought=0.80,  # RSI > 80

        # --- EMA trend ---
        ema_fast_period=9,            # 9-bar EMA (45 min on 5m chart)
        ema_slow_period=21,           # 21-bar EMA (~1.75 hours on 5m chart)

        # --- Bollinger Bands ---
        bb_period=20,                 # 20-bar BB (~1.67 hours on 5m chart)
        bb_std=2.0,                   # Standard 2σ

        # --- Stochastics (KDJ equivalent) ---
        stoch_k_period=14,            # %K period
        stoch_d_period=3,             # %D smoothing
        stoch_oversold=20.0,          # %K < 20 = oversold
        stoch_overbought=80.0,        # %K > 80 = overbought

        # --- ATR for SL/TP ---
        atr_period=14,
        sl_atr_multiplier=2.0,        # SL = 2 × ATR from entry
        tp_atr_multiplier=3.0,        # TP = 3 × ATR from entry → R:R = 1.5:1

        # --- Signal confluence ---
        min_signals=3,                # Need 3 of 4 indicators to agree

        # --- Regime detection ---
        bb_width_trending_pct=1.5,    # BB width > 1.5% = trending
        bb_width_ranging_pct=0.8,     # BB width < 0.8% = ranging

        # --- Risk controls ---
        cooldown_bars=3,              # Wait 3 bars (15 min) between trades
        max_daily_loss_usd=20.0,      # Stop for the day after $20 loss
        max_daily_trades=20,          # Max 20 trades per day

        # --- Candle body filter ---
        body_filter_enabled=True,
        body_filter_pct=0.20,         # Body > 20% of avg (from Fast RSI article)
        body_avg_period=20,
    ))

    node = TradingNode(config=node_config)
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(HYPERLIQUID, HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory(HYPERLIQUID, HyperliquidLiveExecClientFactory)
    node.build()

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
