#!/usr/bin/env python3
"""Hyperliquid Testnet Smoke Test — Phase 1a.

Connects to Hyperliquid testnet via NautilusTrader adapter.
Tests:
  1. Instrument provider loads perps
  2. Subscribes to BTC-USD-PERP and ETH-USD-PERP order books
  3. Logs top-of-book every 5 seconds
  4. Verifies WebSocket data flow

ENVIRONMENT:
    HYPERLIQUID_TESTNET_PK — EVM private key (auto-sourced by adapter)

USAGE:
    .venv/bin/python scripts/hyperliquid/hl_testnet_smoke_test.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from nautilus_trader.adapters.hyperliquid import HYPERLIQUID
from nautilus_trader.adapters.hyperliquid import HyperliquidDataClientConfig
from nautilus_trader.adapters.hyperliquid import HyperliquidExecClientConfig
from nautilus_trader.adapters.hyperliquid import HyperliquidLiveDataClientFactory
from nautilus_trader.adapters.hyperliquid import HyperliquidLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig


# =============================================================================
# SMOKE TEST STRATEGY
# =============================================================================

class HLSmokeTestConfig(StrategyConfig, frozen=True):
    strategy_id: str = "HL-SMOKE-001"
    instrument_ids: tuple[str, ...] = (
        "BTC-USD-PERP.HYPERLIQUID",
        "ETH-USD-PERP.HYPERLIQUID",
    )
    log_interval_secs: int = 5


class HLSmokeTest(Strategy):
    """Minimal strategy that subscribes to order books and logs top-of-book."""

    def __init__(self, config: HLSmokeTestConfig) -> None:
        super().__init__(config)
        self._instrument_ids = [
            InstrumentId.from_str(s) for s in config.instrument_ids
        ]
        self._log_interval = config.log_interval_secs
        self._tick_count: dict[str, int] = {}

    def on_start(self) -> None:
        self.log.info("=" * 60)
        self.log.info("🔬 HYPERLIQUID SMOKE TEST — Starting")
        self.log.info(f"   Instruments: {len(self._instrument_ids)}")
        self.log.info("=" * 60)

        for instrument_id in self._instrument_ids:
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.warning(f"   ❌ Instrument not found: {instrument_id}")
                continue

            self.log.info(f"   ✅ Loaded: {instrument_id}")
            self.log.info(f"      tick_size={instrument.price_increment}, "
                         f"lot_size={instrument.size_increment}")

            # Subscribe to L2 order book (top 10 levels)
            self.subscribe_order_book_deltas(instrument_id)
            self._tick_count[str(instrument_id)] = 0
            self.log.info(f"   📊 Subscribed to order book: {instrument_id}")

        # Set a recurring timer to log state
        from datetime import timedelta
        self.clock.set_timer(
            name="log_state",
            interval=timedelta(seconds=self._log_interval),
        )
        self.log.info("🟢 Smoke test running — watching for order book updates...")

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        key = str(deltas.instrument_id)
        self._tick_count[key] = self._tick_count.get(key, 0) + 1

    def on_event(self, event) -> None:
        pass

    def on_timer(self, event) -> None:
        """Log top-of-book for each instrument."""
        self.log.info("-" * 50)
        for instrument_id in self._instrument_ids:
            key = str(instrument_id)
            count = self._tick_count.get(key, 0)

            book = self.cache.order_book(instrument_id)
            if book is not None and book.best_bid_price() and book.best_ask_price():
                bid = book.best_bid_price()
                ask = book.best_ask_price()
                spread_bps = ((float(ask) - float(bid)) / float(bid)) * 10_000
                self.log.info(
                    f"📈 {instrument_id.symbol} | "
                    f"bid={bid} ask={ask} "
                    f"spread={spread_bps:.1f}bps | "
                    f"updates={count}"
                )
            else:
                self.log.info(
                    f"⏳ {instrument_id.symbol} | "
                    f"Waiting for book data... updates={count}"
                )

    def on_stop(self) -> None:
        self.log.info("🔴 Smoke test stopped")
        for key, count in self._tick_count.items():
            self.log.info(f"   {key}: {count} total updates")


# =============================================================================
# MAIN
# =============================================================================

def main():
    # Use testnet PK (adapter auto-sources HYPERLIQUID_TESTNET_PK)
    private_key = os.environ.get("HYPERLIQUID_TESTNET_PK") or os.environ.get("HYPERLIQUID_PK")
    if not private_key:
        print("❌ Set HYPERLIQUID_TESTNET_PK or HYPERLIQUID_PK in environment")
        sys.exit(1)

    print("=" * 60)
    print("🔬 HYPERLIQUID TESTNET SMOKE TEST")
    print(f"   Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"   Key:  {private_key[:6]}...{private_key[-4:]}")
    print(f"   Mode: TESTNET")
    print("=" * 60)

    instrument_ids = (
        "BTC-USD-PERP.HYPERLIQUID",
        "ETH-USD-PERP.HYPERLIQUID",
    )

    node_config = TradingNodeConfig(
        trader_id=TraderId("HL-SMOKE-001"),
        logging=LoggingConfig(
            log_level="INFO",
            use_pyo3=True,
            log_colors=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=False,
        ),
        data_clients={
            HYPERLIQUID: HyperliquidDataClientConfig(
                testnet=True,
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
                ),
            ),
        },
        exec_clients={
            HYPERLIQUID: HyperliquidExecClientConfig(
                private_key=private_key,
                testnet=True,
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
                ),
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    strategy_config = HLSmokeTestConfig(
        strategy_id="HL-SMOKE-001",
        instrument_ids=instrument_ids,
        log_interval_secs=5,
    )

    node = TradingNode(config=node_config)
    strategy = HLSmokeTest(config=strategy_config)
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(HYPERLIQUID, HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory(HYPERLIQUID, HyperliquidLiveExecClientFactory)
    node.build()

    try:
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️ Shutting down...")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
