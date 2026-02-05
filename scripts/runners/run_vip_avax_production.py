#!/usr/bin/env python3
"""VIP AVAX production runner using LeadLagMMv3Primer.

Usage:
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python run_vip_avax_production.py
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

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
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.portfolio.config import PortfolioConfig

from strategy.lead_lag_bybit_binance_mm_v003_primer import (
    LeadLagMMv3Primer,
    LeadLagMMv3PrimerConfig,
)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} not set")
    return value


BYBIT_API_KEY = _require_env("BYBIT_API_KEY_LLMM")
BYBIT_API_SECRET = _require_env("BYBIT_API_SECRET_LLMM")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

LOG_DIR = Path(os.getenv("NAUTILUS_LOG_DIR", "./logs")).expanduser().resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

TRADER_ID = os.getenv("TRADER_ID", "VIP1-PRODUCTION-AVAX")


# AVAX configuration based on live trading data showing +$86 profit with 269 trades
PAIRS: list[dict[str, object]] = [
    {
        "symbol": "AVAXUSDT",
        "order_qty": Decimal("5.0"),  # ~$50 per order at $10/AVAX
        "min_order_qty": Decimal("2.5"),
        "max_position_qty": Decimal("100.0"),
        "guard_threshold_bps": Decimal("12.0"),
        "spread_bps": Decimal("15.0"),
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 3000,
        "refresh_offset": 0,
        "liquidity_high_qty": Decimal("2000.0"),
        "liquidity_low_qty": Decimal("100.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("4.0"),
        "min_quote_lifetime_ms": 1200,
        "internal_price_delta_limit": Decimal("0.0"),
    },
]


def main() -> None:
    leader_ids = [InstrumentId.from_str(f"{p['symbol']}.BINANCE_SPOT") for p in PAIRS]
    follower_ids = [InstrumentId.from_str(f"{p['symbol']}-SPOT.BYBIT") for p in PAIRS]

    strategies = []
    for pair in PAIRS:
        leader_id = InstrumentId.from_str(f"{pair['symbol']}.BINANCE_SPOT")
        follower_id = InstrumentId.from_str(f"{pair['symbol']}-SPOT.BYBIT")

        config = LeadLagMMv3PrimerConfig(
            leader_instrument_id=leader_id,
            follower_instrument_id=follower_id,
            order_qty=pair["order_qty"],
            min_order_qty=pair["min_order_qty"],
            max_position_qty=pair["max_position_qty"],
            guard_threshold_bps=pair["guard_threshold_bps"],
            spread_bps=pair["spread_bps"],
            min_profit_bps=pair["min_profit_bps"],
            global_guard_id=pair.get("global_guard_id"),
            quote_refresh_interval_ms=pair["refresh_interval"],
            quote_refresh_offset_ms=pair["refresh_offset"],
            liquidity_high_qty=pair["liquidity_high_qty"],
            liquidity_low_qty=pair["liquidity_low_qty"],
            ofi_enabled=pair["ofi_enabled"],
            ofi_max_bps=pair["ofi_max_bps"],
            min_quote_lifetime_ms=pair["min_quote_lifetime_ms"],
            internal_price_delta_limit=pair["internal_price_delta_limit"],
        )
        strategies.append(LeadLagMMv3Primer(config=config))

    binance_data = BinanceDataClientConfig(
        api_key=None,
        api_secret=None,
        account_type=BinanceAccountType.SPOT,
        base_url_http=None,
        base_url_ws=None,
        us=False,
        testnet=False,
        instrument_provider=InstrumentProviderConfig(load_all=True),
    )

    bybit_data = BybitDataClientConfig(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        product_types=[BybitProductType.SPOT],
        testnet=BYBIT_TESTNET,
        instrument_provider=InstrumentProviderConfig(load_all=True),
    )

    bybit_exec = BybitExecClientConfig(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        product_types=[BybitProductType.SPOT],
        testnet=BYBIT_TESTNET,
        instrument_provider=InstrumentProviderConfig(load_all=True),
    )

    config = TradingNodeConfig(
        trader_id=TraderId(TRADER_ID),
        logging=LoggingConfig(
            log_level="INFO",
            log_directory=LOG_DIR,
            log_file_format="{time:YYYY-MM-DD}_{name}.log",
            log_file_name=f"{TRADER_ID}.service",
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
        ),
        risk_engine=LiveRiskEngineConfig(
            bypass=False,
            max_order_submit_rate="100/00:00:01",
            max_order_modify_rate="100/00:00:01",
            max_notional_per_order={},
        ),
        data_clients={
            "BINANCE_SPOT": binance_data,
            "BYBIT": bybit_data,
        },
        exec_clients={"BYBIT": bybit_exec},
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    node = TradingNode(config=config)

    # Subscribe to orderbooks
    for leader_id in leader_ids:
        node.trader.subscribe(
            f"orderbook.{leader_id.venue}.{leader_id.symbol.value}",
            book_type=BookType.L2_MBP,
        )
    for follower_id in follower_ids:
        node.trader.subscribe(
            f"orderbook.{follower_id.venue}.{follower_id.symbol.value}",
            book_type=BookType.L2_MBP,
        )

    # Add strategies
    for strategy in strategies:
        node.trader.add_strategy(strategy)

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
