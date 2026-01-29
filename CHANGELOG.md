# Changelog — Changes made in this workspace session

All changes below were made after cloning `nautilus_trader` into `~/nebakineza/nautilus_trader` during this session.

## Summary of additions

- Added example mid-price market-maker strategy:
  - `examples/mid_price_mm.py`

- Added institutional HFT example implementing OFI, inventory-skew, emergency liquidation, and laddering:
  - `examples/professional_hft.py`

- Backtesting helpers and runner
  - `examples/backtest/hft_runner.py` — backtest runner wiring `BacktestEngine`, BYBIT venue (latency), and wranglers
  - `examples/backtest/data_wranglers.py` — small helpers demonstrating `OrderBookDeltaWrangler` and `TradeWrangler` usage
  - `examples/backtest/hft_metrics.py` — post-backtest metrics helpers (Sharpe, fill ratio, markout, inventory duration)

- Documentation and guides
  - Updated `examples/README.md` with a Backtesting Guide and linked `01-backtesting.md`
  - `docs/grafana_monitoring.md` — short Grafana / InfluxDB monitoring guide and heartbeat recommendations

## Modifications

- Syntax-verified the added example scripts with `python -m py_compile`.

## Notes

- These files are illustrative examples and require real market data (high-fidelity L2 deltas and trade ticks) plus appropriate configuration and credentials to run real or paper/live sessions.
- The backtest runner uses placeholder paths for data files — replace `DATA_ORDERBOOK_PATH` and `DATA_TRADES_PATH` with real files/URIs.

If you want, I can:
- Add a runnable backtest that includes a small synthetic dataset for CI-style verification.
- Add an `examples/monitoring/emit_metrics.py` demo for pushing metrics to InfluxDB.
