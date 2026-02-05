# AGENTS.md

## Purpose
This document orients automated agents to the project structure, data pipeline, available strategies, and operational workflows for the Nebakineza trading system built on NautilusTrader.

---

## Repo Layout

```
nautilus_trader/
├── strategy/                    # Strategy implementations
│   ├── lead_lag_bybit_binance_mm_v00*.py   # Lead/Lag MM strategies
│   ├── stablecoin_mm_v001.py               # Stablecoin market maker
│   ├── triangular_arb_v00*.py              # Triangular arbitrage
│   ├── ofi_microstructure_mm_v001.py       # Order flow imbalance MM
│   ├── inventory/                          # Inventory management modules
│   │   └── cross_pair_coordinator.py       # Multi-pair inventory balancer
│   ├── analysis/                           # Strategy analysis tools
│   └── metrics/                            # Performance metrics
├── examples/
│   ├── backtest/               # Backtest runners and loaders
│   │   ├── llmmv3_primer_backtest.py       # Multi-pair backtest runner
│   │   ├── llmmv4_primer_backtest.py       # v4 backtest runner
│   │   ├── llmmv3_primer_sweep.py          # Parameter sweep runner
│   │   └── questdb_orderbook_loader.py     # QuestDB data loader
│   └── live/                   # Live trading runners
│       └── bybit/              # Bybit-specific live runners
├── scripts/
│   ├── runners/                # Production strategy runners
│   │   ├── run_live_trading_multi_v3_primer.py
│   │   └── run_vip_*_production.py         # VIP pair runners
│   ├── questdb_*.py            # QuestDB ingest/query scripts
│   ├── coinapi_*.py            # CoinAPI data ingest scripts
│   └── sweep_*.py              # Parameter sweep scripts
├── deploy/                     # Systemd service files
├── docs/                       # Documentation
└── data/                       # Local data (gitignored)
    ├── ob_data/                # Orderbook data
    └── tick_data/              # Tick data
```

---

## STRICT: Python Environment

**ALWAYS** use the project virtual environment:
```bash
# NEVER call python or pip directly
/home/seb/nebakineza/nautilus_trader/.venv/bin/python
/home/seb/nebakineza/nautilus_trader/.venv/bin/pip
```

---

## Strategies

### 1. Lead/Lag Market Maker v004 (PRODUCTION)
**File:** `strategy/lead_lag_bybit_binance_mm_v004_primer.py`
**Runner:** `scripts/runners/run_vip_production_v3.py` (VPS)

**Architecture:**
- **Leader:** Binance SPOT orderbook feeds signal
- **Follower:** Bybit SPOT executes trades
- **Event-Driven:** Refreshes quotes on both Leader and Follower book updates
- **Batching:** Uses `SubmitOrderList` for atomic bid/ask placement

**Key Parameters:**
- `spread_bps`: Target spread in basis points (25-35 for profitable MM)
- `order_qty`: Base order size
- `max_position_qty`: Maximum inventory position
- `guard_threshold_bps`: Price movement guard threshold
- `ofi_enabled`: Order Flow Imbalance signal
- `max_drawdown_pct`: Drawdown killswitch (set to 1.0 to disable)

**Instrument ID Format:**
- Leader: `{SYMBOL}.BINANCE` (e.g., `SUIUSDT.BINANCE`)
- Follower: `{SYMBOL}-SPOT.BYBIT` (e.g., `SUIUSDT-SPOT.BYBIT`)

### 2. Lead/Lag Market Maker v003 (LEGACY)
**File:** `strategy/lead_lag_bybit_binance_mm_v003.py`
**Runner:** `examples/live/bybit/bybit_lead_lag_mm_multi_v3.py`

Same architecture as v004 but without inventory coordinator support.

### 3. Stablecoin Market Maker
**File:** `strategy/stablecoin_mm_v001.py`
**Runner:** `scripts/runners/run_stablecoin_mm_backtest.py`

