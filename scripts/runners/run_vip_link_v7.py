"""Run LINK with v007 FORTRESS - Tuned Profile.

LINK was showing only +15 bps avg - needs wider spreads.
Moving from AGGRESSIVE to SAFE profile with wider base.
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

from lead_lag_bybit_binance_mm_v007_fortress import LeadLagMMv7Fortress, LeadLagMMv7FortressConfig

SYMBOL = "LINKUSDT"
STRATEGY_ID = "VIP-LINK-V7"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_MAINACC_02"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_MAINACC_02"]
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

    # LINK: Was +15 bps avg - needs WIDER spreads (moved from AGGRESSIVE to SAFE+)
    strategy_config = LeadLagMMv7FortressConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        # === SIZING ===
        order_qty=1.5,
        max_position_qty=15.0,
        min_order_qty=0.5,
        # === SPREAD (WIDENED from 60 to 90) ===
        spread_bps=90.0,
        # === REGIME ===
        regime_window_ticks=150,
        regime_trending_threshold=0.65,
        regime_trending_spread_mult=2.5,
        regime_trending_size_mult=0.3,
        # === TREND FILTER ===
        trend_filter_enabled=True,
        trend_filter_threshold=0.75,
        # === INVENTORY SKEW ===
        inventory_skew_enabled=True,
        inventory_skew_threshold_pct=0.30,
        inventory_skew_max_widen_bps=50.0,
        # === GUARDIAN ===
        realized_spread_guardian_enabled=True,
        realized_spread_window_fills=5,
        realized_spread_min_bps=15.0,  # Higher requirement
        realized_spread_widen_step_bps=20.0,
        # === FIFO P&L ===
        fifo_pnl_enabled=True,
        fifo_pnl_loss_threshold_bps=-15.0,
        # === DYNAMIC SPREAD ===
        dynamic_spread_enabled=True,
        dynamic_spread_min_bps=50.0,
        dynamic_spread_max_bps=200.0,
        # === LOGGING ===
        log_fills=True,
        log_protection_events=True,
        metrics_enabled=True,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv7Fortress(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} v007 FORTRESS (TUNED: 90bps base)")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
