#!/usr/bin/env python3
"""Live StatArb pairs trading on Bybit Spot (DOT-LINK only)."""

import os
from dotenv import load_dotenv

load_dotenv()

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitDataClientConfig
from nautilus_trader.adapters.bybit import BybitExecClientConfig
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitLiveExecClientFactory
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.portfolio.config import PortfolioConfig

from strategy.statarb.pairs_strategy import StatArbPairsStrategy
from strategy.statarb.configs import DOT_LINK_CONFIG

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError("BYBIT API credentials not set!")

config_node = TradingNodeConfig(
    trader_id=TraderId("STATARB-PAIRS-001"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory="/home/ubuntu/trading/logs",
        log_file_name="statarb_pairs_live.log",
        clear_log_file=False,
        use_pyo3=True,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=False,
        graceful_shutdown_on_exception=True,
    ),
    risk_engine=LiveRiskEngineConfig(bypass=False),
    portfolio=PortfolioConfig(min_account_state_logging_interval_ms=5_000),
    data_clients={
        BYBIT: BybitDataClientConfig(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset(
                    {
                        InstrumentId.from_str("DOTUSDT-SPOT.BYBIT"),
                        InstrumentId.from_str("LINKUSDT-SPOT.BYBIT"),
                    }
                ),
            ),
            product_types=(BybitProductType.SPOT,),
            demo=False,
            testnet=BYBIT_TESTNET,
            recv_window_ms=30_000,
            max_retries=5,
            retry_delay_initial_ms=500,
            retry_delay_max_ms=5_000,
        ),
    },
    exec_clients={
        BYBIT: BybitExecClientConfig(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset(
                    {
                        InstrumentId.from_str("DOTUSDT-SPOT.BYBIT"),
                        InstrumentId.from_str("LINKUSDT-SPOT.BYBIT"),
                    }
                ),
            ),
            product_types=(BybitProductType.SPOT,),
            use_spot_position_reports=True,
            demo=False,
            testnet=BYBIT_TESTNET,
            recv_window_ms=30_000,
            max_retries=5,
            retry_delay_initial_ms=500,
            retry_delay_max_ms=5_000,
        ),
    },
    timeout_connection=30.0,
    timeout_reconciliation=10.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)

node = TradingNode(config=config_node)

# Add StatArb strategy (DOT-LINK only)
node.trader.add_strategy(StatArbPairsStrategy(config=DOT_LINK_CONFIG))

node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
node.build()

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
