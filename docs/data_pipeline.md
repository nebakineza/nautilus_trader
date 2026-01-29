# Data Pipeline (VPS → Sentinel → QuestDB → Backtests)

## Overview
1. **VPS records live market data** (order book deltas) to JSONL `.data` files.
2. **Sentinel syncs** those files every 10 minutes into `ob_data`.
3. **QuestDB ingests** the JSONL files via ILP.
4. **Backtests load** from `ob_data` with the provided loader.

---

## 1) VPS → Sentinel Sync
Script:
- scripts/pull_vps_ob_data.sh

Defaults:
- Source: /home/ubuntu/trading/ob_data_live
- Destination: /home/seb/nebakineza/nautilus_trader/ob_data
- Cadence: every 10 minutes (cron)

Logs:
- logs/ob_data_sync.log

---

## 2) QuestDB Ingest
Script:
- scripts/questdb_ingest_ob_data.py

Example:
- python scripts/questdb_ingest_ob_data.py \
  --data-dir /home/seb/nebakineza/nautilus_trader/ob_data \
  --host 127.0.0.1 --port 9009

Table:
- orderbook_deltas

Schema (ILP fields):
- tags: venue, symbol
- fields: side, price, size, action, snapshot
- timestamp: ts_ms converted to nanoseconds

---

## 3) Backtest Loader
Backtest script:
- examples/backtest/lead_lag_bybit_binance_backtest.py

Example:
- python examples/backtest/lead_lag_bybit_binance_backtest.py \
  --date 2026-01-29 --max-updates 200000 \
  --data-dir ob_data --depth 50

Notes:
- File naming expected: ob_data/<SYMBOL>_Spot/YYYY-MM-DD_<SYMBOL>_ob<DEPTH>.data
- The live recorder uses depth 50 by default.
