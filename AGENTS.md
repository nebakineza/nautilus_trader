# AGENTS.md

## Purpose
This document orients automated agents to the project structure, data pipeline, available strategies, and operational workflows for the Nebakineza trading system built on NautilusTrader.

---

## Repo Layout

```
nautilus_trader/
├── strategy/                    # Strategy implementations
│   ├── lead_lag_bybit_binance_mm_v005_primer.py  # PRODUCTION (Feb 2026)
│   ├── lead_lag_bybit_binance_mm_v006_turbo.py   # Performance-optimized (testing)
│   ├── lead_lag_bybit_binance_mm_v004_primer.py  # Legacy
│   ├── hft_obi_*.py                              # HFT OBI strategies
│   ├── triangular_arb_v00*.py                    # Triangular arbitrage
│   ├── inventory/                                # Inventory management
│   │   └── cross_pair_coordinator.py
│   ├── analysis/                                 # Strategy analysis tools
│   └── metrics/                                  # Performance metrics
├── examples/
│   ├── backtest/               # Backtest runners and loaders
│   │   ├── llmmv4_primer_backtest.py
│   │   └── questdb_orderbook_loader.py
│   └── live/                   # Live trading runners
│       └── bybit/
├── scripts/
│   ├── runners/                # Production strategy runners
│   │   ├── run_vip_*.py        # Per-pair VIP runners (8 pairs)
│   │   └── run_vip_sui_v6.py   # v006 turbo test runner
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

### 1. Lead/Lag Market Maker v005 (PRODUCTION - Feb 2026)
**File:** `strategy/lead_lag_bybit_binance_mm_v005_primer.py`
**Runners:** `scripts/runners/run_vip_{pair}.py` (8 separate services)

**Architecture:**
- **Leader:** Binance SPOT orderbook feeds signal
- **Follower:** Bybit SPOT executes trades
- **Event-Driven:** Refreshes quotes on Leader/Follower book updates
- **Batching:** Uses `SubmitOrderList` for atomic bid/ask placement

**Key Features:**
- Regime detection (RANGING/TRENDING) with auto spread/size adjustment
- Guardian system: Auto-widens spreads on negative realized PnL
- Killswitch: Equity drawdown protection
- OFI (Order Flow Imbalance) signal integration

**Key Parameters:**
```python
spread_bps=60.0              # Target spread in basis points
order_qty=20.0               # Base order size
max_position_qty=200.0       # Maximum inventory
regime_trending_spread_mult=2.0  # 2x spread in trends
regime_trending_size_mult=0.5    # 0.5x size in trends
```

**Instrument ID Format:**
- Leader: `{SYMBOL}.BINANCE_SPOT` (e.g., `SUIUSDT.BINANCE_SPOT`)
- Follower: `{SYMBOL}-SPOT.BYBIT` (e.g., `SUIUSDT-SPOT.BYBIT`)

### 2. Lead/Lag Market Maker v006 TURBO (Testing)
**File:** `strategy/lead_lag_bybit_binance_mm_v006_turbo.py`
**Status:** Performance-optimized rewrite, in testing

**Optimizations over v005:**
- Float64 arithmetic (no Decimal in hot path) - 4x faster
- Ring buffers (no heap allocations)
- Guarded logging - 17x faster when disabled
- Pre-computed constants
- `__slots__` for faster attribute access

### 3. Legacy Strategies
- `lead_lag_bybit_binance_mm_v004_primer.py` - Previous production
- `lead_lag_bybit_binance_mm_v003.py` - Legacy

---

## Live Deployment (VPS: sentinel-vps)

### Current Production (Feb 2026) - 8 Pair Portfolio
```
/home/ubuntu/trading/
├── strategy_pkg/
│   ├── run_vip_sui.py          # MAINACC_01
│   ├── run_vip_link.py         # MAINACC_02
│   ├── run_vip_avax.py         # MAINACC_03
│   ├── run_vip_ena.py          # MAINACC_04
│   ├── run_vip_near.py         # MAINACC_05
│   ├── run_vip_arb.py          # MAINACC_06
│   ├── run_vip_ondo.py         # MAINACC_07
│   ├── run_vip_ton.py          # MAINACC_08
│   └── lead_lag_bybit_binance_mm_v005_primer.py
├── .env                        # API keys (MAINACC_01-08)
├── logs/                       # Per-pair logs
└── runtime_env/                # Python environment
```

### Active Pairs Configuration
| Pair | API Key | Order Qty | Spread | Notes |
|------|---------|-----------|--------|-------|
| SUIUSDT | MAINACC_01 | 20 SUI | 60bps | Primary |
| LINKUSDT | MAINACC_02 | 1 LINK | 60bps | |
| AVAXUSDT | MAINACC_03 | 1 AVAX | 60bps | |
| ENAUSDT | MAINACC_04 | 50 ENA | 60bps | |
| NEARUSDT | MAINACC_05 | 10 NEAR | 60bps | |
| ARBUSDT | MAINACC_06 | 50 ARB | 60bps | |
| ONDOUSDT | MAINACC_07 | 25 ONDO | 60bps | |
| TONUSDT | MAINACC_08 | 5 TON | 60bps | |

### Systemd Services
```bash
# List all services
sudo systemctl list-units 'vip_*.service'

