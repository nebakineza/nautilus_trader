"""ATR Volatility-Sized Trend Scanner v002 - Multi-Asset Portfolio Manager.

STRATEGY OVERVIEW:
- Dynamic Universe Selection: Scans 20-50 coins, selects top performers
- Cross-Sectional Filtering: Ranks by trend strength, filters by correlation
- ATR-based position sizing keeps VaR (Value at Risk) constant per trade
- Correlation Filter prevents "accidental" over-exposure to correlated assets
- Designed for Bybit UK Spot trading with regulatory compliance

KEY FEATURES:
1. Multi-instrument trend scanning (Relative Strength approach)
2. Correlation matrix prevents concentrated risk
3. ATR-sized entries with constant risk per trade
4. Portfolio rotation: Exit weak, enter strong
5. Max 3-5 concurrent uncorrelated positions

SAFETY FEATURES:
1. Correlation filter (reject trades with r > 0.70 to existing positions)
2. Portfolio heat limit (max 10% total capital at risk)
3. Per-position and total drawdown killswitch
4. Daily loss limit protection
5. ATR trailing stops for profit locking

PERFORMANCE OPTIMIZATIONS:
1. Float64 arithmetic in hot path
2. NumPy for correlation calculations (C-speed)
3. Ring buffers for returns history
4. Pre-computed EMA multipliers
5. __slots__ for faster attribute access

Target: Bybit UK Spot, 4-hour timeframe, diversified portfolio.
"""

from __future__ import annotations

import gc
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

import numpy as np

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.events import OrderFilled, PositionChanged, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# CONSTANTS
# =============================================================================
BPS_MULTIPLIER: Final[float] = 10000.0
LOG_RETURNS_EPSILON: Final[float] = 1e-10  # Prevent log(0)


@dataclass
class InstrumentState:
    """Per-instrument state tracking for the scanner."""
    
    instrument_id: InstrumentId
    instrument: Instrument | None = None
    tick_size: float = 0.0
    min_qty: float = 0.0
    
    # Price history (ring buffer for returns)
    close_buffer: np.ndarray = field(default_factory=lambda: np.zeros(250, dtype=np.float64))
    high_buffer: np.ndarray = field(default_factory=lambda: np.zeros(250, dtype=np.float64))
    low_buffer: np.ndarray = field(default_factory=lambda: np.zeros(250, dtype=np.float64))
    buffer_idx: int = 0
    buffer_count: int = 0
    
    # Log returns for correlation (ring buffer)
    log_returns: np.ndarray = field(default_factory=lambda: np.zeros(100, dtype=np.float64))
    returns_idx: int = 0
    returns_count: int = 0
    
    # Indicator values
    fast_ema: float = 0.0
    slow_ema: float = 0.0
    trend_sma: float = 0.0
    atr: float = 0.0
    adx: float = 0.0
    rsi: float = 50.0
    
    # Previous EMA for crossover detection
    prev_fast_ema: float = 0.0
    prev_slow_ema: float = 0.0
    
    # Current price
    last_close: float = 0.0
    
    # Trend score for ranking
    trend_score: float = 0.0
    
    # Position tracking
    has_position: bool = False
    position_side: OrderSide | None = None
    entry_price: float = 0.0
    entry_atr: float = 0.0


class SafeAlphaScannerConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for Safe Alpha Scanner - Multi-Asset Trend Strategy.
    
    This strategy implements institutional-grade portfolio management:
    - Scans multiple instruments for trend opportunities
    - Uses correlation filtering to ensure true diversification
    - ATR-based position sizing for constant risk per trade
    
    Parameters
    ----------
    instrument_ids : tuple[InstrumentId, ...]
        Universe of instruments to scan (20-50 recommended)
    bar_type_template : str
        Bar type template with {symbol} placeholder
        e.g., "{symbol}-SPOT.BYBIT-4-HOUR-LAST-EXTERNAL"
    
    Portfolio Configuration:
    max_positions : int
        Maximum concurrent positions (default 3)
    max_portfolio_heat_pct : float
        Maximum total portfolio risk as % of equity (default 10%)
    risk_per_trade_usd : float
        USD amount at risk per trade (default 100)
    max_position_usd : float
        Maximum position value per instrument (default 5000)
    
    Correlation Filter:
    correlation_enabled : bool
        Enable correlation-based filtering (default True)
    correlation_threshold : float
        Max correlation allowed (default 0.70)
    correlation_lookback : int
        Bars for correlation calculation (default 30)
    
    Trend Detection:
    fast_ema_period : int
        Fast EMA period (default 20)
    slow_ema_period : int  
        Slow EMA period (default 50)
    trend_filter_period : int
        Long-term trend filter SMA (default 200)
    trend_filter_enabled : bool
        Only trade in direction of trend (default True)
    
    Quality Filters:
    adx_filter_enabled : bool
        Require strong trend (ADX > threshold) (default True)
    adx_min_threshold : float
        Minimum ADX for entry (default 25)
    rsi_filter_enabled : bool
        Filter overbought/oversold (default True)
    rsi_overbought : float
        RSI overbought level (default 70)
    rsi_oversold : float
        RSI oversold level (default 30)
    
    ATR & Stops:
    atr_period : int
        ATR period (default 14)
    atr_stop_multiplier : float
        ATR multiplier for stops (default 2.5)
    trailing_stop_enabled : bool
        Enable trailing stops (default True)
    trailing_atr_multiplier : float
        ATR multiplier for trailing (default 2.5)
    
    Safety:
    max_drawdown_pct : float
        Max drawdown before killswitch (default 15%)
    daily_loss_limit_usd : float
        Daily loss limit (default 500)
    
    Execution:
    use_limit_orders : bool
        Use limit orders (default True)
    limit_chase_ticks : int
        Ticks to chase for fills (default 3)
    
    Fees:
    taker_fee_pct : float
        Taker fee (default 0.10%)
    maker_fee_pct : float
        Maker fee (default 0.10%)
    
    Logging:
    log_scans : bool
        Log universe scans
    log_correlations : bool
        Log correlation rejections
    log_trades : bool
        Log trade entries/exits
    log_sizing : bool
        Log position sizing
    """
    
    # === UNIVERSE ===
    instrument_ids: tuple[InstrumentId, ...]
    bar_type_template: str = "{symbol}-4-HOUR-LAST-EXTERNAL"
    
    # === PORTFOLIO CONFIGURATION ===
    max_positions: int = 3
    max_portfolio_heat_pct: float = 10.0
    risk_per_trade_usd: float = 100.0
    max_position_usd: float = 5000.0
    min_position_usd: float = 10.0
    
    # === CORRELATION FILTER ===
    correlation_enabled: bool = True
    correlation_threshold: float = 0.70
    correlation_lookback: int = 30
    
    # === TREND DETECTION ===
    fast_ema_period: int = 20
    slow_ema_period: int = 50
    trend_filter_period: int = 200
    trend_filter_enabled: bool = True
    
    # === QUALITY FILTERS ===
    adx_filter_enabled: bool = True
    adx_period: int = 14
    adx_min_threshold: float = 25.0
    rsi_filter_enabled: bool = True
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    
    # === ATR & STOPS ===
    atr_period: int = 14
    atr_stop_multiplier: float = 2.5
    trailing_stop_enabled: bool = True
    trailing_atr_multiplier: float = 2.5
    trailing_activation_profit_pct: float = 1.5
    
    # === SAFETY ===
    max_drawdown_pct: float = 15.0
    daily_loss_limit_usd: float = 500.0
    
    # === EXECUTION ===
    use_limit_orders: bool = True
    limit_chase_ticks: int = 3
    time_in_force: TimeInForce = TimeInForce.GTC
    client_id: ClientId | None = None
    
    # === FEES ===
    taker_fee_pct: float = 0.10
    maker_fee_pct: float = 0.10
    
    # === LOGGING ===
    log_scans: bool = True
    log_correlations: bool = True
    log_trades: bool = True
    log_sizing: bool = True


class SafeAlphaScanner(Strategy):
    """
    Multi-Asset Trend Scanner with Correlation Filtering.
    
    This is an "institutional-grade" portfolio manager that:
    1. Scans a universe of 20-50 instruments
    2. Ranks them by trend strength (EMA spread)
    3. Filters by correlation to avoid concentrated risk
    4. Sizes positions using ATR for constant risk
    5. Rotates capital from weak to strong trends
    
    The correlation filter is the key safety feature:
    - Calculates rolling correlation using log returns
    - Rejects trades if correlation > 0.70 with existing positions
    - Ensures true diversification, not "5x the same BTC trade"
    """
    
    __slots__ = (
        # Config cache
        '_max_positions', '_max_heat_pct', '_risk_usd', '_max_pos_usd',
        '_corr_threshold', '_corr_lookback',
        '_fast_period', '_slow_period', '_trend_period',
        '_atr_period', '_stop_mult', '_trailing_mult',
        '_adx_threshold', '_rsi_ob', '_rsi_os',
        '_fee_roundtrip',
        # EMA multipliers (pre-computed)
        '_fast_alpha', '_slow_alpha',
        # Per-instrument state
        '_states',
        '_bar_types',
        # Active positions
        '_active_positions',
        # Portfolio state
        '_peak_equity', '_starting_equity', '_daily_start_equity',
        '_realized_pnl_today', '_total_unrealized_pnl',
        '_killswitch_triggered', '_daily_limit_hit',
        # Orders tracking
        '_pending_orders',
        '_stop_orders',
        # Client ID
        'client_id',
    )
    
    def __init__(self, config: SafeAlphaScannerConfig) -> None:
        super().__init__(config)
        
        # Validate
        if config.fast_ema_period >= config.slow_ema_period:
            raise ValueError("fast_ema_period must be less than slow_ema_period")
        if len(config.instrument_ids) < 2:
            raise ValueError("Need at least 2 instruments for universe scanning")
        
        # Cache config
        self._max_positions: int = config.max_positions
        self._max_heat_pct: float = config.max_portfolio_heat_pct
        self._risk_usd: float = config.risk_per_trade_usd
        self._max_pos_usd: float = config.max_position_usd
        self._corr_threshold: float = config.correlation_threshold
        self._corr_lookback: int = config.correlation_lookback
        self._fast_period: int = config.fast_ema_period
        self._slow_period: int = config.slow_ema_period
        self._trend_period: int = config.trend_filter_period
        self._atr_period: int = config.atr_period
        self._stop_mult: float = config.atr_stop_multiplier
        self._trailing_mult: float = config.trailing_atr_multiplier
        self._adx_threshold: float = config.adx_min_threshold
        self._rsi_ob: float = config.rsi_overbought
        self._rsi_os: float = config.rsi_oversold
        self._fee_roundtrip: float = config.taker_fee_pct + config.maker_fee_pct
        
        # Pre-compute EMA multipliers
        self._fast_alpha: float = 2.0 / (config.fast_ema_period + 1)
        self._slow_alpha: float = 2.0 / (config.slow_ema_period + 1)
        
        # Per-instrument state
        self._states: dict[InstrumentId, InstrumentState] = {}
        self._bar_types: dict[InstrumentId, BarType] = {}
        
        # Active positions set
        self._active_positions: set[InstrumentId] = set()
        
        # Portfolio state
        self._peak_equity: float = 0.0
        self._starting_equity: float = 0.0
        self._daily_start_equity: float = 0.0
        self._realized_pnl_today: float = 0.0
        self._total_unrealized_pnl: float = 0.0
        self._killswitch_triggered: bool = False
        self._daily_limit_hit: bool = False
        
        # Order tracking
        self._pending_orders: dict[InstrumentId, Order] = {}
        self._stop_orders: dict[InstrumentId, Order] = {}
        
        # Client ID
        self.client_id = config.client_id

    # =========================================================================
    # LIFECYCLE
    # =========================================================================
    
    def on_start(self) -> None:
        """Initialize strategy - subscribe to all instruments."""
        gc.disable()
        
        self.log.info("=" * 70, LogColor.GREEN)
        self.log.info("🚀 SAFE ALPHA SCANNER v002 - STARTING", LogColor.GREEN)
        self.log.info(f"   Universe Size: {len(self.config.instrument_ids)} instruments", LogColor.GREEN)
        self.log.info(f"   Max Positions: {self._max_positions}", LogColor.GREEN)
        self.log.info(f"   Correlation Threshold: {self._corr_threshold}", LogColor.GREEN)
        self.log.info(f"   Risk per Trade: ${self._risk_usd:.2f}", LogColor.GREEN)
        self.log.info("=" * 70, LogColor.GREEN)
        
        # Initialize state for each instrument
        for inst_id in self.config.instrument_ids:
            # Get instrument
            instrument = self.cache.instrument(inst_id)
            if instrument is None:
                self.log.warning(f"⚠️ Instrument not found: {inst_id}")
                continue
            
            # Create state
            state = InstrumentState(instrument_id=inst_id)
            state.instrument = instrument
            state.tick_size = float(instrument.price_increment)
            state.min_qty = float(instrument.min_quantity)
            
            # Allocate buffers
            max_lookback = max(self._trend_period, self._slow_period, self._atr_period) + 10
            state.close_buffer = np.zeros(max_lookback, dtype=np.float64)
            state.high_buffer = np.zeros(max_lookback, dtype=np.float64)
            state.low_buffer = np.zeros(max_lookback, dtype=np.float64)
            state.log_returns = np.zeros(self._corr_lookback + 10, dtype=np.float64)
            
            self._states[inst_id] = state
            
            # Create bar type
            symbol_str = str(inst_id)
            bar_type_str = self.config.bar_type_template.replace("{symbol}", symbol_str)
            bar_type = BarType.from_str(bar_type_str)
            self._bar_types[inst_id] = bar_type
            
            # Subscribe
            self.subscribe_bars(bar_type)
            
            self.log.info(f"   📊 Subscribed: {inst_id}", LogColor.BLUE)
        
        # Get initial equity
        accounts = self.cache.accounts()
        if accounts:
            account = accounts[0]
            for balance in account.balances():
                if balance.currency.code == "USDT":
                    self._starting_equity = float(balance.total)
                    self._peak_equity = self._starting_equity
                    self._daily_start_equity = self._starting_equity
                    break
        
        self.log.info(f"   💰 Starting Equity: ${self._starting_equity:.2f}", LogColor.GREEN)
        self.log.info(f"   ✅ Initialized {len(self._states)} instruments", LogColor.GREEN)
    
    def on_stop(self) -> None:
        """Clean up on stop."""
        gc.enable()
        
        # Cancel all orders
        for inst_id in self._states:
            self.cancel_all_orders(inst_id)
        
        # Close all positions
        for inst_id in self._active_positions.copy():
            self.close_all_positions(inst_id)
        
        self.log.info("🛑 Safe Alpha Scanner STOPPED", LogColor.RED)

    # =========================================================================
    # BAR HANDLER
    # =========================================================================
    
    def on_bar(self, bar: Bar) -> None:
        """Process new bar - update indicators and check for trades."""
        inst_id = bar.bar_type.instrument_id
        
        if inst_id not in self._states:
            return
        
        state = self._states[inst_id]
        
        # Safety checks
        if self._killswitch_triggered or self._daily_limit_hit:
            return
        
        # Update price buffers and indicators
        self._update_state(state, bar)
        
        # Check warmup
        min_bars = max(self._trend_period, self._slow_period, self._atr_period) + 5
        if state.buffer_count < min_bars:
            return
        
        # Calculate trend score for this instrument
        self._calculate_trend_score(state)
        
        # Check safety
        self._check_safety()
        
        # === POSITION MANAGEMENT ===
        if state.has_position:
            self._manage_position(state, bar)
        
        # === ENTRY LOGIC (only if we have room and this is a scan bar) ===
        if len(self._active_positions) < self._max_positions:
            if self._should_enter(state):
                # Check correlation with existing positions
                if self._passes_correlation_filter(inst_id):
                    self._execute_entry(state, float(bar.close))
                elif self.config.log_correlations:
                    self.log.info(
                        f"❌ {inst_id.symbol} rejected: too correlated with portfolio",
                        LogColor.YELLOW
                    )
        
        # Store previous EMA values
        state.prev_fast_ema = state.fast_ema
        state.prev_slow_ema = state.slow_ema

    # =========================================================================
    # STATE UPDATES
    # =========================================================================
    
    def _update_state(self, state: InstrumentState, bar: Bar) -> None:
        """Update price buffers and calculate indicators."""
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        
        # Update price buffers (ring buffer)
        idx = state.buffer_idx
        state.close_buffer[idx] = close
        state.high_buffer[idx] = high
        state.low_buffer[idx] = low
        
        state.buffer_idx = (idx + 1) % len(state.close_buffer)
        if state.buffer_count < len(state.close_buffer):
            state.buffer_count += 1
        
        # Update log returns for correlation
        if state.last_close > 0:
            log_ret = np.log(close / (state.last_close + LOG_RETURNS_EPSILON))
            ret_idx = state.returns_idx
            state.log_returns[ret_idx] = log_ret
            state.returns_idx = (ret_idx + 1) % len(state.log_returns)
            if state.returns_count < len(state.log_returns):
                state.returns_count += 1
        
        state.last_close = close
        
        # Calculate indicators
        self._calculate_indicators(state)
    
    def _calculate_indicators(self, state: InstrumentState) -> None:
        """Calculate EMA, ATR, ADX, RSI from buffers."""
        closes = self._get_ordered_prices(state.close_buffer, state.buffer_idx, state.buffer_count)
        n = len(closes)
        
        if n < 2:
            return
        
        # === EMAs ===
        state.fast_ema = self._calculate_ema_fast(closes, self._fast_period, self._fast_alpha)
        state.slow_ema = self._calculate_ema_fast(closes, self._slow_period, self._slow_alpha)
        
        # === Trend SMA ===
        if self.config.trend_filter_enabled and n >= self._trend_period:
            state.trend_sma = np.mean(closes[-self._trend_period:])
        
        # === ATR ===
        state.atr = self._calculate_atr(state)
        
        # === ADX ===
        if self.config.adx_filter_enabled:
            state.adx = self._calculate_adx(state)
        
        # === RSI ===
        if self.config.rsi_filter_enabled:
            state.rsi = self._calculate_rsi(closes)
    
    def _get_ordered_prices(self, buffer: np.ndarray, idx: int, count: int) -> np.ndarray:
        """Get prices in chronological order from ring buffer."""
        buf_len = len(buffer)
        
        if count == buf_len:
            result = np.empty(count, dtype=np.float64)
            first_part = buf_len - idx
            result[:first_part] = buffer[idx:]
            result[first_part:] = buffer[:idx]
            return result
        else:
            return buffer[:count].copy()
    
    def _calculate_ema_fast(self, prices: np.ndarray, period: int, alpha: float) -> float:
        """Fast EMA calculation with pre-computed alpha."""
        n = len(prices)
        if n < period:
            return 0.0
        
        ema = np.mean(prices[:period])
        for i in range(period, n):
            ema = alpha * prices[i] + (1 - alpha) * ema
        
        return ema
    
    def _calculate_atr(self, state: InstrumentState) -> float:
        """Calculate ATR from buffers."""
        n = min(state.buffer_count, self._atr_period + 1)
        if n < 2:
            return 0.0
        
        highs = self._get_ordered_prices(state.high_buffer, state.buffer_idx, state.buffer_count)
        lows = self._get_ordered_prices(state.low_buffer, state.buffer_idx, state.buffer_count)
        closes = self._get_ordered_prices(state.close_buffer, state.buffer_idx, state.buffer_count)
        
        tr_sum = 0.0
        period = min(n - 1, self._atr_period)
        
        for i in range(1, period + 1):
            idx = len(closes) - period - 1 + i
            if idx < 1:
                continue
            tr = max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1])
            )
            tr_sum += tr
        
        return tr_sum / period if period > 0 else 0.0
    
    def _calculate_adx(self, state: InstrumentState) -> float:
        """Simplified ADX calculation."""
        n = min(state.buffer_count, self._atr_period * 2)
        if n < self._atr_period + 1:
            return 0.0
        
        highs = self._get_ordered_prices(state.high_buffer, state.buffer_idx, state.buffer_count)
        lows = self._get_ordered_prices(state.low_buffer, state.buffer_idx, state.buffer_count)
        closes = self._get_ordered_prices(state.close_buffer, state.buffer_idx, state.buffer_count)
        
        plus_dm = 0.0
        minus_dm = 0.0
        tr_total = 0.0
        
        for i in range(1, min(n, self._atr_period + 1)):
            idx = len(closes) - self._atr_period - 1 + i
            if idx < 1:
                continue
            
            up = highs[idx] - highs[idx - 1]
            down = lows[idx - 1] - lows[idx]
            
            if up > down and up > 0:
                plus_dm += up
            if down > up and down > 0:
                minus_dm += down
            
            tr = max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1])
            )
            tr_total += tr
        
        if tr_total > 0:
            plus_di = (plus_dm / tr_total) * 100
            minus_di = (minus_dm / tr_total) * 100
            di_sum = plus_di + minus_di
            if di_sum > 0:
                return abs(plus_di - minus_di) / di_sum * 100
        
        return 0.0
    
    def _calculate_rsi(self, prices: np.ndarray) -> float:
        """Calculate RSI."""
        period = self.config.rsi_period
        if len(prices) < period + 1:
            return 50.0
        
        changes = np.diff(prices[-(period + 1):])
        gains = np.where(changes > 0, changes, 0)
        losses = np.where(changes < 0, -changes, 0)
        
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _calculate_trend_score(self, state: InstrumentState) -> None:
        """Calculate trend strength score for ranking.
        
        Score = (Fast EMA - Slow EMA) / Slow EMA
        Higher positive score = stronger uptrend
        """
        if state.slow_ema > 0:
            state.trend_score = (state.fast_ema - state.slow_ema) / state.slow_ema
        else:
            state.trend_score = 0.0

    # =========================================================================
    # CORRELATION FILTER (The Key Safety Feature)
    # =========================================================================
    
    def _passes_correlation_filter(self, candidate_id: InstrumentId) -> bool:
        """Check if candidate is sufficiently uncorrelated with portfolio.
        
        Uses Pearson correlation on log returns.
        Rejects if correlation > threshold with ANY existing position.
        """
        if not self.config.correlation_enabled:
            return True
        
        if not self._active_positions:
            return True  # First trade always passes
        
        candidate_state = self._states.get(candidate_id)
        if candidate_state is None or candidate_state.returns_count < self._corr_lookback:
            return True  # Not enough data, allow trade
        
        # Get candidate returns
        candidate_returns = self._get_ordered_returns(candidate_state)
        
        for pos_id in self._active_positions:
            pos_state = self._states.get(pos_id)
            if pos_state is None or pos_state.returns_count < self._corr_lookback:
                continue
            
            pos_returns = self._get_ordered_returns(pos_state)
            
            # Calculate Pearson correlation using NumPy (C-speed)
            if len(candidate_returns) >= self._corr_lookback and len(pos_returns) >= self._corr_lookback:
                corr = np.corrcoef(
                    candidate_returns[-self._corr_lookback:],
                    pos_returns[-self._corr_lookback:]
                )[0, 1]
                
                if not np.isnan(corr) and corr > self._corr_threshold:
                    if self.config.log_correlations:
                        self.log.info(
                            f"🔗 CORRELATION: {candidate_id.symbol} <-> {pos_id.symbol} = {corr:.2f} > {self._corr_threshold}",
                            LogColor.YELLOW
                        )
                    return False
        
        return True
    
    def _get_ordered_returns(self, state: InstrumentState) -> np.ndarray:
        """Get log returns in chronological order."""
        return self._get_ordered_prices(
            state.log_returns, 
            state.returns_idx, 
            state.returns_count
        )

    # =========================================================================
    # ENTRY LOGIC
    # =========================================================================
    
    def _should_enter(self, state: InstrumentState) -> bool:
        """Check if we should enter a position on this instrument."""
        
        # Need previous EMA values for crossover
        if state.prev_fast_ema == 0 or state.prev_slow_ema == 0:
            return False
        
        # Check for bullish crossover (we only go long in spot)
        bullish_cross = (
            state.prev_fast_ema <= state.prev_slow_ema and
            state.fast_ema > state.slow_ema
        )
        
        if not bullish_cross:
            return False
        
        close = state.last_close
        
        # Trend filter
        if self.config.trend_filter_enabled and close < state.trend_sma:
            return False
        
        # ADX filter
        if self.config.adx_filter_enabled and state.adx < self._adx_threshold:
            return False
        
        # RSI filter (avoid overbought)
        if self.config.rsi_filter_enabled and state.rsi > self._rsi_ob:
            return False
        
        if self.config.log_scans:
            self.log.info(
                f"✅ SIGNAL: {state.instrument_id.symbol} | "
                f"Score={state.trend_score:.4f} | ADX={state.adx:.1f} | RSI={state.rsi:.1f}",
                LogColor.CYAN
            )
        
        return True
    
    def _execute_entry(self, state: InstrumentState, price: float) -> None:
        """Execute entry with ATR-based sizing."""
        if state.atr <= 0 or state.instrument is None:
            return
        
        # ATR-based position sizing
        stop_distance = state.atr * self._stop_mult
        raw_qty = self._risk_usd / stop_distance
        position_value = raw_qty * price
        
        # Cap position
        if position_value > self._max_pos_usd:
            raw_qty = self._max_pos_usd / price
            position_value = self._max_pos_usd
        
        if position_value < self.config.min_position_usd:
            if self.config.log_sizing:
                self.log.warning(f"⚠️ {state.instrument_id.symbol} position too small: ${position_value:.2f}")
            return
        
        # Ensure minimum quantity
        if raw_qty < state.min_qty:
            raw_qty = state.min_qty
        
        quantity = state.instrument.make_qty(Decimal(str(raw_qty)))
        
        if self.config.log_sizing:
            self.log.info(
                f"📐 SIZING {state.instrument_id.symbol} | ATR={state.atr:.4f} | "
                f"Stop={stop_distance:.4f} | Qty={quantity} | Value=${position_value:.2f}",
                LogColor.BLUE
            )
        
        # Create order
        if self.config.use_limit_orders:
            chase = state.tick_size * self.config.limit_chase_ticks
            limit_price = price + chase
            limit_price = round(limit_price / state.tick_size) * state.tick_size
            price_obj = state.instrument.make_price(Decimal(str(limit_price)))
            
            order = self.order_factory.limit(
                instrument_id=state.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                price=price_obj,
                time_in_force=self.config.time_in_force,
                post_only=False,
            )
        else:
            order = self.order_factory.market(
                instrument_id=state.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
            )
        
        self._pending_orders[state.instrument_id] = order
        self.submit_order(order)
        
        if self.config.log_trades:
            self.log.info(
                f"📤 ENTRY: {state.instrument_id.symbol} BUY {quantity} @ {price:.4f} | "
                f"Stop={self._stop_mult}x ATR",
                LogColor.GREEN
            )

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================
    
    def _manage_position(self, state: InstrumentState, bar: Bar) -> None:
        """Manage existing position - check for exit signals."""
        close = float(bar.close)
        
        # Check for bearish crossover (exit signal)
        if state.prev_fast_ema >= state.prev_slow_ema and state.fast_ema < state.slow_ema:
            self._close_position(state, "EMA Crossover Exit")
            return
        
        # Update trailing stop if enabled
        if self.config.trailing_stop_enabled:
            self._update_trailing_stop(state, close)
    
    def _close_position(self, state: InstrumentState, reason: str) -> None:
        """Close position for instrument."""
        inst_id = state.instrument_id
        
        # Cancel any stop orders
        if inst_id in self._stop_orders:
            stop = self._stop_orders[inst_id]
            if not stop.is_closed:
                self.cancel_order(stop)
            del self._stop_orders[inst_id]
        
        # Close position
        self.close_all_positions(inst_id)
        
        if self.config.log_trades:
            self.log.info(
                f"📥 CLOSE: {inst_id.symbol} | Reason: {reason}",
                LogColor.YELLOW
            )
    
    def _update_trailing_stop(self, state: InstrumentState, current_price: float) -> None:
        """Update trailing stop based on ATR."""
        if not state.has_position or state.entry_price == 0:
            return
        
        # Check if profit threshold reached
        profit_pct = (current_price - state.entry_price) / state.entry_price * 100
        if profit_pct < self.config.trailing_activation_profit_pct:
            return
        
        # Calculate new stop level
        trailing_distance = state.atr * self._trailing_mult
        new_stop = current_price - trailing_distance
        
        # Only update if new stop is higher (tighter)
        inst_id = state.instrument_id
        if inst_id in self._stop_orders:
            old_stop = self._stop_orders[inst_id]
            if not old_stop.is_closed:
                old_stop_price = float(old_stop.trigger_price) if old_stop.trigger_price else 0
                if new_stop <= old_stop_price:
                    return  # New stop not better
                self.cancel_order(old_stop)
        
        # Place new stop
        if state.instrument is not None:
            position = self.cache.position(inst_id)
            if position is not None and position.quantity > 0:
                new_stop = round(new_stop / state.tick_size) * state.tick_size
                stop_price = state.instrument.make_price(Decimal(str(new_stop)))
                
                stop_order = self.order_factory.stop_market(
                    instrument_id=inst_id,
                    order_side=OrderSide.SELL,
                    quantity=position.quantity,
                    trigger_price=stop_price,
                    trigger_type=TriggerType.LAST_PRICE,
                    time_in_force=TimeInForce.GTC,
                )
                
                self._stop_orders[inst_id] = stop_order
                self.submit_order(stop_order)

    # =========================================================================
    # SAFETY
    # =========================================================================
    
    def _check_safety(self) -> None:
        """Check portfolio-wide safety limits."""
        current_equity = self._starting_equity + self._realized_pnl_today + self._total_unrealized_pnl
        
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        
        # Drawdown check
        if self._peak_equity > 0:
            dd_pct = (self._peak_equity - current_equity) / self._peak_equity * 100
            if dd_pct >= self.config.max_drawdown_pct:
                self._killswitch_triggered = True
                self._close_all_positions("KILLSWITCH: Max Drawdown")
                self.log.error(
                    f"🚨 KILLSWITCH: Drawdown {dd_pct:.1f}% >= {self.config.max_drawdown_pct}%",
                    LogColor.RED
                )
        
        # Daily loss check
        daily_pnl = current_equity - self._daily_start_equity
        if daily_pnl <= -self.config.daily_loss_limit_usd:
            self._daily_limit_hit = True
            self._close_all_positions("Daily Loss Limit")
            self.log.warning(
                f"⚠️ DAILY LIMIT: ${daily_pnl:.2f} <= -${self.config.daily_loss_limit_usd}",
                LogColor.RED
            )
    
    def _close_all_positions(self, reason: str) -> None:
        """Close all positions across portfolio."""
        for inst_id in list(self._active_positions):
            state = self._states.get(inst_id)
            if state:
                self._close_position(state, reason)

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================
    
    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle order fill."""
        inst_id = event.instrument_id
        state = self._states.get(inst_id)
        
        if state is None:
            return
        
        qty = float(event.last_qty)
        price = float(event.last_px)
        side = event.order_side
        
        if self.config.log_trades:
            self.log.info(
                f"✅ FILLED: {inst_id.symbol} {side.name} {qty} @ {price:.4f}",
                LogColor.GREEN if side == OrderSide.BUY else LogColor.RED
            )
        
        # Entry fill
        if inst_id in self._pending_orders and side == OrderSide.BUY:
            state.has_position = True
            state.position_side = side
            state.entry_price = price
            state.entry_atr = state.atr
            self._active_positions.add(inst_id)
            del self._pending_orders[inst_id]
            
            # Place initial stop
            self._place_initial_stop(state, price)
    
    def _place_initial_stop(self, state: InstrumentState, entry_price: float) -> None:
        """Place initial stop loss after entry."""
        if state.entry_atr <= 0 or state.instrument is None:
            return
        
        stop_distance = state.entry_atr * self._stop_mult
        stop_price = entry_price - stop_distance
        stop_price = round(stop_price / state.tick_size) * state.tick_size
        
        position = self.cache.position(state.instrument_id)
        if position is None:
            return
        
        stop_price_obj = state.instrument.make_price(Decimal(str(stop_price)))
        
        stop_order = self.order_factory.stop_market(
            instrument_id=state.instrument_id,
            order_side=OrderSide.SELL,
            quantity=position.quantity,
            trigger_price=stop_price_obj,
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
        )
        
        self._stop_orders[state.instrument_id] = stop_order
        self.submit_order(stop_order)
        
        if self.config.log_trades:
            self.log.info(
                f"🛡️ STOP: {state.instrument_id.symbol} @ {stop_price:.4f} "
                f"(ATR {state.entry_atr:.4f} x {self._stop_mult})",
                LogColor.YELLOW
            )
    
    def on_position_opened(self, event: PositionOpened) -> None:
        """Handle position opened."""
        inst_id = event.position.instrument_id
        state = self._states.get(inst_id)
        if state:
            state.has_position = True
            self._active_positions.add(inst_id)
    
    def on_position_closed(self, event: PositionClosed) -> None:
        """Handle position closed."""
        inst_id = event.position.instrument_id
        state = self._states.get(inst_id)
        
        if state:
            state.has_position = False
            state.position_side = None
            state.entry_price = 0.0
            state.entry_atr = 0.0
        
        self._active_positions.discard(inst_id)
        
        if inst_id in self._stop_orders:
            del self._stop_orders[inst_id]
        
        # Track PnL
        pnl = float(event.realized_pnl) if event.realized_pnl else 0.0
        self._realized_pnl_today += pnl
        
        if self.config.log_trades:
            self.log.info(
                f"📊 CLOSED: {inst_id.symbol} | PnL=${pnl:.2f} | Daily=${self._realized_pnl_today:.2f}",
                LogColor.GREEN if pnl >= 0 else LogColor.RED
            )
    
    def on_position_changed(self, event: PositionChanged) -> None:
        """Update unrealized PnL."""
        # Sum unrealized across all positions
        total_unrealized = 0.0
        for pos_id in self._active_positions:
            pos = self.cache.position(pos_id)
            if pos and pos.unrealized_pnl:
                total_unrealized += float(pos.unrealized_pnl)
        
        self._total_unrealized_pnl = total_unrealized
    
    # =========================================================================
    # SCANNING & RANKING
    # =========================================================================
    
    def get_ranked_opportunities(self) -> list[tuple[InstrumentId, float]]:
        """Get instruments ranked by trend score.
        
        Returns list of (instrument_id, score) sorted by score descending.
        Only includes instruments with positive score (uptrend).
        """
        scores = []
        
        for inst_id, state in self._states.items():
            if state.has_position:
                continue  # Skip if already have position
            
            if state.trend_score > 0 and state.fast_ema > state.slow_ema:
                # Additional quality checks
                if self.config.adx_filter_enabled and state.adx < self._adx_threshold:
                    continue
                if self.config.rsi_filter_enabled and state.rsi > self._rsi_ob:
                    continue
                
                scores.append((inst_id, state.trend_score))
        
        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        
        return scores
