"""Run VIP pair with v006 TURBO strategy - Maximum Performance Edition."""

from __future__ import annotations

import os
import sys

# Add strategy package to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.adapters.binance.factories import BinanceSpotDataClientFactory
from nautilus_trader.adapters.bybit.common.enums import BybitProductType
from nautilus_trader.adapters.bybit.config import BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit.factories import BybitDataClientFactory, BybitExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import ClientId, InstrumentId

from lead_lag_bybit_binance_mm_v006_turbo import LeadLagMMv6Turbo, LeadLagMMv6TurboConfig

# =============================================================================
# CONFIGURATION
# =============================================================================
SYMBOL = os.environ.get("TRADING_SYMBOL", "SUIUSDT")
API_KEY_ENV = os.environ.get("API_KEY_ENV", "MAINACC_01")
STRATEGY_ID = f"VIP-{SYMBOL[:3].upper()}"

# API Keys
BYBIT_API_KEY = os.environ[f"BYBIT_API_KEY_{API_KEY_ENV}"]
BYBIT_API_SECRET = os.environ[f"BYBIT_API_SECRET_{API_KEY_ENV}"]

# Instrument IDs
FOLLOWER_ID = InstrumentId.from_str(f"{SYMBOL}-SPOT.BYBIT")
LEADER_ID = InstrumentId.from_str(f"{SYMBOL}.BINANCE")

# Per-pair sizing (tune based on wallet allocation)
PAIR_CONFIG = {
    "SUIUSDT": {"order_qty": 80.0, "max_pos": 800.0, "min_qty": 1.0},
    "LINKUSDT": {"order_qty": 2.0, "max_pos": 20.0, "min_qty": 0.1},
    "AVAXUSDT": {"order_qty": 2.0, "max_pos": 20.0, "min_qty": 0.1},
    "ENAUSDT": {"order_qty": 100.0, "max_pos": 1000.0, "min_qty": 1.0},
    "NEARUSDT": {"order_qty": 10.0, "max_pos": 100.0, "min_qty": 0.1},
    "ARBUSDT": {"order_qty": 100.0, "max_pos": 1000.0, "min_qty": 1.0},
    "ONDOUSDT": {"order_qty": 50.0, "max_pos": 500.0, "min_qty": 1.0},
    "TONUSDT": {"order_qty": 10.0, "max_pos": 100.0, "min_qty": 0.1},
}

# Get pair-specific config
pair_cfg = PAIR_CONFIG.get(SYMBOL, {"order_qty": 10.0, "max_pos": 100.0, "min_qty": 0.1})


def main():
    # Trading Node Config
    node_config = TradingNodeConfig(
        trader_id=STRATEGY_ID,
        data_clients={
            "BINANCE_SPOT": BinanceDataClientConfig(
                api_key="",
                api_secret="",
                account_type=BinanceAccountType.SPOT,
                instrument_provider=InstrumentProviderConfig(load_ids=[LEADER_ID]),
            ),
            "BYBIT": BybitDataClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                product_types=[BybitProductType.SPOT],
                instrument_provider=InstrumentProviderConfig(load_ids=[FOLLOWER_ID]),
            ),
        },
        exec_clients={
            "BYBIT": BybitExecClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                product_types=[BybitProductType.SPOT],
                instrument_provider=InstrumentProviderConfig(load_ids=[FOLLOWER_ID]),
            ),
        },
        timeout_connection=30.0,
    )

    # Strategy Config
    strategy_config = LeadLagMMv6TurboConfig(
        strategy_id=STRATEGY_ID,
        follower_instrument_id=FOLLOWER_ID,
        leader_instrument_id=LEADER_ID,
        # === SIZING ===
        order_qty=pair_cfg["order_qty"],
        max_position_qty=pair_cfg["max_pos"],
        min_order_qty=pair_cfg["min_qty"],
        # === SPREAD (60 bps for forward test) ===
        spread_bps=60.0,
        # === TOXIC FLOW AVOIDANCE ===
        ofi_enabled=True,
        ofi_widen_bps=10.0,
        ofi_tighten_bps=5.0,
        volatility_enabled=True,
        # === REGIME DETECTION ===
        regime_detection_enabled=True,
        regime_window_ticks=100,
        regime_trending_spread_mult=2.0,
        regime_trending_size_mult=0.5,
        # === SAFETY ===
        hard_position_cap_enabled=True,
        position_cap_buffer_pct=0.95,
        runaway_detection_enabled=True,
        runaway_pause_secs=30,
        # === FEES (VIP0 + MNT) ===
        maker_fee_bps=7.5,
        min_profit_bps=5.0,
        # === LOGGING (minimal for speed) ===
        log_quotes=False,
        log_regime_changes=True,
        log_guards=True,
        log_fills=True,
        # === METRICS ===
        metrics_enabled=True,
        metrics_interval_secs=5,
    )

    # Build and run node
    node = TradingNode(config=node_config)
    node.trader.add_strategy(LeadLagMMv6Turbo(config=strategy_config))

    # Register factories
    node.add_data_client_factory("BINANCE_SPOT", BinanceSpotDataClientFactory)
    node.add_data_client_factory("BYBIT", BybitDataClientFactory)
    node.add_exec_client_factory("BYBIT", BybitExecClientFactory)

    # Build and run
    node.build()
    node.run()


if __name__ == "__main__":
    main()
