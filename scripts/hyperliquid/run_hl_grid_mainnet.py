#!/usr/bin/env python3
"""Run HFT Grid MM v001 GENESIS on Hyperliquid MAINNET.

Deploys the grid market maker on ETH-USD-PERP with parameters tuned for
$320 account balance and VIP0 fee tier (1.0 bps maker).

CAPITAL ALLOCATION:
    Account:     $320 USDC
    Per level:   $25 × 10 levels = $250 committed
    Reserve:     $70 (margin buffer for unrealized PnL)
    Max exposure:$300

EXPECTED PERFORMANCE (VIP0):
    Gross spread:    24 bps (12 bps half-spread × 2)
    Fees roundtrip:  2 bps (1 bps maker × 2 sides)
    Net per round:  ~22 bps
    Target fills:    10-50/hour
    Target volume:   $5K-25K/day (VIP1 in ~1 month)

USAGE:
    source .env
    .venv/bin/python scripts/hyperliquid/run_hl_grid_mainnet.py
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
    private_key = os.environ.get("HYPERLIQUID_MAINNET_PK") or os.environ.get("HYPERLIQUID_PK")
    if not private_key:
        print("❌ Set HYPERLIQUID_MAINNET_PK or HYPERLIQUID_PK in environment")
        sys.exit(1)

    INSTRUMENT = "ETH-USD-PERP.HYPERLIQUID"

    print("=" * 60)
    print("⚡ HFT GRID MM v001 GENESIS — Hyperliquid MAINNET")
    print(f"   Time:       {datetime.now(timezone.utc).isoformat()}")
    print(f"   Instrument: {INSTRUMENT}")
    print(f"   Network:    🔴 MAINNET — REAL FUNDS")
    print(f"   Key:        {private_key[:6]}...{private_key[-4:]}")
    print(f"   Account:    ~$320 USDC")
    print("=" * 60)

    confirm = input("\n⚠️  MAINNET with REAL funds. Type 'CONFIRM' to proceed: ")
    if confirm.strip() != "CONFIRM":
        print("Aborted.")
        sys.exit(0)

    node_config = TradingNodeConfig(
        trader_id=TraderId("HL-GRID-001"),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
            log_colors=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,    # Reconcile on mainnet
        ),
        data_clients={
            HYPERLIQUID: HyperliquidDataClientConfig(
                testnet=False,      # MAINNET
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
                ),
            ),
        },
        exec_clients={
            HYPERLIQUID: HyperliquidExecClientConfig(
                private_key=private_key,
                testnet=False,      # MAINNET
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
                ),
                max_retries=3,
                retry_delay_initial_ms=500,
                retry_delay_max_ms=5000,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=15.0,
        timeout_portfolio=15.0,
        timeout_disconnection=15.0,
        timeout_post_stop=10.0,
    )

    # Production parameters for $320 account
    strategy_config = HLGridMMConfig(
        strategy_id="HL-GRID-ETH-001",
        instrument_id=INSTRUMENT,

        # Grid structure — conservative for $320 capital
        grid_levels=10,              # 10 per side (20 total orders)
        half_spread_bps=12.0,        # Inner edge 12 bps from mid
        grid_interval_bps=6.0,       # 6 bps between levels → covers 72 bps depth
        order_qty_usd=25.0,          # $25 per level ($250 per side)

        # Position limits — strict for small account
        max_position_usd=300.0,      # Max $300 (leaves $20 margin buffer)

        # Inventory management
        skew_enabled=True,
        skew_bps_per_unit=1.0,       # 1 bps shift per normalized unit (more aggressive skew)

        # Refresh
        refresh_interval_secs=3.0,   # 3 second refresh (conservative, saves rate limit)

        # Risk controls — tight for real money
        max_loss_usd=-30.0,          # Kill at -$30 (~10% of capital)
        max_inventory_age_secs=300.0,  # Force exit after 5 min
        pause_on_spread_collapse_bps=1.5,

        # Fee tier
        maker_fee_bps=1.0,           # VIP0
        taker_fee_bps=3.5,
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
