# HFT OBI Multi-Pair ML Strategy Design
**Version**: v002 (Low-Capital Multi-Pair ML Enhanced)
**Status**: Design Phase
**Target Capital**: $500 - $5,000
**Created**: January 27, 2026

---

## Executive Summary

Enhancement of `hft_obi_bybit_spot_mm_lowcap_v001` to support:
1. **Multi-pair trading** (BTC/USDT + ETH/USDT simultaneous)
2. **ML-based OBI weighting** for signal quality prediction
3. **Adaptive spread calculation** using market microstructure

**Expected Performance Improvements**:
- 2x capital efficiency (dual instrument deployment)
- 15-25% better fill rates (ML spread optimization)
- 20-30% higher Sharpe ratio (diversification + smart weighting)
- Reduced drawdowns through correlation management

---

## 1. Multi-Pair Architecture

### 1.1 Core Design Principles

```python
# Single strategy instance manages multiple instruments
class MultiPairLowCapitalOBIMarketMaker(Strategy):
    """
    Multi-instrument OBI market maker with shared capital allocation.
    
    Key innovations:
    - Shared risk budget across instruments
    - Cross-pair correlation monitoring
    - Dynamic capital allocation per pair
    - Unified order management
    """
```

### 1.2 Instrument Management

#### Configuration
```python
@dataclass
class InstrumentConfig:
    """Per-instrument configuration."""
    instrument_id: str
    base_qty: Decimal  # Quote size
    max_position_qty: Decimal  # Max exposure
    weight: float  # Capital allocation weight (0-1)
    obi_sensitivity: float  # Instrument-specific OBI multiplier
    min_spread_bps: int
    max_spread_bps: int

class MultiPairMMConfig(StrategyConfig):
    """Multi-pair strategy configuration."""
    # Instruments
    instruments: list[InstrumentConfig] = [
        InstrumentConfig(
            instrument_id="BTCUSDT-SPOT.BYBIT",
            base_qty=Decimal("0.0001"),  # ~$9 per quote
            max_position_qty=Decimal("0.0003"),  # $27 max (60% of capital)
            weight=0.6,  # 60% capital allocation
            obi_sensitivity=1.0,
            min_spread_bps=1,
            max_spread_bps=5,
        ),
        InstrumentConfig(
            instrument_id="ETHUSDT-SPOT.BYBIT",
            base_qty=Decimal("0.005"),  # ~$9 per quote
            max_position_qty=Decimal("0.015"),  # $27 max (40% of capital)
            weight=0.4,  # 40% capital allocation
            obi_sensitivity=1.2,  # ETH more volatile, scale OBI
            min_spread_bps=1,
            max_spread_bps=6,
        ),
    ]
    
    # Shared parameters
    total_capital_usd: float = 500.0
    max_total_exposure_pct: float = 0.15  # 15% total at risk
    emergency_liquidation_loss_usd: float = -100.0
    
    # Correlation management
    max_correlated_exposure: float = 0.7  # Max % in same direction
    correlation_lookback_seconds: int = 300  # 5 min
```

#### State Management
```python
class InstrumentState:
    """Per-instrument state tracking."""
    instrument_id: InstrumentId
    book: OrderBook
    obi_history: deque
    obi_ema: float
    net_position: Decimal
    unrealized_pnl: Decimal
    active_bid_order_id: Optional[ClientOrderId]
    active_ask_order_id: Optional[ClientOrderId]
    last_quote_time: float
    last_fill_time: float
    
    # ML state
    ml_obi_weight: float  # 0-1, confidence in OBI signal
    predicted_fill_probability: float
    predicted_adverse_selection_risk: float

# Strategy maintains dict of states
self._instruments: dict[InstrumentId, InstrumentState] = {}
```

### 1.3 Order Book Subscription Model

```python
def on_start(self) -> None:
    """Initialize all instruments."""
    for config in self.config.instruments:
        instrument_id = InstrumentId.from_str(config.instrument_id)
        instrument = self.cache.instrument(instrument_id)
        
        # Create order book
        book = OrderBook(
            instrument_id=instrument_id,
            book_type=BookType.L2_MBP,
        )
        
        # Subscribe to deltas
        self.subscribe_order_book_deltas(instrument_id)
        
        # Initialize state
        self._instruments[instrument_id] = InstrumentState(
            instrument_id=instrument_id,
            book=book,
            obi_history=deque(maxlen=15),
            # ... other initialization
        )
```

### 1.4 Capital Allocation Logic

```python
def _allocate_capital(self) -> dict[InstrumentId, Decimal]:
    """
    Dynamic capital allocation based on:
    - Base weights from config
    - Current positions
    - Correlation state
    - ML signal confidence
    """
    total_exposure = sum(abs(state.net_position * mid_price)
                        for state in self._instruments.values())
    
    available_capital = self.config.total_capital_usd - total_exposure
    
    allocations = {}
    for inst_id, state in self._instruments.items():
        config = self._get_instrument_config(inst_id)
        
        # Base allocation
        base_allocation = available_capital * config.weight
        
        # Adjust for ML confidence
        ml_adjustment = 1.0 + (state.ml_obi_weight - 0.5) * 0.4
        
        # Adjust for correlation risk
        correlation_penalty = self._calculate_correlation_penalty(inst_id)
        
        allocations[inst_id] = base_allocation * ml_adjustment * correlation_penalty
    
    return allocations
```

---

## 2. ML OBI Weighting System

### 2.1 Problem Statement

**Goal**: Predict OBI signal quality to weight trading decisions

**Features**:
- Historical OBI values and patterns
- Order book shape metrics
- Volume imbalance persistence
- Recent fill success rate
- Time of day / market regime

**Target**: Probability that OBI signal leads to profitable fill

### 2.2 Model Architecture

#### Option A: Lightweight Online Model (Recommended for HFT)
```python
class OnlineOBIWeightingModel:
    """
    Fast online learning model using exponential decay.
    
    Advantages:
    - Sub-millisecond inference
    - No external dependencies
    - Adapts to changing markets
    - Low memory footprint
    
    Based on: Exponentially Weighted Least Squares
    """
    
    def __init__(self, decay_factor: float = 0.99):
        self.decay = decay_factor
        self.weights = np.zeros(10)  # Feature weights
        self.covariance = np.eye(10) * 1000  # Uncertainty
        
    def predict(self, features: np.ndarray) -> float:
        """Return OBI signal weight (0-1)."""
        score = np.dot(self.weights, features)
        return 1.0 / (1.0 + np.exp(-score))  # Sigmoid
    
    def update(self, features: np.ndarray, outcome: float):
        """Online update after observing outcome."""
        prediction = self.predict(features)
        error = outcome - prediction
        
        # Recursive least squares update
        gain = self.covariance @ features
        gain /= (1 + features @ gain)
        
        self.weights += gain * error
        self.covariance -= np.outer(gain, gain)
        self.covariance /= self.decay
```

