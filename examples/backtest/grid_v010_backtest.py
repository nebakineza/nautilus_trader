#!/usr/bin/env python3
"""
Spot Grid Bot v010 - Backtest Runner

Backtests the Bybit-style grid bot strategy.
"""

import argparse
from decimal import Decimal
from pathlib import Path
import json

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import OrderBookDelta, OrderBookDeltas, QuoteTick
from nautilus_trader.model.enums import (
    AccountType,
    BookAction,
    BookType,
    OmsType,
    OrderSide,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from strategy.spot_grid_bot_v010 import SpotGridBot, SpotGridBotConfig


# =============================================================================
# PAIR CONFIGURATIONS
# =============================================================================
PAIR_CONFIGS = {
    "LINKUSDT": {
        "tick_size": Decimal("0.001"),
        "lot_size": Decimal("0.01"),
        "price_precision": 3,
        "size_precision": 2,
        "base_price": 21.0,
        "order_qty": 1.0,
        "max_position": 10.0,
    },
    "AVAXUSDT": {
        "tick_size": Decimal("0.001"),
        "lot_size": Decimal("0.01"),
        "price_precision": 3,
        "size_precision": 2,
        "base_price": 35.0,
        "order_qty": 0.5,
        "max_position": 5.0,
    },
    "SUIUSDT": {
        "tick_size": Decimal("0.0001"),
        "lot_size": Decimal("0.01"),
        "price_precision": 4,
        "size_precision": 2,
        "base_price": 5.0,
        "order_qty": 5.0,
        "max_position": 50.0,
    },
    "SOLUSDT": {
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("0.01"),
        "price_precision": 2,
        "size_precision": 2,
        "base_price": 200.0,
        "order_qty": 0.1,
        "max_position": 1.0,
    },
    "DOGEUSDT": {
        "tick_size": Decimal("0.00001"),
        "lot_size": Decimal("1"),
        "price_precision": 5,
        "size_precision": 0,
        "base_price": 0.40,
        "order_qty": 50.0,
        "max_position": 500.0,
    },
    "XRPUSDT": {
        "tick_size": Decimal("0.0001"),
        "lot_size": Decimal("0.01"),
        "price_precision": 4,
        "size_precision": 2,
        "base_price": 2.5,
        "order_qty": 10.0,
        "max_position": 100.0,
    },
    "ETHUSDT": {
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("0.00001"),
        "price_precision": 2,
        "size_precision": 5,
        "base_price": 3000.0,
        "order_qty": 0.01,
        "max_position": 0.1,
    },
    "BTCUSDT": {
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("0.00001"),
        "price_precision": 2,
        "size_precision": 5,
        "base_price": 100000.0,
        "order_qty": 0.0005,
        "max_position": 0.005,
    },
}

BYBIT_VENUE = Venue("BYBIT")


def create_instrument(
    symbol: str,
    config: dict,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> CurrencyPair:
    """Create a CurrencyPair instrument."""
    base_str = symbol[:-4]  # Remove USDT
    base = Currency.from_str(base_str)

    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}-SPOT.BYBIT"),
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=USDT,
        price_precision=config["price_precision"],
        size_precision=config["size_precision"],
        price_increment=Price(config["tick_size"], config["price_precision"]),
        size_increment=Quantity(config["lot_size"], config["size_precision"]),
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
    data_path: Path,
    max_updates: int | None = None,
) -> tuple[int, list[QuoteTick]]:
    """Load orderbook data and generate quote ticks for the grid bot."""
    if not data_path.exists():
        raise FileNotFoundError(f"Data file not found: {data_path}")
    
    print(f"Loading {data_path}...")
    
    quote_ticks = []
    count = 0
    
    with open(data_path, "r") as f:
        for line in f:
            if max_updates and count >= max_updates:
                break
            
            try:
                data = json.loads(line.strip())
                ob_data = data.get("data", {})
                bids = ob_data.get("b", [])
                asks = ob_data.get("a", [])
                ts = ob_data.get("ts", 0) * 1_000_000  # ms to ns
                
                if not bids or not asks:
                    continue
                
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                bid_size = float(bids[0][1])
                ask_size = float(asks[0][1])
                
                if best_bid <= 0 or best_ask <= 0:
                    continue
                
                # Create quote tick
                quote = QuoteTick(
                    instrument_id=instrument.id,
                    bid_price=instrument.make_price(best_bid),
                    ask_price=instrument.make_price(best_ask),
                    bid_size=instrument.make_qty(bid_size),
                    ask_size=instrument.make_qty(ask_size),
                    ts_event=ts,
                    ts_init=ts,
                )
                quote_ticks.append(quote)
                count += 1
                
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    
    # Add data to engine
    engine.add_data(quote_ticks)
    
    print(f"  Loaded {count:,} quote ticks")
    return count, quote_ticks


