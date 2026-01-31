# AGENTS.md

## Purpose
This document orients automated agents to the project structure, data pipeline, and the Lead/Lag MM v003 strategy.

## Repo Layout
- `strategy/` — Strategy implementations and research docs.
- `examples/backtest/` — Backtest runners and loaders.
- `examples/live/` — Live runners (staged only).
- `scripts/` — Data sync + ingest scripts.
- `docs/` — Pipeline documentation.

## STRICT: Python Environment
- ALWAYS use the project virtual environment at `/home/seb/nebakineza/nautilus_trader/.venv`.
- Never call `python` or `pip` directly.
- Use:
  - `/home/seb/nebakineza/nautilus_trader/.venv/bin/python`
  - `/home/seb/nebakineza/nautilus_trader/.venv/bin/pip`

## Lead/Lag MM v003
**File:** `strategy/lead_lag_bybit_binance_mm_v003.py`

Behavior summary:
- Lead (Binance) book feeds signal, follower (Bybit) executes.
- **Event-Driven:** Refreshes quotes on both Leader and Follower updates.
- **Batching:** Uses `SubmitOrderList` for atomic placement of new Bid/Ask pairs (halves API calls on reset).
- Dynamic order sizing:
  - Volatility scalar based on diff bps.
  - Liquidity scalar based on leader book **bid (BUY)** and **ask (SELL)** walls.
- Guard rails:
  - Local guard: cancels quotes on extreme diff.
  - Global guard: cancels when cross-market diff spikes.
- Equity drawdown killswitch:
  - `max_drawdown_pct` (default 5%) halts trading when equity drops past limit.
- **Inventory Management:**
  - Neutral target (0).
  - Skews prices to offload inventory bags (e.g., selling aggressively if long).

Config parameters to tune:
- `spread_bps`, `guard_threshold_bps`, `global_guard_threshold_bps`
- `order_qty`, `min_order_qty`, `max_order_qty`, `max_position_qty`
- `liquidity_high_qty`, `liquidity_low_qty`, `liquidity_*_scalar`
- `max_drawdown_pct` (equity protection)

## Live Runner (staged only)
- `examples/live/bybit/bybit_lead_lag_mm_multi_v3.py`
- Uses per-symbol sizing + liquidity thresholds.
- **Rate Limits:** BTC@400ms, ETH@1000ms, SOL@1000ms.
- **Reconciliation:** Enabled for live sync.
- VPS runner: `scripts/runners/run_live_trading_multi_v3.py` (in `/home/ubuntu/trading`).
- Service management: `leadlag-v003.service` (systemd).

## Backtesting
### Single Pair (v003)
- `examples/backtest/lead_lag_bybit_binance_backtest.py`
- Supports QuestDB or file-based input.

### Multi Pair (v003)
- `examples/backtest/lead_lag_bybit_binance_backtest_multi_v003.py`
- Runs BTC/ETH/SOL in one engine.
- Shared BYBIT cash account balance across symbols.

## Data Pipeline
1. **Capture** on VPS (orderbook deltas, L2 depth).
2. **Sync** to local `data/ob_data_live/`.
3. **Ingest** to QuestDB `orderbook_deltas` table.
4. **Build bars** to QuestDB `bars_1m_mid` table (SpotMatrix).
5. **Backtest** queries QuestDB by date/venue/symbol.

### Live Capture (Bybit Spot)
- DOT/LINK orderbook + tick capture service: `capture_dot_link.service` (VPS).
- Capture outputs:
  - `/home/ubuntu/trading/data/ob_data_live/DOTUSDT_Spot/*.data`
  - `/home/ubuntu/trading/data/ob_data_live/LINKUSDT_Spot/*.data`
  - `/home/ubuntu/trading/data/tick_data/DOTUSDT_Spot/*.csv`
  - `/home/ubuntu/trading/data/tick_data/LINKUSDT_Spot/*.csv`

### QuestDB Table
`orderbook_deltas` expected columns:
- `timestamp` (designated)
- `venue` (BINANCE/BYBIT)
- `symbol` (BTCUSDT/ETHUSDT/SOLUSDT)
- `side` (BUY/SELL)
- `price`, `size`
- `snapshot` (bool)

### QuestDB Access
  `docker run -d --name questdb -p 9000:9000 -p 9009:9009 -v ./data/questdb:/root/.questdb questdb/questdb:latest`

## Operational Guidance
- Use QuestDB for realistic L2 backtesting.
- Ensure both venues have data for the same date per symbol.
- Tune per-symbol liquidity thresholds (BTC/ETH/SOL differ).
- Avoid excessive guard logging by setting `log_guard_events=False` in test configs.

## Security & Secrets
- Do **not** commit VPS IPs, SSH keys, credentials.
- Use `.ssh/config` and environment variables for remote access.
- Reference VPS by SSH alias instead of hardcoding IPs.

## Quick Backtest Examples
Multi-symbol (QuestDB):
- `python examples/backtest/lead_lag_bybit_binance_backtest_multi_v003.py --questdb --questdb-host 127.0.0.1 --questdb-port 9000 --questdb-table orderbook_deltas --questdb-step-seconds 300 --date YYYY-MM-DD --max-updates 50000`

Single-pair (QuestDB):
- `python examples/backtest/lead_lag_bybit_binance_backtest.py --questdb --questdb-host 127.0.0.1 --questdb-port 9000 --questdb-table orderbook_deltas --questdb-step-seconds 300 --date YYYY-MM-DD --max-updates 50000`
