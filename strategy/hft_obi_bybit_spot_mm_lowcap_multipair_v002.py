"""Multi-Pair Low-Capital OBI Market Maker with ML and Inventory Management.

Version: v002
Enhancement of v001 with:
- Multi-pair support (BTC + ETH simultaneous) ✓ Phase 1 Complete
- Avellaneda-Stoikov inventory management ✓ Phase 2 Complete
- ML-based OBI weighting ✓ Phase 3 Complete
- Adaptive spread calculation ✓ Phase 4 Complete
- Cross-pair correlation hedging ✓ Phase 4 Complete

Strategy ID: hft_obi_bybit_spot_mm_lowcap_multipair_v002
Venue: BYBIT SPOT
Asset Pairs: BTC-USDT + ETH-USDT
Capital Range: $500 - $5,000
Created: January 2026
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import numpy as np

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

# Import inventory management module
from strategy.inventory.risk_manager import InventoryRiskManager
from strategy.inventory.metrics import InventoryRiskMetrics

# Import ML module
from strategy.ml.feature_engineering import FeatureExtractor
from strategy.ml.online_model import OnlineOBIPredictor

# Import adaptive spread module
from strategy.spread.volatility_estimator import VolatilityEstimator
from strategy.spread.adaptive_spread import AdaptiveSpreadCalculator

# Import correlation module
from strategy.correlation.correlation_manager import EnhancedCorrelationManager


# ============================================================================
# CONFIGURATION DATACLASSES
# ============================================================================

@dataclass
class InstrumentConfig:
    """Per-instrument configuration."""
    
    instrument_id: str
    base_qty: Decimal  # Quote size per order
    max_position_qty: Decimal  # Maximum directional position
    weight: float  # Capital allocation weight (0-1)
    obi_sensitivity: float = 1.0  # Instrument-specific OBI multiplier
    min_spread_bps: int = 2
    max_spread_bps: int = 5


class MultiPairMMConfig(StrategyConfig):
    """Multi-pair low-capital market maker configuration."""
    
    # Instruments (will be processed in strategy init)
    instruments: list[dict] = None  # Optional - defaults to BTC+ETH
    
    # Shared risk parameters
    total_capital_usd: float = 500.0
    max_total_exposure_pct: float = 0.15  # 15% total at risk
    emergency_liquidation_loss_usd: float = -100.0  # Kill switch
    
    # OBI Configuration (shared)
    obi_levels: int = 5  # Top 5 levels
    obi_ema_period: int = 15  # EMA smoothing
    obi_entry_threshold: float = 0.15  # 15% to enter
    obi_exit_threshold: float = 0.03  # 3% to flatten
    
    # Inventory Management (Avellaneda-Stoikov)
    risk_aversion: float = 0.5  # γ (gamma) - risk aversion parameter
    inventory_half_life_seconds: float = 30.0  # Target turnover
    max_inventory_age_seconds: float = 90.0  # Force close after 90s
    
    # Correlation Management
    max_correlated_exposure_pct: float = 0.7  # Max 70% when corr > 0.8
    correlation_lookback_seconds: int = 300  # 5 min rolling window
    
    # Timing
    orderbook_update_throttle_ms: int = 5  # 5ms throttle
    quote_refresh_interval_ms: int = 50  # Re-quote every 50ms

    # Quote management (HFT-style queue friendliness)
    min_requote_ticks: int = 1  # Minimum price change to cancel/replace
    min_quote_lifetime_ms: int = 50  # Minimum time between cancel/replace

    # Momentum filter (anti-adverse selection)
    momentum_window: int = 20  # Number of mid-price samples
    momentum_threshold: float = 0.0008  # Log-return threshold to skip one side

    # Micro-price reference
    use_micro_price: bool = True

    # Micro-price shock guard (to avoid toxic flow)
    micro_move_window_ms: int = 50
    micro_move_threshold_bps: float = 10.0
    micro_move_cooldown_ms: int = 100
    momentum_gate_bps: float = 5.0

    # Markout tracking (adverse selection monitor)
    markout_horizons_ms: list[int] = None  # Defaults applied in strategy init
    markout_window: int = 50
    markout_min_samples: int = 5
    markout_bad_threshold_bps: float = -0.5
    markout_spread_multiplier: float = 1.2
    markout_size_multiplier: float = 0.7
    markout_reaction_horizon_ms: int = 100
    markout_reaction_cooldown_ms: int = 5000
    markout_reaction_spread_multiplier: float = 1.5
    markout_reaction_size_multiplier: float = 0.7

    # Post-only safety
    # Buffer in ticks away from touch when placing post-only orders.
    # 0 = allow quoting at the touch; >0 = step back for fewer post-only rejects.
    post_only_buffer_ticks: int = 1
    # If we see a post-only rejection, temporarily widen by this many extra ticks.
    post_only_reject_bump_ticks: int = 1
    # How long to keep the temporary widened buffer (ms).
    post_only_reject_cooldown_ms: int = 1500

    # Edge filtering (fees + minimum expected edge)
    maker_fee_bps: float = 0.1  # Estimated maker fee in bps
    min_edge_bps: float = 0.5  # Additional edge margin over fees and min spread
    edge_bps_multiplier: float = 40.0  # Scale for expected edge from signal strength

    # Dynamic size throttling
    size_throttle_min_multiplier: float = 0.2
    size_throttle_max_multiplier: float = 1.2
    size_throttle_flat_pos_ratio: float = 0.15
    size_throttle_wide_spread_bps: float = 4.0
    size_throttle_vol_low: float = 0.0006
    size_throttle_vol_high: float = 0.0015

    # Maker-only inventory exits
    inventory_exit_only_position_ratio: float = 0.1  # Switch to one-sided when |pos| >= 10%
    # Exit-only pricing buffer (bps) applied to micro price
    exit_only_pricing_bps: float = 2.0

    # Profit-taking skew
    profit_take_bps: float = 2.0
    profit_take_skew_multiplier: float = 1.5

    # Static exit-clearing bias (bps)
    holding_exit_bias_bps: float = 2.5

    # Inventory pin management
    pinned_position_ratio: float = 0.9  # Trigger pin mode at >= 90% of max position
    pinned_inside_spread_ticks: int = 1  # Tighten ask inside spread by ticks (if spread allows)
    pinned_chunk_start_seconds: float = 120.0  # Start time-based reductions after 2 minutes pinned
    pinned_chunk_interval_seconds: float = 300.0  # Reduce every 5 minutes while pinned
    pinned_chunk_ratio: float = 0.25  # Reduce 25% of max position per chunk

    # Adverse selection cooldowns
    adverse_selection_cooldown_ms: int = 2000
    adverse_selection_spread_multiplier: float = 1.15
    shared_adverse_cooldown_ms: int = 1500
    shared_adverse_spread_multiplier: float = 1.10

    # Volatility-scaled cooldown tuning
    cooldown_min_ms: int = 250
    cooldown_max_ms: int = 5000
    cooldown_vol_alpha: float = 0.02

    # Fee-aware fill filtering
    min_fill_prob: float = 0.05

    # Emergency kill-switch refinement
    emergency_loss_persist_seconds: float = 8.0

    # Correlation-aware inventory caps
    correlation_threshold: float = 0.8

    # Cross-pair skew (mean reversion)
    pair_ratio_ema_alpha: float = 0.05
    pair_skew_bps_per_pct: float = 5.0

    # Hedge imbalance handling
    hedge_imbalance_size_multiplier: float = 0.7
    same_direction_spread_multiplier: float = 1.10

    # Last-resort inventory control
    enable_taker_ioc_rebalance: bool = True
    rebalance_ioc_max_slippage_bps: float = 3.0
    rebalance_ioc_min_position_ratio: float = 0.85  # Trigger IOC if |pos| >= 85% of max
    
    def get_instrument_configs(self) -> list[InstrumentConfig]:
        """Get instrument configurations (processes instruments list)."""
        # Get instruments list (use provided or defaults)
        instruments_list = self.instruments if self.instruments is not None else [
            {
                'instrument_id': 'BTCUSDT-SPOT.BYBIT',
                'base_qty': '0.0001',
                'max_position_qty': '0.0003',
                'weight': 0.6,
                'obi_sensitivity': 1.0,
                'min_spread_bps': 2,
                'max_spread_bps': 5,
            },
            {
                'instrument_id': 'ETHUSDT-SPOT.BYBIT',
                'base_qty': '0.005',
                'max_position_qty': '0.015',
                'weight': 0.4,
                'obi_sensitivity': 1.2,
                'min_spread_bps': 2,
                'max_spread_bps': 6,
            },
        ]
        
        # Convert to InstrumentConfig objects
        return [
            InstrumentConfig(
                instrument_id=cfg['instrument_id'],
                base_qty=Decimal(str(cfg['base_qty'])),
                max_position_qty=Decimal(str(cfg['max_position_qty'])),
                weight=cfg['weight'],
                obi_sensitivity=cfg.get('obi_sensitivity', 1.0),
                min_spread_bps=cfg.get('min_spread_bps', 1),
                max_spread_bps=cfg.get('max_spread_bps', 5),
            )
            for cfg in instruments_list
        ]


# ============================================================================
# STATE MANAGEMENT
# ============================================================================

@dataclass
class InstrumentState:
    """Per-instrument state tracking."""
    
    instrument_id: InstrumentId
    book: OrderBook
    inventory_manager: InventoryRiskManager  # Inventory management
    ml_predictor: OnlineOBIPredictor  # ML model
    feature_extractor: FeatureExtractor  # Feature extraction
    volatility_estimator: VolatilityEstimator  # NEW: Volatility tracking
    spread_calculator: AdaptiveSpreadCalculator  # NEW: Adaptive spreads
    
    # OBI tracking
    obi_history: deque
    obi_ema: float = 0.0
    
    # Position tracking
    net_position: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    fees_paid: Decimal = Decimal(0)
    position_cost_usdt: Decimal = Decimal(0)  # Signed cost basis in USDT (netting)
    
    # Order tracking
    active_bid_order_id: Optional[ClientOrderId] = None
    active_ask_order_id: Optional[ClientOrderId] = None
    
    # Timing
    last_quote_time: float = 0.0
    last_fill_time: float = 0.0
    last_update_time: float = 0.0
    last_rebalance_time: float = 0.0
    last_fill_price: float = 0.0  # Track last fill price for learning
    last_quote_update_time: float = 0.0

    # Inventory pin tracking
    pinned_since: float = 0.0
    last_pinned_reduce_time: float = 0.0
    
    # ML state
    ml_confidence: float = 0.5  # Current ML confidence (0-1)

    # Momentum filter state
    price_history: deque = None
    momentum: float = 0.0
    skip_bids: bool = False
    skip_asks: bool = False

    # Markout tracking
    markout_queue: deque = None
    markout_history: deque = None
    last_reference_price: float = 0.0
    last_reference_time: float = 0.0
    markout_cooldown_until: float = 0.0

    # Micro-guard reference price at last quote
    last_quote_micro_price: float = 0.0

    # Volatility regime tracking
    avg_volatility: float = 0.0
    
    # Metrics
    fill_count: int = 0
    quote_count: int = 0

    # Post-only rejection adaptation
    post_only_reject_count: int = 0
    _post_only_buffer_ticks_until: float = 0.0
    _post_only_buffer_ticks_dynamic: int = 0

    # Adverse PnL tracking
    adverse_pnl_start_time: float = 0.0


# ============================================================================
# CORRELATION MONITOR
# ============================================================================

class CorrelationMonitor:
    """Monitor cross-pair price correlation."""
    
    def __init__(self, lookback_seconds: int = 300):
        self.lookback = lookback_seconds
        self.price_history: dict[InstrumentId, deque] = {}
    
    def update(self, instrument_id: InstrumentId, mid_price: float, timestamp: float):
        """Record mid price with timestamp."""
        if instrument_id not in self.price_history:
            self.price_history[instrument_id] = deque(maxlen=300)
        
        self.price_history[instrument_id].append({
            'timestamp': timestamp,
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
        
        # Extract prices as numpy arrays
        prices1 = np.array([p['price'] for p in hist1])
        prices2 = np.array([p['price'] for p in hist2])
        
        # Calculate returns
        returns1 = np.diff(prices1) / prices1[:-1]
        returns2 = np.diff(prices2) / prices2[:-1]
        
        # Align to same length
        min_len = min(len(returns1), len(returns2))
        returns1 = returns1[-min_len:]
        returns2 = returns2[-min_len:]
        
        # Pearson correlation
        if len(returns1) < 10:
            return 0.0
        
        correlation = np.corrcoef(returns1, returns2)[0, 1]
        return correlation if not np.isnan(correlation) else 0.0


# ============================================================================
# MAIN STRATEGY
# ============================================================================

class MultiPairLowCapitalOBIMarketMaker(Strategy):
    """
    Multi-instrument OBI market maker with ML and inventory management.
    
    Phase 1 (Current): Multi-pair foundation
    - Dual instrument subscription (BTC + ETH)
    - Per-instrument state management
    - Basic quote generation for both pairs
    - Capital allocation
    
    Phase 2 (Next): Inventory management
    Phase 3 (Later): ML integration
    """
    
    def __init__(self, config: MultiPairMMConfig):
        super().__init__(config)
        
        # State management
        self._instruments: dict[InstrumentId, InstrumentState] = {}
        self._instrument_configs: dict[InstrumentId, InstrumentConfig] = {}
        
        # Enhanced correlation monitoring (v003 feature - disabled in v002)
        # self.correlation_monitor = EnhancedCorrelationManager(
        #     lookback_seconds=config.correlation_lookback_seconds,
        #     update_interval_seconds=10.0,
        #     high_correlation_threshold=0.8,
        # )
        self.correlation_monitor = CorrelationMonitor(
            lookback_seconds=config.correlation_lookback_seconds,
        )

        # Pair ratio tracking for cross-pair skew
        self._pair_ratio_ema: Optional[float] = None

        # Shared adverse selection cooldown
        self._shared_adverse_cooldown_until: dict[InstrumentId, float] = {}

        # Markout horizons (ms)
        self._markout_horizons_ms = (
            config.markout_horizons_ms
            if config.markout_horizons_ms is not None
            else [100, 500, 1000]
        )
        
        # Global metrics
        self._total_trades = 0
        self._total_pnl = Decimal(0)
        self._max_loss_trigger = False
        self._emergency_start_time: float = 0.0
        
        # Parse instrument configs
        instrument_configs = config.get_instrument_configs()
        
        # Cache config objects
        for inst_cfg in instrument_configs:
            inst_id = InstrumentId.from_str(inst_cfg.instrument_id)
            self._instrument_configs[inst_id] = inst_cfg
    
    def on_start(self) -> None:
        """Initialize strategy on start."""
        self.log.info(
            "="*70 + "\n" +
            "MULTI-PAIR LOW-CAPITAL OBI MARKET MAKER v002\n" +
            "="*70,
            LogColor.CYAN,
        )
        
        # Initialize each instrument
        for inst_cfg in self.config.get_instrument_configs():
            instrument_id = InstrumentId.from_str(inst_cfg.instrument_id)
            instrument = self.cache.instrument(instrument_id)
            
            if not instrument:
                self.log.error(f"Instrument not found: {instrument_id}")
                self.stop()
                return
            
            # Create order book
            book = OrderBook(
                instrument_id=instrument_id,
                book_type=BookType.L2_MBP,
            )
            
            # Subscribe to order book deltas
            self.subscribe_order_book_deltas(instrument_id)
            
            # Create inventory risk manager
            inventory_manager = InventoryRiskManager(
                max_position=inst_cfg.max_position_qty,
                risk_aversion=self.config.risk_aversion,
                volatility=0.02,  # 2% volatility estimate
                time_horizon=self.config.inventory_half_life_seconds * 2,  # 60s
            )
            
            # Create ML components
            ml_predictor = OnlineOBIPredictor(
                n_features=10,
                learning_rate=0.01,
                confidence_threshold=0.3,  # Only trade if >30% confidence
            )
            feature_extractor = FeatureExtractor()
            
            # Try to load saved weights if they exist
            weights_path = f"strategy/ml/model_weights/{instrument_id.symbol}_weights.npz"
            if ml_predictor.load_weights(weights_path):
                self.log.info(f"Loaded ML weights from {weights_path}", LogColor.GREEN)
            
            # Create adaptive spread components
            volatility_estimator = VolatilityEstimator(
                half_life_seconds=60.0,
                lookback_periods=100,
            )
            spread_calculator = AdaptiveSpreadCalculator(
                min_base_spread_bps=inst_cfg.min_spread_bps,
                max_base_spread_bps=inst_cfg.max_spread_bps,
            )
            
            # Initialize state
            self._instruments[instrument_id] = InstrumentState(
                instrument_id=instrument_id,
                book=book,
                inventory_manager=inventory_manager,
                ml_predictor=ml_predictor,
                feature_extractor=feature_extractor,
                volatility_estimator=volatility_estimator,
                spread_calculator=spread_calculator,
                obi_history=deque(maxlen=self.config.obi_ema_period),
                price_history=deque(maxlen=max(2, int(getattr(self.config, "momentum_window", 20)))),
                markout_queue=deque(),
                markout_history=deque(maxlen=max(1, int(getattr(self.config, "markout_window", 50)))),
                last_fill_time=time.time(),
                last_rebalance_time=time.time(),
            )
            
            self.log.info(
                f"✓ Initialized {instrument_id.symbol} | "
                f"Base: {inst_cfg.base_qty} | "
                f"Max: {inst_cfg.max_position_qty} | "
                f"Weight: {inst_cfg.weight:.0%}",
                LogColor.GREEN,
            )
        
        self.log.info(
            f"Capital: ${self.config.total_capital_usd:.0f} | "
            f"Kill Switch: ${self.config.emergency_liquidation_loss_usd:.0f} | "
            f"Instruments: {len(self._instruments)}",
        )
    
    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """Process order book updates for any instrument."""
        instrument_id = deltas.instrument_id
        state = self._instruments.get(instrument_id)
        
        if not state:
            return
        
        now = time.time()
        
        # Apply deltas to local order book
        state.book.apply(deltas)
        
        # Throttle updates
        elapsed_ms = (now - state.last_update_time) * 1000
        if elapsed_ms < self.config.orderbook_update_throttle_ms:
            return
        
        state.last_update_time = now
        
        # Calculate OBI signal
        obi = self._calculate_obi(state)
        if obi is not None:
            state.obi_history.append(obi)
            self._update_obi_ema(state)
        
        # Update correlation monitor for cross-pair risk controls
        ref_price = self._get_reference_price(state)
        if ref_price:
            state.last_reference_price = ref_price
            state.last_reference_time = now
            self.correlation_monitor.update(instrument_id, ref_price, now)
            self._update_momentum(state, ref_price)
            self._process_markouts(state, now, ref_price)
        
        # Check emergency conditions
        self._check_emergency_conditions()
        
        # Update quotes if needed
        elapsed_quote_ms = (now - state.last_quote_time) * 1000
        if elapsed_quote_ms >= self.config.quote_refresh_interval_ms:
            self._update_quotes(instrument_id, now)
            state.last_quote_time = now
    
    def _calculate_obi(self, state: InstrumentState) -> Optional[float]:
        """
        Calculate Order Book Imbalance for instrument.
        
        OBI = (Bid_Volume - Ask_Volume) / (Bid_Volume + Ask_Volume)
        """
        if not state.book or not state.book.spread():
            return None
        
        bid_levels = self.config.obi_levels
        ask_levels = self.config.obi_levels
        
        bid_volume = Decimal(0)
        ask_volume = Decimal(0)
        
        bids = state.book.bids()
        asks = state.book.asks()
        
        for i, level in enumerate(bids):
            if i >= bid_levels:
                break
            bid_volume += Decimal(str(level.size()))
        
        for i, level in enumerate(asks):
            if i >= ask_levels:
                break
            ask_volume += Decimal(str(level.size()))
        
        total_volume = bid_volume + ask_volume
        if total_volume == 0:
            return None
        
        obi = float((bid_volume - ask_volume) / total_volume)
        
        # Apply instrument-specific sensitivity
        config = self._instrument_configs[state.instrument_id]
        obi *= config.obi_sensitivity
        
        return obi
    
    def _update_obi_ema(self, state: InstrumentState) -> None:
        """Update exponential moving average of OBI."""
        if not state.obi_history:
            return
        
        alpha = 2.0 / (self.config.obi_ema_period + 1)
        current_obi = state.obi_history[-1]
        
        if len(state.obi_history) == 1:
            state.obi_ema = current_obi
        else:
            state.obi_ema = alpha * current_obi + (1 - alpha) * state.obi_ema
    
    def _get_mid_price(self, state: InstrumentState) -> Optional[float]:
        """Get current mid price for instrument."""
        best_bid = state.book.best_bid_price()
        best_ask = state.book.best_ask_price()
        
        if best_bid and best_ask:
            return (float(best_bid) + float(best_ask)) / 2.0
        return None

    def _get_micro_price(self, state: InstrumentState) -> Optional[float]:
        """Get micro-price using top-of-book volumes."""
        best_bid = state.book.best_bid_price()
        best_ask = state.book.best_ask_price()
        best_bid_size = state.book.best_bid_size()
        best_ask_size = state.book.best_ask_size()

        if not (best_bid and best_ask and best_bid_size and best_ask_size):
            return None

        bid_p = float(best_bid)
        ask_p = float(best_ask)
        bid_v = float(best_bid_size)
        ask_v = float(best_ask_size)
        denom = bid_v + ask_v
        if denom <= 0.0:
            return None

        return (bid_p * ask_v + ask_p * bid_v) / denom

    def _get_reference_price(self, state: InstrumentState) -> Optional[float]:
        """Get reference price (micro if enabled, else mid)."""
        if bool(getattr(self.config, "use_micro_price", True)):
            micro = self._get_micro_price(state)
            if micro is not None:
                return micro
        return self._get_mid_price(state)

    def _update_momentum(self, state: InstrumentState, mid_price: float) -> None:
        """Update mid-price momentum filter and set one-sided skip flags."""
        if state.price_history is None:
            state.price_history = deque(maxlen=max(2, int(getattr(self.config, "momentum_window", 20))))

        state.price_history.append(float(mid_price))

        window = int(getattr(self.config, "momentum_window", 20))
        if len(state.price_history) < max(2, window):
            state.momentum = 0.0
            state.skip_bids = False
            state.skip_asks = False
            return

        first = state.price_history[0]
        last = state.price_history[-1]
        if first <= 0.0 or last <= 0.0:
            state.momentum = 0.0
            state.skip_bids = False
            state.skip_asks = False
            return

        momentum = float(np.log(last) - np.log(first))
        state.momentum = momentum

        threshold = float(getattr(self.config, "momentum_threshold", 0.0008))
        if momentum > threshold:
            state.skip_asks = True
            state.skip_bids = False
        elif momentum < -threshold:
            state.skip_bids = True
            state.skip_asks = False
        else:
            state.skip_bids = False
            state.skip_asks = False

    def _check_micro_guard(self, state: InstrumentState, current_micro_price: float) -> tuple[bool, bool]:
        """Return (block_bids, block_asks) based on micro-price velocity and momentum gate."""
        gate_bps = float(getattr(self.config, "momentum_gate_bps", 5.0))
        momentum_bps = float(state.momentum) * 1e4
        if abs(momentum_bps) < gate_bps:
            return False, False

        last_micro = float(state.last_quote_micro_price or 0.0)
        if last_micro <= 0.0:
            return False, False

        move_bps = (current_micro_price - last_micro) / last_micro * 1e4
        threshold_bps = float(getattr(self.config, "micro_move_threshold_bps", 10.0))

        if move_bps <= -threshold_bps:
            self.log.warning(
                f"GUARD: Crash detected ({move_bps:.1f} bps). Blocking bids.",
                LogColor.YELLOW,
            )
            return True, False
        if move_bps >= threshold_bps:
            self.log.warning(
                f"GUARD: Pump detected ({move_bps:.1f} bps). Blocking asks.",
                LogColor.YELLOW,
            )
            return False, True

        return False, False

    def _process_markouts(self, state: InstrumentState, now: float, ref_price: float) -> None:
        """Process due markout checks and store markout history (bps)."""
        if state.markout_queue is None or state.markout_history is None:
            return

        while state.markout_queue and state.markout_queue[0]["due_time"] <= now:
            markout = state.markout_queue.popleft()
            fill_price = markout["fill_price"]
            side_sign = markout["side_sign"]
            horizon_ms = int(markout.get("horizon_ms", 0))
            if fill_price <= 0.0 or ref_price <= 0.0:
                continue

            markout_bps = ((ref_price - fill_price) / fill_price) * 1e4 * side_sign
            state.markout_history.append(markout_bps)

            reaction_horizon = int(getattr(self.config, "markout_reaction_horizon_ms", 100))
            reaction_threshold = float(
                getattr(self.config, "markout_reaction_threshold_bps", getattr(self.config, "markout_bad_threshold_bps", -0.5))
            )
            if horizon_ms == reaction_horizon and markout_bps < reaction_threshold:
                cooldown_s = max(0.0, float(getattr(self.config, "markout_reaction_cooldown_ms", 5000)) / 1000.0)
                if cooldown_s > 0.0:
                    state.markout_cooldown_until = max(state.markout_cooldown_until, now + cooldown_s)

    def _get_other_state(self, instrument_id: InstrumentId) -> Optional[InstrumentState]:
        for inst_id, st in self._instruments.items():
            if inst_id != instrument_id:
                return st
        return None

    def _pair_role(self, instrument_id: InstrumentId) -> Optional[str]:
        symbol = instrument_id.symbol.upper()
        if symbol.startswith("BTC"):
            return "BTC"
        if symbol.startswith("ETH"):
            return "ETH"
        return None

    def _compute_cross_pair_skew_bps(self, instrument_id: InstrumentId) -> float:
        if len(self._instruments) < 2:
            return 0.0

        btc_mid = None
        eth_mid = None
        for inst_id, st in self._instruments.items():
            mid = self._get_reference_price(st)
            if mid is None:
                continue
            role = self._pair_role(inst_id)
            if role == "BTC":
                btc_mid = mid
            elif role == "ETH":
                eth_mid = mid

        if not btc_mid or not eth_mid:
            return 0.0

        ratio = btc_mid / eth_mid
        if self._pair_ratio_ema is None:
            self._pair_ratio_ema = ratio
        else:
            alpha = float(getattr(self.config, "pair_ratio_ema_alpha", 0.05))
            self._pair_ratio_ema = alpha * ratio + (1.0 - alpha) * self._pair_ratio_ema

        if not self._pair_ratio_ema:
            return 0.0

        dev_pct = (ratio - self._pair_ratio_ema) / self._pair_ratio_ema * 100.0
        base_bps = float(getattr(self.config, "pair_skew_bps_per_pct", 5.0)) * dev_pct

        role = self._pair_role(instrument_id)
        if role == "BTC":
            return -base_bps
        if role == "ETH":
            return base_bps
        return 0.0

    def _combined_exposure_usdt(self) -> float:
        total = 0.0
        for st in self._instruments.values():
            mid = self._get_reference_price(st)
            if mid is None:
                continue
            total += abs(float(st.net_position)) * float(mid)
        return total
    
    def _update_quotes(self, instrument_id: InstrumentId, now: float):
        """
        Update quotes for instrument with ML and inventory management (Phase 3).
        
        Includes:
        - ML feature extraction and prediction
        - Confidence-based filtering
        - Avellaneda-Stoikov price skewing
        - Asymmetric size quoting
        - Optimal inventory target
        - Rebalancing checks
        """
        state = self._instruments[instrument_id]
        config = self._instrument_configs[instrument_id]
        
        # Get mid price
        best_bid = state.book.best_bid_price()
        best_ask = state.book.best_ask_price()
        
        if not best_bid or not best_ask:
            return
        
        mid = self._get_reference_price(state)
        if mid is None:
            mid = (float(best_bid) + float(best_ask)) / 2.0

        # Cancel/replace hygiene: respect minimum quote lifetime
        min_quote_lifetime_s = max(0.0, float(getattr(self.config, "min_quote_lifetime_ms", 0)) / 1000.0)
        if (
            min_quote_lifetime_s > 0.0
            and (state.active_bid_order_id or state.active_ask_order_id)
            and (now - state.last_quote_update_time) < min_quote_lifetime_s
        ):
            state.quote_count += 1
            return
        
        # === ML PREDICTION ===
        # Extract features
        bids_list = list(state.book.bids())
        asks_list = list(state.book.asks())
        
        features = state.feature_extractor.extract_features(
            obi_ema=state.obi_ema,
            obi_history=state.obi_history,
            book_bids=bids_list,
            book_asks=asks_list,
            best_bid_price=float(best_bid),
            best_ask_price=float(best_ask),
            net_position=state.net_position,
            max_position=config.max_position_qty,
            last_fill_time=state.last_fill_time,
            quote_count=state.quote_count,
            fill_count=state.fill_count,
        )
        
        # Get ML prediction
        ml_confidence = state.ml_predictor.predict(features)
        state.ml_confidence = ml_confidence
        
        # Continuous (HFT-style) control: always quote unless risk-stopped.
        # Low confidence should widen spreads / reduce sizes, not turn off quoting.
        
        # Calculate optimal inventory target (use ML confidence)
        optimal_target = state.inventory_manager.calculate_optimal_target(
            obi_ema=state.obi_ema,
            ml_confidence=ml_confidence,
        )
        
        # Check if rebalancing needed
        time_since_fill = now - state.last_fill_time
        force_exit = False
        max_age = float(getattr(self.config, "max_inventory_age_seconds", 90.0))
        if state.net_position != 0 and max_age > 0.0 and time_since_fill >= max_age:
            force_exit = True
        needs_rebalance, reason = state.inventory_manager.check_rebalancing_needed(
            position=state.net_position,
            optimal_target=optimal_target,
            time_since_fill=time_since_fill,
        )
        
        if needs_rebalance:
            self.log.warning(
                f"Rebalancing {instrument_id.symbol}: {reason}",
                LogColor.YELLOW,
            )
            # Last resort: IOC taker rebalance if we are near limits.
            if self._rebalance_ioc(instrument_id, state, optimal_target, mid):
                state.inventory_manager.mark_rebalanced()

        # === ADAPTIVE SPREAD CALCULATION (Phase 4) ===
        # Update volatility estimator
        state.volatility_estimator.update(mid, now)

        # Get current volatility
        current_volatility = state.volatility_estimator.get_ewma_volatility()
        if current_volatility > 0.0:
            vol_alpha = float(getattr(self.config, "cooldown_vol_alpha", 0.02))
            if state.avg_volatility <= 0.0:
                state.avg_volatility = current_volatility
            else:
                state.avg_volatility = (
                    vol_alpha * current_volatility
                    + (1.0 - vol_alpha) * state.avg_volatility
                )

        # Calculate position ratio
        if float(config.max_position_qty) > 0:
            position_ratio = float(state.net_position) / float(config.max_position_qty)
        else:
            position_ratio = 0.0

        # Exit-only threshold flags
        exit_ratio = float(getattr(self.config, "inventory_exit_only_position_ratio", 0.1))
        exit_only_long = False
        exit_only_short = False
        if force_exit:
            if state.net_position > 0:
                exit_only_long = True
            elif state.net_position < 0:
                exit_only_short = True
        else:
            if position_ratio >= exit_ratio:
                exit_only_long = True
            elif position_ratio <= -exit_ratio:
                exit_only_short = True

        # Inventory pin detection
        pinned_ratio = float(getattr(self.config, "pinned_position_ratio", 0.9))
        is_pinned = abs(position_ratio) >= pinned_ratio
        if is_pinned:
            if state.pinned_since == 0.0:
                state.pinned_since = now
        else:
            state.pinned_since = 0.0

        # Time-based chunked liquidation while pinned
        if is_pinned and state.pinned_since > 0.0:
            chunk_start = float(getattr(self.config, "pinned_chunk_start_seconds", 120.0))
            chunk_interval = float(getattr(self.config, "pinned_chunk_interval_seconds", 300.0))
            if chunk_start > 0.0 and (now - state.pinned_since) >= chunk_start:
                if (now - state.last_pinned_reduce_time) >= max(1.0, chunk_interval):
                    if self._pinned_chunk_ioc(instrument_id, state, mid):
                        state.last_pinned_reduce_time = now
        
        # Calculate recent fill rate
        recent_fill_rate = state.fill_count / max(state.quote_count, 1) if state.quote_count > 0 else 0.4
        
        # Get inventory urgency
        metrics = state.inventory_manager.calculate_metrics(
            position=state.net_position,
            optimal_target=optimal_target,
            mid_price=mid,
            unrealized_pnl=state.unrealized_pnl,
            time_since_fill=time_since_fill,
            volatility=current_volatility,
            obi_ema=state.obi_ema,
            ml_confidence=ml_confidence,
        )
        urgency_multiplier = metrics.urgency_multiplier
        
        # Calculate adaptive spread
        spread_components = state.spread_calculator.calculate_spread(
            volatility=current_volatility,
            position_ratio=position_ratio,
            ml_confidence=ml_confidence,
            recent_fill_rate=recent_fill_rate,
            trade_intensity=1.0,  # Could track this from fills
            urgency_multiplier=urgency_multiplier,
        )
        spread_bps = spread_components.total_spread

        # Adverse-selection cooldown (volatility-scaled)
        base_cooldown_ms = float(getattr(self.config, "adverse_selection_cooldown_ms", 2000))
        min_cd_ms = float(getattr(self.config, "cooldown_min_ms", 250))
        max_cd_ms = float(getattr(self.config, "cooldown_max_ms", 5000))
        scaled_cd_ms = base_cooldown_ms
        if current_volatility > 0.0 and state.avg_volatility > 0.0:
            scaled_cd_ms = base_cooldown_ms * (state.avg_volatility / current_volatility)
        scaled_cd_ms = max(min_cd_ms, min(max_cd_ms, scaled_cd_ms))
        adverse_cooldown_s = max(0.0, scaled_cd_ms / 1000.0)
        if adverse_cooldown_s > 0.0 and (now - state.last_fill_time) <= adverse_cooldown_s:
            spread_bps *= float(getattr(self.config, "adverse_selection_spread_multiplier", 1.15))

        # Shared adverse-selection cooldown (other leg)
        shared_until = self._shared_adverse_cooldown_until.get(instrument_id, 0.0)
        if shared_until and now <= shared_until:
            spread_bps *= float(getattr(self.config, "shared_adverse_spread_multiplier", 1.10))

        # Markout-based self-throttling (adverse selection monitor)
        markout_size_multiplier = 1.0
        markout_history = state.markout_history or []
        min_samples = int(getattr(self.config, "markout_min_samples", 5))
        if len(markout_history) >= min_samples:
            avg_markout_bps = float(sum(markout_history) / len(markout_history))
            bad_threshold = float(getattr(self.config, "markout_bad_threshold_bps", -0.5))
            if avg_markout_bps < bad_threshold:
                spread_bps *= float(getattr(self.config, "markout_spread_multiplier", 1.2))
                markout_size_multiplier = float(getattr(self.config, "markout_size_multiplier", 0.7))

        # Immediate markout reaction cooldown (e.g., 100ms adverse move -> widen for 5s)
        if state.markout_cooldown_until and now <= state.markout_cooldown_until:
            spread_bps *= float(getattr(self.config, "markout_reaction_spread_multiplier", 1.5))
            markout_size_multiplier *= float(getattr(self.config, "markout_reaction_size_multiplier", 0.7))

        # Hard floor: enforce minimum per-side edge above fees (bps)
        hard_floor_bps = float(getattr(self.config, "maker_fee_bps", 0.0)) + float(
            getattr(self.config, "min_edge_bps", 0.0)
        )
        if hard_floor_bps > 0.0:
            spread_bps = max(spread_bps, 2.0 * hard_floor_bps)

        # Hedge imbalance handling and same-direction risk widening
        other_state = self._get_other_state(instrument_id)
        hedge_size_multiplier = 1.0
        if other_state is not None:
            if state.net_position != 0 and other_state.net_position != 0:
                if (state.net_position > 0 and other_state.net_position > 0) or (
                    state.net_position < 0 and other_state.net_position < 0
                ):
                    spread_bps *= float(getattr(self.config, "same_direction_spread_multiplier", 1.10))
                else:
                    hedge_size_multiplier *= float(getattr(self.config, "hedge_imbalance_size_multiplier", 0.7))
        
        # Calculate inventory skew
        skew_bps = state.inventory_manager.calculate_skew(
            position=state.net_position,
            optimal_target=optimal_target,
            mid_price=mid,
            volatility=current_volatility,
        )

        # Static exit-clearing bias (shift ladder toward exit side when holding inventory)
        exit_bias_bps = 0.0
        min_pos_threshold = float(config.base_qty) * 0.1
        if state.net_position > min_pos_threshold:
            exit_bias_bps = -float(getattr(self.config, "holding_exit_bias_bps", 2.5))
        elif state.net_position < -min_pos_threshold:
            exit_bias_bps = float(getattr(self.config, "holding_exit_bias_bps", 2.5))
        skew_bps += exit_bias_bps

        # Profit-taking skew (flatten when unrealized PnL is strong)
        if state.net_position != 0:
            pos_value = abs(float(state.net_position)) * float(mid)
            if pos_value > 0:
                unrealized_bps = float(state.unrealized_pnl) / pos_value * 1e4
                if unrealized_bps >= float(getattr(self.config, "profit_take_bps", 2.0)):
                    skew_bps *= float(getattr(self.config, "profit_take_skew_multiplier", 1.5))

        # Cross-pair mean-reversion skew
        skew_bps += self._compute_cross_pair_skew_bps(instrument_id)
        
        # Apply skew to mid price
        skewed_mid = mid + (skew_bps / 10000) * mid
        
        # Calculate half spread
        half_spread = (spread_bps / 10000) * mid / 2
        
        # Generate quotes around skewed mid
        instrument = self.cache.instrument(instrument_id)
        quote_bid = instrument.make_price(skewed_mid - half_spread)
        quote_ask = instrument.make_price(skewed_mid + half_spread)

        # Exit-only pricing: quote exit side at micro to clear faster
        if exit_only_long:
            exit_buffer_bps = float(getattr(self.config, "exit_only_pricing_bps", 2.0))
            quote_ask = instrument.make_price(mid * (1.0 + (exit_buffer_bps / 10000.0)))
        elif exit_only_short:
            exit_buffer_bps = float(getattr(self.config, "exit_only_pricing_bps", 2.0))
            quote_bid = instrument.make_price(mid * (1.0 - (exit_buffer_bps / 10000.0)))

        # Ensure post-only quotes cannot cross the current top-of-book.
        # Bybit will reject post-only orders which would execute immediately.
        tick = float(instrument.price_increment)
        if tick > 0.0:
            best_bid_f = float(best_bid)
            best_ask_f = float(best_ask)
            bid_f = float(quote_bid)
            ask_f = float(quote_ask)

            base_buffer = max(0, int(getattr(self.config, "post_only_buffer_ticks", 0)))
            dyn_buffer = 0
            if now < state._post_only_buffer_ticks_until:
                dyn_buffer = max(0, int(state._post_only_buffer_ticks_dynamic))
            buffer_ticks = max(base_buffer, dyn_buffer)
            buffer_px = buffer_ticks * tick

            # If pinned long, tighten ask inside the spread when possible.
            if is_pinned and state.net_position > 0:
                inside_ticks = max(0, int(getattr(self.config, "pinned_inside_spread_ticks", 1)))
                if inside_ticks > 0 and best_ask_f - best_bid_f > tick:
                    target_ask = best_ask_f - (inside_ticks * tick)
                    ask_f = min(ask_f, target_ask)
                # Ensure post-only safety vs bid
                ask_f = max(ask_f, best_bid_f + tick)
                # Do not widen away from the touch during pin liquidation
                buffer_ticks = 0
                buffer_px = 0.0

            # Anchor to the touch to stay maker.
            # This is more robust than only checking against the opposite side, because
            # the top-of-book can move between quote calc and submit.
            bid_f = min(bid_f, best_bid_f - buffer_px)
            ask_f = max(ask_f, best_ask_f + buffer_px)

            # If market is locked/tight or skew collapses quotes, widen safely.
            if bid_f >= ask_f:
                bid_f = best_bid_f - tick
                ask_f = best_ask_f + tick

            quote_bid = instrument.make_price(bid_f)
            quote_ask = instrument.make_price(ask_f)
        
        # Calculate asymmetric sizes
        bid_size, ask_size = state.inventory_manager.calculate_sizes(
            position=state.net_position,
            optimal_target=optimal_target,
            base_size=config.base_qty,
        )

        # Dynamic size throttling (inventory + volatility + spread width)
        size_multiplier = 1.0
        inv_ratio = abs(position_ratio)
        if inv_ratio > 0:
            size_multiplier *= max(
                float(getattr(self.config, "size_throttle_min_multiplier", 0.2)),
                1.0 - inv_ratio,
            )

        vol_low = float(getattr(self.config, "size_throttle_vol_low", 0.0006))
        vol_high = float(getattr(self.config, "size_throttle_vol_high", 0.0015))
        if current_volatility >= vol_high:
            size_multiplier *= float(getattr(self.config, "size_throttle_min_multiplier", 0.2))
        elif current_volatility <= vol_low:
            size_multiplier *= float(getattr(self.config, "size_throttle_max_multiplier", 1.2))

        if inv_ratio <= float(getattr(self.config, "size_throttle_flat_pos_ratio", 0.15)) and spread_bps >= float(
            getattr(self.config, "size_throttle_wide_spread_bps", 4.0)
        ):
            size_multiplier *= float(getattr(self.config, "size_throttle_max_multiplier", 1.2))

        size_multiplier *= hedge_size_multiplier
        size_multiplier *= markout_size_multiplier

        # Correlation-aware combined exposure cap
        if other_state is not None:
            corr = self.correlation_monitor.calculate_correlation(instrument_id, other_state.instrument_id)
            if abs(corr) >= float(getattr(self.config, "correlation_threshold", 0.8)):
                combined = self._combined_exposure_usdt()
                cap = float(self.config.total_capital_usd) * float(getattr(self.config, "max_correlated_exposure_pct", 0.7))
                if combined > cap and cap > 0:
                    size_multiplier *= max(0.1, cap / combined)

        size_multiplier = max(0.0, size_multiplier)
        bid_size *= Decimal(str(size_multiplier))
        ask_size *= Decimal(str(size_multiplier))
        
        # Check OBI signal (weighted by ML confidence)
        weighted_obi = state.obi_ema * ml_confidence
        should_quote_bid = weighted_obi > -self.config.obi_entry_threshold
        should_quote_ask = weighted_obi < self.config.obi_entry_threshold

        # Momentum-gated directional micro-guard (block entry side only)
        block_bids, block_asks = self._check_micro_guard(state, mid)
        if block_bids and state.net_position >= 0:
            should_quote_bid = False
        if block_asks and state.net_position <= 0:
            should_quote_ask = False

        # Momentum filter: avoid quoting into strong trend moves
        if state.skip_bids:
            should_quote_bid = False
        if state.skip_asks:
            should_quote_ask = False

        # Maker-only inventory exits (one-sided when off target)
        if exit_only_long:
            bid_size = Decimal(0)
        elif exit_only_short:
            ask_size = Decimal(0)

        # Inventory pin mode: disable re-accumulation and tighten liquidation side
        if is_pinned:
            if state.net_position > 0:
                bid_size = Decimal(0)
            elif state.net_position < 0:
                ask_size = Decimal(0)

        # Minimum edge filter + fee-aware fill probability
        edge_signal_bps = weighted_obi * float(getattr(self.config, "edge_bps_multiplier", 10.0))
        edge_buy_bps = edge_signal_bps
        edge_sell_bps = -edge_signal_bps
        required_edge_bps = (
            float(getattr(self.config, "maker_fee_bps", 1.0))
            + float(config.min_spread_bps)
            + float(getattr(self.config, "min_edge_bps", 0.5))
        )
        fill_prob = min(1.0, max(0.0, float(recent_fill_rate)))
        min_fill_prob = float(getattr(self.config, "min_fill_prob", 0.15))

        effective_edge_buy = edge_buy_bps * fill_prob
        effective_edge_sell = edge_sell_bps * fill_prob

        # Minimum edge filter
        if edge_buy_bps < required_edge_bps:
            should_quote_bid = False
        if edge_sell_bps < required_edge_bps:
            should_quote_ask = False

        if effective_edge_buy < required_edge_bps and fill_prob < min_fill_prob:
            should_quote_bid = False
        if effective_edge_sell < required_edge_bps and fill_prob < min_fill_prob:
            should_quote_ask = False

        # Spot cash safety: do not place sell quotes without inventory (no shorting).
        if state.net_position <= 0:
            should_quote_ask = False
        
        # === CORRELATION-ADJUSTED POSITION LIMITS (Phase 4) ===
        # Correlation monitoring (v003 feature - disabled in v002)
        # Update correlation monitor (if we have 2 instruments)
        # if len(self._instruments) == 2:
        #     instruments_list = list(self._instruments.values())
        #     if len(instruments_list) == 2:
        #         state1, state2 = instruments_list[0], instruments_list[1]
        #         mid1 = self._get_mid_price(state1) or mid
        #         mid2 = self._get_mid_price(state2) or mid
        #         self.correlation_monitor.update_prices(mid1, mid2, now)
        #         
        #         # Get correlation-adjusted limits
        #         corr_limits = self.correlation_monitor.get_correlation_adjusted_limits(
        #             base_limit_usd=self.config.total_capital_usd * self.config.max_total_exposure_pct,
        #             position1=state1.net_position,
        #             position2=state2.net_position,
        #             price1=mid1,
        #             price2=mid2,
        #         )
        #         
        #         # Check if we're at correlation-adjusted limit
        #         combined_exposure = self.correlation_monitor.calculate_combined_exposure(
        #             state1.net_position, state2.net_position, mid1, mid2
        #         )
        #         
        #         if combined_exposure >= corr_limits['max_combined_usd']:
        #             # At correlation limit - reduce or skip quotes
        #             # Reduce sizes by 50%
        #             bid_size = bid_size * Decimal("0.5")
        #             ask_size = ask_size * Decimal("0.5")
        
        # Don't quote if at position limits (safety check)
        if state.net_position >= config.max_position_qty:
            bid_size = Decimal(0)
        if state.net_position <= -config.max_position_qty:
            ask_size = Decimal(0)
        
        # Cancel/replace only if quotes moved enough to justify losing queue priority.
        if not self._should_requote(state, quote_bid, quote_ask, instrument):
            state.quote_count += 1
            return

        self._cancel_quotes(state)
        
        # Place new orders
        submitted = False
        if should_quote_bid and bid_size > 0:
            bid_order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.BUY,
                quantity=instrument.make_qty(float(bid_size)),
                price=quote_bid,
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
            self.submit_order(bid_order)
            state.active_bid_order_id = bid_order.client_order_id
            submitted = True
        
        if should_quote_ask and ask_size > 0:
            ask_order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(float(ask_size)),
                price=quote_ask,
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
            self.submit_order(ask_order)
            state.active_ask_order_id = ask_order.client_order_id
            submitted = True

        if submitted:
            state.last_quote_micro_price = float(mid)
            state.last_quote_update_time = now
        
        state.quote_count += 1
        
        # Log periodically with comprehensive metrics
        if state.quote_count % 100 == 0:
            ml_stats = state.ml_predictor.get_stats()
            vol_regime = state.volatility_estimator.get_volatility_regime()
            
            # Get correlation state if multi-pair
            corr_info = ""
            if len(self._instruments) == 2:
                instruments_list = list(self._instruments.values())
                if len(instruments_list) == 2:
                    state1, state2 = instruments_list[0], instruments_list[1]
                    mid1 = self._get_mid_price(state1) or mid
                    mid2 = self._get_mid_price(state2) or mid
                    corr_state = self.correlation_monitor.get_state(
                        state1.net_position, state2.net_position, mid1, mid2
                    )
                    corr_info = f" | Corr={corr_state.correlation:+.2f}"
            
            self.log.info(
                f"{instrument_id.symbol} | {metrics.to_log_string()} | "
                f"ML: conf={ml_confidence:.2f} acc={ml_stats['accuracy']:.2%} | "
                f"Spread: {spread_bps:.1f}bps ({vol_regime} vol){corr_info}",
                LogColor.CYAN if abs(metrics.position_ratio) < 0.5 else LogColor.YELLOW,
            )
    
    def _cancel_quotes(self, state: InstrumentState):
        """Cancel active quotes for instrument."""
        if state.active_bid_order_id:
            order = self.cache.order(state.active_bid_order_id)
            if order:
                self.cancel_order(order)
            state.active_bid_order_id = None
        
        if state.active_ask_order_id:
            order = self.cache.order(state.active_ask_order_id)
            if order:
                self.cancel_order(order)
            state.active_ask_order_id = None

    def _should_requote(
        self,
        state: InstrumentState,
        new_bid: Price,
        new_ask: Price,
        instrument,
    ) -> bool:
        """Decide whether to cancel/replace based on a minimum tick move threshold."""
        min_ticks = max(0, int(getattr(self.config, "min_requote_ticks", 0)))
        if min_ticks == 0:
            return True

        tick = float(instrument.price_increment)
        if tick <= 0:
            return True

        # If there are no active orders yet, we should quote.
        active_prices: list[float] = []
        if state.active_bid_order_id:
            o = self.cache.order(state.active_bid_order_id)
            if o and o.price:
                active_prices.append(float(o.price))
                if abs(float(new_bid) - float(o.price)) >= min_ticks * tick:
                    return True
        if state.active_ask_order_id:
            o = self.cache.order(state.active_ask_order_id)
            if o and o.price:
                active_prices.append(float(o.price))
                if abs(float(new_ask) - float(o.price)) >= min_ticks * tick:
                    return True

        # If we can't resolve current prices, err on re-quoting.
        return False if active_prices else True
    
    def _check_emergency_conditions(self) -> None:
        """Check for emergency stop conditions."""
        if self._max_loss_trigger:
            return  # Already triggered
        
        # Calculate total unrealized P&L
        total_unrealized = sum(
            state.unrealized_pnl for state in self._instruments.values()
        )
        
        now = time.time()
        if total_unrealized <= self.config.emergency_liquidation_loss_usd:
            if self._emergency_start_time == 0.0:
                self._emergency_start_time = now
            persist_s = max(0.0, float(getattr(self.config, "emergency_loss_persist_seconds", 8.0)))
            if persist_s == 0.0 or (now - self._emergency_start_time) >= persist_s:
                self._max_loss_trigger = True
                self.log.error(
                    f"EMERGENCY STOP: Total unrealized P&L {total_unrealized:.2f} "
                    f"<= {self.config.emergency_liquidation_loss_usd:.2f} for {persist_s:.1f}s",
                    LogColor.RED,
                )
                self._emergency_flatten()
        else:
            self._emergency_start_time = 0.0
    
    def _emergency_flatten(self) -> None:
        """Emergency liquidation of all positions."""
        self.log.warning("Flattening all positions...", LogColor.YELLOW)
        
        for state in self._instruments.values():
            # Cancel all quotes
            self._cancel_quotes(state)
            
            # Market orders to flatten (simplified - improve in Phase 2)
            if abs(state.net_position) > 0:
                self.log.warning(
                    f"Emergency flatten {state.instrument_id.symbol}: "
                    f"{state.net_position}",
                    LogColor.YELLOW,
                )
        
        self.stop()
    
    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle order fills and update ML model."""
        instrument_id = event.instrument_id
        state = self._instruments.get(instrument_id)
        
        if not state:
            return
        
        # Calculate fill outcome for ML learning
        fill_price = float(event.last_px)
        current_mid = self._get_reference_price(state)
        
        if current_mid and state.last_fill_price > 0:
            # Determine if fill was good or bad (simplified)
            # Good: Buy below mid or sell above mid
            # Bad: Buy above mid or sell below mid (adverse selection)
            if event.order_side == OrderSide.BUY:
                outcome = 1.0 if fill_price < current_mid else 0.0
            else:  # SELL
                outcome = 1.0 if fill_price > current_mid else 0.0
            
            # Update ML model with outcome
            # Extract features at time of fill
            bids_list = list(state.book.bids())
            asks_list = list(state.book.asks())
            config = self._instrument_configs[instrument_id]
            
            features = state.feature_extractor.extract_features(
                obi_ema=state.obi_ema,
                obi_history=state.obi_history,
                book_bids=bids_list,
                book_asks=asks_list,
                best_bid_price=float(state.book.best_bid_price()) if state.book.best_bid_price() else None,
                best_ask_price=float(state.book.best_ask_price()) if state.book.best_ask_price() else None,
                net_position=state.net_position,
                max_position=config.max_position_qty,
                last_fill_time=state.last_fill_time,
                quote_count=state.quote_count,
                fill_count=state.fill_count,
            )
            
            state.ml_predictor.update(features, outcome)
        
        # Store fill price for next learning cycle
        state.last_fill_price = fill_price

        # Schedule markout checks
        if state.markout_queue is not None:
            side_sign = 1.0 if event.order_side == OrderSide.BUY else -1.0
            now = time.time()
            for horizon_ms in self._markout_horizons_ms:
                due_time = now + (float(horizon_ms) / 1000.0)
                state.markout_queue.append({
                    "due_time": due_time,
                    "fill_price": fill_price,
                    "side_sign": side_sign,
                    "horizon_ms": int(horizon_ms),
                })
        
        fill_qty = Decimal(str(event.last_qty))
        fill_px = Decimal(str(event.last_px))

        # Track fees
        try:
            state.fees_paid += Decimal(str(event.commission.as_decimal()))
        except Exception:
            pass
        # Netting cost-basis accounting (supports reducing/closing positions)
        prev_pos = state.net_position
        prev_cost = state.position_cost_usdt

        if event.order_side == OrderSide.BUY:
            new_pos = prev_pos + fill_qty
            trade_cost = fill_qty * fill_px
            if prev_pos >= 0:
                # Increasing/establishing a long
                new_cost = prev_cost + trade_cost
            else:
                # Covering a short (realize PnL on the reduced portion)
                avg_entry = abs(prev_cost / prev_pos) if prev_pos != 0 else Decimal(0)
                closing_qty = min(fill_qty, abs(prev_pos))
                state.realized_pnl += (avg_entry - fill_px) * closing_qty
                remaining_qty = fill_qty - closing_qty
                new_cost = prev_cost + (closing_qty * avg_entry) + (remaining_qty * fill_px)
            state.net_position = new_pos
            state.position_cost_usdt = new_cost
        else:
            new_pos = prev_pos - fill_qty
            trade_proceeds = fill_qty * fill_px
            if prev_pos <= 0:
                # Increasing/establishing a short
                new_cost = prev_cost - trade_proceeds
            else:
                # Selling out of a long (realize PnL on the reduced portion)
                avg_entry = (prev_cost / prev_pos) if prev_pos != 0 else Decimal(0)
                closing_qty = min(fill_qty, prev_pos)
                state.realized_pnl += (fill_px - avg_entry) * closing_qty
                remaining_qty = fill_qty - closing_qty
                new_cost = prev_cost - (closing_qty * avg_entry) - (remaining_qty * fill_px)
            state.net_position = new_pos
            state.position_cost_usdt = new_cost

        state.fill_count += 1
        state.last_fill_time = time.time()
        self._total_trades += 1

        # Calculate unrealized P&L from cost basis
        if current_mid and state.net_position != 0:
            mid_px = Decimal(str(current_mid))
            if state.net_position > 0:
                avg_entry = state.position_cost_usdt / state.net_position
                state.unrealized_pnl = (mid_px - avg_entry) * state.net_position
            else:
                avg_entry = abs(state.position_cost_usdt / state.net_position)
                state.unrealized_pnl = (avg_entry - mid_px) * abs(state.net_position)
        else:
            state.unrealized_pnl = Decimal(0)

        # Log fill with ML stats
        ml_stats = state.ml_predictor.get_stats()
        self.log.info(
            f"FILL | {instrument_id.symbol} | "
            f"{event.order_side.name} {event.last_qty} @ {event.last_px} | "
            f"Position: {state.net_position} | "
            f"ML Acc: {ml_stats['accuracy']:.1%}",
            LogColor.GREEN,
        )

        # Shared adverse-selection cooldown for the other leg
        other_state = self._get_other_state(instrument_id)
        if other_state is not None:
            cooldown_s = max(0.0, float(getattr(self.config, "shared_adverse_cooldown_ms", 1500)) / 1000.0)
            if cooldown_s > 0:
                self._shared_adverse_cooldown_until[other_state.instrument_id] = time.time() + cooldown_s

    def on_order_rejected(self, event: OrderRejected) -> None:
        """Handle rejected orders (notably post-only rejections)."""
        state = self._instruments.get(event.instrument_id)
        if not state:
            return

        # Clear active IDs if the rejected order was one of our live quotes.
        if state.active_bid_order_id and event.client_order_id == state.active_bid_order_id:
            state.active_bid_order_id = None
        if state.active_ask_order_id and event.client_order_id == state.active_ask_order_id:
            state.active_ask_order_id = None

        if not getattr(event, "due_post_only", False):
            return

        # Temporarily widen the post-only buffer to reduce repeated rejects
        # when the top-of-book is moving between quote calc and submit.
        state.post_only_reject_count += 1
        base_buffer = max(0, int(getattr(self.config, "post_only_buffer_ticks", 0)))
        bump = max(1, int(getattr(self.config, "post_only_reject_bump_ticks", 1)))
        cooldown_s = max(0.0, float(getattr(self.config, "post_only_reject_cooldown_ms", 1500)) / 1000.0)

        widened = base_buffer + (bump * state.post_only_reject_count)
        state._post_only_buffer_ticks_dynamic = max(state._post_only_buffer_ticks_dynamic, widened)
        state._post_only_buffer_ticks_until = time.time() + cooldown_s

        # Force a quick re-quote on the next book update (and allow immediate refresh).
        state.last_quote_time = 0.0

        reason = getattr(event, "reason", "")
        if reason:
            reason = f" | Reason: {reason}"
        self.log.info(
            f"{event.instrument_id.symbol} | Post-only reject #{state.post_only_reject_count} | "
            f"Widening buffer to {state._post_only_buffer_ticks_dynamic}t for {int(cooldown_s * 1000)}ms"
            f"{reason}",
            LogColor.YELLOW,
        )

    def _rebalance_ioc(
        self,
        instrument_id: InstrumentId,
        state: InstrumentState,
        optimal_target: Decimal,
        mid: float,
    ) -> bool:
        """Last-resort inventory rebalance using an IOC taker limit order."""
        if not getattr(self.config, "enable_taker_ioc_rebalance", True):
            return False

        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            return False

        cfg = self._instrument_configs[instrument_id]
        imbalance = state.net_position - optimal_target
        if cfg.max_position_qty == 0:
            return False

        pos_ratio = abs(float(state.net_position)) / float(cfg.max_position_qty)
        if pos_ratio < float(getattr(self.config, "rebalance_ioc_min_position_ratio", 0.85)):
            return False

        best_bid = state.book.best_bid_price()
        best_ask = state.book.best_ask_price()
        if not best_bid or not best_ask:
            return False

        slip_bps = float(getattr(self.config, "rebalance_ioc_max_slippage_bps", 3.0))
        slip_bps = max(0.0, slip_bps)

        qty = min(abs(imbalance), cfg.max_position_qty)
        if qty <= 0:
            return False

        # Cancel maker quotes before taking liquidity
        self._cancel_quotes(state)

        if imbalance > 0:
            # Too long -> SELL
            limit_px = mid * (1.0 - slip_bps / 10000.0)
            limit_px = min(limit_px, float(best_bid))  # ensure crosses bid
            price = instrument.make_price(limit_px)
            order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(float(qty)),
                price=price,
                time_in_force=TimeInForce.IOC,
                post_only=False,
            )
        else:
            # Too short -> BUY
            limit_px = mid * (1.0 + slip_bps / 10000.0)
            limit_px = max(limit_px, float(best_ask))  # ensure crosses ask
            price = instrument.make_price(limit_px)
            order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.BUY,
                quantity=instrument.make_qty(float(qty)),
                price=price,
                time_in_force=TimeInForce.IOC,
                post_only=False,
            )

        self.submit_order(order)
        self.log.warning(
            f"IOC REBALANCE | {instrument_id.symbol} | imbalance={imbalance} qty={qty} slip_bps={slip_bps}",
            LogColor.YELLOW,
        )
        return True

    def _pinned_chunk_ioc(
        self,
        instrument_id: InstrumentId,
        state: InstrumentState,
        mid: float,
    ) -> bool:
        """Reduce inventory in chunks when pinned for too long."""
        if not getattr(self.config, "enable_taker_ioc_rebalance", True):
            return False

        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            return False

        cfg = self._instrument_configs[instrument_id]
        if cfg.max_position_qty == 0:
            return False

        best_bid = state.book.best_bid_price()
        best_ask = state.book.best_ask_price()
        if not best_bid or not best_ask:
            return False

        chunk_ratio = float(getattr(self.config, "pinned_chunk_ratio", 0.25))
        chunk_ratio = max(0.0, min(chunk_ratio, 1.0))
        qty = Decimal(str(float(cfg.max_position_qty) * chunk_ratio))
        qty = min(abs(state.net_position), qty)
        if qty <= 0:
            return False

        # Cancel maker quotes before taking liquidity
        self._cancel_quotes(state)

        slip_bps = float(getattr(self.config, "rebalance_ioc_max_slippage_bps", 3.0))
        slip_bps = max(0.0, slip_bps)

        if state.net_position > 0:
            limit_px = mid * (1.0 - slip_bps / 10000.0)
            limit_px = min(limit_px, float(best_bid))
            price = instrument.make_price(limit_px)
            order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(float(qty)),
                price=price,
                time_in_force=TimeInForce.IOC,
                post_only=False,
            )
        else:
            limit_px = mid * (1.0 + slip_bps / 10000.0)
            limit_px = max(limit_px, float(best_ask))
            price = instrument.make_price(limit_px)
            order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.BUY,
                quantity=instrument.make_qty(float(qty)),
                price=price,
                time_in_force=TimeInForce.IOC,
                post_only=False,
            )

        self.submit_order(order)
        self.log.warning(
            f"PINNED CHUNK IOC | {instrument_id.symbol} | qty={qty} slip_bps={slip_bps}",
            LogColor.YELLOW,
        )
        return True
    
    def _log_instrument_state(self, state: InstrumentState):
        """Log current state for instrument."""
        config = self._instrument_configs[state.instrument_id]
        position_ratio = abs(float(state.net_position)) / float(config.max_position_qty)
        
        self.log.info(
            f"{state.instrument_id.symbol} | "
            f"OBI={state.obi_ema:+.3f} | "
            f"Pos={state.net_position} ({position_ratio:.1%}) | "
            f"Fills={state.fill_count} | "
            f"Quotes={state.quote_count}"
        )
    
    def on_stop(self) -> None:
        """Cleanup on stop and save ML model weights."""
        self.log.info("Stopping multi-pair strategy...", LogColor.YELLOW)
        
        # Save ML model weights for each instrument
        for instrument_id, state in self._instruments.items():
            # Cancel all quotes
            self._cancel_quotes(state)
            
            # Save ML weights
            weights_path = f"strategy/ml/model_weights/{instrument_id.symbol}_weights.npz"
            try:
                state.ml_predictor.save_weights(weights_path)
                ml_stats = state.ml_predictor.get_stats()
                self.log.info(
                    f"Saved ML weights for {instrument_id.symbol} | "
                    f"Predictions: {ml_stats['predictions']} | "
                    f"Accuracy: {ml_stats['accuracy']:.1%}",
                    LogColor.GREEN,
                )
            except Exception as e:
                self.log.error(f"Failed to save ML weights: {e}", LogColor.RED)
        
        # Log final summary
        self.log.info(
            "="*70 + "\n" +
            f"Total Trades: {self._total_trades}\n" +
            "="*70,
            LogColor.CYAN,
        )
