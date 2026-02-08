"""Run SUI with v007 FORTRESS strategy - Maximum Protection Edition."""

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

from lead_lag_bybit_binance_mm_v007_fortress import LeadLagMMv7Fortress, LeadLagMMv7FortressConfig

# =============================================================================
# CONFIGURATION - SUI (MAINACC_01)
# =============================================================================
SYMBOL = "SUIUSDT"
STRATEGY_ID = "VIP-SUI-V7"
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

    # SUI: SAFE PROFILE - order_qty=80, max=800
    strategy_config = LeadLagMMv7FortressConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        # === SIZING ===
        order_qty=80.0,
        max_position_qty=800.0,
        min_order_qty=20.0,
        # === BASE SPREAD (SAFE) ===
        spread_bps=80.0,
        quote_refresh_interval_ms=30,
        min_quote_lifetime_ms=20,
        # === OFI ===
        ofi_enabled=True,
        ofi_widen_bps=15.0,
        ofi_tighten_bps=3.0,
        # === VOLATILITY ===
        volatility_enabled=True,
        volatility_max_widen_bps=50.0,
        # === REGIME (STRICT) ===
        regime_detection_enabled=True,
        regime_window_ticks=150,
        regime_trending_threshold=0.65,
        regime_ranging_threshold=0.25,
        regime_trending_spread_mult=2.5,
        regime_trending_size_mult=0.3,
        # === TREND FILTER (NEW) ===
        trend_filter_enabled=True,
        trend_filter_threshold=0.75,
        trend_filter_pause_secs=60,
        # === GUARD ===
        guard_threshold_bps=5.0,
        guard_hysteresis_bps=10.0,
        # === INVENTORY SKEW (NEW) ===
        inventory_skew_enabled=True,
        inventory_skew_threshold_pct=0.30,
        inventory_skew_max_widen_bps=50.0,
        inventory_skew_block_threshold_pct=0.80,
        # === POSITION SAFETY ===
        hard_position_cap_enabled=True,
        position_cap_buffer_pct=0.90,
        # === RUNAWAY (STRICT) ===
        runaway_detection_enabled=True,
        runaway_window_fills=5,
        runaway_threshold_pct=0.70,
        runaway_pause_secs=60,
        # === HIT RATE (NEW) ===
        hit_rate_tracking_enabled=True,
        hit_rate_window_fills=20,
        hit_rate_imbalance_threshold=0.70,
        hit_rate_pause_secs=30,
        # === FEES ===
        maker_fee_bps=10.0,
        min_profit_bps=10.0,
        # === GUARDIAN (ENHANCED) ===
        realized_spread_guardian_enabled=True,
        realized_spread_window_fills=5,
        realized_spread_min_bps=10.0,
        realized_spread_widen_step_bps=20.0,
        realized_spread_max_widen_bps=150.0,
        realized_spread_cooldown_fills=3,
        log_realized_spread=True,
        # === FIFO P&L (NEW) ===
        fifo_pnl_enabled=True,
        fifo_pnl_window=50,
        fifo_pnl_loss_threshold_bps=-20.0,
        fifo_pnl_pause_secs=120,
        # === DYNAMIC SPREAD (NEW) ===
        dynamic_spread_enabled=True,
        dynamic_spread_min_bps=50.0,  # Min 50bps ensures profit above 15bps fees
        dynamic_spread_max_bps=200.0,
        dynamic_spread_adjust_rate=0.1,
        # === LOGGING ===
        log_quotes=False,
        log_regime_changes=True,
        log_guards=True,
        log_fills=True,
        log_protection_events=True,
        metrics_enabled=True,
        metrics_interval_secs=5,
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv7Fortress(config=strategy_config))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
    node.build()
    print(f">>> STARTING {SYMBOL} v007 FORTRESS")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
