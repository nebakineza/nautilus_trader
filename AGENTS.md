# AGENTS.md

## Purpose
This document orients automated agents to the project structure, data pipeline, available strategies, and operational workflows for the Nebakineza trading system built on NautilusTrader.

---

## Repo Layout

```
nautilus_trader/
├── strategy/                    # Strategy implementations
│   ├── lead_lag_bybit_binance_mm_v008_timekeeper.py  # PRODUCTION (Feb 2026)
│   ├── lead_lag_bybit_binance_mm_v007_fortress.py    # Previous production
│   ├── lead_lag_bybit_binance_mm_v006_turbo.py       # Performance-optimized
│   ├── lead_lag_bybit_binance_mm_v005_primer.py      # Legacy
│   ├── hft_obi_*.py                                  # HFT OBI strategies
│   ├── triangular_arb_v00*.py                        # Triangular arbitrage
│   ├── inventory/                                    # Inventory management
│   │   └── cross_pair_coordinator.py
│   ├── analysis/                                     # Strategy analysis tools
│   └── metrics/                                      # Performance metrics
├── examples/
│   ├── backtest/               # Backtest runners and loaders
│   │   ├── llmmv4_primer_backtest.py
│   │   └── questdb_orderbook_loader.py
│   └── live/                   # Live trading runners
│       └── bybit/
├── scripts/
│   ├── runners/                # Production strategy runners
│   │   ├── run_vip_*_v8.py     # Per-pair v008 runners (10 pairs)
│   │   └── run_vip_*_v6.py     # Legacy v006 runners
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

### 1. Lead/Lag Market Maker v008 TIMEKEEPER (PRODUCTION - Feb 2026)
**File:** \`strategy/lead_lag_bybit_binance_mm_v008_timekeeper.py\`
**Runners:** \`scripts/runners/run_vip_{pair}_v8.py\` (10 separate services)
**Version:** v008.14

**Architecture:**
- **Leader:** Binance SPOT orderbook feeds signal
- **Follower:** Bybit SPOT executes trades
- **Event-Driven:** Refreshes quotes on Leader/Follower book updates
- **Order Management:** Cancel-replace with IOC aggressive exits

**Key Features (v008):**
- **TIMEKEEPER**: Inventory age tracking with forced exits
- **FIFO P&L**: Per-trade profit tracking with timestamps
- **Balance Capping**: Prevents "Insufficient balance" rejections
- **Entry Quality Filter**: Blocks entries during strong trends
- **Regime Detection**: RANGING/TRENDING with auto spread/size adjustment
- **Underwater Exit**: Skews quotes to exit losing positions faster
- **Runaway Detection**: Pauses one-sided fills

**Key Parameters:**
```python
spread_bps=80.0                    # Target spread in basis points
order_qty=20.0                     # Base order size
max_position_qty=200.0             # Maximum inventory
inventory_max_hold_secs=1800       # Force exit after 30min
entry_quality_max_trend_strength=0.50  # Block entry if trend > 50%
fifo_pnl_enabled=True              # Track per-trade P&L
underwater_exit_enabled=True       # Skew to exit losers
```

**Critical Fixes (v008.14):**
- SELL orders capped to \`net_position * 0.98\` (prevents over-selling)
- Aggressive exits cancel open orders first + 90% balance buffer
- Minimum order value check (\$5.50 for Bybit)
- Balance tracking updated on every fill

**Instrument ID Format:**
- Leader: \`{SYMBOL}.BINANCE_SPOT\` (e.g., \`SUIUSDT.BINANCE_SPOT\`)
- Follower: \`{SYMBOL}-SPOT.BYBIT\` (e.g., \`SUIUSDT-SPOT.BYBIT\`)

### 2. Lead/Lag Market Maker v007 FORTRESS (Previous)
**File:** \`strategy/lead_lag_bybit_binance_mm_v007_fortress.py\`
**Status:** Deprecated - replaced by v008

### 3. Lead/Lag Market Maker v006 TURBO (Performance)
**File:** \`strategy/lead_lag_bybit_binance_mm_v006_turbo.py\`
**Status:** Performance-optimized rewrite, available for testing

**Optimizations over v005:**
- Float64 arithmetic (no Decimal in hot path) - 4x faster
- Ring buffers (no heap allocations)
- Guarded logging - 17x faster when disabled
- Pre-computed constants
- \`__slots__\` for faster attribute access

### 4. Legacy Strategies
- \`lead_lag_bybit_binance_mm_v005_primer.py\` - Previous production
- \`lead_lag_bybit_binance_mm_v004_primer.py\` - Legacy
- \`lead_lag_bybit_binance_mm_v003.py\` - Legacy

---

## Live Deployment (VPS: sentinel-vps)

### Current Production (Feb 2026) - 10 Pair Portfolio (v008)
```
/home/ubuntu/trading/
├── strategy_pkg/
│   ├── run_vip_sui_v8.py           # MAINACC_01
│   ├── run_vip_link_v8.py          # MAINACC_02
│   ├── run_vip_avax_v8.py          # MAINACC_03
│   ├── run_vip_ena_v8.py           # MAINACC_04
│   ├── run_vip_near_v8.py          # MAINACC_05
│   ├── run_vip_arb_v8.py           # MAINACC_06
│   ├── run_vip_ondo_v8.py          # MAINACC_07
│   ├── run_vip_ton_v8.py           # MAINACC_08
│   ├── run_vip_sei_v8.py           # MAINACC_09
│   ├── run_vip_apt_v8.py           # MAINACC_10
│   └── lead_lag_bybit_binance_mm_v008_timekeeper.py
├── .env                            # API keys (MAINACC_01-10)
├── logs/                           # Per-pair logs
└── runtime_env/                    # Python environment
```

### Active Pairs Configuration (v008)
| Pair | API Key | Spread | Max Hold | Entry Filter | Notes |
|------|---------|--------|----------|--------------|-------|
| SUIUSDT | MAINACC_01 | 120bps | 600s | 0.40 | Tuned |
| LINKUSDT | MAINACC_02 | 150bps | 300s | 0.25 | Aggressive |
| AVAXUSDT | MAINACC_03 | 80bps | 1800s | 0.50 | Default |
| ENAUSDT | MAINACC_04 | 80bps | 1800s | 0.50 | Default |
| NEARUSDT | MAINACC_05 | 80bps | 1800s | 0.50 | Default |
| ARBUSDT | MAINACC_06 | 80bps | 1800s | 0.50 | Default |
| ONDOUSDT | MAINACC_07 | 80bps | 1800s | 0.50 | Default |
| TONUSDT | MAINACC_08 | 80bps | 1800s | 0.50 | Default |
| SEIUSDT | MAINACC_09 | 120bps | 600s | 0.35 | Tuned |
| APTUSDT | MAINACC_10 | 150bps | 300s | 0.30 | Aggressive |

### Systemd Services (v008)
```bash
# List all v008 services
sudo systemctl list-units 'vip_*_v8.service'

