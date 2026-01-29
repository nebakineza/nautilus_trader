import math
import time
import numpy as np
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.objects import Quantity, Price
from nautilus_trader.trading.strategy import Strategy


class ProfessionalHFTConfig(StrategyConfig):
    instrument_id: str = "BTCUSDT-SPOT.BYBIT"
    base_qty: float = 0.001
    num_layers: int = 5

    # Advanced Risk Params
    max_notional_exposure: float = 10000.0  # $10k Max position value
    inventory_alpha: float = 0.2            # Skew aggression
    toxic_flow_threshold: float = 2.5       # OFI ratio to trigger pull-back

    # Timing
    update_interval_ms: int = 20            # 50Hz update (Institutional speed)


class InstitutionalHFT(Strategy):
    def __init__(self, config: ProfessionalHFTConfig):
        super().__init__(config)
        self.ofi_accumulator = 0.0
        self._last_update = time.time()
        self._last_price = None

    def on_start(self):
        from nautilus_trader.model.identifiers import InstrumentId
        instrument_id = InstrumentId.from_str(self.config.instrument_id)
        self.instrument = self.cache.instrument(instrument_id)
        if not self.instrument:
            self.log.error("Instrument not found in cache; stopping strategy")
            self.stop()
            return
        # Subscribe to trade ticks only (no quotes available)
        self.subscribe_trade_ticks(instrument_id)

    def on_trade_tick(self, trade):
        # Update OFI accumulator
        try:
            side_mult = 1 if trade.side == OrderSide.BUY else -1
        except Exception:
            side_mult = 1 if getattr(trade, "side", 1) == 1 else -1
        self.ofi_accumulator += float(trade.size) * side_mult
        
        # Update last price and trigger order placement
        self._last_price = float(trade.price)
        now = time.time()
        if (now - self._last_update) * 1000 < self.config.update_interval_ms:
            return
        self._last_update = now
        
        # Process order placement
        if self._last_price is not None:
            self._process_orders(self._last_price)

    def _process_orders(self, mid_price):
        # 1. EMERGENCY CHECK: Inventory vs Notional Cap
        from nautilus_trader.model.identifiers import InstrumentId
        instrument_id = InstrumentId.from_str(self.config.instrument_id)
        # Get all positions and find ours
        positions = self.cache.positions_open()
        net_qty = 0.0
        for pos in positions:
            if pos.instrument_id == instrument_id:
                net_qty = float(pos.signed_qty)
                break
        
        notional = abs(net_qty * float(mid_price))

        if notional > self.config.max_notional_exposure:
            self.emergency_liquidate(net_qty)
            return

        # 2. TOXIC FLOW PROTECTION
        toxic_skew = self.ofi_accumulator * 0.00001
        # decay OFI
        self.ofi_accumulator *= 0.9

        # 3. INVENTORY SKEW
        inv_skew = -net_qty * self.config.inventory_alpha

        total_skew = inv_skew + toxic_skew

        self._submit_pro_ladder(float(mid_price), total_skew)

    def _submit_pro_ladder(self, mid, skew):
        from nautilus_trader.model.identifiers import InstrumentId
        instrument_id = InstrumentId.from_str(self.config.instrument_id)
        
        # Use market orders based on skew direction
        # Lower threshold to generate more trades for backtesting
        
        if abs(skew) > 0.001:  # Lower threshold for more activity
            if skew > 0:  # Bearish skew - sell
                side = OrderSide.SELL
            else:  # Bullish skew - buy  
                side = OrderSide.BUY
            
            # Place market order
            self.submit_order(self.order_factory.market(
                instrument_id=instrument_id,
                order_side=side,
                quantity=Quantity.from_str(str(self.config.base_qty))
            ))
        else:  # Neutral - close existing position if any
            self.cancel_all_orders(instrument_id)

    def emergency_liquidate(self, qty):
        from nautilus_trader.model.identifiers import InstrumentId
        instrument_id = InstrumentId.from_str(self.config.instrument_id)
        self.log.error("MAX EXPOSURE BREACHED - LIQUIDATING")
        self.cancel_all_orders(instrument_id)
        side = OrderSide.SELL if qty > 0 else OrderSide.BUY
        self.submit_order(self.order_factory.market(
            instrument_id=instrument_id,
            order_side=side,
            quantity=Quantity.from_str(str(abs(qty)))
        ))

    def _create_order(self, side, price):
        from nautilus_trader.model.identifiers import InstrumentId
        instrument_id = InstrumentId.from_str(self.config.instrument_id)
        try:
            p = self.instrument.make_price(price)
        except Exception:
            p = Price.from_float(float(price)) if hasattr(Price, 'from_float') else price
        return self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=side,
            quantity=Quantity.from_str(str(self.config.base_qty)),
            price=p,
            time_in_force=TimeInForce.GTC,
            post_only=False  # No order book data available
        )
