"""Run ENA with v007 FORTRESS strategy - ULTRA SAFE Profile.

ENA lost -348 bps on Feb 6, 2026 due to:
- 16.6% uptrend exposure
- Only +16 bps FIFO matched spread (below 20 bps fee breakeven)

This runner uses maximum protection settings.
"""

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
# CONFIGURATION - ENA (MAINACC_04)
# =============================================================================
SYMBOL = "ENAUSDT"
STRATEGY_ID = "VIP-ENA-V7"
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_MAINACC_04"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_MAINACC_04"]
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

    # ENA: ULTRA SAFE PROFILE - reduced size, wide spreads
    strategy_config = LeadLagMMv7FortressConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        # === SIZING (REDUCED) ===
        order_qty=100.0,  # Was 300, reduced by 67%
        max_position_qty=500.0,  # Was 1500, reduced by 67%
        min_order_qty=10.0,
        # === BASE SPREAD (ULTRA WIDE) ===
        spread_bps=120.0,  # Very wide base spread
        quote_refresh_interval_ms=30,
        min_quote_lifetime_ms=20,
        # === OFI (AGGRESSIVE) ===
        ofi_enabled=True,
        ofi_widen_bps=25.0,  # Very aggressive widening
        ofi_tighten_bps=2.0,  # Very slow tightening
        # === VOLATILITY ===
        volatility_enabled=True,
        volatility_max_widen_bps=80.0,  # Large volatility buffer
        # === REGIME (ULTRA STRICT) ===
        regime_detection_enabled=True,
        regime_window_ticks=200,  # Very long window
        regime_trending_threshold=0.70,  # Very strict
        regime_ranging_threshold=0.20,  # Wide hysteresis
        regime_trending_spread_mult=3.0,  # 3x spread in trends!
        regime_trending_size_mult=0.2,  # 0.2x size in trends
        # === TREND FILTER (STRICT) ===
        trend_filter_enabled=True,
        trend_filter_threshold=0.70,  # Lower threshold = more pausing
        trend_filter_pause_secs=120,  # Longer pause
        # === GUARD (TIGHT) ===
        guard_threshold_bps=3.0,
        guard_hysteresis_bps=15.0,
        # === INVENTORY SKEW (AGGRESSIVE) ===
        inventory_skew_enabled=True,
        inventory_skew_threshold_pct=0.20,  # Start widening early
        inventory_skew_max_widen_bps=80.0,  # Large max widen
        inventory_skew_block_threshold_pct=0.70,  # Block earlier
        # === POSITION SAFETY ===
        hard_position_cap_enabled=True,
        position_cap_buffer_pct=0.85,  # Stricter cap
        # === RUNAWAY (VERY STRICT) ===
        runaway_detection_enabled=True,
        runaway_window_fills=3,  # Very fast detection
        runaway_threshold_pct=0.65,  # Very strict
        runaway_pause_secs=120,  # Long pause
        # === HIT RATE (STRICT) ===
        hit_rate_tracking_enabled=True,
        hit_rate_window_fills=15,  # Smaller window
        hit_rate_imbalance_threshold=0.65,  # Stricter
        hit_rate_pause_secs=60,
        # === FEES ===
        maker_fee_bps=10.0,
        min_profit_bps=15.0,  # Require more profit
        # === GUARDIAN (ULTRA RESPONSIVE) ===
        realized_spread_guardian_enabled=True,
        realized_spread_window_fills=3,  # React very fast
        realized_spread_min_bps=20.0,  # Require 20 bps profit
        realized_spread_widen_step_bps=30.0,  # Big steps
        realized_spread_max_widen_bps=200.0,  # Allow huge widening
        realized_spread_cooldown_fills=2,  # Fast recovery
        log_realized_spread=True,
        # === FIFO P&L (STRICT) ===
        fifo_pnl_enabled=True,
        fifo_pnl_window=30,  # Smaller window
        fifo_pnl_loss_threshold_bps=-10.0,  # Pause at lower loss
        fifo_pnl_pause_secs=180,  # 3 minute pause
        # === DYNAMIC SPREAD (CONSERVATIVE) ===
        dynamic_spread_enabled=True,
        dynamic_spread_min_bps=60.0,  # Higher minimum
        dynamic_spread_max_bps=300.0,  # Allow very wide
        dynamic_spread_adjust_rate=0.15,  # Faster adjustment
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
    print(f">>> STARTING {SYMBOL} v007 FORTRESS (ULTRA SAFE)")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