#### Option B: Lightweight Neural Network (Higher accuracy)
```python
class FastOBINet:
    """
    Tiny neural network optimized for latency.
    
    Architecture:
    - Input: 10 features
    - Hidden: 16 neurons (ReLU)
    - Output: 1 sigmoid (confidence)
    
    Inference time: ~100 microseconds
    """
    
    def __init__(self):
        # Pre-trained weights loaded at startup
        self.w1 = np.random.randn(10, 16) * 0.1
        self.b1 = np.zeros(16)
        self.w2 = np.random.randn(16, 1) * 0.1
        self.b2 = np.zeros(1)
    
    def forward(self, x: np.ndarray) -> float:
        """Fast forward pass."""
        h = np.maximum(0, x @ self.w1 + self.b1)  # ReLU
        return 1.0 / (1.0 + np.exp(-(h @ self.w2 + self.b2)[0]))
```

### 2.3 Feature Engineering

```python
def _extract_ml_features(self, state: InstrumentState) -> np.ndarray:
    """
    Extract 10 features for ML model.
    
    Returns: np.ndarray of shape (10,)
    """
    features = np.zeros(10)
    
    # [0] Current OBI value
    features[0] = state.obi_ema
    
    # [1] OBI momentum (change rate)
    if len(state.obi_history) >= 2:
        features[1] = state.obi_history[-1] - state.obi_history[-2]
    
    # [2] OBI volatility
    if len(state.obi_history) >= 5:
        features[2] = np.std(list(state.obi_history)[-5:])
    
    # [3] Spread tightness (smaller = better liquidity)
    best_bid = state.book.best_bid_price()
    best_ask = state.book.best_ask_price()
    if best_bid and best_ask:
        mid = (float(best_bid) + float(best_ask)) / 2
        features[3] = (float(best_ask) - float(best_bid)) / mid
    
    # [4] Top-of-book volume ratio
    bids = state.book.bids()
    asks = state.book.asks()
    if bids and asks:
        bid_vol = float(bids[0].size())
        ask_vol = float(asks[0].size())
        features[4] = bid_vol / (bid_vol + ask_vol)
    
    # [5] Order book depth (total volume in top 5)
    total_bid_vol = sum(float(b.size()) for b in bids[:5])
    total_ask_vol = sum(float(a.size()) for a in asks[:5])
    features[5] = total_bid_vol + total_ask_vol
    
    # [6] Recent fill success rate (last 10 quotes)
    features[6] = self._calculate_recent_fill_rate(state)
    
    # [7] Time since last fill (normalized)
    time_since_fill = time.time() - state.last_fill_time
    features[7] = min(time_since_fill / 60.0, 1.0)  # Cap at 1 min
    
    # [8] Current position ratio (-1 to +1)
    config = self._get_instrument_config(state.instrument_id)
    features[8] = float(state.net_position) / float(config.max_position_qty)
    
    # [9] Market regime (time of day cyclic encoding)
    hour = datetime.now().hour
    features[9] = np.sin(2 * np.pi * hour / 24)
    
    return features
```

### 2.4 Training Pipeline (Offline)

```python
class MLTrainingPipeline:
    """
    Offline training pipeline using historical data.
    
    Process:
    1. Load order book snapshots + fills
    2. Calculate features at each timestamp
    3. Label outcomes (fill = 1, no fill = 0, adverse = -1)
    4. Train model
    5. Validate on holdout set
    6. Export weights for production
    """
    
    def train_from_backtest_data(
        self,
        orderbook_data_path: str,
        fills_data_path: str,
    ):
        """Train model on historical backtest data."""
        # Load data
        ob_deltas = self._load_orderbook_deltas(orderbook_data_path)
        fills = self._load_fills(fills_data_path)
        
        # Generate features and labels
        X, y = self._generate_training_data(ob_deltas, fills)
        
        # Split train/val
        split_idx = int(len(X) * 0.8)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]
        
        # Train
        model = FastOBINet()
        self._train_sgd(model, X_train, y_train, epochs=100)
        
        # Validate
        val_accuracy = self._evaluate(model, X_val, y_val)
        print(f"Validation accuracy: {val_accuracy:.2%}")
        
        # Export
        self._export_weights(model, "obi_weights_v002.npz")
```

### 2.5 Integration with Trading Logic

```python
def _update_quotes(self, instrument_id: InstrumentId, now: float):
    """Update quotes with ML-weighted OBI."""
    state = self._instruments[instrument_id]
    
    # Extract features
    features = self._extract_ml_features(state)
    
    # Get ML weight
    ml_weight = self.ml_model.predict(features)
    state.ml_obi_weight = ml_weight
    
    # Adjust OBI threshold based on confidence
    effective_threshold = self.config.obi_entry_threshold / ml_weight
    
    # Only quote if ML has sufficient confidence (>0.3)
    if ml_weight < 0.3:
        self.log.debug(f"Low ML confidence ({ml_weight:.2f}), skipping quote")
        return
    
    # Determine quoting sides with weighted OBI
    weighted_obi = state.obi_ema * ml_weight
    should_quote_bid = weighted_obi > -effective_threshold
    should_quote_ask = weighted_obi < effective_threshold
    
    # Rest of quoting logic...
```

---

## 3. Adaptive Spread Calculation

### 3.1 Design Goals

**Current**: Fixed spread range (1-5 bps) + volatility adjustment
**Target**: Dynamic spread based on:
- Real-time fill probability estimation
- Adverse selection risk
- Inventory costs
- Competition (other MM activity)

### 3.2 Spread Components Model

```python
def _calculate_adaptive_spread(
    self,
    state: InstrumentState,
    ml_features: np.ndarray,
) -> float:
    """
    Calculate optimal spread in basis points.
    
    Spread = Base + Volatility + Inventory + Adverse Selection - Competition
    """
    
    # 1. Base spread (minimum for profitability)
    base_spread = self._get_base_spread(state)  # 1-2 bps
    
    # 2. Volatility component (wider when volatile)
    volatility_spread = self._calculate_volatility_spread(state)
    
    # 3. Inventory cost (wider when imbalanced)
    inventory_spread = self._calculate_inventory_spread(state)
    
    # 4. Adverse selection protection (wider when toxic flow)
    adverse_selection_spread = self._predict_adverse_selection(
        state, ml_features
    )
    
    # 5. Competition adjustment (tighter when others are wide)
    competition_adjustment = self._estimate_competition_spread(state)
    
    total_spread = (
        base_spread
        + volatility_spread
        + inventory_spread
        + adverse_selection_spread
        - competition_adjustment
    )
    
    # Clamp to configured limits
    config = self._get_instrument_config(state.instrument_id)
    return np.clip(total_spread, config.min_spread_bps, config.max_spread_bps)
```

