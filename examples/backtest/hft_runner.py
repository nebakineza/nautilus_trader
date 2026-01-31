"""HFT backtest runner example.

This script demonstrates configuring the BacktestEngine, adding a BYBIT venue
with latency, loading high-frequency L2/orderbook delta and trade data via
wranglers, and attaching the `InstitutionalHFT` strategy from
`examples/professional_hft.py`.

Adjust `DATA_ORDERBOOK_PATH` and `DATA_TRADES_PATH` to point to your parquet/csv
files (high-frequency, tick-level). This script is illustrative and intentionally
keeps data-loading flexible so you can adapt it to your provider (Tardis, custom dumps).
"""

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT

# Wranglers (example helper to convert CSV/Parquet to Nautilus objects)
from nautilus_trader.persistence.wranglers import OrderBookDeltaWrangler, TradeWrangler
import os
import glob

# Import strategy
from examples.professional_hft import ProfessionalHFTConfig, InstitutionalHFT

# If you have local tick data, point `TICK_DATA_DIR` to it. The runner will
# automatically add all files matching *_ob200.data as orderbook deltas and
# *_trades.data as trades. Adjust patterns as needed for your files.
TICK_DATA_DIR = os.path.expanduser("~/nebakineza/nautilus_trader/data/tick_data/BTCUSDT_Spot")




def main():
    BYBIT = Venue("BYBIT")

    engine_config = BacktestEngineConfig(
        trader_id=TraderId("HFT-BACKTESTER"),
        ref_currency=USDT,
        initial_balance=[Money(10000, USDT)],
    )

    engine = BacktestEngine(config=engine_config)

    # Add venue with simulated OMS latency (ns). 5ms default; increase to test jitter.
    engine.add_venue(venue=BYBIT, oms_latency_ns=5_000_000)

    # Load tick data from local folder if present
    if os.path.isdir(TICK_DATA_DIR):
        # Orderbook delta files (example pattern)
        ob_files = sorted(glob.glob(os.path.join(TICK_DATA_DIR, "*_ob*.data")))
        for fpath in ob_files:
            print(f"Adding orderbook data: {fpath}")
            wr = OrderBookDeltaWrangler(fpath)
            engine.add_data(wr.yield_objects())

        # Trade files (if present)
        trade_files = sorted(glob.glob(os.path.join(TICK_DATA_DIR, "*_trades*.data")))
        for fpath in trade_files:
            print(f"Adding trade data: {fpath}")
            wr = TradeWrangler(fpath)
            engine.add_data(wr.yield_objects())
    else:
        # Fallback: attempt to use explicit paths
        ob_wrangler = OrderBookDeltaWrangler(DATA_ORDERBOOK_PATH)
        trade_wrangler = TradeWrangler(DATA_TRADES_PATH)
        engine.add_data(ob_wrangler.yield_objects())
        engine.add_data(trade_wrangler.yield_objects())

    # Configure and add the HFT strategy
    cfg = ProfessionalHFTConfig(
        instrument_id="BTCUSDT-SPOT.BYBIT",
        base_qty=0.01,
        num_layers=5,
        max_notional_exposure=5000.0,
        inventory_alpha=0.1,
    )
    strat = InstitutionalHFT(config=cfg)
    engine.add_strategy(strat)

    # Run the backtest (blocking)
    engine.run()


if __name__ == "__main__":
    main()
