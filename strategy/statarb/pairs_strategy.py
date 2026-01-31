from __future__ import annotations

from decimal import Decimal
import time
import uuid
from typing import Optional

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from strategy.statarb.kalman import OnlineKalmanFilter
from strategy.metrics.questdb_writer import QuestDbILPWriter


class StatArbPairsConfig(StrategyConfig, frozen=True, kw_only=True):
    instrument_a_id: InstrumentId
    instrument_b_id: InstrumentId

    # Bar resolution (INTERNAL bars aggregate from trade ticks)
    bar_interval: str = "15-MINUTE"
    bar_price_type: str = "LAST"
    bar_source: str = "INTERNAL"
    
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

    # Fees & profit floor (bps)
    taker_fee_bps: Decimal = Decimal("7.5")
    min_profit_bps: Decimal = Decimal("10.0")
    
    # Safety
    min_spread_variance: float = 1e-6 # Avoid trading flat-lines

    # Inventory rebalance (ammo reset)
    inventory_rebalance_interval_secs: int = 86_400
    inventory_target_multiple: Decimal = Decimal("3.0")
    inventory_tolerance_multiple: Decimal = Decimal("1.0")

class StatArbPairsStrategy(Strategy):
    def __init__(self, config: StatArbPairsConfig) -> None:
        super().__init__(config)
        
        self.instrument_a: Optional[Instrument] = None
        self.instrument_b: Optional[Instrument] = None
        
        self.kalman = OnlineKalmanFilter(delta=config.kalman_delta, vt=config.kalman_vt)
        
        # State
        self.latest_bar_a: Optional[Bar] = None
        self.latest_bar_b: Optional[Bar] = None
        self.current_z_score: float = 0.0
        self.current_beta: float = 0.0
        self.current_alpha: float = 0.0
        self.current_spread_bps: Decimal = Decimal("0")
        self._metrics = QuestDbILPWriter.from_env("StatArb")
        
        self.is_long_spread = False
        self.is_short_spread = False
        self.last_inventory_rebalance_ts: float = 0.0

    def on_start(self) -> None:
        self.instrument_a = self.cache.instrument(self.config.instrument_a_id)
        self.instrument_b = self.cache.instrument(self.config.instrument_b_id)
        
        if not self.instrument_a or not self.instrument_b:
            self.log.error("Missing instruments for StatArb")
            self.stop()
            return
            
        bar_type_a = BarType.from_str(
            f"{self.config.instrument_a_id}-{self.config.bar_interval}-{self.config.bar_price_type}-{self.config.bar_source}"
        )
        bar_type_b = BarType.from_str(
            f"{self.config.instrument_b_id}-{self.config.bar_interval}-{self.config.bar_price_type}-{self.config.bar_source}"
        )

        # Ensure trade ticks are streaming for INTERNAL bar aggregation
        if self.config.bar_source == "INTERNAL":
            self.subscribe_trade_ticks(self.config.instrument_a_id)
            self.subscribe_trade_ticks(self.config.instrument_b_id)

        self.subscribe_bars(bar_type_a)
        self.subscribe_bars(bar_type_b)
        
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

        self.current_spread_bps = self._calculate_spread_bps(price_a, price_b)
        
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

        self._metrics.send(
            table="statarb_signals",
            tags={
                "strategy": "StatArb",
                "pair": f"{self.config.instrument_a_id.symbol.value}-{self.config.instrument_b_id.symbol.value}",
                "venue": self.config.instrument_a_id.venue.value,
            },
            fields={
                "z_score": self.current_z_score,
                "beta": self.current_beta,
                "alpha": self.current_alpha,
                "spread_bps": float(self.current_spread_bps),
                "price_a": price_a,
                "price_b": price_b,
            },
            ts_ns=int(self.latest_bar_a.ts_init),
        )

        self._check_inventory_rebalance()
        
        # 3. Logic
        self._check_signals()

    def _check_signals(self):
        z = self.current_z_score
        spread_ok = self._spread_meets_fee_floor()
        
        # -- ENTRIES --
        # Short Spread: A is too high vs B -> Sell A, Buy B
        if z > self.config.entry_z_score and not self.is_short_spread and spread_ok:
            if self.is_long_spread:
                self._exit_positions("Reversal Short")
            self.log.info(f"ENTRY SHORT SPREAD (Z={z:.2f})", color=LogColor.MAGENTA)
            if self._execute_spread(side_a=OrderSide.SELL):
                self.is_short_spread = True

        # Long Spread: A is too low vs B -> Buy A, Sell B
        elif z < -self.config.entry_z_score and not self.is_long_spread and spread_ok:
            if self.is_short_spread:
                self._exit_positions("Reversal Long")
            self.log.info(f"ENTRY LONG SPREAD (Z={z:.2f})", color=LogColor.MAGENTA)
            if self._execute_spread(side_a=OrderSide.BUY):
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
            self.log.warning(f"STOP LOSS HIT (Z={z:.2f})")
            self._exit_positions("Stop Loss")

    def _execute_spread(self, side_a: OrderSide) -> bool:
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
            return False

        qty_a_dec = notional / price_a
        
        # Hedge Ratio: Qty_A * Price_A  vs  Qty_B * Price_B
        # Model: P_A = beta * P_B
        # To be dollar neutral: Value_A = Value_B (approx)
        # Actually Stat Arb technically hedges the *variance*, so Qty_B = Qty_A * Beta
        # Let's use Beta hedging:
        # Qty_B = Qty_A * Beta
        
        beta = Decimal(str(abs(self.current_beta)))
        if beta == 0:
            return False
            
        qty_b_dec = qty_a_dec * beta
        
        qty_a = self.instrument_a.make_qty(qty_a_dec)
        qty_b = self.instrument_b.make_qty(qty_b_dec)
        
        side_b = OrderSide.SELL if side_a == OrderSide.BUY else OrderSide.BUY

        if not self._has_required_balances(
            qty_a=qty_a,
            price_a=price_a,
            side_a=side_a,
            qty_b=qty_b,
            price_b=price_b,
            side_b=side_b,
        ):
            self.log.warning("Skipping spread: insufficient balance for one or both legs")
            return False
        
            trade_group_id = str(uuid.uuid4())[:8]
            order_a = self.order_factory.market(
                instrument_id=self.config.instrument_a_id,
                order_side=side_a,
                quantity=qty_a,
                tags=[f"group:{trade_group_id}", "leg:a"],
            )
            order_b = self.order_factory.market(
                instrument_id=self.config.instrument_b_id,
                order_side=side_b,
                quantity=qty_b,
                tags=[f"group:{trade_group_id}", "leg:b"],
            )

            order_list = self.order_factory.create_list([order_a, order_b])
            self.submit_order_list(order_list)

        return True

    def on_order_rejected(self, event: OrderRejected) -> None:
        tags = getattr(event, "tags", None) or []
        if not isinstance(tags, (list, tuple)):
            return

        group_id = None
        role = None
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("group:"):
                group_id = tag.split("group:", 1)[1]
            if tag == "leg:a":
                role = "leg_a"
            elif tag == "leg:b":
                role = "leg_b"

        if group_id is None:
            return

        self.log.error(
            f"LEG REJECTED: {role or 'unknown'} (Group: {group_id}). Engaging Fail-Safe.",
            LogColor.RED,
        )
        self.log.warning(
            "FAIL-SAFE: Closing all positions to neutralize risk.",
            LogColor.RED,
        )
        self.close_all_positions(self.config.instrument_a_id)
        self.close_all_positions(self.config.instrument_b_id)
        self.is_long_spread = False
        self.is_short_spread = False

    def _check_inventory_rebalance(self) -> None:
        now = time.time()
        if now - self.last_inventory_rebalance_ts < self.config.inventory_rebalance_interval_secs:
            return

        if self.instrument_a is None or self.instrument_b is None:
            return

        price_a = self._latest_price(self.config.instrument_a_id)
        price_b = self._latest_price(self.config.instrument_b_id)
        if price_a is None or price_b is None:
            return

        val_a = self._get_value_usd(self.instrument_a, price_a)
        val_b = self._get_value_usd(self.instrument_b, price_b)
        if val_a is None or val_b is None:
            return

        target = self.config.notional_per_trade * self.config.inventory_target_multiple
        threshold = self.config.notional_per_trade * self.config.inventory_tolerance_multiple

        if abs(val_a - target) > threshold:
            diff = target - val_a
            self.log.info(f"Rebalancing A: Adjustment ${diff:.2f}", LogColor.YELLOW)
            self._execute_market_adjustment(self.instrument_a, diff, price_a)

        if abs(val_b - target) > threshold:
            diff = target - val_b
            self.log.info(f"Rebalancing B: Adjustment ${diff:.2f}", LogColor.YELLOW)
            self._execute_market_adjustment(self.instrument_b, diff, price_b)

        self.last_inventory_rebalance_ts = now

    def _latest_price(self, instrument_id: InstrumentId) -> Decimal | None:
        if instrument_id == self.config.instrument_a_id and self.latest_bar_a is not None:
            return self.latest_bar_a.close.as_decimal()
        if instrument_id == self.config.instrument_b_id and self.latest_bar_b is not None:
            return self.latest_bar_b.close.as_decimal()
        ticker = self.cache.ticker(instrument_id)
        if ticker is None or ticker.last is None:
            return None
        return ticker.last.as_decimal()

    def _get_value_usd(self, instrument: Instrument, price: Decimal) -> Decimal | None:
        account = self.cache.account_for_venue(self.config.instrument_a_id.venue)
        if account is None:
            accounts = self.cache.accounts()
            if accounts:
                account = next(iter(accounts.values()))
        if account is None:
            return None

        balance = account.balance_total(instrument.base_currency)
        if balance is None:
            return None
        qty = balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)
        return qty * price

    def _execute_market_adjustment(self, instrument: Instrument, diff_usd: Decimal, price: Decimal) -> None:
        if price <= 0:
            return
        side = OrderSide.BUY if diff_usd > 0 else OrderSide.SELL
        qty_dec = abs(diff_usd) / price
        qty = instrument.make_qty(qty_dec)
        if qty.as_decimal() <= 0:
            return

        if not self._has_required_balances(
            qty_a=qty,
            price_a=price,
            side_a=side,
            qty_b=Quantity.from_int(0),
            price_b=Decimal("0"),
            side_b=OrderSide.BUY,
        ):
            self.log.warning("Skipping inventory rebalance: insufficient balance")
            return

        self.submit_order(
            self.order_factory.market(
                instrument_id=instrument.id,
                order_side=side,
                quantity=qty,
                time_in_force=TimeInForce.GTC,
            )
        )

    def _has_required_balances(
        self,
        qty_a: Quantity,
        price_a: Decimal,
        side_a: OrderSide,
        qty_b: Quantity,
        price_b: Decimal,
        side_b: OrderSide,
    ) -> bool:
        account = self.cache.account_for_venue(self.config.instrument_a_id.venue)
        if account is None:
            accounts = self.cache.accounts()
            if accounts:
                account = next(iter(accounts.values()))
        if account is None:
            return False

        def has_balance(instrument: Instrument, side: OrderSide, qty: Quantity, price: Decimal) -> bool:
            if side == OrderSide.SELL:
                balance = account.balance_free(instrument.base_currency)
                if balance is None:
                    return False
                available = balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)
                return available >= qty.as_decimal()
            balance = account.balance_free(instrument.quote_currency)
            if balance is None:
                return False
            available = balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)
            required = qty.as_decimal() * price
            return available >= required

        return (
            has_balance(self.instrument_a, side_a, qty_a, price_a)
            and has_balance(self.instrument_b, side_b, qty_b, price_b)
        )

    def _has_balance_for_leg(self, instrument: Instrument, side: OrderSide, qty: Quantity, price: Decimal) -> bool:
        account = self.cache.account_for_venue(self.config.instrument_a_id.venue)
        if account is None:
            accounts = self.cache.accounts()
            if accounts:
                account = next(iter(accounts.values()))
        if account is None:
            return False

        if side == OrderSide.SELL:
            balance = account.balance_free(instrument.base_currency)
            if balance is None:
                return False
            available = balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)
            return available >= qty.as_decimal()

        balance = account.balance_free(instrument.quote_currency)
        if balance is None:
            return False
        available = balance.as_decimal() if hasattr(balance, "as_decimal") else Decimal(balance)
        required = qty.as_decimal() * price
        return available >= required

    def _exit_positions(self, reason: str):
        self.close_all_positions(self.config.instrument_a_id)
        self.close_all_positions(self.config.instrument_b_id)
        self.is_long_spread = False
        self.is_short_spread = False
        self.log.info(f"Positions Closed: {reason}")

    def on_event(self, event) -> None:
        if not hasattr(event, "last_qty") or not hasattr(event, "order_side"):
            return

        price = None
        if hasattr(event, "last_px") and event.last_px is not None:
            price = event.last_px.as_decimal()
        elif hasattr(event, "price") and event.price is not None:
            price = event.price.as_decimal()

        if price is None:
            return

        ts_event = getattr(event, "ts_event", None)
        ts_ns = int(ts_event) if ts_event is not None else int(time.time() * 1_000_000_000)
        commission = getattr(event, "commission", None)
        commission_val = commission.as_decimal() if hasattr(commission, "as_decimal") else commission

        if not self._has_balance_for_leg(instrument, side, qty, price):
                "liquidity": getattr(liquidity_side, "name", None),
            },
            fields={
                "qty": event.last_qty.as_decimal(),
                "price": price,
                "commission": commission_val,
            },
            ts_ns=ts_ns,
        )

    def _calculate_spread_bps(self, price_a: float, price_b: float) -> Decimal:
        if price_a <= 0 or price_b <= 0:
            return Decimal("0")

        beta = Decimal(str(abs(self.current_beta)))
        if beta <= Decimal("0"):
            return Decimal("0")

        implied_a = beta * Decimal(str(price_b))
        spread = abs(Decimal(str(price_a)) - implied_a)
        return (spread / Decimal(str(price_a))) * Decimal("10000")

    def _spread_meets_fee_floor(self) -> bool:
        floor = (self.config.taker_fee_bps * Decimal("4")) + self.config.min_profit_bps
        if self.current_spread_bps < floor:
            return False
        return True
