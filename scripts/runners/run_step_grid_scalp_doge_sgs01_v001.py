"""Run DOGEUSDT StepGridScalp v001 on SGS01 subaccount.

Same account as ZIG StepGridScalp. Separate process, shared balance (50/50 split).
DOGE profile: High-volume meme coin, volatile, scalping-friendly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
from nautilus_trader.portfolio.config import PortfolioConfig

from step_grid_scalp_v001 import StepGridScalp, StepGridScalpConfig

SYMBOL = "DOGEUSDT"
STRATEGY_ID = "STEPGRIDSCALP-DOGE-SGS01-V001"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_SGS01_00"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_SGS01_00"]
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

    strategy_config = StepGridScalpConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT_ID,
        trading_limit=20.0,        # ~80 DOGE per buy at $0.25
        tl_multiplier=1.0,
        max_buy_count=40,
        min_volume_to_sell=10.0,
        max_investment=500.0,
        quote_budget_pct=0.50,     # Share account with ZIG
        gain_pct=1.0,
        gain_partial_pct=0.5,
        partial_sell_ratio=0.95,
        unit_cost=True,
        period="5m",
        period_medium="15m",
        period_long="1h",
        auto_step_size="ATR",
        min_step_pct=0.3,
        pct_trailing_range=False,
        pct_sell_trailing_range=False,
        trend_sync=True,
        strict_entry=False,
        strict_dca=False,
        exhaustion_sensitivity="MEDIUM",
        trade_supports=True,
        trend_scalping=True,
        multiple_timeframes_mode=False,
        accumulation_cycle=False,
        trend_plus=True,
        btfd_mode=False,
        custom_trading_range_mode=False,
        buy_enabled=True,
        sell_enabled=True,
        stop_after_sell=False,
        atr_period=50,
        keep_quote=0.0,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(StepGridScalp(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} StepGridScalp v001 (SGS01 account)")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
