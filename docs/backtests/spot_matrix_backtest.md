# SpotMatrix v1 Backtest (QuestDB)

This backtest can build 1-minute MID bars from QuestDB orderbook deltas or load precomputed bars from QuestDB.

## Script

- scripts/runners/run_spot_matrix_backtest.py

## Example

```bash
PYTHONPATH=/home/seb/nebakineza/nautilus_trader \
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
  scripts/runners/run_spot_matrix_backtest.py \
  --date 2026-01-29 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --max-updates 200000 \
  --lookback-window 500 \
  --rebalance-mins 60

## Example (Bars Table)

```bash
python scripts/questdb_build_bars.py \
  --date 2026-01-29 --symbols BTCUSDT,ETHUSDT,SOLUSDT

PYTHONPATH=/home/seb/nebakineza/nautilus_trader \
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
  scripts/runners/run_spot_matrix_backtest.py \
  --use-bars --bars-table bars_1m_mid \
  --date 2026-01-29 --symbols BTCUSDT,ETHUSDT,SOLUSDT
```
```

## Notes

- Bars are synthesized from orderbook mid prices (BYBIT venue).
- Ensure QuestDB table `orderbook_deltas` is populated for each symbol and date.
