#!/usr/bin/env python3
"""Live multi-symbol Lead-Lag MM (Binance leader, Bybit follower)."""

import os
import sys
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

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

sys.path.insert(0, "/home/ubuntu/trading")
from lead_lag_bybit_binance_mm_v001 import LeadLagMM
from lead_lag_bybit_binance_mm_v001 import LeadLagMMConfig

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError("BYBIT API credentials not set!")

PORTFOLIO_ID = "LEADLAG-MULTI"
MAX_TOTAL_EXPOSURE_USD = Decimal("500")

pairs = [
    {
        "symbol": "BTCUSDT",
        "order_qty": Decimal("0.0001"),
        "max_position_qty": Decimal("0.001"),
        "guard_threshold_bps": Decimal("8.0"),
    },
    {
        "symbol": "ETHUSDT",
        "order_qty": Decimal("0.002"),
        "max_position_qty": Decimal("0.02"),
        "guard_threshold_bps": Decimal("12.0"),
        "spread_bps": Decimal("16.0"),
    },
    {
        "symbol": "SOLUSDT",
        "order_qty": Decimal("0.1"),
        "max_position_qty": Decimal("1.0"),
        "guard_threshold_bps": Decimal("12.0"),
        "spread_bps": Decimal("16.0"),
    },
]

leader_ids = [InstrumentId.from_str(f"{p['symbol']}.BINANCE_SPOT") for p in pairs]
follower_ids = [InstrumentId.from_str(f"{p['symbol']}-SPOT.BYBIT") for p in pairs]

config_node = TradingNodeConfig(
    trader_id=TraderId("LEADLAG-MULTI-001"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory=".",
        log_file_name="leadlag_multi_live.log",
        clear_log_file=False,
        use_pyo3=True,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_instrument_ids=follower_ids,
        open_check_interval_secs=5.0,
        position_check_interval_secs=5.0,
        graceful_shutdown_on_exception=True,
    ),
    risk_engine=LiveRiskEngineConfig(bypass=False),
    portfolio=PortfolioConfig(min_account_state_logging_interval_ms=1_000),
    data_clients={
        "BINANCE_SPOT": BinanceDataClientConfig(
            venue=Venue("BINANCE_SPOT"),
            api_key=None,
            api_secret=None,
            account_type=BinanceAccountType.SPOT,
            base_url_http=None,
            base_url_ws=None,
            us=False,
            testnet=False,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset(leader_ids),
            ),
        ),
        BYBIT: BybitDataClientConfig(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            base_url_http=None,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset(follower_ids),
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
            base_url_http=None,
            base_url_ws_private=None,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset(follower_ids),
            ),
            product_types=(BybitProductType.SPOT,),
            use_spot_position_reports=True,
            demo=False,
            testnet=BYBIT_TESTNET,
        ),
    },
    timeout_connection=20.0,
    timeout_reconciliation=10.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)

node = TradingNode(config=config_node)

for pair in pairs:
    leader_id = InstrumentId.from_str(f"{pair['symbol']}.BINANCE_SPOT")
    follower_id = InstrumentId.from_str(f"{pair['symbol']}-SPOT.BYBIT")

    spread_bps = pair.get("spread_bps", Decimal("12.0"))
    config_strategy = LeadLagMMConfig(
        leader_instrument_id=leader_id,
        follower_instrument_id=follower_id,
        order_qty=pair["order_qty"],
        max_position_qty=pair["max_position_qty"],
        max_total_exposure_usd=MAX_TOTAL_EXPOSURE_USD,
        spread_bps=spread_bps,
        quote_refresh_interval_ms=50,
        min_quote_lifetime_ms=50,
        min_requote_ticks=1,
        guard_threshold_bps=pair["guard_threshold_bps"],
        book_type=BookType.L2_MBP,
        book_depth=50,
        post_only=True,
        client_id=ClientId(BYBIT),
        log_guard_events=True,
        log_leader_updates=False,
        portfolio_id=PORTFOLIO_ID,
        record_orderbook=True,
        record_path="/home/ubuntu/trading/ob_data_live",
        record_depth=50,
        record_snapshot_interval_secs=0,
    )

    node.trader.add_strategy(LeadLagMM(config=config_strategy))

node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)

node.build()

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
