# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import eth_account
from hyperliquid.exchange import Exchange as HyperliquidSDKExchange
from hyperliquid.info import Info as HyperliquidSDKInfo
from hyperliquid.utils.constants import MAINNET_API_URL as HL_MAINNET_URL
from hyperliquid.utils.constants import TESTNET_API_URL as HL_TESTNET_URL

from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.adapters.hyperliquid.constants import HYPERLIQUID_VENUE
from nautilus_trader.adapters.hyperliquid.providers import HyperliquidInstrumentProvider
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.enums import LogColor
from nautilus_trader.common.enums import LogLevel
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.execution.messages import BatchCancelOrders
from nautilus_trader.execution.messages import CancelAllOrders
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.execution.messages import GenerateFillReports
from nautilus_trader.execution.messages import GenerateOrderStatusReport
from nautilus_trader.execution.messages import GenerateOrderStatusReports
from nautilus_trader.execution.messages import GeneratePositionStatusReports
from nautilus_trader.execution.messages import ModifyOrder
from nautilus_trader.execution.messages import QueryAccount
from nautilus_trader.execution.messages import QueryOrder
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.execution.messages import SubmitOrderList
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import LiquiditySide
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import order_side_to_str
from nautilus_trader.model.functions import order_side_to_pyo3
from nautilus_trader.model.functions import order_type_to_pyo3
from nautilus_trader.model.functions import time_in_force_to_pyo3
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


