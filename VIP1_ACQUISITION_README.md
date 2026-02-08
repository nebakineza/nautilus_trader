# VIP1 Acquisition Strategy Suite

## 🎯 Mission Statement

Generate **$33,333 USD daily volume** to achieve Bybit VIP1 tier ($1M/30 days) while maintaining **strict positive PnL** on every currency pair in the portfolio.

### Core Principle: "ZERO LOSS RULE"
Every pair must be individually profitable after trading fees. **Volume is secondary to profitability.**

---

## 📦 Deliverables

This suite provides three production-ready tools:

### 1. **Data Ingestion** (`scripts/coinapi_flatfiles_ingest_orderbook.py`)
✅ **Already exists** - No modifications needed

**Purpose:** Download CoinAPI Flat Files (Binance leader data) and ingest into QuestDB

**Usage:**
```bash
export COIN_API="your-api-key"
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 \
    --exchange BINANCE \
    --symbol SUIUSDT \
    --coinapi-symbol-id BINANCE_SPOT_SUI_USDT
```

---

### 2. **Green Sweep Engine** (`scripts/run_vip_acquisition_sweep.py`)
🆕 **New tool**

**Purpose:** Multi-dimensional parameter sweep across 5-pair portfolio

**Optimization Target:**
```
MAX(volume) WHERE pnl > $0
```

**Parameters Swept:**
- `spread_bps`: [15, 20, 25, 30, 35, 40, 45, 50]
- `guard_threshold_bps`: [10, 15, 20, 25]
- `min_profit_bps`: [1, 2, 3, 5]
- `ofi_max_bps`: [0, 3, 5, 8]
- `refresh_interval_ms`: [3000, 5000, 8000]

**Usage:**
```bash
# Full sweep (5 pairs, ~4-6 hours)
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
    --profile balanced

# Quick sweep (testing)
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs SUIUSDT \
    --quick
```

**Output:**
```
sweep_results/vip_acquisition_TIMESTAMP/
├── sweep_results.json          # All results
└── PAIR_CONFIG_NAME/           # Individual backtest results
    ├── summary.json
    ├── wap_summary.json
    └── fills_report.csv
```

---

### 3. **Portfolio Analyzer** (`scripts/analyze_vip_progress.py`)
🆕 **New tool**

**Purpose:** Select optimal configuration per pair and determine if target is met

**Decision Logic:**
1. Filter all runs where `PnL > $0` (ZERO LOSS RULE)
2. For each pair: Select `MAX(volume)` among green configs
3. Sum volumes across portfolio
4. If `Sum >= $33,333`: ✅ SUCCESS
5. If `Sum < $33,333`: 🔄 Recommend "ADD PAIRS"

**Usage:**
```bash
# Auto-detect latest sweep
.venv/bin/python scripts/analyze_vip_progress.py --auto

# Explicit path
.venv/bin/python scripts/analyze_vip_progress.py \
    --sweep-file sweep_results/vip_acquisition_20260205/sweep_results.json
```

**Output:**
```
📈 PORTFOLIO METRICS
────────────────────────────────────────────────────
Active Pairs:      5
Total Volume:      $28,450 / $33,333 target
Total Net PnL:     $127.35
Progress:          [████████████████░░░] 85.4%

💡 RECOMMENDATIONS
────────────────────────────────────────────────────
🔄 Volume gap: $4,883 (14.6% short)
➕ Add expansion pairs: SOLUSDT, XRPUSDT, ARBUSDT
```

**Files Generated:**
- `portfolio_summary.json` - Complete analysis + deployment configs

---

## 🚀 Quick Start Guide

### Step 1: Ingest Leader Data (Binance)
```bash
export COIN_API="e4857d4b-3ac5-488a-bdac-7006983d98c8"

for PAIR in ETHUSDT SUIUSDT DOGEUSDT AVAXUSDT LINKUSDT; do
    SYMBOL_ID="BINANCE_SPOT_${PAIR:0:-4}_USDT"
    .venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
        --date 2026-01-28 \
        --exchange BINANCE \
        --symbol $PAIR \
        --coinapi-symbol-id $SYMBOL_ID \
        --batch-size 5000
done
```

### Step 2: Run Green Sweep
```bash
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
    --profile balanced \
    --questdb
```

### Step 3: Analyze Results
```bash
.venv/bin/python scripts/analyze_vip_progress.py --auto
```

### Step 4: Deploy (if target met)
```bash
# Extract optimal configs
cat sweep_results/vip_acquisition_*/portfolio_summary.json | jq '.deployment_config'

# Update production runner with optimal parameters
# Deploy to VPS
```

### Step 5: Expand (if target not met)
```bash
# Add SOLUSDT and XRPUSDT
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,SOLUSDT,XRPUSDT \
    --profile balanced

# Re-analyze
.venv/bin/python scripts/analyze_vip_progress.py --auto
```

---

## 📊 Portfolio Configuration

### Initial 5-Pair Portfolio
| Pair | Order Qty | Max Position | Expected Volume/Day |
|------|-----------|--------------|---------------------|
| ETHUSDT | 0.005 ETH | 0.05 ETH | $3,000 - $8,000 |
| SUIUSDT | 80 SUI | 800 SUI | $5,000 - $12,000 |
| DOGEUSDT | 1500 DOGE | 15000 DOGE | $8,000 - $15,000 |
| AVAXUSDT | 2 AVAX | 20 AVAX | $3,000 - $6,000 |
| LINKUSDT | 2 LINK | 20 LINK | $3,000 - $6,000 |

