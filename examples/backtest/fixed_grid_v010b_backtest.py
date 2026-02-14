#!/usr/bin/env python3
"""
Fixed Grid Bot v010b - Backtest Runner

Simple Bybit-style fixed range grid trading.
"""

import argparse
from decimal import Decimal
from pathlib import Path
import json

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import (
    AccountType,
    BookType,
    OmsType,
    OrderSide,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from strategy.fixed_grid_bot_v010b import FixedGridBot, FixedGridBotConfig


PAIR_CONFIGS = {
    "LINKUSDT": {
        "tick_size": Decimal("0.001"),
        "lot_size": Decimal("0.01"),
        "price_precision": 3,
        "size_precision": 2,
        "order_qty": 1.0,
        "max_position": 10.0,
    },
    "AVAXUSDT": {
        "tick_size": Decimal("0.001"),
        "lot_size": Decimal("0.01"),
        "price_precision": 3,
        "size_precision": 2,
        "order_qty": 0.5,
        "max_position": 5.0,
    },
    "SUIUSDT": {
        "tick_size": Decimal("0.0001"),
        "lot_size": Decimal("0.01"),
        "price_precision": 4,
        "size_precision": 2,
        "order_qty": 5.0,
        "max_position": 50.0,
    },
    "SOLUSDT": {
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("0.01"),
        "price_precision": 2,
        "size_precision": 2,
        "order_qty": 0.1,
        "max_position": 1.0,
    },
    "DOGEUSDT": {
        "tick_size": Decimal("0.00001"),
        "lot_size": Decimal("1"),
        "price_precision": 5,
        "size_precision": 0,
        "order_qty": 50.0,
        "max_position": 500.0,
    },
}

BYBIT_VENUE = Venue("BYBIT")


def create_instrument(symbol: str, config: dict, maker_fee: Decimal, taker_fee: Decimal) -> CurrencyPair:
    """Create instrument."""
    base_str = symbol[:-4]
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


def load_quote_ticks(engine: BacktestEngine, instrument: CurrencyPair, data_path: Path, max_updates: int | None = None) -> tuple[int, list[QuoteTick]]:
    """Load orderbook data as quote ticks."""
    if not data_path.exists():
        raise FileNotFoundError(f"Data not found: {data_path}")
    
    print(f"Loading {data_path}...")
    
    quote_ticks = []
    count = 0
    
    with open(data_path) as f:
        for line in f:
            if max_updates and count >= max_updates:
                break
            
            try:
                data = json.loads(line.strip())
                ob = data.get("data", {})
                bids = ob.get("b", [])
                asks = ob.get("a", [])
                ts = ob.get("ts", 0) * 1_000_000
                
                if not bids or not asks:
                    continue
                
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                bid_size = float(bids[0][1])
                ask_size = float(asks[0][1])
                
                if best_bid <= 0 or best_ask <= 0:
                    continue
                
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
    
    engine.add_data(quote_ticks)
    print(f"  Loaded {count:,} quotes")
    return count, quote_ticks


def run_backtest(
    symbol: str,
    date_str: str,
    num_grids: int = 5,
    range_pct: float = 2.0,
    max_updates: int | None = None,
    starting_usdt: float = 10000.0,
    maker_fee_pct: float = 0.000675,
    taker_fee_pct: float = 0.000675,
) -> dict:
    """Run grid bot backtest."""
    
    if symbol not in PAIR_CONFIGS:
        raise ValueError(f"Unknown symbol: {symbol}")
    
    pair_config = PAIR_CONFIGS[symbol]
    
    output_dir = Path(f"backtest_results/grid_v010b/{symbol}/{date_str}")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*60}")
    print(f"FIXED GRID BOT v010b BACKTEST")
    print(f"{'='*60}")
    print(f"Symbol: {symbol}")
    print(f"Date: {date_str}")
    print(f"Grids: {num_grids} each side")
    print(f"Range: {range_pct}%")
    print(f"Grid interval: {range_pct*2/(num_grids*2):.2f}% = {range_pct*2*100/(num_grids*2):.0f} bps")
    print(f"Fees: {maker_fee_pct*100:.4f}% = {maker_fee_pct*10000:.1f} bps")
    print(f"{'='*60}\n")
    
    # Engine config
    config = BacktestEngineConfig(
        trader_id="GRID-BT-001",
        logging=LoggingConfig(
            log_level="INFO",
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
        book_type=BookType.L1_MBP,
        latency_model=LatencyModel(base_latency_nanos=50_000_000),
    )
    
    engine.add_instrument(instrument)
    
    # Load data
    data_path = Path(f"data/ob_data/{symbol}_Spot/{date_str}_{symbol}_ob200.data")
    if not data_path.exists():
        data_path = Path(f"data/ob_data/{symbol}_Spot/{date_str}_{symbol}_bybit_ob50.data")
    
    count, quotes = load_quote_ticks(engine, instrument, data_path, max_updates)
    
    if count == 0:
        print("No data!")
        return {"error": "No data"}
    
    # Create strategy
    strategy_config = FixedGridBotConfig(
        strategy_id="GRID-001",
        instrument_id=f"{symbol}-SPOT.BYBIT",
        num_grids=num_grids,
        range_pct=range_pct,
        order_qty=pair_config["order_qty"],
        max_position_qty=pair_config["max_position"],
        min_order_value_usd=5.50,
        warmup_ticks=10,
        log_level=1,
    )
    
    strategy = FixedGridBot(config=strategy_config)
    engine.add_strategy(strategy)
    
    # Run
    print("Running backtest...")
    engine.run()
    
    # Results
    fills = engine.cache.orders_filled()
    account = engine.cache.account_for_venue(BYBIT_VENUE)
    final_balances = account.balances()
    
    final_usdt = float(final_balances.get(USDT, Money(0, USDT)).total)
    final_base = float(final_balances.get(base_currency, Money(0, base_currency)).total)
    
    if quotes:
        final_price = float(quotes[-1].bid_price)
        initial_price = float(quotes[0].bid_price)
    else:
        final_price = initial_price = 0
    
    initial_value = starting_usdt + (initial_base * initial_price)
    final_value = final_usdt + (final_base * final_price)
    pnl = final_value - initial_value
    pnl_pct = (pnl / initial_value) * 100 if initial_value > 0 else 0
    
    # Grid metrics
    grid_interval_bps = (range_pct * 2 * 100) / (num_grids * 2)
    fee_bps = maker_fee_pct * 10000 * 2  # RT fee
    net_bps_per_cycle = grid_interval_bps - fee_bps
    estimated_profit = strategy.grid_cycles * net_bps_per_cycle * strategy.order_qty * strategy.mid_price / 10000
    
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"Fills: {len(fills)} (Buys: {strategy.total_buys}, Sells: {strategy.total_sells})")
    print(f"Grid cycles: {strategy.grid_cycles:.1f}")
    print(f"Grid interval: {grid_interval_bps:.1f} bps")
    print(f"RT fees: {fee_bps:.1f} bps")
    print(f"Net per cycle: {net_bps_per_cycle:.1f} bps")
    print(f"Estimated grid profit: ${estimated_profit:.2f}")
    print(f"Initial value: ${initial_value:,.2f}")
    print(f"Final value: ${final_value:,.2f}")
    print(f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
    print(f"{'='*60}")
    
    # Save reports
    engine.generate_order_fills_report(output_dir / "fills_report.csv")
    engine.generate_positions_report(output_dir / "positions_report.csv")
    engine.generate_account_report(output_dir / "account_report.csv")
    
    summary = {
        "symbol": symbol,
        "date": date_str,
        "num_grids": num_grids,
        "range_pct": range_pct,
        "grid_interval_bps": grid_interval_bps,
        "total_fills": len(fills),
        "buy_fills": strategy.total_buys,
        "sell_fills": strategy.total_sells,
        "grid_cycles": strategy.grid_cycles,
        "estimated_profit": estimated_profit,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }
    
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    engine.dispose()
    return summary


def main():
    parser = argparse.ArgumentParser(description="Fixed Grid Bot v010b Backtest")
    parser.add_argument("--symbol", type=str, default="LINKUSDT")
    parser.add_argument("--date", type=str, default="2026-02-01")
    parser.add_argument("--num-grids", type=int, default=5)
    parser.add_argument("--range-pct", type=float, default=2.0)
    parser.add_argument("--max-updates", type=int, default=None)
    parser.add_argument("--starting-usdt", type=float, default=10000.0)
    
    args = parser.parse_args()
    
    run_backtest(
        symbol=args.symbol,
        date_str=args.date,
        num_grids=args.num_grids,
        range_pct=args.range_pct,
        max_updates=args.max_updates,
        starting_usdt=args.starting_usdt,
    )


if __name__ == "__main__":
    main()
