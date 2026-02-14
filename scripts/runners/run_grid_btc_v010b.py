"""Run BTCUSDT Fixed Grid Bot v010b (manual range).

Manual range grid bot to emulate Bybit native grid bot behavior.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nautilus_trader.adapters.bybit import BYBIT, BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory, BybitLiveExecClientFactory, BybitProductType
from nautilus_trader.config import InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.portfolio.config import PortfolioConfig

from fixed_grid_bot_v010b import FixedGridBot, FixedGridBotConfig

SYMBOL = "BTCUSDT"
STRATEGY_ID = "GRID-BTC-V010B"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_SG01_00"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_SG01_00"]
LOG_DIR = Path("./logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

FOLLOWER_ID = InstrumentId.from_str(f"{SYMBOL}-SPOT.BYBIT")


def main() -> None:
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
        data_clients={"BYBIT": bybit_data},
        exec_clients={"BYBIT": bybit_exec},
        exec_engine=LiveExecEngineConfig(reconciliation=True, reconciliation_lookback_mins=60),
        risk_engine=LiveRiskEngineConfig(bypass=False),
        timeout_connection=30.0,
        timeout_reconciliation=30.0,
    )

    strategy_config = FixedGridBotConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=f"{SYMBOL}-SPOT.BYBIT",
        total_grids=40,
        fixed_lower_price=49000.0,
        fixed_upper_price=190000.0,
        order_qty=0.0001,  # ~$10 at $100k
        max_position_qty=0.01,  # ~$1,000 notional at $100k
        min_order_value_usd=5.0,
        quote_budget_pct=0.50,  # Share account with ETH grid
        rebuild_on_out_of_range=False,
        max_orders_per_tick=1,
        min_submit_interval_ms=500,
        warmup_ticks=10,
        log_level=1,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(FixedGridBot(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()
    print(">>> STARTING BTCUSDT Fixed Grid Bot v010b (manual range)")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
