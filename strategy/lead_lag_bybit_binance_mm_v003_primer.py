"""Lead-Lag Market Maker v003 Primer (VIP1 acquisition profile)."""

from __future__ import annotations

import time
from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from strategy.lead_lag_bybit_binance_mm_v003 import LeadLagMMv3, LeadLagMMv3Config


class LeadLagMMv3PrimerConfig(LeadLagMMv3Config, frozen=True, kw_only=True):
    """VIP1 acquisition defaults (volume-focused, fee-neutral target)."""

    spread_bps: Decimal = Decimal("16.0")
    min_profit_bps: Decimal = Decimal("0.0")
    maker_fee_bps: Decimal = Decimal("7.5")
    ofi_enabled: bool = True
    ofi_max_bps: Decimal = Decimal("15.0")

    # Daily loss kill-switch (volume run safety)
    daily_loss_limit_usdt: Decimal = Decimal("20.0")
    daily_killswitch_reset_secs: int = 86_400


class LeadLagMMv3Primer(LeadLagMMv3):
    """Lead-Lag MM v003 with VIP1 acquisition defaults."""

    def __init__(self, config: LeadLagMMv3PrimerConfig) -> None:
        super().__init__(config)
        self._daily_equity_start: Decimal | None = None
        self._daily_reset_ts: float = 0.0

    def _check_killswitch(self) -> bool:
        if super()._check_killswitch():
            return True

        now = time.time()
        if self._daily_reset_ts == 0.0 or (now - self._daily_reset_ts) >= self.config.daily_killswitch_reset_secs:
            equity = self._calculate_total_equity()
            if equity > Decimal("0"):
                self._daily_equity_start = equity
                self._daily_reset_ts = now
                self.log.info(
                    f"DAILY KILLSWITCH ARMED: Equity = {self._daily_equity_start} USDT",
                    LogColor.YELLOW,
                )

        if self._daily_equity_start is None:
            return False

        current_equity = self._calculate_total_equity()
        if current_equity <= Decimal("0"):
            return False

        daily_loss = self._daily_equity_start - current_equity
        if daily_loss > self.config.daily_loss_limit_usdt:
            self.log.error(
                f"DAILY KILLSWITCH TRIGGERED: Loss {daily_loss:.2f} > {self.config.daily_loss_limit_usdt:.2f}",
                LogColor.RED,
            )
            self.cancel_all_orders(self.config.follower_instrument_id, client_id=self.client_id)
            self._killswitch_triggered = True
            self.stop()
            return True

        return False
