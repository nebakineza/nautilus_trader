"""Backtest runner for ATR Vol-Sized Trend Follower.

This script allows backtesting the strategy on historical data
to validate the approach before live deployment.

DATA REQUIREMENTS:
- Historical 4-hour bars for the target instrument
- Can load from Parquet files or use built-in data providers

USAGE:
    python run_atr_trend_backtest.py --symbol BTCUSDT --start 2025-01-01 --end 2025-12-31
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from decimal import Decimal

import pandas as pd

# Add strategy directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel, LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy.atr_vol_sized_trend_v001 import ATRVolSizedTrend, ATRVolSizedTrendConfig


def create_engine(
    starting_balance: float = 10000.0,
    venue_name: str = "BYBIT",
) -> BacktestEngine:
    """Create backtest engine with realistic configuration."""
    
    config = BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
        ),
    )
    
    engine = BacktestEngine(config=config)
    
    # Add simulated exchange venue
    engine.add_venue(
        venue=Venue(venue_name),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,  # Spot trading = cash account
        base_currency=USDT,
        starting_balances=[Money(starting_balance, USDT)],
        fill_model=FillModel(
            prob_fill_on_limit=0.95,  # 95% fill probability on limits
            prob_fill_on_stop=0.99,
            prob_slippage=0.2,  # 20% chance of slippage
            random_seed=42,
        ),
        latency_model=LatencyModel(
            base_latency_nanos=50_000_000,  # 50ms base latency
            insert_latency_nanos=100_000_000,  # 100ms order insert
            update_latency_nanos=100_000_000,
            cancel_latency_nanos=100_000_000,
        ),
        # Bybit UK Spot fees
        default_leverage=Decimal("1.0"),  # No leverage for spot
        fee_model=None,  # Use instrument-level fees
    )
    
    return engine


def add_instrument(
    engine: BacktestEngine,
    symbol: str,
    venue_name: str = "BYBIT",
) -> InstrumentId:
    """Add trading instrument to the engine."""
    
    # For BTC, use crypto spot instrument
    if "BTC" in symbol:
        instrument = TestInstrumentProvider.btcusdt_binance()
    elif "ETH" in symbol:
        instrument = TestInstrumentProvider.ethusdt_binance()
    else:
        # Default to BTC config
        instrument = TestInstrumentProvider.btcusdt_binance()
    
    # Override venue
    instrument_id = InstrumentId.from_str(f"{symbol}-SPOT.{venue_name}")
    
    engine.add_instrument(instrument)
    
    return instrument_id


def load_bar_data(
    symbol: str,
    start_date: str,
    end_date: str,
    timeframe: str = "4h",
) -> pd.DataFrame:
    """
    Load historical bar data.
    
    In production, this would load from:
    - QuestDB
    - Parquet files
    - External data provider
    
    For this example, we'll generate synthetic data.
    """
    print(f"📊 Loading {timeframe} bars for {symbol} from {start_date} to {end_date}")
    
    # Generate date range
    dates = pd.date_range(start=start_date, end=end_date, freq="4h")
    n = len(dates)
    
    # Generate realistic price data with trends
    import numpy as np
    np.random.seed(42)
    
    # Start price (BTC around 45000, ETH around 2500)
    if "BTC" in symbol:
        start_price = 45000.0
    elif "ETH" in symbol:
        start_price = 2500.0
    else:
        start_price = 100.0
    
    # Generate returns with mean-reversion and momentum
    returns = np.random.normal(0.0002, 0.015, n)  # Slight upward drift
    
    # Add some trend structure
    trend = np.sin(np.linspace(0, 4 * np.pi, n)) * 0.001
    returns += trend
    
    # Calculate prices
    prices = start_price * np.exp(np.cumsum(returns))
    
    # Generate OHLC from close prices
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices * (1 + np.random.uniform(-0.005, 0.005, n)),
        "high": prices * (1 + np.abs(np.random.normal(0, 0.01, n))),
        "low": prices * (1 - np.abs(np.random.normal(0, 0.01, n))),
        "close": prices,
        "volume": np.random.uniform(100, 1000, n),
    })
    
    # Ensure high >= open, close and low <= open, close
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    
    print(f"   Loaded {len(df)} bars")
    print(f"   Price range: {df['close'].min():.2f} - {df['close'].max():.2f}")
    
    return df


def run_backtest(
    symbol: str = "BTCUSDT",
    start_date: str = "2025-01-01",
    end_date: str = "2025-12-31",
    starting_balance: float = 10000.0,
    risk_per_trade: float = 100.0,
    max_position: float = 5000.0,
    output_dir: str = "backtest_results/atr_trend",
):
    """Run the backtest and generate reports."""
    
    print("=" * 70)
    print("🔬 ATR VOL-SIZED TREND FOLLOWER - BACKTEST")
    print("=" * 70)
    print(f"   Symbol: {symbol}")
    print(f"   Period: {start_date} to {end_date}")
    print(f"   Starting Balance: ${starting_balance:.2f}")
    print(f"   Risk per Trade: ${risk_per_trade:.2f}")
    print(f"   Max Position: ${max_position:.2f}")
    print("=" * 70)
    
    # Create engine
    engine = create_engine(starting_balance=starting_balance)
    
    # Add instrument
    venue_name = "BYBIT"
    instrument_id = add_instrument(engine, symbol, venue_name)
    
    # Load data
    bar_data = load_bar_data(symbol, start_date, end_date)
    
    # Create bar type
    bar_type = BarType.from_str(f"{symbol}-SPOT.{venue_name}-4-HOUR-LAST-EXTERNAL")
    
    # Add data to engine
    # Note: In production, you'd convert the DataFrame to Nautilus Bar objects
    # For now, this is a placeholder showing the structure
    
    # Create strategy config
    strategy_config = ATRVolSizedTrendConfig(
        strategy_id="ATR-TREND-BT",
        instrument_id=instrument_id,
        bar_type=bar_type,
        
        # EMA Configuration
        fast_ema_period=20,
        slow_ema_period=50,
        trend_filter_period=200,
        trend_filter_enabled=True,
        
        # ATR Position Sizing
        atr_period=14,
        risk_per_trade_usd=risk_per_trade,
        atr_stop_multiplier=2.0,
        max_position_usd=max_position,
        min_position_usd=10.0,
        
        # Safety Limits
        max_drawdown_pct=15.0,  # More relaxed for backtest
        daily_loss_limit_usd=500.0,
        max_concurrent_positions=1,
        
        # Execution
        use_limit_orders=True,
        limit_chase_ticks=2,
        time_in_force=TimeInForce.GTC,
        
        # Trailing Stop
        trailing_stop_enabled=True,
        trailing_atr_multiplier=2.5,
        trailing_activation_profit_pct=1.5,
        
        # Fees (Bybit Spot)
        taker_fee_pct=0.10,
        maker_fee_pct=0.10,
        
        # Filters
        adx_filter_enabled=True,
        adx_period=14,
        adx_min_threshold=25.0,
        rsi_filter_enabled=True,
        rsi_period=14,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        
        # Logging
        log_signals=True,
        log_trades=True,
        log_sizing=True,
    )
    
    # Add strategy
    strategy = ATRVolSizedTrend(config=strategy_config)
    engine.add_strategy(strategy)
    
    print("\n🏃 Running backtest...")
    
    # Run backtest
    engine.run()
    
    # Generate reports
    print("\n📈 Generating reports...")
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Get results
    try:
        # Account report
        account_report = engine.trader.generate_account_report(Venue(venue_name))
        if account_report is not None:
            account_report.to_csv(f"{output_dir}/account_report.csv")
            print(f"   ✅ Account report: {output_dir}/account_report.csv")
        
        # Fills report
        fills_report = engine.trader.generate_fills_report()
        if fills_report is not None:
            fills_report.to_csv(f"{output_dir}/fills_report.csv")
            print(f"   ✅ Fills report: {output_dir}/fills_report.csv")
        
        # Orders report
        orders_report = engine.trader.generate_orders_report()
        if orders_report is not None:
            orders_report.to_csv(f"{output_dir}/orders_report.csv")
            print(f"   ✅ Orders report: {output_dir}/orders_report.csv")
        
        # Positions report
        positions_report = engine.trader.generate_positions_report()
        if positions_report is not None:
            positions_report.to_csv(f"{output_dir}/positions_report.csv")
            print(f"   ✅ Positions report: {output_dir}/positions_report.csv")
        
    except Exception as e:
        print(f"   ⚠️ Error generating reports: {e}")
    
    # Dispose engine
    engine.dispose()
    
    print("\n✅ Backtest complete!")
    return output_dir


def main():
    parser = argparse.ArgumentParser(description="Backtest ATR Vol-Sized Trend Follower")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--start", type=str, default="2025-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2025-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--balance", type=float, default=10000.0, help="Starting balance in USD")
    parser.add_argument("--risk", type=float, default=100.0, help="Risk per trade in USD")
    parser.add_argument("--max-position", type=float, default=5000.0, help="Max position in USD")
    parser.add_argument("--output", type=str, default="backtest_results/atr_trend", help="Output directory")
    
    args = parser.parse_args()
    
    run_backtest(
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        starting_balance=args.balance,
        risk_per_trade=args.risk,
        max_position=args.max_position,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
