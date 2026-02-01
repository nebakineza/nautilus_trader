"""Lead-Lag Market Maker v003 Primer (VIP1 acquisition profile)."""

from __future__ import annotations

import time
from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.enums import OrderSide

from strategy.lead_lag_bybit_binance_mm_v003 import LeadLagMMv3, LeadLagMMv3Config
from strategy.metrics.questdb_writer import QuestDbILPWriter


class LeadLagMMv3PrimerConfig(LeadLagMMv3Config, frozen=True, kw_only=True):
    """VIP1 acquisition defaults (volume-focused, fee-neutral target)."""

    spread_bps: Decimal = Decimal("16.0")
    min_profit_bps: Decimal = Decimal("0.0")
    maker_fee_bps: Decimal = Decimal("7.5")
    ofi_enabled: bool = True
    ofi_max_bps: Decimal = Decimal("15.0")
    internal_price_delta_limit: Decimal = Decimal("8.0")

    # Daily loss kill-switch (volume run safety)
    daily_loss_limit_usdt: Decimal = Decimal("20.0")
    daily_killswitch_reset_secs: int = 86_400


class LeadLagMMv3Primer(LeadLagMMv3):
    """Lead-Lag MM v003 with VIP1 acquisition defaults."""

    def __init__(self, config: LeadLagMMv3PrimerConfig) -> None:
        super().__init__(config)
        self._metrics = QuestDbILPWriter.from_env("LLMMv3Primer")
        self._daily_realized_start: Decimal | None = None
        self._daily_reset_ts: float = 0.0

    def _emit_account_snapshot(self, now_ns: int) -> None:
        if self.follower_instrument is None:
            return

        account = self.cache.account_for_venue(self.config.follower_instrument_id.venue)
        if account is None:
            return

        base_currency = self.follower_instrument.base_currency
        quote_currency = self.follower_instrument.quote_currency

        base_balance = account.balance_total(base_currency)
        quote_balance = account.balance_total(quote_currency)
        if base_balance is None or quote_balance is None:
            return

        base_qty = base_balance.as_decimal() if hasattr(base_balance, "as_decimal") else Decimal(base_balance)
        quote_qty = quote_balance.as_decimal() if hasattr(quote_balance, "as_decimal") else Decimal(quote_balance)
        mid = self.follower_mid if self.follower_mid is not None else Decimal("0")
        equity = quote_qty + (base_qty * mid)

        self._metrics.send(
            table="live_account_snapshot",
            tags={
                "strategy": "LLMMv3Primer",
                "venue": self.config.follower_instrument_id.venue.value,
                "symbol": self.config.follower_instrument_id.symbol.value,
            },
            fields={
                "base_qty": base_qty,
                "quote_qty": quote_qty,
                "mid": mid,
                "equity": equity,
                "equity_usd": equity,
                "net_position": self._net_position,
            },
            ts_ns=now_ns,
        )

    def on_event(self, event) -> None:
        if hasattr(event, "last_qty") and hasattr(event, "order_side"):
            if event.order_side == OrderSide.BUY:
                self._net_position += event.last_qty.as_decimal()
            elif event.order_side == OrderSide.SELL:
                self._net_position -= event.last_qty.as_decimal()

            price = None
            if hasattr(event, "last_px") and event.last_px is not None:
                price = event.last_px.as_decimal()
            elif hasattr(event, "price") and event.price is not None:
                price = event.price.as_decimal()

            if price is not None:
                ts_event = getattr(event, "ts_event", None)
                ts_ns = self._event_ts_ns_from_event(ts_event)
                self._markout_pending.append((ts_ns, event.order_side, price))

                commission = getattr(event, "commission", None)
                commission_val = commission.as_decimal() if hasattr(commission, "as_decimal") else commission
                liquidity_side = getattr(event, "liquidity_side", None)
                self._metrics.send(
                    table="live_fills",
                    tags={
                        "strategy": "LLMMv3Primer",
                        "venue": self.config.follower_instrument_id.venue.value,
                        "symbol": self.config.follower_instrument_id.symbol.value,
                        "side": event.order_side.name,
                        "liquidity": getattr(liquidity_side, "name", None),
                    },
                    fields={
                        "qty": event.last_qty.as_decimal(),
                        "price": price,
                        "commission": commission_val,
                    },
                    ts_ns=ts_ns,
                )

    def _check_killswitch(self) -> bool:
        if super()._check_killswitch():
            return True

        now = time.time()
        if self._daily_reset_ts == 0.0 or (now - self._daily_reset_ts) >= self.config.daily_killswitch_reset_secs:
            self._daily_realized_start = self._get_realized_pnl()
            self._daily_reset_ts = now
            self.log.info(
                f"DAILY KILLSWITCH ARMED: Realized PnL = {self._daily_realized_start} USDT",
                LogColor.YELLOW,
            )

        if self._daily_realized_start is None:
            return False

        current_realized = self._get_realized_pnl()
        daily_loss = self._daily_realized_start - current_realized
        if daily_loss > self.config.daily_loss_limit_usdt:
            self.log.error(
                f"DAILY KILLSWITCH TRIGGERED: Realized loss {daily_loss:.2f} > {self.config.daily_loss_limit_usdt:.2f}",
                LogColor.RED,
            )
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
            self._killswitch_triggered = True
            self.stop()
            return True

        return False
