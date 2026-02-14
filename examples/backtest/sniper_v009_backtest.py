#!/usr/bin/env python3
"""Backtest for v009 Sniper HFT strategy with Volume Profile.

Uses local orderbook files from data/ob_data/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import (
    LatencyModel,
    FillModel,
)
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.venues import Venue

from examples.backtest.binance_orderbook_loader import BinanceOrderBookLoader
from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from strategy.lead_lag_bybit_binance_mm_v009_sniper import (
    LeadLagMMv9Sniper,
    LeadLagMMv9SniperConfig,
)


# =============================================================================
# PAIR CONFIGURATIONS
# =============================================================================
@dataclass(frozen=True)
class SniperPairConfig:
    symbol: str
    order_qty: float
    max_position_qty: float
    ranging_spread_bps: float
    breakout_spread_bps: float
    tick_size: Decimal
    lot_size: Decimal
    price_precision: int
    size_precision: int


PAIR_CONFIGS = {
    "ETHUSDT": SniperPairConfig(
        symbol="ETHUSDT",
        order_qty=0.01,         # ~$30 per order
        max_position_qty=0.1,   # ~$300 max position
        ranging_spread_bps=5.0,  # Very tight 5 bps
        breakout_spread_bps=10.0,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.00001"),
        price_precision=2,
        size_precision=5,
    ),
    "SUIUSDT": SniperPairConfig(
        symbol="SUIUSDT",
        order_qty=5.0,          # ~$25 per order
        max_position_qty=50.0,  # ~$250 max position
        ranging_spread_bps=25.0,
        breakout_spread_bps=50.0,
        tick_size=Decimal("0.0001"),
        lot_size=Decimal("0.01"),
        price_precision=4,
        size_precision=2,
    ),
    "LINKUSDT": SniperPairConfig(
        symbol="LINKUSDT",
        order_qty=1.0,
        max_position_qty=10.0,
        ranging_spread_bps=25.0,
        breakout_spread_bps=50.0,
        tick_size=Decimal("0.001"),
        lot_size=Decimal("0.01"),
        price_precision=3,
        size_precision=2,
    ),
    "AVAXUSDT": SniperPairConfig(
        symbol="AVAXUSDT",
        order_qty=0.5,
        max_position_qty=5.0,
        ranging_spread_bps=25.0,
        breakout_spread_bps=50.0,
        tick_size=Decimal("0.001"),
        lot_size=Decimal("0.01"),
        price_precision=3,
        size_precision=2,
    ),
    "SOLUSDT": SniperPairConfig(
        symbol="SOLUSDT",
        order_qty=0.1,          # ~$20 per order
        max_position_qty=1.0,   # ~$200 max position
        ranging_spread_bps=10.0,
        breakout_spread_bps=20.0,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.01"),
        price_precision=2,
        size_precision=2,
    ),
    "XRPUSDT": SniperPairConfig(
        symbol="XRPUSDT",
        order_qty=10.0,         # ~$25 per order
        max_position_qty=100.0, # ~$250 max position
        ranging_spread_bps=10.0,
        breakout_spread_bps=20.0,
        tick_size=Decimal("0.0001"),
        lot_size=Decimal("0.01"),
        price_precision=4,
        size_precision=2,
    ),
    "DOGEUSDT": SniperPairConfig(
        symbol="DOGEUSDT",
        order_qty=50.0,         # ~$20 per order
        max_position_qty=500.0, # ~$200 max position
        ranging_spread_bps=15.0,
        breakout_spread_bps=25.0,
        tick_size=Decimal("0.00001"),
        lot_size=Decimal("1"),
        price_precision=5,
        size_precision=0,
    ),
    "BTCUSDT": SniperPairConfig(
        symbol="BTCUSDT",
        order_qty=0.0005,       # ~$50 per order
        max_position_qty=0.005, # ~$500 max position
        ranging_spread_bps=3.0,  # BTC is very tight
        breakout_spread_bps=5.0,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.00001"),
        price_precision=2,
        size_precision=5,
    ),
    "DOTUSDT": SniperPairConfig(
        symbol="DOTUSDT",
        order_qty=2.0,          # ~$16 per order
        max_position_qty=20.0,  # ~$160 max position
        ranging_spread_bps=15.0,
        breakout_spread_bps=25.0,
        tick_size=Decimal("0.001"),
        lot_size=Decimal("0.01"),
        price_precision=3,
        size_precision=2,
    ),
    "OPUSDT": SniperPairConfig(
        symbol="OPUSDT",
        order_qty=5.0,          # ~$10 per order
        max_position_qty=50.0,  # ~$100 max position
        ranging_spread_bps=15.0,
        breakout_spread_bps=25.0,
        tick_size=Decimal("0.001"),
        lot_size=Decimal("0.01"),
        price_precision=3,
        size_precision=2,
    ),
}


def create_instrument(
    suffix: str,
    symbol: str,
    pair_config: SniperPairConfig,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> CurrencyPair:
    """Create a CurrencyPair instrument."""
    base_str = symbol[:-4]  # Remove USDT
    base = Currency.from_str(base_str)
    quote = USDT

    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}{suffix}"),
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=quote,
        price_precision=pair_config.price_precision,
        size_precision=pair_config.size_precision,
        price_increment=Price(pair_config.tick_size, pair_config.price_precision),
        size_increment=Quantity(pair_config.lot_size, pair_config.size_precision),
        lot_size=None,
        max_quantity=None,
        min_quantity=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )


def load_orderbook_data(
    engine: BacktestEngine,
    instrument: CurrencyPair,
    venue: str,
    symbol: str,
    date_str: str,
    data_dir: Path,
    depth: int = 200,
    max_updates: int | None = None,
    batch_size: int = 10000,
) -> int:
    """Load orderbook data from file."""
    count = 0
    batch = []

    ob_file = data_dir / f"{symbol}_Spot" / f"{date_str}_{symbol}_ob{depth}.data"
    if not ob_file.exists():
        raise FileNotFoundError(f"Order book file not found: {ob_file}")

    loader = BinanceOrderBookLoader if venue.upper() == "BINANCE" else BybitOrderBookLoader

    print(f"Loading {ob_file}...")
    for delta in loader.load_file(ob_file, instrument):
        batch.append(delta)
        count += 1
        if max_updates and count >= max_updates:
            break
        if len(batch) >= batch_size:
            engine.add_data(batch)
            batch = []
            if count % 100000 == 0:
                print(f"  Loaded {count:,} updates...")

    if batch:
        engine.add_data(batch)

    print(f"  Total: {count:,} orderbook updates")
    return count


def run_backtest(
    symbol: str,
    date_str: str,
    data_dir: Path,
    output_dir: Path,
    max_updates: int | None = None,
    latency_ms: int = 5,
    starting_usdt: float = 10000.0,
    # Fee structure
    maker_fee_pct: float = 0.0675,  # VIP1 default: 0.0675%
    taker_fee_pct: float = 0.0675,  # VIP1 default: 0.0675%
    # Strategy params
    ranging_spread_bps: float | None = None,
    breakout_spread_bps: float | None = None,
    bb_period: int = 20,
    rsi_period: int = 14,
    range_confirm_ticks: int = 2,     # Very fast entry
    breakout_confirm_ticks: int = 15, # Very slow exit - need many confirms
    max_hold_secs: int = 180,
    volume_profile_enabled: bool = True,
) -> dict:
    """Run a single backtest."""
    if symbol not in PAIR_CONFIGS:
        raise ValueError(f"Unknown symbol: {symbol}. Available: {list(PAIR_CONFIGS.keys())}")

    pair_config = PAIR_CONFIGS[symbol]
    
    # Override spreads if provided
    ranging_spread = ranging_spread_bps or pair_config.ranging_spread_bps
    breakout_spread = breakout_spread_bps or pair_config.breakout_spread_bps

    # Fee structure - configurable for different VIP tiers
    # VIP0: 0.10% maker/taker (with MNT discount)
    # VIP1: 0.0675% maker, 0.0675% taker
    maker_fee = Decimal(str(maker_fee_pct / 100))
    taker_fee = Decimal(str(taker_fee_pct / 100))

    print(f"\n{'='*60}")
    print(f"SNIPER v009.1 BACKTEST")
    print(f"{'='*60}")
    print(f"Symbol: {symbol}")
    print(f"Date: {date_str}")
    print(f"Fees: {maker_fee_pct*100:.4f}% maker, {taker_fee_pct*100:.4f}% taker")
    print(f"Ranging Spread: {ranging_spread} bps")
    print(f"Breakout Spread: {breakout_spread} bps")
    print(f"BB Period: {bb_period}")
    print(f"Volume Profile: {'ON' if volume_profile_enabled else 'OFF'}")
    print(f"{'='*60}\n")

    # Configure engine
    config = BacktestEngineConfig(
        trader_id="SNIPER-BT-001",
        logging=LoggingConfig(
            log_level="WARN",  # WARN for speed
            log_level_file="DEBUG",
            log_directory=str(output_dir),
            log_file_name="sniper_backtest.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)

    # Create instruments
    follower = create_instrument("-SPOT.BYBIT", symbol, pair_config, maker_fee, taker_fee)
    leader = create_instrument(".BINANCE_SPOT", symbol, pair_config, maker_fee, taker_fee)

    # Create latency model
    latency_model = LatencyModel(base_latency_nanos=latency_ms * 1_000_000)

    # Starting balance - need BOTH currencies for market making
    base_currency = Currency.from_str(symbol[:-4])
    # Start with significant base currency balance to allow immediate selling
    # ETH ~$3000, so 1 ETH = ~$3000. We want to be able to sell from the start.
    initial_base_amount = pair_config.max_position_qty * 2  # 2x max position
    starting_balances = [
        Money(starting_usdt, USDT),
        Money(initial_base_amount, base_currency),
    ]

    # Add venues
    engine.add_venue(
        venue=Venue("BINANCE_SPOT"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances,
        book_type=BookType.L2_MBP,
        latency_model=latency_model,
    )
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances,
        book_type=BookType.L2_MBP,
        latency_model=latency_model,
    )

    # Add instruments
    engine.add_instrument(leader)
    engine.add_instrument(follower)

    # Create strategy
    strategy_config = LeadLagMMv9SniperConfig(
        strategy_id=f"SNIPER-{symbol[:3]}",
        follower_instrument_id=follower.id,
        leader_instrument_id=leader.id,
        book_type=BookType.L2_MBP,
        book_depth=50,
        # Spreads - use tighter spreads for backtest
        ranging_spread_bps=ranging_spread,
        breakout_spread_bps=breakout_spread,
        min_spread_bps=5.0,  # Lower min for backtest (actual min ~3 bps from fees)
        # Order sizing
        order_qty=pair_config.order_qty,
        max_position_qty=pair_config.max_position_qty,
        min_order_qty=float(pair_config.lot_size),
        min_order_value_usd=5.50,
        # Indicators
        bb_period=bb_period,
        bb_std_dev=2.0,
        bb_squeeze_threshold=0.75,
        rsi_period=rsi_period,
        rsi_neutral_low=40.0,
        rsi_neutral_high=60.0,
        rsi_extreme_low=30.0,
        rsi_extreme_high=70.0,
        atr_period=14,
        atr_expansion_threshold=2.0,  # Very high - only on big expansion
        # Volume Profile
        volume_profile_enabled=volume_profile_enabled,
        volume_profile_bins=100,
        volume_profile_window=500,
        volume_profile_hvn_threshold=1.5,
        volume_profile_lvn_threshold=0.5,
        volume_profile_poc_tolerance_bps=15.0,
        # Range detection - very fast entry
        range_confirm_ticks=range_confirm_ticks,
        range_min_width_bps=2.0,    # Ultra low for backtest
        range_max_width_bps=2000.0, # Ultra high for backtest
        # Breakout detection - much less sensitive for backtest
        breakout_confirm_ticks=breakout_confirm_ticks,
        breakout_ofi_threshold=0.95,  # Essentially disable OFI-based breakout
        # Exit - very short cooldown
        exit_cooldown_secs=3,         # Very short
        exit_aggressive_threshold_bps=-15.0,
        hold_if_appreciating=True,
        max_hold_secs=max_hold_secs,
        # Inventory
        inventory_skew_enabled=True,
        inventory_skew_multiplier=0.3,
        # Logging
        log_state_changes=True,
        log_indicator_values=False,
        log_trade_signals=True,
    )

    strategy = LeadLagMMv9Sniper(config=strategy_config)
    engine.add_strategy(strategy)

    # Load data
    print("Loading leader (Binance) orderbook...")
    leader_count = load_orderbook_data(
        engine, leader, "BINANCE", symbol, date_str, data_dir,
        depth=200, max_updates=max_updates,
    )

    # For follower, we use the same file (simulating Bybit following Binance)
    # In production these would be separate files
    print("Loading follower (Bybit) orderbook...")
    follower_count = load_orderbook_data(
        engine, follower, "BINANCE", symbol, date_str, data_dir,
        depth=200, max_updates=max_updates,
    )

    print(f"\nTotal data loaded: {leader_count + follower_count:,} updates")
    print("\nRunning backtest...")

    # Run backtest
    import time
    start_time = time.time()
    engine.run()
    elapsed = time.time() - start_time

    print(f"\nBacktest completed in {elapsed:.1f}s")

    # Generate reports
    fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()
    
    # Account report needs venue
    try:
        account_report = engine.trader.generate_account_report(venue=BYBIT_VENUE)
    except Exception:
        account_report = None

    # Save reports
    output_dir.mkdir(parents=True, exist_ok=True)
    if fills_report is not None:
        fills_report.to_csv(output_dir / "fills_report.csv", index=False)
    if positions_report is not None:
        positions_report.to_csv(output_dir / "positions_report.csv", index=False)
    if account_report is not None:
        account_report.to_csv(output_dir / "account_report.csv", index=False)

    # Calculate P&L
    total_fills = len(fills_report) if fills_report is not None else 0
    buy_fills = 0
    sell_fills = 0
    if fills_report is not None and len(fills_report) > 0:
        # Column is 'side' not 'order_side' in NautilusTrader reports
        if "side" in fills_report.columns:
            buy_fills = len(fills_report[fills_report["side"] == "BUY"])
            sell_fills = len(fills_report[fills_report["side"] == "SELL"])

    # Get final account balance from cache
    final_usdt = starting_usdt  # Default
    try:
        accounts = engine.cache.accounts()
        if accounts:
            account = accounts[0]
            balances = account.balances()
            usdt_balance = balances.get(USDT)
            if usdt_balance:
                if hasattr(usdt_balance, 'total'):
                    final_usdt = float(usdt_balance.total.as_decimal())
                elif hasattr(usdt_balance, 'as_decimal'):
                    final_usdt = float(usdt_balance.as_decimal())
    except Exception:
        pass

    pnl = final_usdt - starting_usdt
    pnl_pct = (pnl / starting_usdt) * 100

    # Summary
    summary = {
        "symbol": symbol,
        "date": date_str,
        "ranging_spread_bps": ranging_spread,
        "breakout_spread_bps": breakout_spread,
        "bb_period": bb_period,
        "volume_profile": volume_profile_enabled,
        "total_fills": total_fills,
        "buy_fills": buy_fills,
        "sell_fills": sell_fills,
        "starting_usdt": starting_usdt,
        "final_usdt": final_usdt,
        "pnl_usdt": pnl,
        "pnl_pct": pnl_pct,
        "runtime_secs": elapsed,
        "data_updates": leader_count + follower_count,
    }

    # Save summary
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    # Print results
    print(f"\n{'='*60}")
    print(f"BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"Total Fills: {total_fills}")
    print(f"  Buy: {buy_fills}")
    print(f"  Sell: {sell_fills}")
    print(f"Starting USDT: ${starting_usdt:,.2f}")
    print(f"Final USDT: ${final_usdt:,.2f}")
    print(f"P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)")
    print(f"{'='*60}")

    engine.dispose()
    return summary


def main():
    parser = argparse.ArgumentParser(description="Sniper v009.1 Backtest")
    parser.add_argument("--symbol", "-s", default="SUIUSDT", help="Trading pair (default: SUIUSDT)")
    parser.add_argument("--date", "-d", default="2026-01-30", help="Date (YYYY-MM-DD)")
    parser.add_argument("--data-dir", default="data/ob_data", help="Data directory")
    parser.add_argument("--output-dir", default="backtest_results/sniper_v009", help="Output directory")
    parser.add_argument("--max-updates", "-m", type=int, default=None, help="Max orderbook updates (for testing)")
    parser.add_argument("--latency-ms", type=int, default=5, help="Simulated latency (ms)")
    parser.add_argument("--starting-usdt", type=float, default=10000.0, help="Starting balance")
    # Strategy params
    parser.add_argument("--ranging-spread-bps", type=float, default=None, help="Ranging spread (bps)")
    parser.add_argument("--breakout-spread-bps", type=float, default=None, help="Breakout spread (bps)")
    parser.add_argument("--bb-period", type=int, default=20, help="Bollinger Bands period")
    parser.add_argument("--rsi-period", type=int, default=14, help="RSI period")
    parser.add_argument("--range-confirm-ticks", type=int, default=8, help="Ticks to confirm range")
    parser.add_argument("--breakout-confirm-ticks", type=int, default=3, help="Ticks to confirm breakout")
    parser.add_argument("--max-hold-secs", type=int, default=180, help="Max hold time (secs)")
    parser.add_argument("--no-volume-profile", action="store_true", help="Disable Volume Profile")

    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir) / args.symbol / args.date

    run_backtest(
        symbol=args.symbol,
        date_str=args.date,
        data_dir=data_dir,
        output_dir=output_dir,
        max_updates=args.max_updates,
        latency_ms=args.latency_ms,
        starting_usdt=args.starting_usdt,
        ranging_spread_bps=args.ranging_spread_bps,
        breakout_spread_bps=args.breakout_spread_bps,
        bb_period=args.bb_period,
        rsi_period=args.rsi_period,
        range_confirm_ticks=args.range_confirm_ticks,
        breakout_confirm_ticks=args.breakout_confirm_ticks,
        max_hold_secs=args.max_hold_secs,
        volume_profile_enabled=not args.no_volume_profile,
    )


if __name__ == "__main__":
    main()
