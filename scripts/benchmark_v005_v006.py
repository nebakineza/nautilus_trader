#!/usr/bin/env python3
"""Benchmark v005 vs v006 performance."""

import time
import numpy as np
from decimal import Decimal

# Number of iterations
N = 100_000

print("=" * 60)
print("PERFORMANCE BENCHMARK: v005 (Decimal) vs v006 (Float64)")
print("=" * 60)
print(f"Iterations: {N:,}")
print()

# =============================================================================
# 1. SPREAD CALCULATION
# =============================================================================
print("1. SPREAD CALCULATION")
print("-" * 40)

# v005 style (Decimal)
mid_dec = Decimal("3.45678")
spread_bps_dec = Decimal("60.0")
ofi_adj_dec = Decimal("10.0")
vol_adj_dec = Decimal("5.0")

start = time.perf_counter_ns()
for _ in range(N):
    bid_spread = spread_bps_dec + ofi_adj_dec + vol_adj_dec
    ask_spread = spread_bps_dec - ofi_adj_dec + vol_adj_dec
    bid_price = mid_dec - (mid_dec * bid_spread / Decimal("20000"))
    ask_price = mid_dec + (mid_dec * ask_spread / Decimal("20000"))
v005_ns = (time.perf_counter_ns() - start) / N

# v006 style (Float64)
mid_f = 3.45678
spread_bps_f = 60.0
ofi_adj_f = 10.0
vol_adj_f = 5.0

start = time.perf_counter_ns()
for _ in range(N):
    bid_spread = spread_bps_f + ofi_adj_f + vol_adj_f
    ask_spread = spread_bps_f - ofi_adj_f + vol_adj_f
    bid_price = mid_f - (mid_f * bid_spread / 20000.0)
    ask_price = mid_f + (mid_f * ask_spread / 20000.0)
v006_ns = (time.perf_counter_ns() - start) / N

print(f"  v005 (Decimal): {v005_ns:.0f} ns/iter")
print(f"  v006 (Float64): {v006_ns:.0f} ns/iter")
print(f"  Speedup: {v005_ns/v006_ns:.1f}x")
print()

# =============================================================================
# 2. INVENTORY SKEW (Avellaneda-Stoikov)
# =============================================================================
print("2. INVENTORY SKEW CALCULATION")
print("-" * 40)

# v005 style
pos_dec = Decimal("50.0")
max_pos_dec = Decimal("100.0")
risk_dec = Decimal("0.8")
vol_dec = Decimal("0.02")
time_dec = Decimal("60.0")

start = time.perf_counter_ns()
for _ in range(N):
    ratio = pos_dec / max_pos_dec
    skew = -risk_dec * (vol_dec ** 2) * time_dec * ratio * Decimal("10000")
    skew = max(Decimal("-50"), min(Decimal("50"), skew))
v005_ns = (time.perf_counter_ns() - start) / N

# v006 style
pos_f = 50.0
max_pos_f = 100.0
risk_f = 0.8
vol_f = 0.02
time_f = 60.0

start = time.perf_counter_ns()
for _ in range(N):
    ratio = pos_f / max_pos_f
    skew = -risk_f * (vol_f ** 2) * time_f * ratio * 10000.0
    skew = max(-50.0, min(50.0, skew))
v006_ns = (time.perf_counter_ns() - start) / N

print(f"  v005 (Decimal): {v005_ns:.0f} ns/iter")
print(f"  v006 (Float64): {v006_ns:.0f} ns/iter")
print(f"  Speedup: {v005_ns/v006_ns:.1f}x")
print()

# =============================================================================
# 3. VOLATILITY CALCULATION (Rolling Std Dev)
# =============================================================================
print("3. VOLATILITY CALCULATION (20 prices)")
print("-" * 40)

# Setup data
prices_list = [3.45 + 0.001 * i for i in range(20)]
prices_array = np.array(prices_list, dtype=np.float64)