- Centers quotes around fixed price (1.0000) or dynamic mid-price
- Inventory skewing for risk management
- Self-match prevention (SMP) tags

### 4. Triangular Arbitrage
**File:** `strategy/triangular_arb_v001.py`
**Runner:** `scripts/runners/run_triangular_arb_backtest.py`

- 3-leg arbitrage (e.g., USDT→ETH→BTC→USDT)
- IOC Market orders for rapid execution
- SMP tags: `smp_type=CancelMaker`

### 5. Cross-Pair Inventory Coordinator
**File:** `strategy/inventory/cross_pair_coordinator.py`

- Balances inventory across multiple trading pairs
- Configurable `max_pair_weight` (default 50%)
- Can pause individual pairs when inventory exceeds limits
- **Note:** Disable for independent pair trading

---

## Live Deployment (VPS: sentinel-vps)

### Production Runner Location
```
/home/ubuntu/trading/
├── strategy_pkg/
│   ├── run_vip_production_v3.py    # Current production runner
│   ├── lead_lag_bybit_binance_mm_v004_primer.py
│   └── cross_pair_coordinator.py
├── .env                            # API keys
├── logs/                           # Trading logs
└── runtime_env/                    # Python environment
```

### Systemd Service
```bash
# Service file: /etc/systemd/system/vip_sui_heavy.service
sudo systemctl status vip_sui_heavy.service
sudo systemctl restart vip_sui_heavy.service
sudo journalctl -u vip_sui_heavy.service -f
```

### Deploy Updates
```bash
# From local machine
scp strategy/lead_lag_bybit_binance_mm_v004_primer.py sentinel-vps:/home/ubuntu/trading/strategy_pkg/
ssh sentinel-vps "sudo systemctl restart vip_sui_heavy.service"
```

### API Keys
- `BYBIT_API_KEY_LLMM` / `BYBIT_API_SECRET_LLMM` - Production (IP-bound to VPS)
- `BYBIT_API_KEY_SENTINEL` / `BYBIT_API_SECRET_SENTINEL` - Sentinel local

---

## Current Production Configuration (Feb 2026)

### Active Pairs (5-Pair Portfolio)
| Pair | Balance | Order Qty | Spread | Notes |
|------|---------|-----------|--------|-------|
| SUIUSDT | ~$450 | 80 SUI | 25bps | Primary volume |
| DOGEUSDT | ~$550 | 1500 DOGE | 35bps | High inventory |
| ETHUSDT | ~$22 | 0.005 ETH | 25bps | Small size |
| LINKUSDT | ~$53 | 2 LINK | 30bps | Medium |
| AVAXUSDT | ~$55 | 2 AVAX | 30bps | Medium |

### Fee Profile (Bybit VIP0 + MNT Discount)
- Maker: 0.075% (7.5 bps)
- Taker: 0.075% (7.5 bps)
- Roundtrip: 15 bps
- **Minimum profitable spread: >15 bps**

### VIP Tier Targets
- VIP1: $33.33K daily volume (goal)
- Current: Working towards VIP1

---

## Data Pipeline

### 1. Data Capture (VPS)
Orderbook deltas captured on VPS using adapter scripts.

### 2. Sync to Local
```bash
rsync -avz sentinel-vps:/path/to/data/ ./data/ob_data/
```

### 3. Ingest to QuestDB
```bash
.venv/bin/python scripts/questdb_ingest_ob_data.py \
    --source data/ob_data/ \
    --symbol SUIUSDT
```

### 4. CoinAPI Ingest (Historical)
```bash
export COIN_API="your-api-key"
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 \
    --exchange BINANCE \
    --symbol SUIUSDT \
    --coinapi-symbol-id BINANCE_SPOT_SUI_USDT
```

### 5. Backtest
```bash
.venv/bin/python examples/backtest/llmmv4_primer_backtest.py \
    --pair SUIUSDT \
    --date 2026-01-28 \
    --spread-bps 25 \
    --profile balanced
```

