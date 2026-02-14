#!/usr/bin/env python3
"""Run HFT Grid MM v001 GENESIS on Hyperliquid TESTNET.

Deploys the grid market maker on ETH-USD-PERP with conservative parameters.
Uses testnet USDC — zero financial risk.

USAGE:
    source .env
    .venv/bin/python scripts/hyperliquid/run_hl_grid_testnet.py

STOP:
    Ctrl+C (graceful shutdown with position flattening)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
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
        print("❌ Set HYPERLIQUID_TESTNET_PK or HYPERLIQUID_PK in environment")
        sys.exit(1)

    # --- Configuration ---
    INSTRUMENT = "ETH-USD-PERP.HYPERLIQUID"
    IS_TESTNET = True

    print("=" * 60)
    print("⚡ HFT GRID MM v001 GENESIS — Hyperliquid TESTNET")
    print(f"   Time:       {datetime.now(timezone.utc).isoformat()}")
    print(f"   Instrument: {INSTRUMENT}")
    print(f"   Network:    {'TESTNET' if IS_TESTNET else '🔴 MAINNET'}")
    print(f"   Key:        {private_key[:6]}...{private_key[-4:]}")
    print("=" * 60)

    node_config = TradingNodeConfig(
        trader_id=TraderId("HL-GRID-001"),
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
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
                ),
            ),
        },
        exec_clients={
            HYPERLIQUID: HyperliquidExecClientConfig(
                private_key=private_key,
                wallet_address=wallet_address,
                testnet=IS_TESTNET,
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
                ),
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    # VOLUME-TUNED config:
    # - $200 per grid level × 10 levels = $2,000 per side
    # - 3.5 bps half-spread = 7 bps gross → ~4 bps net after 1.5bp maker fee
    # - Max position $5K (needs funded account)
    # - 1-second refresh for maximum fill rate
    strategy_config = HLGridMMConfig(
        strategy_id="HL-GRID-ETH-001",
        instrument_id=INSTRUMENT,
        grid_levels=10,              # 10 per side (20 total)
        half_spread_bps=3.5,         # Tight — just above 1.5 bps maker fee
        grid_interval_bps=2.0,       # Dense grid
        order_qty_usd=200.0,         # $200 per level
        max_position_usd=5000.0,     # Max $5K exposure
        skew_bps_per_unit=2.0,       # Aggressive skewing
        refresh_interval_secs=1.0,   # 1-second refresh
        min_refresh_interval_secs=0.3,
        max_loss_usd=-200.0,         # Kill at -$200
        max_inventory_age_secs=300.0,  # Force exit after 5 min
        pause_on_spread_collapse_bps=1.0,
        maker_fee_bps=1.5,           # VIP0
        taker_fee_bps=4.5,
    )

    node = TradingNode(config=node_config)
    strategy = HLGridMM(config=strategy_config)
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
