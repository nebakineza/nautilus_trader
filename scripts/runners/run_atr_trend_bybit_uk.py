"""Run ATR Vol-Sized Trend Follower on Bybit UK Spot.

DEPLOYMENT NOTES:
- This strategy is designed for Bybit UK subaccount trading
- Uses 4-hour bars for lower trade frequency (lower fees)
- ATR-based position sizing keeps risk per trade constant
- Compliant with UK FCA Cryptoasset Reporting Framework (CARF)

ENVIRONMENT VARIABLES REQUIRED:
- BYBIT_UK_API_KEY: Bybit UK subaccount API key
- BYBIT_UK_API_SECRET: Bybit UK subaccount API secret

FEES (Bybit UK Spot VIP0):
- Taker: 0.10%
- Maker: 0.10%
- Roundtrip: 0.20%
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

# Add strategy directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId

from strategy.atr_vol_sized_trend_v001 import ATRVolSizedTrend, ATRVolSizedTrendConfig


# =============================================================================
# CONFIGURATION
# =============================================================================

# Trading pair - Start with BTC for liquidity and lower slippage
SYMBOL = "BTCUSDT"
INSTRUMENT_ID = f"{SYMBOL}-SPOT.BYBIT"
STRATEGY_ID = "ATR-TREND-UK-001"

# Bar type: 4-hour bars for trend following (lower frequency = lower fees)
# Format: {symbol}.{venue}-{step}-{aggregation}-{price_type}-{source}
BAR_TYPE = f"{INSTRUMENT_ID}-4-HOUR-LAST-EXTERNAL"

# API Keys (from environment)
# For subaccount, you may need to prefix with subaccount name
BYBIT_API_KEY = os.environ.get("BYBIT_UK_API_KEY", os.environ.get("BYBIT_API_KEY", ""))
BYBIT_API_SECRET = os.environ.get("BYBIT_UK_API_SECRET", os.environ.get("BYBIT_API_SECRET", ""))

# Validate API keys
if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    print("ERROR: BYBIT_UK_API_KEY and BYBIT_UK_API_SECRET must be set")
    print("       Or use BYBIT_API_KEY and BYBIT_API_SECRET as fallback")
    sys.exit(1)


def get_strategy_config(
    symbol: str = SYMBOL,
    risk_per_trade: float = 100.0,
    max_position: float = 5000.0,
    max_drawdown_pct: float = 10.0,
    daily_loss_limit: float = 300.0,
) -> ATRVolSizedTrendConfig:
    """
    Create strategy configuration with customizable parameters.
    
    Parameters
    ----------
    symbol : str
        Trading symbol (e.g., BTCUSDT, ETHUSDT)
    risk_per_trade : float
        Maximum USD at risk per trade (default $100)
    max_position : float
        Maximum position value in USD (default $5000)
    max_drawdown_pct : float
        Maximum drawdown before killswitch (default 10%)
    daily_loss_limit : float
        Maximum daily loss before pausing (default $300)
    
    Returns
    -------
    ATRVolSizedTrendConfig
        Configured strategy config
    """
    instrument_id = InstrumentId.from_str(f"{symbol}-SPOT.BYBIT")
    bar_type = BarType.from_str(f"{symbol}-SPOT.BYBIT-4-HOUR-LAST-EXTERNAL")
    
    return ATRVolSizedTrendConfig(
        strategy_id=f"ATR-TREND-{symbol}",
        instrument_id=instrument_id,
        bar_type=bar_type,
        
        # === EMA Configuration (20/50 crossover with 200 trend filter) ===
        fast_ema_period=20,
        slow_ema_period=50,
        trend_filter_period=200,
        trend_filter_enabled=True,
        
        # === ATR Position Sizing (CORE SAFETY FEATURE) ===
        atr_period=14,
        risk_per_trade_usd=risk_per_trade,
        atr_stop_multiplier=2.0,  # 2x ATR stop = reasonable volatility buffer
        max_position_usd=max_position,
        min_position_usd=10.0,  # Bybit minimum
        
        # === Safety Limits (CRITICAL FOR UK COMPLIANCE) ===
        max_drawdown_pct=max_drawdown_pct,
        daily_loss_limit_usd=daily_loss_limit,
        max_concurrent_positions=1,
        
        # === Execution (Use limits for lower fees) ===
        use_limit_orders=True,
        limit_chase_ticks=3,  # Chase 3 ticks for better fills
        time_in_force=TimeInForce.GTC,
        
        # === Trailing Stop (Lock in profits) ===
        trailing_stop_enabled=True,
        trailing_atr_multiplier=2.5,
        trailing_activation_profit_pct=1.5,  # Activate after 1.5% profit
        
        # === Fees (Bybit UK Spot VIP0 - worst case) ===
        taker_fee_pct=0.10,
        maker_fee_pct=0.10,
        
        # === ADX Filter (Only trade strong trends) ===
        adx_filter_enabled=True,
        adx_period=14,
        adx_min_threshold=25.0,  # ADX > 25 = strong trend
        
        # === RSI Filter (Avoid overbought/oversold) ===
        rsi_filter_enabled=True,
        rsi_period=14,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        
        # === Logging (Enable all for monitoring) ===
        log_signals=True,
        log_trades=True,
        log_sizing=True,
    )


def create_trading_node(
    testnet: bool = False,
    reconciliation: bool = True,
) -> TradingNode:
    """
    Create and configure the trading node.
    
    Parameters
    ----------
    testnet : bool
        Use Bybit testnet (default False for live)
    reconciliation : bool
        Enable order reconciliation on startup
    
    Returns
    -------
    TradingNode
        Configured trading node
    """
    config = TradingNodeConfig(
        trader_id=TraderId(STRATEGY_ID),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
            log_colors=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=reconciliation,
            reconciliation_lookback_mins=1440,  # 24 hours
        ),
        data_clients={
            BYBIT: BybitDataClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset([InstrumentId.from_str(INSTRUMENT_ID)]),
                ),
                product_types=(BybitProductType.SPOT,),
                testnet=testnet,
            ),
        },
        exec_clients={
            BYBIT: BybitExecClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                instrument_provider=InstrumentProviderConfig(
                    load_all=False,
                    load_ids=frozenset([InstrumentId.from_str(INSTRUMENT_ID)]),
                ),
                product_types=(BybitProductType.SPOT,),
                testnet=testnet,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=15.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )
    
    return TradingNode(config=config)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="ATR Vol-Sized Trend Follower for Bybit UK")
    parser.add_argument("--symbol", type=str, default=SYMBOL, help="Trading symbol")
    parser.add_argument("--risk", type=float, default=100.0, help="Risk per trade in USD")
    parser.add_argument("--max-position", type=float, default=5000.0, help="Max position in USD")
    parser.add_argument("--max-drawdown", type=float, default=10.0, help="Max drawdown percentage")
    parser.add_argument("--daily-limit", type=float, default=300.0, help="Daily loss limit in USD")
    parser.add_argument("--testnet", action="store_true", help="Use Bybit testnet")
    parser.add_argument("--no-reconciliation", action="store_true", help="Disable reconciliation")
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("🚀 ATR VOL-SIZED TREND FOLLOWER - BYBIT UK")
    print("=" * 70)
    print(f"   Symbol: {args.symbol}")
    print(f"   Timeframe: 4-HOUR bars")
    print(f"   Risk per Trade: ${args.risk:.2f}")
    print(f"   Max Position: ${args.max_position:.2f}")
    print(f"   Max Drawdown: {args.max_drawdown}%")
    print(f"   Daily Limit: ${args.daily_limit:.2f}")
    print(f"   Testnet: {args.testnet}")
    print("=" * 70)
    
    # Create strategy config
    strategy_config = get_strategy_config(
        symbol=args.symbol,
        risk_per_trade=args.risk,
        max_position=args.max_position,
        max_drawdown_pct=args.max_drawdown,
        daily_loss_limit=args.daily_limit,
    )
    
    # Create trading node
    node = create_trading_node(
        testnet=args.testnet,
        reconciliation=not args.no_reconciliation,
    )
    
    # Add strategy
    strategy = ATRVolSizedTrend(config=strategy_config)
    node.trader.add_strategy(strategy)
    
    # Register Bybit factories
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
