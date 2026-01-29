"""Inventory risk metrics and monitoring."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class InventoryRiskMetrics:
    """Comprehensive inventory risk metrics for monitoring."""
    
    # Position state
    current_position: Decimal
    optimal_target: Decimal
    position_ratio: float  # As fraction of max (0-1)
    inventory_age_seconds: float
    
    # P&L
    unrealized_pnl: Decimal
    realized_pnl: Decimal = Decimal(0)
    
    # Risk measures
    var_1min: float = 0.0  # 1-minute Value at Risk
    volatility_contribution: float = 0.0  # Position * volatility
    time_to_rebalance: float = 0.0  # Estimated seconds to neutral
    
    # Skewing
    current_skew_bps: float = 0.0
    urgency_multiplier: float = 1.0
    bid_size_multiplier: float = 1.0
    ask_size_multiplier: float = 1.0
    
    # Cross-pair (optional)
    correlated_exposure_usd: float = 0.0
    hedge_delta: float = 0.0
    
    # Rebalancing
    needs_rebalancing: bool = False
    rebalance_reason: str = ""
    
    def to_log_string(self) -> str:
        """Format metrics for logging."""
        return (
            f"Pos={self.current_position} (target={self.optimal_target}) | "
            f"Ratio={self.position_ratio:.1%} | "
            f"Skew={self.current_skew_bps:.1f}bps | "
            f"Urgency={self.urgency_multiplier:.1f}x | "
            f"PnL={self.unrealized_pnl:.2f} | "
            f"VaR1m=${self.var_1min:.2f}"
        )
