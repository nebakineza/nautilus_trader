"""Inventory risk manager for HFT market making."""

import time
from decimal import Decimal
from typing import Optional
import numpy as np

from strategy.inventory.skewing import (
    calculate_avellaneda_stoikov_skew,
    calculate_asymmetric_sizes,
    calculate_optimal_inventory_target,
    calculate_urgency_multiplier,
)
from strategy.inventory.metrics import InventoryRiskMetrics


class InventoryRiskManager:
    """
    Manages inventory risk using Avellaneda-Stoikov model.
    
    Responsibilities:
    - Calculate optimal inventory targets
    - Calculate price skewing
    - Calculate size skewing
    - Determine when rebalancing is needed
    - Calculate risk metrics
    """
    
    def __init__(
        self,
        max_position: Decimal,
        risk_aversion: float = 0.5,
        volatility: float = 0.02,
        time_horizon: float = 120.0,
        comfort_zone_pct: float = 0.3,
        warning_zone_pct: float = 0.7,
        danger_zone_pct: float = 0.9,
    ):
        """
        Initialize inventory risk manager.
        
        Args:
            max_position: Maximum position size
            risk_aversion: γ parameter (higher = more aggressive skewing)
            volatility: Market volatility estimate
            time_horizon: Target time to neutral (seconds)
            comfort_zone_pct: Position ratio for comfort zone (default 30%)
            warning_zone_pct: Position ratio for warning zone (default 70%)
            danger_zone_pct: Position ratio for danger zone (default 90%)
        """
        self.max_position = max_position
        self.risk_aversion = risk_aversion
        self.volatility = volatility
        self.time_horizon = time_horizon
        
        # Position zones
        self.comfort_zone = comfort_zone_pct
        self.warning_zone = warning_zone_pct
        self.danger_zone = danger_zone_pct
        
        # State tracking
        self.last_rebalance_time = time.time()
    
    def calculate_optimal_target(
        self,
        obi_ema: float,
        ml_confidence: float = 0.5,
    ) -> Decimal:
        """
        Calculate optimal inventory target.
        
        May be non-zero if high ML confidence and strong OBI signal.
        """
        return calculate_optimal_inventory_target(
            obi_ema=obi_ema,
            ml_confidence=ml_confidence,
            max_position=self.max_position,
        )
    
    def calculate_skew(
        self,
        position: Decimal,
        optimal_target: Decimal,
        mid_price: float,
        volatility: Optional[float] = None,
    ) -> float:
        """
        Calculate price skew in basis points.
        
        Returns:
            Skew in bps (positive = shift up, negative = shift down)
        """
        return calculate_avellaneda_stoikov_skew(
            position=position,
            max_position=self.max_position,
            optimal_target=optimal_target,
            risk_aversion=self.risk_aversion,
            volatility=float(volatility) if volatility is not None else self.volatility,
            time_horizon=self.time_horizon,
            mid_price=mid_price,
        )
    
    def calculate_sizes(
        self,
        position: Decimal,
        optimal_target: Decimal,
        base_size: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Calculate asymmetric bid/ask sizes.
        
        Returns:
            (bid_size, ask_size)
        """
        return calculate_asymmetric_sizes(
            position=position,
            max_position=self.max_position,
            optimal_target=optimal_target,
            base_size=base_size,
        )
    
    def check_rebalancing_needed(
        self,
        position: Decimal,
        optimal_target: Decimal,
        time_since_fill: float,
    ) -> tuple[bool, str]:
        """
        Determine if active rebalancing is needed.
        
        Returns:
            (needs_rebalancing, reason)
        """
        imbalance = position - optimal_target
        position_ratio = abs(float(imbalance)) / float(self.max_position)
        
        # Critical zone - immediate rebalance
        if position_ratio >= self.danger_zone:
            return True, f"Critical position {position_ratio:.1%} >= {self.danger_zone:.0%}"
        
        # Warning zone for extended time
        if position_ratio >= self.warning_zone:
            time_since_rebalance = time.time() - self.last_rebalance_time
            if time_since_rebalance > 30.0:  # 30 seconds
                return True, f"Warning zone {position_ratio:.1%} for {time_since_rebalance:.0f}s"
        
        # Comfort zone but aged position
        if position_ratio > self.comfort_zone:
            if time_since_fill > 60.0:  # 60 seconds
                return True, f"Position aged {time_since_fill:.0f}s in comfort zone"
        
        return False, ""
    
    def calculate_metrics(
        self,
        position: Decimal,
        optimal_target: Decimal,
        mid_price: float,
        unrealized_pnl: Decimal,
        time_since_fill: float,
        volatility: Optional[float] = None,
        obi_ema: float = 0.0,
        ml_confidence: float = 0.5,
    ) -> InventoryRiskMetrics:
        """
        Calculate comprehensive inventory risk metrics.
        
        Args:
            position: Current position
            optimal_target: Optimal target position
            mid_price: Current mid price
            unrealized_pnl: Unrealized P&L
            time_since_fill: Seconds since last fill
            obi_ema: Current OBI EMA
            ml_confidence: Current ML confidence
        
        Returns:
            InventoryRiskMetrics object
        """
        # Position metrics
        imbalance = position - optimal_target
        position_ratio = abs(float(imbalance)) / float(self.max_position)
        
        # Calculate skewing
        skew_bps = self.calculate_skew(position, optimal_target, mid_price, volatility=volatility)
        urgency = calculate_urgency_multiplier(position_ratio)
        
        # Calculate size multipliers
        bid_size, ask_size = self.calculate_sizes(
            position, optimal_target, Decimal("1.0")
        )
        bid_mult = float(bid_size)
        ask_mult = float(ask_size)
        
        # Risk metrics
        position_usd = float(position) * mid_price
        effective_vol = float(volatility) if volatility is not None else self.volatility
        var_1min = abs(position_usd) * effective_vol * np.sqrt(1/60)
        vol_contribution = abs(position_usd * effective_vol)
        
        # Time to rebalance estimate
        # Assuming 40% fill rate and quote refresh every 50ms
        if imbalance != 0:
            fills_per_minute = 0.4 * (60 / 0.05)  # 480 potential fills/min
            avg_fill_size = float(self.max_position) * 0.0001 / 0.0005  # Ratio
            volume_per_minute = fills_per_minute * avg_fill_size
            time_to_neutral = (abs(float(imbalance)) / volume_per_minute) * 60 if volume_per_minute > 0 else 999
        else:
            time_to_neutral = 0
        
        # Check rebalancing
        needs_rebalance, reason = self.check_rebalancing_needed(
            position, optimal_target, time_since_fill
        )
        
        return InventoryRiskMetrics(
            current_position=position,
            optimal_target=optimal_target,
            position_ratio=position_ratio,
            inventory_age_seconds=time_since_fill,
            unrealized_pnl=unrealized_pnl,
            var_1min=var_1min,
            volatility_contribution=vol_contribution,
            time_to_rebalance=time_to_neutral,
            current_skew_bps=skew_bps,
            urgency_multiplier=urgency,
            bid_size_multiplier=bid_mult,
            ask_size_multiplier=ask_mult,
            needs_rebalancing=needs_rebalance,
            rebalance_reason=reason,
        )
    
    def mark_rebalanced(self):
        """Mark that rebalancing was executed."""
        self.last_rebalance_time = time.time()
