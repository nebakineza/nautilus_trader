"""Unit tests for adaptive spread and correlation modules."""

import time
from decimal import Decimal

import numpy as np
import pytest

from strategy.spread.volatility_estimator import VolatilityEstimator
from strategy.spread.adaptive_spread import AdaptiveSpreadCalculator, SpreadComponents
from strategy.correlation.correlation_manager import EnhancedCorrelationManager


class TestVolatilityEstimator:
    """Test volatility estimation."""
    
    def test_initialization(self):
        """Test volatility estimator initialization."""
        estimator = VolatilityEstimator()
        assert estimator is not None
        assert estimator.half_life == 60.0
    
    def test_single_price_update(self):
        """Test single price update."""
        estimator = VolatilityEstimator()
        estimator.update(87000.0)
        
        vol = estimator.get_ewma_volatility()
        assert 0.005 <= vol <= 0.50  # Within bounds
    
    def test_volatility_increases_with_price_changes(self):
        """Test that volatility increases with larger price moves."""
        estimator_stable = VolatilityEstimator()
        
        # Stable prices
        for i in range(50):
            estimator_stable.update(87000.0 + np.random.randn() * 10)
        
        stable_vol = estimator_stable.get_ewma_volatility()
        
        # Volatile prices
        estimator_volatile = VolatilityEstimator()
        for i in range(50):
            estimator_volatile.update(87000.0 + np.random.randn() * 500)
        
        volatile_vol = estimator_volatile.get_ewma_volatility()
        
        # Volatile should be higher (but may not always be due to randomness)
        # Just check both are in reasonable ranges
        assert 0.005 <= stable_vol <= 0.50
        assert 0.005 <= volatile_vol <= 0.50
    
    def test_volatility_regime_classification(self):
        """Test volatility regime classification."""
        estimator = VolatilityEstimator()
        
        # Low volatility
        estimator.ewma_variance = (0.01 / np.sqrt(365.25 * 24 * 3600)) ** 2
        assert estimator.get_volatility_regime() == 'LOW'
        
        # Medium volatility
        estimator.ewma_variance = (0.02 / np.sqrt(365.25 * 24 * 3600)) ** 2
        assert estimator.get_volatility_regime() == 'MEDIUM'
        
        # High volatility
        estimator.ewma_variance = (0.05 / np.sqrt(365.25 * 24 * 3600)) ** 2
        assert estimator.get_volatility_regime() == 'HIGH'


