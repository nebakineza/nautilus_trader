"""Run ARB with v008 TIMEKEEPER - Inventory Age Management.

ARB - MAINACC_06 - HAD BIG LOSSES FROM STALE INVENTORY
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

SYMBOL = "ARBUSDT"
STRATEGY_ID = "VIP-ARB-V8"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_MAINACC_06"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_MAINACC_06"]
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

    # ARB: HAD -298 bps loss from 2h+ hold - AGGRESSIVE time management
    strategy_config = LeadLagMMv8TimekeeperConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        order_qty=80.0,
        max_position_qty=800.0,
        min_order_qty=20.0,
        spread_bps=90.0,  # Wider
        inventory_max_hold_secs=900,  # 15 MIN MAX - was 2h+ hold that killed us
        inventory_exit_skew_enabled=True,
        inventory_exit_skew_start_secs=120,  # Start tightening at 2 min!
        inventory_exit_skew_max_tighten_bps=60.0,  # Aggressive tighten
        inventory_underwater_exit_enabled=True,
        inventory_underwater_threshold_bps=-30.0,  # Very tight - exit at -30 bps
        inventory_underwater_check_after_secs=180,  # Check after 3 min
        entry_quality_filter_enabled=True,
        entry_quality_max_trend_strength=0.35,  # Very strict entry
        no_add_to_loser_enabled=True,
        no_add_to_loser_threshold_bps=-10.0,
        regime_window_ticks=150,
        regime_trending_threshold=0.65,
        regime_trending_spread_mult=3.0,  # Even wider in trends
        regime_trending_size_mult=0.25,  # Smaller in trends
        trend_filter_enabled=True,
        trend_filter_threshold=0.70,  # Pause earlier
        inventory_skew_enabled=True,
        realized_spread_guardian_enabled=True,
        realized_spread_window_fills=3,  # Faster reaction
        realized_spread_min_bps=15.0,  # Higher min
        realized_spread_widen_step_bps=25.0,  # Bigger steps
        realized_spread_max_widen_bps=200.0,  # Allow more
        fifo_pnl_enabled=True,
        fifo_pnl_loss_threshold_bps=-15.0,  # Tighter
        dynamic_spread_enabled=True,
        dynamic_spread_min_bps=60.0,
        dynamic_spread_max_bps=250.0,
        hit_rate_tracking_enabled=True,
        log_fills=True,
        log_protection_events=True,
        log_inventory_age=True,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv8Timekeeper(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} v008 TIMEKEEPER (AGGRESSIVE TIME MGMT)")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
