# Copilot Instructions

## Project Overview
This repo is a Nautilus Trader–based research stack focused on high-frequency market making, lead/lag microstructure, and backtesting at L2 depth. It includes:
- Rust core engine and Python strategies.
- Live and backtest scripts for bybit/binance lead/lag MM.
- QuestDB data pipeline for order book deltas.
- Local ingestion of VPS order book data.

Key directories:
- `strategy/` — strategy implementations, including `lead_lag_bybit_binance_mm_v003.py`.
- `examples/backtest/` — backtest scripts and loaders.
- `examples/live/` — live runners (staged only).
- `scripts/` — data ingest/sync tooling.
- `data/ob_data_live/` — synced live order book data (local, not committed).
- `outputs/backtests/` — backtest outputs (local, not committed).

## STRICT: Python Environment
- ALWAYS use the project virtual environment at `/home/seb/nebakineza/nautilus_trader/.venv`.
- Never call `python` or `pip` directly.
- Use:
  - `/home/seb/nebakineza/nautilus_trader/.venv/bin/python`
  - `/home/seb/nebakineza/nautilus_trader/.venv/bin/pip`

## Lead/Lag MM v003
Primary strategy file:
- `strategy/lead_lag_bybit_binance_mm_v003.py`

Highlights:
- Dynamic sizing with volatility and liquidity scalars.
- **Buy and sell liquidity scaling** uses leader bid/ask depth.
- Guard rails: local guard + global guard (diff bps) to cancel quotes.

Key config knobs:
- `spread_bps`, `guard_threshold_bps`, `global_guard_threshold_bps`.
- `liquidity_high_qty`, `liquidity_low_qty`, `liquidity_*_scalar`.
- `min_order_qty`, `max_order_qty`.

### v003 Multi-symbol Live (staged only)
- `examples/live/bybit/bybit_lead_lag_mm_multi_v3.py`
- Uses per-symbol liquidity thresholds and sizes.
- VPS runner: `scripts/runners/run_live_trading_multi_v3.py` (deployed under `/home/ubuntu/trading`).

## Backtesting
Single-pair v003 backtest:
- `examples/backtest/lead_lag_bybit_binance_backtest.py`
  - Supports file-based or QuestDB-based loaders.
  - QuestDB flags: `--questdb --questdb-host --questdb-port --questdb-table --questdb-step-seconds`.

Multi-symbol v003 backtest:
- `examples/backtest/lead_lag_bybit_binance_backtest_multi_v003.py`
  - Runs BTC/ETH/SOL in one engine.
  - Uses shared BYBIT cash account balance.
  - Pulls L2 deltas via QuestDB (recommended).

### QuestDB Loader
- `examples/backtest/questdb_orderbook_loader.py`
- Assumes QuestDB table `orderbook_deltas` with columns:
  - `timestamp` (designated), `venue`, `symbol`, `side`, `price`, `size`, `snapshot`.

## Data Pipeline
1. **VPS capture** produces L2 deltas (`*.data`) for BINANCE/BYBIT.
2. **Local sync** pulls data to `data/ob_data_live/`.
3. **QuestDB ingest** loads deltas into `orderbook_deltas`.
4. **Backtest** queries QuestDB by symbol + venue + date.

### Sync Script
- `scripts/pull_vps_ob_data.sh`
  - Uses SSH key from local config.
  - Do **not** hardcode sensitive hostnames/keys in docs; use env/config.

### Ingest Script
- `scripts/questdb_ingest_ob_data.py`
  - Example:
    - `python scripts/questdb_ingest_ob_data.py --data-dir data/ob_data_live --host 127.0.0.1 --port 9009`

## QuestDB Setup
Local Docker is used:
- HTTP: 9000
- ILP: 9009

Start (example):
- `docker run -d --name questdb -p 9000:9000 -p 9009:9009 -v ./data/questdb:/root/.questdb questdb/questdb:latest`

## Operational Notes
- `.gitignore` excludes `data/ob_data*`, `data/tick_data*`, `outputs/`, `logs`.
- Use `log_guard_events=False` for noise reduction in multi backtests.
- Dynamic sizes must respect instrument size precision (min order sizing).
- v003 enforces a dynamic size floor of 20% of `order_qty` plus `min_order_qty`.
- Equity drawdown killswitch is enabled in v003 (`max_drawdown_pct`, default 5%).
- Live v003 is managed via systemd service `leadlag-v003.service` on the VPS.
- **Reconciliation:** Enabled in `scripts/runners/run_live_trading_multi_v3.py` for live sync.
- **Inventory:** Strategy follows strict inventory management with skewing to offload positions (target=0).

## Lead-Lag & Rate Limits
- **Latency:** Strategy uses `recv_window=20000` to prevent Bybit API timeouts.
- **Rate Limits:** Quote refresh intervals are staggered to avoid API bans:
  - BTC: 400ms
  - ETH: 1000ms
  - SOL: 1000ms
- **Event-Driven:** Strategy refreshes quotes immediately on BOTH Leader (Binance) and Follower (Bybit) book updates to minimize latency, subject to the rate limits above.

## Known Constraints / Gotchas
- Guard events will cancel quotes during volatile lead/lag spikes.
- Using shared BYBIT cash account across symbols means capital is **pooled**.
- Ensure per-symbol liquidity thresholds are set for v003 (BTC/ETH/SOL differ greatly).

## Security & Secrets
- Do **not** commit private keys, VPS IPs, or credentials.
- Use SSH config and environment variables for remote access.
- Reference hosts by SSH alias (e.g., `sentinel-vps`) instead of hardcoding IPs.

## Recommended Commands
Quick v003 multi-symbol backtest (QuestDB):
- `python examples/backtest/lead_lag_bybit_binance_backtest_multi_v003.py --questdb --questdb-host 127.0.0.1 --questdb-port 9000 --questdb-table orderbook_deltas --questdb-step-seconds 300 --date YYYY-MM-DD --max-updates 50000`

Single-pair v003 backtest (QuestDB):
- `python examples/backtest/lead_lag_bybit_binance_backtest.py --questdb --questdb-host 127.0.0.1 --questdb-port 9000 --questdb-table orderbook_deltas --questdb-step-seconds 300 --date YYYY-MM-DD --max-updates 50000`
