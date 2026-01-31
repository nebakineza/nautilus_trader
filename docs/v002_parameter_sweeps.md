# V002 Parameter Sweeps (BTC vs ETH)

This document defines concrete, *finite* parameter grids to sweep for the v002 multi-pair market maker, plus the KPIs to rank runs using `scripts/analyze_v002_backtest.py`.

## KPIs to optimize (order matters)

1. **Spread capture (bps)** at fill time: positive and stable.
2. **Markouts (bps)** at 250ms / 1s / 5s: aim for near-zero or positive (avoid adverse selection).
3. **Fee drag (USDT)**: commissions per fill and total commissions.
4. **Inventory behavior**: low time-at-max-inventory, controlled taker IOC usage.

A strong configuration typically has:
- Spread capture bps > 0 (maker fills), and
- 1s markout bps close to 0 (or slightly positive), and
- Low adverse_pct on markouts.

## Enable markouts in the analyzer

If you have Bybit JSONL order book data (`data/ob_data/..._ob200.data`), generate a mid series:

```bash
python3 scripts/extract_mid_from_bybit_ob.py \
  --ob-file data/ob_data/BTCUSDT_Spot/2026-01-15_BTCUSDT_ob200.data \
  --output-csv outputs/backtests/backtest_results_v002/mid_btc.csv \
  --instrument-id BTCUSDT-SPOT.BYBIT \
  --min-interval-ms 50
```

Then run the analyzer with markouts:

```bash
python3 scripts/analyze_v002_backtest.py \
  --results-dir outputs/backtests/backtest_results_v002 \
  --mid-csv outputs/backtests/backtest_results_v002/mid_btc.csv \
  --markout-horizons-ms 250,1000,5000
```

For multi-pair markouts, generate one mid CSV per instrument and merge them into a single file with an `instrument_id` column, or emit both instruments in a single extractor run.

## Sweep design (two stages)

### Stage A: Coarse grid (fast triage)

Keep the grid small and interpretable (~36–72 runs). Only sweep the parameters that most strongly control:
- quote width (min/max spread),
- quote aggressiveness (OBI threshold + sensitivity), and
- inventory pressure (risk aversion + half-life).

### Stage B: Focused grid (refine winners)

Take the best ~5 configs from Stage A and refine around them with tighter steps.

## Concrete grids

### Shared (applies to both BTC and ETH)

Sweep these as a *small cartesian* (keep total runs bounded):

- `obi_levels`: **[5, 10]**
- `obi_ema_period`: **[10, 15, 20]**
- `obi_entry_threshold`: **[0.10, 0.15, 0.20]**
- `obi_exit_threshold`: **[0.02, 0.03, 0.05]**
- `risk_aversion`: **[0.25, 0.50, 1.00]**
- `inventory_half_life_seconds`: **[15, 30, 60]**
- `max_inventory_age_seconds`: **[60, 120, 240]**
- `quote_refresh_interval_ms`: **[20, 50, 100]**
- `min_requote_ticks`: **[0, 1, 2]**
- `rebalance_ioc_min_position_ratio`: **[0.80, 0.85, 0.90]**
- `rebalance_ioc_max_slippage_bps`: **[2.0, 3.0, 5.0]**

If runtime is a concern, freeze everything except:
- `obi_entry_threshold`, `risk_aversion`, `inventory_half_life_seconds`, `min_requote_ticks`.

### BTC instrument grid (Bybit spot)

- `base_qty`: **[0.00005, 0.00010, 0.00015]**
- `max_position_qty`: **[0.00030, 0.00050, 0.00080]**
- `min_spread_bps`: **[1, 2]**
- `max_spread_bps`: **[4, 6, 8]**
- `obi_sensitivity`: **[0.8, 1.0, 1.2]**

### ETH instrument grid (Bybit spot)

- `base_qty`: **[0.0025, 0.0050, 0.0075]**
- `max_position_qty`: **[0.0075, 0.0150, 0.0250]**
- `min_spread_bps`: **[1, 2]**
- `max_spread_bps`: **[6, 8, 12]**
- `obi_sensitivity`: **[0.9, 1.1, 1.3]**

### Capital weights (only if multi-pair runs)

- `BTC weight`: **[0.50, 0.60, 0.70]**
- `ETH weight`: **[1 - BTC weight]**

## Recommended run-budget presets

- **Quick (≤ 24 runs)**:
  - Fix all shared params to current defaults.
  - Sweep only per-instrument spreads + `obi_sensitivity`.

- **Balanced (≈ 48–96 runs)**:
  - Sweep shared: `obi_entry_threshold` × `risk_aversion` × `inventory_half_life_seconds`.
  - Sweep per instrument: `max_spread_bps` × `obi_sensitivity`.

- **Deep (200+ runs)**:
  - Full Stage A cartesian + Stage B refinement.