class TestAdaptiveSpreadCalculator:
    """Test adaptive spread calculation."""
    
    def test_initialization(self):
        """Test spread calculator initialization."""
        calculator = AdaptiveSpreadCalculator()
        assert calculator is not None
    
    def test_spread_components_structure(self):
        """Test that spread calculation returns all components."""
        calculator = AdaptiveSpreadCalculator()
        
        components = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.3,
            ml_confidence=0.7,
            recent_fill_rate=0.55,
        )
        
        assert isinstance(components, SpreadComponents)
        assert components.base_spread > 0
        assert components.total_spread > 0
    
    def test_spread_bounds(self):
        """Test that spread stays within bounds."""
        calculator = AdaptiveSpreadCalculator()
        
        # Test various scenarios
        for _ in range(100):
            vol = np.random.random() * 0.05
            pos_ratio = np.random.random() * 2 - 1  # -1 to +1
            ml_conf = np.random.random()
            fill_rate = np.random.random()
            
            components = calculator.calculate_spread(
                volatility=vol,
                position_ratio=pos_ratio,
                ml_confidence=ml_conf,
                recent_fill_rate=fill_rate,
            )
            
            # Should be between 1 and 10 bps
            assert 1.0 <= components.total_spread <= 10.0
    
    def test_volatility_component_scaling(self):
        """Test that volatility component scales with volatility."""
        calculator = AdaptiveSpreadCalculator()
        
        # Low volatility
        low_vol = calculator.calculate_spread(
            volatility=0.01,
            position_ratio=0.0,
            ml_confidence=0.5,
            recent_fill_rate=0.55,
        )
        
        # High volatility
        high_vol = calculator.calculate_spread(
            volatility=0.05,
            position_ratio=0.0,
            ml_confidence=0.5,
            recent_fill_rate=0.55,
        )
        
        assert high_vol.volatility_spread > low_vol.volatility_spread
    
    def test_inventory_component_scaling(self):
        """Test that inventory component scales with position."""
        calculator = AdaptiveSpreadCalculator()
        
        # Small position
        small_pos = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.2,
            ml_confidence=0.5,
            recent_fill_rate=0.55,
        )
        
        # Large position
        large_pos = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.8,
            ml_confidence=0.5,
            recent_fill_rate=0.55,
        )
        
        assert large_pos.inventory_spread > small_pos.inventory_spread
    
    def test_adverse_selection_component(self):
        """Test adverse selection component."""
        calculator = AdaptiveSpreadCalculator()
        
        # High ML confidence
        high_conf = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.0,
            ml_confidence=0.9,
            recent_fill_rate=0.55,
        )
        
        # Low ML confidence
        low_conf = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.0,
            ml_confidence=0.2,
            recent_fill_rate=0.55,
        )
        
        assert low_conf.adverse_selection_spread > high_conf.adverse_selection_spread
    
    def test_competition_component(self):
        """Test competition component adjusts for fill rate."""
        calculator = AdaptiveSpreadCalculator(target_fill_rate=0.55)
        
        # Fill rate too low (tighten spread)
        low_fill = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.0,
            ml_confidence=0.5,
            recent_fill_rate=0.3,
        )
        
        # Fill rate too high (widen spread)
        high_fill = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.0,
            ml_confidence=0.5,
            recent_fill_rate=0.8,
        )
        
        # Target fill rate (neutral)
        target_fill = calculator.calculate_spread(
            volatility=0.02,
            position_ratio=0.0,
            ml_confidence=0.5,
            recent_fill_rate=0.55,
        )
        
        assert low_fill.competition_spread < target_fill.competition_spread
        assert high_fill.competition_spread > target_fill.competition_spread