---

## QuestDB (Sentinel Data Lake)

**Local, non-Docker installation on sentinel machine.**

### Ports
| Port | Service |
|------|---------|
| 9000 | HTTP/Web Console |
| 9009 | ILP TCP ingest (required) |
| 8812 | Postgres wire |
| 9003 | Health |

### Start/Stop
```bash
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh start
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh stop
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh status
```

### Tables
- `orderbook_deltas`: timestamp, venue, symbol, side, price, size, snapshot
- `backtest_fills`: Strategy fill records
- `inventory_snapshots`: Balance snapshots
- `fee_snapshots`: Fee tier records

---

## Backtest Suite

### Profiles
| Profile | Fill Model | Latency | Liquidity Consumption |
|---------|------------|---------|----------------------|
| `accuracy` | competition | Yes | Yes |
| `balanced` | competition | Low | Yes |
| `speed` | best_price | No | No |

### Fee Profiles (Bybit Spot)
| Tier | Maker | Taker |
|------|-------|-------|
| vip0 | 0.10% | 0.10% |
| vip0+mnt | 0.075% | 0.075% |
| vip1 | 0.0675% | 0.08% |
| vip2 | 0.065% | 0.0775% |
| vip3 | 0.0625% | 0.075% |
| vip4 | 0.05% | 0.06% |
| vip5 | 0.04% | 0.05% |
| supreme | 0.03% | 0.045% |

### Parameter Sweeps
```bash
.venv/bin/python scripts/sweep_profitable_spreads_v2.py \
    --pair SUIUSDT \
    --date 2026-01-28 \
    --spread-min 20 \
    --spread-max 50 \
    --spread-step 5
```

---

## Operational Guidance

### Common Issues & Fixes

1. **Killswitch Triggering**
   - Symptom: Pairs stop quoting with "KILLSWITCH TRIGGERED: Drawdown X%"
   - Fix: Set `max_drawdown_pct=Decimal("1.00")` (100% = disabled)

2. **Cross-Pair Coordinator Pausing**
   - Symptom: "DOGE inventory at 72.9% (max: 50.0%) - PAUSING pair"
   - Fix: Increase `max_pair_weight` or disable coordinator

3. **Binance Connection Error**
   - Symptom: `ValueError: instrument_id.venue BINANCE_SPOT != BINANCE`
   - Fix: Use `.BINANCE` not `.BINANCE_SPOT` for leader instrument IDs

4. **Order Value Too Low**
   - Symptom: `OrderModifyRejected: Order value exceeded lower limit`
   - Fix: Increase `order_qty` to meet Bybit minimums

### Best Practices
- **SMP Tags:** Always use `tags=["smp_type=CancelMaker"]` in live execution
- **Post-Only:** Enable `post_only=True` for maker-only orders
- **Spread > Fees:** Ensure spread_bps > 15 (for VIP0+MNT) to be profitable

---

## Security & Secrets

**NEVER commit:**
- API keys, secrets, passwords
- VPS IPs, SSH keys
- `.env` files

**Use:**
- `.ssh/config` for remote access aliases
- Environment variables for credentials
- `.gitignore` for sensitive files

---

## Quick Reference Commands

```bash
# Check VPS service status
ssh sentinel-vps "sudo systemctl status vip_sui_heavy.service"

# View live logs
ssh sentinel-vps "tail -f /home/ubuntu/trading/logs/vip_sui_heavy.service.log"

# Check orders per pair
ssh sentinel-vps "grep OrderAccepted /home/ubuntu/trading/logs/*.log | grep -oE '(SUI|DOGE|ETH|LINK|AVAX)USDT' | sort | uniq -c"

# Restart production
ssh sentinel-vps "sudo systemctl restart vip_sui_heavy.service"

# Check QuestDB status
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh status

# Run backtest
.venv/bin/python examples/backtest/llmmv4_primer_backtest.py --pair SUIUSDT --date 2026-01-28
```
