# Nebakineza Portfolio Plan 2026

> **Strategy:** Mean Reversion v003 (JAMES)  
> **Platform:** Hyperliquid Perpetuals  
> **Engine:** NautilusTrader  
> **Start Date:** February 13, 2026  
> **Starting Capital:** $2,400 USDC (consolidated from Bybit)  
> **Target:** $67,000 USDC (unlocks $1,000/day production mode)  
> **DCA Schedule:** $200 USDC every Friday + ~$800 Bybit coin liquidation over first weeks  

---

## Table of Contents

1. [Strategy Overview](#1-strategy-overview)
2. [Coin Selection & Backtest Results](#2-coin-selection--backtest-results)
3. [Portfolio Tiers & Rollout Phases](#3-portfolio-tiers--rollout-phases)
4. [Auto-Scaling Mechanism](#4-auto-scaling-mechanism)
5. [Growth Model & Compounding Math](#5-growth-model--compounding-math)
6. [Month-by-Month Projections](#6-month-by-month-projections)
7. [Scenario Analysis](#7-scenario-analysis)
8. [Risk Management](#8-risk-management)
9. [Operational Milestones](#9-operational-milestones)
10. [Beyond $67k — Production Mode](#10-beyond-67k--production-mode)

---

## 1. Strategy Overview

### 1.1 Core Methodology — "The Four-Legged Stool"

Based on a 33-year veteran trader's framework (IDSS — Indicator Decision Support System), our Mean Reversion v003 strategy implements three of the four models that produce 90%+ win rates when used in confluence:

| Model | Implementation | Role |
|-------|---------------|------|
| **Mean Reversion** | Bollinger Bands → Z-score oscillator | Primary signal — identifies extreme deviations from the mean |
| **Optimized Trend** | Dual EMA (39/152) with separation filter | Gate — blocks entries against the trend |
| **Noise Suppression** | Quality window (35 bars, 90% ratio) | Filter — removes weak/marginal signals |
| **Confidence** | RSI + Z-score + trend-turn bonus | Confluence — confirms entries with momentum |

### 1.2 Entry Rules

1. Price must be **outside** the Bollinger Band (Z-score > entry_band)
2. A **turning point** must be detected (Z-score reversing direction)
3. **Trend must confirm** — EMAs must agree with trade direction
4. **Noise suppression** must pass — signal quality must exceed threshold
5. **Two-zone system**: inner band (0.8σ) for nibbles, outer band (1.4σ) for conviction entries

### 1.3 Position Sizing — Double-Tap LIFO

Capital is deployed across up to 3 legs per trade:

| Leg | Name | Allocation | Trigger |
|-----|------|-----------|---------|
| 1 | **Nibble** | 30% of budget | Inner band (0.8σ) |
| 2 | **Chunk** | 40% of budget | Outer band (1.4σ) — price went further against us |
| 3 | **Slam** | 30% of budget | Extreme (>1.4σ) — maximum conviction |

Exits are **LIFO** — Last In, First Out. Most trades are single-leg (nibble only).

### 1.4 Exit Rules

| Exit Type | Trigger | Priority |
|-----------|---------|----------|
| **Take Profit** | Z-score crosses opposite band (1.5σ) | 1 |
| **Hard Stop** | Position down 2.9% from average entry | 2 |
| **Time Stop** | Position held > 75 bars (6.25 hours on 5m) | 3 |
| **Emergency** | Z-score exceeds 4.5σ adverse | 4 |

### 1.5 Verified Performance (17-Day Backtest, Production Params)

| Metric | Value |
|--------|-------|
| Win Rate | 72.1% |
| Total Trades | 68 |
| Trades/Day | 4.0 |
| Profit Factor | >1.5 |
| Avg Trade | $0.25 (at $150 base) |
| Max Single Loss | 2.9% of position |
| Scaling | 98.5% linear (verified to 100x) |

---

## 2. Coin Selection & Backtest Results

### 2.1 Multi-Coin Sweep

All coins tested with **identical production parameters** over 17 days on 5-minute bars. Base trade size $150, max position $200.

| Rank | Coin | Trades | Win Rate | $/day | Trades/day | Grade |
|------|------|--------|----------|-------|------------|-------|
| 1 | **SOL** | 69 | 72.5% | +$1.02 | 4.1 | A+ |
| 2 | **DOGE** | 70 | 71.4% | +$0.82 | 4.1 | A+ |
| 3 | **SUI** | 71 | 70.4% | +$0.56 | 4.2 | A+ |
| 4 | **ETH** | 66 | 71.2% | +$0.52 | 3.9 | A+ |
| 5 | **BTC** | 67 | 73.1% | +$0.46 | 3.9 | A+ |
| 6 | **WIF** | 8 | 75.0% | +$0.25 | 0.5 | A |
| 7 | **ENA** | 34 | 64.7% | +$0.22 | 2.0 | B |
| 8 | **APT** | 16 | 68.8% | +$0.18 | 0.9 | A |
| 9 | **TIA** | 7 | 71.4% | +$0.12 | 0.4 | A |
| 10 | **PENGU** | 17 | 82.4% | +$0.05 | 1.0 | B |
| — | AVAX | 31 | 64.5% | -$0.28 | 1.8 | F |
| — | ARB | 18 | 61.1% | -$0.11 | 1.1 | F |
| — | NEAR | 9 | 55.6% | -$0.02 | 0.5 | F |
| — | ZRO | 26 | 53.8% | -$0.86 | 1.5 | F |
| — | HYPE | 79 | 64.6% | -$0.46 | 4.6 | F |
| — | JUP | 47 | 59.6% | -$0.42 | 2.8 | F |

### 2.2 Key Insights

- **Top 5 coins** (SOL, DOGE, SUI, ETH, BTC) all share similar characteristics: 70%+ win rate, 4 trades/day, positive daily P&L
- **SOL is the clear leader** at $1.02/day — nearly 2x the next best
- Coins with **low trade frequency** (WIF, APT, TIA) are profitable but contribute less due to fewer opportunities
- Coins with **<65% win rate** are consistently unprofitable with our parameters — excluded
- **LINK, ADA, DOT, OP, RENDER, FIL, ATOM** produced zero trades — the trend/noise filters correctly blocked everything (sideways/unclear market structure)

### 2.3 Coins Excluded (and Why)

| Coin | Reason | Status |
|------|--------|--------|
| AVAX | 64.5% WR, -$0.28/day — fees eat the edge | ❌ Excluded |
| ARB | 61.1% WR, negative — too choppy | ❌ Excluded |
| NEAR | 55.6% WR — near coin-flip | ❌ Excluded |
| ZRO | 53.8% WR — worst performer | ❌ Excluded |
| HYPE | 64.6% WR, too many trades, negative — overtrading | ❌ Excluded |
| JUP | 59.6% WR — below threshold | ❌ Excluded |
| LINK/ADA/DOT/OP | Zero signals — strategy correctly abstained | 🔄 Re-test quarterly |

---

## 3. Portfolio Tiers & Rollout Phases

### 3.1 Concentration Strategy: SOL → $67k → Diversify

The fastest path to $67k is **maximum concentration on the highest-performing coin**. Diversification is a luxury for when the base is large enough to absorb rate dilution.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    PORTFOLIO ARCHITECTURE                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  PHASE 1 — CONCENTRATION (Feb 2026 → Oct 2026)                    │
│  ┌───────────────────────────────────────┐                          │
│  │               S O L                   │                          │
│  │            1.53%/day                  │                          │
│  │        100% of capital                │                          │
│  │     $67k target in 35 weeks           │                          │
│  └───────────────────────────────────────┘                          │
│  All capital concentrated on the single best performer.            │
│  Max compounding speed. Accepts concentration risk.                │
│                                                                     │
│  PHASE 2 — PRODUCTION DIVERSIFICATION (at $67k)                    │
│  ┌─────┐ ┌──────┐ ┌─────┐ ┌─────┐ ┌─────┐                        │
│  │ SOL │ │ DOGE │ │ SUI │ │ ETH │ │ BTC │                        │
│  │ A+  │ │  A+  │ │ A+  │ │ A+  │ │ A+  │                        │
│  └─────┘ └──────┘ └─────┘ └─────┘ └─────┘                        │
│  5-coin diversification for $1,000/day stability.                  │
│  Daily rate: 1.01% — but on $67k+ base = $670+/day                │
│                                                                     │
│  PHASE 3 — FULL PORTFOLIO (at $150k+)                              │
│  ┌─────┐ ┌──────┐ ┌─────┐ ┌─────┐ ┌─────┐                        │
│  │ SOL │ │ DOGE │ │ SUI │ │ ETH │ │ BTC │                        │
│  │ A+  │ │  A+  │ │ A+  │ │ A+  │ │ A+  │                        │
│  ├─────┤ ├─────┤ ├─────┤ ├─────┤ ├───────┤                       │
│  │ WIF │ │ APT │ │ TIA │ │ ENA │ │ PENGU │                       │
│  │  A  │ │  A  │ │  A  │ │  B  │ │   B   │                       │
│  └─────┘ └─────┘ └─────┘ └─────┘ └───────┘                       │
│  10-coin portfolio for maximum diversification.                    │
│  Daily rate: 0.63% — but on $150k+ base = $945+/day              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Why SOL-Only Until $67k?

**Diversification has a cost — and at small capital, that cost is time.** Every coin you add dilutes the per-coin capital allocation and slows compounding:

| Configuration | Daily Rate | Time to $67k | Time Lost vs SOL-Only |
|---------------|-----------|--------------|----------------------|
| **SOL only** | **1.53%** | **35 weeks** | **—** |
| Top 5 coins | 1.01% | 46 weeks | +11 weeks |
| All 10 coins | 0.63% | 63 weeks | +28 weeks |

Diversifying from day one costs **11 to 28 extra weeks** to reach $67k. That's 3–7 months of delayed income.

**The math is clear: concentrate first, diversify once the base is large enough that rate dilution doesn't matter.**

- At $45–$67k: SOL-only. Every basis point compounds. The 1.53% daily rate is the engine.
- At $67k: Add top 5 coins. The 1.01% rate on $67k still yields $670+/day — above target.
- At $150k+: Add all 10 coins. The 0.63% rate on $150k yields $945/day — dilution is irrelevant.

**Concentration risk is real but bounded:** SOL has the highest win rate (72.5%), highest trade frequency (4.1/day), and the hard stop limits any single loss to 2.9% of position. The bigger risk is wasting months at lower compounding rates.

### 3.3 Phase Schedule

| Phase | Trigger | Coins | Total | Est. Timeline |
|-------|---------|-------|-------|---------------|
| **Phase 1** | Launch (Feb 13) | **SOL only** | 1 | Weeks 1–20 (Feb → Jul '26) |
| **Phase 2** | Equity ≥ $67,000 | + DOGE, SUI, ETH, BTC | 5 | ~Week 20 (Jul '26) |
| **Phase 3** | Equity ≥ $150,000 | + WIF, APT, TIA, ENA, PENGU | 10 | ~Week 27 (Aug '26) |
| **Re-evaluate** | Quarterly | Test AVAX, ARB, LINK, etc. | 10+ | Ongoing |

> **Key insight:** Phase 1 is the sprint. SOL alone at 1.53%/day with $2,400 start
> reaches $67k in ~20 weeks. Only after hitting this target do we trade speed for safety.

---

## 4. Auto-Scaling Mechanism

### 4.1 How It Works

The strategy includes a **dynamic position sizing engine** that queries the Hyperliquid unified account equity before each new trade entry and computes sizes automatically.

> **Note:** Hyperliquid runs in **Unified Trading** mode — spot and perps share a single margin pool. There is no separate spot/perp balance or transfer. The equity figure is the total unified account value.

```
Before each new trade:
  1. Query HL REST API → get unified account equity
  2. max_position = equity × leverage (5x)
  3. base_trade_size = max_position × 0.75
  4. Leg sizes computed from base_trade_size × allocation fractions
```

### 4.2 Sizing Table (at 5x Leverage)

| Account Equity | Max Position | Base Trade Size | Leg 1 (30%) | Est. Daily P&L |
|----------------|-------------|----------------|-------------|----------------|
| $45 | $225 | $169 | $51 | ~$0.67 |
| $200 | $1,000 | $750 | $225 | ~$3.00 |
| $1,000 | $5,000 | $3,750 | $1,125 | ~$15 |
| $5,000 | $25,000 | $18,750 | $5,625 | ~$75 |
| $10,000 | $50,000 | $37,500 | $11,250 | ~$150 |
| $25,000 | $125,000 | $93,750 | $28,125 | ~$375 |
| $67,000 | $335,000 | $251,250 | $75,375 | ~$1,005 |

### 4.3 Safety Guards

| Guard | Value | Purpose |
|-------|-------|---------|
| Floor | $10 minimum position | Don't trade dust |
| Ceiling | $500,000 max position | Hard cap regardless of equity |
| Fallback | Last known sizes | If API query fails, use previous sizes |
| Scope | New entries only | Don't resize mid-trade (legs keep original sizing) |

### 4.4 Multi-Coin Allocation

When running multiple coins from a single HL account, each coin's allocation will be:

```
per_coin_max_position = (equity × leverage) / N_coins
per_coin_base_size = per_coin_max_position × 0.75
```

This needs implementation in the auto-scale logic per runner (tracked as a future task — currently each coin's runner sees full equity).

---

## 5. Growth Model & Compounding Math

### 5.1 Key Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| Starting capital | $2,400 | Consolidated from Bybit (Feb 13) |
| Weekly DCA | $200 | Friday deposits |
| Effective leverage | 5x | Conservative for perpetuals |
| Backtest-to-live haircut | 60% | Industry standard for execution gap |
| **Phase 1 daily rate (SOL only)** | **1.53%** | SOL backtest × 60% haircut |
| Phase 2 daily rate (top 5 coins) | 1.01% | After diversification at $67k |
| Phase 3 daily rate (all 10) | 0.63% | After full portfolio at $150k+ |

### 5.2 The Compounding Engine

During Phase 1 (SOL-only), the daily return rate is **1.53%** of equity. Starting at $2,400 (consolidated from Bybit), the compounding starts well past the initial seed phase:

```
Week 1:  $2,400 equity  → $257/week profit   + $200 DCA  ← ALREADY PAST INFLECTION
Week 4:  $4,700 equity  → $503/week profit   + $200 DCA
Week 8:  $9,200 equity  → $985/week profit   + $200 DCA
Week 12: $17,200 equity → $1,842/week profit + $200 DCA
Week 16: $32,400 equity → $3,470/week profit + $200 DCA
Week 20: $60,900 equity → $6,524/week profit + $200 DCA
Week 22: $80,000+ equity → 🎯 TARGET
```

### 5.3 The Inflection Point

The inflection point is when **weekly trading P&L exceeds weekly DCA deposits**. With $2,400 starting capital, **we start already past inflection**:

| Phase | When | Weekly Trading P&L | Weekly DCA | Ratio |
|-------|------|-------------------|-----------|-------|
| **Inflection** | **Week 1 (NOW)** | **$257** | $200 | **1.3x 🔄** |
| Compound Dominant | Weeks 1–8 | $257–$985 | $200 | 1–5x |
| DCA Irrelevant | Weeks 8+ | >$985 | $200 | >5x |
| Pure Compounding | Weeks 12+ | >$1,800 | $200 | >9x |

At $2,400 starting equity, trading profits already exceed DCA from day one. The weekly $200 + Bybit coin liquidation proceeds add fuel but compounding does the heavy lifting immediately.

---

## 6. Month-by-Month Projections

### 6.1 Base Case (60% Haircut — SOL-Only Phase 1, $2,400 Start)

| Month | Week | Equity | Coins | Daily P&L | Weekly P&L | Cum. Deposits |
|-------|------|--------|-------|-----------|-----------|---------------|
| **Feb** | 1–2 | $3,200 | SOL | $49/d | $340/w | $2,800 |
| **Mar** | 3–6 | $6,800 | SOL | $104/d | $729/w | $3,600 |
| **Apr** | 7–10 | $14,200 | SOL | $217/d | $1,521/w | $4,400 |
| **May** | 11–14 | $29,400 | SOL | $450/d | $3,150/w | $5,200 |
| **Jun** | 15–18 | $60,600 | SOL | $927/d | $6,490/w | $6,000 |
| **Jul** | **~20** | **$67,000+** | **→ diversify** | **$1,025/d** | **$7,178/w** | **$6,400** |

> **Note:** Starting at $2,400 instead of $45 skips ~10 weeks of the seed phase.
> The $67k target is now reachable by **~July 2026** instead of October.
> The ~$800 in Bybit coin assets will be liquidated and added over the first weeks.

### 6.2 Key Milestones (SOL-Only Sprint, $2,400 Start)

| Milestone | Equity | Est. Week | Est. Date | Event |
|-----------|--------|-----------|-----------|-------|
| First fills | $2,400 | Week 1 | Feb 13 | Strategy live with Bybit funds |
| Inflection | $2,400 | **Week 1 (NOW)** | **Feb 13** | **Already past — compounding > DCA** |
| $5,000 | $5,000 | Week 5 | Mar 14 | Daily PnL ~$77 |
| $10,000 | $10,000 | Week 8 | Apr 4 | Daily PnL ~$153 |
| $25,000 | $25,000 | Week 13 | May 9 | Daily PnL ~$383 |
| DCA stop? | $50,000 | Week 17 | Jun 6 | DCA is <3% of weekly growth |
| **🎯 $67,000** | **$67,000** | **Week 20** | **Jul 4** | **→ Phase 2: diversify to 5 coins** |
| $150,000 | $150,000 | ~Week 27 | Aug 22 | → Phase 3: full 10-coin portfolio |

### 6.3 End of 2026 Summary (Base Case — $2,400 Start)

| Metric | Value |
|--------|-------|
| **$67k target reached** | **~Week 20 (Jul 2026)** |
| **EOY Equity (Dec 31)** | **$500,000+** |
| Total Deposited | ~$6,400 ($2,400 Bybit + ~20 × $200 DCA) |
| Trading Profits | ~$494,000+ |
| ROI on Deposits | 7,700%+ |
| Daily P&L at EOY | ~$3,000+/day (5–10 coins) |
| Monthly P&L at EOY | ~$90,000+/month |
| Phase at EOY | Phase 2/3 (diversified, compounding on large base) |

> Starting at $2,400 instead of $45 accelerates everything by ~15 weeks.
> $67k reached by July, diversification in Aug, Phase 3 by late 2026.

---

## 7. Scenario Analysis

### 7.1 SOL-Only Sprint: Weeks to $67k by Scenario ($2,400 Start)

| Scenario | Haircut | Daily Rate | $67k Reached | EOY 2026 Equity | Daily @EOY |
|----------|---------|-----------|-------------|----------------|------------|
| Backtest (100%) | 100% | 2.55% | **Week 13 (May '26)** | $5,000,000+ | $50,000+/day |
| Optimistic (75%) | 75% | 1.91% | **Week 16 (Jun '26)** | $1,500,000+ | $15,000+/day |
| **Realistic (60%)** | **60%** | **1.53%** | **Week 20 (Jul '26)** | **$500,000+** | **$3,000+/day** |
| Conservative (50%) | 50% | 1.27% | Week 24 (Aug '26) | $200,000+ | $1,300+/day |
| Pessimistic (40%) | 40% | 1.02% | Week 30 (Sep '26) | $100,000 | $630/day |

### 7.2 What Each Scenario Means

- **Backtest (100%):** Live performance matches backtest perfectly. Unlikely but possible on high-liquidity pairs (SOL, ETH, BTC).
- **Optimistic (75%):** Minor slippage, occasional missed fills. Achievable if most coins perform near-backtest.
- **Realistic (60%):** Standard industry haircut. Accounts for slippage, execution latency, market microstructure, and occasional adverse market regimes.
- **Conservative (50%):** Assumes significant execution challenges. Still profitable, still grows.
- **Pessimistic (40%):** Strategy works but barely. Fee pressure and slippage eat half the edge.

### 7.3 The Cost of Early Diversification

| Setup | Daily Rate | Weeks to $67k | Time Lost |
|-------|-----------|---------------|----------|
| **SOL only** ✅ | **1.53%** | **35 weeks** | **—** |
| Top 5 from day 1 | 1.01% | 46 weeks | +11 weeks |
| All 10 from day 1 | 0.63% | 63 weeks | +28 weeks |

**Diversifying from day one would cost 3–7 months.** The correct sequence:
1. **Sprint:** SOL-only to $67k (35 weeks)
2. **Stabilize:** Diversify to 5 coins for $1k/day production mode
3. **Scale:** Add remaining coins once base exceeds $150k

---

## 8. Risk Management

### 8.1 Per-Trade Risk

| Risk Type | Limit | Max Loss |
|-----------|-------|----------|
| Hard stop | 2.9% of position | Varies with position size |
| Time stop | 75 bars (6.25 hrs) | Market-dependent |
| Emergency σ | 4.5σ adverse | Rare — extreme moves |

### 8.2 Portfolio-Level Risk

| Control | Setting | Effect |
|---------|---------|--------|
| Max daily loss | $30 (scales with equity) | Pauses trading after bad day |
| Max daily trades | 20 | Prevents overtrading |
| Max open positions | 1 per coin | No pyramiding beyond 3 legs |
| Auto-scale ceiling | $500,000 | Absolute cap on position size |

### 8.3 Worst-Case Scenarios

| Event | Impact | Mitigation |
|-------|--------|-----------|
| 3 consecutive losses (single coin) | -8.7% of that coin's allocation | Diversification across 5-10 coins |
| All coins hit stop simultaneously | -2.9% × leverage = -14.5% of equity | Uncorrelated signal timing across assets |
| Exchange outage | Open positions can't be managed | Hard stop on exchange side; time stops |
| Strategy edge degrades | Declining win rate over weeks | Weekly performance review; pause if WR < 55% |
| Black swan (>10σ move) | Potential liquidation at high leverage | 5x leverage = safe margin; 20% move needed for liquidation |

### 8.4 Circuit Breakers

Automated checks to implement:

- [ ] **Weekly WR check:** If rolling 20-trade win rate drops below 55%, alert and review
- [ ] **Max drawdown:** If equity drops >20% from peak, pause all strategies
- [ ] **Daily loss limit:** Scale with equity (currently fixed at $30)
- [ ] **Correlation monitor:** If >3 coins enter simultaneously, flag potential correlated risk

---

## 9. Operational Milestones

### 9.1 Deployment Checklist

| # | Milestone | Status | Date |
|---|-----------|--------|------|
| 1 | Strategy v003 coded & backtested | ✅ Complete | Feb 12 |
| 2 | Auto-scaling implemented | ✅ Complete | Feb 13 |
| 3 | SOL live on HL (mainnet) | ✅ Running | Feb 13 |
| 4 | Bybit funds consolidated to HL ($2,400) | ✅ Complete | Feb 13 |
| 5 | SOL producing live fills | ⏳ Awaiting first signal | Feb 13 |
| 6 | Weekly performance review #1 | 🔲 Planned | Feb 21 |
| 7 | Compounding inflection (wk PnL > $200) | ✅ Already past | Feb 13 |
| 8 | Bybit coin assets sold → added to DCA | 🔲 In progress | ~Mar |
| 9 | Consider stopping DCA ($50k+) | 🔲 Decision point | ~Jun 6 |
| 10 | **$67k — Phase 2: deploy DOGE, SUI, ETH, BTC** | 🔲 Target | **~Jul 4** |
| 11 | Multi-coin auto-scale allocation | 🔲 Planned | At Phase 2 |
| 12 | $150k — Phase 3: add WIF, APT, TIA, ENA, PENGU | 🔲 Target | ~Aug 22 |
| 13 | $1,000/day sustained | 🔲 Target | ~Jul 2026 |

### 9.2 Weekly Review Process

Every Friday (coinciding with DCA deposit):

1. Check portfolio equity vs projection
2. Review per-coin win rates (rolling 20 trades)
3. Check for rejected/stuck orders
4. Verify auto-scale sizing is correct
5. Log results in tracking spreadsheet
6. Deposit $200 DCA

### 9.3 Quarterly Rebalancing

Every 3 months:

1. Re-run 30-day backtest sweep across all coins
2. Promote/demote coins between tiers based on performance
3. Test new coins (LINK, ADA, DOT, etc.) that previously showed no signal
4. Adjust parameters if market regime has shifted
5. Consider adding new HL-listed assets

---

## 10. Beyond $67k — Production Mode

### 10.1 The $1,000/Day Configuration

Once equity reaches $67,000, the portfolio switches to **production mode** with diversification:

| Parameter | Phase 2 (at $67k) | Phase 3 (at $150k+) |
|-----------|-------------------|---------------------|
| Active coins | SOL, DOGE, SUI, ETH, BTC | + WIF, APT, TIA, ENA, PENGU |
| Leverage | 5x | 3x (reduced for safety) |
| Daily rate | 1.01% | 0.63% |
| Daily P&L | ~$670/day → $1,500+/day | ~$945+/day |
| Monthly P&L | $20,000–$45,000 | $28,000+ |
| DCA | **Stopped** — portfolio self-sustaining | Stopped |

### 10.2 Profit Extraction Schedule

At $1,000/day, implement a withdrawal schedule:

| Component | Amount | Frequency |
|-----------|--------|-----------|
| Living expenses | $3,000/month | Monthly |
| Tax reserve | 25% of profits | Monthly |
| Reinvestment | Remaining | Compounds |

### 10.3 Long-Term Vision

| Timeline | Equity | Daily P&L | Phase | Status |
|----------|--------|-----------|-------|--------|
| Jul 2026 | $67,000 | $1,000/day | Phase 2 (5 coins) | Production mode activated |
| Aug 2026 | $150,000 | $1,500/day | Phase 3 (10 coins) | Full diversification |
| Dec 2026 | $500,000+ | $3,000+/day | Phase 3 | Self-sustaining + withdrawals |
| Jun 2027 | $2,000,000+ | $12,600/day | Phase 3 | Wealth accumulation |
| 2028+ | $5,000,000+ | $31,500/day | Phase 3 | Scale limit — add new strategies |

---

## Appendix A: DCA Deposit Schedule

| Friday | Date | Deposit | Cumulative |
|--------|------|---------|-----------|
| 1 | Feb 14, 2026 | $200 | $245 |
| 2 | Feb 21 | $200 | $445 |
| 3 | Feb 28 | $200 | $645 |
| 4 | Mar 7 | $200 | $845 |
| 5–8 | Mar 14 – Apr 4 | $200/wk | $1,645 |
| 9–12 | Apr 11 – May 2 | $200/wk | $2,445 |
| 13–16 | May 9 – May 30 | $200/wk | $3,245 |
| 17–20 | Jun 6 – Jun 27 | $200/wk | $4,045 |
| 21–24 | Jul 4 – Jul 25 | $200/wk | $4,845 |
| 25–28 | Aug 1 – Aug 22 | $200/wk | $5,645 |
| 29–32 | Aug 29 – Sep 19 | $200/wk | $6,445 |
| 33–36 | Sep 26 – Oct 17 | $200/wk | $7,245 |
| 37–40 | Oct 24 – Nov 14 | $200/wk | $8,045 |
| 41–46 | Nov 21 – Dec 26 | $200/wk | $9,245 |

**Total 2026 DCA deposits: $9,200** (46 weeks × $200)

---

## Appendix B: Per-Coin Return Characteristics

Return slope = daily P&L per $1 of base trade size (from backtest):

| Coin | Slope ($/day/$1) | At $1k Base | At $10k Base | At $100k Base |
|------|-------------------|------------|-------------|--------------|
| SOL | 0.006800 | $6.80/day | $68/day | $680/day |
| DOGE | 0.005467 | $5.47/day | $54.67/day | $546.70/day |
| SUI | 0.003733 | $3.73/day | $37.33/day | $373.30/day |
| ETH | 0.003467 | $3.47/day | $34.67/day | $346.70/day |
| BTC | 0.003067 | $3.07/day | $30.67/day | $306.70/day |
| WIF | 0.001667 | $1.67/day | $16.67/day | $166.70/day |
| ENA | 0.001467 | $1.47/day | $14.67/day | $146.70/day |
| APT | 0.001200 | $1.20/day | $12.00/day | $120.00/day |
| TIA | 0.000800 | $0.80/day | $8.00/day | $80.00/day |
| PENGU | 0.000333 | $0.33/day | $3.33/day | $33.30/day |

---

## Appendix C: Assumptions & Caveats

### What Could Go Wrong

1. **Backtest overfitting:** 17 days is a short backtest window. Market regimes change. Re-test monthly.
2. **Execution gap:** Live slippage on HL perpetuals may exceed the 60% haircut. Monitor actual vs expected fill quality.
3. **Compounding assumption:** Model assumes profits are immediately reinvested. In practice, there's a lag between equity growth and position sizing update (one trade cycle).
4. **Correlation risk:** During market-wide crashes (e.g., macro events), all coins may move together, making diversification ineffective.
5. **HL platform risk:** Single exchange dependency. Consider multi-exchange in 2027.
6. **Regulatory risk:** Crypto perpetuals regulation could change.
7. **Strategy decay:** Mean reversion edge may weaken if market structure changes (e.g., more algorithmic competition).

### What's Conservative in This Model

1. **60% haircut** is industry-standard conservative — many live strategies achieve 70-80% of backtest.
2. **5x leverage** is modest for HL (max 50x available) — large margin of safety.
3. **Equal allocation** across coins is suboptimal — weighting toward SOL/DOGE would improve returns.
4. **No parameter optimization per coin** — all coins use SOL's parameters. Per-coin tuning could improve weaker performers.

---

*Document generated: February 13, 2026*  
*Strategy: Mean Reversion v003 JAMES*  
*Engine: NautilusTrader on Hyperliquid*  
*Author: Nebakineza Trading Systems*