### 3.3 Component Implementations

#### Volatility Spread
```python
def _calculate_volatility_spread(self, state: InstrumentState) -> float:
    """
    Spread based on recent price volatility.
    
    Uses: Rolling standard deviation of mid-price changes
    """
    if len(state.obi_history) < 10:
        return 1.0  # Default
    
    # Calculate mid-price volatility
    price_changes = []
    for i in range(1, min(20, len(state.obi_history))):
        # Approximate price change from OBI shifts
        price_changes.append(abs(state.obi_history[-i] - state.obi_history[-i-1]))
    
    volatility = np.std(price_changes)
    
    # Scale to bps (0-3 bps)
    return min(volatility * 100, 3.0)
```

#### Inventory Spread
```python
def _calculate_inventory_spread(self, state: InstrumentState) -> float:
    """
    Spread adjustment based on position imbalance.
    
    Larger position = wider spread to encourage offsetting
    """
    config = self._get_instrument_config(state.instrument_id)
    
    # Position ratio (-1 to +1)
    position_ratio = float(state.net_position) / float(config.max_position_qty)
    
    # Quadratic penalty (0-2 bps)
    return abs(position_ratio) ** 2 * 2.0
```

#### Adverse Selection Prediction
```python
def _predict_adverse_selection(
    self,
    state: InstrumentState,
    ml_features: np.ndarray,
) -> float:
    """
    Predict probability of adverse selection and widen spread.
    
    Adverse selection = filling just before price moves against us
    
    Indicators:
    - Sudden OBI shifts
    - Large orders hitting the book
    - Recent adverse fills
    """
    # Use ML model to predict adverse selection risk
    # (Can be same model or separate one)
    
    adverse_prob = self._estimate_adverse_prob(ml_features)
    
    # Convert to spread adjustment (0-4 bps)
    return adverse_prob * 4.0

def _estimate_adverse_prob(self, features: np.ndarray) -> float:
    """Estimate adverse selection probability from features."""
    # High OBI volatility (feature[2]) indicates toxic flow
    obi_volatility = features[2]
    
    # Sudden OBI changes (feature[1]) = informed traders
    obi_momentum = abs(features[1])
    
    # Combine heuristically (can replace with ML)
    adverse_score = obi_volatility * 2 + obi_momentum * 3
    return min(adverse_score, 1.0)
```

#### Competition Estimation
```python
def _estimate_competition_spread(self, state: InstrumentState) -> float:
    """
    Estimate competitive spread from order book.
    
    If we see tight quotes from other MMs, we can be tighter too.
    If book is wide, we don't need to compete.
    """
    best_bid = state.book.best_bid_price()
    best_ask = state.book.best_ask_price()
    
    if not best_bid or not best_ask:
        return 0.0
    
    # Current market spread
    mid = (float(best_bid) + float(best_ask)) / 2
    market_spread_bps = ((float(best_ask) - float(best_bid)) / mid) * 10000
    
    # If market is tight (<5 bps), compete by tightening (-1 bps)
    # If market is wide (>10 bps), no need to compete
    if market_spread_bps < 5:
        return 1.0  # Tighten by 1 bps
    elif market_spread_bps > 10:
        return 0.0  # No adjustment
    else:
        # Linear interpolation
        return (10 - market_spread_bps) / 5
```

### 3.4 Fill Probability Optimization

```python
def _optimize_spread_for_fill_rate(
    self,
    state: InstrumentState,
    target_fill_rate: float = 0.4,  # 40% target
) -> float:
    """
    Adjust spread to hit target fill rate.
    
    Uses: Exponential smoothing of realized fill rate
    """
    # Track historical fill rate
    if not hasattr(state, 'fill_rate_ema'):
        state.fill_rate_ema = 0.4  # Initialize
    
    # Calculate recent fill rate (last 10 quotes)
    recent_fills = self._count_recent_fills(state, lookback=10)
    recent_quotes = self._count_recent_quotes(state, lookback=10)
    
    if recent_quotes > 0:
        recent_fill_rate = recent_fills / recent_quotes
        
        # Update EMA
        alpha = 0.1
        state.fill_rate_ema = (
            alpha * recent_fill_rate + (1 - alpha) * state.fill_rate_ema
        )
    
    # Adjust spread
    # If fill rate too low, tighten (negative adjustment)
    # If fill rate too high, widen (positive adjustment)
    fill_rate_error = state.fill_rate_ema - target_fill_rate
    
    return fill_rate_error * 5.0  # -2.5 to +2.5 bps adjustment
```

---

## 4. Industry-Standard Inventory Management & Skewing

### 4.1 Theoretical Foundation

**Avellaneda-Stoikov Framework** (2008)
Market makers face fundamental tradeoff:
- **Tighter spreads** → More fills → Higher inventory risk
- **Wider spreads** → Fewer fills → Lower inventory risk

Optimal strategy: Dynamic spread/skew based on:
1. Current inventory position
2. Inventory risk aversion
3. Market volatility
4. Time horizon

### 4.2 Inventory Risk Model

```python
class InventoryRiskManager:
    """
    Sophisticated inventory management using quantitative models.
    
    Based on:
    - Avellaneda-Stoikov optimal market making
    - Cartea-Jaimungal high-frequency models
    - Industry best practices from proprietary trading firms
    """
    
    def __init__(self, config: InstrumentConfig):
        # Risk parameters
        self.risk_aversion = 0.5  # γ (gamma) in A-S model
        self.volatility = 0.02  # σ (estimated volatility)
        self.time_horizon = 120.0  # T (seconds to target inventory)
        
        # Inventory targets
        self.target_inventory = Decimal(0)  # Neutral position
        self.max_inventory = config.max_position_qty
        self.comfort_zone = config.max_position_qty * Decimal("0.3")  # 30%
        
        # Tracking
        self.inventory_half_life = 30.0  # seconds
        self.last_rebalance_time = 0.0
```

### 4.3 Optimal Inventory Target (Dynamic)

