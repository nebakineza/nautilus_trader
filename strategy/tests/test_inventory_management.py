"""Unit tests for inventory management module."""

import pytest
from decimal import Decimal
from strategy.inventory.skewing import (
    calculate_avellaneda_stoikov_skew,
    calculate_asymmetric_sizes,
    calculate_urgency_multiplier,
    calculate_optimal_inventory_target,
)
from strategy.inventory.risk_manager import InventoryRiskManager
from strategy.inventory.metrics import InventoryRiskMetrics


class TestUrgencyMultiplier:
    """Test urgency multiplier calculation."""
    
    def test_comfort_zone(self):
        """Urgency should be 1.0 in comfort zone (<30%)."""
        assert calculate_urgency_multiplier(0.0) == 1.0
        assert calculate_urgency_multiplier(0.1) == 1.0
        assert calculate_urgency_multiplier(0.29) == 1.0
    
    def test_warning_zone(self):
        """Urgency should increase linearly in warning zone (30-70%)."""
        # At 30%: should be 1.0
        assert calculate_urgency_multiplier(0.3) == pytest.approx(1.0, rel=0.01)
        
        # At 50%: should be 2.0 (middle of warning zone)
        assert calculate_urgency_multiplier(0.5) == pytest.approx(2.0, rel=0.01)
        
        # At 70%: should be 3.0
        assert calculate_urgency_multiplier(0.7) == pytest.approx(3.0, rel=0.01)
    
    def test_danger_zone(self):
        """Urgency should increase exponentially in danger zone (>70%)."""
        # At 85%: should be ~4.75
        urgency_85 = calculate_urgency_multiplier(0.85)
        assert 4.0 < urgency_85 < 6.0
        
        # At 100%: should be 10.0 (maximum)
        urgency_100 = calculate_urgency_multiplier(1.0)
        assert urgency_100 == pytest.approx(10.0, rel=0.01)


class TestAvellanedaStoikovSkew:
    """Test Avellaneda-Stoikov price skewing."""
    
    def test_neutral_position_no_skew(self):
        """Neutral position should have no skew."""
        skew = calculate_avellaneda_stoikov_skew(
            position=Decimal(0),
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
        )
        assert skew == 0.0
    
    def test_long_position_negative_skew(self):
        """Long position should have negative skew (lower prices)."""
        skew = calculate_avellaneda_stoikov_skew(
            position=Decimal("0.0003"),  # 60% of max
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
            risk_aversion=0.5,
            volatility=0.02,
            time_horizon=120.0,
        )
        assert skew < 0  # Should be negative
        # Skew will be large due to urgency multiplier at 60% position
        assert -500 < skew < -100  # Reasonable range with urgency
    
    def test_short_position_positive_skew(self):
        """Short position should have positive skew (higher prices)."""
        skew = calculate_avellaneda_stoikov_skew(
            position=Decimal("-0.0003"),  # -60% of max
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
        )
        assert skew > 0  # Should be positive
    
    def test_skew_scales_with_position(self):
        """Larger position should have larger skew."""
        skew_small = calculate_avellaneda_stoikov_skew(
            position=Decimal("0.0001"),
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
        )
        
        skew_large = calculate_avellaneda_stoikov_skew(
            position=Decimal("0.0004"),
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
        )
        
        assert abs(skew_large) > abs(skew_small)


class TestAsymmetricSizes:
    """Test asymmetric size calculation."""
    
    def test_neutral_position_symmetric_sizes(self):
        """Neutral position should have equal bid/ask sizes."""
        bid, ask = calculate_asymmetric_sizes(
            position=Decimal(0),
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
            base_size=Decimal("0.0001"),
        )
        assert bid == ask == Decimal("0.0001")
    
    def test_long_position_smaller_bid_larger_ask(self):
        """Long position: smaller bid, larger ask."""
        bid, ask = calculate_asymmetric_sizes(
            position=Decimal("0.0003"),  # Long
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
            base_size=Decimal("0.0001"),
        )
        assert bid < Decimal("0.0001")  # Reduced bid
        assert ask > Decimal("0.0001")  # Increased ask
    
    def test_short_position_larger_bid_smaller_ask(self):
        """Short position: larger bid, smaller ask."""
        bid, ask = calculate_asymmetric_sizes(
            position=Decimal("-0.0003"),  # Short
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
            base_size=Decimal("0.0001"),
        )
        assert bid > Decimal("0.0001")  # Increased bid
        assert ask < Decimal("0.0001")  # Reduced ask
    
    def test_sizes_respect_min_max_bounds(self):
        """Sizes should respect min/max multiplier bounds."""
        bid, ask = calculate_asymmetric_sizes(
            position=Decimal("0.0005"),  # At max (extreme)
            max_position=Decimal("0.0005"),
            optimal_target=Decimal(0),
            base_size=Decimal("0.0001"),
            min_multiplier=0.3,
            max_multiplier=2.0,
        )
        # Bid should be at minimum (0.3x)
        assert bid >= Decimal("0.0001") * Decimal("0.3")
        # Ask should be at maximum (2.0x)
        assert ask <= Decimal("0.0001") * Decimal("2.0")


