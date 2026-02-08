"""Run SUI with v008 TIMEKEEPER - Inventory Age Management.

SUI - MAINACC_01 - Primary test pair for v008
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nautilus_trader.adapters.binance import BinanceAccountType, BinanceDataClientConfig, BinanceLiveDataClientFactory
from nautilus_trader.adapters.bybit import BYBIT, BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory, BybitLiveExecClientFactory, BybitProductType
from nautilus_trader.config import InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.portfolio.config import PortfolioConfig

from lead_lag_bybit_binance_mm_v008_timekeeper import LeadLagMMv8Timekeeper, LeadLagMMv8TimekeeperConfig

SYMBOL = "SUIUSDT"
STRATEGY_ID = "VIP-SUI-V8"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_MAINACC_01"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_MAINACC_01"]
LOG_DIR = Path("./logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

FOLLOWER_ID = InstrumentId.from_str(f"{SYMBOL}-SPOT.BYBIT")
LEADER_ID = InstrumentId.from_str(f"{SYMBOL}.BINANCE_SPOT")


def main():
    binance_data = BinanceDataClientConfig(
        venue=Venue("BINANCE_SPOT"),
        account_type=BinanceAccountType.SPOT,
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=frozenset([LEADER_ID])),
    )
    bybit_data = BybitDataClientConfig(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        product_types=(BybitProductType.SPOT,),
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=frozenset([FOLLOWER_ID])),
    )
    bybit_exec = BybitExecClientConfig(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        product_types=(BybitProductType.SPOT,),
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=frozenset([FOLLOWER_ID])),
        max_retries=5,
        retry_delay_initial_ms=500,
        retry_delay_max_ms=5000,
    )

    node_config = TradingNodeConfig(
        trader_id=TraderId(STRATEGY_ID),
        logging=LoggingConfig(log_level="INFO", log_directory=str(LOG_DIR), log_colors=False),
        portfolio=PortfolioConfig(min_account_state_logging_interval_ms=1000),
        data_clients={"BINANCE_SPOT": binance_data, "BYBIT": bybit_data},
        exec_clients={"BYBIT": bybit_exec},
        exec_engine=LiveExecEngineConfig(reconciliation=True, reconciliation_lookback_mins=60),
        risk_engine=LiveRiskEngineConfig(bypass=False),
        timeout_connection=30.0,
        timeout_reconciliation=30.0,
    )

    # SUI: TIMEKEEPER profile with inventory age management
    strategy_config = LeadLagMMv8TimekeeperConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        # === SIZING ===
        order_qty=80.0,  # ~$80 per trade
        max_position_qty=800.0,
        min_order_qty=20.0,
        # === SPREAD ===
        spread_bps=80.0,
        
        # ==========================================================================
        # v008 CORE: INVENTORY TIME MANAGEMENT
        # ==========================================================================
        # Max hold: 30 minutes - after this, force exit regardless of P&L
        inventory_max_hold_secs=1800,
        
        # Exit skew: start tightening exit side after 5 min, max 40 bps tighter
        inventory_exit_skew_enabled=True,
        inventory_exit_skew_start_secs=300,
        inventory_exit_skew_max_tighten_bps=40.0,
        
        # Underwater exit: if inventory is -50 bps after 10 min, force exit
        inventory_underwater_exit_enabled=True,
        inventory_underwater_threshold_bps=-50.0,
        inventory_underwater_check_after_secs=600,
        
        # Entry quality: only enter when conditions are favorable
        entry_quality_filter_enabled=True,
        entry_quality_min_ofi=0.0,
        entry_quality_max_trend_strength=0.50,
        no_add_to_loser_enabled=True,
        no_add_to_loser_threshold_bps=-10.0,
        
        # ==========================================================================
        # STANDARD PROTECTIONS (from v007)
        # ==========================================================================
        # Regime detection
        regime_window_ticks=150,
        regime_trending_threshold=0.65,
        regime_trending_spread_mult=2.5,
        regime_trending_size_mult=0.3,
        # Trend filter
        trend_filter_enabled=True,
        trend_filter_threshold=0.75,
        # Inventory skew (position-based)
        inventory_skew_enabled=True,
        inventory_skew_threshold_pct=0.30,
        # Guardian
        realized_spread_guardian_enabled=True,
        realized_spread_window_fills=5,
        realized_spread_min_bps=10.0,
        realized_spread_widen_step_bps=20.0,
        realized_spread_max_widen_bps=150.0,
        # FIFO P&L
        fifo_pnl_enabled=True,
        fifo_pnl_loss_threshold_bps=-20.0,
        # Dynamic spread
        dynamic_spread_enabled=True,
        dynamic_spread_min_bps=50.0,
        dynamic_spread_max_bps=200.0,
        # Hit rate
        hit_rate_tracking_enabled=True,
        # === LOGGING ===
        log_fills=True,
        log_protection_events=True,
        log_inventory_age=True,
        metrics_enabled=True,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv8Timekeeper(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} v008 TIMEKEEPER (max_hold=30m, exit_skew, underwater_exit)")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
