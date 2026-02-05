# Data Pipeline (VPS → Sentinel → QuestDB → Backtests)

## Overview
1. **VPS records live market data** (order book deltas) to JSONL `.data` files.
2. **Sentinel syncs** those files every 10 minutes into `data/ob_data`.
3. **QuestDB ingests** the JSONL files via ILP.
4. **QuestDB builds bars** (1-minute MID) for SpotMatrix backtests.
5. **Backtests load** from `data/ob_data` with the provided loader or the bars table.

---

## 3e) Inventory Ratio Metrics
Script:
- scripts/questdb_inventory_metrics.py

Example:
- /home/seb/nebakineza/nautilus_trader/.venv/bin/python scripts/questdb_inventory_metrics.py

Table:
- live_inventory_metrics

Schema (ILP fields):
- tags: venue, symbol
- fields: equity_usdt, usdt_value, base_value, base_ratio, target_ratio, ratio_drift

## 1) VPS → Sentinel Sync
Script:
- scripts/pull_vps_ob_data.sh

Defaults:
- Source: /home/ubuntu/trading/data/ob_data_live
- Destination: /home/seb/nebakineza/nautilus_trader/data/ob_data
- Cadence: every 10 minutes (cron)

Logs:
- logs/ob_data_sync.log

---

## 2) QuestDB Ingest
Script:
- scripts/questdb_ingest_ob_data.py

Example:
- python scripts/questdb_ingest_ob_data.py \
  --data-dir /home/seb/nebakineza/nautilus_trader/data/ob_data \
  --host 127.0.0.1 --port 9009

Table:
- orderbook_deltas

Schema (ILP fields):
- tags: venue, symbol
- fields: side, price, size, action, snapshot
- timestamp: ts_ms converted to nanoseconds

---

## 3) QuestDB Bar Build (SpotMatrix)
Script:
- scripts/questdb_build_bars.py

Example:
- python scripts/questdb_build_bars.py \
  --date 2026-01-29 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --source-table orderbook_deltas \
  --bars-table bars_1m_mid

Table:
- bars_1m_mid

Schema (ILP fields):
- tags: venue, symbol, interval, price_type, source
- fields: open, high, low, close, volume
- timestamp: bar close time in nanoseconds

---

## 3b) Instrument Metadata Snapshot
Script:
- scripts/questdb_snapshot_instruments.py

Example:
- /home/seb/nebakineza/nautilus_trader/.venv/bin/python scripts/questdb_snapshot_instruments.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,DOGEUSDT,AVAXUSDT \
  --venue BYBIT --venue BINANCE

Table:
- instrument_meta

Schema (ILP fields):
- tags: venue, symbol
- fields: price_tick, qty_step, min_qty, max_qty, min_notional

---

## 3c) Fee Rates Snapshot (Bybit)
Script:
- scripts/questdb_snapshot_fees.py

Example:
- /home/seb/nebakineza/nautilus_trader/.venv/bin/python scripts/questdb_snapshot_fees.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,DOGEUSDT,AVAXUSDT

Table:
- fee_rates

Schema (ILP fields):
- tags: venue, symbol
- fields: maker_bps, taker_bps

---

## 3d) Balance Snapshot (Bybit)
Script:
- scripts/questdb_snapshot_balances.py

Example:
- /home/seb/nebakineza/nautilus_trader/.venv/bin/python scripts/questdb_snapshot_balances.py

Table:
- account_balances

Schema (ILP fields):
- tags: venue, currency
- fields: total, free, locked

## 4) Backtest Loader
Backtest script:
- examples/backtest/lead_lag_bybit_binance_backtest.py

Example:
- python examples/backtest/lead_lag_bybit_binance_backtest.py \
  --date 2026-01-29 --max-updates 200000 \
  --data-dir data/ob_data --depth 50

QuestDB Example:
- python examples/backtest/lead_lag_bybit_binance_backtest.py \
  --questdb --questdb-host 127.0.0.1 --questdb-port 9000 \
  --questdb-table orderbook_deltas --questdb-step-seconds 300 \
  --date 2026-01-29 --max-updates 200000

SpotMatrix Example (bars table):
- python scripts/runners/run_spot_matrix_backtest.py \
  --use-bars --bars-table bars_1m_mid \
  --date 2026-01-29 --symbols BTCUSDT,ETHUSDT,SOLUSDT

Notes:
- File naming expected: data/ob_data/<SYMBOL>_Spot/YYYY-MM-DD_<SYMBOL>_ob<DEPTH>.data
- The live recorder uses depth 50 by default.
