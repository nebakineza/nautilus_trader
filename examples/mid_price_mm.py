import os
from decimal import Decimal
from nautilus_trader.common.component import LiveClock
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.message import Event
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity, Price
from nautilus_trader.trading.strategy import Strategy

# Bybit Specifics
from nautilus_trader.adapters.bybit.config import BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit.common import BybitProductType


class MidPriceMMConfig(StrategyConfig):
    instrument_id: str
    order_size: float = 0.001  # e.g., 0.001 BTC
    spread_bps: float = 5.0    # 5 basis points offset from mid


class MidPriceMM(Strategy):
    def __init__(self, config: MidPriceMMConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.order_size = Quantity.from_str(str(config.order_size))
        self.half_spread = config.spread_bps / 10000

    def on_start(self):
        """Called when the strategy starts."""
        self.instrument = self.cache.instrument(self.instrument_id)
        if not self.instrument:
            self.log.error(f"Instrument {self.instrument_id} not found in cache.")
            self.stop()
            return

        # Subscribe to L2 Order Book
        self.subscribe_order_book(self.instrument_id)

    def on_order_book(self, order_book):
        """Called every time the order book updates."""
        # 1. Get Mid Price
        mid_price = order_book.mid_price()
        if mid_price is None:
            return

        # 2. Calculate Quote Prices
        bid_price = mid_price * (1 - self.half_spread)
        ask_price = mid_price * (1 + self.half_spread)

        # 3. Simple Management: Cancel all and re-quote
        # In a real HFT scenario, you would use 'modify' or 'update'
        # to avoid losing queue priority, but cancel/replace is safer for a boilerplate.
        self.cancel_all_orders(self.instrument_id)

        # 4. Submit New Quotes
        self._place_limit_order(OrderSide.BUY, bid_price)
        self._place_limit_order(OrderSide.SELL, ask_price)

    def _place_limit_order(self, side: OrderSide, price: float):
        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=self.order_size,
            price=self.instrument.make_price(price),
            time_in_force=TimeInForce.GTC,
            post_only=True, # Critical: Ensure we are the Maker
        )
        self.submit_order(order)

    def on_stop(self):
        """Cleanup on stop."""
        self.cancel_all_orders(self.instrument_id)


def main():
    # 1. Setup Node
    node = TradingNode(trader_id="BYBIT-BOT-01")

    # 2. Configure Bybit Clients
    # Use environment variables for security!
    API_KEY = os.getenv("BYBIT_API_KEY", "your_key_here")
    API_SECRET = os.getenv("BYBIT_API_SECRET", "your_secret_here")

    data_config = BybitDataClientConfig(
        api_key=API_KEY,
        api_secret=API_SECRET,
        testnet=True  # Set to False for production
    )

    exec_config = BybitExecClientConfig(
        api_key=API_KEY,
        api_secret=API_SECRET,
        product_types=[BybitProductType.SPOT],
        testnet=True
    )

    node.add_data_client("BYBIT", data_config)
    node.add_exec_client("BYBIT", exec_config)

    # 3. Add Strategy
    # Symbol format for Bybit Spot in Nautilus: SYMBOL.BYBIT
    strategy_config = MidPriceMMConfig(
        instrument_id="BTCUSDT.BYBIT",
        order_size=0.0005,
        spread_bps=10
    )
    node.add_strategy(MidPriceMM(strategy_config))

    # 4. Run
    node.run()


if __name__ == "__main__":
    main()
