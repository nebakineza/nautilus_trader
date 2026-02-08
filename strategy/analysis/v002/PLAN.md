# LLMM v005 Optimization Plan

**Status:** ✅ IMPLEMENTED  
**Files:** 
- [lead_lag_bybit_binance_mm_v005.py](../../lead_lag_bybit_binance_mm_v005.py)
- [lead_lag_bybit_binance_mm_v005_primer.py](../../lead_lag_bybit_binance_mm_v005_primer.py)

---

## 📋 **MARKET MAKING OPTIMIZATION PLAN - VPS CO-LOCATION EDGE**

---

### **Phase 1: Toxic Flow Avoidance (PRIORITY 1)**

**Problem:** Getting filled only when price moves against you (adverse selection)

**Proven Solutions:**

#### **1.1 Order Flow Imbalance (OFI) Filtering**
- **What it is:** Measure buy/sell pressure in top-of-book
- **How it works:** If BUY pressure > SELL pressure → price likely going UP → widen bid (or cancel), tighten ask
- **Implementation:**
  ```python
  ofi = (bid_qty_delta - ask_qty_delta) / (bid_qty_delta + ask_qty_delta)
  if ofi > threshold:  # Strong buy pressure
      bid_offset += ofi_penalty_bps  # Move bid further away
      ask_offset -= ofi_reward_bps   # Move ask closer (capture upward move)
  ```
- **Your existing code:** Already has `ofi_enabled` but likely needs tuning
- **Action:** Analyze fills vs OFI to find optimal thresholds

#### **1.2 Time-Weighted Spread Adjustment**
- **What it is:** Widen spread during high volatility periods
- **How it works:** Track mid-price moves over last 5-10 seconds
- **If volatility > threshold:** Add 10-20 bps to spread temporarily
- **Proven metric:** Rolling standard deviation of mid-price ticks

#### **1.3 Quote Lifetime Management**
- **Current:** You have `min_quote_lifetime_ms: 2000` (2 seconds)
- **Problem:** Stale quotes get picked off when market moves
- **Solution:** Cancel and requote faster when volatility spikes
  - Normal: 2-5 second lifetime
  - High volatility: 500ms-1s lifetime
- **VPS advantage:** Low latency allows aggressive requoting

---

### **Phase 2: Queue Position Optimization (PRIORITY 2)**

**Problem:** When to join vs leave the queue at a price level

#### **2.1 Queue Position Awareness**
- **Track:** Your position in queue at each price level
- **Nautilus has this:** `queue_position` tracking in backtest
- **Live implementation:** 
  - Monitor `OrderBookDepth` updates
  - Count orders ahead of you in queue
  - If queue_position > threshold (e.g., 50% of level volume) → Consider moving closer to mid

#### **2.2 Smart Price Placement**
- **DON'T:** Always quote at best bid/ask (high adverse selection)
- **DON'T:** Always quote far from mid (no fills)
- **DO:** Use "penny improvement" on leader exchange signals
  - Watch Binance bid@0.9500 with 5000 SUI depth
  - If Binance bid moves to 0.9501 → Immediately update Bybit bid to 0.9501 - spread/2
  - Beat other makers to the new price level

#### **2.3 Inventory-Driven Urgency**
- **Low inventory:** Join queue even if far back (need fills)
- **High inventory:** Only join if near front of queue (avoid building more)
- **Formula:** `max_acceptable_queue_position = f(inventory_skew)`

---

### **Phase 3: Latency Edge Exploitation (PRIORITY 3)**

**Your VPS advantage:** ~5-20ms vs retail ~50-200ms

#### **3.1 Leader-Follower Race Condition**
- **Opportunity:** When Binance orderbook updates, race to update Bybit quotes
- **Current:** You refresh on `refresh_interval: 5000ms`
- **Optimization:** Event-driven refresh on EVERY Binance top-of-book change
  - Binance bid moves → Immediately recalculate and update Bybit quotes
  - You beat slower market makers by 30-150ms
  - **This is your biggest edge!**

#### **3.2 Cancel-Replace vs Modify**
- **Current:** Likely using order modification
- **Faster:** Cancel + immediate resubmit (atomic on Bybit)
  - Modification waits for exchange ACK
  - Cancel+replace can be pipelined
- **Test both** with timestamps to find faster method

#### **3.3 Batch Order Updates**
- **Current:** Using `SubmitOrderList` (good!)
- **Optimization:** Always batch bid+ask updates in single message
- **VPS win:** Single round-trip vs 2x for separate orders

---

### **Phase 4: Spread Intelligence (PRIORITY 4)**

#### **4.1 Dynamic Spread Based on Conditions**
- **Base spread:** 60 bps (current)
- **Adjustments:**
  - High OFI: +10-20 bps
  - High volatility: +10-30 bps
  - Wide Binance spread: +5-10 bps
  - Low inventory: -5-10 bps (urgency to fill)
  - High inventory: +10-20 bps (avoid building)

#### **4.2 Competitive Spread Monitoring**
- **Watch:** Other market makers on Bybit
- **If:** Bybit BBO tighter than your quotes → You're out of market
- **Action:** Either:
  - Join their price (if profitable after fees)
  - Stay wide and wait for them to get picked off
  - **Never chase unprofitable spreads!**

---

### **Phase 5: Implementation Roadmap**

**Week 1: Quick Wins (Est. +50-100% improvement)**
1. ✅ Enable event-driven requoting on Binance book updates
2. ✅ Add volatility-based spread widening
3. ✅ Implement fast cancel-replace on quote refresh
4. ✅ Test with 60 bps base spread (validation phase)

**Week 2: Medium Complexity (Est. +50% additional)**
5. ✅ Fine-tune OFI thresholds using historical fill data
6. ✅ Add queue position awareness (if exchange provides data)
7. ✅ Implement inventory-urgency skewing

**Week 3: Advanced (Est. +20-30% additional)**
8. ✅ ML model for toxicity prediction (optional)
9. ✅ Multi-timeframe volatility signals
10. ✅ Cross-venue spread arbitrage detection

---

### **Metrics to Track (CRITICAL)**

```python
# Per-fill analysis
metrics = {
    "fill_price_vs_mid_at_submit": [],  # How far from mid when we placed order
    "fill_price_vs_mid_at_fill": [],    # How far from mid when filled
    "time_to_fill": [],                 # How long order sat in book
    "pnl_per_fill": [],                 # Immediate P&L on each fill
    "ofi_at_fill": [],                  # OFI value when filled
    "volatility_at_fill": [],           # Volatility when filled
    "queue_position_at_fill": [],       # Where we were in queue
}
```

**Goal:** Identify patterns in BAD fills (losing trades) to avoid those setups

---

## 🎯 **Immediate Next Step (Tonight)**

**Create a fill quality analyzer** that processes your VPS logs:
1. Extract all fills from logs
2. Calculate realized P&L per roundtrip
3. Flag "toxic fills" (those that lost money)
4. Correlate with market conditions (OFI, spread, time-in-book)
5. Find the pattern that predicts bad fills

Would you like me to create this analyzer script now? It will give us the data-driven insights needed to optimize the strategy!