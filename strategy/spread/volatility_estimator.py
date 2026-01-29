"""
Volatility estimator for adaptive spread calculation.

Uses exponentially weighted moving average for fast, responsive volatility estimation.
"""

import time
from collections import deque
from typing import Optional

import numpy as np


class VolatilityEstimator:
    """
    Real-time volatility estimator using EWMA.
    
    Tracks price changes and calculates exponentially weighted volatility
    for adaptive spread calculation.
    """
    
    def __init__(
        self,
        half_life_seconds: float = 60.0,
        lookback_periods: int = 100,
    ):
        """
        Initialize volatility estimator.
        
        Args:
            half_life_seconds: Half-life for exponential weighting
            lookback_periods: Number of recent periods to track
        """
        self.half_life = half_life_seconds
        self.lookback_periods = lookback_periods
        
        # Price history
        self.prices = deque(maxlen=lookback_periods)
        self.timestamps = deque(maxlen=lookback_periods)
        
        # EWMA state
        self.ewma_variance = None
        self.last_price = None
        self.last_update_time = time.time()
        
        # Decay factor
        self.alpha = 1.0 - np.exp(-np.log(2) / half_life_seconds)
    
    def update(self, price: float, timestamp: Optional[float] = None) -> None:
        """
        Update volatility estimate with new price.
        
        Args:
            price: Current mid price
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = time.time()
        
        # Store price
        self.prices.append(price)
        self.timestamps.append(timestamp)
        
        # Calculate return if we have previous price.
        # We keep an estimate of *variance rate* (per-second) so the output volatility
        # is non-annualized and consistent for event-driven (irregular) updates.
        if self.last_price is not None and self.last_price > 0:
            time_elapsed = max(1e-6, timestamp - self.last_update_time)

            # Log return over dt
            log_return = np.log(price / self.last_price)

            # Convert to variance rate (r^2 / dt)
            var_rate = (log_return ** 2) / time_elapsed

            # Update EWMA variance rate
            if self.ewma_variance is None:
                self.ewma_variance = var_rate
            else:
                # Adjust alpha for irregular time intervals (continuous-time EWMA)
                effective_alpha = 1.0 - np.exp(-np.log(2) * (time_elapsed / self.half_life))
                self.ewma_variance = (
                    effective_alpha * var_rate +
                    (1.0 - effective_alpha) * self.ewma_variance
                )
        
        self.last_price = price
        self.last_update_time = timestamp
    
    def get_ewma_volatility(self) -> float:
        """
        Get current EWMA volatility estimate.

        Returns:
            Non-annualized volatility per sqrt-second (e.g., 0.001 = 10 bps / sqrt-sec)
        """
        if self.ewma_variance is None or self.ewma_variance <= 0:
            return 0.0005  # Default ~5 bps / sqrt-sec

        volatility = float(np.sqrt(self.ewma_variance))

        # Sanity bounds (0.1 bps/sqrt-sec to 200 bps/sqrt-sec)
        return float(np.clip(volatility, 1e-5, 0.02))
    
    def get_realized_volatility(self, window_seconds: float = 300.0) -> float:
        """
        Get realized volatility over recent window.
        
        Args:
            window_seconds: Lookback window in seconds (default 5 min)
        
        Returns:
            Realized volatility per sqrt-second
        """
        if len(self.prices) < 10:
            return self.get_ewma_volatility()  # Fallback to EWMA
        
        # Find cutoff time
        cutoff_time = time.time() - window_seconds
        
        # Get recent prices within window
        recent_prices = []
        for i, ts in enumerate(self.timestamps):
            if ts >= cutoff_time:
                recent_prices.append(self.prices[i])
        
        if len(recent_prices) < 10:
            return self.get_ewma_volatility()
        
        # Calculate returns
        prices_arr = np.array(recent_prices)
        returns = np.diff(np.log(prices_arr))
        
        # Convert return std to per-sqrt-second by dividing by sqrt(dt)
        dt = max(1e-6, window_seconds / max(len(returns), 1))
        realized_vol = float(np.std(returns) / np.sqrt(dt))

        # Sanity bounds
        return float(np.clip(realized_vol, 1e-5, 0.02))
    
    def get_volatility_regime(self) -> str:
        """
        Classify current volatility regime.
        
        Returns:
            'LOW', 'MEDIUM', or 'HIGH'
        """
        vol = self.get_ewma_volatility()
        
        if vol < 0.0006:
            return 'LOW'
        elif vol < 0.0015:
            return 'MEDIUM'
        else:
            return 'HIGH'
    
    def reset(self) -> None:
        """Reset estimator to initial state."""
        self.prices.clear()
        self.timestamps.clear()
        self.ewma_variance = None
        self.last_price = None
        self.last_update_time = time.time()
