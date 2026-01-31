#!/usr/bin/env python3
"""Capture Bybit Spot order book deltas + trade ticks for DOT/LINK/OP."""

import os
from dotenv import load_dotenv

load_dotenv()

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitDataClientConfig
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.venues import Venue

from strategy.capture.bybit_ob_tick_capture import BybitCaptureConfig, BybitObTickCapture

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError("BYBIT API credentials not set!")

instrument_ids = [
    InstrumentId.from_str("DOTUSDT-SPOT.BYBIT"),
    InstrumentId.from_str("LINKUSDT-SPOT.BYBIT"),
    InstrumentId.from_str("OPUSDT-SPOT.BYBIT"),
]

config_node = TradingNodeConfig(
    trader_id=TraderId("CAPTURE-DOTLINKOP-001"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory=".",
        log_file_name="capture_dot_link_op.log",
        clear_log_file=False,
        use_pyo3=True,
    ),
    data_clients={
        BYBIT: BybitDataClientConfig(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset(instrument_ids),
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
    timeout_connection=30.0,
    timeout_disconnection=10.0,
)

node = TradingNode(config=config_node)

strategy = BybitObTickCapture(
    config=BybitCaptureConfig(
        instrument_ids=instrument_ids,
        book_depth=50,
        ob_data_dir="/home/ubuntu/trading/data/ob_data_live",
        tick_data_dir="/home/ubuntu/trading/data/tick_data",
    )
)

node.trader.add_strategy(strategy)
node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.build()

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
