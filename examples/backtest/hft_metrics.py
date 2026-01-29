"""Post-backtest metrics and reporting helpers.

Compute Sharpe, Fill Ratio, Markout and Inventory Duration from a finished
`BacktestEngine` instance. These helpers expect the engine to expose
`get_report()`, `get_fills()`, and `get_positions()` or similar methods.
"""

import math
import numpy as np


def sharpe_ratio(returns, risk_free=0.0):
    arr = np.array(returns)
    if arr.size < 2:
        return float('nan')
    excess = arr - risk_free
    return float(np.mean(excess) / (np.std(excess, ddof=1) + 1e-12) * math.sqrt(252))


def fill_ratio(engine):
    try:
        report = engine.get_report()
        orders_submitted = report.get('orders_submitted', None)
        orders_filled = report.get('orders_filled', None)
        if orders_submitted is None or orders_submitted == 0:
            return 0.0
        return float(orders_filled) / float(orders_submitted)
    except Exception:
        # Fallback: compute from fills list
        fills = engine.get_fills()
        submitted = engine.get_submitted_orders_count() if hasattr(engine, 'get_submitted_orders_count') else None
        if submitted:
            return float(len(fills)) / float(submitted)
        return float(len(fills))


def markout_analysis(engine, window_ms=100):
    """Compute simple markout: average price movement after fills within `window_ms`."""
    results = []
    fills = engine.get_fills()
    for f in fills:
        fill_ts = getattr(f, 'timestamp', None)
        if fill_ts is None:
            continue
        # naive: ask engine for price at fill_ts + window
        later_price = engine.get_price_at(fill_ts + window_ms / 1000.0)
        if later_price is None:
            continue
        markout = (later_price - float(f.price)) if getattr(f, 'side', None) == 'BUY' else (float(f.price) - later_price)
        results.append(markout)
    return {
        'count': len(results),
        'avg_markout': float(np.mean(results)) if results else None,
        'median_markout': float(np.median(results)) if results else None,
    }


def inventory_duration(engine):
    """Estimate how long inventory was off-zero (seconds) and average duration per event."""
    positions = engine.get_positions()
    # positions timeline expected; if not available, return None
    if not positions:
        return None
    durations = []
    for p in positions:
        durations.append(getattr(p, 'duration_seconds', 0))
    return {
        'events': len(durations),
        'total_seconds': sum(durations),
        'avg_seconds': float(np.mean(durations)) if durations else 0.0
    }
