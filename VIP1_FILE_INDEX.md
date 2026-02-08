# VIP1 Acquisition Strategy - File Index

## 📂 Complete File Listing

### 🎯 Core Scripts (3 files)
```
scripts/
├── coinapi_flatfiles_ingest_orderbook.py  ✅ EXISTING - Data ingestion
├── run_vip_acquisition_sweep.py           🆕 NEW - Green sweep engine
└── analyze_vip_progress.py                🆕 NEW - Portfolio analyzer
```

### 📚 Documentation (4 files)
```
.
├── VIP1_ACQUISITION_README.md        🆕 Complete reference guide
├── VIP1_ACQUISITION_WORKFLOW.md      🆕 Step-by-step workflow
├── VIP1_DELIVERY_SUMMARY.md          🆕 Delivery summary
└── VIP1_FILE_INDEX.md                🆕 This file
```

### 🛠️ Utilities (2 files)
```
scripts/
├── vip1_quick_reference.sh           🆕 Quick reference card
└── vip1_launcher.sh                  🆕 Interactive launcher
```

---

## 🚀 Quick Start

### Option 1: Interactive Launcher (Recommended)
```bash
./scripts/vip1_launcher.sh
```

### Option 2: Manual Commands
```bash
# 1. View quick reference
./scripts/vip1_quick_reference.sh

# 2. Ingest data
export COIN_API="your-key"
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 --exchange BINANCE --symbol SUIUSDT \
    --coinapi-symbol-id BINANCE_SPOT_SUI_USDT

# 3. Run sweep
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 --pairs SUIUSDT --quick

# 4. Analyze
.venv/bin/python scripts/analyze_vip_progress.py --auto
```

---

## 📖 Documentation Guide

### New to the system?
**Start here:** [VIP1_ACQUISITION_README.md](VIP1_ACQUISITION_README.md)
- Mission statement
- Tool descriptions
- Quick start guide
- Expected performance
- Success criteria

### Ready to execute?
**Follow this:** [VIP1_ACQUISITION_WORKFLOW.md](VIP1_ACQUISITION_WORKFLOW.md)
- Step-by-step instructions
- Validation checklists
- Troubleshooting guide
- Command examples

### Need a quick reminder?
**Use this:** [scripts/vip1_quick_reference.sh](scripts/vip1_quick_reference.sh)
```bash
./scripts/vip1_quick_reference.sh
```

### Want to see what was delivered?
**Read this:** [VIP1_DELIVERY_SUMMARY.md](VIP1_DELIVERY_SUMMARY.md)
- Complete deliverables list
- Architecture overview
- Validation results
- Next steps

---

## 🔧 Script Details

### 1. coinapi_flatfiles_ingest_orderbook.py
**Purpose:** Download CoinAPI Flat Files and ingest into QuestDB  
**Status:** ✅ Existing, production-ready  
**Key Features:**
- S3 API integration
- GZIP decompression
- QuestDB ILP ingestion
- Batch processing

**Usage:**
```bash
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 \
    --exchange BINANCE \
    --symbol SUIUSDT \
    --coinapi-symbol-id BINANCE_SPOT_SUI_USDT \
    --batch-size 5000
```

**Help:**
```bash
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py --help
```

---

### 2. run_vip_acquisition_sweep.py
**Purpose:** Multi-dimensional parameter sweep for 5-pair portfolio  
**Status:** 🆕 New, production-ready  
**Key Features:**
- 5-parameter sweep (1,536 combos/pair)
- Profitability filtering (ZERO LOSS RULE)
- Quick mode for testing
- Comprehensive result capture

**Usage:**
```bash
# Full sweep
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT

# Quick test
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 --pairs SUIUSDT --quick
```

**Help:**
```bash
.venv/bin/python scripts/run_vip_acquisition_sweep.py --help
```

**Output:**
```
sweep_results/vip_acquisition_TIMESTAMP/
├── sweep_results.json
└── PAIR_CONFIG_NAME/
    ├── summary.json
    ├── wap_summary.json
    └── fills_report.csv
```

---

### 3. analyze_vip_progress.py
**Purpose:** Select optimal configs and determine if VIP1 target is met  
**Status:** 🆕 New, production-ready  
**Key Features:**
- Green config filtering
- Best config selection per pair
- Volume aggregation
- Gap analysis
- Expansion recommendations

**Usage:**
```bash
# Auto-detect latest results
.venv/bin/python scripts/analyze_vip_progress.py --auto

# Explicit path
.venv/bin/python scripts/analyze_vip_progress.py \
    --sweep-file sweep_results/vip_acquisition_20260205/sweep_results.json
```

