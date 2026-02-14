"""Run LINK with v009 SNIPER - Range Detection HFT.

LINK - tuned v009 config.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nautilus_trader.adapters.binance import (
    BinanceAccountType,
    BinanceDataClientConfig,
    BinanceLiveDataClientFactory,
)
from nautilus_trader.adapters.bybit import (
    BYBIT,
    BybitDataClientConfig,
    BybitExecClientConfig,
    BybitLiveDataClientFactory,
    BybitLiveExecClientFactory,
    BybitProductType,
)
from nautilus_trader.config import InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.portfolio.config import PortfolioConfig

from lead_lag_bybit_binance_mm_v009_sniper import LeadLagMMv9Sniper, LeadLagMMv9SniperConfig
from sniper_v009_configs import PAIR_CONFIGS

SYMBOL = "LINKUSDT"
STRATEGY_ID = "VIP-LINK-V9"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_MAINACC_02"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_MAINACC_02"]
LOG_DIR = Path("./logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

FOLLOWER_ID = InstrumentId.from_str(f"{SYMBOL}-SPOT.BYBIT")
LEADER_ID = InstrumentId.from_str(f"{SYMBOL}.BINANCE_SPOT")


def main() -> None:
    pair_cfg = PAIR_CONFIGS[SYMBOL]

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

    strategy_config = LeadLagMMv9SniperConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        ranging_spread_bps=pair_cfg.ranging_spread_bps,
        breakout_spread_bps=pair_cfg.breakout_spread_bps,
        min_spread_bps=max(4.0, pair_cfg.ranging_spread_bps - 2.0),
        order_qty=pair_cfg.order_qty,
        max_position_qty=pair_cfg.max_position_qty,
        max_hold_secs=pair_cfg.max_hold_secs,
        exit_cooldown_secs=pair_cfg.exit_cooldown_secs,
        range_confirm_ticks=pair_cfg.range_confirm_ticks,
        breakout_confirm_ticks=pair_cfg.breakout_confirm_ticks,
        log_state_changes=True,
        log_indicator_values=False,
        log_trade_signals=True,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv9Sniper(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} v009 SNIPER")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
