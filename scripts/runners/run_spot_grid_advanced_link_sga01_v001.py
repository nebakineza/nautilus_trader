"""Run LINK Spot Grid Advanced v001 on SGA01 subaccount."""

from __future__ import annotations

import os
from pathlib import Path

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

from spot_grid_advanced_v001 import SpotGridAdvanced, SpotGridAdvancedConfig

SYMBOL = "XRPUSDT"
STRATEGY_ID = "SPOTGRIDADV-XRP-SGA01-V001"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_SGA01_00"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_SGA01_00"]
LOG_DIR = Path("./logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

INSTRUMENT_ID = InstrumentId.from_str(f"{SYMBOL}-SPOT.BYBIT")


def main() -> None:
    bybit_data = BybitDataClientConfig(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        product_types=(BybitProductType.SPOT,),
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=frozenset([INSTRUMENT_ID])),
    )
    bybit_exec = BybitExecClientConfig(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        product_types=(BybitProductType.SPOT,),
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=frozenset([INSTRUMENT_ID])),
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

    # Default config sized for $500 USDT account
    strategy_config = SpotGridAdvancedConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT_ID,
        trading_limit=25.0,
        tl_multiplier=1.0,
        max_buy_count=15,
        min_volume_to_sell=10.0,
        max_investment=500.0,
        funds_reserve=0.0,
        keep_quote=0.0,
        quote_budget_pct=0.50,
        period="15m",
        gain_pct=0.5,
        auto_gain=True,
        fee_bps=20.0,
        min_step_pct=0.3,
        unit_cost=True,
        bar_price_type="LAST",
        bar_source="EXTERNAL",
        ct_enabled=True,
        start_cont_trading=3,
        ct_tl_multiplier=0.5,
        ct_restart_multiplier=1.0,
        trend_open=False,
        trend_block_dca=False,
        trend_lower_dca=False,
        trend_grid_multiplier=2.0,
        trend_ct_multiplier=2.0,
        sma_period=50,
        trend_fast_period="15m",
        trend_slow_period="4h",
        buy_enabled=True,
        sell_enabled=True,
        stop_after_sell=False,
        ignore_trades_before_ms=0,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(SpotGridAdvanced(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} SpotGridAdvanced v001 (SGA01)")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
