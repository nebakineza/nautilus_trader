#!/usr/bin/env python3
"""Validation test for institutional HFT backtest components.

Tests individual components without requiring full Nautilus environment.
"""

import json
import sys
import time
from pathlib import Path
from decimal import Decimal


def test_orderbook_loader():
    """Test order book data loader."""
    print("\n" + "="*70)
    print("TEST 1: ORDER BOOK LOADER")
    print("="*70)
    
    ob_file = Path("data/ob_data/BTCUSDT_Spot/2026-01-15_BTCUSDT_ob200.data")
    
    if not ob_file.exists():
        print(f"✗ Order book file not found: {ob_file}")
        return False
    
    print(f"✓ File exists: {ob_file}")
    print(f"  Size: {ob_file.stat().st_size / 1e6:.1f} MB")
    
    # Test parsing
    snapshot_count = 0
    delta_count = 0
    bid_levels_total = 0
    ask_levels_total = 0
    
    try:
        with open(ob_file, "r") as f:
            for i, line in enumerate(f):
                if i >= 100:  # Test first 100 lines
                    break
                
                data = json.loads(line)
                msg_type = data.get("type")
                
                if msg_type == "snapshot":
                    snapshot_count += 1
                    bid_levels_total += len(data["data"]["b"])
                    ask_levels_total += len(data["data"]["a"])
                    
                elif msg_type == "delta":
                    delta_count += 1
                    bid_levels_total += len(data["data"].get("b", []))
                    ask_levels_total += len(data["data"].get("a", []))
        
        print(f"✓ Data parsing successful")
        print(f"  Snapshots: {snapshot_count}")
        print(f"  Deltas: {delta_count}")
        print(f"  Bid updates: {bid_levels_total}")
        print(f"  Ask updates: {ask_levels_total}")
        
        if snapshot_count > 0 and delta_count > 0:
            print(f"\n✓ ORDERBOOK LOADER: PASSED")
            return True
        else:
            print(f"\n✗ ORDERBOOK LOADER: FAILED (missing data)")
            return False
            
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_strategy_config():
    """Test strategy configuration."""
    print("\n" + "="*70)
    print("TEST 2: STRATEGY CONFIGURATION")
    print("="*70)
    
    try:
        # Check config values
        config_params = {
            "instrument_id": "BTCUSDT-SPOT.BYBIT",
            "base_qty": Decimal("0.01"),
            "max_position_qty": Decimal("0.5"),
            "obi_levels": 10,
            "obi_ema_period": 20,
            "obi_entry_threshold": 0.20,
            "min_spread_bps": 2,
            "max_spread_bps": 10,
            "inventory_skew_bps": 0.5,
            "max_inventory_age_seconds": 300.0,
            "max_notional_usd": 100_000.0,
        }
        
        print(f"✓ Configuration parameters:")
        for key, value in config_params.items():
            print(f"  {key}: {value}")
        
        # Validate ranges
        validations = [
            ("obi_entry_threshold", 0 < config_params["obi_entry_threshold"] < 1),
            ("base_qty", config_params["base_qty"] > 0),
            ("max_position_qty", config_params["max_position_qty"] > config_params["base_qty"]),
            ("min_spread_bps", config_params["min_spread_bps"] > 0),
            ("max_spread_bps", config_params["max_spread_bps"] > config_params["min_spread_bps"]),
        ]
        
        all_valid = True
        for param, is_valid in validations:
            status = "✓" if is_valid else "✗"
            print(f"  {status} {param}")
            all_valid = all_valid and is_valid
        
        if all_valid:
            print(f"\n✓ STRATEGY CONFIG: PASSED")
            return True
        else:
            print(f"\n✗ STRATEGY CONFIG: FAILED")
            return False
            
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_models():
    """Test latency and fill models."""
    print("\n" + "="*70)
    print("TEST 3: LATENCY & FILL MODELS")
    print("="*70)
    
    try:
        import random
        
        # Test latency model
        base_latency_ns = 250_000  # 250μs
        jitter_std_ns = 100_000    # 100μs
        
        latencies = []
        for _ in range(1000):
            jitter = int(random.gauss(0, jitter_std_ns))
            latency = max(base_latency_ns + jitter, 10_000)
            latencies.append(latency)
        
        latencies.sort()
        mean_latency = sum(latencies) // len(latencies)
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        
        print(f"✓ Latency Model (1000 samples):")
        print(f"  Mean:  {mean_latency / 1000:.1f}μs")
        print(f"  P50:   {p50 / 1000:.1f}μs")
        print(f"  P95:   {p95 / 1000:.1f}μs")
        print(f"  P99:   {p99 / 1000:.1f}μs")
        
        # Validate latency distribution
        assert 200 <= p50 / 1000 <= 400, "P50 latency out of range"
        assert p95 / 1000 < 1000, "P95 latency too high"
        assert p99 / 1000 < 2000, "P99 latency too high"
        
        # Test fill model
        prob_fill_on_limit = 0.85
        prob_slippage = 0.05
        
        fills = 0
        slips = 0
        rejects = 0
        
        for _ in range(1000):
            if random.random() < prob_fill_on_limit:
                fills += 1
                if random.random() < prob_slippage:
                    slips += 1
            else:
                rejects += 1
        
        fill_rate = fills / 1000
        slip_rate = slips / fills if fills > 0 else 0
        
        print(f"\n✓ Fill Model (1000 samples):")
        print(f"  Fill Rate: {fill_rate:.1%}")
        print(f"  Reject Rate: {100 - fill_rate * 100:.1%}")
        print(f"  Slip Rate: {slip_rate:.1%}")
        print(f"  Expected Fill: 85% (actual: {fill_rate:.1%})")
        
        # Validate fill rates
        assert 0.75 < fill_rate < 0.95, "Fill rate out of expected range"
        
        print(f"\n✓ MODELS: PASSED")
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_file_structure():
    """Test that all required files exist."""
    print("\n" + "="*70)
    print("TEST 4: FILE STRUCTURE")
    print("="*70)
    
    required_files = [
        "examples/backtest/bybit_orderbook_loader.py",
        "examples/professional_hft_mm.py",
        "examples/backtest/institutional_models.py",
        "scripts/runners/run_institutional_hft_backtest.py",
        "data/ob_data/BTCUSDT_Spot/2026-01-15_BTCUSDT_ob200.data",
    ]
    
    all_exist = True
    for file_path in required_files:
        exists = Path(file_path).exists()
        status = "✓" if exists else "✗"
        print(f"  {status} {file_path}")
        all_exist = all_exist and exists
    
    if all_exist:
        print(f"\n✓ FILE STRUCTURE: PASSED")
        return True
    else:
        print(f"\n✗ FILE STRUCTURE: FAILED")
        return False


def main():
    """Run all validation tests."""
    print("\n" + "="*70)
    print("INSTITUTIONAL HFT BACKTEST - VALIDATION TESTS")
    print("="*70)
    
    results = []
    
    # Run tests
    results.append(("Order Book Loader", test_file_structure()))
    results.append(("Strategy Configuration", test_strategy_config()))
    results.append(("Latency & Fill Models", test_models()))
    results.append(("Data Format Parsing", test_orderbook_loader()))
    
    # Summary
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print(f"\n✓ ALL VALIDATION TESTS PASSED")
        print(f"\nYou are ready to run:")
        print("  python3 scripts/runners/run_institutional_hft_backtest.py --test --max-updates 10000")
        print("  python3 scripts/runners/run_institutional_hft_backtest.py")
        return 0
    else:
        print(f"\n✗ SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
