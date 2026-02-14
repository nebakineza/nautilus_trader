#!/usr/bin/env python3
"""Run HFT Grid MM v001 GENESIS on Hyperliquid — VOLUME MODE (TESTNET).

Tuned for maximum fill rate and volume accumulation.
Goal: achieve VIP tiers as fast as possible.

STRATEGY:
    - 3.5 bps half-spread (just above 1.5 bps maker fee = ~5.5 bps net)
    - 10 grid levels per side (wide capture net)
    - $200 per level = $4,000 total grid exposure
    - 1-second refresh cycle
    - Aggressive inventory skewing (shed positions fast)
    - Multi-instrument: ETH + BTC for 2x volume

FEE MATH (VIP0):
    Maker: 1.5 bps → at 3.5 bps half-spread → 7 bps gross → 4 bps net per roundtrip
    Breakeven: half_spread > 1.5 bps (maker fee)
    Target: $5M 14d vol for VIP1 → need $357K/day → ~$15K/hour

USAGE:
    source .env
    .venv/bin/python scripts/hyperliquid/run_hl_grid_volume_testnet.py

STOP:
    Ctrl+C (graceful shutdown with position flattening)
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

from strategy.hl_grid_mm_v001_genesis import HLGridMM, HLGridMMConfig


def main():
    private_key = os.environ.get("HYPERLIQUID_TESTNET_PK") or os.environ.get("HYPERLIQUID_PK")
    wallet_address = os.environ.get("HYPERLIQUID_WALLET")
    if not private_key:
        print("❌ Set HYPERLIQUID_TESTNET_PK in environment")
        sys.exit(1)

    IS_TESTNET = True
    INSTRUMENTS = ["ETH-USD-PERP.HYPERLIQUID"]

    print("=" * 60)
    print("🚀 VOLUME MODE — HFT Grid MM v001 GENESIS — TESTNET")
    print(f"   Time:        {datetime.now(timezone.utc).isoformat()}")
    print(f"   Instruments: {INSTRUMENTS}")
    print(f"   Network:     TESTNET")
    print(f"   Key:         {private_key[:6]}...{private_key[-4:]}")
    print()
    print("   VOLUME TARGETS:")
    print("   VIP1 = $5M 14d vol  → $357K/day → $14.9K/hour")
    print("   VIP2 = $25M 14d vol → $1.79M/day → $74.4K/hour")
    print()
    print("   FEE EDGE:")
    print("   Maker 1.5 bps | half_spread 3.5 bps → ~4 bps net/RT")
    print("=" * 60)

    node_config = TradingNodeConfig(
        trader_id=TraderId("HL-VOL-001"),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
            log_colors=True,
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

    strategies = []

    # ETH — Highest fill probability, profitable spreads
    strategies.append(HLGridMM(config=HLGridMMConfig(
        strategy_id="HL-VOL-ETH-001",
        instrument_id="ETH-USD-PERP.HYPERLIQUID",
        grid_levels=10,
        half_spread_bps=5.0,         # 5 bps half-spread → 10 bps gross → 7 bps net
        grid_interval_bps=2.0,       # Dense grid for max fill rate
        order_qty_usd=200.0,         # $200 per level
        max_position_usd=3000.0,     # $3K max exposure (tighter for safety)
        skew_bps_per_unit=3.0,       # Aggressive skewing to shed fast
        min_profit_bps=2.0,          # Minimum 2 bps profit per roundtrip
        refresh_interval_secs=3.0,   # 3 second refresh
        min_refresh_interval_secs=2.0,
        max_loss_usd=-50.0,          # Kill at -$50 (testnet)
        max_inventory_age_secs=600.0,  # Force exit after 10 min (testnet is thin)
        pause_on_spread_collapse_bps=1.0,
        maker_fee_bps=1.5,
        taker_fee_bps=4.5,
    )))

    node = TradingNode(config=node_config)
    for strategy in strategies:
        node.trader.add_strategy(strategy)
    node.add_data_client_factory(HYPERLIQUID, HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory(HYPERLIQUID, HyperliquidLiveExecClientFactory)
    node.build()

    try:
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️ Ctrl+C — shutting down gracefully...")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