```python
def _calculate_optimal_inventory_target(
    self,
    state: InstrumentState,
    market_conditions: dict,
) -> Decimal:
    """
    Calculate optimal inventory target based on market state.
    
    Target shifts from 0 based on:
    - Directional signals (OBI, ML predictions)
    - Market momentum
    - Cross-pair hedging opportunities
    - Time of day / liquidity regime
    """
    base_target = Decimal(0)  # Start neutral
    
    # 1. Directional bias from ML model
    if state.ml_obi_weight > 0.6:  # High confidence
        if state.obi_ema > 0.10:  # Bullish OBI
            base_target += self.max_inventory * Decimal("0.2")  # Allow 20% long bias
        elif state.obi_ema < -0.10:  # Bearish OBI
            base_target -= self.max_inventory * Decimal("0.2")  # Allow 20% short bias
    
    # 2. Cross-pair hedging signal
    # If BTC is long and ETH correlation high, allow ETH short bias
    cross_hedge_adjustment = self._calculate_hedge_target(state)
    base_target += cross_hedge_adjustment
    
    # 3. Time decay towards neutral (end of trading period)
    time_remaining = self._get_time_to_close()  # seconds to session end
    if time_remaining < 300:  # Last 5 minutes
        # Exponentially decay target to zero
        decay_factor = time_remaining / 300.0
        base_target *= Decimal(str(decay_factor))
    
    return base_target

def _calculate_hedge_target(self, state: InstrumentState) -> Decimal:
    """Calculate cross-pair hedging inventory target."""
    if len(self._instruments) < 2:
        return Decimal(0)
    
    # Get correlated instrument
    other_states = [s for s in self._instruments.values() if s != state]
    if not other_states:
        return Decimal(0)
    
    other_state = other_states[0]
    
    # If correlation high and other has position, allow offsetting here
    correlation = self.correlation_monitor.calculate_correlation(
        state.instrument_id,
        other_state.instrument_id,
    )
    
    if abs(correlation) > 0.8:
        # Highly correlated - allow offsetting position
        other_position_ratio = float(other_state.net_position) / float(
            self._get_instrument_config(other_state.instrument_id).max_position_qty
        )
        
        # Opposite position (hedge)
        hedge_ratio = -correlation * other_position_ratio
        return self.max_inventory * Decimal(str(hedge_ratio * 0.5))  # 50% hedge
    
    return Decimal(0)
```

### 4.4 Price Skewing (Avellaneda-Stoikov)

```python
def _calculate_optimal_skew(
    self,
    state: InstrumentState,
    mid_price: float,
) -> float:
    """
    Calculate optimal price skew based on inventory.
    
    Avellaneda-Stoikov Formula:
    δ_bid = δ_ask + (γ * σ² * (T - t) * q) / 2
    
    where:
    - δ = half-spread
    - γ = risk aversion
    - σ = volatility
    - T-t = time remaining
    - q = inventory position
    """
    # Current inventory
    current_inventory = float(state.net_position)
    optimal_target = float(self._calculate_optimal_inventory_target(state, {}))
    inventory_imbalance = current_inventory - optimal_target
    
    # Normalize by max position
    config = self._get_instrument_config(state.instrument_id)
    normalized_imbalance = inventory_imbalance / float(config.max_position_qty)
    
    # Time-scaled risk aversion
    time_remaining = min(self.time_horizon, 120.0)  # Cap at 2 minutes
    time_factor = time_remaining / self.time_horizon
    
    # Calculate skew in basis points
    # Higher inventory → skew prices down (encourage selling)
    # Lower inventory → skew prices up (encourage buying)
    skew_bps = (
        -self.risk_aversion
        * (self.volatility ** 2)
        * time_factor
        * normalized_imbalance
        * 10000  # Convert to bps
    )
    
    # Additional urgency factor when approaching limits
    urgency_multiplier = self._calculate_urgency_multiplier(
        abs(normalized_imbalance)
    )
    skew_bps *= urgency_multiplier
    
    return skew_bps

def _calculate_urgency_multiplier(self, position_ratio: float) -> float:
    """
    Increase skew urgency as position approaches limits.
    
    position_ratio: 0.0 to 1.0 (fraction of max position)
    
    Returns: 1.0 to 10.0 (multiplier for skew)
    """
    if position_ratio < 0.3:  # Comfort zone
        return 1.0
    elif position_ratio < 0.7:  # Warning zone
        # Linear increase from 1.0 to 3.0
        return 1.0 + (position_ratio - 0.3) * 5.0
    else:  # Danger zone (>70% of max)
        # Exponential increase
        excess = position_ratio - 0.7
        return 3.0 + (excess / 0.3) ** 2 * 7.0  # Up to 10x at max
```

### 4.5 Size Skewing (Asymmetric Quoting)

```python
def _calculate_asymmetric_quote_sizes(
    self,
    state: InstrumentState,
    base_size: Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Adjust bid/ask sizes based on inventory.
    
    When long:
    - Smaller bid size (don't accumulate more)
    - Larger ask size (eager to sell)
    
    When short:
    - Larger bid size (eager to buy back)
    - Smaller ask size (don't accumulate more)
    """
    config = self._get_instrument_config(state.instrument_id)
    optimal_target = self._calculate_optimal_inventory_target(state, {})
    inventory_imbalance = state.net_position - optimal_target
    
    # Normalize imbalance (-1 to +1)
    normalized_imbalance = float(inventory_imbalance) / float(config.max_position_qty)
    
    # Calculate size adjustments (0.5x to 2.0x of base)
    # When long (positive imbalance):
    #   - Bid size reduced (less eager to buy)
    #   - Ask size increased (more eager to sell)
    bid_multiplier = 1.0 - (normalized_imbalance * 0.5)
    ask_multiplier = 1.0 + (normalized_imbalance * 0.5)
    
    # Clamp multipliers
    bid_multiplier = max(0.3, min(2.0, bid_multiplier))
    ask_multiplier = max(0.3, min(2.0, ask_multiplier))
    
    bid_size = base_size * Decimal(str(bid_multiplier))
    ask_size = base_size * Decimal(str(ask_multiplier))
    
    # Round to instrument precision
    instrument = self.cache.instrument(state.instrument_id)
    bid_size = instrument.make_qty(float(bid_size))
    ask_size = instrument.make_qty(float(ask_size))
    
    return bid_size, ask_size
```

### 4.6 Inventory-Aware Quote Generation

