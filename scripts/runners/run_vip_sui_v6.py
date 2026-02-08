"""Run SUI with v006 TURBO strategy - Maximum Performance Edition."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
# CONFIGURATION - SUI (MAINACC_01)
# =============================================================================
SYMBOL = "SUIUSDT"
STRATEGY_ID = "VIP-SUI-V6"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_MAINACC_01"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_MAINACC_01"]
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
        timeout_reconciliation=30.0,
    )

    # SUI: order_qty=80, max=800
    strategy_config = LeadLagMMv6TurboConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        order_qty=80.0,
        max_position_qty=800.0,
        min_order_qty=20.0,
        # === SAFE PROFILE: Wider spreads, stricter regime detection ===
        spread_bps=80.0,  # Was 60, now 80 for safety margin
        quote_refresh_interval_ms=30,
        min_quote_lifetime_ms=20,
        ofi_enabled=True,
        ofi_widen_bps=15.0,  # Was 10, more aggressive
        ofi_tighten_bps=3.0,  # Was 5, less aggressive recovery
        volatility_enabled=True,
        volatility_max_widen_bps=50.0,  # Was 30, more protection
        regime_detection_enabled=True,
        regime_window_ticks=150,  # Was 100, longer window (less noise)
        regime_trending_threshold=0.65,  # Stricter trending detection
        regime_ranging_threshold=0.25,   # Wider hysteresis
        regime_trending_spread_mult=2.5,  # Was 2.0, now 2.5x in trends
        regime_trending_size_mult=0.3,    # Was 0.5, now 0.3x in trends
        guard_threshold_bps=5.0,  # Was 10, stricter
        guard_hysteresis_bps=10.0,  # Was 5, wider hysteresis
        hard_position_cap_enabled=True,
        position_cap_buffer_pct=0.95,
        runaway_detection_enabled=True,
        runaway_window_fills=5,      # Was 10, detect faster
        runaway_threshold_pct=0.70,  # Was 0.80, stricter
        runaway_pause_secs=60,       # Was 30, longer pause
        maker_fee_bps=10.0,
        min_profit_bps=5.0,
        # === GUARDIAN: React faster, widen more ===
        realized_spread_guardian_enabled=True,
        realized_spread_window_fills=5,      # Was 10, react faster
        realized_spread_min_bps=10.0,        # Was 0, require 10 bps profit
        realized_spread_widen_step_bps=20.0, # Was 10, bigger steps
        realized_spread_max_widen_bps=150.0, # Was 100, allow more widening
        realized_spread_cooldown_fills=3,    # Was 5, recover faster
        log_quotes=False,
        log_regime_changes=True,
        log_guards=True,
        log_fills=True,
        log_realized_spread=True,
        metrics_enabled=True,
        metrics_interval_secs=5,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv6Turbo(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} v006 TURBO")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
