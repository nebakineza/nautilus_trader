#!/usr/bin/env python3
"""VIP production runner (SUI) using LeadLagMMv5Primer (Toxic Flow Avoidance).

Features:
- Asymmetric OFI spread adjustment
- Volatility-based spread widening  
- Event-driven requoting on leader changes
- Dynamic quote lifetime
- Fill quality analytics

Usage:
  python run_vip_production_v5.py
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

from lead_lag_bybit_binance_mm_v005_primer import (
    LeadLagMMv5Primer,
    LeadLagMMv5PrimerConfig,
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

TRADER_ID = os.getenv("TRADER_ID", "VIP1-PRODUCTION-V5-SUI")


# --- SUI with v005 Toxic Flow Avoidance ---
PAIRS: list[dict[str, object]] = [
    {
        "symbol": "SUIUSDT",
        "order_qty": Decimal("80.0"),
        "min_order_qty": Decimal("20.0"),
        "max_position_qty": Decimal("800.0"),
        
        # === v005 TOXIC FLOW AVOIDANCE ===
        "spread_bps": Decimal("60.0"),  # Base spread
        
        # OFI (asymmetric)
        "ofi_enabled": True,
        "ofi_widen_threshold": Decimal("0.3"),
        "ofi_widen_bps": Decimal("10.0"),
        "ofi_tighten_bps": Decimal("5.0"),
        
        # Volatility
        "volatility_enabled": True,
        "volatility_base_threshold_bps": Decimal("5.0"),
        "volatility_max_widen_bps": Decimal("20.0"),
        
        # Dynamic lifetime
        "dynamic_lifetime_enabled": True,
        "lifetime_volatility_fast_ms": 500,
        "lifetime_volatility_slow_ms": 3000,
        
        # Event-driven requoting
        "event_driven_requote": True,
        "leader_update_min_interval_ms": 10,
        
        # Queue & inventory
        "queue_tracking_enabled": True,
        "inventory_urgency_enabled": True,
        
        # Spread intelligence
        "binance_spread_tracking": True,
        "competitive_monitoring": True,
        
        # Guard
        "guard_threshold_bps": Decimal("25.0"),
        "global_guard_id": None,
        
        # Timing
        "refresh_interval": 30,  # Faster base refresh for v005
        "refresh_offset": 0,
        "min_quote_lifetime_ms": 20,
        
        # Fees
        "maker_fee_bps": Decimal("7.5"),
        "min_profit_bps": Decimal("5.0"),
        
        # Inventory
        "internal_price_delta_limit": Decimal("8.0"),
        "liquidity_high_qty": Decimal("5000.0"),
        "liquidity_low_qty": Decimal("200.0"),
    },
]


def main() -> None:
    leader_ids = [InstrumentId.from_str(f"{p['symbol']}.BINANCE_SPOT") for p in PAIRS]
    follower_ids = [InstrumentId.from_str(f"{p['symbol']}-SPOT.BYBIT") for p in PAIRS]

    binance_data = BinanceDataClientConfig(
        venue=Venue("BINANCE_SPOT"),
        api_key=None,
        api_secret=None,
        account_type=BinanceAccountType.SPOT,
        base_url_http=None,
        base_url_ws=None,
        us=False,
        testnet=False,
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=frozenset(leader_ids)),
    )

    bybit_data = BybitDataClientConfig(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        base_url_http=None,
        product_types=(BybitProductType.SPOT,),
        testnet=BYBIT_TESTNET,
        instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=frozenset(follower_ids)),
    )

    bybit_exec = BybitExecClientConfig(
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
    )

    config_node = TradingNodeConfig(
        trader_id=TraderId(TRADER_ID),
        logging=LoggingConfig(
            log_level="INFO",
            log_directory=str(LOG_DIR),
            log_file_format="{ts_init}_{trader_id}.log",
            log_colors=False,
        ),
        portfolio=PortfolioConfig(min_account_state_logging_interval_ms=1_000),
        data_clients={"BINANCE_SPOT": binance_data, "BYBIT": bybit_data},
        exec_clients={"BYBIT": bybit_exec},
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
        ),
        risk_engine=LiveRiskEngineConfig(bypass=False),
        timeout_connection=30.0,
        timeout_reconciliation=20.0,
        timeout_portfolio=20.0,
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

        config_strategy = LeadLagMMv5PrimerConfig(
            follower_instrument_id=follower_id,
            leader_instrument_id=leader_id,
            global_guard_id=guard_id,
            
            # Base quoting
            order_qty=pair["order_qty"],
            max_position_qty=pair["max_position_qty"],
            spread_bps=pair["spread_bps"],
            quote_refresh_interval_ms=int(pair["refresh_interval"]),
            quote_refresh_jitter_ms=10,
            quote_refresh_offset_ms=int(pair["refresh_offset"]),
            min_quote_lifetime_ms=int(pair["min_quote_lifetime_ms"]),
            min_requote_ticks=1,
            
            # Toxic flow avoidance - OFI
            ofi_enabled=bool(pair["ofi_enabled"]),
            ofi_widen_threshold=pair["ofi_widen_threshold"],
            ofi_widen_bps=pair["ofi_widen_bps"],
            ofi_tighten_bps=pair["ofi_tighten_bps"],
            
            # Toxic flow avoidance - Volatility
            volatility_enabled=bool(pair["volatility_enabled"]),
            volatility_base_threshold_bps=pair["volatility_base_threshold_bps"],
            volatility_max_widen_bps=pair["volatility_max_widen_bps"],
            
            # Dynamic lifetime
            dynamic_lifetime_enabled=bool(pair["dynamic_lifetime_enabled"]),
            lifetime_volatility_fast_ms=int(pair["lifetime_volatility_fast_ms"]),
            lifetime_volatility_slow_ms=int(pair["lifetime_volatility_slow_ms"]),
            
            # Event-driven requoting
            event_driven_requote=bool(pair["event_driven_requote"]),
            leader_update_min_interval_ms=int(pair["leader_update_min_interval_ms"]),
            
            # Queue & inventory
            queue_tracking_enabled=bool(pair["queue_tracking_enabled"]),
            inventory_urgency_enabled=bool(pair["inventory_urgency_enabled"]),
            
            # Spread intelligence
            binance_spread_tracking=bool(pair["binance_spread_tracking"]),
            competitive_monitoring=bool(pair["competitive_monitoring"]),
            
            # Guard
            guard_threshold_bps=pair["guard_threshold_bps"],
            global_guard_threshold_bps=Decimal("500.0"),
            global_guard_window_ms=500,
            
            # Book
            book_type=BookType.L2_MBP,
            book_depth=50,
            post_only=True,
            
            # Sizing
            min_order_qty=pair["min_order_qty"],
            max_order_qty=pair["order_qty"] * Decimal("3"),
            liquidity_high_qty=pair["liquidity_high_qty"],
            liquidity_low_qty=pair["liquidity_low_qty"],
            internal_price_delta_limit=pair["internal_price_delta_limit"],
            
            # Fees
            maker_fee_bps=pair["maker_fee_bps"],
            min_profit_bps=pair["min_profit_bps"],
            
            # Balance protection
            min_quote_reserve_ratio=Decimal("0.5"),
            min_quote_reserve_usdt=Decimal("500"),
            
            # Killswitches
            daily_loss_limit_usdt=Decimal("50"),
            max_drawdown_pct=Decimal("1.0"),  # 100% = disabled
            
            # Logging & analytics
            log_spread_adjustments=True,
            log_toxic_flow=True,
            fill_analytics_enabled=True,
        )

        node.trader.add_strategy(LeadLagMMv5Primer(config=config_strategy))

    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.add_data_client_factory("BINANCE_SPOT", BinanceLiveDataClientFactory)

    node.build()

    print(
        f"\n>>> DEPLOYING v005 TOXIC FLOW AVOIDANCE (testnet={BYBIT_TESTNET}) <<<\n"
        f"TraderId: {TRADER_ID}\n"
        f"Log dir: {LOG_DIR}\n"
        f"Pairs: {[p['symbol'] for p in PAIRS]}\n"
        f"\n"
        f"=== v005 FEATURES ENABLED ===\n"
        f"✅ Asymmetric OFI: widen {PAIRS[0]['ofi_widen_bps']}bps toxic, tighten {PAIRS[0]['ofi_tighten_bps']}bps favorable\n"
        f"✅ Volatility spread: +{PAIRS[0]['volatility_max_widen_bps']}bps max when vol > {PAIRS[0]['volatility_base_threshold_bps']}bps\n"
        f"✅ Event-driven requoting: {PAIRS[0]['leader_update_min_interval_ms']}ms min interval\n"
        f"✅ Dynamic lifetime: {PAIRS[0]['lifetime_volatility_fast_ms']}ms fast, {PAIRS[0]['lifetime_volatility_slow_ms']}ms slow\n"
        f"✅ Fill analytics: ENABLED\n"
    )

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