```python
def _generate_inventory_aware_quotes(
    self,
    state: InstrumentState,
    mid_price: float,
    base_spread: float,
) -> tuple[Price, Price, Quantity, Quantity]:
    """
    Generate bid/ask quotes with inventory skewing.
    
    Returns: (bid_price, ask_price, bid_size, ask_size)
    """
    config = self._get_instrument_config(state.instrument_id)
    instrument = self.cache.instrument(state.instrument_id)
    
    # 1. Calculate optimal price skew
    price_skew_bps = self._calculate_optimal_skew(state, mid_price)
    price_skew = (price_skew_bps / 10000) * mid_price
    
    # 2. Apply skew to mid price (shifts entire quote)
    skewed_mid = mid_price + price_skew
    
    # 3. Calculate half-spread
    half_spread = (base_spread / 10000) * mid_price / 2
    
    # 4. Generate symmetric quotes around skewed mid
    bid_price = instrument.make_price(skewed_mid - half_spread)
    ask_price = instrument.make_price(skewed_mid + half_spread)
    
    # 5. Calculate asymmetric sizes
    bid_size, ask_size = self._calculate_asymmetric_quote_sizes(
        state,
        config.base_qty,
    )
    
    # 6. Verify we're not quoting in direction of limit
    position_ratio = abs(float(state.net_position)) / float(config.max_position_qty)
    
    if position_ratio > 0.9:  # At 90% of max position
        if state.net_position > 0:  # Long position
            # Don't quote bid (don't buy more)
            bid_size = instrument.make_qty(0)
        else:  # Short position
            # Don't quote ask (don't sell more)
            ask_size = instrument.make_qty(0)
    
    return bid_price, ask_price, bid_size, ask_size
```

### 4.7 Inventory Rebalancing Logic

```python
def _check_inventory_rebalancing(self, state: InstrumentState) -> bool:
    """
    Determine if active rebalancing needed.
    
    Triggers:
    1. Position exceeds comfort zone for too long
    2. Approaching position limits
    3. End of trading session
    4. High adverse selection detected
    """
    config = self._get_instrument_config(state.instrument_id)
    optimal_target = self._calculate_optimal_inventory_target(state, {})
    inventory_imbalance = state.net_position - optimal_target
    
    position_ratio = abs(float(inventory_imbalance)) / float(config.max_position_qty)
    
    # Trigger 1: In danger zone (>70%) for >30 seconds
    if position_ratio > 0.7:
        time_in_danger = time.time() - state.last_rebalance_time
        if time_in_danger > 30.0:
            return True
    
    # Trigger 2: Critical zone (>90%)
    if position_ratio > 0.9:
        return True  # Immediate rebalance
    
    # Trigger 3: Time-based (every 60s if outside comfort zone)
    if position_ratio > 0.3:
        time_since_rebalance = time.time() - state.last_rebalance_time
        if time_since_rebalance > 60.0:
            return True
    
    return False

def _execute_inventory_rebalance(self, state: InstrumentState):
    """
    Actively rebalance inventory using market orders.
    
    Only used in extreme cases - normally rely on skewing.
    """
    config = self._get_instrument_config(state.instrument_id)
    optimal_target = self._calculate_optimal_inventory_target(state, {})
    inventory_imbalance = state.net_position - optimal_target
    
    # Cancel existing quotes
    self._cancel_all_quotes(state)
    
    # Calculate rebalance size (move 50% towards target)
    rebalance_qty = abs(inventory_imbalance) * Decimal("0.5")
    
    if inventory_imbalance > 0:  # Long → need to sell
        # Place aggressive ask (market-like limit order)
        best_bid = state.book.best_bid_price()
        if best_bid:
            aggressive_ask = best_bid  # Cross the spread
            
            order = self.order_factory.limit(
                instrument_id=state.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.cache.instrument(state.instrument_id).make_qty(
                    float(rebalance_qty)
                ),
                price=aggressive_ask,
                time_in_force=TimeInForce.IOC,  # Immediate or cancel
            )
            
            self.submit_order(order)
            self.log.warning(
                f"REBALANCING: Selling {rebalance_qty} at {aggressive_ask}",
                LogColor.YELLOW,
            )
    
    else:  # Short → need to buy
        # Place aggressive bid
        best_ask = state.book.best_ask_price()
        if best_ask:
            aggressive_bid = best_ask  # Cross the spread
            
            order = self.order_factory.limit(
                instrument_id=state.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.cache.instrument(state.instrument_id).make_qty(
                    float(rebalance_qty)
                ),
                price=aggressive_bid,
                time_in_force=TimeInForce.IOC,
            )
            
            self.submit_order(order)
            self.log.warning(
                f"REBALANCING: Buying {rebalance_qty} at {aggressive_bid}",
                LogColor.YELLOW,
            )
    
    state.last_rebalance_time = time.time()
```

### 4.8 Cross-Pair Inventory Hedging

```python
def _calculate_cross_pair_hedge_opportunity(
    self,
) -> Optional[tuple[InstrumentId, OrderSide, Decimal]]:
    """
    Identify cross-pair hedging opportunities.
    
    Example: If long BTC and correlation with ETH is high,
    consider shorting ETH to reduce overall delta exposure.
    """
    if len(self._instruments) < 2:
        return None
    
    # Get both instrument states
    btc_state = self._instruments.get(self.btc_instrument_id)
    eth_state = self._instruments.get(self.eth_instrument_id)
    
    if not btc_state or not eth_state:
        return None
    
    # Calculate correlation
    correlation = self.correlation_monitor.calculate_correlation(
        self.btc_instrument_id,
        self.eth_instrument_id,
    )
    
    # Only hedge if correlation > 0.8
    if abs(correlation) < 0.8:
        return None
    
    # Calculate USD positions
    btc_mid = self._get_mid_price(btc_state)
    eth_mid = self._get_mid_price(eth_state)
    
    btc_position_usd = float(btc_state.net_position) * btc_mid
    eth_position_usd = float(eth_state.net_position) * eth_mid
    
    # If both same direction and large, hedge
    if btc_position_usd * eth_position_usd > 0:  # Same sign
        total_directional = abs(btc_position_usd) + abs(eth_position_usd)
        
        if total_directional > self.config.total_capital_usd * 0.3:  # >30% exposure
            # Decide which to hedge
            if abs(btc_position_usd) > abs(eth_position_usd):
                # Hedge via ETH (smaller position)
                hedge_usd = total_directional * 0.3  # Reduce by 30%
                hedge_qty = Decimal(str(hedge_usd / eth_mid))
                
                if btc_position_usd > 0:  # Both long → short ETH
                    return (self.eth_instrument_id, OrderSide.SELL, hedge_qty)
                else:  # Both short → long ETH
                    return (self.eth_instrument_id, OrderSide.BUY, hedge_qty)
            else:
                # Hedge via BTC
                hedge_usd = total_directional * 0.3
                hedge_qty = Decimal(str(hedge_usd / btc_mid))
                
                if eth_position_usd > 0:
                    return (self.btc_instrument_id, OrderSide.SELL, hedge_qty)
                else:
                    return (self.btc_instrument_id, OrderSide.BUY, hedge_qty)
    
    return None
```

