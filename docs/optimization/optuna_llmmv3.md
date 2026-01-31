# Optuna Optimization (LLMMv3)

This guide explains how to run per-symbol Optuna studies for the Lead-Lag MM v003 strategy and where outputs are stored.

## Script

- scripts/optimize_llmmv3.py

## Data Inputs

- Order book data (file-based): data/ob_data/<SYMBOL>_Spot/YYYY-MM-DD_<SYMBOL>_ob<DEPTH>.data
- Order book data (QuestDB): orderbook_deltas table

## Outputs

All optimization artifacts are written under outputs/optuna:

- outputs/optuna/logs/optuna_<symbol>.log
- outputs/optuna/studies/<symbol>_<timestamp>_best.json

## Example (QuestDB)

```bash
PYTHONPATH=/home/seb/nebakineza/nautilus_trader \
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
  scripts/optimize_llmmv3.py \
  --questdb --questdb-host 127.0.0.1 --questdb-port 9000 \
  --questdb-table orderbook_deltas --questdb-step-seconds 300 \
  --date 2026-01-29 --max-updates 50000 --n-trials 200 --symbols BTCUSDT
```

## Example (Files)

```bash
PYTHONPATH=/home/seb/nebakineza/nautilus_trader \
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
  scripts/optimize_llmmv3.py \
  --date 2026-01-29 --max-updates 50000 --symbols BTCUSDT \
  --data-dir data/ob_data
```

## Notes

- Run separate studies per symbol (BTC/ETH/SOL) to respect different volatility regimes.
- Use objective=pnl or objective=equity depending on your preference.