class TestOptimalInventoryTarget:
    """Test optimal inventory target calculation."""
    
    def test_low_confidence_neutral_target(self):
        """Low ML confidence should result in neutral target."""
        target = calculate_optimal_inventory_target(
            obi_ema=0.5,  # Strong signal
            ml_confidence=0.3,  # Low confidence
            max_position=Decimal("0.0005"),
        )
        assert target == Decimal(0)
    
    def test_high_confidence_bullish_allows_long_bias(self):
        """High confidence + bullish OBI should allow long bias."""
        target = calculate_optimal_inventory_target(
            obi_ema=0.15,  # Bullish
            ml_confidence=0.7,  # High confidence
            max_position=Decimal("0.0005"),
        )
        assert target > Decimal(0)  # Should be positive
        assert target <= Decimal("0.0005") * Decimal("0.2")  # Max 20%
    
    def test_high_confidence_bearish_allows_short_bias(self):
        """High confidence + bearish OBI should allow short bias."""
        target = calculate_optimal_inventory_target(
            obi_ema=-0.15,  # Bearish
            ml_confidence=0.7,  # High confidence
            max_position=Decimal("0.0005"),
        )
        assert target < Decimal(0)  # Should be negative


class TestInventoryRiskManager:
    """Test InventoryRiskManager class."""
    
    def test_initialization(self):
        """Test manager initialization."""
        manager = InventoryRiskManager(
            max_position=Decimal("0.0005"),
            risk_aversion=0.5,
        )
        assert manager.max_position == Decimal("0.0005")
        assert manager.risk_aversion == 0.5
    
    def test_calculate_skew(self):
        """Test skew calculation through manager."""
        manager = InventoryRiskManager(max_position=Decimal("0.0005"))
        
        skew = manager.calculate_skew(
            position=Decimal("0.0003"),
            optimal_target=Decimal(0),
            mid_price=87000.0,
        )
        
        assert isinstance(skew, float)
        assert skew < 0  # Long position should have negative skew
    
    def test_calculate_sizes(self):
        """Test size calculation through manager."""
        manager = InventoryRiskManager(max_position=Decimal("0.0005"))
        
        bid, ask = manager.calculate_sizes(
            position=Decimal("0.0003"),
            optimal_target=Decimal(0),
            base_size=Decimal("0.0001"),
        )
        
        assert isinstance(bid, Decimal)
        assert isinstance(ask, Decimal)
        assert bid < Decimal("0.0001")  # Long position reduces bid
        assert ask > Decimal("0.0001")  # Long position increases ask
    
    def test_rebalancing_trigger_danger_zone(self):
        """Test rebalancing triggers in danger zone."""
        manager = InventoryRiskManager(max_position=Decimal("0.0005"))
        
        # Danger zone starts at 90%, so use 0.00046 (92%)
        needs_rebalance, reason = manager.check_rebalancing_needed(
            position=Decimal("0.00046"),  # 92% of max
            optimal_target=Decimal(0),
            time_since_fill=10.0,
        )
        
        assert needs_rebalance is True
        assert "Critical" in reason
    
    def test_no_rebalancing_in_comfort_zone(self):
        """Test no rebalancing in comfort zone."""
        manager = InventoryRiskManager(max_position=Decimal("0.0005"))
        
        needs_rebalance, reason = manager.check_rebalancing_needed(
            position=Decimal("0.00010"),  # 20% of max
            optimal_target=Decimal(0),
            time_since_fill=10.0,
        )
        
        assert needs_rebalance is False
    
    def test_calculate_metrics(self):
        """Test comprehensive metrics calculation."""
        manager = InventoryRiskManager(max_position=Decimal("0.0005"))
        
        metrics = manager.calculate_metrics(
            position=Decimal("0.0003"),
            optimal_target=Decimal(0),
            mid_price=87000.0,
            unrealized_pnl=Decimal("10.5"),
            time_since_fill=30.0,
        )
        
        assert isinstance(metrics, InventoryRiskMetrics)
        assert metrics.current_position == Decimal("0.0003")
        assert metrics.position_ratio > 0
        assert metrics.current_skew_bps < 0  # Long position
        assert metrics.urgency_multiplier >= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
