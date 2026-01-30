from decimal import Decimal
import numpy as np

class OnlineKalmanFilter:
    """
    An online Kalman Filter for estimating the hedge ratio (beta) and intercept (alpha)
    between two cointegrated assets.
    
    Model:
        y_t = alpha_t + beta_t * x_t + epsilon_t
        
    State Vector (theta): [alpha, beta]
    Observation (y): Price of Asset A
    Regressor (x): Price of Asset B
    """
    
    def __init__(self, delta: float = 1e-4, vt: float = 1e-3):
        """
        Args:
            delta (float): System noise covariance scale (random walk variance).
            vt (float): Measurement noise covariance.
        """
        # State vector [alpha, beta]
        self.state = np.zeros(2)
        
        # State covariance matrix (initialized with some uncertainty)
        self.P = np.eye(2) * 1.0
        
        # System noise covariance (Q)
        # We assume alpha and beta follow a random walk
        self.delta = delta
        self.Q = np.eye(2) * self.delta
        
        # Measurement noise covariance (R or Vt)
        self.R = vt
        
        self.is_initialized = False

    def update(self, price_a: float, price_b: float):
        """
        Update the filter with new observations.
        
        Args:
            price_a (float): Dependent variable (y)
            price_b (float): Independent variable (x)
        """
        y = price_a
        x = price_b
        
        # Observation matrix H = [1, x]
        H = np.array([1.0, x])
        
        if not self.is_initialized:
            # Initialize state with first observation? 
            # Or just wait for convergence. For pairs, starting at [0,0] is dangerous.
            # We'll just let it converge over a few bars.
            self.is_initialized = True

        # 1. Prediction Step
        # State prediction: theta_k|k-1 = theta_k-1 (Random Walk)
        state_pred = self.state
        
        # Covariance prediction: P_k|k-1 = P_k-1 + Q
        P_pred = self.P + self.Q
        
        # 2. Update Step
        # Measurement residual: y_k - H * theta_k|k-1
        y_pred = np.dot(H, state_pred)
        error = y - y_pred
        
        # System uncertainty (scalar)
        # S = H * P_pred * H.T + R
        S = np.dot(np.dot(H, P_pred), H.T) + self.R
        
        # Kalman Gain
        # K = P_pred * H.T * S^-1
        K = np.dot(P_pred, H.T) / S
        
        # Update state: theta_k = theta_k|k-1 + K * error
        self.state = state_pred + K * error
        
        # Update covariance: P_k = (I - K * H) * P_pred
        # Using Joseph form for numerical stability: P = (I-KH)P(I-KH)' + KRK'
        # But for HFT/Live simple form is usually mostly fine.
        I_KH = np.eye(2) - np.outer(K, H)
        self.P = np.dot(I_KH, P_pred)
        
        return self.state, error, np.sqrt(S)

    @property
    def alpha(self) -> float:
        return self.state[0]
        
    @property
    def beta(self) -> float:
        return self.state[1]
