#!/usr/bin/env python3
"""Runner for Stablecoin MM v001 (USDC/USDT)."""

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

from strategy.stablecoin_mm_v001 import StablecoinMM, StablecoinMMConfig

load_dotenv()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError("BYBIT API credentials not set!")

# Target Pair: USDC/USDT on Bybit Spot
instrument_id = InstrumentId.from_str("USDCUSDT-SPOT.BYBIT")

config_node = TradingNodeConfig(
    trader_id=TraderId("STABLE-MM-VIP1"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory="/home/ubuntu/trading/logs",
        log_file_name="stable_mm_v1.log",
        clear_log_file=False,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_instrument_ids=[instrument_id],
        open_check_interval_secs=5.0,
    ),
    risk_engine=LiveRiskEngineConfig(bypass=True), # Managing risk in strategy
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
        ),
    },
    exec_clients={
        BYBIT: BybitExecClientConfig(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([instrument_id]),
            ),
            product_types=(BybitProductType.SPOT,),
            use_spot_position_reports=True,
            demo=False,
            testnet=BYBIT_TESTNET,
        ),
    },
)

node = TradingNode(config=config_node)

strategy_config = StablecoinMMConfig(
    instrument_id=instrument_id,
    order_qty=Decimal("11.0"),       # e.g., 11 USDC per clip (above min order size)
    max_position_qty=Decimal("1000.0"), # Max inventory
    center_price=Decimal("1.0000"),
    spread_ticks=1,                  # 0.9999 / 1.0001 if tick is 0.0001
    min_balance_ratio=Decimal("0.95"),
    max_divergence_bps=Decimal("20.0"),  # 0.2% depeg guard
    quote_refresh_interval_ms=5000,
)

node.trader.add_strategy(StablecoinMM(config=strategy_config))

node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
node.build()

if __name__ == "__main__":
    print(f"Starting Stablecoin Market Maker on {instrument_id}...")
    try:
        node.run()
    finally:
        node.dispose()
