# Phase 4: Adaptive Spread & Correlation - Implementation Plan

**Goal**: Dynamic spread optimization and cross-pair correlation risk management

---

## Objectives

1. **Adaptive Spread**: 5-component dynamic spread calculation
2. **Fill Probability**: Optimize spread-fill tradeoff
3. **Cross-Pair Hedging**: Intelligent correlation-based position management
4. **Risk Limits**: Enhanced correlation-aware exposure limits
5. **Performance**: Maintain <100ms total latency

---

## 5-Component Adaptive Spread Model

### Spread Formula
```python
total_spread_bps = (
    base_spread +
    volatility_spread +
    inventory_spread +
    adverse_selection_spread +
    competition_spread
)
```

### Component Details

#### 1. Base Spread (1-2 bps)
- Minimum spread to ensure profitability
- Covers exchange fees + slippage
- Instrument-specific (BTC vs ETH)

#### 2. Volatility Spread (0-3 bps)
```python
vol_spread = k_vol * realized_volatility * urgency
```
- Widens during high volatility
- Protects against adverse moves
- Uses exponentially weighted volatility (last 100 ticks)

#### 3. Inventory Spread (0-2 bps)
```python
inv_spread = k_inv * abs(position_ratio) * inventory_risk
```
- Additional spread when holding large positions
- Works with inventory skewing
- Compounds protection

#### 4. Adverse Selection Spread (0-2 bps)
```python
as_spread = k_as * (1 - ml_confidence) * trade_intensity
```
- Wider spread when ML confidence is low
- Protects against informed traders
- Increases during high trade activity

#### 5. Competition Spread (-1 to +1 bps)
```python
comp_spread = k_comp * (our_rank - median_rank) * liquidity_factor
```
- Tighten if we're uncompetitive (not filling)
- Widen if we're too aggressive (adverse selection)
- Based on recent fill rate

---

## Cross-Pair Correlation Management

### Enhanced Correlation Monitor

#### Real-Time Correlation
- 5-minute rolling window
- Calculate every 10 seconds
- Exponentially weighted

#### Hedging Logic
```python
if correlation > 0.8 and positions_aligned:
    # Both long or both short
    offset_excess_via_smaller_position()
    reduce_new_quotes_on_correlated_side()
```

### Position Limits
```python
# Individual limits
btc_max = 0.0003 BTC
eth_max = 0.015 ETH

# Correlated exposure limit
if correlation > 0.8:
    combined_usd = abs(btc_pos * btc_price) + abs(eth_pos * eth_price)
    max_combined = total_capital * 0.12  # 12% instead of 15%
    
    if combined_usd > max_combined:
        reduce_quotes_on_larger_position()
```

### Cross-Pair Hedging
```python
# When BTC long and ETH short with high correlation
if correlation > 0.8:
    # Positions naturally hedge
    allow_slightly_larger_positions()
    
# When both long with high correlation  
if correlation > 0.8 and same_direction:
    # Concentrated risk
    reduce_position_targets()
    widen_spreads()
```

---

## Implementation Tasks

### Task 1: Adaptive Spread Calculator
**File**: `strategy/spread/adaptive_spread.py`
```python
class AdaptiveSpreadCalculator:
    def calculate_spread(
        volatility, position_ratio, ml_confidence,
        fill_rate, trade_intensity, ...
    ) -> float  # Total spread in bps
    
    def get_components() -> dict  # Individual components
```

### Task 2: Volatility Estimator
**File**: `strategy/spread/volatility_estimator.py`
```python
class VolatilityEstimator:
    def update(price) -> None
    def get_realized_volatility() -> float
    def get_ewma_volatility() -> float
```

### Task 3: Enhanced Correlation Manager
**File**: `strategy/correlation/correlation_manager.py`
```python
class EnhancedCorrelationManager:
    def update_correlation() -> float
    def check_hedging_opportunity() -> tuple
    def calculate_combined_exposure() -> float
    def get_correlation_adjusted_limits() -> dict
```

