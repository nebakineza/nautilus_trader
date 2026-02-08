"""Run LINK with v006 TURBO strategy - Maximum Performance Edition."""

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

from lead_lag_bybit_binance_mm_v006_turbo import LeadLagMMv6Turbo, LeadLagMMv6TurboConfig

# =============================================================================
# CONFIGURATION - LINK (MAINACC_02)
# =============================================================================
SYMBOL = "LINKUSDT"
STRATEGY_ID = "VIP-LINK-V6"
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

    # LINK: order_qty=2, max=20
    strategy_config = LeadLagMMv6TurboConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        order_qty=2.0,
        max_position_qty=20.0,
        min_order_qty=0.1,
        spread_bps=60.0,
        quote_refresh_interval_ms=30,
        min_quote_lifetime_ms=20,
        ofi_enabled=True,
        ofi_widen_bps=10.0,
        ofi_tighten_bps=5.0,
        volatility_enabled=True,
        volatility_max_widen_bps=30.0,
        regime_detection_enabled=True,
        regime_window_ticks=100,
        regime_trending_spread_mult=2.0,
        regime_trending_size_mult=0.5,
        guard_threshold_bps=10.0,
        guard_hysteresis_bps=5.0,
        hard_position_cap_enabled=True,
        position_cap_buffer_pct=0.95,
        runaway_detection_enabled=True,
        runaway_pause_secs=30,
        maker_fee_bps=10.0,
        min_profit_bps=5.0,
        log_quotes=False,
        log_regime_changes=True,
        log_guards=True,
        log_fills=True,
        metrics_enabled=True,
        metrics_interval_secs=5,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv6Turbo(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} v006 TURBO")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