### 4.9 Inventory Risk Metrics

```python
@dataclass
class InventoryRiskMetrics:
    """Real-time inventory risk metrics."""
    
    # Per-instrument
    current_position: Decimal
    optimal_target: Decimal
    position_ratio: float  # As % of max (0-1)
    inventory_age_seconds: float
    unrealized_pnl: Decimal
    
    # Risk measures
    var_1min: float  # 1-minute Value at Risk
    volatility_contribution: float  # Position * volatility
    time_to_rebalance: float  # Estimated seconds to neutral
    
    # Skewing
    current_skew_bps: float
    urgency_multiplier: float
    
    # Cross-pair
    correlated_exposure_usd: float
    hedge_delta: float  # Net delta after correlation adjustment

def _calculate_inventory_risk_metrics(
    self,
    state: InstrumentState,
) -> InventoryRiskMetrics:
    """Calculate comprehensive inventory risk metrics."""
    config = self._get_instrument_config(state.instrument_id)
    mid_price = self._get_mid_price(state)
    
    # Position metrics
    optimal_target = self._calculate_optimal_inventory_target(state, {})
    position_ratio = abs(float(state.net_position)) / float(config.max_position_qty)
    
    # Risk measures
    position_usd = float(state.net_position) * mid_price
    volatility = self.volatility  # From model
    var_1min = abs(position_usd) * volatility * np.sqrt(1/60)  # 1-min VaR
    
    # Time to rebalance estimate
    if state.net_position != optimal_target:
        avg_fill_rate = 0.4  # 40% of quotes fill
        avg_fill_size = float(config.base_qty)
        fills_per_minute = avg_fill_rate * (60 / (self.config.quote_refresh_interval_ms / 1000))
        volume_per_minute = fills_per_minute * avg_fill_size
        imbalance = abs(float(state.net_position - optimal_target))
        time_to_rebalance = (imbalance / volume_per_minute) * 60 if volume_per_minute > 0 else 999
    else:
        time_to_rebalance = 0
    
    return InventoryRiskMetrics(
        current_position=state.net_position,
        optimal_target=optimal_target,
        position_ratio=position_ratio,
        inventory_age_seconds=time.time() - state.last_fill_time,
        unrealized_pnl=state.unrealized_pnl,
        var_1min=var_1min,
        volatility_contribution=abs(position_usd * volatility),
        time_to_rebalance=time_to_rebalance,
        current_skew_bps=self._calculate_optimal_skew(state, mid_price),
        urgency_multiplier=self._calculate_urgency_multiplier(position_ratio),
        correlated_exposure_usd=0.0,  # Calculated separately
        hedge_delta=0.0,
    )
```

### 4.10 Integration with Main Strategy

```python
def _update_quotes(self, instrument_id: InstrumentId, now: float):
    """
    Enhanced quote generation with inventory management.
    
    Flow:
    1. Calculate base spread (from adaptive spread logic)
    2. Extract ML features
    3. Get ML weight
    4. Check inventory risk
    5. Generate inventory-aware quotes
    6. Submit orders
    """
    state = self._instruments[instrument_id]
    
    # Check if rebalancing needed
    if self._check_inventory_rebalancing(state):
        self._execute_inventory_rebalance(state)
        return  # Skip normal quoting during rebalance
    
    # Check cross-pair hedge opportunity
    hedge_opp = self._calculate_cross_pair_hedge_opportunity()
    if hedge_opp:
        self._execute_cross_pair_hedge(*hedge_opp)
    
    # Get mid price
    best_bid = state.book.best_bid_price()
    best_ask = state.book.best_ask_price()
    if not best_bid or not best_ask:
        return
    
    mid = (float(best_bid) + float(best_ask)) / 2.0
    
    # Calculate adaptive spread
    ml_features = self._extract_ml_features(state)
    base_spread_bps = self._calculate_adaptive_spread(state, ml_features)
    
    # Generate inventory-aware quotes (includes skewing)
    bid_price, ask_price, bid_size, ask_size = self._generate_inventory_aware_quotes(
        state,
        mid,
        base_spread_bps,
    )
    
    # ML confidence check
    ml_weight = self.ml_model.predict(ml_features)
    if ml_weight < 0.3:
        return  # Skip if low confidence
    
    # Cancel old orders
    self._cancel_quotes(state)
    
    # Submit new orders (if sizes > 0)
    if bid_size > 0:
        bid_order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=bid_size,
            price=bid_price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
        self.submit_order(bid_order)
        state.active_bid_order_id = bid_order.client_order_id
    
    if ask_size > 0:
        ask_order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.SELL,
            quantity=ask_size,
            price=ask_price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
        self.submit_order(ask_order)
        state.active_ask_order_id = ask_order.client_order_id
    
    # Log inventory state periodically
    if self._order_count % 50 == 0:
        metrics = self._calculate_inventory_risk_metrics(state)
        self.log.info(
            f"{instrument_id.symbol} | "
            f"Pos={metrics.current_position} (target={metrics.optimal_target}) | "
            f"Ratio={metrics.position_ratio:.1%} | "
            f"Skew={metrics.current_skew_bps:.1f}bps | "
            f"VaR1min=${metrics.var_1min:.2f}"
        )
```

---

## 5. Cross-Pair Correlation Management

### 5.1 Correlation Tracking

```python
class CorrelationMonitor:
    """Monitor cross-pair price correlation."""
    
    def __init__(self, lookback_seconds: int = 300):
        self.lookback = lookback_seconds
        self.price_history: dict[InstrumentId, deque] = {}
    
    def update(self, instrument_id: InstrumentId, mid_price: float):
        """Record mid price."""
        if instrument_id not in self.price_history:
            self.price_history[instrument_id] = deque(maxlen=300)
        
        self.price_history[instrument_id].append({
            'timestamp': time.time(),
            'price': mid_price,
        })
    
    def calculate_correlation(
        self,
        inst1: InstrumentId,
        inst2: InstrumentId,
    ) -> float:
        """Calculate rolling correlation between two instruments."""
        hist1 = self.price_history.get(inst1, [])
        hist2 = self.price_history.get(inst2, [])
        
        if len(hist1) < 30 or len(hist2) < 30:
            return 0.0  # Not enough data
        
        # Align timestamps and calculate returns
        returns1 = self._calculate_returns(hist1)
        returns2 = self._calculate_returns(hist2)
        
        # Pearson correlation
        return np.corrcoef(returns1, returns2)[0, 1]
```

