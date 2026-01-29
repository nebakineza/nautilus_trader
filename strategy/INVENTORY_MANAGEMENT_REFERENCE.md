# Inventory Management Quick Reference
**For: Multi-Pair HFT Strategy v002**

## Core Concepts

### 1. Avellaneda-Stoikov Model

**Key Equation**: Optimal price skew based on inventory position

```
skew = -γ * σ² * T * q

where:
  γ = risk aversion (0.5)
  σ = volatility (0.02)
  T = time horizon (120s)
  q = normalized position (-1 to +1)
```

**Intuition**: 
- Long position → Skew prices DOWN → Encourage selling
- Short position → Skew prices UP → Encourage buying

### 2. Three-Layer Inventory Control

#### Layer 1: Price Skewing (Passive)
- Shift bid/ask quotes away from position
- Magnitude: 0-20 bps typically
- Always active

#### Layer 2: Size Skewing (Semi-Passive)
- Reduce size on accumulating side
- Increase size on offsetting side
- Range: 0.3x to 2.0x base size

#### Layer 3: Active Rebalancing (Aggressive)
- IOC market orders when >90% of max position
- Only when passive skewing insufficient
- Last resort

## Implementation Checklist

### Setup
```python
✓ Define risk_aversion parameter (γ)
✓ Estimate or measure volatility (σ)
✓ Set time_horizon (T)
✓ Define comfort/warning/danger zones (30%/70%/90%)
✓ Set optimal_target (usually 0, but can be directional)
```

### Real-Time Calculations
```python
1. Calculate normalized position
   position_ratio = current_pos / max_pos

2. Calculate optimal skew (bps)
   skew_bps = -γ * σ² * T * position_ratio * 10000

3. Apply urgency multiplier
   if position_ratio > 0.7:
       urgency = exponential_function(position_ratio)
       skew_bps *= urgency

4. Calculate asymmetric sizes
   bid_multiplier = 1.0 - (position_ratio * 0.5)
   ask_multiplier = 1.0 + (position_ratio * 0.5)

5. Generate quotes
   bid_price = mid - half_spread + skew
   ask_price = mid + half_spread + skew
   bid_size = base_size * bid_multiplier
   ask_size = base_size * ask_multiplier
```

## Position Zones

| Zone | Range | Action | Skew Multiplier |
|------|-------|--------|-----------------|
| **Comfort** | 0-30% | Normal quoting | 1.0x |
| **Warning** | 30-70% | Moderate skew | 1.0x - 3.0x |
| **Danger** | 70-90% | Aggressive skew | 3.0x - 10.0x |
| **Critical** | >90% | Emergency rebalance | N/A (market orders) |

## Cross-Pair Considerations

### When to Hedge
```
IF correlation(BTC, ETH) > 0.8 AND
   both_positions_same_direction AND
   total_exposure > 30% of capital
THEN
   Place offsetting order in smaller position
```

### Optimal Targets with Correlation
```python
# BTC long, ETH high correlation → Allow ETH short bias
if btc_position > 0 and correlation > 0.8:
    eth_optimal_target = -btc_position * correlation * hedge_ratio
```

## Key Metrics to Monitor

### Position Health
- **Position Ratio**: `current / max` (0-1 scale)
- **Distance from Target**: `abs(current - optimal_target)`
- **Time in Position**: Seconds since last fill
- **VaR (1-min)**: `position_usd * volatility * sqrt(1/60)`

### Skewing Effectiveness
- **Actual Skew Applied**: Basis points
- **Fill Rate by Side**: Are we filling more on offsetting side?
- **Time to Neutral**: Estimated seconds to reach target
- **Rebalance Frequency**: How often hitting emergency rebalance?

### Cross-Pair
- **Correlated Exposure**: `abs(btc_usd) + abs(eth_usd)` when corr > 0.7
- **Hedge Delta**: Net exposure after correlation adjustment

## Common Pitfalls & Solutions

