"""
Avellaneda-Stoikov price and size skewing for inventory management.

Based on:
- Avellaneda & Stoikov (2008): "High-frequency trading in a limit order book"
- Cartea & Jaimungal (2015): "Algorithmic and High-Frequency Trading"
"""

from decimal import Decimal
from typing import Tuple
import numpy as np


def calculate_avellaneda_stoikov_skew(
    position: Decimal,
    max_position: Decimal,
    optimal_target: Decimal,
    risk_aversion: float = 0.5,
    volatility: float = 0.02,
    time_horizon: float = 120.0,
    mid_price: float = 0.0,
) -> float:
    """
    Calculate optimal price skew based on inventory using Avellaneda-Stoikov model.
    
    Formula:
        skew_bps = -γ * σ² * T * q * 10000 * urgency
    
    where:
        γ = risk aversion parameter
        σ = volatility
        T = time horizon (seconds)
        q = normalized position (-1 to +1)
        urgency = multiplier that increases near position limits
    
    Args:
        position: Current position quantity
        max_position: Maximum allowed position quantity
        optimal_target: Optimal target position (usually 0, but can be directional)
        risk_aversion: Risk aversion parameter γ (default 0.5)
        volatility: Market volatility σ (default 0.02 = 2%)
        time_horizon: Time horizon in seconds (default 120s)
        mid_price: Current mid price (for logging, optional)
    
    Returns:
        Skew in basis points (positive = skew up, negative = skew down)
        
    Example:
        Long 0.0002 BTC with max 0.0003:
        - position_ratio = 0.67
        - Base skew = -0.5 * 0.0004 * 120 * 0.67 * 10000 = -16.08 bps
        - With urgency (2.3x) = -37 bps
        - Result: Quotes shifted DOWN by 37 bps to encourage selling
    """
    # Calculate inventory imbalance
    inventory_imbalance = position - optimal_target
    
    # Normalize by max position (-1 to +1)
    if max_position == 0:
        return 0.0
    
    normalized_position = float(inventory_imbalance) / float(max_position)
    
    # Clamp to [-1, 1]
    normalized_position = max(-1.0, min(1.0, normalized_position))
    
    # Calculate base skew
    # Note: negative sign means long position → negative skew → lower prices
    base_skew_bps = (
        -risk_aversion
        * (volatility ** 2)
        * time_horizon
        * normalized_position
        * 10000  # Convert to basis points
    )
    
    # Apply urgency multiplier
    urgency = calculate_urgency_multiplier(abs(normalized_position))
    skew_bps = base_skew_bps * urgency
    
    return skew_bps


def calculate_urgency_multiplier(position_ratio: float) -> float:
    """
    Calculate urgency multiplier based on position size.
    
    Position zones:
    - Comfort (0-30%): 1.0x (normal skewing)
    - Warning (30-70%): 1.0x - 3.0x (linear increase)
    - Danger (70-100%): 3.0x - 10.0x (exponential increase)
    
    Args:
        position_ratio: Absolute position as fraction of max (0.0 to 1.0)
    
    Returns:
        Urgency multiplier (1.0 to 10.0)
    """
    position_ratio = abs(position_ratio)
    
    if position_ratio < 0.3:  # Comfort zone
        return 1.0
    elif position_ratio < 0.7:  # Warning zone
        # Linear increase from 1.0 to 3.0
        return 1.0 + (position_ratio - 0.3) * 5.0
    else:  # Danger zone (>70%)
        # Exponential increase from 3.0 to 10.0
        excess = position_ratio - 0.7
        return 3.0 + (excess / 0.3) ** 2 * 7.0


def calculate_asymmetric_sizes(
    position: Decimal,
    max_position: Decimal,
    optimal_target: Decimal,
    base_size: Decimal,
    min_multiplier: float = 0.3,
    max_multiplier: float = 2.0,
) -> Tuple[Decimal, Decimal]:
    """
    Calculate asymmetric bid/ask sizes based on inventory.
    
    Logic:
    - When long: Reduce bid size (don't accumulate), increase ask size (eager to sell)
    - When short: Increase bid size (eager to buy), reduce ask size (don't accumulate)
    
    Args:
        position: Current position quantity
        max_position: Maximum allowed position quantity
        optimal_target: Optimal target position
        base_size: Base quote size
        min_multiplier: Minimum size multiplier (default 0.3 = 30%)
        max_multiplier: Maximum size multiplier (default 2.0 = 200%)
    
    Returns:
        (bid_size, ask_size) tuple
        
    Example:
        Long position (normalized +0.5):
        - bid_multiplier = 1.0 - 0.5 * 0.5 = 0.75 (reduce bids)
        - ask_multiplier = 1.0 + 0.5 * 0.5 = 1.25 (increase asks)
    """
    # Calculate inventory imbalance
    inventory_imbalance = position - optimal_target
    
    # Normalize (-1 to +1)
    if max_position == 0:
        return base_size, base_size
    
    normalized_imbalance = float(inventory_imbalance) / float(max_position)
    normalized_imbalance = max(-1.0, min(1.0, normalized_imbalance))
    
    # Calculate multipliers
    # When long (positive imbalance):
    #   - Bid size reduced (less eager to buy)
    #   - Ask size increased (more eager to sell)
    bid_multiplier = 1.0 - (normalized_imbalance * 0.5)
    ask_multiplier = 1.0 + (normalized_imbalance * 0.5)
    
    # Clamp multipliers
    bid_multiplier = max(min_multiplier, min(max_multiplier, bid_multiplier))
    ask_multiplier = max(min_multiplier, min(max_multiplier, ask_multiplier))
    
    # Apply to base size
    bid_size = base_size * Decimal(str(bid_multiplier))
    ask_size = base_size * Decimal(str(ask_multiplier))
    
    return bid_size, ask_size


def calculate_optimal_inventory_target(
    obi_ema: float,
    ml_confidence: float,
    max_position: Decimal,
    directional_bias_threshold: float = 0.6,
    directional_allocation: float = 0.2,
) -> Decimal:
    """
    Calculate optimal inventory target (may be non-zero for directional bias).
    
    Args:
        obi_ema: Current OBI EMA value
        ml_confidence: ML model confidence (0-1)
        max_position: Maximum position size
        directional_bias_threshold: ML confidence threshold for directional bias
        directional_allocation: Fraction of max position for directional bias
    
    Returns:
        Optimal target position (can be non-zero)
    """
    base_target = Decimal(0)  # Start neutral
    
    # Only allow directional bias if ML confidence is high
    if ml_confidence > directional_bias_threshold:
        if obi_ema > 0.10:  # Bullish OBI
            base_target = max_position * Decimal(str(directional_allocation))
        elif obi_ema < -0.10:  # Bearish OBI
            base_target = -max_position * Decimal(str(directional_allocation))
    
    return base_target
