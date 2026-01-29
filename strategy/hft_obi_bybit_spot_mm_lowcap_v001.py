"""Low-Capital Order Book Imbalance (OBI) market maker for BYBIT SPOT.

This strategy is specifically optimized for low-capital accounts ($500-$5,000) using:
- Aggressive OBI signals for faster entry/exit (15% threshold vs 20%)
- Reduced order book depth analysis (5 levels vs 10) for quicker reactions
- Tighter spreads (1-5 bps vs 2-10 bps) for higher fill rates
- Faster quote refresh (50ms vs 100ms) for micro-scalping
- Shorter position holding limits (120s vs 300s)
- Higher turnover targeting for capital efficiency

Strategy ID: hft_obi_bybit_spot_mm_lowcap_v001
Venue: BYBIT SPOT
Asset Pair: BTC-USDT
Capital Range: $500 - $5,000
Created: January 2026
"""

import time
from decimal import Decimal
from collections import deque

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy


class LowCapitalMMConfig(StrategyConfig):
    """Configuration for low-capital market maker strategy."""

    instrument_id: str = "BTCUSDT-SPOT.BYBIT"

    # Position Sizing (optimized for $500 account)
    base_qty: Decimal = Decimal("0.0001")  # ~$9.75 per quote (2% of $500)
    max_position_qty: Decimal = Decimal("0.0005")  # $48.75 max directional (10% of capital)
    max_notional_usd: float = 500.0  # $500 total exposure cap

    # Order Book Imbalance (OBI) - More aggressive for low capital
    obi_levels: int = 5  # Top 5 levels only (faster signal, less noise)
    obi_ema_period: int = 15  # Shorter EMA (faster reaction to changes)
    obi_entry_threshold: float = 0.15  # |OBI| > 15% to trade (more aggressive)
    obi_exit_threshold: float = 0.03  # |OBI| < 3% to flatten (tighter exit)

    # Spread Management - Tighter for higher fill probability
    min_spread_bps: int = 1  # Minimum 1bp spread (aggressive)
    max_spread_bps: int = 5  # Maximum 5bps spread (tighter)
    volatility_multiplier: float = 1.2  # Less volatility adjustment

    # Inventory Management - Faster turnover
    inventory_skew_bps: float = 1.0  # 1.0bps skew per $1k inventory (2x aggressive)
    inventory_half_life_seconds: float = 30.0  # Target 30s turnover (2x faster)
    max_inventory_age_seconds: float = 120.0  # Force close after 2min (2.5x faster)

    # Risk Controls - Adjusted for low capital
    max_order_rate_per_second: int = 200  # 200 orders/sec (2x higher)
    min_order_spacing_ms: int = 25  # 25ms between order updates (2x faster)
    emergency_liquidation_loss_usd: float = -100.0  # Kill switch @ -$100 (20% of capital)

    # Timing - Much faster for micro-scalping
    orderbook_update_throttle_ms: int = 5  # Process OB every 5ms (2x faster)
    quote_refresh_interval_ms: int = 50  # Re-quote every 50ms (2x faster)


