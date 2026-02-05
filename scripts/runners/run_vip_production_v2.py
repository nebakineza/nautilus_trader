#!/usr/bin/env python3
"""VIP production runner v2 (multi-pair) using LeadLagMMv4Primer.

Fixed configuration based on actual Bybit balances:
- SUI:  411 ($440)
- DOGE: 5334 ($550)
- ETH:  0.01 ($22) - DISABLED (too small for efficient MM)
- LINK: 5.8 ($53)
- AVAX: 5.7 ($55)
- USDT: 1597 ($1594)

Total: ~$3,150 USD

Spreads set for VIP0 + MNT discount (0.075% maker / 0.075% taker = 15bps roundtrip):
- Need > 15bps spread to be profitable after fees
- Using 25-35bps spreads for safety margin

Usage:
  python run_vip_production_v2.py
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
from lead_lag_bybit_binance_mm_v004_primer import (
    LeadLagMMv4Primer,
    LeadLagMMv4PrimerConfig,
)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} not set")
    return value


# Use SENTINEL keys (this account is IP-bound to sentinel-vps)
BYBIT_API_KEY = _require_env("BYBIT_API_KEY_SENTINEL")
BYBIT_API_SECRET = _require_env("BYBIT_API_SECRET_SENTINEL")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

LOG_DIR = Path(os.getenv("NAUTILUS_LOG_DIR", "./logs")).expanduser().resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

TRADER_ID = os.getenv("TRADER_ID", "VIP1-PRODUCTION-MULTI")


# =============================================================================
# PAIR CONFIGURATIONS - Based on actual Bybit balances (Feb 5, 2026)
# =============================================================================
# VIP0 + MNT discount fees: 0.075% maker / 0.075% taker = 15bps roundtrip
# Target: Spread must be > 15bps to be profitable
# Safety margin: Using 25-40bps spreads
# =============================================================================

PAIRS: list[dict[str, object]] = [
    # SUI: Primary volume engine - $440 in SUI + $320 USDT allocation = $760 total
    # Price ~$1.07, so 411 SUI = $440
    # Quote size: 80 SUI (~$85), max position 400 SUI (~$430)
    {
        "symbol": "SUIUSDT",
        "order_qty": Decimal("80.0"),          # ~$85 per quote
        "min_order_qty": Decimal("20.0"),      # 25% min for skewing
        "max_position_qty": Decimal("600.0"),  # ~$640 max inventory
        "guard_threshold_bps": Decimal("15.0"),
        "spread_bps": Decimal("25.0"),         # 25bps = 10bps profit after 15bps fees
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 3000,
        "refresh_offset": 0,
        "liquidity_high_qty": Decimal("3000.0"),
        "liquidity_low_qty": Decimal("150.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 1000,
        "internal_price_delta_limit": Decimal("5.0"),
    },
    # DOGE: Good volume - $550 in DOGE + $320 USDT allocation = $870 total
    # Price ~$0.103, so 5334 DOGE = $550
    # Quote size: 1500 DOGE (~$155), max position 8000 DOGE (~$824)
    {
        "symbol": "DOGEUSDT",
        "order_qty": Decimal("1500.0"),        # ~$155 per quote
        "min_order_qty": Decimal("375.0"),     # 25% min for skewing
        "max_position_qty": Decimal("8000.0"), # ~$824 max inventory
        "guard_threshold_bps": Decimal("20.0"),
        "spread_bps": Decimal("35.0"),         # 35bps = 20bps profit after fees
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 2000,
        "refresh_offset": 50,
        "liquidity_high_qty": Decimal("400000"),
        "liquidity_low_qty": Decimal("40000"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("5.0"),
        "min_quote_lifetime_ms": 1000,
        "internal_price_delta_limit": Decimal("3.0"),
    },
    # LINK: Medium volume - $53 in LINK + $320 USDT allocation = $373 total
    # Price ~$9.15, so 5.8 LINK = $53
    # Quote size: 2 LINK (~$18), max position 10 LINK (~$92)
    {
        "symbol": "LINKUSDT",
        "order_qty": Decimal("2.0"),           # ~$18 per quote
        "min_order_qty": Decimal("0.5"),       # 25% min for skewing
        "max_position_qty": Decimal("15.0"),   # ~$137 max inventory
        "guard_threshold_bps": Decimal("15.0"),
        "spread_bps": Decimal("30.0"),         # 30bps = 15bps profit after fees
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 2500,
        "refresh_offset": 100,
        "liquidity_high_qty": Decimal("500.0"),
        "liquidity_low_qty": Decimal("50.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 800,
        "internal_price_delta_limit": Decimal("3.0"),
    },
    # AVAX: Medium volume - $55 in AVAX + $320 USDT allocation = $375 total
    # Price ~$9.67, so 5.7 AVAX = $55
    # Quote size: 2 AVAX (~$19), max position 10 AVAX (~$97)
    {
        "symbol": "AVAXUSDT",
        "order_qty": Decimal("2.0"),           # ~$19 per quote
        "min_order_qty": Decimal("0.5"),       # 25% min for skewing
        "max_position_qty": Decimal("15.0"),   # ~$145 max inventory
        "guard_threshold_bps": Decimal("15.0"),
        "spread_bps": Decimal("30.0"),         # 30bps = 15bps profit after fees
        "min_profit_bps": Decimal("1.0"),
        "global_guard_id": None,
        "refresh_interval": 2500,
        "refresh_offset": 150,
        "liquidity_high_qty": Decimal("8000.0"),
        "liquidity_low_qty": Decimal("400.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 800,
        "internal_price_delta_limit": Decimal("3.0"),
    },
]

# NOTE: ETH disabled - only $22 balance, not enough for efficient market making


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
            log_file_name="vip_multi_live.log",
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

        config_strategy = LeadLagMMv4PrimerConfig(
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
            # Fee-adjusted settings for VIP0 + MNT discount
            maker_fee_bps=Decimal("7.5"),       # 0.075% maker fee
            min_profit_bps=pair["min_profit_bps"],
            min_quote_reserve_ratio=Decimal("0.3"),
            min_quote_reserve_usdt=Decimal("100"),  # Lower reserve requirement
            daily_loss_limit_usdt=Decimal("25"),    # Tighter daily loss limit per pair
            max_drawdown_pct=Decimal("1.00"),       # 100% = effectively disabled
        )

        node.trader.add_strategy(LeadLagMMv4Primer(config=config_strategy))

    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)

    node.build()
    
    # Initialize cross-pair inventory coordinator
    from nautilus_trader.model.objects import Currency
    inventory_coordinator = CrossPairInventoryCoordinator(
        cache=node.cache,
        logger=node.trader._log,
        target_allocations=None,  # Equal weight across all pairs
        max_pair_weight=Decimal("0.50"),  # Max 50% in any single pair (SUI is heavy)
        min_pair_weight=Decimal("0.05"),  # Min 5% target
        rebalance_threshold=Decimal("0.20"),  # 20% deviation triggers adjustment
    )
    
    # Register all tracked currencies
    for pair in PAIRS:
        base_code = pair["symbol"].replace("USDT", "")
        base_currency = Currency.from_str(base_code)
        inventory_coordinator.register_currency(base_currency)
    
    inventory_coordinator.register_currency(Currency.from_str("USDT"))
    
    # Inject coordinator into all strategies
    for strategy in node.trader.strategies():
        if hasattr(strategy, 'set_inventory_coordinator'):
            strategy.set_inventory_coordinator(inventory_coordinator)

    print(
        f"\n>>> DEPLOYING MULTI-PAIR PORTFOLIO v2 (testnet={BYBIT_TESTNET}) <<<\n"
        f"TraderId: {TRADER_ID}\n"
        f"Log dir: {LOG_DIR}\n"
        f"Pairs: {[p['symbol'] for p in PAIRS]}\n"
        f"✅ Cross-pair inventory coordinator: ACTIVE\n"
        f"✅ Killswitch drawdown: DISABLED (100%)\n"
        f"✅ Fee profile: VIP0 + MNT (7.5bps maker)\n"
    )

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