### Task 4: Integration with Strategy
**Update**: `hft_obi_bybit_spot_mm_lowcap_multipair_v002.py`
- Replace fixed spread with adaptive calculation
- Add volatility tracking
- Implement correlation-based position limits
- Add cross-pair hedging logic

### Task 5: Unit Tests
**File**: `strategy/tests/test_adaptive_spread.py`
- Test each spread component
- Test correlation calculations
- Test position limit adjustments
- Test hedging logic

---

## Spread Component Targets

| Component | Min | Typical | Max | Condition |
|-----------|-----|---------|-----|-----------|
| Base | 1 bps | 1.5 bps | 2 bps | Always |
| Volatility | 0 bps | 1 bps | 3 bps | High vol |
| Inventory | 0 bps | 0.5 bps | 2 bps | Large pos |
| Adverse Selection | 0 bps | 1 bps | 2 bps | Low ML conf |
| Competition | -1 bps | 0 bps | 1 bps | Fill rate |
| **TOTAL** | **1 bps** | **4 bps** | **10 bps** | |

---

## Performance Requirements

### Latency Targets
- Volatility update: <10μs
- Spread calculation: <50μs
- Correlation update: <100μs (every 10s)
- Total quote generation: <5ms (maintain)

### Accuracy Targets
- Volatility estimation: σ within 10% of realized
- Correlation estimation: ρ within 0.05 of true
- Fill rate optimization: 50-60% target

---

## Implementation Sequence

1. **Create spread module** (5 min)
2. **Implement VolatilityEstimator** (15 min)
3. **Implement AdaptiveSpreadCalculator** (20 min)
4. **Enhance CorrelationManager** (20 min)
5. **Write unit tests** (15 min)
6. **Integrate with v002 strategy** (25 min)
7. **Benchmark performance** (10 min)
8. **Deploy to AWS** (5 min)

**Total Estimated Time**: 115 minutes (~2 hours)

---

## Testing Strategy

### Unit Tests
```python
test_volatility_estimation()
test_spread_components()
test_spread_bounds()
test_correlation_hedging()
test_position_limits()
test_combined_exposure()
```

### Integration Tests
```python
test_adaptive_spread_integration()
test_correlation_based_quoting()
test_cross_pair_limits()
```

### Performance Tests
```python
test_volatility_update_latency()  # <10μs
test_spread_calc_latency()        # <50μs
test_correlation_update_latency() # <100μs
```

---

## Success Criteria

✅ Adaptive spread working (5 components)
✅ Spread adapts to market conditions
✅ Volatility estimation accurate
✅ Cross-pair correlation tracked
✅ Hedging opportunities identified
✅ Position limits enforced
✅ Latency targets met
✅ Unit tests passing (100%)
✅ Deployed to AWS

---

## Expected Performance Improvements

### With Adaptive Spreads
- **+10-15% returns**: Better spread optimization
- **+5% Sharpe**: Reduced volatility exposure
- **+20% fill rate**: Competitive pricing
- **-20% adverse selection**: Dynamic protection

### With Correlation Management
- **-30% max drawdown**: Hedged positions
- **+15% capital efficiency**: Better position sizing
- **-25% correlation risk**: Smart limits

---

## Risks & Mitigations

**Risk 1**: Spread too wide → miss fills
- *Mitigation*: Competition component tightens spread

**Risk 2**: Spread too tight → adverse selection
- *Mitigation*: AS component widens when needed

**Risk 3**: Correlation calculation lag
- *Mitigation*: Exponentially weighted, updates frequently

**Risk 4**: Position limits too restrictive
- *Mitigation*: Allow hedged positions to be larger

---

## Next Phase Preview (Phase 5)

After Phase 4:
- Multi-day backtesting
- Parameter optimization
- Stress testing
- Performance profiling
- Integration validation

---

**Ready to execute Phase 4 implementation!**
