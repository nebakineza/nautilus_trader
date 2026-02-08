"""V007 Fortress Runner Configuration Profiles.

Based on Feb 6, 2026 loss analysis and pair characteristics.
"""

from typing import Any


# =============================================================================
# PROFILE DEFINITIONS
# =============================================================================

AGGRESSIVE_PROFILE: dict[str, Any] = {
    # For high-cap, liquid pairs with stable spreads.
    "spread_bps": 60.0,
    "ofi_widen_bps": 12.0,
    "ofi_tighten_bps": 5.0,
    "volatility_max_widen_bps": 40.0,
    "regime_window_ticks": 120,
    "regime_trending_threshold": 0.60,
    "regime_ranging_threshold": 0.28,
    "regime_trending_spread_mult": 2.0,
    "regime_trending_size_mult": 0.4,
    "trend_filter_threshold": 0.78,
    "trend_filter_pause_secs": 45,
    "inventory_skew_threshold_pct": 0.35,
    "inventory_skew_max_widen_bps": 40.0,
    "inventory_skew_block_threshold_pct": 0.85,
    "runaway_window_fills": 8,
    "runaway_threshold_pct": 0.75,
    "runaway_pause_secs": 45,
    "hit_rate_imbalance_threshold": 0.75,
    "min_profit_bps": 8.0,
    "realized_spread_window_fills": 7,
    "realized_spread_min_bps": 8.0,
    "realized_spread_widen_step_bps": 15.0,
    "realized_spread_max_widen_bps": 120.0,
    "fifo_pnl_loss_threshold_bps": -25.0,
    "fifo_pnl_pause_secs": 90,
    "dynamic_spread_min_bps": 35.0,
    "dynamic_spread_max_bps": 180.0,
}

SAFE_PROFILE: dict[str, Any] = {
    # Default safe profile for mid-cap pairs.
    "spread_bps": 80.0,
    "ofi_widen_bps": 15.0,
    "ofi_tighten_bps": 3.0,
    "volatility_max_widen_bps": 50.0,
    "regime_window_ticks": 150,
    "regime_trending_threshold": 0.65,
    "regime_ranging_threshold": 0.25,
    "regime_trending_spread_mult": 2.5,
    "regime_trending_size_mult": 0.3,
    "trend_filter_threshold": 0.75,
    "trend_filter_pause_secs": 60,
    "inventory_skew_threshold_pct": 0.30,
    "inventory_skew_max_widen_bps": 50.0,
    "inventory_skew_block_threshold_pct": 0.80,
    "runaway_window_fills": 5,
    "runaway_threshold_pct": 0.70,
    "runaway_pause_secs": 60,
    "hit_rate_imbalance_threshold": 0.70,
    "min_profit_bps": 10.0,
    "realized_spread_window_fills": 5,
    "realized_spread_min_bps": 10.0,
    "realized_spread_widen_step_bps": 20.0,
    "realized_spread_max_widen_bps": 150.0,
    "fifo_pnl_loss_threshold_bps": -20.0,
    "fifo_pnl_pause_secs": 120,
    "dynamic_spread_min_bps": 40.0,
    "dynamic_spread_max_bps": 200.0,
}

ULTRA_SAFE_PROFILE: dict[str, Any] = {
    # Maximum protection for volatile/low-cap pairs.
    "spread_bps": 120.0,
    "ofi_widen_bps": 25.0,
    "ofi_tighten_bps": 2.0,
    "volatility_max_widen_bps": 80.0,
    "regime_window_ticks": 200,
    "regime_trending_threshold": 0.70,
    "regime_ranging_threshold": 0.20,
    "regime_trending_spread_mult": 3.0,
    "regime_trending_size_mult": 0.2,
    "trend_filter_threshold": 0.70,
    "trend_filter_pause_secs": 120,
    "inventory_skew_threshold_pct": 0.20,
    "inventory_skew_max_widen_bps": 80.0,
    "inventory_skew_block_threshold_pct": 0.70,
    "runaway_window_fills": 3,
    "runaway_threshold_pct": 0.65,
    "runaway_pause_secs": 120,
    "hit_rate_imbalance_threshold": 0.65,
    "min_profit_bps": 15.0,
    "realized_spread_window_fills": 3,
    "realized_spread_min_bps": 20.0,
    "realized_spread_widen_step_bps": 30.0,
    "realized_spread_max_widen_bps": 200.0,
    "fifo_pnl_loss_threshold_bps": -10.0,
    "fifo_pnl_pause_secs": 180,
    "dynamic_spread_min_bps": 60.0,
    "dynamic_spread_max_bps": 300.0,
}


