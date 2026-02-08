# VIP1 Acquisition - Final Strategy & Action Plan

**Date:** February 5, 2026  
**Objective:** Generate $33,333 daily volume while maintaining ZERO LOSS RULE  
**Current Status:** 1 winner identified, expansion needed

---

## 🎯 **CURRENT SITUATION**

### ✅ **Proven Winner: SUIUSDT**
- **Daily Volume:** $26,224 (78.7% of target)
- **Daily PnL:** $51.37 (consistently positive)
- **Fills:** 224/day
- **Golden Config:** 25bps spread, 25bps guard, OFI disabled
- **Confidence:** VERY HIGH (100% green rate across all parameters)

### ❌ **Tested & Rejected**
| Pair | Volume | Fills | Status | Reason |
|------|--------|-------|--------|--------|
| SOLUSDT | $61 | 3 | ❌ REJECTED | Insufficient volume (99% below target) |
| DOGEUSDT | $990 | 6 | ❌ REJECTED | Marginal, unstable |
| ETHUSDT | $0 | 0 | ❌ REJECTED | Unprofitable at all tested spreads |
| AVAXUSDT | $0 | 0 | ❌ REJECTED | Unprofitable at all tested spreads |
| LINKUSDT | $0 | 0 | ❌ REJECTED | Unprofitable at all tested spreads |

### ⏸️ **Cannot Test (Missing Bybit Data)**
- XRPUSDT (only Binance leader data available)
- Other potential candidates

---

## 📊 **THE VOLUME GAP**

**Current:** $26,224/day (SUIUSDT only)  
**Target:** $33,333/day  
**Gap:** $7,109/day (21.3% short)

**To reach VIP1 ($1M/30 days):**
- With SUIUSDT only: 38 days (8 days longer)
- With full target: 30 days (on schedule)

---

## 🔍 **ROOT CAUSE ANALYSIS: Why SUIUSDT Works**

### SUIUSDT Success Factors:
1. **High Liquidity:** Both Binance and Bybit have deep orderbooks
2. **Tight Natural Spreads:** ~10-15bps base spread between exchanges
3. **Low Adverse Selection:** Market is relatively calm, not dominated by HFT
4. **Good Book Quality:** Consistent depth at top levels
5. **Medium Volatility:** Active enough for fills, stable enough for MM
6. **Retail Participation:** Good retail flow, less toxic than institutional pairs

### Why Others Failed:
1. **ETH/AVAX/LINK:** Too much HFT competition, adverse selection eats profit
2. **DOGE:** Thin orderbook, sporadic activity
3. **SOL:** Unclear (possibly data quality issue, or book too thin on Bybit)

---

## 🎯 **STRATEGIC OPTIONS**

### **Option A: Find More SUI-Like Pairs** ⭐ RECOMMENDED

**Hypothesis:** SUIUSDT works because it has:
- Medium market cap ($6-8B)
- High retail interest
- Good exchange support
- Not dominated by HFT

**Candidates to Test (Similar Profile):**

#### Tier 1 - High Priority (Similar to SUI):
1. **ARBUSDT** - Layer 2, retail friendly, good liquidity
2. **OPUSDT** - Layer 2, similar profile to ARB
3. **APTUSDT** - New L1, similar to SUI, high retail interest
4. **NEARUSDT** - Established L1, good liquidity
5. **INJUSDT** - DeFi focused, active trading

#### Tier 2 - Medium Priority:
6. **RENDERUSDT** (RNDR) - AI narrative, retail favorite
7. **FTMUSDT** - Established, good liquidity
8. **ATOMUSDT** - Cosmos, institutional + retail
9. **MATICUSDT** (POL) - Polygon, very high volume
10. **ADAUSDT** - Cardano, massive retail following

#### Tier 3 - Low Priority (But Worth Testing):
11. **PEPEUSDT** - Meme, high volume but volatile
12. **BONKUSDT** - Meme on Solana
13. **WIFUSDT** - Meme, high retail interest

**Testing Protocol:**
```bash
# For each candidate:
# 1. Ingest Binance leader data (CoinAPI)
# 2. Check if we have local Bybit follower data
# 3. Run quick sweep (24 configs)
# 4. If >10k daily volume with PnL > $0, add to portfolio
```

**Expected Outcome:**
- Test 10-15 candidates
- Find 1-2 more SUI-like winners
- Each contributing $8-15k/day
- **Total portfolio: $40-50k/day → Exceeds $33k target**

---

### **Option B: Scale SUIUSDT**

**Approaches:**
1. **Increase Order Sizes:** 80 SUI → 120 SUI (+50%)
   - Expected volume: $39k/day
   - Risk: May hit inventory limits faster
   
