"""Lead-Lag Market Maker v005 Primer (Production Profile - Toxic Flow Avoidance)."""

from __future__ import annotations

import time
from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.enums import OrderSide

from strategy.lead_lag_bybit_binance_mm_v005 import LeadLagMMv5, LeadLagMMv5Config
from strategy.metrics.questdb_writer import QuestDbILPWriter


class LeadLagMMv5PrimerConfig(LeadLagMMv5Config, frozen=True, kw_only=True):
    """Production defaults with toxic flow avoidance and latency edge."""

    # === BASE SPREAD (widened for real-world profitability) ===
    spread_bps: Decimal = Decimal("60.0")  # Base 60 bps
    min_profit_bps: Decimal = Decimal("5.0")
    maker_fee_bps: Decimal = Decimal("10.0")  # VIP0 without MNT discount (API trades excluded)

    # === TOXIC FLOW AVOIDANCE ===
    ofi_enabled: bool = True
    ofi_widen_threshold: Decimal = Decimal("0.3")
    ofi_widen_bps: Decimal = Decimal("10.0")
    ofi_tighten_bps: Decimal = Decimal("5.0")
    
    volatility_enabled: bool = True
    volatility_base_threshold_bps: Decimal = Decimal("5.0")
    volatility_max_widen_bps: Decimal = Decimal("20.0")
    
    dynamic_lifetime_enabled: bool = True

    # === QUEUE OPTIMIZATION ===
    queue_tracking_enabled: bool = True
    inventory_urgency_enabled: bool = True

    # === LATENCY EDGE ===
    event_driven_requote: bool = True
    leader_update_min_interval_ms: int = 10

    # === SPREAD INTELLIGENCE ===
    binance_spread_tracking: bool = True
    competitive_monitoring: bool = True

    # === INVENTORY MANAGEMENT ===
    internal_price_delta_limit: Decimal = Decimal("8.0")

    # === REGIME DETECTION (Trending vs Ranging) ===
    regime_detection_enabled: bool = True
    regime_window_ticks: int = 100  # ~3-5 seconds of ticks for stability
    regime_trending_threshold: Decimal = Decimal("0.50")  # Above = trending
    regime_ranging_threshold: Decimal = Decimal("0.30")  # Below = ranging
    regime_trending_spread_multiplier: Decimal = Decimal("2.0")  # 2x spread when trending
    regime_trending_size_multiplier: Decimal = Decimal("0.5")  # 50% size when trending
    regime_pause_when_trending: bool = False  # Don't pause entirely, just widen
    log_regime_changes: bool = True

    # === KILL SWITCHES ===
    max_drawdown_pct: Decimal = Decimal("1.0")  # 100% = disabled
    daily_loss_limit_usdt: Decimal = Decimal("50.0")
    daily_killswitch_reset_secs: int = 86_400
    
    # === HARD POSITION CAP (NEW) ===
    hard_position_cap_enabled: bool = True  # Enforce max_position_qty strictly
    position_cap_buffer_pct: Decimal = Decimal("0.95")  # Block at 95% of max
    
    # === RUNAWAY FILL DETECTION (NEW) ===
    runaway_detection_enabled: bool = True
    runaway_window_fills: int = 10  # Rolling window of fills to check
    runaway_threshold_pct: Decimal = Decimal("0.80")  # 80% same-side = runaway
    runaway_pause_secs: int = 30  # Pause accumulating side for 30 seconds
    
    # === REALIZED SPREAD GUARDIAN ===
    # Ensures fees are ALWAYS covered by tracking realized P&L
    realized_spread_guardian_enabled: bool = True
    realized_spread_window_fills: int = 10
    realized_spread_min_bps: Decimal = Decimal("0.0")  # Just cover fees, 0 = break even
    realized_spread_widen_step_bps: Decimal = Decimal("10.0")
    realized_spread_max_widen_bps: Decimal = Decimal("100.0")
    realized_spread_cooldown_fills: int = 5
    log_realized_spread: bool = True

    # === LOGGING ===
    log_spread_adjustments: bool = True
    log_toxic_flow: bool = True
    fill_analytics_enabled: bool = True