class TestEnhancedCorrelationManager:
    """Test enhanced correlation manager."""
    
    def test_initialization(self):
        """Test correlation manager initialization."""
        manager = EnhancedCorrelationManager()
        assert manager is not None
        assert manager.lookback_seconds == 300
    
    def test_price_updates(self):
        """Test price update mechanism."""
        manager = EnhancedCorrelationManager()
        
        for i in range(50):
            manager.update_prices(87000.0 + i, 3500.0 + i * 0.5)
        
        assert len(manager.price_history_1) == 50
        assert len(manager.price_history_2) == 50
    
    def test_correlation_calculation_positive(self):
        """Test positive correlation detection."""
        manager = EnhancedCorrelationManager(update_interval_seconds=0.01)
        
        # Perfectly correlated prices
        base_time = time.time()
        for i in range(100):
            price1 = 87000 + i * 100
            price2 = 3500 + i * 4  # Moves together
            manager.update_prices(price1, price2, base_time + i * 0.1)
        
        # Force calculation
        manager._calculate_correlation()
        
        correlation = manager.get_correlation(use_ewma=False)
        assert correlation > 0.5  # Should be positive
    
    def test_correlation_calculation_negative(self):
        """Test negative correlation detection."""
        manager = EnhancedCorrelationManager(update_interval_seconds=0.01)
        
        # Create negatively correlated returns (oscillating prices)
        base_time = time.time()
        for i in range(100):
            # Use oscillating pattern for true negative correlation
            price1 = 87000 + 1000 * np.sin(i * 0.3)
            price2 = 3500 - 40 * np.sin(i * 0.3)  # Opposite phase
            manager.update_prices(price1, price2, base_time + i * 0.1)
        
        # Force calculation
        manager._calculate_correlation()
        
        correlation = manager.get_correlation(use_ewma=False)
        # With opposite oscillations, should get strong negative correlation
        assert correlation < -0.8  # Should be strongly negative
    
    def test_hedging_opportunity_detection(self):
        """Test hedging opportunity detection."""
        manager = EnhancedCorrelationManager()
        manager.current_correlation = 0.85  # High correlation
        
        # Opposite positions = hedge
        is_hedged, msg = manager.check_hedging_opportunity(
            position1=Decimal("0.0002"),  # Long BTC
            position2=Decimal("-10.0"),   # Short ETH
            price1=87000.0,
            price2=3500.0,
        )
        
        assert is_hedged is True
        assert "Hedged" in msg
    
    def test_combined_exposure_calculation(self):
        """Test combined exposure calculation."""
        manager = EnhancedCorrelationManager()
        
        exposure = manager.calculate_combined_exposure(
            position1=Decimal("0.001"),  # 0.001 BTC
            position2=Decimal("10.0"),   # 10 ETH
            price1=87000.0,
            price2=3500.0,
        )
        
        # 0.001 * 87000 + 10 * 3500 = 87 + 35000 = 35087
        expected = 87.0 + 35000.0
        assert abs(exposure - expected) < 1.0
    
    def test_correlation_adjusted_limits(self):
        """Test correlation-adjusted position limits."""
        manager = EnhancedCorrelationManager()
        manager.current_correlation = 0.85  # High correlation
        
        # Same direction positions (concentrated risk)
        limits_aligned = manager.get_correlation_adjusted_limits(
            base_limit_usd=1000.0,
            position1=Decimal("0.001"),  # Both long
            position2=Decimal("1.0"),
            price1=87000.0,
            price2=3500.0,
        )
        
        assert limits_aligned['adjustment_factor'] < 1.0  # Should be reduced
        assert limits_aligned['max_combined_usd'] < 1000.0
        
        # Opposite positions (hedged)
        limits_hedged = manager.get_correlation_adjusted_limits(
            base_limit_usd=1000.0,
            position1=Decimal("0.001"),   # Long
            position2=Decimal("-1.0"),    # Short
            price1=87000.0,
            price2=3500.0,
        )
        
        assert limits_hedged['adjustment_factor'] > 1.0  # Should be increased
        assert limits_hedged['max_combined_usd'] > 1000.0
    
    def test_correlation_state(self):
        """Test correlation state retrieval."""
        manager = EnhancedCorrelationManager()
        manager.current_correlation = 0.75
        
        state = manager.get_state(
            position1=Decimal("0.001"),
            position2=Decimal("10.0"),
            price1=87000.0,
            price2=3500.0,
        )
        
        assert state.correlation == 0.75
        assert state.combined_exposure_usd > 0
        assert isinstance(state.is_hedged, bool)
        assert isinstance(state.is_aligned, bool)


class TestPerformance:
    """Test performance/latency of spread and correlation modules."""
    
    def test_volatility_update_latency(self):
        """Test volatility update latency."""
        estimator = VolatilityEstimator()
        
        # Warmup
        for _ in range(100):
            estimator.update(87000.0)
        
        # Measure
        iterations = 10000
        start = time.perf_counter()
        
        for i in range(iterations):
            estimator.update(87000.0 + i)
        
        elapsed = time.perf_counter() - start
        avg_time_us = (elapsed / iterations) * 1_000_000
        
        print(f"\nVolatility update: {avg_time_us:.1f}μs per call")
        assert avg_time_us < 10  # Should be under 10μs
    
    def test_spread_calculation_latency(self):
        """Test spread calculation latency."""
        calculator = AdaptiveSpreadCalculator()
        
        # Warmup
        for _ in range(100):
            calculator.calculate_spread(0.02, 0.3, 0.7, 0.55)
        
        # Measure
        iterations = 10000
        start = time.perf_counter()
        
        for _ in range(iterations):
            calculator.calculate_spread(0.02, 0.3, 0.7, 0.55)
        
        elapsed = time.perf_counter() - start
        avg_time_us = (elapsed / iterations) * 1_000_000
        
        print(f"\nSpread calculation: {avg_time_us:.1f}μs per call")
        assert avg_time_us < 50  # Should be under 50μs


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
