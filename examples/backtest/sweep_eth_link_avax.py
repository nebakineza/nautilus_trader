#!/usr/bin/env python3
"""Parameter sweep for ETH/LINK/AVAX to find optimal configurations."""
import sys
import json
from decimal import Decimal
from pathlib import Path
from itertools import product

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


def create_instrument(symbol: str, base_currency, config: dict, venue_str: str):
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
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


def create_bybit_instrument(symbol: str, base_currency, config: dict):
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
        maker_fee=Decimal("0.00075"),  # VIP0 + MNT
        taker_fee=Decimal("0.00075"),
        ts_event=0,
        ts_init=0,
    )


def test_params(pair_name: str, config: dict, params: dict, data_cache: dict):
    """Test a parameter set for a pair."""
    symbol = config["symbol"]
    
    # Create instruments
    leader = create_instrument(symbol, config["base_currency"], config, "BINANCE")
    follower = create_bybit_instrument(symbol, config["base_currency"], config)
    
    # Create engine
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId(f"{pair_name}-SWEEP-001"),
            logging=LoggingConfig(log_level="ERROR"),
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
        starting_balances=[Money(5000, USDT)],
        fill_model=CompetitionAwareFillModel(),
        latency_model=LatencyModel(10),
    )
    
    engine.add_instrument(leader)
    engine.add_instrument(follower)
    
    # Add data from cache
    engine.add_data(data_cache[pair_name])
    
    # Create strategy with test params
    strategy_config = LeadLagMMv3PrimerConfig(
        leader_instrument_id=leader.id,
        follower_instrument_id=follower.id,
        order_qty=params["order_qty"],
        min_order_qty=params["order_qty"] / 2,
        max_position_qty=params["max_position_qty"],
        guard_threshold_bps=params["guard_threshold_bps"],
        spread_bps=params["spread_bps"],
        min_profit_bps=params["min_profit_bps"],
        ofi_enabled=True,
        ofi_max_bps=Decimal("3.0"),
        internal_price_delta_limit=Decimal("0.0"),
        quote_refresh_interval_ms=2000,
        min_quote_lifetime_ms=800,
    )
    
    strategy = LeadLagMMv3Primer(config=strategy_config)
    engine.add_strategy(strategy)
    
    # Run
    try:
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        
        # Calculate P&L
        total_buy_value = 0.0
        total_sell_value = 0.0
        
        if not fills.empty:
            for _, fill in fills.iterrows():
                price = float(fill.get('price', fill.get('avg_px', 0)))
                qty = float(fill.get('quantity', fill.get('filled_qty', 0)))
                
                if fill['side'] == 'BUY':
                    total_buy_value += abs(price * qty)
                else:
                    total_sell_value += abs(price * qty)
        
        pnl = total_sell_value - total_buy_value
        
        return {
            "params": params,
            "fills": len(fills),
            "pnl": pnl,
            "buy_value": total_buy_value,
            "sell_value": total_sell_value,
        }
    except Exception as e:
        return {
            "params": params,
            "fills": 0,
            "pnl": -99999,
            "error": str(e),
        }


