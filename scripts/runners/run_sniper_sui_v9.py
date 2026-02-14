#!/usr/bin/env python3
"""
V009 Sniper Runner - SUI Test

Tests the HFT Sniper strategy on SUIUSDT with tight spreads.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add strategy package to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

from nautilus_trader.adapters.binance import BinanceAccountType, BinanceDataClientConfig, BinanceLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory, BybitLiveExecClientFactory, BybitProductType
from nautilus_trader.config import InstrumentProviderConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId

from strategy.lead_lag_bybit_binance_mm_v009_sniper import (
    LeadLagMMv9Sniper,
    LeadLagMMv9SniperConfig,
)

# Load environment
load_dotenv()

# Symbol configuration
SYMBOL = "SUIUSDT"
INSTANCE_ID = f"SNIPER-{SYMBOL[:3]}-V9"


def main():
    print(f">>> STARTING {SYMBOL} v009 SNIPER (Range Detection HFT)")
    
    # Get API keys
    api_key = os.environ.get("BYBIT_API_KEY_MAINACC_00", "")
    api_secret = os.environ.get("BYBIT_API_SECRET_MAINACC_00", "")
    
    if not api_key or not api_secret:
        raise ValueError("BYBIT_API_KEY_MAINACC_00 and BYBIT_API_SECRET_MAINACC_00 required")
    
    # Configure node
    config = TradingNodeConfig(
        trader_id=TraderId(INSTANCE_ID),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory="logs",
            log_colors=True,
        ),
        data_clients={
            "BINANCE_SPOT": BinanceDataClientConfig(
                api_key="",  # Public data only
                api_secret="",
                account_type=BinanceAccountType.SPOT,
                instrument_provider=InstrumentProviderConfig(load_all=False),
            ),
            "BYBIT": BybitDataClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                product_types=[BybitProductType.SPOT],
                instrument_provider=InstrumentProviderConfig(load_all=False),
            ),
        },
        exec_clients={
            "BYBIT": BybitExecClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                product_types=[BybitProductType.SPOT],
                instrument_provider=InstrumentProviderConfig(load_all=False),
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=5.0,
    )
    
    # Build node
    node = TradingNode(config=config)
    
    # Add data client factories
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.add_data_client_factory("BYBIT", BybitLiveDataClientFactory)
    node.add_exec_client_factory("BYBIT", BybitLiveExecClientFactory)
    
    # Build and start
    node.build()
    
    # Wait for instruments
    import time
    time.sleep(3)
    
    # Strategy configuration - SNIPER SETTINGS
    strategy_config = LeadLagMMv9SniperConfig(
        strategy_id=f"SNIPER-{SYMBOL[:3]}",
        
        # Instruments
        follower_instrument_id=InstrumentId.from_str(f"{SYMBOL}-SPOT.BYBIT"),
        leader_instrument_id=InstrumentId.from_str(f"{SYMBOL}.BINANCE_SPOT"),
        
        # === TIGHT SPREADS FOR HFT ===
        ranging_spread_bps=25.0,      # 25 bps during range (5 bps profit after fees)
        breakout_spread_bps=50.0,     # 50 bps during breakout pending
        min_spread_bps=22.0,          # Must cover 20 bps roundtrip fees
        
        # === SMALL POSITION FOR TESTING ===
        order_qty=5.0,                # Small qty for testing
        max_position_qty=25.0,        # Max 25 units
        min_order_qty=0.1,
        
        # === INDICATOR SETTINGS ===
        bb_period=20,
        bb_std_dev=2.0,
        bb_squeeze_threshold=0.75,    # Width < 75% of EMA = squeeze
        
        rsi_period=14,
        rsi_neutral_low=40.0,
        rsi_neutral_high=60.0,
        rsi_extreme_low=30.0,
        rsi_extreme_high=70.0,
        
        atr_period=14,
        atr_expansion_threshold=1.25, # ATR > 125% of EMA = expansion
        
        # === VOLUME PROFILE SETTINGS ===
        volume_profile_enabled=True,
        volume_profile_bins=100,       # 100 price bins
        volume_profile_window=500,     # 500 tick rolling window
        volume_profile_hvn_threshold=1.5,  # HVN if vol > 150% avg
        volume_profile_lvn_threshold=0.5,  # LVN if vol < 50% avg
        volume_profile_poc_tolerance_bps=15.0,  # POC proximity
        
        # === RANGE DETECTION ===
        range_confirm_ticks=8,        # 8 ticks to confirm range
        range_min_width_bps=25.0,     # Range must be at least 25 bps
        range_max_width_bps=150.0,    # Range can't be too wide
        
        # === BREAKOUT DETECTION ===
        breakout_confirm_ticks=3,
        breakout_ofi_threshold=0.35,
        
        # === EXIT SETTINGS ===
        exit_cooldown_secs=30,
        exit_aggressive_threshold_bps=-15.0,  # Aggressive exit if losing > 15 bps
        hold_if_appreciating=True,
        max_hold_secs=180,            # Force exit after 3 min
        
        # === LOGGING ===
        log_state_changes=True,
        log_indicator_values=True,    # Log indicators for debugging
        log_trade_signals=True,
    )
    
    # Add strategy
    node.trader.add_strategy(LeadLagMMv9Sniper(config=strategy_config))
    
    # Run
    try:
        node.run()
    except KeyboardInterrupt:
        print("\n>>> Shutting down...")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