class LowCapitalOBIMarketMaker(Strategy):
    """Low-capital optimized order book imbalance market maker for BYBIT SPOT."""

    def __init__(self, config: LowCapitalMMConfig):
        super().__init__(config)
        
        # Store config reference for type hints
        self.instrument = None
        self.book = None
        
        # OBI calculation
        self._obi_history = deque(maxlen=config.obi_ema_period)
        self._obi_ema = 0.0
        self._obi_lookback = []  # For volatility
        
        # Timing control
        self._last_update_time = 0.0
        self._last_quote_time = 0.0
        self._position_entry_time = 0.0
        
        # Position tracking
        self._net_qty = Decimal(0)
        self._last_bid_price = None
        self._last_ask_price = None
        self._position_pnl = Decimal(0)
        
        # Order tracking
        self._active_bid_order_id = None
        self._active_ask_order_id = None
        self._order_count = 0
        self._last_order_time = 0.0
        
        # Risk metrics
        self._max_loss_trigger = False
        self._unrealized_loss = Decimal(0)
        
        # Metrics
        self._trades_count = 0
        self._maker_fills = 0
        self._taker_fills = 0

    def on_start(self) -> None:
        """Initialize strategy on start."""
        self.instrument = self.cache.instrument(
            InstrumentId.from_str(self.config.instrument_id)
        )
        
        if not self.instrument:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return
        
        # Create order book
        self.book = OrderBook(
            instrument_id=self.instrument.id,
            book_type=BookType.L2_MBP,
        )
        
        # Subscribe to order book deltas (L2 updates)
        self.subscribe_order_book_deltas(self.instrument.id)
        
        self.log.info(
            f"✓ LOW-CAPITAL Strategy initialized: {self.config.instrument_id}",
            LogColor.CYAN,
        )
        self.log.info(
            f"  OBI Threshold: {self.config.obi_entry_threshold:.2%} (AGGRESSIVE)",
        )
        self.log.info(
            f"  OBI Levels: {self.config.obi_levels} (FAST)",
        )
        self.log.info(
            f"  Max Position: {self.config.max_position_qty} BTC",
        )
        self.log.info(
            f"  Quote Spread: {self.config.min_spread_bps}-{self.config.max_spread_bps} bps (TIGHT)",
        )
        self.log.info(
            f"  Quote Refresh: {self.config.quote_refresh_interval_ms}ms (FAST)",
        )

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """Process order book updates."""
        now = time.time()
        
        # Apply delta to local order book
        self.book.apply(deltas)
        
        # Throttle updates to prevent overprocessing
        elapsed_ms = (now - self._last_update_time) * 1000
        if elapsed_ms < self.config.orderbook_update_throttle_ms:
            return
        
        self._last_update_time = now
        
        # Calculate OBI signal
        obi = self._calculate_obi()
        if obi is not None:
            self._obi_history.append(obi)
            self._update_obi_ema()
        
        # Check for emergency conditions
        self._check_emergency_conditions()
        
        # Update market making quotes
        elapsed_quote_ms = (now - self._last_quote_time) * 1000
        if elapsed_quote_ms >= self.config.quote_refresh_interval_ms:
            self._update_quotes(now)
            self._last_quote_time = now

    def _calculate_obi(self) -> float | None:
        """
        Calculate Order Book Imbalance.
        
        OBI = (Bid_Volume - Ask_Volume) / (Bid_Volume + Ask_Volume)
        Positive OBI = more buyer interest (bullish)
        Negative OBI = more seller interest (bearish)
        
        Low-capital version uses fewer levels for faster signal.
        """
        if not self.book or not self.book.spread():
            return None
        
        bid_levels = self.config.obi_levels
        ask_levels = self.config.obi_levels
        
        # Get cumulative volume at each side
        bid_volume = Decimal(0)
        ask_volume = Decimal(0)
        
        bids = self.book.bids()
        asks = self.book.asks()
        
        for i, level in enumerate(bids):
            if i >= bid_levels:
                break
            bid_volume += Decimal(str(level.size()))
        
        for i, level in enumerate(asks):
            if i >= ask_levels:
                break
            ask_volume += Decimal(str(level.size()))
        
        total_volume = bid_volume + ask_volume
        if total_volume == 0:
            return None
        
        obi = float((bid_volume - ask_volume) / total_volume)
        return obi

    def _update_obi_ema(self) -> None:
        """Update exponential moving average of OBI."""
        if not self._obi_history:
            return
        
        alpha = 2.0 / (self.config.obi_ema_period + 1)
        current_obi = self._obi_history[-1]
        
        if len(self._obi_history) == 1:
            self._obi_ema = current_obi
        else:
            self._obi_ema = (current_obi * alpha) + (self._obi_ema * (1 - alpha))

    def _update_quotes(self, now: float) -> None:
        """Update market making quotes based on OBI and inventory."""
        if not self.book or not self.book.spread():
            return
        
        # Get best bid/ask
        best_bid = self.book.best_bid_price()
        best_ask = self.book.best_ask_price()
        
        if best_bid is None or best_ask is None:
            return
        
        self._last_bid_price = best_bid
        self._last_ask_price = best_ask
        
        mid = (float(best_bid) + float(best_ask)) / 2.0
        
        # Calculate spread (tighter for low capital)
        spread_bps = max(
            self.config.min_spread_bps,
            min(self.config.max_spread_bps, int(self._calculate_spread_bps())),
        )
        spread = spread_bps * mid / 10000
        
        # Apply inventory skew (more aggressive)
        inventory_usd = float(self._net_qty) * mid
        skew_bps = (inventory_usd / 1000.0) * self.config.inventory_skew_bps
        skew = skew_bps * mid / 10000
        
        # Quote prices
        quote_bid = self.instrument.make_price(mid - (spread / 2) + skew)
        quote_ask = self.instrument.make_price(mid + (spread / 2) - skew)
        
        # Check OBI signal (more aggressive threshold)
        should_quote_bid = self._obi_ema > -self.config.obi_entry_threshold
        should_quote_ask = self._obi_ema < self.config.obi_entry_threshold
        
        # Cancel old orders
        if self._active_bid_order_id:
            order = self.cache.order(self._active_bid_order_id)
            if order:
                self.cancel_order(order)
            self._active_bid_order_id = None
        
        if self._active_ask_order_id:
            order = self.cache.order(self._active_ask_order_id)
            if order:
                self.cancel_order(order)
            self._active_ask_order_id = None
        
        # Place new orders
        if should_quote_bid and self._net_qty < self.config.max_position_qty:
            bid_order = self.order_factory.limit(
                instrument_id=self.instrument.id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(float(self.config.base_qty)),
                price=quote_bid,
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
            self.submit_order(bid_order)
            self._active_bid_order_id = bid_order.client_order_id
            self._order_count += 1
        
        if should_quote_ask and self._net_qty > -self.config.max_position_qty:
            ask_order = self.order_factory.limit(
                instrument_id=self.instrument.id,
                order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(float(self.config.base_qty)),
                price=quote_ask,
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
            self.submit_order(ask_order)
            self._active_ask_order_id = ask_order.client_order_id
            self._order_count += 1
        
        # Log quote metrics (more frequently for debugging)
        if self._order_count % 20 == 0:  # Log every 20 orders (vs 50)
            self.log.info(
                f"Quotes: Bid={quote_bid} Ask={quote_ask} | "
                f"OBI={self._obi_ema:.3f} | Position={self._net_qty} BTC | "
                f"Spread={spread_bps}bps",
            )

    def _calculate_spread_bps(self) -> float:
        """Calculate dynamic spread based on volatility and state."""
        # Base spread (tighter for low capital)
        spread = float(self.config.min_spread_bps)
        
        # Add volatility component (less sensitive)
        if len(self._obi_history) > 3:  # Faster threshold (vs 5)
            obi_volatility = self._calculate_obi_volatility()
            spread += obi_volatility * self.config.volatility_multiplier
        
        return spread

    def _calculate_obi_volatility(self) -> float:
        """Calculate rolling volatility of OBI."""
        if len(self._obi_history) < 2:
            return 0.0
        
        values = list(self._obi_history)
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5

    def _check_emergency_conditions(self) -> None:
        """Check for emergency conditions requiring shutdown."""
        # Check max loss (stricter for low capital)
        if self._unrealized_loss < self.config.emergency_liquidation_loss_usd:
            if not self._max_loss_trigger:
                self.log.error(
                    f"MAX LOSS TRIGGERED: {self._unrealized_loss:.2f} USD (20% of capital)",
                    LogColor.RED,
                )
                self._max_loss_trigger = True
                self._emergency_flatten()
        
        # Check position age (much shorter for low capital)
        if self._net_qty != 0 and time.time() - self._position_entry_time > self.config.max_inventory_age_seconds:
            self.log.warning(
                f"Position age exceeded {self.config.max_inventory_age_seconds}s, flattening (FAST TURNOVER)",
                LogColor.YELLOW,
            )
            self._emergency_flatten()

    def _emergency_flatten(self) -> None:
        """Emergency position flattening."""
        self.cancel_all_orders(self.instrument.id)
        
        if self._net_qty > 0:
            order = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=OrderSide.SELL,
                quantity=Quantity.from_str(str(abs(self._net_qty))),
            )
            self.submit_order(order)
        elif self._net_qty < 0:
            order = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_str(str(abs(self._net_qty))),
            )
            self.submit_order(order)

    def on_order_filled(self, fill) -> None:
        """Update position on order fills."""
        self._trades_count += 1
        
        # Update net quantity
        if fill.order_side == OrderSide.BUY:
            self._net_qty += fill.last_qty
            self._maker_fills += 1 if fill.liquidity_side.name == "MAKER" else 0
        else:
            self._net_qty -= fill.last_qty
            self._taker_fills += 1 if fill.liquidity_side.name == "TAKER" else 0
        
        # Update position entry time
        if self._net_qty != 0:
            self._position_entry_time = time.time()
        
        # Log fill
        self.log.info(
            f"Fill: {fill.order_side.name} {fill.last_qty} @ {fill.last_px} | "
            f"Position={self._net_qty} | Liquidity={fill.liquidity_side.name}",
        )

    def on_stop(self) -> None:
        """Cleanup on strategy stop."""
        self.cancel_all_orders(self.instrument.id)
        
        # Final metrics
        self.log.info(
            f"\n{'='*60}\nFinal Low-Capital Strategy Metrics:\n{'='*60}",
        )
        self.log.info(f"Total Trades: {self._trades_count}")
        self.log.info(f"Maker Fills: {self._maker_fills} ({self._maker_fills/max(1, self._trades_count)*100:.1f}%)")
        self.log.info(f"Taker Fills: {self._taker_fills} ({self._taker_fills/max(1, self._trades_count)*100:.1f}%)")
        self.log.info(f"Final Position: {self._net_qty} BTC")
        self.log.info(f"{'='*60}")