def sweep_pair(pair_name: str, config: dict, param_grid: dict):
    """Run parameter sweep for a pair."""
    print(f"\n{'='*80}")
    print(f"PARAMETER SWEEP: {pair_name}")
    print(f"{'='*80}")
    
    symbol = config["symbol"]
    
    # Load data once
    print(f"Loading {symbol} data...")
    qdb_cfg = QuestDbConfig()
    
    leader = create_instrument(symbol, config["base_currency"], config, "BINANCE")
    follower = create_bybit_instrument(symbol, config["base_currency"], config)
    
    leader_deltas = list(load_questdb(qdb_cfg, leader, "BINANCE", symbol, "2026-02-02"))
    follower_deltas = list(load_questdb(qdb_cfg, follower, "BYBIT", symbol, "2026-02-02"))
    
    all_data = []
    all_data.extend(leader_deltas)
    all_data.extend(follower_deltas)
    all_data.sort(key=lambda d: d.ts_init)
    
    print(f"Loaded {len(all_data):,} total deltas")
    
    data_cache = {pair_name: all_data}
    
    # Generate parameter combinations
    spread_values = param_grid["spread_bps"]
    min_profit_values = param_grid["min_profit_bps"]
    order_qty_values = param_grid["order_qty"]
    
    combinations = list(product(spread_values, min_profit_values, order_qty_values))
    total = len(combinations)
    
    print(f"Testing {total} parameter combinations...")
    
    results = []
    for i, (spread, min_profit, order_qty) in enumerate(combinations, 1):
        params = {
            "spread_bps": Decimal(str(spread)),
            "min_profit_bps": Decimal(str(min_profit)),
            "order_qty": Decimal(str(order_qty)),
            "max_position_qty": Decimal(str(order_qty * 10)),
            "guard_threshold_bps": Decimal("10.0"),
        }
        
        if i % 5 == 0:
            print(f"Progress: {i}/{total} ({i/total*100:.1f}%)")
        
        result = test_params(pair_name, config, params, data_cache)
        results.append(result)
    
    # Sort by P&L
    results.sort(key=lambda r: r["pnl"], reverse=True)
    
    # Print top 10
    print(f"\n{'-'*80}")
    print(f"TOP 10 RESULTS for {pair_name}")
    print(f"{'-'*80}")
    print(f"{'Rank':<6} {'Spread':<8} {'MinProfit':<10} {'OrderQty':<10} {'Fills':<8} {'P&L':<12}")
    print(f"{'-'*80}")
    
    for i, r in enumerate(results[:10], 1):
        p = r["params"]
        print(f"{i:<6} {float(p['spread_bps']):<8.1f} {float(p['min_profit_bps']):<10.2f} {float(p['order_qty']):<10.2f} {r['fills']:<8} ${r['pnl']:<11.2f}")
    
    return results


def main():
    # Pair configs
    configs = {
        "ETH": {
            "symbol": "ETHUSDT",
            "base_currency": ETH,
            "price_precision": 2,
            "size_precision": 5,
            "price_increment": "0.01",
            "size_increment": "0.00001",
        },
        "LINK": {
            "symbol": "LINKUSDT",
            "base_currency": LINK,
            "price_precision": 4,
            "size_precision": 2,
            "price_increment": "0.0001",
            "size_increment": "0.01",
        },
        "AVAX": {
            "symbol": "AVAXUSDT",
            "base_currency": AVAX,
            "price_precision": 4,
            "size_precision": 3,
            "price_increment": "0.0001",
            "size_increment": "0.001",
        },
    }
    
    # Parameter grids - focused on tighter spreads and lower min_profit
    param_grids = {
        "ETH": {
            "spread_bps": [4.0, 6.0, 8.0],
            "min_profit_bps": [0.2, 0.4, 0.6],
            "order_qty": [0.01, 0.02, 0.03],
        },
        "LINK": {
            "spread_bps": [6.0, 8.0, 10.0],
            "min_profit_bps": [0.3, 0.5, 0.7],
            "order_qty": [3.0, 5.0, 7.0],
        },
        "AVAX": {
            "spread_bps": [8.0, 10.0, 12.0],
            "min_profit_bps": [0.4, 0.6, 0.8],
            "order_qty": [3.0, 5.0, 7.0],
        },
    }
    
    all_results = {}
    
    for pair in ["ETH", "LINK", "AVAX"]:
        results = sweep_pair(pair, configs[pair], param_grids[pair])
        all_results[pair] = results
    
    # Save results
    output = {
        pair: [
            {
                "params": {k: float(v) for k, v in r["params"].items()},
                "fills": r["fills"],
                "pnl": r["pnl"],
                "buy_value": r.get("buy_value", 0),
                "sell_value": r.get("sell_value", 0),
            }
            for r in results[:10]
        ]
        for pair, results in all_results.items()
    }
    
    output_file = Path("sweep_results_eth_link_avax.json")
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\n{'='*80}")
    print(f"Results saved to {output_file}")
    print(f"{'='*80}")
    
    # Print best config for each pair
    print(f"\n{'='*80}")
    print("RECOMMENDED CONFIGURATIONS")
    print(f"{'='*80}")
    
    for pair, results in all_results.items():
        best = results[0]
        p = best["params"]
        print(f"\n{pair}:")
        print(f"  spread_bps: {float(p['spread_bps'])}")
        print(f"  min_profit_bps: {float(p['min_profit_bps'])}")
        print(f"  order_qty: {float(p['order_qty'])}")
        print(f"  max_position_qty: {float(p['max_position_qty'])}")
        print(f"  → Fills: {best['fills']}, P&L: ${best['pnl']:.2f}")


if __name__ == "__main__":
    main()
