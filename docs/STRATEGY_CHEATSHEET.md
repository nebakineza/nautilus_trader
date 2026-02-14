# Mean Reversion v003 "JAMES" — Strategy Cheat Sheet

> *"Everything mean reverts."* — The core premise.

---

## 🧠 Philosophy

James's 33-year methodology: price deviates from the statistical mean,
then snaps back. We measure the deviation, confirm the trend is favorable,
check momentum agrees, and enter when all three align.

**Three-Model Confluence** — all must agree for an entry:

```
┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│  1. MEAN REVERSION  │ + │     2. TREND GATE    │ + │  3. RSI CONFIDENCE  │
│  Z-score outside    │   │  EMA cross confirms  │   │  Momentum turning   │
│  Bollinger Band     │   │  direction            │   │  at extreme         │
└─────────────────────┘   └─────────────────────┘   └─────────────────────┘
         ALL THREE MUST PASS → ENTRY
```

---

## 📊 Indicators on Chart

| Indicator | Settings | What to Watch |
|-----------|----------|---------------|
| **Bollinger Bands** | Period=26, StdDev=2.0 | Price outside bands = deviation from mean |
| **EMA Fast** | Period=39 (5m) / 25 (1h) | Trend direction — above slow = bullish |
| **EMA Slow** | Period=152 (5m) / 113 (1h) | Macro trend anchor |
| **RSI** | Period=14, scale 0–1 | Oversold < 0.25, Overbought > 0.75 |

### Z-Score (mental model)
The strategy computes Z-score from Bollinger Bands:

$$Z = \frac{\text{close} - \text{BB}_{middle}}{\text{BB}_{upper} - \text{BB}_{middle}}$$

- Z = 0 → price at the mean (BB middle)
- Z = +1 → price at upper band
- Z = -1 → price at lower band
- Z = +1.4 → price 40% beyond the upper band ← **entry territory**

---

## 🟢 LONG Entry Checklist

All 5 gates must pass simultaneously:

| # | Gate | What to See on Chart | Parameter |
|---|------|---------------------|-----------|
| 1 | **Z-score ≤ −entry_band** | Price at or below lower BB | 5m: −0.8σ, 1h: −1.4σ |
| 2 | **Z turning UP** | Current Z > Z from 3 bars ago | lookback=3 |
| 3 | **Trend bullish** | EMA fast > EMA slow (with 0.06% gap) | strict=True |
| 4 | **RSI confirms** | RSI < 0.25 (oversold) OR RSI turning up | period=14 |
| 5 | **Noise filter passes** | Current \|Z\| ≥ 90% of peak \|Z\| in last 35 bars | ratio=0.90 |

### Visual: What a LONG entry looks like
```
Price
  │
  │          ╭── EMA fast crosses above EMA slow (trend turns bullish)
  │         ╱
  │  ──────╱──────── EMA slow
  │       ╱
  │ ─────╱────────── EMA fast
  │     ╱
  │    ╱   ┌─── RSI oversold + turning up
  │   ╱    │
  │──╱─────┼──────── Upper BB
  │ ╱      │
  │╱  ○────┤──────── BB Middle (mean)
  │        │
  │   ●────┘──────── Lower BB ← price touches/breaches here
  │   ↑
  │   Z turning up = GREEN DOT (Z[-1] > Z[-4])
  │   ENTRY HERE
```

---

## 🔴 SHORT Entry Checklist

Mirror of LONG:

| # | Gate | What to See on Chart | Parameter |
|---|------|---------------------|-----------|
| 1 | **Z-score ≥ +entry_band** | Price at or above upper BB | 5m: +0.8σ, 1h: +1.4σ |
| 2 | **Z turning DOWN** | Current Z < Z from 3 bars ago | lookback=3 |
| 3 | **Trend bearish** | EMA slow > EMA fast (with gap) | strict=True |
| 4 | **RSI confirms** | RSI > 0.75 (overbought) OR RSI turning down | period=14 |
| 5 | **Noise filter passes** | Same quality check | ratio=0.90 |

---

## 🚪 Exit Rules (Priority Order)

Exits are checked every bar. First matching rule fires:

| Priority | Exit Type | Condition | Action | What You See |
|----------|-----------|-----------|--------|--------------|
| **1** | 🛑 **Hard Stop** | Price moves −2.9% from avg entry | Close ALL legs | Price drops sharply against you |
| **2** | ✅ **Take Profit** | Z-score reaches opposite band | LIFO (one leg) or ALL | Price reverts back through mean |
| **3** | 🔄 **Trend Stop** | Trend flips against position | Close ALL legs | EMA fast crosses below slow (if LONG) |
| **4** | ⏰ **Time Stop** | Held > max_hold bars | Close ALL legs | Position has been open too long |
| **5** | 🚨 **Emergency** | Z-score hits ±5.0σ | Close ALL legs | Extreme move — thesis broken |

