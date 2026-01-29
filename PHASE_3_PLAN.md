# Phase 3: ML Infrastructure - Implementation Plan

**Goal**: Add machine learning OBI weighting to improve signal quality prediction

---

## Objectives

1. **Feature Engineering**: Extract 10 real-time features from market data
2. **ML Model**: Lightweight model for sub-millisecond inference
3. **Integration**: Use ML confidence to weight trading decisions
4. **Training**: Offline pipeline using historical data
5. **Validation**: Ensure latency <1ms per prediction

---

## Architecture Decision

### Option A: Online Learning Model (CHOSEN)
**Advantages**:
- Sub-100μs inference
- No external dependencies
- Adapts to changing markets
- Simple to deploy

**Implementation**: Exponentially Weighted Recursive Least Squares
```python
prediction = sigmoid(weights @ features)
# Update after observing outcome
weights += gain * (outcome - prediction)
```

### Option B: Tiny Neural Network
**Advantages**:
- Higher potential accuracy
- Non-linear patterns

**Disadvantages**:
- Requires training pipeline
- Less adaptive to regime changes
- More complexity

**Decision**: Start with Option A (online learning), can upgrade to Option B in v003

---

## Feature Set (10 Features)

### Market Microstructure
1. **OBI Value** - Current order book imbalance
2. **OBI Momentum** - Rate of change in OBI
3. **OBI Volatility** - Standard deviation of recent OBI values
4. **Spread Tightness** - (ask - bid) / mid (lower = better liquidity)

### Liquidity Metrics
5. **Top-of-Book Volume Ratio** - bid_vol / (bid_vol + ask_vol)
6. **Book Depth** - Total volume in top 5 levels
7. **Recent Fill Success Rate** - Last 10 quotes

### Timing Features
8. **Time Since Last Fill** - Normalized (0-1, capped at 1 min)
9. **Position Ratio** - Current position / max position (-1 to +1)
10. **Market Regime** - Time of day (cyclic encoding: sin(2π * hour / 24))

---

## Implementation Tasks

### Task 1: Feature Extraction Module
**File**: `strategy/ml/feature_engineering.py`
```python
class FeatureExtractor:
    def extract_features(state, book, position) -> np.ndarray[10]
```

### Task 2: Online Learning Model
**File**: `strategy/ml/online_model.py`
```python
class OnlineOBIPredictor:
    def predict(features) -> float  # Confidence 0-1
    def update(features, outcome)   # Online learning
```

### Task 3: Model State Persistence
**File**: `strategy/ml/model_weights/`
- Save/load model weights
- Version control
- Backup previous weights

### Task 4: Integration with Strategy
**Update**: `hft_obi_bybit_spot_mm_lowcap_multipair_v002.py`
- Add feature extraction in `_update_quotes()`
- Get ML prediction
- Use confidence to:
  - Weight OBI signal
  - Adjust optimal inventory target
  - Skip quoting if confidence <30%

### Task 5: Training Pipeline (Optional for Phase 3)
**File**: `strategy/ml/training_pipeline.py`
- Load historical order book data
- Generate features and labels
- Pre-train weights (optional - can start with zeros)
- Validate on holdout set

### Task 6: Unit Tests
**File**: `strategy/tests/test_ml.py`
- Feature extraction validation
- Model prediction range (0-1)
- Online update mechanism
- Latency benchmarks (<1ms)

---

## Performance Requirements

### Latency Targets
- Feature extraction: <500μs
- Model prediction: <100μs
- Total ML overhead: <1ms
- Overall quote generation: <5ms (maintain)

### Accuracy Targets
- Better than random (>50% on validation)
- Improve fill success rate by 10-15%
- Reduce adverse selection events

---

## Implementation Sequence

1. **Create ML module structure** (5 min)
2. **Implement FeatureExtractor** (15 min)
3. **Implement OnlineOBIPredictor** (20 min)
4. **Write unit tests** (15 min)
5. **Integrate with v002 strategy** (20 min)
6. **Benchmark latency** (10 min)
7. **Deploy to AWS** (5 min)

**Total Estimated Time**: 90 minutes

---

## Testing Strategy

### Unit Tests
```python
test_feature_extraction_shape()      # Verify 10 features
test_feature_ranges()                 # Check normalization
test_model_prediction_range()         # Output 0-1
test_online_update_convergence()      # Learning works
```

### Integration Tests
```python
test_ml_integration()                 # Features → Model → Quote
test_latency_benchmark()              # <1ms overhead
test_confidence_filtering()           # Skip when <30%
```

### Performance Validation
```python
# Measure on 1000 predictions
assert mean_latency < 0.001  # 1ms
assert p99_latency < 0.002   # 2ms
```

---

## Success Criteria

✅ Feature extraction working (10 features per instrument)
✅ Online model making predictions (0-1 confidence)
✅ Integration with quote generation
✅ ML confidence used to filter signals
✅ Latency <1ms verified
✅ Unit tests passing (100%)
✅ Deployed to AWS

---

## Risks & Mitigations

**Risk 1**: ML predictions too slow
- *Mitigation*: Use NumPy vectorization, pre-allocate arrays

**Risk 2**: Model doesn't learn useful patterns
- *Mitigation*: Start with simple online model, can enhance later

**Risk 3**: Feature calculation errors
- *Mitigation*: Extensive unit tests with edge cases

**Risk 4**: Overfitting to backtest data
- *Mitigation*: Use online learning (adapts continuously)

---

## Next Phase Preview (Phase 4)

After Phase 3, we'll add:
- Adverse selection prediction
- Adaptive spread components
- Cross-pair correlation hedging
- Fill probability estimation

---

**Ready to execute Phase 3 implementation!**