class HyperliquidExecutionClient(LiveExecutionClient):
    """
    Provides an execution client for the Hyperliquid decentralized exchange (DEX).

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    client : Any
        The Hyperliquid HTTP client.
    msgbus : MessageBus
        The message bus for the client.
    cache : Cache
        The cache for the client.
    clock : LiveClock
        The clock for the client.
    instrument_provider : HyperliquidInstrumentProvider
        The instrument provider.
    config : HyperliquidExecClientConfig
        The configuration for the client.
    name : str, optional
        The custom client ID.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: Any,  # TODO: Replace with actual HyperliquidHttpClient when available
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: HyperliquidInstrumentProvider,
        config: HyperliquidExecClientConfig,
        name: str | None = None,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(name or HYPERLIQUID_VENUE.value),
            venue=HYPERLIQUID_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=None,  # Multi-currency account
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )

        # Configuration
        self._config = config
        self._client = client
        self._instrument_provider: HyperliquidInstrumentProvider = instrument_provider

        # Order batching — collects orders for 50ms then flushes as single bulk_orders API call
        self._pending_orders: list = []
        self._batch_flush_task: asyncio.Task | None = None

        # Modify batching — collects modify commands then flushes as single batchModify API call
        self._pending_modifies: list = []
        self._modify_flush_task: asyncio.Task | None = None

        # Log configuration details
        self._log.info(f"config.testnet={config.testnet}", LogColor.BLUE)
        self._log.info(f"config.http_timeout_secs={config.http_timeout_secs}", LogColor.BLUE)
        self._log.info(f"{config.http_proxy_url=}", LogColor.BLUE)
        self._log.info(f"{config.ws_proxy_url=}", LogColor.BLUE)

        account_id = AccountId(f"{name or HYPERLIQUID_VENUE.value}-master")
        self._set_account_id(account_id)

        # Initialize official Python SDK exchange for order execution
        # (bypasses Rust signing which has msgpack serialization mismatch)
        private_key = config.private_key
        if not private_key:
            if config.testnet:
                private_key = os.environ.get("HYPERLIQUID_TESTNET_PK")
            else:
                private_key = os.environ.get("HYPERLIQUID_PK") or os.environ.get("HYPERLIQUID_MAINNET_PK")
        if private_key:
            sdk_wallet = eth_account.Account.from_key(private_key)
            sdk_base_url = HL_TESTNET_URL if config.testnet else HL_MAINNET_URL

            # Resolve master wallet address
            # API/agent wallets sign on behalf of the master wallet that holds funds
            self._master_address = (
                config.wallet_address
                or os.environ.get("HYPERLIQUID_WALLET")
            )

            self._sdk_info = HyperliquidSDKInfo(sdk_base_url, skip_ws=True)
            self._sdk_exchange = HyperliquidSDKExchange(
                sdk_wallet,
                sdk_base_url,
                vault_address=config.vault_address,
                account_address=self._master_address,
            )

            agent_label = sdk_wallet.address[:10]
            master_label = self._master_address[:10] if self._master_address else 'SELF'
            self._log.info(
                f"SDK exchange initialized: agent={agent_label}... "
                f"master={master_label}... "
                f"({'testnet' if config.testnet else 'mainnet'})",
                LogColor.GREEN,
            )
        else:
            self._sdk_exchange = None
            self._sdk_info = None
            self._log.warning("No private key - SDK exchange not initialized, orders will fail")

        # Lock to serialize SDK order submissions (avoids nonce collisions)
        self._sdk_lock = asyncio.Lock()

        # Fill polling — track which fills we've already processed
        self._seen_fill_ids: set[str] = set()
        self._fill_poll_task: asyncio.Task | None = None
        self._fill_poll_running: bool = False
        # Map venue_order_id -> (strategy_id, client_order_id, instrument_id) for fill routing
        self._order_id_map: dict[str, tuple] = {}

        self._ws_connection = None

        self._log.info("Hyperliquid execution client initialized")

    @property
    def hyperliquid_instrument_provider(self) -> HyperliquidInstrumentProvider:
        return self._instrument_provider

    def _cache_instruments(self) -> None:
        # Ensures instrument definitions are available for correct
        # price and size precisions when parsing responses
        instruments_pyo3 = self._instrument_provider.instruments_pyo3()
        for inst in instruments_pyo3:
            self._client.cache_instrument(inst)

        self._log.debug("Cached instruments", LogColor.MAGENTA)

    # -- CONNECTION HANDLERS -----------------------------------------------------------------------

    async def _connect(self) -> None:
        self._log.info("Loading instruments...", LogColor.BLUE)
        await self._instrument_provider.initialize()
        self._cache_instruments()

        # Set account ID on HTTP client for report generation
        self._client.set_account_id(str(self.account_id))

        self._log.info(
            f"Loaded {len(self._instrument_provider.list_all())} instruments",
            LogColor.GREEN,
        )

        # TODO: Implement account state updates when API is available
        # await self._update_account_state()

        # Start fill polling loop (queries userFills every 2s to detect fills)
        if self._sdk_info is not None:
            self._fill_poll_running = True
            self._fill_poll_task = asyncio.ensure_future(self._poll_fills_loop())
            self._log.info("Fill polling started (2s interval)", LogColor.GREEN)

        self._log.info("Hyperliquid execution client connected", LogColor.GREEN)

    async def _disconnect(self) -> None:
        # Stop fill polling
        self._fill_poll_running = False
        if self._fill_poll_task and not self._fill_poll_task.done():
            self._fill_poll_task.cancel()
            try:
                await self._fill_poll_task
            except asyncio.CancelledError:
                pass

        if self._ws_connection:
            pass

        await asyncio.sleep(0.1)
        self._log.info("Hyperliquid execution disconnection completed", LogColor.GREEN)

    # -- FILL POLLING -----------------------------------------------------------------------------

    async def _poll_fills_loop(self) -> None:
        """Poll Hyperliquid for user fills every 2 seconds.

        This is the primary mechanism for detecting fills since WebSocket
        user events are not yet wired up. Each new fill generates an
        OrderFilled event that flows to the strategy's on_order_filled().
        """
        # Seed with existing fills so we don't replay history
        try:
            await self._seed_seen_fills()
        except Exception as e:
            self._log.warning(f"Failed to seed fill history: {e}")

        while self._fill_poll_running:
            try:
                await asyncio.sleep(3.0)  # 3s interval (was 2s) — reduces info API load by 33%
                await self._poll_fills_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str:
                    self._log.warning(f"Fill poll rate limited (429) — backing off 15s")
                    await asyncio.sleep(15.0)
                else:
                    self._log.error(f"Fill poll error: {e}")
                    await asyncio.sleep(5.0)

    async def _seed_seen_fills(self) -> None:
        """Load existing fill IDs so we only process NEW fills."""
        addr = self._master_address or self._sdk_exchange.wallet.address
        loop = asyncio.get_event_loop()
        fills = await loop.run_in_executor(
            None,
            lambda: self._sdk_info.user_fills(addr),
        )
        for f in fills:
            fill_id = f.get("tid") or f.get("oid") or str(f.get("time", ""))
            self._seen_fill_ids.add(str(fill_id))
        self._log.info(f"Seeded {len(self._seen_fill_ids)} historical fills")

    async def _poll_fills_once(self) -> None:
        """Query userFills and generate OrderFilled events for new fills."""
        if not self._sdk_info:
            return

        addr = self._master_address or self._sdk_exchange.wallet.address
        loop = asyncio.get_event_loop()
        fills = await loop.run_in_executor(
            None,
            lambda: self._sdk_info.user_fills(addr),
        )

        new_count = 0
        for f in fills:
            fill_id = str(f.get("tid") or f.get("oid") or f.get("time", ""))
            if fill_id in self._seen_fill_ids:
                continue
            self._seen_fill_ids.add(fill_id)
            new_count += 1

            # Parse fill data
            coin = f.get("coin", "")
            side_str = f.get("side", "")
            px = float(f.get("px", 0))
            sz = float(f.get("sz", 0))
            fee = float(f.get("fee", 0))
            oid = str(f.get("oid", ""))

            # Map coin to instrument_id (e.g., "ETH" -> "ETH-USD-PERP.HYPERLIQUID")
            instrument_id = InstrumentId.from_str(f"{coin}-USD-PERP.HYPERLIQUID")
            instrument = self._cache.instrument(instrument_id)
            if instrument is None:
                self._log.warning(f"Fill for unknown instrument {coin}, skipping")
                continue

            order_side = OrderSide.BUY if side_str == "B" else OrderSide.SELL

            # Try to find the matching order from our order map
            order_info = self._order_id_map.get(oid)
            if order_info:
                strategy_id, client_order_id, _ = order_info
            else:
                # Fill from an order we don't know about (placed before this session)
                # Still generate the event — strategy needs to know about position
                strategy_id = None
                client_order_id = None

            # Find matching cached order by venue_order_id
            venue_order_id = VenueOrderId(oid)
            client_oid = self._cache.client_order_id(venue_order_id)
            cached_order = self._cache.order(client_oid) if client_oid else None

            if cached_order is not None:
                # Generate fill through the standard NautilusTrader pipeline
                self.generate_order_filled(
                    strategy_id=cached_order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=cached_order.client_order_id,
                    venue_order_id=venue_order_id,
                    venue_position_id=None,
                    trade_id=TradeId(fill_id),
                    order_side=order_side,
                    order_type=cached_order.order_type,
                    last_qty=instrument.make_qty(sz),
                    last_px=instrument.make_price(px),
                    quote_currency=instrument.quote_currency,
                    commission=Money(fee, instrument.quote_currency),
                    liquidity_side=LiquiditySide.MAKER,
                    ts_event=self._clock.timestamp_ns(),
                )

                side_emoji = "🟢" if order_side == OrderSide.BUY else "🔴"
                self._log.info(
                    f"{side_emoji} FILL DETECTED: {coin} {side_str} {sz} @ {px} "
                    f"fee=${fee:.4f} oid={oid}",
                    LogColor.GREEN if order_side == OrderSide.BUY else LogColor.RED,
                )
            else:
                # Order not in cache — emit a custom data event for the strategy
                # The strategy has its own fill tracking via on_order_filled
                self._log.warning(
                    f"Fill for uncached order: {coin} {side_str} {sz} @ {px} oid={oid} — "
                    f"strategy position may be stale"
                )

        if new_count > 0:
            self._log.info(f"Processed {new_count} new fills")

    # -- COMMANDS ---------------------------------------------------------------------------------

    async def _submit_order(self, command: SubmitOrder) -> None:
        order = command.order

        if order.is_closed:
            self._log.warning(f"Order {order} is already closed")
            return

        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

        # Batch orders to reduce API calls (prevents 429 rate limits)
        # Collect orders for a short window then flush as a single bulk_orders call
        if self._sdk_exchange is not None:
            self._pending_orders.append(order)
            if self._batch_flush_task is None or self._batch_flush_task.done():
                self._batch_flush_task = asyncio.ensure_future(self._flush_pending_orders())
            return

        # Fallback: submit individually via Rust client
        try:
            report = await self._submit_order_via_rust(order)
            self.generate_order_accepted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=report.venue_order_id if hasattr(report, 'venue_order_id') else VenueOrderId(str(report)),
                ts_event=self._clock.timestamp_ns(),
            )
            self._log.info(f"Order {order.client_order_id} accepted")
        except Exception as e:
            self._log.error(f"Error submitting order {order.client_order_id}: {e}")
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(e),
                ts_event=self._clock.timestamp_ns(),
            )

    async def _flush_pending_orders(self) -> None:
        """Wait briefly to collect orders, then flush as a single bulk_orders API call."""
        # Short delay to allow strategy to submit all grid orders
        await asyncio.sleep(0.05)  # 50ms collection window

        orders = list(self._pending_orders)
        self._pending_orders.clear()

        if not orders:
            return

        try:
            specs = [self._order_to_sdk_spec(o) for o in orders]
            self._log.info(f"SDK bulk submit: {len(specs)} orders in single API call")

            async with self._sdk_lock:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._sdk_exchange.bulk_orders(specs),
                )

            if result.get("status") == "ok":
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                accepted = 0
                for i, order in enumerate(orders):
                    if i < len(statuses):
                        status = statuses[i]
                        if "resting" in status:
                            venue_oid = str(status["resting"]["oid"])
                        elif "filled" in status:
                            venue_oid = str(status["filled"]["oid"])
                        else:
                            self._log.warning(f"Order {order.client_order_id}: {status}")
                            self.generate_order_rejected(
                                strategy_id=order.strategy_id,
                                instrument_id=order.instrument_id,
                                client_order_id=order.client_order_id,
                                reason=str(status),
                                ts_event=self._clock.timestamp_ns(),
                            )
                            continue

                        self.generate_order_accepted(
                            strategy_id=order.strategy_id,
                            instrument_id=order.instrument_id,
                            client_order_id=order.client_order_id,
                            venue_order_id=VenueOrderId(venue_oid),
                            ts_event=self._clock.timestamp_ns(),
                        )
                        # Track for fill matching
                        self._order_id_map[venue_oid] = (
                            order.strategy_id,
                            order.client_order_id,
                            order.instrument_id,
                        )
                        accepted += 1
                self._log.info(f"Bulk submit: {accepted}/{len(orders)} accepted")
            else:
                raise RuntimeError(f"Bulk order rejected: {result}")

        except Exception as e:
            self._log.error(f"Error in bulk submit ({len(orders)} orders): {e}")
            for order in orders:
                self.generate_order_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    reason=str(e),
                    ts_event=self._clock.timestamp_ns(),
                )

    async def _submit_order_via_sdk(self, order) -> Any:
        """Submit order using the official Hyperliquid Python SDK (correct signing).

        For single order submission. For batch submission of multiple orders
        in a single API call, see _submit_order_list which uses bulk_orders.
        """
        # Extract coin name from instrument_id (e.g., "ETH-USD-PERP.HYPERLIQUID" -> "ETH")
        instrument_id_str = order.instrument_id.value
        symbol = instrument_id_str.split(".")[0]  # "ETH-USD-PERP"
        coin = symbol.split("-")[0]  # "ETH"

        is_buy = order.side == OrderSide.BUY
        sz = float(order.quantity)
        limit_px = float(order.price) if order.has_price else 0.0
        # Hyperliquid requires at most 5 significant figures for prices
        limit_px = self._snap_price_sig_figs(limit_px)

        # Map NautilusTrader TIF to Hyperliquid TIF
        if order.time_in_force == TimeInForce.GTC:
            tif = "Gtc"
        elif order.time_in_force == TimeInForce.IOC:
            tif = "Ioc"
        else:
            tif = "Gtc"

        # Post-only maps to ALO
        if order.is_post_only:
            tif = "Alo"

        order_type = {"limit": {"tif": tif}}
        reduce_only = order.is_reduce_only

        self._log.info(
            f"SDK submit: {coin} {'BUY' if is_buy else 'SELL'} {sz} @ {limit_px} "
            f"tif={tif} reduce_only={reduce_only}",
        )

        # Run sync SDK call in thread pool to not block event loop
        # Lock ensures sequential submission to avoid nonce collisions
        async with self._sdk_lock:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._sdk_exchange.order(
                    coin, is_buy, sz, limit_px, order_type, reduce_only=reduce_only,
                ),
            )

        self._log.debug(f"SDK result: {result}")

        if result.get("status") != "ok":
            raise RuntimeError(f"Order rejected by Hyperliquid: {result}")

        # Extract venue order ID
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if statuses:
            status = statuses[0]
            if "resting" in status:
                venue_oid = str(status["resting"]["oid"])
            elif "filled" in status:
                venue_oid = str(status["filled"]["oid"])
            else:
                venue_oid = str(status)
        else:
            venue_oid = "unknown"

        return VenueOrderId(venue_oid)

    @staticmethod
    def _snap_price_sig_figs(price: float, sig_figs: int = 5) -> float:
        """Snap price to Hyperliquid's max significant figures.

        Hyperliquid requires prices have at most 5 significant figures.
        Integer prices are always valid regardless of sig fig count.
        """
        if price == 0:
            return 0.0
        import math
        d = math.ceil(math.log10(abs(price)))
        power = sig_figs - d
        magnitude = 10 ** power
        return round(price * magnitude) / magnitude

    def _order_to_sdk_spec(self, order) -> dict:
        """Convert a NautilusTrader order to an SDK bulk_orders spec dict."""
        instrument_id_str = order.instrument_id.value
        symbol = instrument_id_str.split(".")[0]
        coin = symbol.split("-")[0]

        is_buy = order.side == OrderSide.BUY
        sz = float(order.quantity)
        limit_px = float(order.price) if order.has_price else 0.0
        # Hyperliquid requires at most 5 significant figures for prices
        limit_px = self._snap_price_sig_figs(limit_px)

        if order.time_in_force == TimeInForce.GTC:
            tif = "Gtc"
        elif order.time_in_force == TimeInForce.IOC:
            tif = "Ioc"
        else:
            tif = "Gtc"
        if order.is_post_only:
            tif = "Alo"

        order_type = {"limit": {"tif": tif}}
        reduce_only = order.is_reduce_only

        return {
            "coin": coin,
            "is_buy": is_buy,
            "sz": sz,
            "limit_px": limit_px,
            "order_type": order_type,
            "reduce_only": reduce_only,
        }

    async def _submit_order_via_rust(self, order) -> Any:
        """Submit order using the Rust PyO3 client (original path)."""
        self._log.info(f"Submitting order to Hyperliquid via Rust client: {order}")

            # Convert Cython types to PyO3 types for Rust client boundary
        pyo3_instrument_id = nautilus_pyo3.InstrumentId.from_str(
            order.instrument_id.value,
        )
        pyo3_client_order_id = nautilus_pyo3.ClientOrderId(
            order.client_order_id.value,
        )
        pyo3_order_side = order_side_to_pyo3(order.side)
        pyo3_order_type = order_type_to_pyo3(order.order_type)
        pyo3_quantity = nautilus_pyo3.Quantity.from_str(str(order.quantity))
        pyo3_time_in_force = time_in_force_to_pyo3(order.time_in_force)
        pyo3_price = (
            nautilus_pyo3.Price.from_str(str(order.price))
            if order.has_price else None
        )
        pyo3_trigger_price = (
            nautilus_pyo3.Price.from_str(str(order.trigger_price))
            if order.has_trigger_price else None
        )

        report = await self._client.submit_order(
            instrument_id=pyo3_instrument_id,
            client_order_id=pyo3_client_order_id,
            order_side=pyo3_order_side,
            order_type=pyo3_order_type,
            quantity=pyo3_quantity,
            time_in_force=pyo3_time_in_force,
            price=pyo3_price,
            trigger_price=pyo3_trigger_price,
            post_only=order.is_post_only,
            reduce_only=order.is_reduce_only,
        )

        self._log.debug(f"Received order status report: {report}")
        return report

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        """Submit multiple orders in a single API call using SDK bulk_orders."""
        order_list = command.order_list
        orders = order_list.orders

        if not orders:
            self._log.warning("Order list is empty, nothing to submit")
            return

        closed_orders = [order for order in orders if order.is_closed]
        if closed_orders:
            self._log.warning(f"Skipping {len(closed_orders)} closed orders in batch")
            orders = [order for order in orders if not order.is_closed]

        if not orders:
            return

        now_ns = self._clock.timestamp_ns()

        for order in orders:
            self.generate_order_submitted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                ts_event=now_ns,
            )

        # Use SDK bulk_orders for a single API call (1 nonce, 1 request)
        if self._sdk_exchange is not None:
            try:
                specs = [self._order_to_sdk_spec(o) for o in orders]
                self._log.info(f"SDK bulk submit: {len(specs)} orders")

                async with self._sdk_lock:
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        lambda: self._sdk_exchange.bulk_orders(specs),
                    )

                self._log.debug(f"Bulk result: {result}")

                if result.get("status") == "ok":
                    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                    for i, order in enumerate(orders):
                        if i < len(statuses):
                            status = statuses[i]
                            if "resting" in status:
                                venue_oid = str(status["resting"]["oid"])
                            elif "filled" in status:
                                venue_oid = str(status["filled"]["oid"])
                            else:
                                venue_oid = str(status)
                                self._log.warning(f"Order {order.client_order_id} status: {status}")
                                self.generate_order_rejected(
                                    strategy_id=order.strategy_id,
                                    instrument_id=order.instrument_id,
                                    client_order_id=order.client_order_id,
                                    reason=str(status),
                                    ts_event=self._clock.timestamp_ns(),
                                )
                                continue

                            self.generate_order_accepted(
                                strategy_id=order.strategy_id,
                                instrument_id=order.instrument_id,
                                client_order_id=order.client_order_id,
                                venue_order_id=VenueOrderId(venue_oid),
                                ts_event=self._clock.timestamp_ns(),
                            )
                else:
                    raise RuntimeError(f"Bulk order rejected: {result}")

            except Exception as e:
                self._log.error(f"Error submitting order batch: {e}")
                for order in orders:
                    self.generate_order_rejected(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        reason=str(e),
                        ts_event=self._clock.timestamp_ns(),
                    )
            return

        # Fallback to Rust client
        try:
            self._log.info(f"Submitting {len(orders)} orders to Hyperliquid as batch")
            reports = await self._client.submit_orders(orders)
            for report in reports:
                order = next(
                    (o for o in orders if o.client_order_id == report.client_order_id),
                    None,
                )
                if order:
                    self.generate_order_accepted(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        venue_order_id=report.venue_order_id,
                        ts_event=self._clock.timestamp_ns(),
                    )

        except Exception as e:
            self._log.error(f"Error submitting order batch: {e}")
            # Generate rejection events for all orders
            for order in orders:
                self.generate_order_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    reason=str(e),
                    ts_event=self._clock.timestamp_ns(),
                )

    async def _modify_order(self, command: ModifyOrder) -> None:
        """Modify an existing order's price/quantity via SDK batchModify.

        Uses batching: commands are collected for 50ms then flushed as
        a single batchModify API call, same pattern as order submission.
        """
        if self._sdk_exchange is None:
            self._log.warning(f"Modify not available — SDK not initialized: {command.client_order_id}")
            return

        if command.venue_order_id is None:
            self._log.warning(f"Cannot modify order without venue_order_id: {command.client_order_id}")
            return

        self._pending_modifies.append(command)
        if self._modify_flush_task is None or self._modify_flush_task.done():
            self._modify_flush_task = asyncio.ensure_future(self._flush_pending_modifies())

    async def _flush_pending_modifies(self) -> None:
        """Wait briefly to collect modify commands, then flush as single batchModify API call."""
        await asyncio.sleep(0.05)  # 50ms collection window

        commands = list(self._pending_modifies)
        self._pending_modifies.clear()

        if not commands:
            return

        try:
            modify_requests = []
            for cmd in commands:
                instrument_id_str = cmd.instrument_id.value
                symbol = instrument_id_str.split(".")[0]
                coin = symbol.split("-")[0]

                oid = int(cmd.venue_order_id.value)

                # Determine order side from cached order
                cached_order = self._cache.order(cmd.client_order_id)
                if cached_order is None:
                    self._log.warning(f"Cannot find cached order for modify: {cmd.client_order_id}")
                    continue
                is_buy = cached_order.side == OrderSide.BUY

                new_px = float(cmd.price) if cmd.price is not None else float(cached_order.price)
                new_sz = float(cmd.quantity) if cmd.quantity is not None else float(cached_order.quantity)

                # Snap price to 5 sig figs
                new_px = self._snap_price_sig_figs(new_px)

                # Determine TIF from cached order
                if cached_order.is_post_only:
                    tif = "Alo"
                elif cached_order.time_in_force == TimeInForce.IOC:
                    tif = "Ioc"
                else:
                    tif = "Gtc"

                order_type = {"limit": {"tif": tif}}
                reduce_only = cached_order.is_reduce_only

                modify_requests.append({
                    "oid": oid,
                    "order": {
                        "coin": coin,
                        "is_buy": is_buy,
                        "sz": new_sz,
                        "limit_px": new_px,
                        "order_type": order_type,
                        "reduce_only": reduce_only,
                    },
                })

            if not modify_requests:
                return

            self._log.info(f"SDK bulk modify: {len(modify_requests)} orders in single API call")

            async with self._sdk_lock:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._sdk_exchange.bulk_modify_orders_new(modify_requests),
                )

            if result.get("status") == "ok":
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                accepted = 0
                for i, cmd in enumerate(commands):
                    if i >= len(modify_requests):
                        break
                    if i < len(statuses):
                        status = statuses[i]
                        if "resting" in status:
                            new_venue_oid = str(status["resting"]["oid"])
                        elif "filled" in status:
                            new_venue_oid = str(status["filled"]["oid"])
                        else:
                            self._log.warning(f"Modify {cmd.client_order_id}: {status}")
                            continue

                        # Generate order updated event
                        # HL assigns a new OID on modify, so venue_order_id_modified=True
                        self.generate_order_updated(
                            strategy_id=cmd.strategy_id,
                            instrument_id=cmd.instrument_id,
                            client_order_id=cmd.client_order_id,
                            venue_order_id=VenueOrderId(new_venue_oid),
                            quantity=cmd.quantity,
                            price=cmd.price,
                            trigger_price=None,
                            ts_event=self._clock.timestamp_ns(),
                            venue_order_id_modified=True,
                        )
                        # Update order_id_map with new venue OID
                        self._order_id_map[new_venue_oid] = (
                            cmd.strategy_id,
                            cmd.client_order_id,
                            cmd.instrument_id,
                        )
                        accepted += 1
                self._log.info(f"Bulk modify: {accepted}/{len(modify_requests)} updated")
            else:
                self._log.error(f"Bulk modify rejected: {result}")
                # Modifications failed — orders remain at old price/qty, no need to reject

        except Exception as e:
            self._log.error(f"Error in bulk modify ({len(commands)} orders): {e}")

    async def _cancel_order(self, command: CancelOrder) -> None:
        if self._sdk_exchange is not None and command.venue_order_id is not None:
            try:
                instrument_id_str = command.instrument_id.value
                symbol = instrument_id_str.split(".")[0]
                coin = symbol.split("-")[0]
                oid = int(command.venue_order_id.value)

                self._log.info(f"SDK cancel: {coin} oid={oid}")

                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._sdk_exchange.cancel(coin, oid),
                )

                self._log.info(f"Cancel result: {result}")

                if result.get("status") == "ok":
                    self.generate_order_canceled(
                        strategy_id=command.strategy_id,
                        instrument_id=command.instrument_id,
                        client_order_id=command.client_order_id,
                        venue_order_id=command.venue_order_id,
                        ts_event=self._clock.timestamp_ns(),
                    )
                else:
                    self._log.error(f"Cancel failed: {result}")
            except Exception as e:
                self._log.error(f"Error canceling order {command.client_order_id}: {e}")
        else:
            self._log.warning(f"Order cancellation not yet implemented for {command.client_order_id}")

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        if command.order_side != OrderSide.NO_ORDER_SIDE:
            self._log.warning(
                f"Hyperliquid does not support order_side filtering for cancel all orders; "
                f"ignoring order_side={order_side_to_str(command.order_side)} and canceling all orders",
            )

        if self._sdk_exchange is not None and command.instrument_id is not None:
            try:
                instrument_id_str = command.instrument_id.value
                symbol = instrument_id_str.split(".")[0]
                coin = symbol.split("-")[0]

                # Use cached venue order IDs to cancel without querying open_orders
                # This saves 1 API call with weight=20 per cancel cycle
                cached_orders = self._cache.orders_open(instrument_id=command.instrument_id)
                oids = []
                for cached_order in cached_orders:
                    if cached_order.venue_order_id is not None:
                        try:
                            oids.append(int(cached_order.venue_order_id.value))
                        except (ValueError, TypeError):
                            pass

                if oids:
                    cancel_requests = [{"coin": coin, "oid": oid} for oid in oids]
                    async with self._sdk_lock:
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(
                            None,
                            lambda: self._sdk_exchange.bulk_cancel(cancel_requests),
                        )
                    self._log.info(f"Cancelled {len(oids)} {coin} orders")
                else:
                    self._log.debug(f"No cached orders to cancel for {coin}")
            except Exception as e:
                self._log.error(f"Error canceling all orders: {e}")
        else:
            instrument_str = (
                f" for {command.instrument_id}" if command.instrument_id is not None else ""
            )
            self._log.warning(f"Cancel all orders not yet implemented{instrument_str}")

    async def _batch_cancel_orders(self, command: BatchCancelOrders) -> None:
        self._log.warning(
            f"Batch cancel orders not yet implemented for {len(command.cancels)} orders",
        )

    # -- REPORTS ----------------------------------------------------------------------------------

    async def generate_order_status_report(
        self,
        command: GenerateOrderStatusReport,
    ) -> OrderStatusReport | None:
        self._log.warning(
            f"Order status report generation not yet implemented for {command.client_order_id}",
        )
        return None

    async def generate_order_status_reports(
        self,
        command: GenerateOrderStatusReports,
    ) -> list[OrderStatusReport]:
        try:
            instrument_id = command.instrument_id.value if command.instrument_id else None
            reports = await self._client.request_order_status_reports(instrument_id=instrument_id)

            self._log_report_receipt(
                len(reports),
                "OrderStatusReport",
                command.log_receipt_level,
                "Generated",
            )
            return reports
        except Exception as e:
            self._log.error(f"Failed to generate order status reports: {e}")
            return []

    async def generate_fill_reports(
        self,
        command: GenerateFillReports,
    ) -> list[FillReport]:
        try:
            instrument_id = command.instrument_id.value if command.instrument_id else None
            reports = await self._client.request_fill_reports(instrument_id=instrument_id)

            self._log_report_receipt(len(reports), "FillReport", LogLevel.INFO, "Generated")
            return reports
        except Exception as e:
            self._log.error(f"Failed to generate fill reports: {e}")
            return []

    async def generate_position_status_reports(
        self,
        command: GeneratePositionStatusReports,
    ) -> list[PositionStatusReport]:
        try:
            instrument_id = command.instrument_id.value if command.instrument_id else None
            reports = await self._client.request_position_status_reports(
                instrument_id=instrument_id,
            )

            self._log_report_receipt(
                len(reports),
                "PositionStatusReport",
                command.log_receipt_level,
            )

            return reports
        except Exception as e:
            self._log.error(f"Failed to generate position status reports: {e}")
            return []

    # -- QUERIES ----------------------------------------------------------------------------------

    async def _query_order(self, command: QueryOrder) -> None:
        self._log.warning(f"Order query not yet implemented for {command.client_order_id}")

    async def _query_account(self, command: QueryAccount) -> None:
        self._log.warning("Account query not yet implemented")
