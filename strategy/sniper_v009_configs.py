"""
SNIPER v009 - Optimized Configuration per Pair
==============================================

Based on backtesting with VIP1 fees (0.0675% = 6.75 bps maker/taker)
Roundtrip fees: 13.5 bps

Requirements for profitability:
- Need to capture > 13.5 bps per roundtrip to profit after fees
- Pairs with tight natural spreads (< 15 bps) are NOT SUITABLE

TUNING RESULTS (Feb 2026):
---------------------------
Portfolio pairs from AGENTS.md (minus SUI):

DATA AVAILABLE + TESTED:
  LINKUSDT (20 bps natural): PROFITABLE at 8-11 bps spread
  AVAXUSDT (58 bps natural): NO FILLS - range detection not triggering
  
DATA AVAILABLE BUT NOT IN PORTFOLIO:
  DOGEUSDT (11 bps natural): Marginal at 8 bps (+$0.31, 2 fills)
  SOLUSDT (8 bps natural): Losing at all spreads
  XRPUSDT (7 bps natural): Data issues
  BTCUSDT (1 bps natural): TOO TIGHT
  ETHUSDT (0.5 bps natural): TOO TIGHT

NO DATA AVAILABLE:
  ENAUSDT, NEARUSDT, ARBUSDT, ONDOUSDT, TONUSDT, SEIUSDT, APTUSDT

METHODOLOGY:
1. Natural spread < 15 bps -> Likely not suitable (can't cover 13.5 bps fees)
2. Natural spread 15-50 bps -> Sweet spot around 8-15 bps target
3. Natural spread > 50 bps -> May not trigger enough (AVAX issue)
"""

from typing import Optional
from dataclasses import dataclass

from nautilus_trader.model.identifiers import InstrumentId


@dataclass
class PairConfig:
    """Configuration for a single trading pair."""
    symbol: str
    enabled: bool
    
    # Spread settings
    ranging_spread_bps: float
    breakout_spread_bps: float
    
    # Sizing
    order_qty: float
    max_position_qty: float
    
    # Risk management  
    max_hold_secs: int
    exit_cooldown_secs: int
    
    # Range detection
    range_confirm_ticks: int
    breakout_confirm_ticks: int
    
    # Notes
    reason: str = ""


# =============================================================================
# OPTIMIZED CONFIGURATIONS - VIP1 FEES (0.0675%)
# =============================================================================

