#!/usr/bin/env python3
"""Multi-pair production parameter backtest for ETH/LINK/AVAX.

Tests the exact production parameters before deployment.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT, ETH, LINK, AVAX
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId
from nautilus_trader.model.venues import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

from examples.backtest.questdb_orderbook_loader import QuestDbConfig, load_questdb
from nautilus_trader.backtest.models import CompetitionAwareFillModel, LatencyModel
from strategy.lead_lag_bybit_binance_mm_v003_primer import LeadLagMMv3Primer, LeadLagMMv3PrimerConfig


# Production parameters for each pair
PAIR_CONFIGS = {
    "ETH": {
        "symbol": "ETHUSDT",
        "base_currency": ETH,
        "order_qty": Decimal("0.02"),
        "min_order_qty": Decimal("0.01"),
        "max_position_qty": Decimal("0.5"),
        "guard_threshold_bps": Decimal("10.0"),
        "spread_bps": Decimal("8.0"),
        "min_profit_bps": Decimal("0.5"),
        "ofi_max_bps": Decimal("2.0"),
        "refresh_interval": 2000,
        "min_quote_lifetime_ms": 800,
        "price_precision": 2,
        "size_precision": 5,
        "price_increment": "0.01",
        "size_increment": "0.00001",
    },
    "LINK": {
        "symbol": "LINKUSDT",
        "base_currency": LINK,
        "order_qty": Decimal("5.0"),
        "min_order_qty": Decimal("2.5"),
        "max_position_qty": Decimal("50.0"),
        "guard_threshold_bps": Decimal("12.0"),
        "spread_bps": Decimal("12.0"),
        "min_profit_bps": Decimal("0.8"),
        "ofi_max_bps": Decimal("3.0"),
        "refresh_interval": 2500,
        "min_quote_lifetime_ms": 1000,
        "price_precision": 4,
        "size_precision": 2,
        "price_increment": "0.0001",
        "size_increment": "0.01",
    },
    "AVAX": {
        "symbol": "AVAXUSDT",
        "base_currency": AVAX,
        "order_qty": Decimal("5.0"),
        "min_order_qty": Decimal("2.5"),
        "max_position_qty": Decimal("100.0"),
        "guard_threshold_bps": Decimal("12.0"),
        "spread_bps": Decimal("15.0"),
        "min_profit_bps": Decimal("1.0"),
        "ofi_max_bps": Decimal("4.0"),
        "refresh_interval": 3000,
        "min_quote_lifetime_ms": 1200,
        "price_precision": 4,
        "size_precision": 3,
        "price_increment": "0.0001",
        "size_increment": "0.001",
    },
}


def create_instrument(symbol: str, base_currency, config: dict, venue_str: str):
    """Create a CurrencyPair instrument."""
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}.{venue_str}"),
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=USDT,
        price_precision=config["price_precision"],
        size_precision=config["size_precision"],
        price_increment=Price.from_str(config["price_increment"]),
        size_increment=Quantity.from_str(config["size_increment"]),
        lot_size=Quantity.from_str(config["size_increment"]),
        max_quantity=Quantity.from_str("1000000"),
        min_quantity=Quantity.from_str(config["size_increment"]),
        max_price=Price.from_str("1000000"),
        min_price=Price.from_str(config["price_increment"]),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),  # Binance
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


def create_bybit_instrument(symbol: str, base_currency, config: dict):
    """Create Bybit instrument with VIP0+MNT fees."""
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}-SPOT.BYBIT"),
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=USDT,
        price_precision=config["price_precision"],
        size_precision=config["size_precision"],
        price_increment=Price.from_str(config["price_increment"]),
        size_increment=Quantity.from_str(config["size_increment"]),
        lot_size=Quantity.from_str(config["size_increment"]),
        max_quantity=Quantity.from_str("1000000"),
        min_quantity=Quantity.from_str(config["size_increment"]),
        max_price=Price.from_str("1000000"),
        min_price=Price.from_str(config["price_increment"]),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.00075"),  # VIP0 + MNT discount
        taker_fee=Decimal("0.00075"),
        ts_event=0,
        ts_init=0,
    )


def backtest_pair(pair_name: str, date: str = "2026-02-02", starting_usdt: float = 5000):
    """Backtest a single pair with production parameters."""
    config = PAIR_CONFIGS[pair_name]
    symbol = config["symbol"]
    
    print(f"\n{'='*80}")
    print(f"BACKTESTING {pair_name} ({symbol}) - {date}")
    print(f"{'='*80}")
    
    # Create instruments
    leader = create_instrument(symbol, config["base_currency"], config, "BINANCE")
    follower = create_bybit_instrument(symbol, config["base_currency"], config)
    
    # Load data
    print(f"Loading Binance {symbol} data...")
    qdb_cfg = QuestDbConfig()
    leader_deltas = list(load_questdb(qdb_cfg, leader, "BINANCE", symbol, date))
    print(f"Loaded {len(leader_deltas):,} leader delta batches")
    
    print(f"Loading Bybit {symbol} data...")
    follower_deltas = list(load_questdb(qdb_cfg, follower, "BYBIT", symbol, date))
    print(f"Loaded {len(follower_deltas):,} follower delta batches")
    
    if not leader_deltas or not follower_deltas:
        print(f"ERROR: No data loaded for {symbol}!")
        return None
    
    # Create engine
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId(f"{pair_name}-BACKTEST-001"),
            logging=LoggingConfig(log_level="ERROR"),  # Reduce noise
        )
    )
    
    # Add venues
    engine.add_venue(
        venue=Venue("BINANCE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(1, USDT)],
    )
    
    engine.add_venue(
        venue=Venue("BYBIT"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(starting_usdt, USDT)],
        fill_model=CompetitionAwareFillModel(),
        latency_model=LatencyModel(10),
    )
    
    # Add instruments
    engine.add_instrument(leader)
    engine.add_instrument(follower)
    
    # Add data
    all_data = []
    all_data.extend(leader_deltas)
    all_data.extend(follower_deltas)
    all_data.sort(key=lambda d: d.ts_init)
    engine.add_data(all_data)
    
    # Create strategy
    strategy_config = LeadLagMMv3PrimerConfig(
        leader_instrument_id=leader.id,
        follower_instrument_id=follower.id,
        order_qty=config["order_qty"],
        min_order_qty=config["min_order_qty"],
        max_position_qty=config["max_position_qty"],
        guard_threshold_bps=config["guard_threshold_bps"],
        spread_bps=config["spread_bps"],
        min_profit_bps=config["min_profit_bps"],
        ofi_enabled=True,
        ofi_max_bps=config["ofi_max_bps"],
        internal_price_delta_limit=Decimal("0.0"),
        quote_refresh_interval_ms=config["refresh_interval"],
        min_quote_lifetime_ms=config["min_quote_lifetime_ms"],
    )
    
    strategy = LeadLagMMv3Primer(config=strategy_config)
    engine.add_strategy(strategy)
    
    # Run
    print("Running backtest...")
    engine.run()
    
    # Get results
    account = engine.trader.generate_account_report(Venue("BYBIT"))
    fills = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()
    
    # Parse results from account state
    account_state = engine.trader.generate_account_report(Venue("BYBIT"))
    pnl_usdt = 0.0
    pnl_base = 0.0
    
    # Calculate P&L from fills
    total_buy_value = 0.0
    total_sell_value = 0.0
    total_fees = 0.0
    
    if not fills.empty:
        # Print columns for debugging
        print(f"Fills columns: {list(fills.columns)}")
        
        for _, fill in fills.iterrows():
            # Handle different column name formats
            price = float(fill.get('last_px', fill.get('price', fill.get('avg_px', 0))))
            qty = float(fill.get('last_qty', fill.get('quantity', fill.get('filled_qty', 0))))
            commission = float(fill.get('commission', fill.get('fees', 0)))
            
            if fill['side'] == 'BUY':
                total_buy_value += abs(price * qty)
                total_fees += abs(commission)
            else:  # SELL
                total_sell_value += abs(price * qty)
                total_fees += abs(commission)
    
    pnl_usdt = total_sell_value - total_buy_value - total_fees
    
    result = {
        "pair": pair_name,
        "symbol": symbol,
        "fills": len(fills),
        "pnl_usdt": pnl_usdt if pnl_usdt != 0 else 0.0,
        "pnl_base": pnl_base,
        "starting_usdt": starting_usdt,
    }
    
    print(f"\n{'-'*80}")
    print(f"RESULTS - {pair_name}")
    print(f"{'-'*80}")
    print(f"Total fills: {len(fills)}")
    print(f"Total positions: {len(positions)}")
    print(f"PnL USDT: ${pnl_usdt:.2f}")
    print(f"Total buy value: ${total_buy_value:.2f}")
    print(f"Total sell value: ${total_sell_value:.2f}")
    print(f"Total fees: ${total_fees:.2f}")
    
    return result


def main():
    """Run backtests for all pairs."""
    print("="*80)
    print("PRODUCTION PARAMETER BACKTEST - ETH/LINK/AVAX")
    print("Date: 2026-02-02")
    print("Fee: VIP0 + MNT (0.075% maker/taker)")
    print("="*80)
    
    results = []
    for pair_name in ["ETH", "LINK", "AVAX"]:
        result = backtest_pair(pair_name)
        if result:
            results.append(result)
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY - ALL PAIRS")
    print("="*80)
    print(f"{'Pair':<8} {'Fills':<8} {'PnL USDT':<15} {'PnL %':<10} {'Status'}")
    print("-"*80)
    
    for r in results:
        pnl_pct = (r['pnl_usdt'] / r['starting_usdt'] * 100) if r['pnl_usdt'] is not None else 0
        status = "✅ PASS" if r['fills'] >= 10 and r['pnl_usdt'] is not None and r['pnl_usdt'] > -100 else "⚠️  REVIEW"
        print(f"{r['pair']:<8} {r['fills']:<8} ${r['pnl_usdt']:<14.2f} {pnl_pct:>8.2f}%  {status}")
    
    print("\n" + "="*80)
    print("Next steps:")
    print("- If all PASS: Deploy to production")
    print("- If NEEDS TUNING: Run parameter sweep")
    print("="*80)


if __name__ == "__main__":
    main()
