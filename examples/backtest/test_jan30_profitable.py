#!/usr/bin/env python3
"""Test ETH/LINK/AVAX on Jan 30, 2026 - a known profitable period."""
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


def backtest_on_date(pair_name: str, config: dict, params: dict, test_date: str):
    """Test on specific date."""
    symbol = config["symbol"]
    
    leader = create_instrument(symbol, config["base_currency"], config, "BINANCE")
    follower = create_bybit_instrument(symbol, config["base_currency"], config)
    
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId(f"{pair_name}-JAN30-001"),
            logging=LoggingConfig(log_level="ERROR"),
        )
    )
    
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
    
    # Load data
    print(f"Loading {symbol} data for {test_date}...")
    qdb_cfg = QuestDbConfig()
    
    leader_deltas = list(load_questdb(qdb_cfg, leader, "BINANCE", symbol, test_date))
    follower_deltas = list(load_questdb(qdb_cfg, follower, "BYBIT", symbol, test_date))
    
    all_data = []
    all_data.extend(leader_deltas)
    all_data.extend(follower_deltas)
    all_data.sort(key=lambda d: d.ts_init)
    
    print(f"Loaded {len(all_data):,} deltas")
    
    if len(all_data) < 100:
        print(f"⚠️  Insufficient data for {test_date}")
        return None
    
    engine.add_data(all_data)
    
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
    
    try:
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        
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
            "fills": len(fills),
            "pnl": pnl,
            "buy_value": total_buy_value,
            "sell_value": total_sell_value,
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


def main():
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
    
    # Use sweep's best params
    best_params = {
        "ETH": {
            "spread_bps": Decimal("8.0"),
            "min_profit_bps": Decimal("0.4"),
            "order_qty": Decimal("0.01"),
            "max_position_qty": Decimal("0.1"),
            "guard_threshold_bps": Decimal("10.0"),
        },
        "LINK": {
            "spread_bps": Decimal("10.0"),
            "min_profit_bps": Decimal("0.7"),
            "order_qty": Decimal("3.0"),
            "max_position_qty": Decimal("30.0"),
            "guard_threshold_bps": Decimal("10.0"),
        },
        "AVAX": {
            "spread_bps": Decimal("8.0"),
            "min_profit_bps": Decimal("0.4"),
            "order_qty": Decimal("3.0"),
            "max_position_qty": Decimal("30.0"),
            "guard_threshold_bps": Decimal("10.0"),
        },
    }
    
    # Test multiple dates
    test_dates = ["2026-01-30", "2026-01-31", "2026-02-01"]
    
    print("="*80)
    print("TESTING ON ALTERNATIVE DATES")
    print("="*80)
    
    for test_date in test_dates:
        print(f"\n{'='*80}")
        print(f"DATE: {test_date}")
        print(f"{'='*80}\n")
        
        for pair in ["ETH", "LINK", "AVAX"]:
            result = backtest_on_date(pair, configs[pair], best_params[pair], test_date)
            
            if result:
                status = "✅ PROFIT" if result["pnl"] > 0 else ("⚠️  LOSS" if result["pnl"] < -50 else "➖ BREAKEVEN")
                print(f"{pair:6} {test_date}  Fills: {result['fills']:3}  P&L: ${result['pnl']:8.2f}  {status}")
            else:
                print(f"{pair:6} {test_date}  ❌ NO DATA")


if __name__ == "__main__":
    main()
