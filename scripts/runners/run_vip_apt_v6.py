"""Run APT with v006 TURBO strategy - Maximum Performance Edition."""

from __future__ import annotations

import os
from pathlib import Path

from nautilus_trader.adapters.binance import BinanceAccountType, BinanceDataClientConfig, BinanceLiveDataClientFactory
from nautilus_trader.adapters.bybit import BYBIT, BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory, BybitLiveExecClientFactory, BybitProductType
from nautilus_trader.config import InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.portfolio.config import PortfolioConfig

from lead_lag_bybit_binance_mm_v006_turbo import LeadLagMMv6Turbo, LeadLagMMv6TurboConfig

# =============================================================================
# CONFIGURATION - APT uses MAINACC_10
# =============================================================================
SYMBOL = "APTUSDT"
STRATEGY_ID = "VIP-APT-V6"
API_KEY_NUM = "10"

BYBIT_API_KEY = os.environ[f"BYBIT_API_KEY_MAINACC_{API_KEY_NUM}"]
BYBIT_API_SECRET = os.environ[f"BYBIT_API_SECRET_MAINACC_{API_KEY_NUM}"]
LOG_DIR = Path("./logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

FOLLOWER_ID = InstrumentId.from_str(f"{SYMBOL}-SPOT.BYBIT")
LEADER_ID = InstrumentId.from_str(f"{SYMBOL}.BINANCE_SPOT")


def main():
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
        timeout_reconciliation=20.0,
    )

    node = TradingNode(config=node_config)

    # APT @ ~$1.14 - order_qty=30 APT = ~$34 per trade
    strategy_config = LeadLagMMv6TurboConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        order_qty=30.0,
        max_position_qty=150.0,
        min_order_qty=5.0,
        spread_bps=60.0,
        ofi_enabled=True,
        ofi_widen_threshold=0.3,
        ofi_widen_bps=10.0,
        ofi_tighten_bps=5.0,
        volatility_enabled=True,
        volatility_base_threshold_bps=5.0,
        volatility_max_widen_bps=20.0,
        regime_detection_enabled=True,
        regime_trending_spread_mult=2.0,
        regime_trending_size_mult=0.5,
        realized_spread_guardian_enabled=True,
        log_quotes=False,
        log_regime_changes=True,
        log_guards=True,
        log_fills=True,
    )

    strategy = LeadLagMMv6Turbo(config=strategy_config)
    node.trader.add_strategy(strategy)

    # Register client factories
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)

    node.build()
    node.run()


if __name__ == "__main__":
    main()
