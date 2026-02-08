"""
V006 Safe Configuration Profiles

Based on analysis of Feb 6, 2026 losses:
- Root cause: Trending market exposure causing -348 bps on ENA, -257 bps on SUI
- Problem: FIFO matched spread only +16 bps while fees cost 20 bps
- Solution: Wider base spreads + stricter regime detection + aggressive guardian

These configs are designed to SURVIVE adverse markets, not maximize profit in ideal conditions.
"""

from typing import Any

# =============================================================================
# SAFE PROFILE: Minimum 80 bps spread, strict regime detection
# =============================================================================
SAFE_PROFILE: dict[str, Any] = {
    # === WIDER BASE SPREAD ===
    # Was 60 bps, causing ~17 bps capture in trending markets (below 20 bps fee breakeven)
    # New: 80 bps minimum, ensures 40 bps profit even with 50% adverse selection
    "spread_bps": 80.0,
    
    # === STRICTER REGIME DETECTION ===
    # Was: threshold=0.5 (too sensitive, flips every few seconds)
    # New: threshold=0.65 (requires stronger trend to trigger)
    "regime_trending_threshold": 0.65,
    "regime_ranging_threshold": 0.25,  # Wider hysteresis
    "regime_window_ticks": 150,  # Longer window (less noise)
    
    # === MORE AGGRESSIVE REGIME RESPONSE ===
    "regime_trending_spread_mult": 2.5,  # Was 2.0, now 2.5x spread in trends
    "regime_trending_size_mult": 0.3,    # Was 0.5, now 0.3x size in trends
    
    # === STRONGER GUARDIAN ===
    "realized_spread_guardian_enabled": True,
    "realized_spread_window_fills": 5,   # Was 10, now react faster
    "realized_spread_min_bps": 10.0,     # Was 0, now require 10 bps profit
    "realized_spread_widen_step_bps": 20.0,  # Was 10, now bigger steps
    "realized_spread_max_widen_bps": 150.0,  # Was 100, allow more widening
    "realized_spread_cooldown_fills": 3,     # Was 5, recover faster
    
    # === STRONGER OFI RESPONSE ===
    "ofi_widen_bps": 15.0,  # Was 10, now more aggressive
    "ofi_tighten_bps": 3.0,  # Was 5, less aggressive recovery
    
    # === VOLATILITY PROTECTION ===
    "volatility_max_widen_bps": 50.0,  # Was 30
    
    # === GUARD RAILS ===
    "guard_threshold_bps": 5.0,   # Was 10
    "guard_hysteresis_bps": 10.0,  # Was 5
    
    # === RUNAWAY PROTECTION ===
    "runaway_detection_enabled": True,
    "runaway_window_fills": 5,      # Was 10, detect faster
    "runaway_threshold_pct": 0.70,  # Was 0.80, stricter
    "runaway_pause_secs": 60,       # Was 30, longer pause
}


# =============================================================================
# AGGRESSIVE PROFILE: For high-volume pairs with good liquidity (LINK, AVAX)
# =============================================================================
AGGRESSIVE_PROFILE: dict[str, Any] = {
    "spread_bps": 60.0,
    "regime_trending_threshold": 0.60,
    "regime_ranging_threshold": 0.30,
    "regime_window_ticks": 100,
    "regime_trending_spread_mult": 2.0,
    "regime_trending_size_mult": 0.5,
    "realized_spread_guardian_enabled": True,
    "realized_spread_window_fills": 8,
    "realized_spread_min_bps": 5.0,
    "realized_spread_widen_step_bps": 15.0,
    "realized_spread_max_widen_bps": 100.0,
    "ofi_widen_bps": 12.0,
    "volatility_max_widen_bps": 40.0,
}


# =============================================================================
# ULTRA SAFE PROFILE: For volatile/low-cap coins (ENA, ONDO, SEI, APT)
# =============================================================================
ULTRA_SAFE_PROFILE: dict[str, Any] = {
    "spread_bps": 100.0,  # Wide base spread
    "regime_trending_threshold": 0.70,  # Very strict trending detection
    "regime_ranging_threshold": 0.20,   # Wide hysteresis
    "regime_window_ticks": 200,         # Very long window
    "regime_trending_spread_mult": 3.0,  # 3x spread in trends!
    "regime_trending_size_mult": 0.2,    # 0.2x size in trends
    "realized_spread_guardian_enabled": True,
    "realized_spread_window_fills": 3,   # React very fast
    "realized_spread_min_bps": 15.0,     # Require 15 bps profit
    "realized_spread_widen_step_bps": 30.0,
    "realized_spread_max_widen_bps": 200.0,
    "ofi_widen_bps": 20.0,
    "volatility_max_widen_bps": 60.0,
    "runaway_threshold_pct": 0.65,  # Very strict
    "runaway_pause_secs": 120,      # 2 minute pause
}


# =============================================================================
# PAIR-SPECIFIC ASSIGNMENTS
# =============================================================================
PAIR_PROFILES = {
    # High-cap, good liquidity → Aggressive
    "LINKUSDT": AGGRESSIVE_PROFILE,
    "AVAXUSDT": AGGRESSIVE_PROFILE,
    
    # Mid-cap → Safe
    "SUIUSDT": SAFE_PROFILE,
    "NEARUSDT": SAFE_PROFILE,
    "TONUSDT": SAFE_PROFILE,
    "ARBUSDT": SAFE_PROFILE,
    
    # Low-cap, volatile → Ultra Safe
    "ENAUSDT": ULTRA_SAFE_PROFILE,
    "ONDOUSDT": ULTRA_SAFE_PROFILE,
    "SEIUSDT": ULTRA_SAFE_PROFILE,
    "APTUSDT": ULTRA_SAFE_PROFILE,
}


def get_safe_config(symbol: str) -> dict[str, Any]:
    """Get safe config for a symbol, defaulting to SAFE_PROFILE."""
    return PAIR_PROFILES.get(symbol, SAFE_PROFILE).copy()