PAIR_CONFIGS: dict[str, PairConfig] = {
    
    # =========================================================================
    # PROFITABLE PAIRS ✅
    # =========================================================================
    
    "LINKUSDT": PairConfig(
        symbol="LINKUSDT",
        enabled=True,  # ✅ PROFITABLE
        
        # Spread: 8-11 bps works depending on market conditions
        # 2026-02-01: 11 bps = 6 fills, +$0.18
        # 2026-02-02: 8 bps = 27 fills, +$0.61
        ranging_spread_bps=10.0,  # Middle ground
        breakout_spread_bps=15.0,
        
        # Sizing
        order_qty=1.0,  # 1 LINK ~ $21
        max_position_qty=10.0,  # $210 max position
        
        # Risk - conservative hold time
        max_hold_secs=300,  # 5 min max
        exit_cooldown_secs=30,
        
        # Detection - tuned for LINK volatility
        range_confirm_ticks=2,  # Fast entry
        breakout_confirm_ticks=5,  # 5 tick confirm
        
        reason="20 bps natural spread. Profitable: 8-11 bps depending on conditions. 6-27 fills/sample."
    ),
    
    "DOGEUSDT": PairConfig(
        symbol="DOGEUSDT",
        enabled=True,  # ✅ MARGINAL - needs monitoring
        
        # 2026-01-30: 8 bps = 4 fills, +$0.31
        ranging_spread_bps=8.0,
        breakout_spread_bps=12.0,
        
        order_qty=50.0,  # ~$20 per order
        max_position_qty=500.0,  # ~$200 max position
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="11 bps natural spread. Marginal at 8 bps (+$0.31, low volume). Monitor closely."
    ),
    
    # =========================================================================
    # NEEDS TUNING ⚠️
    # =========================================================================
    
    "AVAXUSDT": PairConfig(
        symbol="AVAXUSDT",
        enabled=False,  # ⚠️ NO FILLS - needs investigation
        
        # Natural spread: 58 bps - VERY WIDE
        # Issue: Range detection not triggering, 0 fills in all tests
        ranging_spread_bps=20.0,
        breakout_spread_bps=30.0,
        
        order_qty=0.5,  # 0.5 AVAX ~ $17
        max_position_qty=5.0,  # $175 max position
        
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="58 bps natural spread but 0 fills. Range detection not triggering - needs wider range_max_width_bps?"
    ),
    
    # =========================================================================
    # DISABLED PAIRS ❌
    # =========================================================================
    
    "SUIUSDT": PairConfig(
        symbol="SUIUSDT",
        enabled=False,  # ❌ NOT PROFITABLE
        
        # Natural spread only 5 bps - can't cover 13.5 bps fees
        ranging_spread_bps=6.0,
        breakout_spread_bps=10.0,
        
        order_qty=10.0,
        max_position_qty=100.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="5 bps natural spread - TOO TIGHT. Cannot cover 13.5 bps RT fees at any spread."
    ),
    
    "SOLUSDT": PairConfig(
        symbol="SOLUSDT",
        enabled=False,  # ❌ LOSING
        
        # 2026-01-30: All spreads losing (-$0.09 to -$1.69)
        ranging_spread_bps=25.0,
        breakout_spread_bps=40.0,
        
        order_qty=0.1,
        max_position_qty=1.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="8 bps natural spread. Losing at all spreads tested (5-25 bps). Not suitable."
    ),
    
    "ENAUSDT": PairConfig(
        symbol="ENAUSDT",
        enabled=False,  # ❌ NO DATA
        
        ranging_spread_bps=10.0,
        breakout_spread_bps=15.0,
        
        order_qty=10.0,
        max_position_qty=100.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="No orderbook data available for backtesting"
    ),
    
    "NEARUSDT": PairConfig(
        symbol="NEARUSDT",
        enabled=False,  # ❌ NO DATA
        
        ranging_spread_bps=10.0,
        breakout_spread_bps=15.0,
        
        order_qty=2.0,
        max_position_qty=20.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="No orderbook data available for backtesting"
    ),
    
    "ARBUSDT": PairConfig(
        symbol="ARBUSDT",
        enabled=False,  # ❌ NO DATA
        
        ranging_spread_bps=10.0,
        breakout_spread_bps=15.0,
        
        order_qty=10.0,
        max_position_qty=100.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="No orderbook data available for backtesting"
    ),
    
    "ONDOUSDT": PairConfig(
        symbol="ONDOUSDT",
        enabled=False,  # ❌ NO DATA
        
        ranging_spread_bps=10.0,
        breakout_spread_bps=15.0,
        
        order_qty=10.0,
        max_position_qty=100.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="No orderbook data available for backtesting"
    ),
    
    "TONUSDT": PairConfig(
        symbol="TONUSDT",
        enabled=False,  # ❌ NO DATA
        
        ranging_spread_bps=10.0,
        breakout_spread_bps=15.0,
        
        order_qty=2.0,
        max_position_qty=20.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="No orderbook data available for backtesting"
    ),
    
    "SEIUSDT": PairConfig(
        symbol="SEIUSDT",
        enabled=False,  # ❌ NO DATA
        
        ranging_spread_bps=10.0,
        breakout_spread_bps=15.0,
        
        order_qty=20.0,
        max_position_qty=200.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="No orderbook data available for backtesting"
    ),
    
    "APTUSDT": PairConfig(
        symbol="APTUSDT",
        enabled=False,  # ❌ NO DATA
        
        ranging_spread_bps=10.0,
        breakout_spread_bps=15.0,
        
        order_qty=1.0,
        max_position_qty=10.0,
        max_hold_secs=300,
        exit_cooldown_secs=30,
        range_confirm_ticks=2,
        breakout_confirm_ticks=5,
        
        reason="No orderbook data available for backtesting"
    ),
}


def get_enabled_pairs() -> list[str]:
    """Return list of enabled trading pairs."""
    return [symbol for symbol, config in PAIR_CONFIGS.items() if config.enabled]


def get_config(symbol: str) -> Optional[PairConfig]:
    """Get configuration for a specific symbol."""
    return PAIR_CONFIGS.get(symbol)


def print_config_summary():
    """Print a summary of all pair configurations."""
    print("=" * 70)
    print("SNIPER v009 PAIR CONFIGURATION SUMMARY")
    print("=" * 70)
    print(f"VIP1 Fees: 0.0675% maker/taker (13.5 bps roundtrip)")
    print()
    
    enabled = [s for s, c in PAIR_CONFIGS.items() if c.enabled]
    disabled = [s for s, c in PAIR_CONFIGS.items() if not c.enabled]
    
    print(f"ENABLED ({len(enabled)}):")
    for symbol in enabled:
        cfg = PAIR_CONFIGS[symbol]
        print(f"  ✅ {symbol}: {cfg.ranging_spread_bps} bps spread")
        print(f"     {cfg.reason}")
    
    print()
    print(f"DISABLED ({len(disabled)}):")
    for symbol in disabled:
        cfg = PAIR_CONFIGS[symbol]
        print(f"  ❌ {symbol}: {cfg.reason}")
    
    print("=" * 70)


if __name__ == "__main__":
    print_config_summary()
