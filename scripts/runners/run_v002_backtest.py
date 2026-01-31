#!/usr/bin/env python3
"""V002 Strategy Backtest Runner.

Runs the v002 strategy on Bybit order book data and emits CSV reports.

This runner is sweep-friendly:
- Accepts a `--config-json` file to override `MultiPairMMConfig` fields.
- Accepts `--output-dir` so each run writes isolated reports.
"""

import argparse
import csv
import json
import sys
import time
import zipfile
from decimal import Decimal
from pathlib import Path
from datetime import datetime

# Import Nautilus components
from nautilus_trader.adapters.bybit.constants import BYBIT_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import BTC, ETH, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, InstrumentId, Symbol
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.model.events import AccountState

# Import v002 strategy
from strategy.hft_obi_bybit_spot_mm_lowcap_multipair_v002 import (
    MultiPairLowCapitalOBIMarketMaker,
    MultiPairMMConfig,
)

# Import data loader and models from v001 backtest
from examples.backtest.bybit_orderbook_loader import BybitOrderBookLoader
from examples.backtest.institutional_models import create_institutional_backtest_models


def create_btcusdt_bybit_instrument() -> CurrencyPair:
    """Create BTCUSDT-SPOT.BYBIT instrument."""
    return CurrencyPair(
        instrument_id=InstrumentId.from_str("BTCUSDT-SPOT.BYBIT"),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=1,
        size_precision=6,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=Quantity.from_str("1000.0"),
        min_quantity=Quantity.from_str("0.000001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.0001"),  # 1 bps maker
        taker_fee=Decimal("0.0001"),  # 1 bps taker
        ts_event=0,
        ts_init=0,
    )


def create_ethusdt_bybit_instrument() -> CurrencyPair:
    """Create ETHUSDT-SPOT.BYBIT instrument.

    Note: parameters are reasonable defaults for backtesting; if your venue
    metadata differs, adjust precision/increment.
    """
    return CurrencyPair(
        instrument_id=InstrumentId.from_str("ETHUSDT-SPOT.BYBIT"),
        raw_symbol=Symbol("ETHUSDT"),
        base_currency=ETH,
        quote_currency=USDT,
        price_precision=2,
        size_precision=5,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.00001"),
        lot_size=None,
        max_quantity=Quantity.from_str("100000.0"),
        min_quantity=Quantity.from_str("0.00001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.0001"),
        taker_fee=Decimal("0.0001"),
        ts_event=0,
        ts_init=0,
    )


def create_instrument(instrument_id: str) -> CurrencyPair:
    inst = InstrumentId.from_str(instrument_id)
    if str(inst) == "BTCUSDT-SPOT.BYBIT":
        return create_btcusdt_bybit_instrument()
    if str(inst) == "ETHUSDT-SPOT.BYBIT":
        return create_ethusdt_bybit_instrument()
    raise ValueError(f"Unsupported instrument_id for this runner: {instrument_id}")


def create_backtest_engine(instrument_id: str) -> tuple[BacktestEngine, CurrencyPair]:
    """Create and configure backtest engine."""
    print("=" * 70)
    print("CREATING BACKTEST ENGINE - V002")
    print("=" * 70)
    
    trader_id = TraderId("BACKTESTER-V002-001")
    config = BacktestEngineConfig(trader_id=trader_id)
    engine = BacktestEngine(config=config)
    
    print(f"✓ Engine created: {trader_id}")
    
    # Create instrument
    instrument = create_instrument(instrument_id)
    print(f"✓ Instrument: {instrument.id}")
    
    # Get realistic latency and fill models
    latency_model, fill_model = create_institutional_backtest_models()

    starting_balances = [Money(500.0, USDT)]
    if instrument.base_currency == BTC:
        starting_balances.append(Money(0.0, BTC))
    elif instrument.base_currency == ETH:
        starting_balances.append(Money(0.0, ETH))
    
    # Add venue
    engine.add_venue(
        venue=BYBIT_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=starting_balances,
        base_currency=None,
        book_type=BookType.L2_MBP,
        latency_model=latency_model,
        fill_model=fill_model,
    )
    
    if latency_model:
        print(f"  ✓ Latency model: CoLocation (250μs base)")
    if fill_model:
        print(f"  ✓ Fill model: Institutional (85% queue, 30% liquidity)")
    
    engine.add_instrument(instrument)
    print(f"✓ Instrument registered")
    
    return engine, instrument


