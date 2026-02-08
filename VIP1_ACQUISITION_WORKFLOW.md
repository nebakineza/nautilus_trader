# VIP1 Acquisition Strategy - Complete Workflow

## 🎯 Objective
Generate **$33,333 USD daily volume** across a multi-pair portfolio while maintaining **positive PnL** for each pair (ZERO LOSS RULE).

**Fee Context:**
- VIP0 + MNT Discount: 7.5 bps maker / 7.5 bps taker
- Round-trip cost: 15 bps
- **Minimum profitable spread: >15 bps**

---

## 📋 Workflow Steps

### **STEP 1: Data Ingestion (Leader: Binance)**

Use the existing CoinAPI Flat Files ingestion script to load Binance orderbook data into QuestDB.

```bash
# Set CoinAPI key
export COIN_API="your-api-key-here"

# Ingest Binance data for each pair (one date at a time)
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 \
    --exchange BINANCE \
    --symbol ETHUSDT \
    --coinapi-symbol-id BINANCE_SPOT_ETH_USDT \
    --batch-size 5000

.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 \
    --exchange BINANCE \
    --symbol SUIUSDT \
    --coinapi-symbol-id BINANCE_SPOT_SUI_USDT \
    --batch-size 5000

# Repeat for: DOGEUSDT, AVAXUSDT, LINKUSDT
```

**Expected Output:**
```
Ingested 1,234,567 rows into orderbook_deltas
```

**Verify in QuestDB:**
```sql
SELECT venue, symbol, COUNT(*) as rows
FROM orderbook_deltas
WHERE timestamp >= '2026-01-28T00:00:00.000000Z'
  AND timestamp < '2026-01-29T00:00:00.000000Z'
GROUP BY venue, symbol
ORDER BY symbol;
```

---

### **STEP 2: Run Green Sweep (Parameter Optimization)**

Execute comprehensive parameter sweep across 5-pair portfolio.

```bash
# Full sweep (RECOMMENDED for production)
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
    --profile balanced \
    --questdb

# Quick sweep (for testing/validation)
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs SUIUSDT \
    --quick
```

**Parameter Grid:**
| Parameter | Full Sweep | Quick Sweep |
|-----------|------------|-------------|
| `spread_bps` | [15, 20, 25, 30, 35, 40, 45, 50] | [25, 35, 50] |
| `guard_threshold_bps` | [10, 15, 20, 25] | [15, 25] |
| `min_profit_bps` | [1, 2, 3, 5] | [1, 3] |
| `ofi_max_bps` | [0, 3, 5, 8] | [0, 5] |
| `refresh_interval_ms` | [3000, 5000, 8000] | [5000] |

**Expected Runtime:**
- Quick sweep (1 pair): ~5-10 minutes
- Full sweep (5 pairs): ~4-6 hours

**Output:**
```
sweep_results/vip_acquisition_20260205_120000/
├── sweep_results.json
├── ETHUSDT_25bps_g15_mp1_ofi0_r5000/
│   ├── summary.json
│   ├── wap_summary.json
│   ├── fills_report.csv
│   └── account_report.csv
├── SUIUSDT_25bps_g15_mp1_ofi0_r5000/
└── ...
```

---

### **STEP 3: Analyze Results (Portfolio Optimization)**

Run analyzer to determine optimal configuration.

```bash
# Auto-detect latest sweep results
.venv/bin/python scripts/analyze_vip_progress.py --auto

# Or specify explicit path
.venv/bin/python scripts/analyze_vip_progress.py \
    --sweep-file sweep_results/vip_acquisition_20260205_120000/sweep_results.json
```

**Expected Output:**

```
═══════════════════════════════════════════════════════════════════════════════════════════════
🎯 VIP1 ACQUISITION PROGRESS SUMMARY
═══════════════════════════════════════════════════════════════════════════════════════════════

📈 PORTFOLIO METRICS
────────────────────────────────────────────────────────────────────────────────────────────────
Active Pairs:      5
Total Volume:      $28,450.00 / $33,333.00 target
Total Net PnL:     $127.35
Total Fills:       1,247
Total Fees Paid:   $42.68
Avg P&L/Fill:      $0.1021

Progress:          [█████████████████████████████████████████░░░░░░░░░] 85.4%

⚠️  STATUS: GAP REMAINING ($4,883 short)

────────────────────────────────────────────────────────────────────────────────────────────────
💡 RECOMMENDATIONS
────────────────────────────────────────────────────────────────────────────────────────────────
🔄 Volume gap: $4,883 (14.6% short of target)
⚠️  DO NOT loosen parameters - maintain ZERO LOSS RULE
➕ Add high-activity pairs to close the gap:
   • SOLUSDT
   • XRPUSDT
   • ARBUSDT
   • ADAUSDT
   • MATICUSDT

📋 Re-run sweep with expanded pair list to meet target
═══════════════════════════════════════════════════════════════════════════════════════════════
```