# =============================================================================
# PAIR-SPECIFIC CONFIGURATIONS
# =============================================================================

PAIR_CONFIGS = {
    # === AGGRESSIVE (High liquidity, stable) ===
    "LINKUSDT": {
        "profile": AGGRESSIVE_PROFILE,
        "api_suffix": "02",
        "order_qty": 1.5,
        "max_position_qty": 15.0,
        "min_order_qty": 0.5,
    },
    "AVAXUSDT": {
        "profile": AGGRESSIVE_PROFILE,
        "api_suffix": "03",
        "order_qty": 3.0,
        "max_position_qty": 30.0,
        "min_order_qty": 0.5,
    },
    
    # === SAFE (Mid-cap) ===
    "SUIUSDT": {
        "profile": SAFE_PROFILE,
        "api_suffix": "01",
        "order_qty": 80.0,
        "max_position_qty": 800.0,
        "min_order_qty": 20.0,
    },
    "NEARUSDT": {
        "profile": SAFE_PROFILE,
        "api_suffix": "05",
        "order_qty": 15.0,
        "max_position_qty": 150.0,
        "min_order_qty": 3.0,
    },
    "TONUSDT": {
        "profile": SAFE_PROFILE,
        "api_suffix": "08",
        "order_qty": 8.0,
        "max_position_qty": 80.0,
        "min_order_qty": 2.0,
    },
    "ARBUSDT": {
        "profile": SAFE_PROFILE,
        "api_suffix": "06",
        "order_qty": 80.0,
        "max_position_qty": 800.0,
        "min_order_qty": 20.0,
    },
    
    # === ULTRA SAFE (Volatile, low-cap) ===
    "ENAUSDT": {
        "profile": ULTRA_SAFE_PROFILE,
        "api_suffix": "04",
        "order_qty": 100.0,  # Reduced from 300
        "max_position_qty": 500.0,  # Reduced from 1500
        "min_order_qty": 10.0,
    },
    "ONDOUSDT": {
        "profile": ULTRA_SAFE_PROFILE,
        "api_suffix": "07",
        "order_qty": 15.0,
        "max_position_qty": 150.0,
        "min_order_qty": 5.0,
    },
    "SEIUSDT": {
        "profile": ULTRA_SAFE_PROFILE,
        "api_suffix": "00",  # Local dev key
        "order_qty": 50.0,
        "max_position_qty": 500.0,
        "min_order_qty": 10.0,
    },
    "APTUSDT": {
        "profile": ULTRA_SAFE_PROFILE,
        "api_suffix": "00",  # Local dev key
        "order_qty": 2.0,
        "max_position_qty": 20.0,
        "min_order_qty": 0.5,
    },
}


def get_pair_config(symbol: str) -> dict[str, Any]:
    """Get complete config for a trading pair.
    
    Returns merged config from profile + pair-specific settings.
    """
    if symbol not in PAIR_CONFIGS:
        raise ValueError(f"Unknown symbol: {symbol}. Available: {list(PAIR_CONFIGS.keys())}")
    
    pair_cfg = PAIR_CONFIGS[symbol]
    profile = pair_cfg["profile"].copy()
    
    # Override with pair-specific settings
    result = {**profile}
    result["order_qty"] = pair_cfg["order_qty"]
    result["max_position_qty"] = pair_cfg["max_position_qty"]
    result["min_order_qty"] = pair_cfg["min_order_qty"]
    result["api_suffix"] = pair_cfg["api_suffix"]
    
    return result


def get_profile_name(symbol: str) -> str:
    """Get profile name for a symbol."""
    if symbol not in PAIR_CONFIGS:
        return "UNKNOWN"
    
    profile = PAIR_CONFIGS[symbol]["profile"]
    if profile is AGGRESSIVE_PROFILE:
        return "AGGRESSIVE"
    elif profile is SAFE_PROFILE:
        return "SAFE"
    elif profile is ULTRA_SAFE_PROFILE:
        return "ULTRA_SAFE"
    return "CUSTOM"


# Print summary when imported
if __name__ == "__main__":
    print("V007 FORTRESS PAIR PROFILES")
    print("=" * 50)
    for symbol, cfg in PAIR_CONFIGS.items():
        profile_name = get_profile_name(symbol)
        spread = cfg["profile"]["spread_bps"]
        print(f"  {symbol:<12} | {profile_name:<12} | {spread}bps | API_KEY_MAINACC_{cfg['api_suffix']}")
