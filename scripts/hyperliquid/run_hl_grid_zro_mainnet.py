#!/usr/bin/env python3
"""Run HFT Grid MM v001 GENESIS on Hyperliquid — ZRO MAINNET.

ZRO-USD-PERP: LayerZero token
    szDecimals=1, maxLeverage=5
    Typical spread: 5-10 bps (vs 3.0 bps roundtrip cost)
    24h volume: ~$100M+
    1h ATR: ~4-5%

EDGE ANALYSIS (VIP0):
    Spread: ~8 bps typical
    Roundtrip maker fees: 3.0 bps (1.5 bps each leg)
    Net edge per roundtrip: ~5 bps
    Per $200 fill pair: ~$0.10 profit

GRID DESIGN:
    half_spread=6.0 bps → inner levels at ~6 bps from mid
    grid_interval=3.0 bps → levels spaced 3 bps apart
    10 levels per side → captures 6-36 bps range
    $100 per level → $2,000 total grid exposure
    Wider spread than ETH testnet because this is REAL MONEY

VOLUME PROJECTION (conservative):
    At 1 fill/min avg → 60 fills/hr → 60 × $100 = $6K/hr
    24h: ~$144K  |  14d: ~$2M
    At 3 fills/min → $18K/hr → $432K/day → $6M/14d (VIP1!)

RISK CONTROLS:
    max_position_usd=$1,000 (small to start)
    max_inventory_age=300s (force exit after 5 min)
    max_loss=-$25 kill switch

USAGE:
    source .env
    .venv/bin/python scripts/hyperliquid/run_hl_grid_zro_mainnet.py
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

    print("=" * 60)
    print(f"🚀 MAINNET — HFT Grid MM v001 GENESIS — {PAIR}")
    print(f"   Time:        {datetime.now(timezone.utc).isoformat()}")
    print(f"   Instrument:  {INSTRUMENT}")
    print(f"   Network:     *** MAINNET — REAL MONEY ***")
    print(f"   Wallet:      {wallet_address[:10]}...{wallet_address[-6:]}")
    print()
    print("   EDGE:")
    print("   ZRO spread ~8bps | maker fee 1.5bps | net ~5bps/RT")
    print()
    print("   RISK LIMITS:")
    print("   Max position: $500 | Max loss: -$25 | Age: 2 min → maker close")
    print("   Take profit: $1 unrealized → close mode | Taker only for kill switch")
    print("=" * 60)

    node_config = TradingNodeConfig(
        trader_id=TraderId("HL-ZRO-001"),
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

    strategy = HLGridMM(config=HLGridMMConfig(
        strategy_id=f"HL-MM-{PAIR}-001",
        instrument_id=INSTRUMENT,
        hl_api_url="https://api.hyperliquid.xyz",  # MAINNET!

        # --- Grid structure ---
        grid_levels=1,                # 1 buy + 1 sell = TIGHT CONTROL — no cascading fills
        half_spread_bps=15.0,         # 15bps from mid → 30bps gross → 27bps net
        grid_interval_bps=5.0,        # Only matters when levels > 1
        order_qty_usd=50.0,           # $50 per level — smaller size = less risk per fill

        # --- Position limits ---
        max_position_usd=150.0,       # $150 max exposure — conservative while fixing bleeding
        max_position_qty=0.0,         # Auto-computed from USD limit

        # --- Inventory management (quadratic skew) ---
        skew_bps_per_unit=5.0,        # Gentle skew — let macro trend handle direction
        skew_enabled=True,

        # --- Fill burst cooldown ---
        burst_count=3,                # 3 fills on one side in 5s → cooldown
        burst_window_secs=5.0,        # Wider window
        burst_cooldown_secs=10.0,     # 10s pause (less aggressive)

        # --- Asymmetric grid ---
        min_grid_levels_adding=1,     # At max inv, only 1 level on adding side

        # --- Trend-aware grid (ALWAYS both sides, skew via reservation + levels) ---
        trend_bias_enabled=True,
        trend_ema_fast=50,                # ~10s of book updates
        trend_ema_slow=200,               # ~40s of book updates
        trend_threshold_bps=2.0,          # Enter trend when EMA spread > 2bps
        trend_exit_threshold_bps=0.5,     # Hysteresis: sticky trends
        trend_bias_max_bps=5.0,           # Max reservation shift (lower to reduce flapping)
        trend_min_levels_per_side=1,      # ALWAYS at least 1 level each side!
        trend_max_skew_levels=1,          # +1 extra level on trend side

        # --- Bollinger Band regime ---
        bb_period=100,                    # 20s of prices for BB
        bb_std_multiplier=2.0,            # Standard 2σ bands
        bb_squeeze_pct=0.3,               # Width < 30% avg = squeeze (pullback)
        bb_trend_confirm_pct=0.7,         # Width > 70% avg = confirmed trend

        # --- Macro trend filter (5-minute direction) ---
        # Blocks new inventory against the macro direction.
        # This prevents shorting during rallies / longing during dumps.
        macro_trend_enabled=True,
        macro_sample_interval_secs=15.0,  # Sample mid every 15s (faster init)
        macro_ema_periods=20,             # 20 samples × 15s = 5 minute window
        macro_threshold_bps=3.0,          # 3bps spread to confirm macro direction (more sensitive)
        macro_block_adding=True,          # Block inventory-adding side against macro

        # --- Trailing take-profit ---
        trailing_tp_enabled=True,
        trailing_tp_activation_usd=0.30,  # Trail after $0.30 unrealized
        trailing_tp_lock_pct=50.0,        # Lock in 50% of peak profit

        # --- Position sync guard ---
        sync_guard_after_fill_secs=10.0,

        # --- Profit protection (with time decay) ---
        min_profit_bps=2.0,
        profit_floor_decay_start_secs=30.0,
        profit_floor_decay_end_secs=120.0,
        max_loss_accept_bps=10.0,

        # --- Maker close mode ---
        maker_close_enabled=True,
        take_profit_unrealized_usd=0.50,  # Close mode at $0.50 unrealized
        take_profit_pct=0.0,
        close_mode_tighten_bps=2.0,
        close_mode_interval_bps=1.0,
        close_mode_max_wait_secs=300.0,
        close_mode_no_taker_fallback=True,

        # --- Refresh ---
        refresh_interval_secs=3.0,    # 3 second grid refresh
        min_refresh_interval_secs=2.0,
        stale_order_secs=15.0,

        # --- Risk controls ---
        max_loss_usd=-50.0,           # Kill switch only for true emergency
        max_inventory_age_secs=600.0, # 10 MINUTES before age-based close mode
                                      # (was 60s — that was triggering close mode constantly,
                                      # blocking the adding side and killing volume.
                                      # The Stoikov skew + trend bias handle inventory;
                                      # close mode should only fire as a last resort.)
        pause_on_spread_collapse_bps=1.0,
        flatten_on_stop=False,        # Do NOT flatten on restart — avoid taker dump!

        # --- Fee tier (VIP0) ---
        maker_fee_bps=1.5,
        taker_fee_bps=4.5,
    ))

    node = TradingNode(config=node_config)
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
