from __future__ import annotations

from decimal import Decimal
from typing import Optional

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from strategy.statarb.kalman import OnlineKalmanFilter


class StatArbPairsConfig(StrategyConfig, frozen=True, kw_only=True):
    instrument_a_id: InstrumentId
    instrument_b_id: InstrumentId
    
    # Bar resolution
    bar_type: str = "15-MINUTE-BID" # e.g. "1-MINUTE-MID" or "15-MINUTE-BID"
    
    # Position Sizing
    notional_per_trade: Decimal = Decimal("100.0") # USDT Amount per leg
    max_notional_total: Decimal = Decimal("500.0")
    
    # Kalman Parameters
    kalman_delta: float = 1e-4
    kalman_vt: float = 1e-3
    
    # Entry/Exit (Z-Score)
    entry_z_score: float = 2.0
    exit_z_score: float = 0.0
    stop_loss_z_score: float = 4.0
    
    # Safety
    min_spread_variance: float = 1e-6 # Avoid trading flat-lines

class StatArbPairsStrategy(Strategy):
    def __init__(self, config: StatArbPairsConfig) -> None:
        super().__init__(config)
        self.config: StatArbPairsConfig = config
        
        self.instrument_a: Optional[Instrument] = None
        self.instrument_b: Optional[Instrument] = None
        
        self.kalman = OnlineKalmanFilter(delta=config.kalman_delta, vt=config.kalman_vt)
        
        # State
        self.latest_bar_a: Optional[Bar] = None
        self.latest_bar_b: Optional[Bar] = None
        self.current_z_score: float = 0.0
        self.current_beta: float = 0.0
        self.current_alpha: float = 0.0
        
        self.is_long_spread = False
        self.is_short_spread = False

    def on_start(self) -> None:
        self.instrument_a = self.cache.instrument(self.config.instrument_a_id)
        self.instrument_b = self.cache.instrument(self.config.instrument_b_id)
        
        if not self.instrument_a or not self.instrument_b:
            self.log.error("Missing instruments for StatArb")
            self.stop()
            return
            
        self.subscribe_bars(self.config.instrument_a_id, BarType.from_str(self.config.bar_type))
        self.subscribe_bars(self.config.instrument_b_id, BarType.from_str(self.config.bar_type))
        
        self.log.info(f"StatArb Started: {self.config.instrument_a_id} vs {self.config.instrument_b_id}")

    def on_bar(self, bar: Bar) -> None:
        # We need synchronization implicitly or via checking timestamps
        # Simplified: We update state when we have 'fresh' bars for both
        
        if bar.bar_type.instrument_id == self.config.instrument_a_id:
            self.latest_bar_a = bar
        elif bar.bar_type.instrument_id == self.config.instrument_b_id:
            self.latest_bar_b = bar
            
        # Check alignment (roughly same time window)
        if self.latest_bar_a and self.latest_bar_b:
            if self.latest_bar_a.ts_init == self.latest_bar_b.ts_init:
                self._process_pair_update()

    def _process_pair_update(self):
        # Extract prices (Close)
        price_a = float(self.latest_bar_a.close)
        price_b = float(self.latest_bar_b.close)
        
        # 1. Update Kalman Filter
        # y = A, x = B
        theta, error, sqrt_S = self.kalman.update(price_a, price_b)
        self.current_alpha = self.kalman.alpha
        self.current_beta = self.kalman.beta
        
        # 2. Calculate Z-Score
        # Z = Error / sqrt(S)  (Standardized innovation)
        # S is the variance of the prediction error
        if sqrt_S > self.config.min_spread_variance:
            self.current_z_score = error / sqrt_S
        else:
            self.current_z_score = 0.0
            
        self.log.info(
            f"Z: {self.current_z_score:.4f} | Beta: {self.current_beta:.4f} | A: {price_a} | B: {price_b}", 
            color=LogColor.CYAN
        )
        
        # 3. Logic
        self._check_signals()

    def _check_signals(self):
        z = self.current_z_score
        
        # -- ENTRIES --
        # Short Spread: A is too high vs B -> Sell A, Buy B
        if z > self.config.entry_z_score and not self.is_short_spread:
            if self.is_long_spread:
                self._exit_positions("Reversal Short")
            self.log.info(f"ENTRY SHORT SPREAD (Z={z:.2f})", color=LogColor.MAGENTA)
            self._execute_spread(side_a=OrderSide.SELL)
            self.is_short_spread = True

        # Long Spread: A is too low vs B -> Buy A, Sell B
        elif z < -self.config.entry_z_score and not self.is_long_spread:
            if self.is_short_spread:
                self._exit_positions("Reversal Long")
            self.log.info(f"ENTRY LONG SPREAD (Z={z:.2f})", color=LogColor.MAGENTA)
            self._execute_spread(side_a=OrderSide.BUY)
            self.is_long_spread = True
            
        # -- EXITS (Mean Reversion) --
        # If we are Short Spread (Z was > 2), we exit when Z crosses 0 (or config level)
        elif self.is_short_spread and z <= self.config.exit_z_score:
            self.log.info(f"EXIT SHORT SPREAD - REVERSION (Z={z:.2f})", color=LogColor.GREEN)
            self._exit_positions("Take Profit")
            
        # If we are Long Spread (Z was < -2), we exit when Z crosses 0
        elif self.is_long_spread and z >= -self.config.exit_z_score:
            self.log.info(f"EXIT LONG SPREAD - REVERSION (Z={z:.2f})", color=LogColor.GREEN)
            self._exit_positions("Take Profit")

        # -- STOP LOSS --
        if abs(z) > self.config.stop_loss_z_score:
             self.log.warn(f"STOP LOSS HIT (Z={z:.2f})")
             self._exit_positions("Stop Loss")

    def _execute_spread(self, side_a: OrderSide):
        """
        Execute the pair trade.
        Side A determines direction. Side B is opposite.
        Beta determines ratio.
        """
        # Close any existing first? (Managed by signals)
        
        # Calculate quantities
        # We target a fixed dollar notional for Asset A
        notional = self.config.notional_per_trade
        price_a = self.latest_bar_a.close.as_decimal()
        price_b = self.latest_bar_b.close.as_decimal()
        
        if price_a == 0 or price_b == 0:
            return

        qty_a_dec = notional / price_a
        
        # Hedge Ratio: Qty_A * Price_A  vs  Qty_B * Price_B
        # Model: P_A = beta * P_B
        # To be dollar neutral: Value_A = Value_B (approx)
        # Actually Stat Arb technically hedges the *variance*, so Qty_B = Qty_A * Beta
        # Let's use Beta hedging:
        # Qty_B = Qty_A * Beta
        
        beta = Decimal(str(abs(self.current_beta)))
        if beta == 0:
            return
            
        qty_b_dec = qty_a_dec * beta
        
        qty_a = self.instrument_a.make_qty(qty_a_dec)
        qty_b = self.instrument_b.make_qty(qty_b_dec)
        
        side_b = OrderSide.SELL if side_a == OrderSide.BUY else OrderSide.BUY
        
        self.submit_order(self.order_factory.market(
            instrument_id=self.config.instrument_a_id,
            order_side=side_a,
            quantity=qty_a
        ))
        
        self.submit_order(self.order_factory.market(
            instrument_id=self.config.instrument_b_id,
            order_side=side_b,
            quantity=qty_b
        ))

    def _exit_positions(self, reason: str):
        self.close_all_positions(self.config.instrument_a_id)
        self.close_all_positions(self.config.instrument_b_id)
        self.is_long_spread = False
        self.is_short_spread = False
        self.log.info(f"Positions Closed: {reason}")