# Per-pair management
sudo systemctl status vip_sui_v8.service
sudo systemctl restart vip_sui_v8.service
sudo journalctl -u vip_sui_v8.service -f

# Restart all pairs
for pair in sui link avax ena near arb ondo ton sei apt; do
  sudo systemctl restart vip_\${pair}_v8.service
done
```

### Deploy Updates
```bash
# Deploy strategy file
scp strategy/lead_lag_bybit_binance_mm_v008_timekeeper.py \\
    sentinel-vps:/home/ubuntu/trading/strategy_pkg/

# Deploy specific runner
scp scripts/runners/run_vip_sui_v8.py \\
    sentinel-vps:/home/ubuntu/trading/strategy_pkg/

# Restart service
ssh sentinel-vps "sudo systemctl restart vip_sui_v8.service"

# Restart all v008 services
ssh sentinel-vps 'for pair in sui link avax ena near arb ondo ton sei apt; do
  sudo systemctl restart vip_\${pair}_v8.service
done'
```

### API Keys Structure
- \`BYBIT_API_KEY_MAINACC_00\` - Local development (sentinel)
- \`BYBIT_API_KEY_MAINACC_01-10\` - VPS production (IP-bound)

---

## Fee Profile (Bybit VIP0 + MNT Discount)
- Maker: 0.10% (10 bps)
- Taker: 0.10% (10 bps)
- Roundtrip: 20 bps
- **Minimum profitable spread: >20 bps**

---

## Data Pipeline

### 1. Sync from VPS
```bash
rsync -avz sentinel-vps:/path/to/data/ ./data/ob_data/
```

### 2. Ingest to QuestDB
```bash
.venv/bin/python scripts/questdb_ingest_ob_data.py \\
    --source data/ob_data/ --symbol SUIUSDT
```

### 3. CoinAPI Historical
```bash
export COIN_API="your-api-key"
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \\
    --date 2026-01-28 --exchange BINANCE --symbol SUIUSDT
```

### 4. Backtest
```bash
.venv/bin/python examples/backtest/llmmv4_primer_backtest.py \\
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

