#!/bin/bash
# VIP1 Acquisition Strategy - Quick Reference Card

cat << 'EOF'
╔════════════════════════════════════════════════════════════════════════════════╗
║                   VIP1 ACQUISITION STRATEGY - QUICK REFERENCE                  ║
╚════════════════════════════════════════════════════════════════════════════════╝

TARGET: $33,333 daily volume @ Net PnL > $0 (ZERO LOSS RULE)

┌────────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: INGEST BINANCE DATA (Leader)                                          │
└────────────────────────────────────────────────────────────────────────────────┘

export COIN_API="your-api-key"

.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 \
    --exchange BINANCE \
    --symbol SUIUSDT \
    --coinapi-symbol-id BINANCE_SPOT_SUI_USDT \
    --batch-size 5000

# Repeat for: ETHUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT

┌────────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: RUN GREEN SWEEP (Parameter Optimization)                              │
└────────────────────────────────────────────────────────────────────────────────┘

# Full sweep (5 pairs, ~4-6 hours)
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
    --profile balanced

# Quick test (1 pair, ~5 mins)
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs SUIUSDT \
    --quick

┌────────────────────────────────────────────────────────────────────────────────┐
│ STEP 3: ANALYZE RESULTS                                                       │
└────────────────────────────────────────────────────────────────────────────────┘

.venv/bin/python scripts/analyze_vip_progress.py --auto

┌────────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: EXPAND IF NEEDED                                                      │
└────────────────────────────────────────────────────────────────────────────────┘

# Add SOLUSDT, XRPUSDT to close volume gap
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,SOLUSDT,XRPUSDT

.venv/bin/python scripts/analyze_vip_progress.py --auto

┌────────────────────────────────────────────────────────────────────────────────┐
│ KEY FILES                                                                      │
└────────────────────────────────────────────────────────────────────────────────┘

Data Ingestion:  scripts/coinapi_flatfiles_ingest_orderbook.py
Green Sweep:     scripts/run_vip_acquisition_sweep.py
Analyzer:        scripts/analyze_vip_progress.py
Workflow:        VIP1_ACQUISITION_WORKFLOW.md
README:          VIP1_ACQUISITION_README.md

┌────────────────────────────────────────────────────────────────────────────────┐
│ VALIDATION                                                                     │
└────────────────────────────────────────────────────────────────────────────────┘

# Check QuestDB data
SELECT venue, symbol, COUNT(*) FROM orderbook_deltas 
WHERE timestamp >= '2026-01-28' GROUP BY venue, symbol;

# Verify sweep outputs
ls -lh sweep_results/vip_acquisition_*/

# View portfolio summary
cat sweep_results/vip_acquisition_*/portfolio_summary.json | jq

┌────────────────────────────────────────────────────────────────────────────────┐
│ CRITICAL CONSTRAINTS                                                           │
└────────────────────────────────────────────────────────────────────────────────┘

✅ All pairs MUST show Net PnL > $0 (ZERO LOSS RULE)
✅ Spread MUST be > 15 bps (fee coverage: VIP0+MNT = 15bps roundtrip)
❌ NEVER tighten spreads below 15 bps to gain volume
❌ NEVER disable guards/protections for volume
✅ ADD PAIRS instead of loosening parameters

┌────────────────────────────────────────────────────────────────────────────────┐
│ EXPECTED PERFORMANCE (Balanced Scenario)                                      │
└────────────────────────────────────────────────────────────────────────────────┘

Per Pair:  $5,000 - $10,000 volume/day @ $30-$150 PnL/day
Portfolio: $25,000 - $50,000 total (5 pairs)
Target:    $33,333/day ✅

If insufficient: Add SOLUSDT, XRPUSDT, ARBUSDT

╔════════════════════════════════════════════════════════════════════════════════╗
║                            READY FOR PRODUCTION ✅                              ║
╚════════════════════════════════════════════════════════════════════════════════╝

EOF