def run_backtest(
    symbol: str,
    date_str: str,
    num_grids: int = 10,
    grid_spread_bps: float = 100.0,
    bb_period: int = 50,
    bb_std_dev: float = 2.0,
    max_updates: int | None = None,
    starting_usdt: float = 10000.0,
    maker_fee_pct: float = 0.000675,  # VIP1
    taker_fee_pct: float = 0.000675,
    latency_ms: int = 50,
) -> dict:
    """Run a single grid bot backtest."""
    
    if symbol not in PAIR_CONFIGS:
        raise ValueError(f"Unknown symbol: {symbol}. Available: {list(PAIR_CONFIGS.keys())}")
    
    pair_config = PAIR_CONFIGS[symbol]
    
    # Output directory
    output_dir = Path(f"backtest_results/grid_v010/{symbol}/{date_str}")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*60}")
    print(f"SPOT GRID BOT v010 BACKTEST")
    print(f"{'='*60}")
    print(f"Symbol: {symbol}")
    print(f"Date: {date_str}")
    print(f"Grids: {num_grids}")
    print(f"Grid Spread: {grid_spread_bps} bps")
    print(f"BB Period: {bb_period}")
    print(f"BB Std Dev: {bb_std_dev}")
    print(f"Fees: {maker_fee_pct*100:.4f}% maker, {taker_fee_pct*100:.4f}% taker")
    print(f"{'='*60}\n")
    
    # Configure engine
    config = BacktestEngineConfig(
        trader_id="GRID-BT-001",
        logging=LoggingConfig(
            log_level="WARN",
            log_level_file="DEBUG",
            log_directory=str(output_dir),
            log_file_name="grid_backtest.log",
            clear_log_file=True,
        ),
    )
    engine = BacktestEngine(config=config)
    
    # Create instrument
    maker_fee = Decimal(str(maker_fee_pct))
    taker_fee = Decimal(str(taker_fee_pct))
    instrument = create_instrument(symbol, pair_config, maker_fee, taker_fee)
    
    # Latency model
    latency_model = LatencyModel(base_latency_nanos=latency_ms * 1_000_000)
    
    # Starting balances
    base_currency = Currency.from_str(symbol[:-4])
    initial_base = pair_config["max_position"] * 2
    starting_balances = [
        Money(starting_usdt, USDT),
        Money(initial_base, base_currency),
    ]
    
    # Add venue
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances,
        book_type=BookType.L1_MBP,  # Use L1 since we're feeding quote ticks
        latency_model=latency_model,
    )
    
    # Add instrument
    engine.add_instrument(instrument)
    
    # Load data
    data_path = Path(f"data/ob_data/{symbol}_Spot/{date_str}_{symbol}_ob200.data")
    if not data_path.exists():
        # Try bybit format
        data_path = Path(f"data/ob_data/{symbol}_Spot/{date_str}_{symbol}_bybit_ob50.data")
    
    count, quote_ticks = load_orderbook_data(engine, instrument, data_path, max_updates)
    
    if count == 0:
        print("No data loaded!")
        return {"error": "No data"}
    
    # Create strategy
    strategy_config = SpotGridBotConfig(
        strategy_id="GRID-001",
        instrument_id=f"{symbol}-SPOT.BYBIT",
        num_grids=num_grids,
        grid_spread_bps=grid_spread_bps,
        use_auto_range=True,
        bb_period=bb_period,
        bb_std_dev=bb_std_dev,
        range_recalc_interval=500,
        order_qty=pair_config["order_qty"],
        max_position_qty=pair_config["max_position"],
        min_order_qty=float(pair_config["lot_size"]),
        min_order_value_usd=5.50,
        max_open_orders=num_grids * 2,
        pause_on_trend=True,
        trend_threshold_bps=300.0,
        log_grid_updates=True,
        log_fills=True,
        log_range_calcs=True,
    )
    
    strategy = SpotGridBot(config=strategy_config)
    engine.add_strategy(strategy)
    
    # Run backtest
    print("Running backtest...")
    engine.run()
    
    # Get results
    fills = engine.cache.orders_filled()
    
    # Calculate P&L
    account = engine.cache.account_for_venue(BYBIT_VENUE)
    final_balances = account.balances()
    
    final_usdt = float(final_balances.get(USDT, Money(0, USDT)).total)
    final_base = float(final_balances.get(base_currency, Money(0, base_currency)).total)
    
    # Use actual prices from data
    if quote_ticks:
        final_price = float(quote_ticks[-1].bid_price)
        initial_price = float(quote_ticks[0].bid_price)
    else:
        final_price = initial_price = pair_config["base_price"]
    
    initial_value = starting_usdt + (initial_base * initial_price)
    final_value = final_usdt + (final_base * final_price)
    pnl = final_value - initial_value
    pnl_pct = (pnl / initial_value) * 100
    
    print(f"\n{'='*60}")
    print("BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"Total Fills: {len(fills)}")
    print(f"  Buys: {sum(1 for o in fills if o.side == OrderSide.BUY)}")
    print(f"  Sells: {sum(1 for o in fills if o.side == OrderSide.SELL)}")
    print(f"Grid Cycles: {strategy.completed_cycles}")
    print(f"Grid Profit: ${strategy.total_grid_profits:.2f}")
    print(f"Initial: ${initial_value:,.2f}")
    print(f"Final: ${final_value:,.2f}")
    print(f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
    print(f"{'='*60}")
    
    # Generate reports
    engine.generate_order_fills_report(output_dir / "fills_report.csv")
    engine.generate_positions_report(output_dir / "positions_report.csv")
    engine.generate_account_report(output_dir / "account_report.csv")
    
    # Save summary
    summary = {
        "symbol": symbol,
        "date": date_str,
        "num_grids": num_grids,
        "grid_spread_bps": grid_spread_bps,
        "total_fills": len(fills),
        "buy_fills": sum(1 for o in fills if o.side == OrderSide.BUY),
        "sell_fills": sum(1 for o in fills if o.side == OrderSide.SELL),
        "grid_cycles": strategy.completed_cycles,
        "grid_profit": strategy.total_grid_profits,
        "initial_value": initial_value,
        "final_value": final_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }
    
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    engine.dispose()
    
    return summary


def main():
    parser = argparse.ArgumentParser(description="Grid Bot v010 Backtest")
    parser.add_argument("--symbol", type=str, default="LINKUSDT", help="Trading pair")
    parser.add_argument("--date", type=str, default="2026-02-01", help="Date for backtest")
    parser.add_argument("--num-grids", type=int, default=10, help="Number of grid levels")
    parser.add_argument("--grid-spread-bps", type=float, default=100.0, help="Spread between grids in bps")
    parser.add_argument("--bb-period", type=int, default=50, help="Bollinger Band period")
    parser.add_argument("--bb-std-dev", type=float, default=2.0, help="BB standard deviations")
    parser.add_argument("--max-updates", type=int, default=None, help="Max orderbook updates")
    parser.add_argument("--starting-usdt", type=float, default=10000.0, help="Starting USDT")
    
    args = parser.parse_args()
    
    run_backtest(
        symbol=args.symbol,
        date_str=args.date,
        num_grids=args.num_grids,
        grid_spread_bps=args.grid_spread_bps,
        bb_period=args.bb_period,
        bb_std_dev=args.bb_std_dev,
        max_updates=args.max_updates,
        starting_usdt=args.starting_usdt,
    )


if __name__ == "__main__":
    main()