**Outputs:**
- `portfolio_summary.json` - Complete analysis with deployment configs
- Console summary with recommendations

---

### **STEP 4: Expansion (If Needed)**

If portfolio doesn't meet $33,333 target:

```bash
# Add expansion pairs (e.g., SOLUSDT, XRPUSDT)
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 \
    --pairs ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,SOLUSDT,XRPUSDT \
    --profile balanced \
    --questdb

# Re-analyze
.venv/bin/python scripts/analyze_vip_progress.py --auto
```

**Expansion Candidates (Prioritized):**
1. **SOLUSDT** - High volume, moderate spreads
2. **XRPUSDT** - Extremely high volume, tight spreads
3. **ARBUSDT** - Growing volume, good liquidity
4. **ADAUSDT** - Stable volume
5. **MATICUSDT** - Moderate volume

---

### **STEP 5: Deploy to Production**

Once portfolio meets target with all pairs GREEN:

1. **Extract Deployment Config:**
   ```bash
   cat sweep_results/vip_acquisition_*/portfolio_summary.json | jq '.deployment_config'
   ```

2. **Update Production Runner:**
   Copy optimal parameters to `run_vip_production.py` or VPS runner.

3. **Deploy:**
   ```bash
   scp strategy/lead_lag_bybit_binance_mm_v004_primer.py sentinel-vps:/home/ubuntu/trading/strategy_pkg/
   ssh sentinel-vps "sudo systemctl restart vip_sui_heavy.service"
   ```

4. **Monitor:**
   ```bash
   ssh sentinel-vps "journalctl -u vip_sui_heavy.service -f"
   ```

---

## 🔍 Validation Checklist

Before deploying to production:

- [ ] All pairs show `Net PnL > $0` in backtest
- [ ] Total portfolio volume ≥ $33,333
- [ ] Spread > 15 bps for all pairs (fee coverage)
- [ ] Fill counts are reasonable (not too sparse)
- [ ] QuestDB data quality validated (no gaps)
- [ ] VPS has correct API keys configured
- [ ] Systemd service configured correctly

---

## 📊 Key Metrics to Monitor

**Backtest Metrics:**
- `net_pnl` - Total PnL after fees
- `volume` - Total USD volume traded
- `fill_count` - Number of fills executed
- `profit_per_fill` - Average profit per fill
- `fees_paid` - Total fees paid

**Production Metrics:**
- Daily volume (target: $33,333)
- Net PnL (must stay positive)
- Fill rate (aggressive vs conservative)
- Inventory imbalances
- Fee tier progression

---

## ⚠️ Common Issues & Fixes

### Issue: No Green Configs Found
**Symptom:** Analyzer reports 0% green configs  
**Causes:**
- Spreads too tight (< 15 bps)
- Fee assumptions incorrect
- Bad data quality (gaps/outliers)

**Fix:**
- Verify fee profile: `--fee-profile vip0 --mnt-discount`
- Check data quality in QuestDB
- Widen spread range: `[20, 25, 30, 35, 40, 50]`

### Issue: Volume Too Low
**Symptom:** Portfolio volume < $33,333  
**Solution:** **ADD PAIRS** (don't loosen parameters!)

### Issue: Backtest Times Out
**Symptom:** `Error: Timeout (>10 minutes)`  
**Fix:**
- Use `--quick` mode for testing
- Reduce date range
- Check QuestDB query performance

---

## 📈 Expected Performance Ranges

**Conservative (Wide Spreads):**
- Spread: 35-50 bps
- Volume/pair: $3,000 - $6,000/day
- PnL/pair: $20 - $80/day
- Fill count: 50-200/day

**Balanced (Optimal):**
- Spread: 25-35 bps
- Volume/pair: $5,000 - $10,000/day
- PnL/pair: $30 - $150/day
- Fill count: 150-400/day

**Aggressive (Tight Spreads):**
- Spread: 15-25 bps
- Volume/pair: $8,000 - $15,000/day
- PnL/pair: $10 - $100/day (higher risk)
- Fill count: 300-800/day

---

## 🚀 Next Steps

1. **Run initial sweep** on historical data (Jan 28-31, 2026)
2. **Analyze results** to determine if 5 pairs sufficient
3. **Add pairs** if needed to close volume gap
4. **Deploy optimal config** to VPS
5. **Monitor** for 7 days to confirm VIP1 qualification

---

## 📞 Support

For issues or questions, refer to:
- `AGENTS.md` - System architecture and operational guide
- `copilot-instructions.md` - AI assistant context
- QuestDB console: `http://127.0.0.1:9000` (local)

---

**Last Updated:** February 5, 2026  
**Author:** Nebakineza Trading System  
**Version:** 1.0