def load_orderbook_data(
    engine: BacktestEngine,
    instrument: CurrencyPair,
    date_str: str = "2026-01-15",
    max_updates: int = None,
) -> int:
    """Load order book data - same as v001."""
    print("\n" + "=" * 70)
    print("LOADING ORDER BOOK DATA")
    print("=" * 70)
    
    symbol = str(instrument.raw_symbol)
    ob_file = Path(f"data/ob_data/{symbol}_Spot/{date_str}_{symbol}_ob200.data")
    ob_zip = Path(str(ob_file) + ".zip")
    
    if not ob_file.exists():
        if ob_zip.exists():
            print(f"i Found zipped order book: {ob_zip}")
            print(f"i Extracting -> {ob_file} (one-time)")
            try:
                with zipfile.ZipFile(ob_zip, "r") as zf:
                    members = [m for m in zf.namelist() if not m.endswith("/")]
                    if not members:
                        raise ValueError("zip contains no files")
                    # Prefer exact filename match; otherwise take first file.
                    target = next((m for m in members if m.endswith(ob_file.name)), members[0])
                    extracted_path = zf.extract(target, path=ob_file.parent)
                    extracted_path = Path(extracted_path)
                    if extracted_path != ob_file:
                        extracted_path.replace(ob_file)
            except Exception as e:
                print(f"✗ Failed to extract {ob_zip}: {e}")
                return 0
        else:
            print(f"✗ Order book file not found: {ob_file} (and no .zip present)")
            return 0
    
    file_size_mb = ob_file.stat().st_size / 1e6
    print(f"✓ Order book file: {ob_file}")
    print(f"  Size: {file_size_mb:.1f} MB")
    print(f"  Max Updates: {max_updates if max_updates else 'All'}")
    
    print(f"\nLoading deltas...", end="", flush=True)
    start_time = time.time()
    
    try:
        delta_count = 0
        for delta in BybitOrderBookLoader.load_file(ob_file, instrument):
            engine.add_data([delta])
            delta_count += 1
            
            if max_updates and delta_count >= max_updates:
                break
            
            if delta_count % 10000 == 0:
                print(f"\r  Loaded {delta_count:,} deltas...", end="", flush=True)
        
        elapsed = time.time() - start_time
        print(f"\r✓ Loaded {delta_count:,} order book deltas in {elapsed:.1f}s")
        return delta_count
        
    except Exception as e:
        print(f"\n✗ Error loading data: {e}")
        import traceback
        traceback.print_exc()
        return 0


