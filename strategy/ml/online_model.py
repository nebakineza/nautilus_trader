"""
Online learning model for OBI signal quality prediction.

Uses Exponentially Weighted Recursive Least Squares (EWRLS)
for real-time adaptive learning with minimal computational overhead.
"""

import os
from pathlib import Path
from typing import Optional

import numpy as np


class OnlineOBIPredictor:
    """
    Lightweight online learning model for OBI signal quality.
    
    Architecture: Linear model with sigmoid activation
    - 10 input features
    - 1 output (confidence/probability)
    - Online learning via exponentially weighted updates
    
    Inference time: <100μs (target)
    """
    
    def __init__(
        self,
        n_features: int = 10,
        learning_rate: float = 0.01,
        decay_factor: float = 0.95,
        confidence_threshold: float = 0.3,
    ):
        """
        Initialize online predictor.
        
        Args:
            n_features: Number of input features (default 10)
            learning_rate: Learning rate for weight updates
            decay_factor: Exponential decay for past observations
            confidence_threshold: Minimum confidence to trade (0-1)
        """
        self.n_features = n_features
        self.learning_rate = learning_rate
        self.decay_factor = decay_factor
        self.confidence_threshold = confidence_threshold
        
        # Model weights (initialized to small random values)
        self.weights = np.random.randn(n_features) * 0.01
        self.bias = 0.0
        
        # Tracking
        self.prediction_count = 0
        self.update_count = 0
        self.correct_predictions = 0
    
    def predict(self, features: np.ndarray) -> float:
        """
        Predict OBI signal quality/confidence.
        
        Args:
            features: Array of shape (10,) with normalized features
        
        Returns:
            Confidence score in [0, 1]
            - 0.0: Very low confidence (don't trade)
            - 0.5: Neutral
            - 1.0: Very high confidence
        """
        # Linear combination
        logit = np.dot(self.weights, features) + self.bias
        
        # Sigmoid activation
        confidence = 1.0 / (1.0 + np.exp(-logit))
        
        self.prediction_count += 1
        
        return float(confidence)
    
    def update(self, features: np.ndarray, outcome: float) -> None:
        """
        Update model weights based on observed outcome.
        
        Args:
            features: Features used for prediction
            outcome: Actual outcome (0.0 = bad, 1.0 = good)
                     - Good: Fill was profitable or neutral
                     - Bad: Fill led to adverse selection
        """
        # Get current prediction
        prediction = self.predict(features)
        
        # Calculate error
        error = outcome - prediction
        
        # Gradient of sigmoid: prediction * (1 - prediction)
        gradient = prediction * (1 - prediction)
        
        # Update weights (gradient descent)
        weight_update = self.learning_rate * error * gradient * features
        bias_update = self.learning_rate * error * gradient
        
        # Apply exponential decay to old weights (regularization)
        self.weights = self.decay_factor * self.weights + weight_update
        self.bias = self.decay_factor * self.bias + bias_update
        
        self.update_count += 1
        
        # Track accuracy (outcome > 0.5 and prediction > 0.5, or both < 0.5)
        if (outcome > 0.5 and prediction > 0.5) or (outcome < 0.5 and prediction < 0.5):
            self.correct_predictions += 1
    
    def should_trade(self, confidence: float) -> bool:
        """
        Determine if confidence is high enough to trade.
        
        Args:
            confidence: Predicted confidence from predict()
        
        Returns:
            True if should trade, False otherwise
        """
        return confidence >= self.confidence_threshold
    
    def get_accuracy(self) -> float:
        """Get current prediction accuracy."""
        if self.update_count == 0:
            return 0.5  # No data yet
        return self.correct_predictions / self.update_count
    
    def save_weights(self, filepath: str) -> None:
        """
        Save model weights to file.
        
        Args:
            filepath: Path to save weights (e.g., 'model_weights/btc_weights.npz')
        """
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        np.savez(
            filepath,
            weights=self.weights,
            bias=self.bias,
            prediction_count=self.prediction_count,
            update_count=self.update_count,
            correct_predictions=self.correct_predictions,
        )
    
    def load_weights(self, filepath: str) -> bool:
        """
        Load model weights from file.
        
        Args:
            filepath: Path to load weights from
        
        Returns:
            True if loaded successfully, False otherwise
        """
        if not os.path.exists(filepath):
            return False
        
        try:
            data = np.load(filepath)
            self.weights = data['weights']
            self.bias = float(data['bias'])
            self.prediction_count = int(data['prediction_count'])
            self.update_count = int(data['update_count'])
            self.correct_predictions = int(data['correct_predictions'])
            return True
        except Exception:
            return False
    
    def reset(self) -> None:
        """Reset model to initial state."""
        self.weights = np.random.randn(self.n_features) * 0.01
        self.bias = 0.0
        self.prediction_count = 0
        self.update_count = 0
        self.correct_predictions = 0
    
    def get_stats(self) -> dict:
        """Get model statistics."""
        return {
            'predictions': self.prediction_count,
            'updates': self.update_count,
            'accuracy': self.get_accuracy(),
            'weights_norm': float(np.linalg.norm(self.weights)),
            'bias': float(self.bias),
        }
