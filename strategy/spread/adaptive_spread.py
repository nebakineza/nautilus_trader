"""
Adaptive spread calculator with 5 components.

Dynamically adjusts spread based on market conditions, inventory, and ML signals.
"""

from dataclasses import dataclass
from typing import Dict

import numpy as np


@dataclass
class SpreadComponents:
    """Individual spread components for analysis."""
    
    base_spread: float
    volatility_spread: float
    inventory_spread: float
    adverse_selection_spread: float
    competition_spread: float
    total_spread: float
    
    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return {
            'base': self.base_spread,
            'volatility': self.volatility_spread,
            'inventory': self.inventory_spread,
            'adverse_selection': self.adverse_selection_spread,
            'competition': self.competition_spread,
            'total': self.total_spread,
        }


class AdaptiveSpreadCalculator:
    """
    Calculate adaptive spread with 5 components.
    
    Components:
    1. Base spread (1-2 bps) - minimum profitable spread
    2. Volatility spread (0-3 bps) - protection against price moves
    3. Inventory spread (0-2 bps) - additional risk premium for positions
    4. Adverse selection spread (0-2 bps) - protection against informed traders
    5. Competition spread (-1 to +1 bps) - fill rate optimization
    """
    
    def __init__(
        self,
        min_base_spread_bps: float = 1.0,
        max_base_spread_bps: float = 2.0,
        volatility_multiplier: float = 5000.0,
        inventory_multiplier: float = 2.0,
        adverse_selection_multiplier: float = 2.0,
        competition_multiplier: float = 1.0,
        target_fill_rate: float = 0.55,
    ):
        """
        Initialize adaptive spread calculator.
        
        Args:
            min_base_spread_bps: Minimum base spread
            max_base_spread_bps: Maximum base spread
            volatility_multiplier: Scaling factor for volatility component (bps per vol_per_sqrt_second)
            inventory_multiplier: Scaling factor for inventory component
            adverse_selection_multiplier: Scaling factor for AS component
            competition_multiplier: Scaling factor for competition component
            target_fill_rate: Target fill rate (default 55%)
        """
        self.min_base_spread = min_base_spread_bps
        self.max_base_spread = max_base_spread_bps
        self.k_vol = volatility_multiplier
        self.k_inv = inventory_multiplier
        self.k_as = adverse_selection_multiplier
        self.k_comp = competition_multiplier
        self.target_fill_rate = target_fill_rate
    
    def calculate_spread(
        self,
        volatility: float,
        position_ratio: float,
        ml_confidence: float,
        recent_fill_rate: float,
        trade_intensity: float = 1.0,
        urgency_multiplier: float = 1.0,
    ) -> SpreadComponents:
        """
        Calculate adaptive spread with all components.
        
        Args:
            volatility: Current volatility per sqrt-second (e.g., 0.001 = 10 bps / sqrt-sec)
            position_ratio: Position as fraction of max (-1 to +1)
            ml_confidence: ML model confidence (0 to 1)
            recent_fill_rate: Recent fill success rate (0 to 1)
            trade_intensity: Market activity level (default 1.0)
            urgency_multiplier: Inventory urgency (1.0 to 10.0)
        
        Returns:
            SpreadComponents object with individual and total spreads
        """
        # 1. Base spread (depends on instrument type)
        base_spread = self._calculate_base_spread()
        
        # 2. Volatility spread
        volatility_spread = self._calculate_volatility_spread(
            volatility, urgency_multiplier
        )
        
        # 3. Inventory spread
        inventory_spread = self._calculate_inventory_spread(
            position_ratio, urgency_multiplier
        )
        
        # 4. Adverse selection spread
        adverse_selection_spread = self._calculate_adverse_selection_spread(
            ml_confidence, trade_intensity
        )
        
        # 5. Competition spread
        competition_spread = self._calculate_competition_spread(
            recent_fill_rate
        )
        
        # Total spread
        total_spread = (
            base_spread +
            volatility_spread +
            inventory_spread +
            adverse_selection_spread +
            competition_spread
        )
        
        # Ensure minimum spread of 1 bps
        total_spread = max(1.0, total_spread)
        
        # Cap maximum spread at 10 bps
        total_spread = min(10.0, total_spread)
        
        return SpreadComponents(
            base_spread=base_spread,
            volatility_spread=volatility_spread,
            inventory_spread=inventory_spread,
            adverse_selection_spread=adverse_selection_spread,
            competition_spread=competition_spread,
            total_spread=total_spread,
        )
    
    def _calculate_base_spread(self) -> float:
        """
        Calculate base spread component.
        
        Returns:
            Base spread in bps (1-2 bps)
        """
        # Use middle of range
        return (self.min_base_spread + self.max_base_spread) / 2
    
    def _calculate_volatility_spread(
        self,
        volatility: float,
        urgency: float,
    ) -> float:
        """
        Calculate volatility-based spread component.
        
        Higher volatility → wider spread for protection.
        
        Args:
            volatility: Volatility per sqrt-second
            urgency: Inventory urgency multiplier (1-10)
        
        Returns:
            Volatility spread in bps (0-3 bps)
        """
        # Base volatility spread (calibrated for per-sqrt-second volatility)
        vol_spread = self.k_vol * max(0.0, volatility)
        
        # Increase during high inventory urgency
        vol_spread *= (1.0 + (urgency - 1.0) * 0.2)  # Up to 20% increase per urgency
        
        # Cap at 4 bps (still bounded by total 10 bps cap)
        return min(4.0, vol_spread)
    
    def _calculate_inventory_spread(
        self,
        position_ratio: float,
        urgency: float,
    ) -> float:
        """
        Calculate inventory-based spread component.
        
        Larger positions → wider spread for risk premium.
        
        Args:
            position_ratio: Position as fraction of max (-1 to +1)
            urgency: Inventory urgency multiplier (1-10)
        
        Returns:
            Inventory spread in bps (0-2 bps)
        """
        # Quadratic in position ratio (grows faster at extremes)
        inv_spread = self.k_inv * (abs(position_ratio) ** 1.5)
        
        # Scale by urgency
        inv_spread *= urgency
        
        # Cap at 2 bps
        return min(2.0, inv_spread)
    
    def _calculate_adverse_selection_spread(
        self,
        ml_confidence: float,
        trade_intensity: float,
    ) -> float:
        """
        Calculate adverse selection spread component.
        
        Low ML confidence or high trade intensity → wider spread.
        
        Args:
            ml_confidence: ML confidence (0-1, higher is better)
            trade_intensity: Market activity level (1.0 = normal)
        
        Returns:
            Adverse selection spread in bps (0-2 bps)
        """
        # Inverse of ML confidence (low confidence = higher AS risk)
        confidence_factor = 1.0 - ml_confidence
        
        # Scale by trade intensity
        as_spread = self.k_as * confidence_factor * trade_intensity
        
        # Cap at 2 bps
        return min(2.0, as_spread)
    
    def _calculate_competition_spread(
        self,
        recent_fill_rate: float,
    ) -> float:
        """
        Calculate competition-based spread adjustment.
        
        Fill rate too low → tighten spread (more competitive)
        Fill rate too high → widen spread (may be adverse selection)
        
        Args:
            recent_fill_rate: Recent fill success rate (0-1)
        
        Returns:
            Competition spread in bps (-1 to +1 bps)
        """
        # Calculate deviation from target
        fill_rate_error = recent_fill_rate - self.target_fill_rate
        
        # Spread adjustment (negative error = tighten, positive = widen)
        comp_spread = self.k_comp * fill_rate_error * 2
        
        # Clip to reasonable range
        return np.clip(comp_spread, -1.0, 1.0)
    
    def get_spread_bounds(self) -> tuple[float, float]:
        """
        Get theoretical min/max spread bounds.
        
        Returns:
            (min_spread_bps, max_spread_bps)
        """
        # Minimum: base - competition
        min_spread = self.min_base_spread - 1.0
        min_spread = max(1.0, min_spread)  # Never below 1 bps
        
        # Maximum: all components at max
        max_spread = (
            self.max_base_spread +
            3.0 +  # Max volatility
            2.0 +  # Max inventory
            2.0 +  # Max adverse selection
            1.0    # Max competition
        )
        max_spread = min(10.0, max_spread)  # Cap at 10 bps
        
        return (min_spread, max_spread)