def _load_config_overrides(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config json must be an object")
    return data


def create_v002_strategy(
    engine: BacktestEngine,
    config_overrides: dict | None,
    instrument_id: str,
) -> MultiPairLowCapitalOBIMarketMaker:
    """Create and configure v002 strategy."""
    print("\n" + "=" * 70)
    print("CONFIGURING V002 STRATEGY")
    print("=" * 70)
    
    # Defaults target single-pair (BTC/ETH) runs; sweep runner can override.
    base_instruments = [
        {
            "instrument_id": instrument_id,
            "base_qty": "0.0001" if instrument_id.startswith("BTC") else "0.005",
            "max_position_qty": "0.0005" if instrument_id.startswith("BTC") else "0.015",
            "weight": 1.0,
            "obi_sensitivity": 1.0,
            "min_spread_bps": 1,
            "max_spread_bps": 5 if instrument_id.startswith("BTC") else 6,
        }
    ]

    config_dict: dict = {
        "instruments": base_instruments,
        "total_capital_usd": 500.0,
        "max_total_exposure_pct": 0.15,
        "emergency_liquidation_loss_usd": -100.0,
        "obi_levels": 5,
        "obi_ema_period": 15,
        "obi_entry_threshold": 0.15,
        "obi_exit_threshold": 0.03,
        "risk_aversion": 0.5,
        "inventory_half_life_seconds": 30.0,
        "max_inventory_age_seconds": 120.0,
        "correlation_lookback_seconds": 300,
        "max_correlated_exposure_pct": 0.7,
        "orderbook_update_throttle_ms": 5,
        "quote_refresh_interval_ms": 50,
    }

    if config_overrides:
        config_dict.update(config_overrides)

    config = MultiPairMMConfig(**config_dict)
    
    print(f"✓ Strategy: Multi-Pair OBI MM v002")
    print(f"\n  V002 ENHANCEMENTS:")
    print(f"  ✅ Avellaneda-Stoikov inventory management")
    print(f"  ✅ ML-based signal filtering")
    print(f"  ✅ Adaptive spread calculation (5 components)")
    print(f"  ✅ Volatility tracking (EWMA)")
    print(f"  ✅ Position optimization")
    
    print(f"\n  CONFIGURATION:")
    print(f"  Capital: $500 USDT")
    print(f"  Base Qty: 0.0001 BTC (~$9 per quote)")
    print(f"  Max Position: 0.0005 BTC (~$45 max)")
    print(f"  OBI Threshold: 15%")
    print(f"  Spread Range: 1-5 bps (adaptive components add 0-5 more)")
    print(f"  Risk Aversion: 0.5 (moderate)")
    print(f"  Half-life: 30s")
    
    strategy = MultiPairLowCapitalOBIMarketMaker(config)
    engine.add_strategy(strategy)
    print(f"✓ Strategy added: {strategy.id}")
    
    return strategy


def run_backtest(engine: BacktestEngine) -> None:
    """Run the backtest."""
    print("\n" + "=" * 70)
    print("RUNNING BACKTEST - V002")
    print("=" * 70)
    
    start_time = time.time()
    print(f"Start time: {datetime.now().isoformat()}")
    print(f"\nProcessing events...", end="", flush=True)
    
    try:
        engine.run()
        elapsed = time.time() - start_time
        print(f"\r✓ Backtest completed in {elapsed:.1f} seconds")
        
    except Exception as e:
        print(f"\n✗ Backtest error: {e}")
        import traceback
        traceback.print_exc()
        return


def generate_reports(engine: BacktestEngine, output_dir: Path) -> None:
    """Generate performance reports."""
    print("\n" + "=" * 70)
    print("GENERATING REPORTS - V002")
    print("=" * 70)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"✓ Output directory: {output_dir}")
    
    def _write_rows(path: Path, rows: list[dict]) -> int:
        if not rows:
            path.write_text("", encoding="utf-8")
            return 0
        # Stable header: union of keys, but keep first row order preference.
        fieldnames: list[str] = []
        seen: set[str] = set()
        for k in rows[0].keys():
            fieldnames.append(k)
            seen.add(k)
        for r in rows[1:]:
            for k in r.keys():
                if k not in seen:
                    fieldnames.append(k)
                    seen.add(k)

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        return len(rows)

    filled: list[dict] = []

    # Fills report (order-level, matches ReportProvider.generate_order_fills_report)
    print("\nGenerating fills report...", end="", flush=True)
    try:
        orders = list(engine.trader._cache.orders())  # pylint: disable=protected-access
        filled = [o.to_dict() for o in orders if getattr(o, "filled_qty", 0) and o.filled_qty > 0]
        for r in filled:
            ts_last = r.get("ts_last")
            ts_init = r.get("ts_init")
            r["ts_last"] = unix_nanos_to_dt(ts_last or 0).isoformat(sep=" ")
            r["ts_init"] = unix_nanos_to_dt(ts_init or 0).isoformat(sep=" ")

        fills_path = output_dir / "fills_report.csv"
        n = _write_rows(fills_path, filled)
        print(f"\r✓ Fills report: {fills_path} ({n} fills)")
    except Exception as e:
        print(f"\r✗ Fills report error: {e}")

    # Positions report
    print("Generating positions report...", end="", flush=True)
    try:
        positions = list(engine.trader._cache.positions())  # pylint: disable=protected-access
        snapshots = list(engine.trader._cache.position_snapshots())  # pylint: disable=protected-access

        snapshot_ids = {str(p.id) for p in snapshots}
        all_positions = positions + snapshots
        rows: list[dict] = []
        for p in all_positions:
            r = p.to_dict()
            # Match ReportProvider field transformations
            if "signed_qty" in r:
                del r["signed_qty"]
            for k in ("quote_currency", "base_currency", "settlement_currency"):
                if k in r:
                    del r[k]
            r["ts_opened"] = unix_nanos_to_dt(r.get("ts_opened") or 0).isoformat(sep=" ")
            ts_closed = r.get("ts_closed")
            r["ts_closed"] = unix_nanos_to_dt(ts_closed).isoformat(sep=" ") if ts_closed else ""
            r["is_snapshot"] = str(r.get("position_id") in snapshot_ids)
            rows.append(r)

        positions_path = output_dir / "positions_report.csv"
        n = _write_rows(positions_path, rows)
        print(f"\r✓ Positions report: {positions_path} ({n} positions)")
    except Exception as e:
        print(f"\r✗ Positions report error: {e}")

    # Account report
    print("Generating account report...", end="", flush=True)
    try:
        account = engine.trader._cache.account_for_venue(venue=BYBIT_VENUE, account_id=None)  # pylint: disable=protected-access
        rows: list[dict] = []
        if account is not None:
            for ev in getattr(account, "events", []) or []:
                state = AccountState.to_dict(ev)
                ts_event = state.get("ts_event")
                ts = unix_nanos_to_dt(ts_event or 0).isoformat(sep=" ")
                balances = state.get("balances") or []
                for bal in balances:
                    row = {
                        "timestamp": ts,
                        "total": bal.get("total"),
                        "locked": bal.get("locked"),
                        "free": bal.get("free"),
                        "currency": bal.get("currency"),
                        "account_id": state.get("account_id"),
                        "account_type": state.get("account_type"),
                        "base_currency": state.get("base_currency"),
                        "margins": state.get("margins"),
                        "reported": state.get("reported"),
                        "info": state.get("info"),
                    }
                    rows.append(row)

        account_path = output_dir / "account_report.csv"
        n = _write_rows(account_path, rows)
        print(f"\r✓ Account report: {account_path} ({n} rows)")
    except Exception as e:
        print(f"\r✗ Account report error: {e}")

    # Mark-to-market equity report (USDT + BTC * last fill price)
    print("Generating equity report...", end="", flush=True)
    try:
        equity_rows: list[dict] = []
        if rows:
            # Build balance snapshot per timestamp.
            balances_by_ts: dict[str, dict[str, float]] = {}
            for row in rows:
                ts = row.get("timestamp")
                cur = row.get("currency")
                if not ts or not cur:
                    continue
                balances_by_ts.setdefault(ts, {})[cur] = float(row.get("total") or 0.0)

            # Build fill price series for BTCUSDT.
            fill_prices: list[tuple[datetime, float]] = []
            for r in filled:
                ts_str = r.get("ts_last") or r.get("ts_init")
                price = r.get("price")
                if ts_str and price is not None:
                    try:
                        ts_dt = datetime.fromisoformat(ts_str)
                    except ValueError:
                        continue
                    fill_prices.append((ts_dt, float(price)))
            fill_prices.sort(key=lambda x: x[0])

            last_price: float | None = None
            idx = 0
            for ts_str in sorted(balances_by_ts.keys(), key=lambda s: datetime.fromisoformat(s)):
                ts_dt = datetime.fromisoformat(ts_str)
                while idx < len(fill_prices) and fill_prices[idx][0] <= ts_dt:
                    last_price = fill_prices[idx][1]
                    idx += 1
                totals = balances_by_ts[ts_str]
                usdt_total = totals.get("USDT", 0.0)
                btc_total = totals.get("BTC", 0.0)
                equity_usdt = None
                if last_price is not None:
                    equity_usdt = usdt_total + btc_total * last_price
                equity_rows.append(
                    {
                        "timestamp": ts_str,
                        "usdt_total": usdt_total,
                        "btc_total": btc_total,
                        "last_fill_price": last_price,
                        "equity_usdt": equity_usdt,
                    }
                )

        equity_path = output_dir / "equity_report.csv"
        n = _write_rows(equity_path, equity_rows)
        print(f"\r✓ Equity report: {equity_path} ({n} rows)")
    except Exception as e:
        print(f"\r✗ Equity report error: {e}")
    
    print(f"\n✓ All reports generated in: {output_dir}")