### Pitfall 1: Over-Skewing
**Problem**: Skew so wide no fills happen, position stays stuck
**Solution**: Monitor fill rate, cap maximum skew at 20 bps

### Pitfall 2: Slow Rebalancing
**Problem**: Skewing working but too slow, position ages
**Solution**: Increase urgency multiplier above 70% ratio

### Pitfall 3: Fighting the Trend
**Problem**: Market moving against position, continuous adverse selection
**Solution**: Allow directional optimal targets when ML confidence high

### Pitfall 4: Emergency Rebalancing Too Often
**Problem**: Hitting 90% limit frequently, taking market impact
**Solution**: More aggressive skewing at 70%, or reduce position limits

### Pitfall 5: Ignoring Correlation
**Problem**: Both BTC and ETH long, effective leverage 2x
**Solution**: Cross-pair hedge or reduce position limits when correlated

## Parameter Tuning Guide

### Conservative (Lower Risk)
```python
risk_aversion = 1.0        # High (2x skew)
comfort_zone = 0.2         # 20% (tighter)
urgency_start = 0.5        # 50% (earlier)
max_position_qty = 0.3     # Smaller positions
```

### Aggressive (Higher Returns)
```python
risk_aversion = 0.3        # Low (less skew)
comfort_zone = 0.5         # 50% (wider)
urgency_start = 0.8        # 80% (later)
max_position_qty = 0.5     # Larger positions
```

### Balanced (Recommended for $500 account)
```python
risk_aversion = 0.5        # Medium
comfort_zone = 0.3         # 30%
urgency_start = 0.7        # 70%
max_position_qty = 0.0005  # BTC (~$45)
```

## Testing Scenarios

### Scenario 1: Gradual Accumulation
```
GIVEN: Clean position (0)
WHEN: Market OBI consistently bullish
THEN: 
  - Should accumulate long position gradually
  - Skew should increase proportionally
  - Should reach optimal target (not max)
  - Should not trigger rebalancing
```

### Scenario 2: Adverse Fill
```
GIVEN: Neutral position
WHEN: Fill at bid, then price drops immediately
THEN:
  - Position now long
  - Skew should shift ask down, bid up
  - Ask size should increase
  - Should unwind within 2 minutes
```

### Scenario 3: Correlation Spike
```
GIVEN: BTC long 0.0003, ETH long 0.01
WHEN: Correlation jumps from 0.5 to 0.9
THEN:
  - Detect correlated exposure risk
  - Reduce new positions or
  - Place hedge in one instrument
```

### Scenario 4: Emergency Rebalance
```
GIVEN: Position at 92% of max
WHEN: Passive skewing ineffective
THEN:
  - Cancel all quotes
  - Place IOC market order (50% of imbalance)
  - Log warning
  - Resume skewed quoting
```

## Code Snippets

### Quick Skew Calculation
```python
def calculate_skew(position: Decimal, max_pos: Decimal) -> float:
    """Returns skew in basis points."""
    ratio = float(position) / float(max_pos)
    base_skew = -0.5 * 0.0004 * 120 * ratio * 10000
    
    if abs(ratio) > 0.7:
        urgency = ((abs(ratio) - 0.7) / 0.3) ** 2 * 7 + 3
        base_skew *= urgency
    
    return base_skew  # e.g., -12.5 bps if long 0.5 ratio
```

### Quick Size Calculation
```python
def calculate_sizes(position: Decimal, max_pos: Decimal, base: Decimal):
    """Returns (bid_size, ask_size)."""
    ratio = float(position) / float(max_pos)
    
    bid_mult = max(0.3, min(2.0, 1.0 - ratio * 0.5))
    ask_mult = max(0.3, min(2.0, 1.0 + ratio * 0.5))
    
    return base * Decimal(str(bid_mult)), base * Decimal(str(ask_mult))
```

---

**Last Updated**: January 27, 2026  
**Status**: Reference Documentation for v002 Implementation
