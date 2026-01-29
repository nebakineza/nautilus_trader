"""Integration tests for v002 multi-pair strategy."""

import time
from decimal import Decimal

import pytest

from strategy.hft_obi_bybit_spot_mm_lowcap_multipair_v002 import (
    MultiPairMMConfig,
    InstrumentConfig,
)


class TestStrategyIntegration:
    """Test full strategy integration."""
    
    def test_config_initialization(self):
        """Test strategy configuration."""
        config = MultiPairMMConfig()
        
        assert config is not None
        instrument_configs = config.get_instrument_configs()
        assert len(instrument_configs) == 2
        assert config.total_capital_usd == 500.0
        assert config.risk_aversion == 0.5
    
    def test_instrument_configs(self):
        """Test instrument configuration parsing."""
        config = MultiPairMMConfig()
        instrument_configs = config.get_instrument_configs()
        
        # Check BTC config
        btc_cfg = instrument_configs[0]
        assert btc_cfg.instrument_id == 'BTCUSDT-SPOT.BYBIT'
        assert btc_cfg.base_qty == Decimal('0.0001')
        assert btc_cfg.max_position_qty == Decimal('0.0003')
        assert btc_cfg.weight == 0.6
        
        # Check ETH config
        eth_cfg = instrument_configs[1]
        assert eth_cfg.instrument_id == 'ETHUSDT-SPOT.BYBIT'
        assert eth_cfg.base_qty == Decimal('0.005')
        assert eth_cfg.max_position_qty == Decimal('0.015')
        assert eth_cfg.weight == 0.4
    
    def test_all_modules_import(self):
        """Test that all strategy modules can be imported."""
        # Inventory management
        from strategy.inventory.risk_manager import InventoryRiskManager
        from strategy.inventory.metrics import InventoryRiskMetrics
        from strategy.inventory.skewing import calculate_avellaneda_stoikov_skew
        
        # ML modules
        from strategy.ml.feature_engineering import FeatureExtractor
        from strategy.ml.online_model import OnlineOBIPredictor
        
        # Spread modules
        from strategy.spread.volatility_estimator import VolatilityEstimator
        from strategy.spread.adaptive_spread import AdaptiveSpreadCalculator
        
        # Correlation
        from strategy.correlation.correlation_manager import EnhancedCorrelationManager
        
        # All imports successful
        assert True
    
    def test_inventory_ml_integration(self):
        """Test inventory manager with ML predictor integration."""
        from strategy.inventory.risk_manager import InventoryRiskManager
        from strategy.ml.online_model import OnlineOBIPredictor
        
        inventory_mgr = InventoryRiskManager(
            max_position=Decimal("0.0005"),
            risk_aversion=0.5,
        )
        
        ml_predictor = OnlineOBIPredictor()
        ml_confidence = ml_predictor.predict([0.1] * 10)
        
        # Calculate target with ML confidence
        target = inventory_mgr.calculate_optimal_target(
            obi_ema=0.2,
            ml_confidence=ml_confidence,
        )
        
        assert isinstance(target, Decimal)
        assert -Decimal("0.0005") <= target <= Decimal("0.0005")
    
    def test_spread_volatility_integration(self):
        """Test spread calculator with volatility estimator."""
        from strategy.spread.volatility_estimator import VolatilityEstimator
        from strategy.spread.adaptive_spread import AdaptiveSpreadCalculator
        
        vol_estimator = VolatilityEstimator()
        spread_calc = AdaptiveSpreadCalculator()
        
        # Update volatility
        for i in range(50):
            vol_estimator.update(87000.0 + i * 10)
        
        vol = vol_estimator.get_ewma_volatility()
        
        # Calculate spread
        components = spread_calc.calculate_spread(
            volatility=vol,
            position_ratio=0.3,
            ml_confidence=0.7,
            recent_fill_rate=0.55,
        )
        
        assert 1.0 <= components.total_spread <= 10.0
        assert components.volatility_spread >= 0
    
    def test_correlation_multi_pair_integration(self):
        """Test correlation manager with multiple instruments."""
        from strategy.correlation.correlation_manager import EnhancedCorrelationManager
        
        corr_mgr = EnhancedCorrelationManager()
        
        # Update with correlated prices
        for i in range(100):
            btc_price = 87000 + i * 100
            eth_price = 3500 + i * 4
            corr_mgr.update_prices(btc_price, eth_price)
        
        corr_mgr._calculate_correlation()
        correlation = corr_mgr.get_correlation()
        
        # Should detect positive correlation
        assert -1.0 <= correlation <= 1.0
        
        # Test limit adjustment
        limits = corr_mgr.get_correlation_adjusted_limits(
            base_limit_usd=1000.0,
            position1=Decimal("0.001"),
            position2=Decimal("10.0"),
            price1=87000.0,
            price2=3500.0,
        )
        
        assert 'max_combined_usd' in limits
        assert limits['max_combined_usd'] > 0
    
    def test_end_to_end_quote_pipeline(self):
        """Test the full quote generation pipeline components."""
        from strategy.inventory.risk_manager import InventoryRiskManager
        from strategy.ml.feature_engineering import FeatureExtractor
        from strategy.ml.online_model import OnlineOBIPredictor
        from strategy.spread.volatility_estimator import VolatilityEstimator
        from strategy.spread.adaptive_spread import AdaptiveSpreadCalculator
        from collections import deque
        
        # Initialize all components
        inventory_mgr = InventoryRiskManager(Decimal("0.0005"))
        feature_extractor = FeatureExtractor()
        ml_predictor = OnlineOBIPredictor()
        vol_estimator = VolatilityEstimator()
        spread_calc = AdaptiveSpreadCalculator()
        
        # Simulate state
        obi_history = deque([0.1, 0.15, 0.12], maxlen=15)
        obi_ema = 0.12
        position = Decimal("0.0002")
        mid_price = 87000.0
        
        # Step 1: Extract features
        features = feature_extractor.extract_features(
            obi_ema=obi_ema,
            obi_history=obi_history,
            book_bids=[],
            book_asks=[],
            best_bid_price=86995.0,
            best_ask_price=87005.0,
            net_position=position,
            max_position=Decimal("0.0005"),
            last_fill_time=time.time() - 30,
            quote_count=100,
            fill_count=45,
        )
        
        assert len(features) == 10
        
        # Step 2: ML prediction
        ml_confidence = ml_predictor.predict(features)
        assert 0.0 <= ml_confidence <= 1.0
        
        # Step 3: Calculate optimal inventory target
        optimal_target = inventory_mgr.calculate_optimal_target(
            obi_ema=obi_ema,
            ml_confidence=ml_confidence,
        )
        
        # Step 4: Update volatility
        vol_estimator.update(mid_price)
        volatility = vol_estimator.get_ewma_volatility()
        
        # Step 5: Calculate adaptive spread
        position_ratio = float(position) / 0.0005
        components = spread_calc.calculate_spread(
            volatility=volatility,
            position_ratio=position_ratio,
            ml_confidence=ml_confidence,
            recent_fill_rate=0.45,
        )
        
        assert components.total_spread >= 1.0
        
        # Step 6: Calculate inventory skew
        skew = inventory_mgr.calculate_skew(
            position=position,
            optimal_target=optimal_target,
            mid_price=mid_price,
        )
        
        # Step 7: Calculate sizes
        bid_size, ask_size = inventory_mgr.calculate_sizes(
            position=position,
            optimal_target=optimal_target,
            base_size=Decimal("0.0001"),
        )
        
        assert bid_size > 0
        assert ask_size > 0
        
        # Pipeline complete!
        print(f"\n✓ Quote Pipeline Test:")
        print(f"  Features: {features[:3]}...")
        print(f"  ML Confidence: {ml_confidence:.2f}")
        print(f"  Optimal Target: {optimal_target}")
        print(f"  Volatility: {volatility:.3f}")
        print(f"  Spread: {components.total_spread:.1f} bps")
        print(f"  Skew: {skew:.1f} bps")
        print(f"  Sizes: bid={bid_size}, ask={ask_size}")
    
    def test_safety_limits(self):
        """Test that safety limits are properly configured."""
        config = MultiPairMMConfig()
        instrument_configs = config.get_instrument_configs()
        
        # Emergency stop should be negative
        assert config.emergency_liquidation_loss_usd < 0
        
        # Exposure should be reasonable
        assert 0.05 <= config.max_total_exposure_pct <= 0.25
        
        # Correlation exposure should be limited
        assert config.max_correlated_exposure_pct < 1.0
        
        # Position limits should be sensible
        approx_prices = {'BTCUSDT': 87000, 'ETHUSDT': 3500}
        for inst_cfg in instrument_configs:
            # Get approximate price
            symbol_base = inst_cfg.instrument_id.split('-')[0][:3]  # BTC or ETH
            approx_price = approx_prices.get(symbol_base + 'USDT', 50000)
            
            max_pos_value = float(inst_cfg.max_position_qty) * approx_price
            # For low capital strategies, positions should be reasonable
            assert max_pos_value < config.total_capital_usd * 0.15  # < 15% per instrument at max


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
