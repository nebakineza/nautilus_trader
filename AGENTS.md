# AGENTS.md

## Purpose
This document orients automated agents to the project structure, data pipeline, and available strategies.

## Repo Layout
- `strategy/` — Strategy implementations and research docs.
- `examples/backtest/` — Backtest runners and loaders.
- `examples/live/` — Live runners.
- `scripts/runners/` — Production strategy runners.
- `docs/` — Pipeline documentation.

## STRICT: Python Environment
- ALWAYS use the project virtual environment at `/home/seb/nebakineza/nautilus_trader/.venv`.
- Never call `python` or `pip` directly.
- Use:
  - `/home/seb/nebakineza/nautilus_trader/.venv/bin/python`
  - `/home/seb/nebakineza/nautilus_trader/.venv/bin/pip`

## Strategies

### 1. Lead/Lag MM v003
**File:** `strategy/lead_lag_bybit_binance_mm_v003.py`
**Runner:** `examples/live/bybit/bybit_lead_lag_mm_multi_v3.py`

Behavior summary:
- Lead (Binance) book feeds signal, follower (Bybit) executes.
- **Event-Driven:** Refreshes quotes on both Leader and Follower updates.
- **Batching:** Uses `SubmitOrderList` for atomic placement of new Bid/Ask pairs.

### 2. Stablecoin Market Maker
**File:** `strategy/stablecoin_mm_v001.py`
**Runner:** `scripts/runners/run_stablecoin_mm_backtest.py`

Behavior:
- **Liquidity Provision:** Centers quotes around a fixed price (1.0000) or dynamic mid-price (for proxy testing).
- **Inventory Management:** Skews quotes to manage inventory.
- **Safety:** Uses self-match prevention (SMP) tags.

### 3. Triangular Arbitrage v001
**File:** `strategy/triangular_arb_v001.py`
**Runner:** `scripts/runners/run_triangular_arb_backtest.py`

Behavior:
- **Triangular Loop:** Executing 3-leg trades (e.g. USDT->ETH->BTC->USDT) to capture price discrepancies.
- **Execution:** Uses IOC Market orders for rapid execution.
- **Logic:** Calculates cross-rates and triggers when profit > threshold.
- **Self-Match Prevention:** Orders tagged with `smp_type=CancelMaker`.

## Data Pipeline
1. **Capture** on VPS (orderbook deltas, L2 depth).
2. **Sync** to local `data/ob_data/` (or `data/ob_data_live/`).
3. **Ingest** to QuestDB `orderbook_deltas` table using `examples/backtest/questdb_orderbook_loader.py`.
4. **Backtest** using `load_questdb` in runners.

## LLMMv3 Primer Backtest Suite
**Runner:** `examples/backtest/llmmv3_primer_backtest.py`
- Multi-pair (SOL/DOGE/AVAX) Binance leader + Bybit follower
- Supports QuestDB and local `data/ob_data`
- Batch loading for speed (`--batch-size`)
- Fill/latency models (`--fill-model`, `--latency-ms`)
- Liquidity consumption toggle (`--liquidity-consumption`)
- Outputs reports + WAP summary (`wap_summary.json`)

**Profiles:**
- `accuracy`: competition fill model, latency, liquidity consumption
- `balanced`: competition fill model, low latency, liquidity consumption
- `speed`: best price fill model, no latency, no liquidity consumption

**Fee Profiles (Spot Crypto-Crypto):**
- `default`/`vip0` → 0.10% maker / 0.10% taker
- `vip1` → 0.0675% maker / 0.08% taker
- `vip2` → 0.0650% maker / 0.0775% taker
- `vip3` → 0.0625% maker / 0.0750% taker
- `vip4` → 0.0500% maker / 0.0600% taker
- `vip5` → 0.0400% maker / 0.0500% taker
- `supreme` → 0.0300% maker / 0.0450% taker
- `--mnt-discount` applies -25% to both maker/taker

**Market Maker Rebates (Spot MM Program):**
- `mm1`: maker rebate -0.001%
- `mm2`: maker rebate -0.005%
- `mm3`: maker rebate -0.0075%

**Sweep Runner:** `examples/backtest/llmmv3_primer_sweep.py`
- Sweeps profiles/fee tiers, writes `sweep_index.json`

### QuestDB (Sentinel Trade Data Lake)
QuestDB is the **local, non-Docker** trade data lake on **sentinel**. It is **required** for realistic backtests and live metrics.

**Location & Ports**
- Data dir: `/home/seb/.questdb`
- HTTP (SQL/Web Console): `9000`
- ILP ingest (required): `9009`
- Postgres wire: `8812`
- Health: `9003`

**Start/Stop (Binaries install)**
Download and extract QuestDB binaries, then:
- Start: `/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh start`
- Stop: `/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh stop`
- Status: `/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh status`

**Important**
- Do **not** use Docker on sentinel for QuestDB.
- If ILP (9009) is down, ingest scripts will fail.
- QuestDB must be running before running any ingest/sync tasks.

### QuestDB (Local Data Lake on Sentinel)
- QuestDB is a **local, non-Docker** instance on **sentinel** and is the trade data lake for backtests.
- Install (Linux binaries):
  - Download: https://github.com/questdb/questdb/releases/download/9.3.2/questdb-9.3.2-rt-linux-x86-64.tar.gz
  - Extract: `tar -xvf questdb-9.3.2-rt-linux-x86-64.tar.gz`
  - Start: `/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh start`
  - Stop: `/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh stop`
  - Status: `/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh status`
- Ports:
  - 9000 (HTTP/Web Console)
  - 9009 (ILP TCP ingest) **required for scripts**
  - 8812 (Postgres wire)
  - 9003 (Health)
- Data dir: `/home/seb/.questdb` (conf + db)

### QuestDB Table
`orderbook_deltas` expected columns:
- `timestamp` (designated)
- `venue` (BINANCE/BYBIT)
- `symbol` (BTCUSDT/ETHUSDT/SOLUSDT)
- `side` (BUY/SELL)
- `price`, `size`
- `snapshot` (bool)

## Operational Guidance
- Use QuestDB for realistic L2 backtesting.
- **SMP:** Always include `tags=["smp_type=CancelMaker"]` in live execution logic.
- **Proxy Testing:** When data for specific pairs (e.g., USDC) is missing, use proxies (e.g., SOLUSDT) with dynamic centering configurations.

## Security & Secrets
- Do **not** commit VPS IPs, SSH keys, credentials.
- Use `.ssh/config` and environment variables for remote access.