# v005 style (list + manual loop)
start = time.perf_counter_ns()
for _ in range(N):
    changes = []
    for i in range(1, len(prices_list)):
        if prices_list[i-1] > 0:
            changes.append(abs(prices_list[i] - prices_list[i-1]) / prices_list[i-1] * 10000.0)
    if changes:
        mean = sum(changes) / len(changes)
        variance = sum((c - mean) ** 2 for c in changes) / len(changes)
        vol = variance ** 0.5
v005_ns = (time.perf_counter_ns() - start) / N

# v006 style (NumPy vectorized - AVX-512)
start = time.perf_counter_ns()
for _ in range(N):
    returns = np.abs(np.diff(prices_array) / prices_array[:-1]) * 10000.0
    vol = float(np.std(returns))
v006_ns = (time.perf_counter_ns() - start) / N

print(f"  v005 (Python list):  {v005_ns:.0f} ns/iter")
print(f"  v006 (NumPy AVX-512): {v006_ns:.0f} ns/iter")
print(f"  Speedup: {v005_ns/v006_ns:.1f}x")
print()

# =============================================================================
# 4. REGIME DETECTION (Efficiency Ratio)
# =============================================================================
print("4. REGIME DETECTION (100 prices)")
print("-" * 40)

prices_100_list = [3.45 + 0.001 * i for i in range(100)]
prices_100_array = np.array(prices_100_list, dtype=np.float64)

# v005 style
start = time.perf_counter_ns()
for _ in range(N // 10):  # Fewer iterations since slower
    net_move = abs(prices_100_list[-1] - prices_100_list[0])
    total_move = 0.0
    for i in range(1, len(prices_100_list)):
        total_move += abs(prices_100_list[i] - prices_100_list[i-1])
    if total_move > 0:
        trend = net_move / total_move
v005_ns = (time.perf_counter_ns() - start) / (N // 10)

# v006 style (NumPy)
start = time.perf_counter_ns()
for _ in range(N // 10):
    net_move = abs(prices_100_array[-1] - prices_100_array[0])
    total_move = float(np.sum(np.abs(np.diff(prices_100_array))))
    if total_move > 0:
        trend = net_move / total_move
v006_ns = (time.perf_counter_ns() - start) / (N // 10)

print(f"  v005 (Python loop): {v005_ns:.0f} ns/iter")
print(f"  v006 (NumPy):       {v006_ns:.0f} ns/iter")
print(f"  Speedup: {v005_ns/v006_ns:.1f}x")
print()

# =============================================================================
# 5. STRING FORMATTING (Logging)
# =============================================================================
print("5. LOG STRING FORMATTING")
print("-" * 40)

# v005 style (always format)
start = time.perf_counter_ns()
for _ in range(N):
    msg = f"SPREAD: Base={spread_bps_f:.1f} | OFI={ofi_adj_f:.1f} | Vol={vol_adj_f:.1f}"
v005_ns = (time.perf_counter_ns() - start) / N

# v006 style (guarded - no format if not logging)
log_enabled = False
start = time.perf_counter_ns()
for _ in range(N):
    if log_enabled:
        msg = f"SPREAD: Base={spread_bps_f:.1f} | OFI={ofi_adj_f:.1f} | Vol={vol_adj_f:.1f}"
v006_ns = (time.perf_counter_ns() - start) / N

print(f"  v005 (always format): {v005_ns:.0f} ns/iter")
print(f"  v006 (guarded):       {v006_ns:.0f} ns/iter")
print(f"  Speedup: {v005_ns/v006_ns:.1f}x (when log disabled)")
print()

# =============================================================================
# SUMMARY
# =============================================================================
print("=" * 60)
print("SUMMARY: Expected latency improvement per quote refresh")
print("=" * 60)
print()
print("The v006 TURBO strategy should be 10-100x faster in hot path")
print("calculations, enabling sub-microsecond quote refresh latency.")
print()
print("Key optimizations:")
print("  • Float64 arithmetic (vs Decimal)")
print("  • NumPy vectorized ops (AVX-512 on t3.medium)")
print("  • Pre-allocated ring buffers")
print("  • Guarded logging")
print("  • __slots__ for faster attribute access")
print("  • Inline calculations (no method call overhead)")
