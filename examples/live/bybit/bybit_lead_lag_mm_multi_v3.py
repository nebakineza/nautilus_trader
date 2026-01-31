#!/usr/bin/env python3
"""Run LeadLagMMv3 multi-symbol (staged only)."""

from __future__ import annotations

import sys
from decimal import Decimal

from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.datetime import utc_now
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.config import MarketDataClientConfig
from nautilus_trader.execution.config import ExecutionClientConfig
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId, TraderId
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.node import TradingNode

try:
    from strategy.lead_lag_bybit_binance_mm_v003 import LeadLagMMv3
    from strategy.lead_lag_bybit_binance_mm_v003 import LeadLagMMv3Config
except ModuleNotFoundError:
    sys.path.insert(0, "/home/ubuntu/trading")
    from lead_lag_bybit_binance_mm_v003 import LeadLagMMv3
    from lead_lag_bybit_binance_mm_v003 import LeadLagMMv3Config


def get_config(symbol: str, leader_id: str, guard_id: str | None, size: Decimal, max_size: Decimal) -> LeadLagMMv3Config:
    if symbol == "BTCUSDT":
        spread = Decimal("16.0")
        guard = Decimal("10.0")
        liquidity_high = Decimal("5.0")
        liquidity_low = Decimal("0.1")
    elif symbol == "ETHUSDT":
        spread = Decimal("16.0")
        guard = Decimal("10.0")
        liquidity_high = Decimal("80.0")
        liquidity_low = Decimal("5.0")
    else:
        spread = Decimal("20.0")
        guard = Decimal("10.0")
        liquidity_high = Decimal("1000.0")
        liquidity_low = Decimal("50.0")

    return LeadLagMMv3Config(
        leader_instrument_id=InstrumentId.from_str(leader_id),
        follower_instrument_id=InstrumentId.from_str(f"{symbol}-SPOT.BYBIT"),
        global_guard_id=InstrumentId.from_str(guard_id) if guard_id else None,
        order_qty=size,
        max_position_qty=max_size,
        spread_bps=spread,
        guard_threshold_bps=guard,
        global_guard_threshold_bps=Decimal("15.0"),
        global_guard_window_ms=500,
        quote_refresh_interval_ms=30,
        quote_refresh_jitter_ms=30,
        min_quote_lifetime_ms=20,
        min_requote_ticks=1,
        book_type=BookType.L2_MBP,
        book_depth=50,
        post_only=True,
        liquidity_high_qty=liquidity_high,
        liquidity_low_qty=liquidity_low,
        min_order_qty=size * Decimal("0.25"),
        max_order_qty=size * Decimal("2.0"),
    )


def main() -> None:
    trader_id = TraderId("LEADLAG-MULTI-003")

    logging = LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory=".",
        log_file_name="leadlag_multi_v3.log",
        clear_log_file=True,
    )

    node = TradingNode(
        trader_id=trader_id,
        log_config=logging,
    )

    pairs = [
        {
            "symbol": "BTCUSDT",
            "order_qty": Decimal("0.0001"),
            "max_position_qty": Decimal("0.001"),
            "leader_id": "BTCUSDT.BINANCE_SPOT",
            "guard_id": None,
        },
        {
            "symbol": "ETHUSDT",
            "order_qty": Decimal("0.002"),
            "max_position_qty": Decimal("0.02"),
            "leader_id": "ETHUSDT.BINANCE_SPOT",
            "guard_id": "BTCUSDT.BINANCE_SPOT",
        },
        {
            "symbol": "SOLUSDT",
            "order_qty": Decimal("0.1"),
            "max_position_qty": Decimal("1.0"),
            "leader_id": "SOLUSDT.BINANCE_SPOT",
            "guard_id": "BTCUSDT.BINANCE_SPOT",
        },
    ]

    strategies: list[StrategyConfig] = []
    for idx, pair in enumerate(pairs):
        config = get_config(
            symbol=pair["symbol"],
            leader_id=pair["leader_id"],
            guard_id=pair["guard_id"],
            size=pair["order_qty"],
            max_size=pair["max_position_qty"],
        )
        strategies.append(config)
        node.add_strategy(LeadLagMMv3(config=config))

    data_client = MarketDataClientConfig(
        venue="BYBIT",
        config_path="./deploy/bybit.json",
    )
    exec_client = ExecutionClientConfig(
        venue="BYBIT",
        config_path="./deploy/bybit.json",
    )

    node.add_data_client(data_client)
    node.add_execution_client(exec_client)

    node.build()
    node.run()


if __name__ == "__main__":
    main()
