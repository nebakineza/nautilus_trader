#!/bin/bash
# VIP1 Golden Configs - Complete Execution Plan
# Date: 2026-02-05
# Target: $33,333 daily volume @ Net PnL > $0 for ALL pairs

set -e

VENV_PYTHON="/home/seb/nebakineza/nautilus_trader/.venv/bin/python"
COIN_API="e4857d4b-3ac5-488a-bdac-7006983d98c8"

# Date range for backtesting (3 days for validation)
DATES=("2026-01-28" "2026-01-29" "2026-01-30")

# Pairs to optimize
PAIRS=("ETHUSDT" "SUIUSDT" "DOGEUSDT" "AVAXUSDT" "LINKUSDT")

echo "╔════════════════════════════════════════════════════════════════════════════════╗"
echo "║           VIP1 GOLDEN CONFIGS - COMPLETE EXECUTION PLAN                       ║"
echo "╚════════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "📋 PLAN OVERVIEW"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Pairs:        ${PAIRS[@]}"
echo "Dates:        ${DATES[@]}"
echo "Total Runs:   $((${#PAIRS[@]} * ${#DATES[@]})) days of data + sweeps"
echo "Target:       $33,333 daily volume with Net PnL > $0 per pair"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# PHASE 1: DATA INGESTION
echo "📥 PHASE 1: DATA INGESTION (Binance Leader Data)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for PAIR in "${PAIRS[@]}"; do
    BASE="${PAIR%USDT}"
    COINAPI_ID="BINANCE_SPOT_${BASE}_USDT"
    
    echo ""
    echo "📊 Pair: $PAIR"
    
    for DATE in "${DATES[@]}"; do
        echo "  ├─ Date: $DATE"
        echo "  │  Ingesting Binance orderbook data..."
        
        export COIN_API="$COIN_API"
        $VENV_PYTHON scripts/coinapi_flatfiles_ingest_orderbook.py \
            --date "$DATE" \
            --exchange BINANCE \
            --symbol "$PAIR" \
            --coinapi-symbol-id "$COINAPI_ID" \
            --batch-size 5000 2>&1 | tail -1
    done
    echo "  ✅ $PAIR ingestion complete"
done

echo ""
echo "✅ PHASE 1 COMPLETE: All Binance data ingested"
echo ""

# PHASE 2: PARAMETER SWEEPS (Per Pair)
echo "🔬 PHASE 2: PARAMETER SWEEPS (Individual Pairs)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for PAIR in "${PAIRS[@]}"; do
    echo ""
    echo "🎯 SWEEPING: $PAIR"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Run sweep for primary date (Jan 28)
    $VENV_PYTHON scripts/run_vip_acquisition_sweep.py \
        --date "2026-01-28" \
        --pairs "$PAIR" \
        --profile balanced \
        --questdb \
        --out-dir "sweep_results/golden_configs_${PAIR}_20260128"
    
    echo "✅ $PAIR sweep complete"
done

echo ""
echo "✅ PHASE 2 COMPLETE: All parameter sweeps finished"
echo ""

# PHASE 3: MULTI-DAY VALIDATION
echo "🧪 PHASE 3: MULTI-DAY VALIDATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Testing optimal configs across Jan 29-30 for consistency..."
echo "(This validates configs work across multiple market conditions)"
echo ""
echo "⚠️  This phase will be run manually after initial analysis"
echo ""

# PHASE 4: PORTFOLIO ANALYSIS
echo "📊 PHASE 4: PORTFOLIO ANALYSIS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "Analyzing individual pair results..."

for PAIR in "${PAIRS[@]}"; do
    echo ""
    echo "📈 Analyzing: $PAIR"
    
    $VENV_PYTHON scripts/analyze_vip_progress.py \
        --sweep-file "sweep_results/golden_configs_${PAIR}_20260128/sweep_results.json" \
        --out-file "sweep_results/golden_configs_${PAIR}_20260128/optimal_config.json"
done

echo ""
echo "✅ PHASE 4 COMPLETE: Individual analysis done"
echo ""

# PHASE 5: PORTFOLIO ASSEMBLY
echo "🏗️  PHASE 5: PORTFOLIO ASSEMBLY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Combining all optimal configs into master portfolio..."
echo ""

cat > sweep_results/GOLDEN_PORTFOLIO_SUMMARY.md << 'SUMMARY'
# Golden Portfolio Configs - VIP1 Acquisition

## Target
- Daily Volume: $33,333 USD
- Net PnL: > $0 for EVERY pair (ZERO LOSS RULE)

## Optimal Configurations (Per Pair)

### ETHUSDT
- Optimal config: (see sweep_results/golden_configs_ETHUSDT_20260128/optimal_config.json)

### SUIUSDT
- Optimal config: (see sweep_results/golden_configs_SUIUSDT_20260128/optimal_config.json)

### DOGEUSDT
- Optimal config: (see sweep_results/golden_configs_DOGEUSDT_20260128/optimal_config.json)

### AVAXUSDT
- Optimal config: (see sweep_results/golden_configs_AVAXUSDT_20260128/optimal_config.json)

### LINKUSDT
- Optimal config: (see sweep_results/golden_configs_LINKUSDT_20260128/optimal_config.json)

## Portfolio Metrics
- Total Volume: (TBD after analysis)
- Total Net PnL: (TBD after analysis)
- Meets Target: (TBD after analysis)

SUMMARY

echo "✅ Golden portfolio summary created: sweep_results/GOLDEN_PORTFOLIO_SUMMARY.md"
echo ""

echo "╔════════════════════════════════════════════════════════════════════════════════╗"
echo "║                      EXECUTION PLAN COMPLETE ✅                                ║"
echo "╚════════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "📋 NEXT STEPS:"
echo "  1. Review individual pair analyses"
echo "  2. Validate portfolio meets $33,333 target"
echo "  3. If gap exists, add expansion pairs (SOLUSDT, XRPUSDT)"
echo "  4. Run multi-day validation (Jan 29-30)"
echo "  5. Generate deployment configs"
echo ""