### 5.2 Risk Limits Based on Correlation

```python
def _check_correlation_risk(self) -> bool:
    """
    Prevent over-exposure when instruments move together.
    
    Returns: True if safe to trade, False if correlated risk too high
    """
    # Calculate current directional exposure
    btc_state = self._instruments[self.btc_instrument_id]
    eth_state = self._instruments[self.eth_instrument_id]
    
    btc_position_usd = float(btc_state.net_position) * self._get_mid_price(btc_state)
    eth_position_usd = float(eth_state.net_position) * self._get_mid_price(eth_state)
    
    # Check if both positions same sign (both long or both short)
    if btc_position_usd * eth_position_usd > 0:
        # Same direction - check correlation
        correlation = self.correlation_monitor.calculate_correlation(
            self.btc_instrument_id,
            self.eth_instrument_id,
        )
        
        # If highly correlated (>0.7) and same direction
        if abs(correlation) > 0.7:
            total_directional = abs(btc_position_usd) + abs(eth_position_usd)
            
            # Limit to max_correlated_exposure
            if total_directional > self.config.total_capital_usd * self.config.max_correlated_exposure:
                self.log.warning(
                    f"Correlation risk: {correlation:.2f}, "
                    f"exposure ${total_directional:.0f} > limit"
                )
                return False
    
    return True
```

---

## 6. Implementation Roadmap

### Phase 1: Multi-Pair Foundation (Week 1)
- [ ] Create `hft_obi_bybit_spot_mm_lowcap_multipair_v002.py`
- [ ] Implement `InstrumentState` and `InstrumentConfig` classes
- [ ] Update strategy to subscribe to multiple order books
- [ ] Implement per-instrument quote generation
- [ ] Test with BTC + ETH in backtest mode
- [ ] Validate capital allocation logic

**Deliverable**: Working dual-instrument strategy without ML

### Phase 2: Inventory Management (Week 2)
- [ ] Implement `InventoryRiskManager` class
- [ ] Add Avellaneda-Stoikov skewing calculations
- [ ] Implement dynamic inventory targets
- [ ] Add asymmetric size quoting
- [ ] Build inventory rebalancing logic
- [ ] Test skewing effectiveness in backtest

**Deliverable**: Professional-grade inventory management

### Phase 3: ML Infrastructure (Week 3)
- [ ] Implement feature extraction pipeline
- [ ] Create `OnlineOBIWeightingModel` class
- [ ] Build offline training pipeline
- [ ] Train initial model on backtest data
- [ ] Integrate ML predictions into quoting logic
- [ ] A/B test ML vs non-ML performance

**Deliverable**: ML-enhanced OBI weighting operational

### Phase 4: Adaptive Spread & Correlation (Week 4)
- [ ] Implement spread component calculations
- [ ] Add adverse selection prediction
- [ ] Implement fill rate tracking and optimization
- [ ] Add `CorrelationMonitor` class
- [ ] Implement cross-pair hedging logic
- [ ] Test spread adaptation in various market conditions

**Deliverable**: Fully adaptive spread with correlation management

### Phase 5: Integration & Testing (Week 5)
- [ ] Integrate all components into cohesive strategy
- [ ] Comprehensive backtesting (multi-day)
- [ ] Stress testing with extreme scenarios
- [ ] Parameter optimization
- [ ] Performance profiling (<100ms latency verified)

**Deliverable**: Complete v002 strategy ready for paper trading

### Phase 6: Optimization & Launch (Week 6)
- [ ] Paper trading on mainnet (1 week)
- [ ] Monitor all metrics and edge cases
- [ ] Final parameter tuning based on live data
- [ ] Documentation and runbooks
- [ ] Deploy to AWS with monitoring
- [ ] Gradual capital allocation ($100 → $500)

**Deliverable**: Live trading v002 on mainnet

---

## 7. Performance Targets

### Baseline (v001 - Single Pair)
- Capital: $500
- Return: 9.65%
- Sharpe: ~2.0
- Max DD: -5%
- Fill Rate: 38.5%

### Target (v002 - Multi-Pair ML + Inventory Mgmt)
- Capital: $500
- Return: **18-25%** (2-2.5x improvement)
- Sharpe: **3.5-4.0** (inventory optimization + diversification)
- Max DD: **-2.5%** (professional inventory management)
- Fill Rate: **50-60%** (adaptive spreads + smart skewing)
- Latency: **<100ms** tick-to-trade (maintained)
- Inventory Turnover: **<2 minutes** average holding time

### Key Improvements from Inventory Management
- **+30-40% higher returns**: Optimal inventory targets allow directional bias
- **+25% better Sharpe**: Reduced volatility from inventory risk control
- **-50% lower drawdowns**: Proactive rebalancing prevents runaway positions
- **+15% better fills**: Skewing increases fill probability on desired side

---

## 8. Risk Considerations

### Operational Risks
1. **Model Overfitting**: ML model fits backtest but fails live
   - *Mitigation*: Simple model, online learning, paper trading first
   
2. **Increased Latency**: ML inference + inventory calculations slow down
   - *Mitigation*: Pre-compute features, optimize calculations, <2ms total overhead
   
3. **Capital Fragmentation**: Splitting $500 across 2 pairs reduces impact
   - *Mitigation*: Dynamic allocation, concentrate when high conviction
   
4. **Correlation Breakdown**: BTC/ETH correlation changes unexpectedly
   - *Mitigation*: Real-time monitoring, automatic position reduction

5. **Inventory Accumulation**: Skewing fails and position grows to limits
   - *Mitigation*: Emergency rebalancing at 90%, IOC market orders if needed

6. **Over-Skewing**: Aggressive skewing reduces fill rate too much
   - *Mitigation*: Fill rate monitoring, adaptive urgency multipliers

### Technical Risks
1. **Multiple Order Book Subscriptions**: 2x network bandwidth
   - *Mitigation*: BYBIT supports efficient WebSocket multiplexing
   
2. **State Management Complexity**: Tracking 2x instruments
   - *Mitigation*: Clean separation, thorough testing
   
3. **Order Routing**: Ensuring correct instrument per order
   - *Mitigation*: Explicit instrument_id in all order commands