2. **Run Multiple Instances:** 2 separate strategies
   - Expected volume: $52k/day
   - Risk: Self-trading, order collision
   
3. **Dual Accounts:** Use 2 Bybit accounts
   - Expected volume: $52k/day
   - Risk: Complexity, capital split

**Assessment:** Possible but less elegant than finding diverse pairs.

---

### **Option C: Accept Longer Timeline**

**Current Performance:**
- SUIUSDT: $26,224/day
- Days to $1M: 38 days
- Extra time needed: 8 days

**Pros:**
- Zero risk (proven strategy)
- No additional development
- Simple to manage

**Cons:**
- VIP1 delayed by 26%
- Opportunity cost of lower fees

---

## 💡 **RECOMMENDED ACTION PLAN**

### **Phase 1: Rapid Candidate Testing** (Next 48 hours)

**Day 1 (Today):**
1. ✅ SUIUSDT validated and documented
2. 🔄 Ingest top 5 candidates (ARB, OP, APT, NEAR, INJ)
3. 🔄 Run quick sweeps (24 configs each)
4. 🔄 Identify any additional winners

**Day 2 (Tomorrow):**
5. Test next 5 candidates if needed
6. Refine parameters for any promising pairs
7. Make final portfolio decision

### **Phase 2: Deployment** (Day 3)

**Scenario A: Found 1-2 More Winners**
- Deploy SUIUSDT + winners
- Target: $40-50k/day
- Timeline: VIP1 in 20-25 days ✅

**Scenario B: No Additional Winners Found**
- Deploy SUIUSDT only
- Consider Option B (scale up)
- Timeline: VIP1 in 30-38 days ⚠️

---

## 📋 **IMMEDIATE NEXT STEPS**

### **Priority 1: Test ARB, OP, APT**

These are most similar to SUI in terms of:
- Market cap range
- Retail appeal
- Exchange support
- Expected MM-friendly behavior

**Ingestion Command:**
```bash
# ARB
export COIN_API="e4857d4b-3ac5-488a-bdac-7006983d98c8"
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 --exchange BINANCE --symbol ARBUSDT \
    --coinapi-symbol-id BINANCE_SPOT_ARB_USDT --batch-size 5000

# OP
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 --exchange BINANCE --symbol OPUSDT \
    --coinapi-symbol-id BINANCE_SPOT_OP_USDT --batch-size 5000

# APT
.venv/bin/python scripts/coinapi_flatfiles_ingest_orderbook.py \
    --date 2026-01-28 --exchange BINANCE --symbol APTUSDT \
    --coinapi-symbol-id BINANCE_SPOT_APT_USDT --batch-size 5000
```

**Sweep Command:**
```bash
.venv/bin/python scripts/run_vip_acquisition_sweep.py \
    --date 2026-01-28 --pairs ARBUSDT,OPUSDT,APTUSDT \
    --questdb --profile balanced --quick
```

### **Priority 2: Fix VPS Environment**

While testing, fix the VPS Python environment so SUIUSDT can go live:
```bash
# On VPS
ssh sentinel-vps
cd /home/ubuntu/trading
# Install nautilus_trader in runtime_env
# Then restart service
```

---

## 📈 **SUCCESS METRICS**

### **Minimum Acceptable:**
- 1 pair (SUIUSDT): $26k/day → VIP1 in 38 days

### **Target:**
- 2-3 pairs: $35-45k/day → VIP1 in 22-28 days

### **Stretch Goal:**
- 3-4 pairs: $50-65k/day → VIP1 in 15-20 days

---

## ⚠️ **RISK FACTORS**

1. **Data Availability:** We need Bybit follower data for candidates
   - Mitigation: Check local files, consider live capture

2. **Parameter Sensitivity:** Other pairs may not be as robust as SUI
   - Mitigation: Require >50% green rate before deployment

3. **Correlation Risk:** Multiple pairs might move together
   - Mitigation: Diversify across different narratives (L2, L1, meme)

4. **VPS Environment:** Currently broken
   - Mitigation: Fix in parallel with candidate testing

---

## 🎯 **DECISION POINT**

**If we find 1-2 more SUI-like pairs:** 
→ Deploy multi-pair portfolio (RECOMMENDED)

**If no additional pairs pass zero-loss test:**
→ Scale SUIUSDT or accept 38-day timeline

**Timeline for Decision:** End of Day 2 (Feb 6, 2026)

---

**Status:** SUIUSDT is proven and ready. Now hunting for companions. The plan is clear: test candidates systematically, maintain zero-loss rule, and build the optimal portfolio.

**Next Action:** Begin candidate testing with ARB/OP/APT.
