"""Cross-Pair Portfolio Risk Coordinator V2.

Enhanced coordinator that adds:
1. Redis-based communication between strategy instances
2. Portfolio-wide risk limits and pausing
3. Correlation detection across pairs
4. Real-time P&L aggregation

Run as standalone process or integrate into strategies via mixin.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None


@dataclass
class PairRiskState:
    """Risk state for a single trading pair."""
    symbol: str
    position: float = 0.0
    position_usd: float = 0.0
    mid_price: float = 0.0
    last_update_ts: float = 0.0
    session_pnl_bps: float = 0.0
    session_pnl_usd: float = 0.0
    fill_count: int = 0
    buy_fills: int = 0
    sell_fills: int = 0
    is_paused: bool = False
    pause_reason: str = ""


@dataclass
class PortfolioRiskState:
    """Aggregated portfolio risk state."""
    pairs: Dict[str, PairRiskState] = field(default_factory=dict)
    total_position_usd: float = 0.0
    total_long_usd: float = 0.0
    total_short_usd: float = 0.0
    net_exposure_pct: float = 0.0
    total_session_pnl_usd: float = 0.0
    correlation_alert: bool = False
    portfolio_pause: bool = False
    last_update: str = ""


class PortfolioRiskCoordinator:
    """
    Coordinates risk across multiple strategy instances.
    
    Communication via Redis:
    - Each strategy publishes to 'fortress:strategy:{symbol}'
    - Coordinator publishes to 'fortress:coordinator'
    - Strategies poll coordinator state before placing orders
    
    Risk Limits:
    - Max net portfolio exposure (default $5000 USD)
    - Max single-pair exposure (default 30% of max)
    - Correlation detection (4+ pairs same direction)
    """

    COORDINATOR_KEY = "fortress:coordinator"
    STRATEGY_KEY_PREFIX = "fortress:strategy:"
    STATE_TTL_SECONDS = 60

    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        max_portfolio_exposure_usd: float = 5000.0,
        max_single_pair_pct: float = 0.30,
        correlation_threshold: int = 4,
        update_interval_secs: float = 1.0,
    ):
        self.max_portfolio_exposure_usd = max_portfolio_exposure_usd
        self.max_single_pair_pct = max_single_pair_pct
        self.correlation_threshold = correlation_threshold
        self.update_interval_secs = update_interval_secs
        
        self.portfolio = PortfolioRiskState()
        self._redis: redis.Redis | None = None
        
        if REDIS_AVAILABLE:
            try:
                self._redis = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    decode_responses=True,
                    socket_timeout=5.0,
                )
                self._redis.ping()
                print(f"✅ Connected to Redis at {redis_host}:{redis_port}")
            except Exception as e:
                print(f"⚠️ Redis not available: {e}")
                self._redis = None

    def update_pair_state(self, symbol: str, state: Dict[str, Any]) -> None:
        """Update state for a single pair."""
        if symbol not in self.portfolio.pairs:
            self.portfolio.pairs[symbol] = PairRiskState(symbol=symbol)
        
        pair = self.portfolio.pairs[symbol]
        pair.position = state.get("position", 0.0)
        pair.mid_price = state.get("mid_price", 0.0)
        pair.position_usd = pair.position * pair.mid_price
        pair.session_pnl_bps = state.get("session_pnl_bps", 0.0)
        pair.session_pnl_usd = state.get("session_pnl_usd", 0.0)
        pair.fill_count = state.get("fill_count", 0)
        pair.buy_fills = state.get("buy_fills", 0)
        pair.sell_fills = state.get("sell_fills", 0)
        pair.last_update_ts = time.time()

    def calculate_portfolio_risk(self) -> Dict[str, Any]:
        """Calculate aggregate portfolio risk metrics."""
        total_long = 0.0
        total_short = 0.0
        total_pnl = 0.0
        long_pairs = []
        short_pairs = []
        
        for symbol, pair in self.portfolio.pairs.items():
            if time.time() - pair.last_update_ts > 30:
                continue  # Skip stale data
            
            total_pnl += pair.session_pnl_usd
            
            if pair.position_usd > 0:
                total_long += pair.position_usd
                long_pairs.append(symbol)
            elif pair.position_usd < 0:
                total_short += abs(pair.position_usd)
                short_pairs.append(symbol)
        
        self.portfolio.total_long_usd = total_long
        self.portfolio.total_short_usd = total_short
        self.portfolio.total_position_usd = total_long - total_short
        self.portfolio.total_session_pnl_usd = total_pnl
        
        total_exposure = total_long + total_short
        if total_exposure > 0:
            self.portfolio.net_exposure_pct = self.portfolio.total_position_usd / total_exposure * 100
        else:
            self.portfolio.net_exposure_pct = 0.0
        
        # Correlation detection
        self.portfolio.correlation_alert = (
            len(long_pairs) >= self.correlation_threshold or
            len(short_pairs) >= self.correlation_threshold
        )
        
        # Risk triggers
        risk_triggers = []
        
        if abs(self.portfolio.total_position_usd) > self.max_portfolio_exposure_usd:
            risk_triggers.append(
                f"Net exposure ${self.portfolio.total_position_usd:.0f} > ${self.max_portfolio_exposure_usd:.0f}"
            )
        
        if self.portfolio.correlation_alert:
            direction = "LONG" if len(long_pairs) >= self.correlation_threshold else "SHORT"
            count = max(len(long_pairs), len(short_pairs))
            risk_triggers.append(f"Correlation: {count} pairs {direction}")
        
        self.portfolio.portfolio_pause = len(risk_triggers) > 0
        self.portfolio.last_update = datetime.now(timezone.utc).isoformat()
        
        return {
            "total_long_usd": total_long,
            "total_short_usd": total_short,
            "net_position_usd": self.portfolio.total_position_usd,
            "net_exposure_pct": self.portfolio.net_exposure_pct,
            "total_pnl_usd": total_pnl,
            "long_pairs": long_pairs,
            "short_pairs": short_pairs,
            "correlation_alert": self.portfolio.correlation_alert,
            "portfolio_pause": self.portfolio.portfolio_pause,
            "risk_triggers": risk_triggers,
        }

    def get_pair_limits(self, symbol: str) -> Dict[str, Any]:
        """Get trading limits for a specific pair."""
        result = {
            "allow_buy": True,
            "allow_sell": True,
            "size_scalar": 1.0,
            "reason": "",
        }
        
        # Portfolio-wide pause
        if self.portfolio.portfolio_pause:
            pair = self.portfolio.pairs.get(symbol)
            if pair:
                if pair.position > 0:
                    result["allow_buy"] = False
                    result["reason"] = "Portfolio risk: only SELL to reduce"
                elif pair.position < 0:
                    result["allow_sell"] = False
                    result["reason"] = "Portfolio risk: only BUY to reduce"
                else:
                    result["allow_buy"] = False
                    result["allow_sell"] = False
                    result["reason"] = "Portfolio risk: no new positions"
            return result
        
        # Single-pair limit
        pair = self.portfolio.pairs.get(symbol)
        if pair:
            max_pair_usd = self.max_portfolio_exposure_usd * self.max_single_pair_pct
            if abs(pair.position_usd) > max_pair_usd:
                if pair.position_usd > 0:
                    result["allow_buy"] = False
                    result["reason"] = f"Pair limit: ${pair.position_usd:.0f} > ${max_pair_usd:.0f}"
                else:
                    result["allow_sell"] = False
                    result["reason"] = f"Pair limit: ${abs(pair.position_usd):.0f} > ${max_pair_usd:.0f}"
        
        # Size reduction near limits
        total_exposure = self.portfolio.total_long_usd + self.portfolio.total_short_usd
        if total_exposure > self.max_portfolio_exposure_usd * 0.9:
            result["size_scalar"] = 0.25
        elif total_exposure > self.max_portfolio_exposure_usd * 0.7:
            result["size_scalar"] = 0.5
        
        return result

    def publish_state(self) -> None:
        """Publish coordinator state to Redis."""
        if not self._redis:
            return
        
        try:
            risk = self.calculate_portfolio_risk()
            
            state = {
                "portfolio_pause": str(self.portfolio.portfolio_pause),
                "net_position_usd": str(self.portfolio.total_position_usd),
                "net_exposure_pct": str(self.portfolio.net_exposure_pct),
                "total_pnl_usd": str(self.portfolio.total_session_pnl_usd),
                "correlation_alert": str(self.portfolio.correlation_alert),
                "long_count": str(len(risk["long_pairs"])),
                "short_count": str(len(risk["short_pairs"])),
                "risk_triggers": json.dumps(risk["risk_triggers"]),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            
            self._redis.hset(self.COORDINATOR_KEY, mapping=state)
            self._redis.expire(self.COORDINATOR_KEY, self.STATE_TTL_SECONDS)
            
        except Exception as e:
            print(f"Error publishing state: {e}")

    def read_strategy_states(self) -> None:
        """Read all strategy states from Redis."""
        if not self._redis:
            return
        
        try:
            for key in self._redis.scan_iter(f"{self.STRATEGY_KEY_PREFIX}*"):
                symbol = key.replace(self.STRATEGY_KEY_PREFIX, "")
                state = self._redis.hgetall(key)
                if state:
                    self.update_pair_state(symbol, {
                        "position": float(state.get("position", 0)),
                        "mid_price": float(state.get("mid_price", 0)),
                        "session_pnl_bps": float(state.get("session_pnl_bps", 0)),
                        "session_pnl_usd": float(state.get("session_pnl_usd", 0)),
                        "fill_count": int(state.get("fill_count", 0)),
                        "buy_fills": int(state.get("buy_fills", 0)),
                        "sell_fills": int(state.get("sell_fills", 0)),
                    })
        except Exception as e:
            print(f"Error reading strategy states: {e}")

    def print_status(self) -> None:
        """Print portfolio status."""
        risk = self.calculate_portfolio_risk()
        
        print("\n" + "=" * 70)
        print(f"PORTFOLIO RISK COORDINATOR - {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
        print("=" * 70)
        
        print(f"\n{'Symbol':<12} {'Position':>10} {'USD':>12} {'P&L':>10} {'Fills':>8}")
        print("-" * 54)
        
        for symbol in sorted(self.portfolio.pairs.keys()):
            pair = self.portfolio.pairs[symbol]
            age = time.time() - pair.last_update_ts
            stale = " ⚠️" if age > 30 else ""
            print(
                f"{symbol:<12} {pair.position:>10.2f} ${pair.position_usd:>10.2f} "
                f"${pair.session_pnl_usd:>+9.2f} {pair.fill_count:>8}{stale}"
            )
        
        print("-" * 54)
        print(f"{'TOTAL':<12} {'':>10} ${self.portfolio.total_position_usd:>10.2f} ${self.portfolio.total_session_pnl_usd:>+9.2f}")
        
        print(f"\nLong:  ${risk['total_long_usd']:>10.2f} ({len(risk['long_pairs'])} pairs)")
        print(f"Short: ${risk['total_short_usd']:>10.2f} ({len(risk['short_pairs'])} pairs)")
        print(f"Net Exposure: {self.portfolio.net_exposure_pct:+.1f}%")
        
        if risk["risk_triggers"]:
            print("\n⚠️ RISK ALERTS:")
            for trigger in risk["risk_triggers"]:
                print(f"   - {trigger}")
        
        if self.portfolio.portfolio_pause:
            print("\n🛑 PORTFOLIO PAUSE - Only position-reducing trades allowed")

    def run(self) -> None:
        """Main loop for standalone coordinator."""
        print("Starting Portfolio Risk Coordinator...")
        print(f"  Max exposure: ${self.max_portfolio_exposure_usd:.0f}")
        print(f"  Max per-pair: {self.max_single_pair_pct:.0%}")
        print(f"  Correlation threshold: {self.correlation_threshold} pairs")
        
        if not self._redis:
            print("\n⚠️ Redis not available - install redis-py and run Redis server")
            return
        
        last_print = 0
        
        try:
            while True:
                self.read_strategy_states()
                self.publish_state()
                
                if time.time() - last_print >= 5:
                    self.print_status()
                    last_print = time.time()
                
                time.sleep(self.update_interval_secs)
                
        except KeyboardInterrupt:
            print("\nCoordinator stopped")


# =============================================================================
# STRATEGY INTEGRATION
# =============================================================================

class PortfolioRiskMixin:
    """
    Mixin for strategies to integrate with portfolio coordinator.
    
    Usage:
        class MyStrategy(Strategy, PortfolioRiskMixin):
            def on_start(self):
                self.init_portfolio_risk("SUIUSDT")
                
            def on_fill(self, ...):
                self.publish_to_coordinator(...)
                
            def before_order(self):
                limits = self.get_portfolio_limits()
                if not limits["allow_buy"]:
                    return
    """
    
    _pr_symbol: str = ""
    _pr_redis: redis.Redis | None = None
    _pr_last_publish: float = 0.0
    
    def init_portfolio_risk(
        self,
        symbol: str,
        redis_host: str = "localhost",
        redis_port: int = 6379,
    ) -> bool:
        """Initialize coordinator connection. Returns True if successful."""
        self._pr_symbol = symbol
        self._pr_last_publish = 0.0
        
        if not REDIS_AVAILABLE:
            return False
        
        try:
            self._pr_redis = redis.Redis(
                host=redis_host,
                port=redis_port,
                decode_responses=True,
                socket_timeout=1.0,
            )
            self._pr_redis.ping()
            return True
        except Exception:
            self._pr_redis = None
            return False

    def publish_to_coordinator(
        self,
        position: float,
        mid_price: float,
        session_pnl_bps: float = 0.0,
        session_pnl_usd: float = 0.0,
        fill_count: int = 0,
        buy_fills: int = 0,
        sell_fills: int = 0,
    ) -> None:
        """Publish state to coordinator (call every few seconds or on fills)."""
        if not self._pr_redis:
            return
        
        now = time.time()
        if now - self._pr_last_publish < 1.0:
            return
        self._pr_last_publish = now
        
        try:
            key = f"{PortfolioRiskCoordinator.STRATEGY_KEY_PREFIX}{self._pr_symbol}"
            self._pr_redis.hset(key, mapping={
                "position": str(position),
                "position_usd": str(position * mid_price),
                "mid_price": str(mid_price),
                "session_pnl_bps": str(session_pnl_bps),
                "session_pnl_usd": str(session_pnl_usd),
                "fill_count": str(fill_count),
                "buy_fills": str(buy_fills),
                "sell_fills": str(sell_fills),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            self._pr_redis.expire(key, PortfolioRiskCoordinator.STATE_TTL_SECONDS)
        except Exception:
            pass

    def get_portfolio_limits(self) -> Dict[str, Any]:
        """Get current limits from coordinator."""
        default = {"allow_buy": True, "allow_sell": True, "size_scalar": 1.0, "reason": ""}
        
        if not self._pr_redis:
            return default
        
        try:
            state = self._pr_redis.hgetall(PortfolioRiskCoordinator.COORDINATOR_KEY)
            if not state:
                return default
            
            portfolio_pause = state.get("portfolio_pause", "False") == "True"
            if not portfolio_pause:
                return default
            
            # Check our position
            our_key = f"{PortfolioRiskCoordinator.STRATEGY_KEY_PREFIX}{self._pr_symbol}"
            our_state = self._pr_redis.hgetall(our_key)
            our_position = float(our_state.get("position", 0)) if our_state else 0
            
            if our_position > 0:
                return {"allow_buy": False, "allow_sell": True, "size_scalar": 1.0, "reason": "Portfolio: reduce long"}
            elif our_position < 0:
                return {"allow_buy": True, "allow_sell": False, "size_scalar": 1.0, "reason": "Portfolio: reduce short"}
            else:
                return {"allow_buy": False, "allow_sell": False, "size_scalar": 1.0, "reason": "Portfolio: no new pos"}
                
        except Exception:
            return default


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Portfolio Risk Coordinator")
    parser.add_argument("--redis-host", default="localhost")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--max-exposure", type=float, default=5000.0)
    parser.add_argument("--max-pair-pct", type=float, default=0.30)
    parser.add_argument("--correlation", type=int, default=4)
    
    args = parser.parse_args()
    
    coordinator = PortfolioRiskCoordinator(
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        max_portfolio_exposure_usd=args.max_exposure,
        max_single_pair_pct=args.max_pair_pct,
        correlation_threshold=args.correlation,
    )
    
    coordinator.run()
