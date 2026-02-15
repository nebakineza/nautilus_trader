#!/usr/bin/env python3
"""Live runner for Mean Reversion v003 "JAMES" on Hyperliquid — SOL-USD-PERP.

SOL-TUNED PROFILE — Optuna-optimized on 60 days of 5m data (2026-02-12).
═══════════════════════════════════════════════════════════════════════════

Results (60-day backtest, $150 position size):
    OPTIMAL: 80.0% WR | 70 trades (56W/14L) | $+16.39 P&L | $0.23/trade | 1.2/day
    SAFE:    78.3% WR | 69 trades (54W/15L) | $+9.89  P&L | $0.14/trade | 1.2/day

NOTE: Backtest used $150 for controlled comparison. Live sizing should
    match your capital. With $2400 USDC and --auto-scale (default ON),
    the strategy auto-computes sizes from equity × leverage.

Key differences from v003 baseline (78% WR, 59t, $12.12 on 60d):
    BB=26 (vs 32)       → slightly faster mean, better for SOL's volatility
    exit_band=1.5σ      → wider exit lets SOL winners run further
    max_hold=75 bars    → SOL trends resolve slower, more patience pays
    trend_ema=39/152    → similar macro filter, slightly faster
    cooldown=6 bars     → slower re-entry after fill

Improvement over baseline:
    +18% more trades (70 vs 59), +35% more P&L ($16.39 vs $12.12)

SNIPER MODE (--sniper):
    100% WR 1h profile — Optuna 200 trials on 60 days of 1h SOL data.
    Trades only at deep statistical extremes (~3 trades/month).
    Runs as a SECOND strategy inside the same TradingNode, sharing
    the same HL connection, nonce lock, and position reconciliation.
    This avoids nonce collisions and reconciliation conflicts that
    would occur with two separate nodes on the same HL wallet.

USAGE:
    # Set your wallet private key (or use .env with EnvironmentFile in systemd)
    export HYPERLIQUID_MAINNET_PK=0x...
    export HYPERLIQUID_WALLET=0x...

    # Default: auto-scale ON, 3x leverage (sizes from your HL equity)
    .venv/bin/python scripts/runners/run_hl_mean_reversion_sol_v003.py

    # Disable auto-scale, use fixed sizing:
    .venv/bin/python scripts/runners/run_hl_mean_reversion_sol_v003.py --no-auto-scale --size 1200 --max-size 1800

    # Conservative mode (78.3% WR, slightly tighter filters):
    .venv/bin/python scripts/runners/run_hl_mean_reversion_sol_v003.py --safe

    # Higher leverage (careful — wider risk):
    .venv/bin/python scripts/runners/run_hl_mean_reversion_sol_v003.py --leverage 5

    # Add 1h sniper alongside the 5m workhorse (both in one process):
    .venv/bin/python scripts/runners/run_hl_mean_reversion_sol_v003.py --sniper
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from nautilus_trader.adapters.hyperliquid import (
    HYPERLIQUID,
    HyperliquidDataClientConfig,
    HyperliquidExecClientConfig,
    HyperliquidLiveDataClientFactory,
    HyperliquidLiveExecClientFactory,
)
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from strategy.hl_mean_reversion_v003 import HLMeanReversion, HLMeanReversionConfig


# =============================================================================
# SOL-TUNED DEFAULTS — Optuna 500 trials on 60-day 5m data
# =============================================================================
COIN = "SOL"
INSTRUMENT_ID = f"{COIN}-USD-PERP.HYPERLIQUID"

# ── Optimal profile (80.0% WR, 70 trades/60d, $16.39) ──
SOL_OPTIMAL = dict(
    bb_period=26,
    entry_band=0.8,
    entry_band_outer=1.4,
    exit_opposite_band=1.5,
    trend_ema_fast=39,
    trend_ema_slow=152,
    trend_min_separation_pct=0.06,   # from Optuna convergence
    trend_turn_lookback=7,
    trend_turn_z_bonus=0.30,
    noise_suppression_window=35,
    noise_suppression_ratio=0.90,
    hard_stop_pct=2.9,
    max_hold_bars=75,
    max_adverse_sigma=4.5,
    cooldown_bars=6,
    rsi_period=14,
    rsi_oversold=0.25,
    rsi_overbought=0.75,
)

# ── Conservative profile (78.3% WR, 69 trades/60d, $9.89) ──
SOL_SAFE = dict(
    bb_period=26,
    entry_band=0.8,
    entry_band_outer=1.4,
    exit_opposite_band=1.6,
    trend_ema_fast=39,
    trend_ema_slow=157,
    trend_min_separation_pct=0.06,
    trend_turn_lookback=7,
    trend_turn_z_bonus=0.30,
    noise_suppression_window=35,
    noise_suppression_ratio=0.85,
    hard_stop_pct=3.0,
    max_hold_bars=70,
    max_adverse_sigma=4.5,
    cooldown_bars=6,
    rsi_period=14,
    rsi_oversold=0.25,
    rsi_overbought=0.75,
)

# ── 1h SNIPER profile (100% WR, 13 trades/60d, $10.68) ──
# James: "moving those bands up moved the win rate to 99.08%"
# Trades only at deep statistical extremes on 1h bars.
# Optuna-optimized: 200 trials on 60 days of 1h SOL data.
SOL_SNIPER = dict(
    bb_period=24,                    # Slightly faster BB for 1h
    entry_band=1.4,                  # Wide inner — only extreme dips
    entry_band_outer=3.0,            # Very wide outer — ultra-extreme only
    exit_opposite_band=0.3,          # Aggressive TP — lock in immediately
    trend_ema_fast=25,               # Faster trend for 1h timeframe
    trend_ema_slow=113,              # Moderate slow EMA
    trend_min_separation_pct=0.16,   # Clear trend required
    trend_turn_lookback=7,           # Standard turn detection
    trend_turn_z_bonus=0.30,         # Standard turn bonus
    noise_suppression_window=35,     # Standard window
    noise_suppression_ratio=0.95,    # Ultra-strict: only peak signals
    hard_stop_pct=4.4,               # Wide stop — give room to revert
    max_hold_bars=50,                # 50 × 1h = ~2 days max hold
    max_adverse_sigma=5.0,           # Standard emergency exit
    cooldown_bars=15,                # 15h cooldown between trades
    rsi_period=14,
    rsi_oversold=0.25,
    rsi_overbought=0.75,
)


def parse_args():
    p = argparse.ArgumentParser(description="Mean Reversion v003 — SOL Live Runner")

    # Mode
    p.add_argument("--safe", action="store_true",
                   help="Use conservative 88.7%% WR profile (fewer trades)")
    p.add_argument("--sniper", action="store_true",
                   help="Add 1h SNIPER strategy (100%% WR, ~3 trades/month) "
                        "alongside the 5m workhorse in the SAME node")

    # Bar interval
    p.add_argument("--bar-interval", default="5m",
                   choices=["1m", "5m", "15m", "1h", "4h"],
                   help="Bar interval (default: 5m)")

    # Position sizing
    p.add_argument("--size", type=float, default=1200.0,
                   help="Total position budget in USD when auto-scale is off (default: 1200)")
    p.add_argument("--max-size", type=float, default=1800.0,
                   help="Max position cap in USD when auto-scale is off (default: 1800)")
    p.add_argument("--auto-scale", action="store_true", default=True,
                   help="Auto-scale position sizes from account equity (default: ON)")
    p.add_argument("--no-auto-scale", action="store_true",
                   help="Disable auto-scale, use fixed --size/--max-size instead")
    p.add_argument("--leverage", type=float, default=5.0,
                   help="Effective leverage for auto-scale (default: 5.0)")
    p.add_argument("--max-pos-cap", type=float, default=500_000.0,
                   help="Hard ceiling on max position USD (default: 500000)")

    # Risk overrides
    p.add_argument("--max-daily-loss", type=float, default=500.0,
                   help="Max daily loss floor in USD (default: 500)")
    p.add_argument("--max-daily-loss-pct", type=float, default=5.0,
                   help="Max daily loss as %% of equity — scales with capital (default: 5.0)")
    p.add_argument("--max-daily-trades", type=int, default=20,
                   help="Max trades per day (default: 20)")

    # Infra
    p.add_argument("--testnet", action="store_true", help="Use testnet")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    return p.parse_args()


def main():
    args = parse_args()

    # Handle auto-scale toggle (--no-auto-scale overrides default ON)
    use_auto_scale = args.auto_scale and not args.no_auto_scale

    # Validate private key
    pk_env = "HYPERLIQUID_TESTNET_PK" if args.testnet else "HYPERLIQUID_MAINNET_PK"
    private_key = os.environ.get(pk_env)
    wallet_address = os.environ.get("HYPERLIQUID_WALLET")
    if not private_key:
        print(f"❌ Set {pk_env} environment variable with your private key")
        sys.exit(1)
    if not wallet_address:
        print("❌ Set HYPERLIQUID_WALLET environment variable with your wallet address")
        sys.exit(1)

    # Select profile
    profile = SOL_SAFE if args.safe else SOL_OPTIMAL
    profile_name = "SAFE (78.3% WR, tighter filters)" if args.safe else "OPTIMAL (80.0% WR, 70 trades/60d)"

    # Build bar type
    interval_map = {
        "1m":  "1-MINUTE-LAST-EXTERNAL",
        "5m":  "5-MINUTE-LAST-EXTERNAL",
        "15m": "15-MINUTE-LAST-EXTERNAL",
        "1h":  "60-MINUTE-LAST-EXTERNAL",
        "4h":  "240-MINUTE-LAST-EXTERNAL",
    }
    bar_type_str = f"{INSTRUMENT_ID}-{interval_map[args.bar_interval]}"

    print("=" * 70)
    print(f"🎯 Mean Reversion v003 JAMES — LIVE on SOL-USD-PERP")
    print(f"   Profile: {profile_name}")
    print(f"   Five-model: Z-score + Trend + RSI + NoiseSup + DoubleTap")
    print("=" * 70)
    print(f"   Instrument:   {INSTRUMENT_ID}")
    print(f"   Bar Type:     {bar_type_str}")
    print(f"   BB Period:    {profile['bb_period']}")
    print(f"   Entry Band:   ±{profile['entry_band']}σ / {profile['entry_band_outer']}σ")
    print(f"   Exit Band:    ±{profile['exit_opposite_band']}σ")
    print(f"   Trend EMA:    {profile['trend_ema_fast']}/{profile['trend_ema_slow']}")
    print(f"   Noise Sup:    {profile['noise_suppression_window']}@{profile['noise_suppression_ratio']:.2f}")
    print(f"   Hard Stop:    {profile['hard_stop_pct']}%")
    print(f"   Max Hold:     {profile['max_hold_bars']} bars ({profile['max_hold_bars']*5}min)")
    if use_auto_scale:
        size_str = f"AUTO-SCALE {args.leverage}x equity"
    else:
        size_str = f"${args.size:,.0f} (max ${args.max_size:,.0f})"
    print(f"   Size:         {size_str}")
    print(f"   Testnet:      {args.testnet}")
    print("=" * 70)

    # ── Trading Node Config ──
    node_config = TradingNodeConfig(
        trader_id=TraderId(f"MR-SOL-003"),
        logging=LoggingConfig(
            log_level=args.log_level,
            use_pyo3=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
            open_check_interval_secs=15.0,
            open_check_open_only=False,
            graceful_shutdown_on_exception=True,
        ),
        data_clients={
            HYPERLIQUID: HyperliquidDataClientConfig(
                instrument_provider=InstrumentProviderConfig(load_all=True),
                testnet=args.testnet,
            ),
        },
        exec_clients={
            HYPERLIQUID: HyperliquidExecClientConfig(
                private_key=private_key,
                testnet=args.testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    # ── Strategy Config ──
    strategy_config = HLMeanReversionConfig(
        strategy_id=f"MR-SOL-003",
        instrument_id=INSTRUMENT_ID,
        bar_type=bar_type_str,

        # Mean Reversion — SOL-tuned
        bb_period=profile["bb_period"],
        bb_std_k=2.0,
        entry_band=profile["entry_band"],
        entry_band_outer=profile["entry_band_outer"],

        # Trend — SOL-tuned
        trend_ema_fast=profile["trend_ema_fast"],
        trend_ema_slow=profile["trend_ema_slow"],
        trend_min_separation_pct=profile["trend_min_separation_pct"],

        # Trend turn
        trend_turn_lookback=profile["trend_turn_lookback"],
        trend_turn_z_bonus=profile["trend_turn_z_bonus"],

        # Confidence
        rsi_period=profile["rsi_period"],
        rsi_oversold=profile["rsi_oversold"],
        rsi_overbought=profile["rsi_overbought"],

        # Turning point
        zscore_lookback=3,
        rsi_lookback=3,

        # Noise suppression — SOL-tuned
        noise_suppression_window=profile["noise_suppression_window"],
        noise_suppression_ratio=profile["noise_suppression_ratio"],

        # Position sizing — Double-tap LIFO
        base_trade_size_usd=args.size,
        max_position_usd=args.max_size,
        leg_allocation=(0.30, 0.40, 0.30),
        max_legs=3,
        scale_with_zscore=True,

        # Auto-scaling (compound growth)
        auto_scale=use_auto_scale,
        auto_scale_leverage=args.leverage,
        auto_scale_max_usd=args.max_pos_cap,

        # Exits — SOL-tuned
        exit_opposite_band=profile["exit_opposite_band"],
        max_adverse_sigma=profile["max_adverse_sigma"],
        hard_stop_pct=profile["hard_stop_pct"],
        max_hold_bars=profile["max_hold_bars"],
        require_strict_trend=True,
        atr_period=14,

        # Risk
        cooldown_bars=profile["cooldown_bars"],
        max_daily_loss_usd=args.max_daily_loss,
        max_daily_loss_pct=args.max_daily_loss_pct,
        max_daily_trades=args.max_daily_trades,
        max_open_positions=1,
    )

    # ── Build and run ──
    node = TradingNode(config=node_config)
    node.trader.add_strategy(HLMeanReversion(config=strategy_config))

    # ── Optional: Add 1h SNIPER strategy to the SAME node ──
    # This avoids nonce collisions and reconciliation conflicts that
    # would occur with two separate TradingNodes on the same HL wallet.
    # Both strategies share the exec client, nonce lock, and position view.
    if args.sniper:
        sniper = SOL_SNIPER
        sniper_bar_type = f"{INSTRUMENT_ID}-1-HOUR-LAST-EXTERNAL"
        sniper_config = HLMeanReversionConfig(
            strategy_id="MR-SOL-SNP",
            instrument_id=INSTRUMENT_ID,
            bar_type=sniper_bar_type,

            # Mean Reversion — 1h sniper
            bb_period=sniper["bb_period"],
            bb_std_k=2.0,
            entry_band=sniper["entry_band"],
            entry_band_outer=sniper["entry_band_outer"],

            # Trend
            trend_ema_fast=sniper["trend_ema_fast"],
            trend_ema_slow=sniper["trend_ema_slow"],
            trend_min_separation_pct=sniper["trend_min_separation_pct"],

            # Trend turn
            trend_turn_lookback=sniper["trend_turn_lookback"],
            trend_turn_z_bonus=sniper["trend_turn_z_bonus"],

            # Confidence
            rsi_period=sniper["rsi_period"],
            rsi_oversold=sniper["rsi_oversold"],
            rsi_overbought=sniper["rsi_overbought"],

            # Turning point
            zscore_lookback=3,
            rsi_lookback=3,

            # Noise suppression — ultra-strict
            noise_suppression_window=sniper["noise_suppression_window"],
            noise_suppression_ratio=sniper["noise_suppression_ratio"],

            # Position sizing (same as main strategy)
            base_trade_size_usd=args.size,
            max_position_usd=args.max_size,
            leg_allocation=(0.30, 0.40, 0.30),
            max_legs=3,
            scale_with_zscore=True,

            # Auto-scaling
            auto_scale=use_auto_scale,
            auto_scale_leverage=args.leverage,
            auto_scale_max_usd=args.max_pos_cap,

            # Exits — wide stop, aggressive TP
            exit_opposite_band=sniper["exit_opposite_band"],
            max_adverse_sigma=sniper["max_adverse_sigma"],
            hard_stop_pct=sniper["hard_stop_pct"],
            max_hold_bars=sniper["max_hold_bars"],
            require_strict_trend=True,
            atr_period=14,

            # Risk — conservative for sniper
            cooldown_bars=sniper["cooldown_bars"],
            max_daily_loss_usd=args.max_daily_loss,
            max_daily_loss_pct=args.max_daily_loss_pct,
            max_daily_trades=5,  # Sniper trades rarely
            max_open_positions=1,
        )
        node.trader.add_strategy(HLMeanReversion(config=sniper_config))
        print(f"   🎯 SNIPER: 1h profile added (inner=±{sniper['entry_band']}σ "
              f"outer=±{sniper['entry_band_outer']}σ exit={sniper['exit_opposite_band']}σ)")

    node.add_data_client_factory(HYPERLIQUID, HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory(HYPERLIQUID, HyperliquidLiveExecClientFactory)
    node.build()

    if use_auto_scale:
        print(f"   🔄 Auto-scale: ON ({args.leverage}x leverage, cap ${args.max_pos_cap:,.0f})")
        print(f"      Sizing computed from account equity at each new trade")
    else:
        print(f"   📏 Fixed sizing: base=${args.size:,.0f} max=${args.max_size:,.0f}")

    print(f"\n🚀 Starting Mean Reversion v003 on SOL (profile: {profile_name})...")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
