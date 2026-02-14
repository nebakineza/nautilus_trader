#!/usr/bin/env python3
"""Run Confluence Scanner v003 on AlphaScan01 Subaccount.

DEPLOYMENT: sentinel-vps
SUBACCOUNT: AlphaScan01 (AS01)
API KEYS: BYBIT_API_KEY_AS01_00 / BYBIT_API_SECRET_AS01_00

STRATEGY: Confluence Scanner v003 (replaces SafeAlpha v002)
- 4-indicator confluence: RSI + KDJ + BB + MACD
- Entry requires 3-of-4 indicators agreeing
- H1 timeframe (4x more signals vs v002's 4H)
- ATR-sized positions, correlation filter, trailing stops

INDICATORS:
- RSI(14):        Oversold recovery (cross above 30)
- KDJ(14,3,3):   Golden cross in oversold zone
- BB(20,2σ):     Lower band touch
- MACD(12,26,9): Histogram turns positive

SAFETY:
- Max 3 concurrent positions
- Correlation filter: r > 0.70 rejected
- ATR stop: 2.5x, trailing after 1.5% profit
- Max drawdown: 15%, daily loss limit: $300
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

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

from atr_vol_sized_trend_scanner_v003 import ConfluenceScanner, ConfluenceScannerConfig


# =============================================================================
# CONFIGURATION
# =============================================================================

STRATEGY_ID = "CONFLUENCE-AS01-V003"

# Universe: 5 liquid Bybit Spot pairs (same as v002, expand once stable)
UNIVERSE_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "LINKUSDT",
]

# API Keys
BYBIT_API_KEY = os.environ["BYBIT_API_KEY_AS01_00"]
BYBIT_API_SECRET = os.environ["BYBIT_API_SECRET_AS01_00"]

# Risk (same conservative profile as v002)
RISK_PER_TRADE_USD = 50.0
MAX_POSITION_USD = 2000.0
MAX_POSITIONS = 3
MAX_PORTFOLIO_HEAT = 10.0
CORRELATION_THRESHOLD = 0.70
MAX_DRAWDOWN_PCT = 15.0
DAILY_LOSS_LIMIT_USD = 300.0


def get_instrument_ids() -> tuple[InstrumentId, ...]:
    return tuple(
        InstrumentId.from_str(f"{symbol}-SPOT.BYBIT")
        for symbol in UNIVERSE_SYMBOLS
    )


def main():
    print("=" * 70)
    print(f"🚀 CONFLUENCE SCANNER v003 - AlphaScan01")
    print(f"   Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    print(f"   Universe: {len(UNIVERSE_SYMBOLS)} instruments")
    print(f"   Symbols: {', '.join(UNIVERSE_SYMBOLS)}")
    print(f"   Timeframe: H1 (1-hour bars)")
    print(f"   Min Confluence: 3/4 indicators")
    print(f"   Max Positions: {MAX_POSITIONS}")
    print(f"   Risk per Trade: ${RISK_PER_TRADE_USD:.2f}")
    print(f"   Correlation: {CORRELATION_THRESHOLD}")
    print("=" * 70)

    instrument_ids = get_instrument_ids()

    node_config = TradingNodeConfig(
        trader_id=TraderId(STRATEGY_ID),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
            log_colors=False,
            log_directory="/home/ubuntu/trading/logs",
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
        ),
        data_clients={
            BYBIT: BybitDataClientConfig(
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
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
                    load_all=True,
                ),
                product_types=(BybitProductType.SPOT,),
                testnet=False,
                max_retries=5,
                retry_delay_initial_ms=500,
                retry_delay_max_ms=5000,
            ),
        },
        timeout_connection=60.0,
        timeout_reconciliation=30.0,
        timeout_portfolio=15.0,
        timeout_disconnection=15.0,
        timeout_post_stop=10.0,
    )

    strategy_config = ConfluenceScannerConfig(
        strategy_id=STRATEGY_ID,
        instrument_ids=instrument_ids,
        bar_type_template="{symbol}-1-HOUR-LAST-EXTERNAL",

        # === PORTFOLIO ===
        max_positions=MAX_POSITIONS,
        max_portfolio_heat_pct=MAX_PORTFOLIO_HEAT,
        risk_per_trade_usd=RISK_PER_TRADE_USD,
        max_position_usd=MAX_POSITION_USD,
        min_position_usd=10.0,

        # === CORRELATION ===
        correlation_enabled=True,
        correlation_threshold=CORRELATION_THRESHOLD,
        correlation_lookback=30,     # 30 x 1H = 30 hours

        # === CONFLUENCE (3-of-4 required) ===
        confluence_min_score=3,

        # RSI
        rsi_enabled=True,
        rsi_period=14,
        rsi_oversold=30.0,
        rsi_overbought=70.0,

        # KDJ / Stochastics
        kdj_enabled=True,
        kdj_period_k=14,
        kdj_period_d=3,
        kdj_slowing=3,
        kdj_oversold=30.0,
        kdj_overbought=70.0,

        # Bollinger Bands
        bb_enabled=True,
        bb_period=20,
        bb_std_dev=2.0,
        bb_touch_pct=0.5,

        # MACD
        macd_enabled=True,
        macd_fast=12,
        macd_slow=26,
        macd_signal_period=9,

        # === TREND FILTER ===
        trend_filter_enabled=True,
        trend_filter_period=50,      # 50 x 1H = ~2 days

        # === ATR & STOPS ===
        atr_period=14,
        atr_stop_multiplier=2.5,
        trailing_stop_enabled=True,
        trailing_atr_multiplier=2.5,
        trailing_activation_profit_pct=1.5,

        # === SAFETY ===
        max_drawdown_pct=MAX_DRAWDOWN_PCT,
        daily_loss_limit_usd=DAILY_LOSS_LIMIT_USD,

        # === DCA (disabled for initial deployment) ===
        dca_enabled=False,

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
        log_confluence=True,
    )

    node = TradingNode(config=node_config)
    strategy = ConfluenceScanner(config=strategy_config)
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    node.build()

    print(f">>> Node built. Warmup: ~50 H1 bars (~2 days). First signals after that.")

    try:
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️ Shutting down...")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
