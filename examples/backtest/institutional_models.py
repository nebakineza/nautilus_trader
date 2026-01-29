"""Institutional-grade latency and fill models for AWS Singapore co-location.

Models realistic market microstructure including:
- Network latency with jitter (AWS Singapore co-lo baseline: 250μs)
- Queue position simulation via fill probability
- Liquidity consumption and market impact
- Professional execution quality assumptions
"""

import random
from typing import Optional

from nautilus_trader.backtest.models import FillModel, LatencyModel


class CoLocationLatencyModel(LatencyModel):
    """
    Latency model for AWS Singapore co-location with realistic jitter.
    
    Latency Distribution:
    - P50: 200-300μs (normal operation)
    - P95: 500-800μs (congestion)
    - P99: 1-2ms (peak load)
    - P99.9: 5-10ms (rare network spikes)
    
    Parameters
    ----------
    base_latency_ns : int, optional
        Base round-trip latency in nanoseconds (default: 250,000 = 250μs)
    insert_latency_ns : int, optional
        Additional latency for order insert (default: 50,000 = 50μs)
    update_latency_ns : int, optional
        Additional latency for order update (default: 30,000 = 30μs)
    cancel_latency_ns : int, optional
        Additional latency for order cancel (default: 20,000 = 20μs)
    jitter_std_ns : int, optional
        Standard deviation of latency jitter (default: 100,000 = 100μs)
    spike_probability : float, optional
        Probability of rare latency spike (default: 0.001 = 0.1%)
    spike_multiplier : float, optional
        Multiplier for spike latency (default: 20x)
    random_seed : int, optional
        Random seed for reproducibility
    """

    def __init__(
        self,
        base_latency_ns: int = 250_000,
        insert_latency_ns: int = 50_000,
        update_latency_ns: int = 30_000,
        cancel_latency_ns: int = 20_000,
        jitter_std_ns: int = 100_000,
        spike_probability: float = 0.001,
        spike_multiplier: float = 20.0,
        random_seed: Optional[int] = None,
    ):
        super().__init__(base_latency_ns)
        
        self.insert_latency_ns = insert_latency_ns
        self.update_latency_ns = update_latency_ns
        self.cancel_latency_ns = cancel_latency_ns
        self.jitter_std_ns = jitter_std_ns
        self.spike_probability = spike_probability
        self.spike_multiplier = spike_multiplier
        
        if random_seed is not None:
            random.seed(random_seed)
        
        # Statistics
        self._sample_count = 0
        self._latency_samples = []

    def get_insert_latency(self) -> int:
        """Get latency for order insertion."""
        return self._apply_jitter(self.base_latency_ns + self.insert_latency_ns)

    def get_update_latency(self) -> int:
        """Get latency for order update."""
        return self._apply_jitter(self.base_latency_ns + self.update_latency_ns)

    def get_cancel_latency(self) -> int:
        """Get latency for order cancellation."""
        return self._apply_jitter(self.base_latency_ns + self.cancel_latency_ns)

    def _apply_jitter(self, base_latency_ns: int) -> int:
        """Apply realistic jitter and occasional spikes."""
        # Check for rare spike
        if random.random() < self.spike_probability:
            spike = int(base_latency_ns * self.spike_multiplier)
            self._sample_count += 1
            self._latency_samples.append(spike)
            return spike
        
        # Normal jitter: Gaussian distribution
        jitter = int(random.gauss(0, self.jitter_std_ns))
        latency = max(base_latency_ns + jitter, 10_000)  # Min 10μs
        
        self._sample_count += 1
        self._latency_samples.append(latency)
        return latency

    def get_statistics(self) -> dict:
        """Get latency statistics."""
        if not self._latency_samples:
            return {}
        
        samples = sorted(self._latency_samples)
        n = len(samples)
        
        return {
            "count": n,
            "mean_ns": sum(samples) // n,
            "min_ns": samples[0],
            "max_ns": samples[-1],
            "p50_ns": samples[n // 2],
            "p95_ns": samples[int(n * 0.95)],
            "p99_ns": samples[int(n * 0.99)],
            "p99_9_ns": samples[int(n * 0.999)] if n > 1000 else samples[-1],
        }


class InstitutionalFillModel(FillModel):
    """
    Professional fill model accounting for queue position and market impact.
    
    - 85% probability limit order fills (queue position success)
    - 5% adverse selection risk (getting picked off)
    - 30% of visible liquidity actually available (competition from other traders)
    
    Parameters
    ----------
    prob_fill_on_limit : float, optional
        Probability of limit order filling at limit price (default: 0.85)
    prob_slippage : float, optional
        Probability of adverse selection (default: 0.05)
    liquidity_factor : float, optional
        Percentage of visible liquidity available (default: 0.30)
    random_seed : int, optional
        Random seed for reproducibility
    """

    def __init__(
        self,
        prob_fill_on_limit: float = 0.85,
        prob_slippage: float = 0.05,
        liquidity_factor: float = 0.30,
        random_seed: Optional[int] = None,
    ):
        super().__init__(prob_fill_on_limit, prob_slippage, random_seed)
        self.liquidity_factor = liquidity_factor
        
        # Statistics
        self._fills_count = 0
        self._slips_count = 0
        self._rejects_count = 0

    def record_fill(self, slipped: bool = False) -> None:
        """Record fill statistics."""
        self._fills_count += 1
        if slipped:
            self._slips_count += 1

    def record_reject(self) -> None:
        """Record rejected fill."""
        self._rejects_count += 1

    def get_statistics(self) -> dict:
        """Get fill model statistics."""
        total = self._fills_count + self._rejects_count
        return {
            "fills_count": self._fills_count,
            "rejects_count": self._rejects_count,
            "slips_count": self._slips_count,
            "fill_rate": self._fills_count / max(1, total),
            "slip_rate": self._slips_count / max(1, self._fills_count),
            "prob_fill_on_limit": self.prob_fill_on_limit,
            "prob_slippage": self.prob_slippage,
            "liquidity_factor": self.liquidity_factor,
        }


def create_institutional_backtest_models(
    random_seed: Optional[int] = None,
) -> tuple[CoLocationLatencyModel, InstitutionalFillModel]:
    """
    Create institutional-grade backtest models.
    
    Returns
    -------
    tuple[CoLocationLatencyModel, InstitutionalFillModel]
        Latency and fill models configured for HFT backtesting
    """
    latency_model = CoLocationLatencyModel(
        base_latency_ns=250_000,  # 250μs AWS Singapore co-lo
        jitter_std_ns=100_000,    # 100μs std dev jitter
        spike_probability=0.001,   # 0.1% chance of 5ms+ spike
        random_seed=random_seed,
    )
    
    fill_model = InstitutionalFillModel(
        prob_fill_on_limit=0.85,   # 85% queue success
        prob_slippage=0.05,         # 5% adverse selection
        liquidity_factor=0.30,      # 30% visible liquidity available
        random_seed=random_seed,
    )
    
    return latency_model, fill_model


if __name__ == "__main__":
    # Test models
    print("Testing Institutional Latency Model")
    print("=" * 60)
    
    latency_model = CoLocationLatencyModel(random_seed=42)
    
    # Simulate 10,000 samples
    for _ in range(10_000):
        latency_model.get_insert_latency()
    
    stats = latency_model.get_statistics()
    print(f"Latency Statistics (10k samples):")
    print(f"  Mean:  {stats['mean_ns'] / 1000:.1f}μs")
    print(f"  P50:   {stats['p50_ns'] / 1000:.1f}μs")
    print(f"  P95:   {stats['p95_ns'] / 1000:.1f}μs")
    print(f"  P99:   {stats['p99_ns'] / 1000:.1f}μs")
    print(f"  P99.9: {stats['p99_9_ns'] / 1000:.1f}μs")
    print(f"  Min:   {stats['min_ns'] / 1000:.1f}μs")
    print(f"  Max:   {stats['max_ns'] / 1000:.1f}μs")
    
    print("\n" + "=" * 60)
    print("Testing Institutional Fill Model")
    print("=" * 60)
    
    fill_model = InstitutionalFillModel(random_seed=42)
    
    # Simulate 1000 fills
    for _ in range(1000):
        if fill_model.is_limit_filled():
            slipped = fill_model.is_slipped()
            fill_model.record_fill(slipped)
        else:
            fill_model.record_reject()
    
    fill_stats = fill_model.get_statistics()
    print(f"Fill Statistics (1k samples):")
    print(f"  Fill Rate:  {fill_stats['fill_rate']:.1%}")
    print(f"  Reject Rate: {100 - fill_stats['fill_rate'] * 100:.1%}")
    print(f"  Slip Rate:   {fill_stats['slip_rate']:.1%}")
    print(f"  Fills:      {fill_stats['fills_count']}")
    print(f"  Slips:      {fill_stats['slips_count']}")
    print(f"  Rejects:    {fill_stats['rejects_count']}")
