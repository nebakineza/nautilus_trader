"""Run Safe Alpha Scanner v002 on Bybit UK Spot.

STRATEGY OVERVIEW:
Multi-asset trend scanner with correlation filtering.
Scans 20+ coins, picks the top 3 uncorrelated trending assets.

FEATURES:
- Dynamic Universe Selection: Ranks all coins by trend strength
- Correlation Filter: Ensures positions are truly diversified (r < 0.70)
- ATR-based sizing: Constant risk per trade regardless of volatility
- Portfolio rotation: Exits weak trends, enters strong ones

UNIVERSE (Top 20 liquid Bybit Spot pairs):
- BTC, ETH, SOL, BNB, XRP
- ADA, DOGE, AVAX, LINK, DOT
- MATIC, SHIB, UNI, LTC, ATOM
- NEAR, ARB, OP, SUI, APT

SAFETY:
- Max 3 concurrent positions
- Max 10% portfolio heat
- Correlation threshold: 0.70
- ATR stop: 2.5x
- Max drawdown: 15%

ENVIRONMENT:
    export BYBIT_UK_API_KEY="your-key"
    export BYBIT_UK_API_SECRET="your-secret"

USAGE:
    python run_safe_alpha_scanner_v002.py --max-positions 3 --risk 100
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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

from strategy.atr_vol_sized_trend_scanner_v002 import SafeAlphaScanner, SafeAlphaScannerConfig


# =============================================================================
# UNIVERSE DEFINITION
# =============================================================================

# Top 20 liquid Bybit Spot pairs (sorted roughly by liquidity)
UNIVERSE_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "MATICUSDT",
    "SHIBUSDT",
    "UNIUSDT",
    "LTCUSDT",
    "ATOMUSDT",
    "NEARUSDT",
    "ARBUSDT",
    "OPUSDT",
    "SUIUSDT",
    "APTUSDT",
]

# Smaller universe for testing
UNIVERSE_SMALL = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "NEARUSDT",
    "ARBUSDT",
    "SUIUSDT",
    "APTUSDT",
]

STRATEGY_ID = "SAFE-ALPHA-v002"


# =============================================================================
# CONFIGURATION
# =============================================================================

def get_instrument_ids(symbols: list[str], venue: str = "BYBIT") -> tuple[InstrumentId, ...]:
    """Convert symbol strings to InstrumentId objects."""
    return tuple(
        InstrumentId.from_str(f"{symbol}-SPOT.{venue}")
        for symbol in symbols
    )


def get_strategy_config(
    symbols: list[str],
    max_positions: int = 3,
    risk_per_trade: float = 100.0,
    max_position: float = 3000.0,
    correlation_threshold: float = 0.70,
    max_drawdown_pct: float = 15.0,
    daily_loss_limit: float = 500.0,
) -> SafeAlphaScannerConfig:
    """Create strategy configuration."""
    
    instrument_ids = get_instrument_ids(symbols)
    
    return SafeAlphaScannerConfig(
        strategy_id=STRATEGY_ID,
        instrument_ids=instrument_ids,
        bar_type_template="{symbol}-4-HOUR-LAST-EXTERNAL",
        
        # === PORTFOLIO ===
        max_positions=max_positions,
        max_portfolio_heat_pct=10.0,  # Max 10% at risk
        risk_per_trade_usd=risk_per_trade,
        max_position_usd=max_position,
        min_position_usd=10.0,
        
        # === CORRELATION FILTER (KEY SAFETY) ===
        correlation_enabled=True,
        correlation_threshold=correlation_threshold,
        correlation_lookback=30,  # 30 x 4H = 5 days
        
        # === TREND DETECTION ===
        fast_ema_period=20,
        slow_ema_period=50,
        trend_filter_period=200,
        trend_filter_enabled=True,
        
        # === QUALITY FILTERS ===
        adx_filter_enabled=True,
        adx_period=14,
        adx_min_threshold=25.0,  # Only strong trends
        rsi_filter_enabled=True,
        rsi_period=14,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        
        # === ATR & STOPS ===
        atr_period=14,
        atr_stop_multiplier=2.5,  # Wide stop for trend following
        trailing_stop_enabled=True,
        trailing_atr_multiplier=2.5,
        trailing_activation_profit_pct=1.5,
        
        # === SAFETY ===
        max_drawdown_pct=max_drawdown_pct,
        daily_loss_limit_usd=daily_loss_limit,
        
        # === EXECUTION ===
        use_limit_orders=True,
        limit_chase_ticks=3,
        time_in_force=TimeInForce.GTC,
        
        # === FEES ===
        taker_fee_pct=0.10,
        maker_fee_pct=0.10,
        
        # === LOGGING ===
        log_scans=True,
        log_correlations=True,
        log_trades=True,
        log_sizing=True,
    )


def create_trading_node(
    symbols: list[str],
    testnet: bool = False,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> TradingNode:
    """Create and configure the trading node."""
    
    # Get API credentials
    if api_key is None:
        api_key = os.environ.get("BYBIT_UK_API_KEY", os.environ.get("BYBIT_API_KEY", ""))
    if api_secret is None:
        api_secret = os.environ.get("BYBIT_UK_API_SECRET", os.environ.get("BYBIT_API_SECRET", ""))
    
    if not api_key or not api_secret:
        print("ERROR: BYBIT_UK_API_KEY and BYBIT_UK_API_SECRET must be set")
        sys.exit(1)
    
    # Get instrument IDs for subscription
    instrument_ids = get_instrument_ids(symbols)
    
    config = TradingNodeConfig(
        trader_id=TraderId(STRATEGY_ID),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
            log_colors=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
        ),
        data_clients={
            BYBIT: BybitDataClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset(instrument_ids),
                ),
                product_types=(BybitProductType.SPOT,),
                testnet=testnet,
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset(instrument_ids),
                ),
                product_types=(BybitProductType.SPOT,),
                testnet=testnet,
            ),
        },
        timeout_connection=60.0,  # Longer timeout for many instruments
        timeout_reconciliation=30.0,
        timeout_portfolio=15.0,
        timeout_disconnection=15.0,
        timeout_post_stop=10.0,
    )
    
    return TradingNode(config=config)


def main():
    parser = argparse.ArgumentParser(description="Safe Alpha Scanner v002 - Multi-Asset Trend Strategy")
    
    # Universe selection
    parser.add_argument("--universe", type=str, choices=["full", "small", "custom"], default="small",
                        help="Universe size (full=20, small=10, custom=specify)")
    parser.add_argument("--symbols", type=str, nargs="+", default=None,
                        help="Custom symbols (e.g., BTCUSDT ETHUSDT SOLUSDT)")
    
    # Portfolio parameters
    parser.add_argument("--max-positions", type=int, default=3,
                        help="Maximum concurrent positions")
    parser.add_argument("--risk", type=float, default=100.0,
                        help="Risk per trade in USD")
    parser.add_argument("--max-position", type=float, default=3000.0,
                        help="Max position size in USD")
    
    # Safety parameters
    parser.add_argument("--correlation", type=float, default=0.70,
                        help="Max correlation threshold (0.0-1.0)")
    parser.add_argument("--max-drawdown", type=float, default=15.0,
                        help="Max drawdown percentage")
    parser.add_argument("--daily-limit", type=float, default=500.0,
                        help="Daily loss limit in USD")
    
    # Environment
    parser.add_argument("--testnet", action="store_true",
                        help="Use Bybit testnet")
    
    args = parser.parse_args()
    
    # Select universe
    if args.symbols:
        symbols = args.symbols
    elif args.universe == "full":
        symbols = UNIVERSE_SYMBOLS
    else:
        symbols = UNIVERSE_SMALL
    
    print("=" * 80)
    print("🚀 SAFE ALPHA SCANNER v002 - MULTI-ASSET TREND STRATEGY")
    print("=" * 80)
    print(f"   Universe: {len(symbols)} instruments")
    print(f"   Symbols: {', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''}")
    print(f"   Max Positions: {args.max_positions}")
    print(f"   Risk per Trade: ${args.risk:.2f}")
    print(f"   Max Position: ${args.max_position:.2f}")
    print(f"   Correlation Threshold: {args.correlation}")
    print(f"   Max Drawdown: {args.max_drawdown}%")
    print(f"   Daily Limit: ${args.daily_limit:.2f}")
    print(f"   Testnet: {args.testnet}")
    print("=" * 80)
    
    # Create strategy
    strategy_config = get_strategy_config(
        symbols=symbols,
        max_positions=args.max_positions,
        risk_per_trade=args.risk,
        max_position=args.max_position,
        correlation_threshold=args.correlation,
        max_drawdown_pct=args.max_drawdown,
        daily_loss_limit=args.daily_limit,
    )
    
    # Create node
    node = create_trading_node(
        symbols=symbols,
        testnet=args.testnet,
    )
    
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
