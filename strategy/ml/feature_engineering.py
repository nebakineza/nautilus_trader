"""
Feature engineering for ML-based OBI quality prediction.

Extracts 10 real-time features from market microstructure data.
"""

import time
from collections import deque
from decimal import Decimal
from typing import Optional

import numpy as np
from datetime import datetime


class FeatureExtractor:
    """
    Extract features for ML prediction from market state.
    
    Features (10):
    [0] Current OBI value
    [1] OBI momentum (rate of change)
    [2] OBI volatility (recent std dev)
    [3] Spread tightness (normalized)
    [4] Top-of-book volume ratio
    [5] Order book depth (top 5 levels)
    [6] Recent fill success rate
    [7] Time since last fill (normalized)
    [8] Position ratio (-1 to +1)
    [9] Market regime (time of day, cyclic)
    """
    
    def __init__(self):
        """Initialize feature extractor."""
        # Tracking for fill rate calculation
        self._recent_quotes = deque(maxlen=10)
        self._recent_fills = deque(maxlen=10)
    
    def extract_features(
        self,
        obi_ema: float,
        obi_history: deque,
        book_bids: list,
        book_asks: list,
        best_bid_price: Optional[float],
        best_ask_price: Optional[float],
        net_position: Decimal,
        max_position: Decimal,
        last_fill_time: float,
        quote_count: int,
        fill_count: int,
    ) -> np.ndarray:
        """
        Extract 10 features from current market state.
        
        Args:
            obi_ema: Current OBI exponential moving average
            obi_history: Deque of recent OBI values
            book_bids: List of bid levels from order book
            book_asks: List of ask levels from order book
            best_bid_price: Best bid price (optional)
            best_ask_price: Best ask price (optional)
            net_position: Current position
            max_position: Maximum allowed position
            last_fill_time: Timestamp of last fill
            quote_count: Total quotes placed
            fill_count: Total fills received
        
        Returns:
            np.ndarray of shape (10,) with normalized features
        """
        features = np.zeros(10, dtype=np.float32)
        
        # [0] Current OBI value (-1 to +1)
        features[0] = np.clip(obi_ema, -1.0, 1.0)
        
        # [1] OBI momentum (change rate)
        if len(obi_history) >= 2:
            recent_obi = list(obi_history)
            momentum = recent_obi[-1] - recent_obi[-2]
            features[1] = np.clip(momentum * 10, -1.0, 1.0)  # Scale and clip
        
        # [2] OBI volatility (standard deviation)
        if len(obi_history) >= 5:
            recent_obi = list(obi_history)[-5:]
            volatility = np.std(recent_obi)
            features[2] = np.clip(volatility * 10, 0.0, 1.0)  # Scale and clip
        
        # [3] Spread tightness (smaller = better liquidity)
        if best_bid_price and best_ask_price:
            mid = (best_bid_price + best_ask_price) / 2
            spread_ratio = (best_ask_price - best_bid_price) / mid
            features[3] = np.clip(spread_ratio * 1000, 0.0, 1.0)  # Scale to 0-1
        
        # [4] Top-of-book volume ratio
        if book_bids and book_asks:
            bid_vol = float(book_bids[0].size()) if hasattr(book_bids[0], 'size') else book_bids[0]
            ask_vol = float(book_asks[0].size()) if hasattr(book_asks[0], 'size') else book_asks[0]
            total_vol = bid_vol + ask_vol
            if total_vol > 0:
                features[4] = bid_vol / total_vol
            else:
                features[4] = 0.5
        else:
            features[4] = 0.5  # Neutral
        
        # [5] Order book depth (total volume in top 5)
        total_depth = 0.0
        for i in range(min(5, len(book_bids))):
            total_depth += float(book_bids[i].size()) if hasattr(book_bids[i], 'size') else book_bids[i]
        for i in range(min(5, len(book_asks))):
            total_depth += float(book_asks[i].size()) if hasattr(book_asks[i], 'size') else book_asks[i]
        
        # Normalize depth (assume typical depth ~100 units)
        features[5] = np.clip(total_depth / 100.0, 0.0, 1.0)
        
        # [6] Recent fill success rate (last 10 quotes)
        if quote_count > 0:
            # Approximate: fill_count / quote_count over recent window
            recent_fill_rate = min(fill_count / max(quote_count, 1), 1.0)
            features[6] = recent_fill_rate
        else:
            features[6] = 0.4  # Default assumption
        
        # [7] Time since last fill (normalized, capped at 1 minute)
        time_since_fill = time.time() - last_fill_time
        features[7] = np.clip(time_since_fill / 60.0, 0.0, 1.0)  # Cap at 1 min
        
        # [8] Current position ratio (-1 to +1)
        if max_position > 0:
            position_ratio = float(net_position) / float(max_position)
            features[8] = np.clip(position_ratio, -1.0, 1.0)
        else:
            features[8] = 0.0
        
        # [9] Market regime (time of day, cyclic encoding)
        hour = datetime.now().hour
        features[9] = np.sin(2 * np.pi * hour / 24)
        
        return features
    
    def feature_names(self) -> list[str]:
        """Return feature names for logging/debugging."""
        return [
            'obi_value',
            'obi_momentum',
            'obi_volatility',
            'spread_tightness',
            'tob_volume_ratio',
            'book_depth',
            'fill_success_rate',
            'time_since_fill',
            'position_ratio',
            'market_regime',
        ]