### Take Profit Detail
- **5m Workhorse**: Exit when Z crosses ±1.5σ on the opposite side
- **1h Sniper**: Exit when Z crosses ±0.3σ (very aggressive — lock in immediately)

### LIFO Exit (multi-leg positions)
When TP fires on a multi-leg position, only the **last leg** closes first:
```
Entry:  Leg 1 ($45) → Leg 2 ($60) → Leg 3 ($45)
                                      ↑ exits first
Exit:   Leg 3 closes → Leg 2 closes → Leg 1 closes
```
All other exits (stop, trend, time) close ALL legs at once.

---

## 🦵 Double-Tap Position Building

Positions build in up to 3 legs as price moves further from mean:

| Leg | Zone | Threshold | Budget | When |
|-----|------|-----------|--------|------|
| **1 — Nibble** | Inner band | 0.8σ (5m) / 1.4σ (1h) | 30% ($45) | First touch outside BB |
| **2 — Chunk** | Outer band | 1.4σ (5m) / 3.0σ (1h) | 40% ($60) | Price goes further against |
| **3 — Slam** | Extreme | 2.1σ (5m) / 4.5σ (1h) | 30% ($45) | Extreme deviation |

Rules for adding legs:
- Must be **same direction** as existing position
- Must be in a **higher zone** than last leg (price went further)
- Z-score must still be **turning** (not accelerating against you)
- Trend must still **confirm**
- Cooldown: 6 bars (5m) / 15 bars (1h) between legs

---

## ⚡ Trend Turn Bonus

*"You don't buy till it actually turns... Trend turns and then you buy"*

When the trend **just flipped** (within 7 bars), entry bands tighten by 0.3σ:
- Normal nibble: 0.8σ → At trend turn: **0.5σ** (easier entry)
- The turn itself IS conviction — less Z-score needed

**On chart**: Watch for EMA fast crossing EMA slow. The 7 bars after that cross
are the "bonus window" where entries need less extreme Z-scores.

---

## 🔇 Noise Suppression

*"Suppresses less optimal signals if stronger ones exist within the time horizon"*

Not a simple cooldown — it's **signal quality** filtering:

```
Bar 10: Z = -2.5σ → ENTRY (strong signal, fires)
Bar 12: Z = -1.1σ → SUPPRESSED (only 44% as strong, below 90% threshold)
Bar 25: Z = -2.3σ → ENTRY (92% of peak, passes 90% threshold)
```

| Parameter | 5m Workhorse | 1h Sniper |
|-----------|-------------|-----------|
| Window | 35 bars (175 min) | 35 bars (35 hours) |
| Min quality ratio | 90% of peak | 95% of peak |
| Cooldown | 6 bars (30 min) | 15 bars (15 hours) |

---

## 📐 Live Profiles — SOL-USD-PERP

### 5m Workhorse (MR-SOL-003)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Timeframe | 5-MINUTE | ~12 bars/hour |
| BB Period | 26 | Slightly faster than default 32 |
| Entry (inner) | ±0.8σ | Nibble zone |
| Entry (outer) | ±1.4σ | Chunk zone |
| Exit (TP) | ±1.5σ opposite | Let SOL winners run |
| Hard stop | 2.9% | Tight — protect capital |
| Max hold | 75 bars (6h 15m) | SOL trends resolve slower |
| EMA fast/slow | 39/152 | ~3h / ~13h effective |
| Min EMA gap | 0.06% | Sensitive trend detection |
| Noise ratio | 0.90 | Only top 90%+ signals |
| Cooldown | 6 bars (30 min) | |
| **Backtest WR** | **80.0%** | 70 trades/60 days |
| **Avg P&L/trade** | **$0.23** | |

### 1h Sniper (MR-SOL-SNP)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Timeframe | 1-HOUR | 24 bars/day |
| BB Period | 24 | |
| Entry (inner) | ±1.4σ | Only deep extremes |
| Entry (outer) | ±3.0σ | Ultra-extreme only |
| Exit (TP) | ±0.3σ opposite | Lock in immediately |
| Hard stop | 4.4% | Wide — give room |
| Max hold | 50 bars (~2 days) | |
| EMA fast/slow | 25/113 | ~1d / ~5d effective |
| Min EMA gap | 0.16% | Clear trend required |
| Noise ratio | 0.95 | Ultra-strict |
| Cooldown | 15 bars (15h) | |
| **Backtest WR** | **100%** | 13 trades/60 days |
| **Avg P&L/trade** | **$0.82** | |