def main() -> int:
    """Main execution."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default="2026-01-15")
    ap.add_argument("--max-updates", type=int, default=50000)
    ap.add_argument("--instrument-id", type=str, default="BTCUSDT-SPOT.BYBIT")
    ap.add_argument("--config-json", type=str, default=None)
    ap.add_argument("--output-dir", type=str, default="outputs/backtests/backtest_results_v002")
    args = ap.parse_args()

    print("\n")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║       V002 MULTI-PAIR MARKET MAKER BACKTEST                      ║")
    print("║       Running on same data as V001 for comparison               ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()
    
    output_dir = Path(args.output_dir)

    overall_start = time.time()

    # Create engine
    engine, instrument = create_backtest_engine(args.instrument_id)
    
    # Load data (same parameters as v001)
    load_start = time.time()
    delta_count = load_orderbook_data(
        engine,
        instrument,
        date_str=args.date,
        max_updates=args.max_updates,
    )
    load_elapsed = time.time() - load_start
    print(f"\n✓ Data load wall time: {load_elapsed:.1f}s")
    
    if delta_count == 0:
        print("✗ No data loaded, aborting backtest")
        return 1
    
    overrides = None
    if args.config_json:
        overrides = _load_config_overrides(Path(args.config_json))

    # Create strategy
    _ = create_v002_strategy(engine, overrides, instrument_id=args.instrument_id)
    
    # Run backtest
    run_backtest(engine)
    
    # Generate reports
    # Persist the exact config used for the run
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        cfg_dump_path = output_dir / "run_config.json"
        cfg_dump_path.write_text(json.dumps(overrides or {}, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass

    generate_reports(engine, output_dir=output_dir)

    total_elapsed = time.time() - overall_start
    print(f"\n✓ End-to-end wall time: {total_elapsed:.1f}s")
    
    print("\n" + "=" * 70)
    print("V002 BACKTEST COMPLETE")
    print("=" * 70)
    print("\nCompare results:")
    print("  V001: outputs/backtests/backtest_results_specialized_lowcap/")
    print("  V002: outputs/backtests/backtest_results_v002/")
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