**Help:**
```bash
.venv/bin/python scripts/analyze_vip_progress.py --help
```

**Output:**
```
Portfolio metrics summary (console)
portfolio_summary.json (deployment configs)
```

---

### 4. vip1_quick_reference.sh
**Purpose:** Display quick reference card in terminal  
**Status:** 🆕 New, utility script  
**Key Features:**
- One-line commands
- Validation queries
- Critical constraints
- Expected performance

**Usage:**
```bash
./scripts/vip1_quick_reference.sh
```

---

### 5. vip1_launcher.sh
**Purpose:** Interactive menu for all VIP1 acquisition tasks  
**Status:** 🆕 New, utility script  
**Key Features:**
- Guided workflow
- Batch operations
- Documentation access
- User-friendly prompts

**Usage:**
```bash
./scripts/vip1_launcher.sh
```

**Menu Options:**
1. Quick Reference Card
2. Ingest Single Pair
3. Ingest All 5 Pairs
4. Quick Test Sweep
5. Full 5-Pair Sweep
6. Analyze Results
7. View Documentation
8. Exit

---

## 📊 File Dependencies

```
VIP1_ACQUISITION_README.md
    └── Entry point (read this first)

VIP1_ACQUISITION_WORKFLOW.md
    └── Detailed steps (follow this to execute)

scripts/vip1_launcher.sh
    ├── calls: coinapi_flatfiles_ingest_orderbook.py
    ├── calls: run_vip_acquisition_sweep.py
    ├── calls: analyze_vip_progress.py
    └── displays: vip1_quick_reference.sh

run_vip_acquisition_sweep.py
    └── calls: examples/backtest/llmmv4_primer_backtest.py
        └── uses: strategy/lead_lag_bybit_binance_mm_v004_primer.py

analyze_vip_progress.py
    └── reads: sweep_results/vip_acquisition_*/sweep_results.json
```

---

## 🎯 Execution Paths

### Path 1: Complete Workflow (Production)
```
1. Read VIP1_ACQUISITION_README.md
2. Follow VIP1_ACQUISITION_WORKFLOW.md
3. Ingest data (coinapi_flatfiles_ingest_orderbook.py)
4. Run sweep (run_vip_acquisition_sweep.py)
5. Analyze (analyze_vip_progress.py)
6. Deploy optimal configs
```

### Path 2: Quick Test (Validation)
```
1. Run vip1_launcher.sh
2. Select option 4 (Quick Test Sweep)
3. Select option 6 (Analyze Results)
4. Review console output
```

### Path 3: Interactive Guided (Beginner)
```
1. Run vip1_launcher.sh
2. Follow menu options 1 → 2 → 4 → 6
3. Review documentation (option 7)
```

---

## ✅ Validation Checklist

### Before First Run
- [ ] QuestDB is running (port 9000, 9009)
- [ ] COIN_API environment variable is set
- [ ] Python virtual environment is activated
- [ ] All scripts have execute permissions

### After Data Ingestion
- [ ] QuestDB table has data: `SELECT COUNT(*) FROM orderbook_deltas`
- [ ] Data covers full date range
- [ ] Both Binance and Bybit data present

### After Sweep
- [ ] sweep_results/ directory created
- [ ] sweep_results.json exists
- [ ] Individual backtest folders present

### After Analysis
- [ ] portfolio_summary.json exists
- [ ] Console shows portfolio metrics
- [ ] Recommendations are clear

---

## 🔍 Finding Files

### All VIP1-related files:
```bash
find . -name "*vip1*" -o -name "*VIP1*"
```

### All sweep results:
```bash
ls -lh sweep_results/vip_acquisition_*
```

### Latest portfolio summary:
```bash
find sweep_results/vip_acquisition_* -name "portfolio_summary.json" | sort -r | head -1
```

---

## 📞 Support

### Issues with scripts?
Check: [VIP1_ACQUISITION_WORKFLOW.md](VIP1_ACQUISITION_WORKFLOW.md) → Troubleshooting section

### Need architecture context?
Check: [AGENTS.md](AGENTS.md) → System architecture

### Want to understand the strategy?
Check: [VIP1_ACQUISITION_README.md](VIP1_ACQUISITION_README.md) → Key Concepts

---

**Last Updated:** February 5, 2026  
**File Count:** 9 files (3 scripts, 4 docs, 2 utilities)  
**Status:** ✅ COMPLETE - READY FOR PRODUCTION