class LeadLagMMv5Primer(LeadLagMMv5):
    """Lead-Lag MM v005 Primer with production defaults and daily loss limit.
    
    Features:
    1. Asymmetric OFI - widens toxic side, tightens favorable side
    2. Volatility-adaptive spread - widens during high volatility
    3. Dynamic quote lifetime - faster requotes when volatile
    4. Event-driven leader following - instant requotes on Binance changes
    5. Fill quality analytics - tracks toxicity metrics
    6. Daily loss limit killswitch
    7. **Realized Spread Guardian** - auto-widens spread if not covering fees
    """

    def __init__(self, config: LeadLagMMv5PrimerConfig) -> None:
        super().__init__(config)
        self._metrics = QuestDbILPWriter.from_env("LLMMv5Primer")
        self._daily_realized_start: Decimal | None = None
        self._daily_reset_ts: float = 0.0

    def _emit_account_snapshot(self, now_ns: int) -> None:
        """Override to use Primer metrics."""
        if self.follower_instrument is None or self._cached_account is None:
            return

        base_currency = self.follower_instrument.base_currency
        quote_currency = self.follower_instrument.quote_currency

        base_balance = self._cached_account.balance_total(base_currency)
        quote_balance = self._cached_account.balance_total(quote_currency)
        if base_balance is None or quote_balance is None:
            return

        base_qty = base_balance.as_decimal() if hasattr(base_balance, "as_decimal") else Decimal(base_balance)
        quote_qty = quote_balance.as_decimal() if hasattr(quote_balance, "as_decimal") else Decimal(quote_balance)
        mid = self.follower_mid if self.follower_mid is not None else Decimal("0")
        equity = quote_qty + (base_qty * mid)

        self._metrics.send(
            table="live_account_snapshot",
            tags={
                "strategy": "LLMMv5Primer",
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
                "ofi": self._current_ofi,
                "volatility_bps": self._current_volatility_bps,
                "markout_ema_bps": self._markout_ema_bps,
                "bid_spread_adj": self._ofi_bid_adjustment_bps + self._volatility_spread_adjustment_bps,
                "ask_spread_adj": self._ofi_ask_adjustment_bps + self._volatility_spread_adjustment_bps,
                "toxic_fill_count": self._toxic_fill_count,
                "good_fill_count": self._good_fill_count,
            },
            ts_ns=now_ns,
        )

    def on_event(self, event) -> None:
        """Override to use Primer metrics and track fills."""
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

                # Update quote budget
                if self._local_quote_budget is not None:
                    notional = event.last_qty.as_decimal() * price
                    if event.order_side == OrderSide.BUY:
                        self._local_quote_budget = max(self._local_quote_budget - notional, Decimal("0"))
                    elif event.order_side == OrderSide.SELL:
                        self._local_quote_budget += notional

                # Update WAP ledger
                commission = getattr(event, "commission", None)
                commission_val = commission.as_decimal() if hasattr(commission, "as_decimal") else commission
                self._update_wap_ledger(
                    event.order_side,
                    price,
                    event.last_qty.as_decimal(),
                    Decimal(str(commission_val)) if commission_val is not None else Decimal("0"),
                )

                # Fill analytics
                if self.config.fill_analytics_enabled:
                    submit_ts = self._bid_submit_ts_ns if event.order_side == OrderSide.BUY else self._ask_submit_ts_ns
                    time_in_book_ms = (ts_ns - submit_ts) / 1_000_000 if submit_ts > 0 else 0
                else:
                    time_in_book_ms = 0

                # Send fill metrics
                liquidity_side = getattr(event, "liquidity_side", None)
                self._metrics.send(
                    table="live_fills",
                    tags={
                        "strategy": "LLMMv5Primer",
                        "venue": self.config.follower_instrument_id.venue.value,
                        "symbol": self.config.follower_instrument_id.symbol.value,
                        "side": event.order_side.name,
                        "liquidity": getattr(liquidity_side, "name", None),
                    },
                    fields={
                        "qty": event.last_qty.as_decimal(),
                        "price": price,
                        "commission": commission_val,
                        "ofi": self._current_ofi,
                        "volatility_bps": self._current_volatility_bps,
                        "bid_spread_adj": self._ofi_bid_adjustment_bps,
                        "ask_spread_adj": self._ofi_ask_adjustment_bps,
                        "time_in_book_ms": time_in_book_ms,
                    },
                    ts_ns=ts_ns,
                )

    def _check_killswitch(self) -> bool:
        """Extended killswitch with daily loss limit."""
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
