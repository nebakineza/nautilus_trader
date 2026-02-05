#!/usr/bin/env python3
"""
Direct deployment of optimized ETH/LINK/AVAX strategies.
Simpler approach using direct adapter instantiation.
"""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
import os

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load environment
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

from nautilus_trader.adapters.binance.factories import get_cached_binance_http_client, get_cached_binance_spot_instrument_provider
from nautilus_trader.adapters.binance.spot.providers import BinanceSpotInstrumentProvider
from nautilus_trader.adapters.bybit.factories import get_cached_bybit_http_client, get_cached_bybit_spot_instrument_provider  
from nautilus_trader.adapters.bybit.common import BybitProductType
from nautilus_trader.common.component import init_logging, Logger, LiveClock, MessageBus
from nautilus_trader.common.enums import LogLevel
from nautilus_trader.cache.cache import Cache
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.execution.engine import ExecutionEngine
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.portfolio.portfolio import Portfolio  
from nautilus_trader.risk.engine import RiskEngine
from nautilus_trader.trading.trader import Trader

from strategy.lead_lag_bybit_binance_mm_v003_primer import LeadLagMMv3Primer, LeadLagMMv3PrimerConfig


BYBIT_API_KEY = os.getenv("BYBIT_API_KEY_LLMM")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET_LLMM")

# Optimized parameters from sweep
STRATEGIES = {
    "ETH": {
        "leader": "ETHUSDT.BINANCE",
        "follower": "ETHUSDT-SPOT.BYBIT",
        "spread_bps": Decimal("8.0"),
        "min_profit_bps": Decimal("0.4"),
        "order_qty": Decimal("0.01"),
        "max_position": Decimal("0.02"),
    },
    "LINK": {
        "leader": "LINKUSDT.BINANCE",
        "follower": "LINKUSDT-SPOT.BYBIT",
        "spread_bps": Decimal("10.0"),
        "min_profit_bps": Decimal("0.7"),
        "order_qty": Decimal("3.0"),
        "max_position": Decimal("6.0"),
    },
    "AVAX": {
        "leader": "AVAXUSDT.BINANCE",
        "follower": "AVAXUSDT-SPOT.BYBIT",
        "spread_bps": Decimal("8.0"),
        "min_profit_bps": Decimal("0.4"),
        "order_qty": Decimal("3.0"),
        "max_position": Decimal("6.0"),
    },
}


async def main():
    init_logging(level_stdout=LogLevel.INFO)
    
    clock = LiveClock()
    logger = Logger(clock)
    
    trader_id = TraderId("SENTINEL-LLMM-OPT")
    
    msgbus = MessageBus(
        trader_id=trader_id,
        clock=clock,
    )
    
    cache = Cache()
    portfolio = Portfolio(
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )
    
    data_engine = DataEngine(
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )
    
    exec_engine = ExecutionEngine(
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )
    
    risk_engine = RiskEngine(
        portfolio=portfolio,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )
    
    trader = Trader(
        trader_id=trader_id,
        msgbus=msgbus,
        cache=cache,
        portfolio=portfolio,
        data_engine=data_engine,
        risk_engine=risk_engine,
        exec_engine=exec_engine,
        clock=clock,
    )
    
    # Load instruments
    logger.info("Loading Binance instruments...")
    binance_provider = await get_cached_binance_spot_instrument_provider(
        clock=clock,
        is_us=False,
    )
    
    logger.info("Loading Bybit instruments...")
    bybit_provider = await get_cached_bybit_spot_instrument_provider(
        clock=clock,
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        is_testnet=False,
    )
    
    # Add instruments to cache
    for symbol in ["ETHUSDT", "LINKUSDT", "AVAXUSDT"]:
        binance_inst = binance_provider.find(InstrumentId.from_str(f"{symbol}.BINANCE"))
        if binance_inst:
            cache.add_instrument(binance_inst)
            logger.info(f"Loaded {binance_inst.id}")
        
        bybit_inst = bybit_provider.find(InstrumentId.from_str(f"{symbol}-SPOT.BYBIT"))
        if bybit_inst:
            cache.add_instrument(bybit_inst)
            logger.info(f"Loaded {bybit_inst.id}")
    
    # Create strategies
    for name, params in STRATEGIES.items():
        config = LeadLagMMv3PrimerConfig(
            leader_instrument_id=params["leader"],
            follower_instrument_id=params["follower"],
            order_qty=params["order_qty"],
            min_order_qty=params["order_qty"] / 2,
            max_position_qty=params["max_position"],
            guard_threshold_bps=Decimal("10.0"),
            spread_bps=params["spread_bps"],
            min_profit_bps=params["min_profit_bps"],
            ofi_enabled=True,
            ofi_max_bps=Decimal("3.0"),
            internal_price_delta_limit=Decimal("0.0"),
            quote_refresh_interval_ms=2000,
            min_quote_lifetime_ms=800,
            order_id_tag=name,
            oms_type="NETTING",
        )
        
        strategy = LeadLagMMv3Primer(config=config)
        trader.add_strategy(strategy)
        logger.info(f"Added strategy: {name}")
    
    logger.info("="*60)
    logger.info("✅ DEPLOYMENT COMPLETE")
    logger.info("Strategies: ETH (8bps), LINK (10bps), AVAX (8bps)")
    logger.info("="*60)
    
    # Run indefinitely
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")


if __name__ == "__main__":
    asyncio.run(main())
