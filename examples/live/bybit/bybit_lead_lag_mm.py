#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

from decimal import Decimal
import os

from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
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
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.portfolio.config import PortfolioConfig
from strategy.lead_lag_bybit_binance_mm_v001 import LeadLagMM
from strategy.lead_lag_bybit_binance_mm_v001 import LeadLagMMConfig


# *** THIS STRATEGY USES BINANCE AS A DATA LEADER AND BYBIT AS EXECUTION FOLLOWER. ***
# *** IT IS INTENDED FOR EXPERIMENTAL USE ONLY. DO NOT TRADE LIVE WITHOUT FULL REVIEW. ***

# Symbols and instruments
symbol = "BTCUSDT"
leader_instrument_id = InstrumentId.from_str(f"{symbol}.BINANCE_SPOT")
follower_instrument_id = InstrumentId.from_str(f"{symbol}-SPOT.BYBIT")

# Configure the trading node
config_node = TradingNodeConfig(
    trader_id=TraderId("LEADLAG-001"),
    logging=LoggingConfig(
        log_level="INFO",
        use_pyo3=True,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_instrument_ids=[follower_instrument_id],
        open_check_interval_secs=5.0,
        position_check_interval_secs=5.0,
        graceful_shutdown_on_exception=True,
    ),
    risk_engine=LiveRiskEngineConfig(bypass=True),
    portfolio=PortfolioConfig(min_account_state_logging_interval_ms=1_000),
    data_clients={
        "BINANCE_SPOT": BinanceDataClientConfig(
            venue=Venue("BINANCE_SPOT"),
            api_key=None,  # 'BINANCE_API_KEY' env var
            api_secret=None,  # 'BINANCE_API_SECRET' env var
            account_type=BinanceAccountType.SPOT,
            base_url_http=None,
            base_url_ws=None,
            us=False,
            testnet=False,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([leader_instrument_id]),
            ),
        ),
        BYBIT: BybitDataClientConfig(
            api_key=None,  # 'BYBIT_API_KEY' env var
            api_secret=None,  # 'BYBIT_API_SECRET' env var
            base_url_http=None,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([follower_instrument_id]),
            ),
            product_types=(BybitProductType.SPOT,),
            demo=False,
            testnet=False,
        ),
    },
    exec_clients={
        BYBIT: BybitExecClientConfig(
            api_key=None,  # 'BYBIT_API_KEY' env var
            api_secret=None,  # 'BYBIT_API_SECRET' env var
            base_url_http=None,
            base_url_ws_private=None,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([follower_instrument_id]),
            ),
            product_types=(BybitProductType.SPOT,),
            use_spot_position_reports=True,
            demo=False,
            testnet=False,
        ),
    },
    timeout_connection=20.0,
    timeout_reconciliation=10.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)

# Instantiate the node
node = TradingNode(config=config_node)

# Configure strategy
config_strategy = LeadLagMMConfig(
    leader_instrument_id=leader_instrument_id,
    follower_instrument_id=follower_instrument_id,
    order_qty=Decimal("0.0001"),
    spread_bps=Decimal("3.0"),
    quote_refresh_interval_ms=50,
    min_quote_lifetime_ms=50,
    min_requote_ticks=1,
    guard_threshold_bps=Decimal("8.0"),
    max_data_staleness_ms=5000,
    book_type=BookType.L2_MBP,
    book_depth=5,
    post_only=True,
    client_id=ClientId(BYBIT),
    log_guard_events=True,
    log_leader_updates=False,
)

strategy = LeadLagMM(config=config_strategy)

# Add strategy
node.trader.add_strategy(strategy)

# Register client factories
node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
node.build()


def _preflight_check() -> None:
    missing = []
    if os.getenv("BINANCE_API_KEY") is None or os.getenv("BINANCE_API_SECRET") is None:
        missing.append("BINANCE_API_KEY/BINANCE_API_SECRET")
    if os.getenv("BYBIT_API_KEY") is None or os.getenv("BYBIT_API_SECRET") is None:
        missing.append("BYBIT_API_KEY/BYBIT_API_SECRET")
    if missing:
        raise RuntimeError(f"Missing API credentials: {', '.join(missing)}")


# Stop and dispose of the node with SIGINT/CTRL+C
if __name__ == "__main__":
    try:
        _preflight_check()
        node.run()
    finally:
        node.dispose()