# Per-pair management
sudo systemctl status vip_sui.service
sudo systemctl restart vip_sui.service
sudo journalctl -u vip_sui.service -f

# Restart all pairs
for svc in sui link avax ena near arb ondo ton; do
  sudo systemctl restart vip_$svc.service
done
```

### Deploy Updates
```bash
# Deploy strategy file
scp strategy/lead_lag_bybit_binance_mm_v005_primer.py \
    sentinel-vps:/home/ubuntu/trading/strategy_pkg/

# Deploy specific runner
scp scripts/runners/run_vip_sui.py \
    sentinel-vps:/home/ubuntu/trading/strategy_pkg/

# Restart service
ssh sentinel-vps "sudo systemctl restart vip_sui.service"
```

### API Keys Structure
- `BYBIT_API_KEY_MAINACC_00` - Local development (sentinel)
- `BYBIT_API_KEY_MAINACC_01-08` - VPS production (IP-bound)

---

## Fee Profile (Bybit VIP0 + MNT Discount)
- Maker: 0.075% (7.5 bps)
- Taker: 0.075% (7.5 bps)
- Roundtrip: 15 bps
- **Minimum profitable spread: >15 bps**

---

## Data Pipeline

### 1. Sync from VPS
```bash
rsync -avz sentinel-vps:/path/to/data/ ./data/ob_data/
```

### 2. Ingest to QuestDB
```bash
.venv/bin/python scripts/questdb_ingest_ob_data.py \
    --source data/ob_data/ --symbol SUIUSDT
```

### 3. CoinAPI Historical
```bash
export COIN_API="your-api-key"
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 --exchange BINANCE --symbol SUIUSDT
```

### 4. Backtest
```bash
.venv/bin/python examples/backtest/llmmv4_primer_backtest.py \
    --pair SUIUSDT --date 2026-01-28 --spread-bps 60 --profile balanced
```

---

## QuestDB (Sentinel Data Lake)

**Local installation on sentinel machine.**

### Start/Stop
```bash
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh start
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh stop
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh status
```

### Ports
| Port | Service |
|------|---------|
| 9000 | HTTP/Web Console |
| 9009 | ILP TCP ingest |
| 8812 | Postgres wire |

---

## Operational Guidance

### Performance Monitoring
```bash
# Check all pairs status
for pair in sui link avax ena near arb ondo ton; do
  echo "=== ${pair^^} ==="
  grep -c 'OrderFilled' /home/ubuntu/trading/logs/vip_${pair}.log
done

# Check regime changes
grep 'REGIME CHANGE' /home/ubuntu/trading/logs/vip_*.log | tail -20

# Check guardian triggers
grep 'GUARDIAN' /home/ubuntu/trading/logs/vip_*.log | tail -10
```

### Common Issues & Fixes

1. **Killswitch Triggering**
   - Symptom: "KILLSWITCH TRIGGERED: Drawdown X%"
   - Fix: Set `max_drawdown_pct=Decimal("1.00")` (disabled)

2. **Guardian Widening Too Much**
   - Symptom: "Widening spread +40.0bps"
   - Fix: Guardian is working correctly - wait for spread to relax

3. **Binance Connection Error**
   - Symptom: `instrument_id.venue BINANCE_SPOT != BINANCE`
   - Fix: Use `.BINANCE_SPOT` for leader instrument IDs

4. **Order Value Too Low**
   - Symptom: `Order value exceeded lower limit`
   - Fix: Increase `order_qty` to meet Bybit minimums

### Best Practices
- Use `tags=["smp_type=CancelMaker"]` for self-match prevention
- Enable `post_only=True` for maker-only orders
- Keep spread_bps > 15 (fee breakeven for VIP0+MNT)
- Monitor win rate: target >70%
- Monitor avg net: target >20 bps per trade

---

## Security & Secrets

**NEVER commit:**
- API keys, secrets, passwords
- VPS IPs, SSH keys
- `.env` files
- Session/state files (`SENTINEL-*.json`)

**Use:**
- `.ssh/config` for remote access aliases
- Environment variables for credentials
- `.gitignore` for sensitive files

---

## Quick Reference

```bash
# Check all VPS services
ssh sentinel-vps "sudo systemctl list-units 'vip_*.service' --no-pager"

# View live logs for SUI
ssh sentinel-vps "tail -f /home/ubuntu/trading/logs/vip_sui.log"

# Check P&L summary
ssh sentinel-vps "grep 'FILL PAIR:' /home/ubuntu/trading/logs/vip_*.log | tail -20"

# Count fills today
ssh sentinel-vps "grep 'OrderFilled' /home/ubuntu/trading/logs/vip_*.log | wc -l"

# Restart all services
ssh sentinel-vps "for s in sui link avax ena near arb ondo ton; do sudo systemctl restart vip_\$s; done"

# Check QuestDB
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh status
```