### Expansion Candidates (If Volume Insufficient)
1. **SOLUSDT** - High volume, moderate spreads
2. **XRPUSDT** - Very high volume, tight spreads
3. **ARBUSDT** - Growing volume, good liquidity
4. **ADAUSDT** - Stable volume
5. **MATICUSDT** - Moderate volume

---

## 🔍 Key Performance Indicators

### Profitability Metrics (Per Pair)
- ✅ **Net PnL > $0** (mandatory)
- `profit_per_fill > $0.05` (healthy)
- `total_fees < 0.4 * gross_pnl` (efficient)

### Volume Metrics (Portfolio)
- 🎯 **Total Volume ≥ $33,333** (target)
- Individual pair contribution: $3k - $15k
- Portfolio diversity: No single pair > 50%

### Execution Metrics
- Fill count: 500 - 2,000/day (balanced)
- Avg spread: 25-35 bps (optimal)
- Guard threshold: 15-25 bps (safe)

---

## ⚠️ Critical Constraints

### Fee Structure (VIP0 + MNT Discount)
- Maker: 7.5 bps
- Taker: 7.5 bps
- **Round-trip: 15 bps**
- **Minimum profitable spread: >15 bps**

### ZERO LOSS RULE
If a pair cannot be profitable at spreads > 15 bps:
1. ❌ **DO NOT** tighten spreads below 15 bps
2. ❌ **DO NOT** disable guards/protections
3. ✅ **DO** widen spreads until green
4. ✅ **DO** add more pairs to portfolio

### Expansion Protocol
- Identify volume gap: `$33,333 - current_volume`
- Select expansion pairs from candidates
- Run sweep with expanded portfolio
- Validate ALL pairs remain green
- Deploy only if portfolio meets target

---

## 📈 Expected Results

### Conservative Scenario (Wide Spreads: 35-50 bps)
- Volume/pair: $3,000 - $6,000
- PnL/pair: $20 - $80
- **Total Volume (5 pairs): $15,000 - $30,000** ⚠️ Likely insufficient

### Balanced Scenario (Optimal Spreads: 25-35 bps)
- Volume/pair: $5,000 - $10,000
- PnL/pair: $30 - $150
- **Total Volume (5 pairs): $25,000 - $50,000** ✅ Likely sufficient

### Aggressive Scenario (Tight Spreads: 15-25 bps)
- Volume/pair: $8,000 - $15,000
- PnL/pair: $10 - $100 (higher variance)
- **Total Volume (5 pairs): $40,000 - $75,000** ✅ Exceeds target (but riskier)

**Recommended:** Balanced scenario with 5-7 pairs

---

## 🛠️ Troubleshooting

### No Green Configs Found
**Symptoms:**
```
🟢 Green configs: 0 / 1280 (0.0%)
```

**Causes:**
- Spreads too tight (< 15 bps)
- Fee assumptions wrong
- Bad data quality

**Fixes:**
1. Verify fee profile: `--fee-profile vip0 --mnt-discount`
2. Check data: `SELECT COUNT(*) FROM orderbook_deltas WHERE symbol='SUIUSDT' AND timestamp >= '2026-01-28'`
3. Widen spread range: Start at 20 bps minimum

### Volume Gap Persists
**Symptoms:**
```
Total Volume: $28,450 / $33,333 target
Status: GAP REMAINING ($4,883 short)
```

**Solution:**
Add expansion pairs (DO NOT loosen parameters):
```bash
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,SOLUSDT,XRPUSDT
```

### Backtest Timeout
**Symptoms:**
```
Error: Timeout (>10 minutes)
```

**Fixes:**
1. Use `--quick` mode for testing
2. Reduce parameter grid
3. Check QuestDB performance

---

## 📚 Documentation

- **[VIP1_ACQUISITION_WORKFLOW.md](VIP1_ACQUISITION_WORKFLOW.md)** - Complete step-by-step workflow
- **[AGENTS.md](AGENTS.md)** - System architecture and operational guide
- **Strategy:** `strategy/lead_lag_bybit_binance_mm_v004_primer.py`
- **Backtest Engine:** `examples/backtest/llmmv4_primer_backtest.py`

---

## 🎓 Key Concepts

### Lead-Lag Market Making (LLMMv4)
- **Leader:** Binance SPOT (price discovery)
- **Follower:** Bybit SPOT (execution)
- **Signal:** Leader orderbook changes trigger follower quote updates
- **Latency:** Event-driven, sub-second refresh

### Green Sweep Methodology
1. **Exhaustive search** across parameter space
2. **Filter by profitability** (ZERO LOSS RULE)
3. **Optimize for volume** among green configs
4. **Portfolio assembly** via stacking safe pairs

### Risk Management
- **Inventory limits:** Max position per pair
- **Guard thresholds:** Reject toxic flow (rapid price moves)
- **Min profit:** Ensure each fill is profitable
- **OFI filtering:** Order Flow Imbalance detection

---

## 🏁 Success Criteria

✅ **VIP1 Acquisition Complete When:**
1. Portfolio generates ≥$33,333 daily volume (backtested)
2. All pairs show Net PnL > $0 (ZERO LOSS RULE)
3. Spreads > 15 bps (fee coverage)
4. Configuration validated on 5+ historical days
5. Production deployment successful
6. Live monitoring confirms backtest projections

---

**Version:** 1.0  
**Date:** February 5, 2026  
**System:** Nebakineza Trading - NautilusTrader  
**Status:** READY FOR PRODUCTION
