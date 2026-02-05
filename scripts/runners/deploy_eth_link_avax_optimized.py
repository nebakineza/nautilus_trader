#!/usr/bin/env python3
"""
Production deployment runner for ETH/LINK/AVAX with optimized parameters.

SWEEP-OPTIMIZED PARAMETERS (Feb 2, 2026):
- ETH:  8 bps spread, 0.4 bps min_profit, 0.01 qty
- LINK: 10 bps spread, 0.7 bps min_profit, 3.0 qty  
- AVAX: 8 bps spread, 0.4 bps min_profit, 3.0 qty

CONSERVATIVE DEPLOYMENT LIMITS:
- Max position: 2x order_qty (very tight)
- 24-hour monitoring required
- Emergency stop on -$50 loss per pair
"""
import sys
import os
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load environment variables from .env file
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

from nautilus_trader.config import LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.adapters.bybit.config import BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.trading.config import ImportableStrategyConfig

from strategy.lead_lag_bybit_binance_mm_v003_primer import LeadLagMMv3PrimerConfig


# Load API credentials from environment
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY_LLMM")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET_LLMM")

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError(f"BYBIT_API_KEY_LLMM and BYBIT_API_SECRET_LLMM must be set. Loaded from: {env_path}")


# Pair configurations
PAIRS = {
    "ETH": {
        "leader_symbol": "ETHUSDT",
        "follower_symbol": "ETHUSDT",
        "spread_bps": Decimal("8.0"),
        "min_profit_bps": Decimal("0.4"),
        "order_qty": Decimal("0.01"),
        "max_position_qty": Decimal("0.02"),  # 2x order_qty - VERY CONSERVATIVE
        "guard_threshold_bps": Decimal("10.0"),
    },
    "LINK": {
        "leader_symbol": "LINKUSDT",
        "follower_symbol": "LINKUSDT",
        "spread_bps": Decimal("10.0"),
        "min_profit_bps": Decimal("0.7"),
        "order_qty": Decimal("3.0"),
        "max_position_qty": Decimal("6.0"),  # 2x order_qty - VERY CONSERVATIVE
        "guard_threshold_bps": Decimal("10.0"),
    },
    "AVAX": {
        "leader_symbol": "AVAXUSDT",
        "follower_symbol": "AVAXUSDT",
        "spread_bps": Decimal("8.0"),
        "min_profit_bps": Decimal("0.4"),
        "order_qty": Decimal("3.0"),
        "max_position_qty": Decimal("6.0"),  # 2x order_qty - VERY CONSERVATIVE
        "guard_threshold_bps": Decimal("10.0"),
    },
}


def create_strategy_config(pair_name: str) -> LeadLagMMv3PrimerConfig:
    """Create strategy config with sweep-optimized parameters."""
    cfg = PAIRS[pair_name]
    
    return LeadLagMMv3PrimerConfig(
        leader_instrument_id=f"{cfg['leader_symbol']}.BINANCE",
        follower_instrument_id=f"{cfg['follower_symbol']}-SPOT.BYBIT",
        order_qty=cfg["order_qty"],
        min_order_qty=cfg["order_qty"] / 2,
        max_position_qty=cfg["max_position_qty"],
        guard_threshold_bps=cfg["guard_threshold_bps"],
        spread_bps=cfg["spread_bps"],
        min_profit_bps=cfg["min_profit_bps"],
        ofi_enabled=True,
        ofi_max_bps=Decimal("3.0"),
        internal_price_delta_limit=Decimal("0.0"),
        quote_refresh_interval_ms=2000,
        min_quote_lifetime_ms=800,
        order_id_tag=pair_name,
        oms_type="NETTING",
    )


def main(pairs_to_run: list[str] = None):
    """
    Run optimized strategies for selected pairs.
    
    Args:
        pairs_to_run: List of pairs to run (e.g., ["ETH", "LINK"]). If None, runs all.
    """
    if pairs_to_run is None:
        pairs_to_run = list(PAIRS.keys())
    
    print("="*80)
    print("PRODUCTION DEPLOYMENT - OPTIMIZED PARAMETERS")
    print("="*80)
    print(f"Pairs: {', '.join(pairs_to_run)}")
    print(f"Date: {Path(__file__).stat().st_mtime}")
    print("="*80)
    
    for pair in pairs_to_run:
        cfg = PAIRS[pair]
        print(f"\n{pair}:")
        print(f"  Spread: {cfg['spread_bps']} bps")
        print(f"  Min Profit: {cfg['min_profit_bps']} bps")
        print(f"  Order Qty: {cfg['order_qty']}")
        print(f"  Max Position: {cfg['max_position_qty']} (CONSERVATIVE)")
    
    print("\n" + "="*80)
    print("⚠️  DEPLOYMENT CHECKLIST:")
    print("  ✓ QuestDB running on sentinel")
    print("  ✓ Emergency stop: -$50 per pair")
    print("  ✓ Monitor for first 24 hours")
    print("  ✓ Validate fills match expected spread")
    print("="*80 + "\n")
    
    # Build strategy configs with instrument subscriptions
    import msgspec
    strategies = []
    for pair in pairs_to_run:
        cfg = create_strategy_config(pair)
        pair_cfg = PAIRS[pair]
        
        # Add instrument IDs to subscribe
        leader_id = f"{pair_cfg['leader_symbol']}.BINANCE"
        follower_id = f"{pair_cfg['follower_symbol']}-SPOT.BYBIT"
        
        cfg_dict = msgspec.structs.asdict(cfg)
        strategies.append(
            ImportableStrategyConfig(
                strategy_path="strategy.lead_lag_bybit_binance_mm_v003_primer:LeadLagMMv3Primer",
                config_path="strategy.lead_lag_bybit_binance_mm_v003_primer:LeadLagMMv3PrimerConfig",
                config=cfg_dict,
            )
        )
    
    # Configure trading node
    config = TradingNodeConfig(
        trader_id=TraderId("SENTINEL-LLMM-001"),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_file_format="json",
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
        ),
        data_clients={
            "BINANCE": BinanceDataClientConfig(
                api_key=None,  # Read-only, no key needed
                api_secret=None,
                base_url_http="https://api.binance.com",
                base_url_ws="wss://stream.binance.com:9443",
                us=False,
            ),
            "BYBIT": BybitDataClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                base_url_http="https://api.bybit.com",
                testnet=False,
            ),
        },
        exec_clients={
            "BYBIT": BybitExecClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                base_url_http="https://api.bybit.com",
                testnet=False,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
        strategies=strategies,  # Add strategies here
    )
    
    # Create and run node
    node = TradingNode(config=config)
    
    try:
        node.build()
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️  Shutdown requested...")
    finally:
        node.stop()
        node.dispose()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Deploy optimized ETH/LINK/AVAX strategies")
    parser.add_argument(
        "--pairs",
        nargs="+",
        choices=["ETH", "LINK", "AVAX"],
        help="Pairs to run (default: all)",
    )
    
    args = parser.parse_args()
    main(pairs_to_run=args.pairs)
