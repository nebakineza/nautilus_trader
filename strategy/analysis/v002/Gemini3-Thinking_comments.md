This `v003` implementation is **95% correct** and represents a solid evolution to a "Smart" Market Maker.

However, you have a **Logical Bug** in the `_calculate_dynamic_size` method (you missed the SELL side), and a **Configuration Risk** regarding the liquidity thresholds for multi-symbol setups.

Here are the two fixes you need before deploying.

### 1. The Bug: One-Sided Blindness

Your code currently only checks the Leader's **Bid** wall to scale your **Buy** size. It ignores the Leader's **Ask** wall when sizing your **Sells**.

* **Current Behavior:** If Binance has a massive 100 BTC sell wall (Resistance), your bot ignores it and sells standard size. You want to *increase* sell size here because the price ceiling is strong.

**Fix:** Add the `elif` block for `OrderSide.SELL`.

```python
    def _calculate_dynamic_size(self, base_qty: Decimal, side: OrderSide) -> Decimal:
        # 1. Volatility Scalar (Same as your code)
        vol_scalar = Decimal("1.0")
        if abs(self._current_diff_bps) > self.config.vol_scalar_threshold_bps:
            vol_scalar = self.config.vol_scalar_reduction

        # 2. Liquidity Scalar (FIXED)
        depth_scalar = Decimal("1.0")
        
        if self.leader_book is not None:
            # --- BUY SIDE: Check Leader BIDS (Support) ---
            if side == OrderSide.BUY:
                best_bid_size = self.leader_book.best_bid_size()
                if best_bid_size is not None:
                    qty = best_bid_size.as_decimal()
                    if qty > self.config.liquidity_high_qty:
                        depth_scalar = self.config.liquidity_high_scalar
                    elif qty < self.config.liquidity_low_qty:
                        depth_scalar = self.config.liquidity_low_scalar
            
            # --- SELL SIDE: Check Leader ASKS (Resistance) ---
            elif side == OrderSide.SELL:
                best_ask_size = self.leader_book.best_ask_size()
                if best_ask_size is not None:
                    qty = best_ask_size.as_decimal()
                    if qty > self.config.liquidity_high_qty:
                        depth_scalar = self.config.liquidity_high_scalar
                    elif qty < self.config.liquidity_low_qty:
                        depth_scalar = self.config.liquidity_low_scalar

        # 3. Final Calculation
        final_size = base_qty * vol_scalar * depth_scalar
        final_size = min(final_size, self.config.max_order_qty)
        final_size = max(final_size, self.config.min_order_qty)
        return final_size

```

### 2. The Config Risk: "5.0" is not Universal

You set `liquidity_high_qty = 5.0` as a default in the Config Class.

* **BTC:** 5.0 BTC is ~$500k. (A Whale Wall). **Correct.**
* **ETH:** 5.0 ETH is ~$15k. (Noise). **Incorrect.**
* **SOL:** 5.0 SOL is ~$1k. (Dust). **Incorrect.**

If you run this code for SOL, the bot will see "5 SOL" (which is tiny) and think "Wow, a Whale!" and triple your size.

**Fix:** You must pass these values dynamically in `run_multi.py`, just like you do for `order_qty`.

**In `LeadLagMMv3Config`:**
(Keep your defaults, but ensure you override them).

**In `run_multi.py` (Update the `pairs` list):**

```python
pairs = [
    {
        "symbol": "BTCUSDT",
        # ... existing ...
        "liquidity_high_qty": Decimal("5.0"),  # $500k
        "liquidity_low_qty": Decimal("0.1"),
    },
    {
        "symbol": "ETHUSDT",
        # ... existing ...
        "liquidity_high_qty": Decimal("80.0"), # ~$240k
        "liquidity_low_qty": Decimal("5.0"),
    },
    {
        "symbol": "SOLUSDT",
        # ... existing ...
        "liquidity_high_qty": Decimal("1000.0"), # ~$150k
        "liquidity_low_qty": Decimal("50.0"),
    },
]

# And pass them into the config in the loop:
config_strategy = LeadLagMMv3Config(
    # ...
    liquidity_high_qty=pair["liquidity_high_qty"],
    liquidity_low_qty=pair["liquidity_low_qty"],
    # ...
)

```

### Final Verdict

With those two adjustments (the Sell-side check and the per-symbol thresholds), `v3` is ready.

**Do you want me to generate the full `run_multi_v3.py` script with these specific liquidity thresholds applied?**