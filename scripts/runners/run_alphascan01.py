#!/usr/bin/env python3
"""Run AlphaScan01 Strategy on Bybit Subaccount.

DEPLOYMENT: sentinel-vps
SUBACCOUNT: AlphaScan01 (AS01)
API KEYS: BYBIT_API_KEY_AS01_00 / BYBIT_API_SECRET_AS01_00

STRATEGY: SafeAlpha Scanner v002
- Multi-asset trend scanner with correlation filtering
- Scans 10 coins, picks top 3 uncorrelated trending assets
- ATR-based position sizing with constant risk per trade
- 4-hour timeframe for lower fees

SAFETY FEATURES:
- Max 3 concurrent positions
- Max 10% portfolio heat
- Correlation threshold: 0.70
- ATR stop: 2.5x
- Max drawdown: 15%
- Daily loss limit: $300
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

# Add strategy package to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId

from atr_vol_sized_trend_scanner_v002 import SafeAlphaScanner, SafeAlphaScannerConfig


# =============================================================================
# CONFIGURATION
# =============================================================================

STRATEGY_ID = "ALPHASCAN-01"

# Universe: Top 5 liquid Bybit Spot pairs (reduced to avoid rate limits)
# Note: Bybit API has rate limits; start with 5, expand later if stable
UNIVERSE_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "LINKUSDT",
]

# API Keys - AlphaScan01 Subaccount
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_AS01_00"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_AS01_00"]

# Risk Parameters (Conservative for initial deployment)
RISK_PER_TRADE_USD = 50.0      # $50 risk per trade
MAX_POSITION_USD = 2000.0      # $2000 max per position
MAX_POSITIONS = 3              # Max 3 concurrent positions
MAX_PORTFOLIO_HEAT = 10.0      # Max 10% portfolio at risk
CORRELATION_THRESHOLD = 0.70   # Reject if r > 0.70
MAX_DRAWDOWN_PCT = 15.0        # Killswitch at 15% drawdown
DAILY_LOSS_LIMIT_USD = 300.0   # Stop trading after $300 daily loss


def get_instrument_ids() -> tuple[InstrumentId, ...]:
    """Convert symbol strings to InstrumentId objects."""
    return tuple(
        InstrumentId.from_str(f"{symbol}-SPOT.BYBIT")
        for symbol in UNIVERSE_SYMBOLS
    )


def main():
    print("=" * 70)
    print(f"🚀 ALPHASCAN-01 - SafeAlpha Scanner v002")
    print(f"   Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    print(f"   Universe: {len(UNIVERSE_SYMBOLS)} instruments")
    print(f"   Symbols: {', '.join(UNIVERSE_SYMBOLS)}")
    print(f"   Max Positions: {MAX_POSITIONS}")
    print(f"   Risk per Trade: ${RISK_PER_TRADE_USD:.2f}")
    print(f"   Max Position: ${MAX_POSITION_USD:.2f}")
    print(f"   Portfolio Heat: {MAX_PORTFOLIO_HEAT}%")
    print(f"   Correlation: {CORRELATION_THRESHOLD}")
    print(f"   Max Drawdown: {MAX_DRAWDOWN_PCT}%")
    print(f"   Daily Limit: ${DAILY_LOSS_LIMIT_USD:.2f}")
    print("=" * 70)
    
    instrument_ids = get_instrument_ids()
    
    # Trading Node Config
    node_config = TradingNodeConfig(
        trader_id=TraderId(STRATEGY_ID),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
            log_colors=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,  # 24 hours
        ),
        data_clients={
            BYBIT: BybitDataClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset(instrument_ids),
                ),
                product_types=(BybitProductType.SPOT,),
                testnet=False,
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset(instrument_ids),
                ),
                product_types=(BybitProductType.SPOT,),
                testnet=False,
            ),
        },
        timeout_connection=60.0,
        timeout_reconciliation=30.0,
        timeout_portfolio=15.0,
        timeout_disconnection=15.0,
        timeout_post_stop=10.0,
    )
    
    # Strategy Config
    strategy_config = SafeAlphaScannerConfig(
        strategy_id=STRATEGY_ID,
        instrument_ids=instrument_ids,
        bar_type_template="{symbol}-4-HOUR-LAST-EXTERNAL",
        
        # === PORTFOLIO ===
        max_positions=MAX_POSITIONS,
        max_portfolio_heat_pct=MAX_PORTFOLIO_HEAT,
        risk_per_trade_usd=RISK_PER_TRADE_USD,
        max_position_usd=MAX_POSITION_USD,
        min_position_usd=10.0,
        
        # === CORRELATION FILTER ===
        correlation_enabled=True,
        correlation_threshold=CORRELATION_THRESHOLD,
        correlation_lookback=30,  # 30 x 4H = 5 days
        
        # === TREND DETECTION ===
        fast_ema_period=20,
        slow_ema_period=50,
        trend_filter_period=50,  # Reduced from 200 for faster warmup (8-9 days)
        trend_filter_enabled=True,
        
        # === QUALITY FILTERS ===
        adx_filter_enabled=True,
        adx_period=14,
        adx_min_threshold=25.0,
        rsi_filter_enabled=True,
        rsi_period=14,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        
        # === ATR & STOPS ===
        atr_period=14,
        atr_stop_multiplier=2.5,
        trailing_stop_enabled=True,
        trailing_atr_multiplier=2.5,
        trailing_activation_profit_pct=1.5,
        
        # === SAFETY ===
        max_drawdown_pct=MAX_DRAWDOWN_PCT,
        daily_loss_limit_usd=DAILY_LOSS_LIMIT_USD,
        
        # === EXECUTION ===
        use_limit_orders=True,
        limit_chase_ticks=3,
        time_in_force=TimeInForce.GTC,
        
        # === FEES (Bybit Spot VIP0) ===
        taker_fee_pct=0.10,
        maker_fee_pct=0.10,
        
        # === LOGGING ===
        log_scans=True,
        log_correlations=True,
        log_trades=True,
        log_sizing=True,
    )
    
    # Build node
    node = TradingNode(config=node_config)
    
    # Add strategy
    strategy = SafeAlphaScanner(config=strategy_config)
    node.trader.add_strategy(strategy)
    
    # Register factories
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    
    # Build and run
    node.build()
    
    try:
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️ Shutting down...")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
