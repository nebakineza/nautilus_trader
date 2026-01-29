"""Unit tests for ML module (feature extraction and online model)."""

import time
from collections import deque
from decimal import Decimal

import numpy as np
import pytest

from strategy.ml.feature_engineering import FeatureExtractor
from strategy.ml.online_model import OnlineOBIPredictor


class TestFeatureExtractor:
    """Test feature extraction."""
    
    def test_initialization(self):
        """Test feature extractor initialization."""
        extractor = FeatureExtractor()
        assert extractor is not None
    
    def test_feature_count(self):
        """Test that we extract exactly 10 features."""
        extractor = FeatureExtractor()
        
        features = extractor.extract_features(
            obi_ema=0.1,
            obi_history=deque([0.05, 0.08, 0.10], maxlen=15),
            book_bids=[],
            book_asks=[],
            best_bid_price=87000.0,
            best_ask_price=87010.0,
            net_position=Decimal("0.0001"),
            max_position=Decimal("0.0005"),
            last_fill_time=time.time() - 10,
            quote_count=100,
            fill_count=40,
        )
        
        assert features.shape == (10,)
        assert features.dtype == np.float32
    
    def test_feature_ranges(self):
        """Test that features are properly normalized."""
        extractor = FeatureExtractor()
        
        features = extractor.extract_features(
            obi_ema=0.5,
            obi_history=deque([0.3, 0.4, 0.5, 0.6, 0.7], maxlen=15),
            book_bids=[],
            book_asks=[],
            best_bid_price=87000.0,
            best_ask_price=87010.0,
            net_position=Decimal("0.0002"),
            max_position=Decimal("0.0005"),
            last_fill_time=time.time() - 30,
            quote_count=100,
            fill_count=45,
        )
        
        # All features should be in reasonable ranges
        assert np.all(features >= -1.1), f"Features too low: {features}"
        assert np.all(features <= 1.1), f"Features too high: {features}"
        
        # Most features should be in [0, 1]
        positive_features = features[[2, 3, 5, 6, 7]]  # Non-symmetric features
        assert np.all(positive_features >= 0), f"Positive features negative: {positive_features}"
        assert np.all(positive_features <= 1), f"Positive features > 1: {positive_features}"
    
    def test_obi_feature(self):
        """Test OBI value feature extraction."""
        extractor = FeatureExtractor()
        
        features = extractor.extract_features(
            obi_ema=0.25,
            obi_history=deque([0.2, 0.25], maxlen=15),
            book_bids=[],
            book_asks=[],
            best_bid_price=87000.0,
            best_ask_price=87010.0,
            net_position=Decimal(0),
            max_position=Decimal("0.0005"),
            last_fill_time=time.time(),
            quote_count=10,
            fill_count=4,
        )
        
        assert features[0] == pytest.approx(0.25, abs=0.01)
    
    def test_position_feature(self):
        """Test position ratio feature."""
        extractor = FeatureExtractor()
        
        features = extractor.extract_features(
            obi_ema=0.0,
            obi_history=deque([0.0], maxlen=15),
            book_bids=[],
            book_asks=[],
            best_bid_price=87000.0,
            best_ask_price=87010.0,
            net_position=Decimal("0.0003"),  # 60% of max
            max_position=Decimal("0.0005"),
            last_fill_time=time.time(),
            quote_count=10,
            fill_count=4,
        )
        
        # Position ratio should be 0.6
        assert features[8] == pytest.approx(0.6, abs=0.01)
    
    def test_feature_names(self):
        """Test feature name retrieval."""
        extractor = FeatureExtractor()
        names = extractor.feature_names()
        
        assert len(names) == 10
        assert 'obi_value' in names
        assert 'position_ratio' in names


