"""
Enhanced correlation manager for multi-pair strategies.

Monitors cross-pair correlation and provides hedging opportunities and risk limits.
"""

import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

import numpy as np


@dataclass
class CorrelationState:
    """Current correlation state between instruments."""
    
    correlation: float
    lookback_seconds: int
    sample_count: int
    last_update_time: float
    
    # Risk metrics
    combined_exposure_usd: float
    is_hedged: bool  # Opposite positions
    is_aligned: bool  # Same direction positions
    
    def to_log_string(self) -> str:
        """Format for logging."""
        return (
            f"Corr={self.correlation:+.2f} | "
            f"Exposure=${self.combined_exposure_usd:.0f} | "
            f"Hedged={self.is_hedged} | "
            f"Samples={self.sample_count}"
        )


class EnhancedCorrelationManager:
    """
    Advanced correlation monitoring and hedging for multi-pair strategies.
    
    Features:
    - Real-time correlation calculation
    - Exponentially weighted correlation
    - Hedging opportunity detection
    - Dynamic position limits
    - Cross-pair exposure tracking
    """
    
    def __init__(
        self,
        lookback_seconds: int = 300,
        update_interval_seconds: float = 10.0,
        high_correlation_threshold: float = 0.8,
        low_correlation_threshold: float = 0.3,
    ):
        """
        Initialize enhanced correlation manager.
        
        Args:
            lookback_seconds: Rolling window for correlation (default 5 min)
            update_interval_seconds: How often to recalculate (default 10s)
            high_correlation_threshold: Threshold for high correlation
            low_correlation_threshold: Threshold for low correlation
        """
        self.lookback_seconds = lookback_seconds
        self.update_interval = update_interval_seconds
        self.high_corr_threshold = high_correlation_threshold
        self.low_corr_threshold = low_correlation_threshold
        
        # Price history for correlation calculation
        self.price_history_1 = deque(maxlen=1000)
        self.price_history_2 = deque(maxlen=1000)
        self.timestamps = deque(maxlen=1000)
        
        # State
        self.current_correlation = 0.0
        self.last_update_time = 0.0
        self.sample_count = 0
        
        # EWMA correlation
        self.ewma_correlation = None
        self.ewma_alpha = 0.1  # 10% weight on new, 90% on old
    
    def update_prices(
        self,
        price1: float,
        price2: float,
        timestamp: Optional[float] = None,
    ) -> None:
        """
        Update price history for both instruments.
        
        Args:
            price1: Price of first instrument
            price2: Price of second instrument
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = time.time()
        
        self.price_history_1.append(price1)
        self.price_history_2.append(price2)
        self.timestamps.append(timestamp)
        
        # Update correlation if interval passed
        if timestamp - self.last_update_time >= self.update_interval:
            self._calculate_correlation()
            self.last_update_time = timestamp
    
    def _calculate_correlation(self) -> None:
        """Calculate correlation over lookback window."""
        if len(self.price_history_1) < 20:
            return  # Need minimum samples
        
        # Get recent prices within lookback window
        cutoff_time = time.time() - self.lookback_seconds
        
        recent_prices_1 = []
        recent_prices_2 = []
        
        for i, ts in enumerate(self.timestamps):
            if ts >= cutoff_time:
                recent_prices_1.append(self.price_history_1[i])
                recent_prices_2.append(self.price_history_2[i])
        
        if len(recent_prices_1) < 10:
            return
        
        # Calculate returns
        prices_1 = np.array(recent_prices_1)
        prices_2 = np.array(recent_prices_2)
        
        returns_1 = np.diff(np.log(prices_1))
        returns_2 = np.diff(np.log(prices_2))
        
        # Pearson correlation
        if len(returns_1) > 0:
            correlation = np.corrcoef(returns_1, returns_2)[0, 1]
            
            # Handle NaN (can happen with constant prices)
            if np.isnan(correlation):
                correlation = 0.0
            
            self.current_correlation = float(correlation)
            self.sample_count = len(returns_1)
            
            # Update EWMA correlation
            if self.ewma_correlation is None:
                self.ewma_correlation = correlation
            else:
                self.ewma_correlation = (
                    self.ewma_alpha * correlation +
                    (1 - self.ewma_alpha) * self.ewma_correlation
                )
    
    def get_correlation(self, use_ewma: bool = True) -> float:
        """
        Get current correlation estimate.
        
        Args:
            use_ewma: Use EWMA correlation (smoother) vs instant
        
        Returns:
            Correlation coefficient (-1 to +1)
        """
        if use_ewma and self.ewma_correlation is not None:
            return self.ewma_correlation
        return self.current_correlation
    
    def check_hedging_opportunity(
        self,
        position1: Decimal,
        position2: Decimal,
        price1: float,
        price2: float,
    ) -> Tuple[bool, str]:
        """
        Check if positions provide natural hedge.
        
        Args:
            position1: Position in first instrument
            position2: Position in second instrument
            price1: Price of first instrument
            price2: Price of second instrument
        
        Returns:
            (is_hedged, description)
        """
        correlation = self.get_correlation()
        
        # High correlation and opposite positions = natural hedge
        if abs(correlation) > self.high_corr_threshold:
            pos1_long = position1 > 0
            pos2_long = position2 > 0
            
            if pos1_long != pos2_long:  # Opposite directions
                return True, f"Hedged: High corr ({correlation:.2f}) with opposite positions"
        
        return False, "No hedge"
    
    def calculate_combined_exposure(
        self,
        position1: Decimal,
        position2: Decimal,
        price1: float,
        price2: float,
    ) -> float:
        """
        Calculate combined USD exposure across both instruments.
        
        Args:
            position1: Position in first instrument
            position2: Position in second instrument
            price1: Price of first instrument
            price2: Price of second instrument
        
        Returns:
            Combined exposure in USD
        """
        exposure1 = abs(float(position1) * price1)
        exposure2 = abs(float(position2) * price2)
        
        return exposure1 + exposure2
    
    def get_correlation_adjusted_limits(
        self,
        base_limit_usd: float,
        position1: Decimal,
        position2: Decimal,
        price1: float,
        price2: float,
    ) -> dict:
        """
        Calculate position limits adjusted for correlation.
        
        High correlation + aligned positions = reduced limits
        High correlation + opposite positions = can be more aggressive
        
        Args:
            base_limit_usd: Base exposure limit
            position1: Current position in instrument 1
            position2: Current position in instrument 2
            price1: Price of instrument 1
            price2: Price of instrument 2
        
        Returns:
            Dict with 'max_combined_usd' and 'adjustment_factor'
        """
        correlation = self.get_correlation()
        
        # Check if positions are aligned (same direction)
        pos1_long = position1 > 0
        pos2_long = position2 > 0
        same_direction = (pos1_long == pos2_long) and (position1 != 0 and position2 != 0)
        
        # High correlation + same direction = concentrated risk
        if abs(correlation) > self.high_corr_threshold and same_direction:
            # Reduce limit by 20%
            adjustment_factor = 0.8
        # High correlation + opposite directions = hedged
        elif abs(correlation) > self.high_corr_threshold and not same_direction:
            # Can increase limit by 10%
            adjustment_factor = 1.1
        # Low correlation = independent
        else:
            adjustment_factor = 1.0
        
        max_combined = base_limit_usd * adjustment_factor
        
        return {
            'max_combined_usd': max_combined,
            'adjustment_factor': adjustment_factor,
            'reason': self._get_adjustment_reason(correlation, same_direction),
        }
    
    def _get_adjustment_reason(self, correlation: float, same_direction: bool) -> str:
        """Get human-readable reason for limit adjustment."""
        if abs(correlation) > self.high_corr_threshold:
            if same_direction:
                return f"High corr ({correlation:.2f}) + aligned → reduced limit"
            else:
                return f"High corr ({correlation:.2f}) + hedged → increased limit"
        return "Independent positions"
    
    def get_state(
        self,
        position1: Decimal,
        position2: Decimal,
        price1: float,
        price2: float,
    ) -> CorrelationState:
        """
        Get current correlation state.
        
        Args:
            position1: Position in first instrument
            position2: Position in second instrument
            price1: Price of first instrument
            price2: Price of second instrument
        
        Returns:
            CorrelationState object
        """
        correlation = self.get_correlation()
        combined_exposure = self.calculate_combined_exposure(
            position1, position2, price1, price2
        )
        
        # Check if hedged or aligned
        is_hedged, _ = self.check_hedging_opportunity(
            position1, position2, price1, price2
        )
        
        pos1_long = position1 > 0
        pos2_long = position2 > 0
        is_aligned = (
            (pos1_long == pos2_long) and
            (position1 != 0 and position2 != 0)
        )
        
        return CorrelationState(
            correlation=correlation,
            lookback_seconds=self.lookback_seconds,
            sample_count=self.sample_count,
            last_update_time=self.last_update_time,
            combined_exposure_usd=combined_exposure,
            is_hedged=is_hedged,
            is_aligned=is_aligned,
        )
    
    def reset(self) -> None:
        """Reset correlation tracking."""
        self.price_history_1.clear()
        self.price_history_2.clear()
        self.timestamps.clear()
        self.current_correlation = 0.0
        self.ewma_correlation = None
        self.sample_count = 0