---

## 💰 Position Sizing

### Auto-Scale Mode (current)
```
equity = HL unified account value (spot USDC + perp margin)
max_position = equity × leverage (5x)
base_budget  = max_position × 0.75

Example: $2,400 equity → max $12,000 → base $9,000
  Nibble (30%): $2,700
  Chunk  (40%): $3,600
  Slam   (30%): $2,700
```

Each leg also scales with Z-score magnitude:
- At 0.8σ: 1.0× (base size)
- At 1.4σ: 1.3× (more conviction)
- At 3.0σ: 2.1× (maximum conviction, capped at 2.5×)

---

## 🛡️ Risk Management

| Control | Value | Purpose |
|---------|-------|---------|
| Hard stop | 2.9% (5m) / 4.4% (1h) | Max loss per trade |
| Max hold | 75 bars / 50 bars | Thesis expiry |
| Emergency Z | ±5.0σ | Black swan exit |
| Max daily loss | $500 | Stops new entries for the day |
| Max daily trades | 20 | Prevents overtrading |
| Max open positions | 1 | Single position at a time |
| Order type | IOC Limit | 0.5% slippage cap |

### Worst-Case Scenarios
```
Single leg loss:   $45 × 2.9% = -$1.31  (5m nibble)
Full 3-leg loss:   $150 × 2.9% = -$4.35 (5m all legs hit stop)
Sniper full loss:  $150 × 4.4% = -$6.60 (1h all legs hit stop)
Daily max loss:    -$500 (halts all new entries)
```

---

## 🏗️ Architecture

```
hl_mr_sol_v003.service (ONE systemd service)
  └── TradingNode (trader_id: MR-SOL-003)
      │
      ├── DataClient-HYPERLIQUID
      │   ├── Subscribe: SOL-USD-PERP 5-MINUTE bars
      │   └── Subscribe: SOL-USD-PERP 1-HOUR bars
      │
      ├── ExecClient-HYPERLIQUID (shared SDK + nonce lock)
      │
      ├── Strategy: MR-SOL-003 (5m workhorse)
      │   ├── BB(26) + EMA(39/152) + RSI(14)
      │   └── Self-managed position tracking (LIFO legs)
      │
      └── Strategy: MR-SOL-SNP (1h sniper)
          ├── BB(24) + EMA(25/113) + RSI(14)
          └── Self-managed position tracking (LIFO legs)
```

Both strategies share one HL connection, one nonce lock, one reconciliation loop.
Fill routing is by `client_order_id` — each strategy only sees its own fills.

---

## 🔧 Operations

```bash
# Service management
sudo systemctl status hl_mr_sol_v003
sudo systemctl restart hl_mr_sol_v003
sudo journalctl -u hl_mr_sol_v003 -f           # Live logs

# Check fills
journalctl -u hl_mr_sol_v003 --since "1h ago" | grep "FILL"

# Check P&L
journalctl -u hl_mr_sol_v003 --since "1h ago" | grep "CLOSED"

# Check position state
journalctl -u hl_mr_sol_v003 --since "5 min ago" | grep "📊"

# Check HL account
source .env && .venv/bin/python -c "
import requests, os
w = os.environ['HYPERLIQUID_WALLET']
r = requests.post('https://api.hyperliquid.xyz/info',
    json={'type': 'clearinghouseState', 'user': w}, timeout=5)
for p in r.json().get('assetPositions', []):
    pi = p['position']
    if abs(float(pi.get('szi',0))) > 0:
        print(f\"{pi['coin']}: {pi['szi']} @ \${pi['entryPx']} uPnL=\${pi['unrealizedPnl']}\")
"
```

---

## 📖 Log Message Guide

| Emoji | Meaning |
|-------|---------|
| 🟢 | LONG entry |
| 🔴 | SHORT entry |
| ✅ | Winning trade closed |
| ❌ | Losing trade closed |
| 📍 | Fill received |
| 📊 | Periodic status (every 10 bars) |
| 📐 | Auto-scale sizing update |
| 🔄 | Trend turn detected |
| 📅 | Daily P&L reset |
| 🚪 | Exit signal triggered |
| ⚠️ | Warning (non-fatal) |
| 🚀 | Strategy started |

---

*Last updated: February 14, 2026*
*Strategy version: v003.0 "JAMES"*
*Profiles: SOL OPTIMAL (5m) + SOL SNIPER (1h)*