### Performance Monitoring (v008)
```bash
# Check all pairs status
ssh sentinel-vps 'for pair in sui link avax ena near arb ondo ton sei apt; do
  echo "=== \${pair^^} ==="
  journalctl -u vip_\${pair}_v8.service --no-pager --since "1 hour ago" | grep -c "OrderFilled"
done'

# Check rejection status
ssh sentinel-vps 'for pair in sui link avax ena near arb ondo ton sei apt; do
  insuff=\$(journalctl -u vip_\${pair}_v8.service --no-pager --since "1 hour ago" | grep -c "Insufficient balance")
  value=\$(journalctl -u vip_\${pair}_v8.service --no-pager --since "1 hour ago" | grep -c "value exceeded")
  postonly=\$(journalctl -u vip_\${pair}_v8.service --no-pager --since "1 hour ago" | grep -c "PostOnly")
  printf "%-6s: insuff=%s value=%s postonly=%s\\n" "\${pair^^}" "\$insuff" "\$value" "\$postonly"
done'

# Check regime changes
ssh sentinel-vps 'journalctl -u "vip_*_v8.service" --since "1 hour ago" | grep "REGIME:" | tail -20'

# Check force exits
ssh sentinel-vps 'journalctl -u "vip_*_v8.service" --since "1 hour ago" | grep "FORCE EXIT" | tail -10'
```

### Common Issues & Fixes (v008)

1. **"Insufficient balance" Rejections**
   - Fixed in v008.14: SELL orders capped to \`net_position * 0.98\`
   - Aggressive exits use 90% buffer and cancel open orders first

2. **"Order value exceeded lower limit"**
   - Fixed in v008.13: Minimum order value check (\$5.50)
   - Orders below this are skipped (dust positions)

3. **"EC_PostOnlyWillTakeLiquidity"**
   - Normal behavior - market moved and our price crossed spread
   - Not an error - Bybit correctly rejected to prevent taking liquidity

4. **Trend Filter Pausing**
   - Symptom: "TREND FILTER: EMA=X > threshold for Y ticks | PAUSING"
   - Normal behavior - strategy pauses during strong trends
   - Adjust \`entry_quality_max_trend_strength\` if too aggressive

5. **Force Exit Skipped**
   - Symptom: "FORCE EXIT SKIPPED: Order value \$X below \$5 min"
   - Dust position too small to exit - will be combined with future trades

### Best Practices
- Use \`spread_bps > 20\` (fee breakeven for VIP0+MNT)
- Set \`inventory_max_hold_secs\` based on pair volatility (300-1800s)
- Tighter \`entry_quality_max_trend_strength\` for volatile pairs (0.25-0.40)
- Monitor win rate: target >60%
- Monitor realized spread: target >20 bps after fees

---

## Security & Secrets

**NEVER commit:**
- API keys, secrets, passwords
- VPS IPs, SSH keys
- \`.env\` files
- Session/state files (\`SENTINEL-*.json\`)

**Use:**
- \`.ssh/config\` for remote access aliases
- Environment variables for credentials
- \`.gitignore\` for sensitive files

---

## Quick Reference (v008)

```bash
# Check all VPS services
ssh sentinel-vps "sudo systemctl list-units 'vip_*_v8.service' --no-pager"

# View live logs for SUI
ssh sentinel-vps "journalctl -u vip_sui_v8.service -f"

# Check fills summary
ssh sentinel-vps 'for pair in sui link avax ena near arb ondo ton sei apt; do
  fills=\$(journalctl -u vip_\${pair}_v8.service --no-pager --since "1 hour ago" | grep -c "OrderFilled")
  printf "%-6s: %s fills\\n" "\${pair^^}" "\$fills"
done'

# Check rejection summary  
ssh sentinel-vps 'journalctl --since "1 hour ago" | grep -cE "Insufficient balance|value exceeded lower limit"'

# Restart all services
ssh sentinel-vps 'for pair in sui link avax ena near arb ondo ton sei apt; do
  sudo systemctl restart vip_\${pair}_v8.service
done'

# Deploy and restart
scp strategy/lead_lag_bybit_binance_mm_v008_timekeeper.py sentinel-vps:/home/ubuntu/trading/strategy_pkg/ && \\
ssh sentinel-vps 'for pair in sui link avax ena near arb ondo ton sei apt; do
  sudo systemctl restart vip_\${pair}_v8.service
done'

# Check QuestDB
/home/seb/questdb-9.3.2-rt-linux-x86-64/bin/questdb.sh status
```
