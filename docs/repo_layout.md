# Repository Layout

This repository uses a structured layout to separate source, data, outputs, and operational scripts.

## Top-level (selected)

- docs/ — documentation and guides
- examples/ — backtest/live examples
- scripts/ — operational tooling (runners, analysis, tools)
- strategy/ — strategy implementations
- data/ — local datasets (not committed)
- outputs/ — local run outputs (not committed)
- logs/ — local logs (not committed)

## Data

- data/ob_data/ — order book deltas (file-based backtests)
- data/ob_data_live/ — synced live order book data
- data/tick_data/ — trade ticks
- data/tick_data_live/ — synced live tick data
- data/questdb/ — local QuestDB storage

## Outputs

- outputs/backtests/ — backtest reports and CSVs
- outputs/sweeps/ — parameter sweep results
- outputs/optuna/ — Optuna study logs and summaries

## Scripts

- scripts/runners/ — execution runners (live/backtest)
- scripts/analysis/ — analysis and validation utilities
- scripts/tools/ — helper tools and quick references

## Conventions

- Data and outputs are excluded from git via .gitignore.
- Use ISO timestamps in filenames for versioned outputs.
- Prefer absolute paths in systemd service files on the VPS.
