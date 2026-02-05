#!/usr/bin/env python3
"""VIP production runner (SUI-heavy) using LeadLagMMv3Primer.

This runner is intentionally conservative:
- Uses spot instruments on both venues (leader=BINANCE_SPOT, follower=BYBIT spot).
- Keeps the risk engine enabled (no bypass).
- Requires BYBIT_API_KEY and BYBIT_API_SECRET via environment (.env supported).

Usage:
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python run_vip_production.py

Optional env vars:
  BYBIT_TESTNET=true|false
  NAUTILUS_LOG_DIR=/path/to/logs   (default: ./logs)
  TRADER_ID=VIP1-PRODUCTION-SUI-HEAVY
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

from strategy.inventory.cross_pair_coordinator import CrossPairInventoryCoordinator
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

TRADER_ID = os.getenv("TRADER_ID", "VIP1-PRODUCTION-SUI-HEAVY")


# --- SUI-heavy portfolio (spot) ---
# Note: Backtests used min_profit_bps=1.0; if you lower this meaningfully
# (e.g. to 0.1 bps), you may see higher fill/volume but also more adverse selection.
PAIRS: list[dict[str, object]] = [
    # SUI: primary volume engine
    {
        "symbol": "SUIUSDT",
        "order_qty": Decimal("80.0"),
        "min_order_qty": Decimal("20.0"),  # 25% of base - allows inventory skewing
        "max_position_qty": Decimal("800.0"),
        "guard_threshold_bps": Decimal("15.0"),
        "spread_bps": Decimal("30.0"),
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 3000,
        "refresh_offset": 0,
        "liquidity_high_qty": Decimal("3000.0"),
        "liquidity_low_qty": Decimal("150.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 1000,
        "internal_price_delta_limit": Decimal("0.0"),
    },
    # DOGE: Anti-bleeding spread (40bps for VIP0+MNT = +25bps edge)
    {
        "symbol": "DOGEUSDT",
        "order_qty": Decimal("2000.0"),
        "min_order_qty": Decimal("500.0"),  # 25% of base - allows inventory skewing
        "max_position_qty": Decimal("20000.0"),
        "guard_threshold_bps": Decimal("20.0"),
        "spread_bps": Decimal("40.0"),
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 1000,
        "refresh_offset": 33,
        "liquidity_high_qty": Decimal("400000"),
        "liquidity_low_qty": Decimal("40000"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("5.0"),
        "min_quote_lifetime_ms": 1000,
        "internal_price_delta_limit": Decimal("2.0"),
    },
    # ETH: Anti-bleeding spread (50bps for VIP0+MNT = +35bps edge)
    {
        "symbol": "ETHUSDT",
        "order_qty": Decimal("0.01"),
        "min_order_qty": Decimal("0.0025"),  # 25% of base - allows inventory skewing
        "max_position_qty": Decimal("0.02"),
        "guard_threshold_bps": Decimal("10.0"),
        "spread_bps": Decimal("50.0"),
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 2000,
        "refresh_offset": 100,
        "liquidity_high_qty": Decimal("80.0"),
        "liquidity_low_qty": Decimal("5.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 800,
        "internal_price_delta_limit": Decimal("0.0"),
    },
    # LINK: Anti-bleeding spread (50bps for VIP0+MNT = +35bps edge)
    {
        "symbol": "LINKUSDT",
        "order_qty": Decimal("3.0"),
        "min_order_qty": Decimal("0.75"),  # 25% of base - allows inventory skewing
        "max_position_qty": Decimal("6.0"),
        "guard_threshold_bps": Decimal("10.0"),
        "spread_bps": Decimal("50.0"),
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 2000,
        "refresh_offset": 133,
        "liquidity_high_qty": Decimal("500.0"),
        "liquidity_low_qty": Decimal("50.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 800,
        "internal_price_delta_limit": Decimal("0.0"),
    },
    # AVAX: Anti-bleeding spread (50bps for VIP0+MNT = +35bps edge)
    {
        "symbol": "AVAXUSDT",
        "order_qty": Decimal("3.0"),
        "min_order_qty": Decimal("0.75"),  # 25% of base - allows inventory skewing
        "max_position_qty": Decimal("6.0"),
        "guard_threshold_bps": Decimal("10.0"),
        "spread_bps": Decimal("50.0"),
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 2000,
        "refresh_offset": 166,
        "liquidity_high_qty": Decimal("8000.0"),
        "liquidity_low_qty": Decimal("400.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 800,
        "internal_price_delta_limit": Decimal("0.0"),
    },
]


def main() -> None:
    leader_ids = [InstrumentId.from_str(f"{p['symbol']}.BINANCE_SPOT") for p in PAIRS]
    follower_ids = [InstrumentId.from_str(f"{p['symbol']}-SPOT.BYBIT") for p in PAIRS]

    global_guard_ids = [
        InstrumentId.from_str(p["global_guard_id"]) for p in PAIRS if p.get("global_guard_id")
    ]

    config_node = TradingNodeConfig(
        trader_id=TraderId(TRADER_ID),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="INFO",
            log_directory=str(LOG_DIR),
            log_file_name="vip_sui_heavy_live.log",
            clear_log_file=False,
            use_pyo3=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_instrument_ids=follower_ids,
            open_check_interval_secs=2.0,
            position_check_interval_secs=2.0,
            graceful_shutdown_on_exception=True,
        ),
        risk_engine=LiveRiskEngineConfig(bypass=False),
        portfolio=PortfolioConfig(min_account_state_logging_interval_ms=1_000),
        data_clients={
            "BINANCE_SPOT": BinanceDataClientConfig(
                venue=Venue("BINANCE_SPOT"),
                api_key=None,
                api_secret=None,
                account_type=BinanceAccountType.SPOT,
                base_url_http=None,
                base_url_ws=None,
                us=False,
                testnet=False,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset(leader_ids + global_guard_ids),
                ),
            ),
            BYBIT: BybitDataClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                base_url_http=None,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset(follower_ids),
                ),
                product_types=(BybitProductType.SPOT,),
                demo=False,
                testnet=BYBIT_TESTNET,
                recv_window_ms=30_000,
                max_retries=5,
                retry_delay_initial_ms=500,
                retry_delay_max_ms=5_000,
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                base_url_http=None,
                base_url_ws_private=None,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset(follower_ids),
                ),
                product_types=(BybitProductType.SPOT,),
                use_spot_position_reports=True,
                demo=False,
                testnet=BYBIT_TESTNET,
                recv_window_ms=30_000,
                max_retries=5,
                retry_delay_initial_ms=500,
                retry_delay_max_ms=5_000,
            ),
        },
        timeout_connection=20.0,
        timeout_reconciliation=20.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    node = TradingNode(config=config_node)

    for pair in PAIRS:
        symbol = str(pair["symbol"])
        leader_id = InstrumentId.from_str(f"{symbol}.BINANCE_SPOT")
        follower_id = InstrumentId.from_str(f"{symbol}-SPOT.BYBIT")
        guard_id = (
            InstrumentId.from_str(str(pair["global_guard_id"]))
            if pair.get("global_guard_id")
            else None
        )

        config_strategy = LeadLagMMv3PrimerConfig(
            follower_instrument_id=follower_id,
            leader_instrument_id=leader_id,
            global_guard_id=guard_id,
            order_qty=pair["order_qty"],
            max_position_qty=pair["max_position_qty"],
            spread_bps=pair["spread_bps"],
            guard_threshold_bps=pair["guard_threshold_bps"],
            global_guard_threshold_bps=Decimal("500.0"),
            global_guard_window_ms=500,
            quote_refresh_interval_ms=int(pair["refresh_interval"]),
            quote_refresh_jitter_ms=10,
            quote_refresh_offset_ms=int(pair["refresh_offset"]),
            min_quote_lifetime_ms=int(pair.get("min_quote_lifetime_ms", 1000)),
            min_requote_ticks=1,
            book_type=BookType.L2_MBP,
            book_depth=50,
            post_only=True,
            ofi_enabled=bool(pair["ofi_enabled"]),
            ofi_max_bps=pair["ofi_max_bps"],
            liquidity_high_qty=pair["liquidity_high_qty"],
            liquidity_low_qty=pair["liquidity_low_qty"],
            min_order_qty=pair["min_order_qty"],
            max_order_qty=pair["order_qty"] * Decimal("3"),
            internal_price_delta_limit=pair["internal_price_delta_limit"],
            maker_fee_bps=Decimal("7.5"),
            min_profit_bps=pair["min_profit_bps"],
            min_quote_reserve_ratio=Decimal("0.5"),
            min_quote_reserve_usdt=Decimal("500"),
            daily_loss_limit_usdt=Decimal("50"),
            max_drawdown_pct=Decimal("0.05"),
        )

        node.trader.add_strategy(LeadLagMMv3Primer(config=config_strategy))

    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)

    node.build()
    
    # Initialize cross-pair inventory coordinator
    # This is shared across all strategy instances for coordinated inventory management
    from nautilus_trader.model.objects import Currency
    inventory_coordinator = CrossPairInventoryCoordinator(
        cache=node.cache,
        logger=node.trader._log,  # Fixed: Use private _log attribute
        target_allocations=None,  # Equal weight across all pairs
        max_pair_weight=Decimal("0.40"),  # Max 40% in any single pair
        min_pair_weight=Decimal("0.05"),  # Min 5% target
        rebalance_threshold=Decimal("0.15"),  # 15% deviation triggers adjustment
    )
    
    # Register all tracked currencies
    for pair in PAIRS:
        # Extract base currency from symbol (e.g., "SUIUSDT" -> "SUI")
        base_code = pair["symbol"].replace("USDT", "")
        base_currency = Currency.from_str(base_code)
        inventory_coordinator.register_currency(base_currency)
    
    # Register USDT (quote currency)
    inventory_coordinator.register_currency(Currency.from_str("USDT"))
    
    # Inject coordinator into all strategies
    for strategy in node.trader.strategies():
        if hasattr(strategy, 'set_inventory_coordinator'):
            strategy.set_inventory_coordinator(inventory_coordinator)

    print(
        f"\n>>> DEPLOYING SUI-HEAVY PORTFOLIO (testnet={BYBIT_TESTNET}) <<<\n"
        f"TraderId: {TRADER_ID}\n"
        f"Log dir: {LOG_DIR}\n"
        f"Pairs: {[p['symbol'] for p in PAIRS]}\n"
        f"✅ Cross-pair inventory coordinator: ACTIVE\n"
    )

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
