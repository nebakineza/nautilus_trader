#!/usr/bin/env python3
"""Runner for OFI Microstructure MM v001 (single-venue, maker-only)."""

import os
from decimal import Decimal
from dotenv import load_dotenv

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
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId, TraderId

from strategy.ofi_microstructure_mm_v001 import OFIMicrostructureMM, OFIMicrostructureMMConfig

load_dotenv()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError("BYBIT API credentials not set!")

instrument_id = InstrumentId.from_str("BTCUSDT-SPOT.BYBIT")

config_node = TradingNodeConfig(
    trader_id=TraderId("OFI-MM-001"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory="/home/ubuntu/trading/logs",
        log_file_name="ofi_mm_v001.log",
        clear_log_file=False,
        use_pyo3=True,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_instrument_ids=[instrument_id],
        open_check_interval_secs=2.0,
        position_check_interval_secs=2.0,
        graceful_shutdown_on_exception=True,
    ),
    risk_engine=LiveRiskEngineConfig(bypass=False),
    data_clients={
        BYBIT: BybitDataClientConfig(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            base_url_http=None,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([instrument_id]),
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
            base_url_http=None,
            base_url_ws_private=None,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([instrument_id]),
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
    timeout_connection=20.0,
    timeout_reconciliation=20.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)

node = TradingNode(config=config_node)

strategy_config = OFIMicrostructureMMConfig(
    instrument_id=instrument_id,
    order_qty=Decimal("0.001"),
    max_position_qty=Decimal("0.01"),
    spread_bps=Decimal("18.0"),
    min_profit_bps=Decimal("1.0"),
    quote_refresh_interval_ms=2000,
    min_quote_lifetime_ms=1000,
    ofi_depth=10,
    ofi_max_bps=Decimal("5.0"),
    ofi_pause_ms=2000,
    inventory_skew_bps=Decimal("3.0"),
    book_type=BookType.L2_MBP,
    book_depth=50,
    post_only=True,
    maker_fee_bps=Decimal("7.5"),
)

node.trader.add_strategy(OFIMicrostructureMM(config=strategy_config))

node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
node.build()

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