class TestOnlineOBIPredictor:
    """Test online learning model."""
    
    def test_initialization(self):
        """Test model initialization."""
        model = OnlineOBIPredictor()
        assert model.n_features == 10
        assert model.weights.shape == (10,)
        assert model.prediction_count == 0
    
    def test_prediction_range(self):
        """Test that predictions are in [0, 1]."""
        model = OnlineOBIPredictor()
        
        # Test with various feature sets
        for _ in range(100):
            features = np.random.randn(10)
            confidence = model.predict(features)
            
            assert 0.0 <= confidence <= 1.0, f"Confidence out of range: {confidence}"
    
    def test_prediction_consistency(self):
        """Test that same features give same prediction."""
        model = OnlineOBIPredictor()
        
        features = np.random.randn(10)
        pred1 = model.predict(features)
        pred2 = model.predict(features)
        
        assert pred1 == pred2
    
    def test_online_learning(self):
        """Test that model learns from updates."""
        model = OnlineOBIPredictor(learning_rate=0.1)
        
        # Create simple pattern: positive OBI → good outcome
        for i in range(100):
            features = np.zeros(10)
            features[0] = 0.5 if i % 2 == 0 else -0.5  # Positive or negative OBI
            outcome = 1.0 if features[0] > 0 else 0.0  # Good if positive, bad if negative
            
            model.update(features, outcome)
        
        # After learning, positive OBI should predict higher confidence
        pos_features = np.zeros(10)
        pos_features[0] = 0.5
        pos_confidence = model.predict(pos_features)
        
        neg_features = np.zeros(10)
        neg_features[0] = -0.5
        neg_confidence = model.predict(neg_features)
        
        assert pos_confidence > neg_confidence, \
            f"Model didn't learn: pos={pos_confidence}, neg={neg_confidence}"
    
    def test_should_trade_threshold(self):
        """Test confidence threshold for trading."""
        model = OnlineOBIPredictor(confidence_threshold=0.4)
        
        assert model.should_trade(0.5) is True
        assert model.should_trade(0.3) is False
        assert model.should_trade(0.4) is True  # Equal to threshold
    
    def test_accuracy_tracking(self):
        """Test accuracy calculation."""
        model = OnlineOBIPredictor()
        
        # Perfect predictions
        features = np.ones(10)
        for _ in range(10):
            model.update(features, 1.0)
        
        accuracy = model.get_accuracy()
        assert accuracy >= 0.5  # Should be better than random
    
    def test_save_and_load_weights(self, tmp_path):
        """Test weight persistence."""
        model1 = OnlineOBIPredictor()
        
        # Make some predictions to change weights
        for _ in range(10):
            features = np.random.randn(10)
            model1.predict(features)
            model1.update(features, np.random.random())
        
        # Save weights
        filepath = tmp_path / "test_weights.npz"
        model1.save_weights(str(filepath))
        
        # Load into new model
        model2 = OnlineOBIPredictor()
        assert model2.load_weights(str(filepath))
        
        # Weights should match
        assert np.allclose(model1.weights, model2.weights)
        assert model1.bias == pytest.approx(model2.bias)
        assert model1.prediction_count == model2.prediction_count
    
    def test_reset(self):
        """Test model reset functionality."""
        model = OnlineOBIPredictor()
        
        # Make predictions
        for _ in range(10):
            features = np.random.randn(10)
            model.predict(features)
        
        assert model.prediction_count == 10
        
        # Reset
        model.reset()
        
        assert model.prediction_count == 0
        assert model.update_count == 0
    
    def test_get_stats(self):
        """Test statistics retrieval."""
        model = OnlineOBIPredictor()
        
        # Make some predictions
        for i in range(5):
            features = np.random.randn(10)
            model.predict(features)
            model.update(features, 1.0 if i % 2 == 0 else 0.0)
        
        stats = model.get_stats()
        
        assert 'predictions' in stats
        assert 'updates' in stats
        assert 'accuracy' in stats
        assert stats['predictions'] == 10  # predict() called in update() too
        assert stats['updates'] == 5


class TestMLLatency:
    """Test ML performance/latency."""
    
    def test_feature_extraction_latency(self):
        """Test feature extraction is fast enough (<500μs)."""
        extractor = FeatureExtractor()
        
        # Warmup
        for _ in range(10):
            extractor.extract_features(
                obi_ema=0.1,
                obi_history=deque([0.1] * 10, maxlen=15),
                book_bids=[],
                book_asks=[],
                best_bid_price=87000.0,
                best_ask_price=87010.0,
                net_position=Decimal(0),
                max_position=Decimal("0.0005"),
                last_fill_time=time.time(),
                quote_count=100,
                fill_count=40,
            )
        
        # Measure
        iterations = 1000
        start = time.perf_counter()
        
        for _ in range(iterations):
            extractor.extract_features(
                obi_ema=0.1,
                obi_history=deque([0.1] * 10, maxlen=15),
                book_bids=[],
                book_asks=[],
                best_bid_price=87000.0,
                best_ask_price=87010.0,
                net_position=Decimal(0),
                max_position=Decimal("0.0005"),
                last_fill_time=time.time(),
                quote_count=100,
                fill_count=40,
            )
        
        elapsed = time.perf_counter() - start
        avg_time_us = (elapsed / iterations) * 1_000_000
        
        print(f"\nFeature extraction: {avg_time_us:.1f}μs per call")
        assert avg_time_us < 500, f"Too slow: {avg_time_us}μs"
    
    def test_model_prediction_latency(self):
        """Test model prediction is fast enough (<100μs)."""
        model = OnlineOBIPredictor()
        features = np.random.randn(10).astype(np.float32)
        
        # Warmup
        for _ in range(100):
            model.predict(features)
        
        # Measure
        iterations = 10000
        start = time.perf_counter()
        
        for _ in range(iterations):
            model.predict(features)
        
        elapsed = time.perf_counter() - start
        avg_time_us = (elapsed / iterations) * 1_000_000
        
        print(f"\nModel prediction: {avg_time_us:.1f}μs per call")
        assert avg_time_us < 100, f"Too slow: {avg_time_us}μs"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