---

## 9. Testing Strategy

### Unit Tests
```python
def test_ml_feature_extraction():
    """Verify feature vector shape and values."""
    
def test_spread_calculation_bounds():
    """Ensure spread stays within min/max limits."""
    
def test_capital_allocation():
    """Verify allocations sum to total capital."""
    
def test_correlation_calculation():
    """Validate Pearson correlation computation."""

def test_inventory_skew_calculation():
    """Verify Avellaneda-Stoikov skewing formula."""
    
def test_asymmetric_size_calculation():
    """Ensure bid/ask sizes scale correctly with inventory."""
    
def test_rebalancing_triggers():
    """Validate rebalancing logic activates at correct thresholds."""
```

### Integration Tests
```python
def test_dual_instrument_backtest():
    """Run full backtest with BTC + ETH."""
    
def test_ml_model_online_update():
    """Verify model updates after fills."""
    
def test_correlation_risk_limits():
    """Ensure trading stops when correlation risk high."""
    
def test_inventory_skewing_integration():
    """Verify quotes shift correctly as position changes."""
    
def test_cross_pair_hedging():
    """Validate hedge orders placed when correlation high."""
```

### Performance Tests
```python
def benchmark_ml_inference_latency():
    """Measure feature extraction + prediction time."""
    # Target: <1ms for both instruments
    
def benchmark_dual_quote_generation():
    """Measure time to generate quotes for both pairs."""
    # Target: <5ms total including inventory calculations
    
def benchmark_inventory_calculations():
    """Measure skewing and rebalancing computation time."""
    # Target: <500μs per instrument
```

---

## 10. Monitoring & Observability

### Key Metrics to Track
```python
@dataclass
class MultiPairMetrics:
    # Per-instrument positions
    btc_position_qty: Decimal
    btc_position_usd: float
    btc_optimal_target: Decimal
    btc_position_ratio: float  # % of max
    eth_position_qty: Decimal
    eth_position_usd: float
    eth_optimal_target: Decimal
    eth_position_ratio: float
    
    # Per-instrument P&L
    btc_unrealized_pnl: float
    btc_realized_pnl: float
    eth_unrealized_pnl: float
    eth_realized_pnl: float
    
    # Per-instrument fills
    btc_fill_rate: float
    btc_maker_fills: int
    btc_taker_fills: int
    eth_fill_rate: float
    eth_maker_fills: int
    eth_taker_fills: int
    
    # Inventory skewing
    btc_current_skew_bps: float
    btc_urgency_multiplier: float
    eth_current_skew_bps: float
    eth_urgency_multiplier: float
    
    # ML
    btc_ml_weight_avg: float
    eth_ml_weight_avg: float
    ml_prediction_accuracy: float
    
    # Cross-pair
    correlation: float
    total_exposure_usd: float
    correlated_exposure_usd: float
    capital_utilization_pct: float
    
    # Risk
    total_var_1min: float  # Combined VaR
    largest_position_ratio: float
    time_to_neutral_btc: float
    time_to_neutral_eth: float
    
    # Performance
    total_pnl_usd: float
    sharpe_ratio: float
    win_rate: float
    avg_holding_time_seconds: float
```

### Logging Enhancements
```python
# Every 60 seconds, log comprehensive state
self.log.info(
    f"Multi-Pair State | "
    f"BTC: pos={btc_pos_qty} (${btc_pos_usd:.0f}) "
    f"target={btc_target} ratio={btc_ratio:.1%} "
    f"skew={btc_skew:.1f}bps pnl=${btc_pnl:.2f} | "
    f"ETH: pos={eth_pos_qty} (${eth_pos_usd:.0f}) "
    f"target={eth_target} ratio={eth_ratio:.1%} "
    f"skew={eth_skew:.1f}bps pnl=${eth_pnl:.2f} | "
    f"Corr={corr:.2f} TotalExp=${total_exp:.0f} "
    f"ML=[BTC:{btc_ml:.2f} ETH:{eth_ml:.2f}]"
)

# Every rebalance, log details
self.log.warning(
    f"INVENTORY REBALANCE | "
    f"{instrument_id.symbol} | "
    f"Current={current_pos} Target={target_pos} | "
    f"Action={side} {rebalance_qty} @ {price} | "
    f"Reason: {reason}",
    LogColor.YELLOW,
)

# Every fill, log inventory impact
self.log.info(
    f"FILL | {instrument_id.symbol} | "
    f"{side} {fill_qty} @ {fill_price} | "
    f"New Position: {new_pos} (was {old_pos}) | "
    f"Position Ratio: {position_ratio:.1%} | "
    f"Optimal Target: {optimal_target}",
    LogColor.GREEN if favorable else LogColor.YELLOW,
)
```

---

## 11. File Structure

```
strategy/
├── hft_obi_bybit_spot_mm_lowcap_v001.py          # Original (DEPLOYED)
├── hft_obi_bybit_spot_mm_lowcap_multipair_v002.py # NEW: Multi-pair ML + Inventory
├── ml/
│   ├── __init__.py
│   ├── obi_weighting_model.py                     # ML model classes
│   ├── feature_engineering.py                     # Feature extraction
│   ├── training_pipeline.py                       # Offline training
│   └── model_weights/
│       └── obi_weights_v002.npz                   # Trained weights
├── inventory/
│   ├── __init__.py
│   ├── risk_manager.py                            # InventoryRiskManager
│   ├── skewing.py                                 # Avellaneda-Stoikov skewing
│   ├── rebalancing.py                             # Rebalancing logic
│   └── metrics.py                                 # Inventory risk metrics
├── utils/
│   ├── __init__.py
│   ├── correlation_monitor.py                     # Correlation tracking
│   ├── spread_calculator.py                       # Adaptive spread logic
│   └── capital_allocator.py                       # Capital management
└── tests/
    ├── test_multipair_strategy.py
    ├── test_ml_model.py
    ├── test_spread_adaptation.py
    ├── test_inventory_management.py               # NEW
    ├── test_skewing.py                            # NEW
    └── test_correlation_mgmt.py
```

---

## 12. Next Steps

1. **Review and Approve Design** ✓ (You are here)
2. **Create v002 Strategy Template**
3. **Implement Multi-Pair Core**
4. **Build ML Infrastructure**
5. **Train Initial Model**
6. **Backtest v002**
7. **Paper Trade v002**
8. **Deploy to Production**

---

**Author**: AI Agent (GitHub Copilot)  
**Date**: January 27, 2026  
**Status**: Awaiting Review & Approval
