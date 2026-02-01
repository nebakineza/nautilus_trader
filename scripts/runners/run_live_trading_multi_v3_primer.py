#!/usr/bin/env python3
"""Live multi-symbol Lead-Lag MM v3 Primer (VIP1 acquisition profile)."""

import os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

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

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError("BYBIT API credentials not set!")

pairs = [
    {
        "symbol": "SOLUSDT",
        "order_qty": Decimal("0.2"),
        "min_order_qty": Decimal("0.2"),
        "max_position_qty": Decimal("2.0"),
        "guard_threshold_bps": Decimal("10.0"),
        "spread_bps": Decimal("24.0"),
        "min_profit_bps": Decimal("3.0"),
        "global_guard_id": None,
        "refresh_interval": 3000,
        "refresh_offset": 0,
        "liquidity_high_qty": Decimal("1000.0"),
        "liquidity_low_qty": Decimal("50.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("3.0"),
        "min_quote_lifetime_ms": 1000,
    },
    {
        "symbol": "DOGEUSDT",
        "order_qty": Decimal("150"),
        "min_order_qty": Decimal("150"),
        "max_position_qty": Decimal("1500.0"),
        "guard_threshold_bps": Decimal("12.0"),
        "spread_bps": Decimal("26.0"),
        "min_profit_bps": Decimal("2.0"),
        "global_guard_id": "SOLUSDT.BINANCE_SPOT",
        "refresh_interval": 5000,
        "refresh_offset": 33,
        "liquidity_high_qty": Decimal("200000"),
        "liquidity_low_qty": Decimal("20000"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("5.0"),
        "min_quote_lifetime_ms": 2000,
    },
    {
        "symbol": "AVAXUSDT",
        "order_qty": Decimal("1.0"),
        "min_order_qty": Decimal("1.0"),
        "max_position_qty": Decimal("10.0"),
        "guard_threshold_bps": Decimal("12.0"),
        "spread_bps": Decimal("24.0"),
        "min_profit_bps": Decimal("2.0"),
        "global_guard_id": "SOLUSDT.BINANCE_SPOT",
        "refresh_interval": 5000,
        "refresh_offset": 66,
        "liquidity_high_qty": Decimal("5000.0"),
        "liquidity_low_qty": Decimal("200.0"),
        "ofi_enabled": True,
        "ofi_max_bps": Decimal("5.0"),
        "min_quote_lifetime_ms": 2000,
    },
]

leader_ids = [InstrumentId.from_str(f"{p['symbol']}.BINANCE_SPOT") for p in pairs]
follower_ids = [InstrumentId.from_str(f"{p['symbol']}-SPOT.BYBIT") for p in pairs]

global_guard_ids = [
    InstrumentId.from_str(p["global_guard_id"]) for p in pairs if p["global_guard_id"]
]

config_node = TradingNodeConfig(
    trader_id=TraderId("LEADLAG-MULTI-003-PRIMER"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory="/home/ubuntu/trading/logs",
        log_file_name="leadlag_multi_v3_primer_live.log",
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

for pair in pairs:
    leader_id = InstrumentId.from_str(f"{pair['symbol']}.BINANCE_SPOT")
    follower_id = InstrumentId.from_str(f"{pair['symbol']}-SPOT.BYBIT")
    guard_id = InstrumentId.from_str(pair["global_guard_id"]) if pair["global_guard_id"] else None

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
        quote_refresh_interval_ms=pair["refresh_interval"],
        quote_refresh_jitter_ms=10,
        quote_refresh_offset_ms=pair["refresh_offset"],
        min_quote_lifetime_ms=pair.get("min_quote_lifetime_ms", 60),
        min_requote_ticks=1,
        book_type=BookType.L2_MBP,
        book_depth=50,
        post_only=True,
        ofi_enabled=pair["ofi_enabled"],
        ofi_max_bps=pair["ofi_max_bps"],
        liquidity_high_qty=pair["liquidity_high_qty"],
        liquidity_low_qty=pair["liquidity_low_qty"],
        min_order_qty=pair["min_order_qty"],
        max_order_qty=pair["order_qty"] * Decimal("3"),
        internal_price_delta_limit=Decimal("8.0"),
        maker_fee_bps=Decimal("7.5"),
        min_profit_bps=pair.get("min_profit_bps", Decimal("0.0")),
        min_quote_reserve_ratio=Decimal("0.5"),
        min_quote_reserve_usdt=Decimal("500"),
        daily_loss_limit_usdt=Decimal("200"),
        max_drawdown_pct=Decimal("0.05"),
    )

    node.trader.add_strategy(LeadLagMMv3Primer(config=config_strategy))

node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)
node.build()

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
