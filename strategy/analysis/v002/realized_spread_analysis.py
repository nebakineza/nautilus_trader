#!/usr/bin/env python3
"""Compute matched-pair realized spreads from fills_report.csv using FIFO matching."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median


def _parse_float(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


def _pearson_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs)
    den_y = sum((y - mean_y) ** 2 for y in ys)
    den = (den_x * den_y) ** 0.5
    if den == 0:
        return None
    return num / den


def _match_fifo(entries: deque, price: float, qty: float):
    """Match qty against FIFO entries. Returns list of (entry_price, match_qty, entry_ts)."""
    matches = []
    remaining = qty
    while remaining > 0 and entries:
        entry_price, entry_qty, entry_ts = entries[0]
        match_qty = min(remaining, entry_qty)
        matches.append((entry_price, match_qty, entry_ts))
        remaining -= match_qty
        entry_qty -= match_qty
        if entry_qty <= 0:
            entries.popleft()
        else:
            entries[0] = (entry_price, entry_qty, entry_ts)
    return matches, remaining


def analyze_fills(fills_path: Path) -> dict:
    buys = defaultdict(deque)   # instrument_id -> deque[(price, qty, ts)]
    sells = defaultdict(deque)  # instrument_id -> deque[(price, qty, ts)]
    matched = []

    with fills_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            instrument = row.get("instrument_id") or "UNKNOWN"
            side = (row.get("side") or "").upper()
            price = _parse_float(row.get("price") or "0")
            qty = _parse_float(row.get("quantity") or row.get("filled_qty") or "0")
            ts = _parse_ts(row.get("ts_last") or row.get("ts_init") or "")
            if qty <= 0 or price <= 0:
                continue

            if side == "BUY":
                # Match against open sells (short cover)
                matches, remaining = _match_fifo(sells[instrument], price, qty)
                for entry_price, match_qty, entry_ts in matches:
                    duration_s = (ts - entry_ts).total_seconds() if (ts and entry_ts) else None
                    spread_bps = (entry_price - price) / entry_price * 1e4
                    matched.append({
                        "instrument_id": instrument,
                        "direction": "short_cover",
                        "entry_price": entry_price,
                        "exit_price": price,
                        "qty": match_qty,
                        "entry_time": entry_ts.isoformat() if entry_ts else None,
                        "exit_time": ts.isoformat() if ts else None,
                        "duration_s": duration_s,
                        "spread_bps": spread_bps,
                    })
                if remaining > 0:
                    buys[instrument].append((price, remaining, ts))
            elif side == "SELL":
                # Match against open buys (long exit)
                matches, remaining = _match_fifo(buys[instrument], price, qty)
                for entry_price, match_qty, entry_ts in matches:
                    duration_s = (ts - entry_ts).total_seconds() if (ts and entry_ts) else None
                    spread_bps = (price - entry_price) / entry_price * 1e4
                    matched.append({
                        "instrument_id": instrument,
                        "direction": "long_exit",
                        "entry_price": entry_price,
                        "exit_price": price,
                        "qty": match_qty,
                        "entry_time": entry_ts.isoformat() if entry_ts else None,
                        "exit_time": ts.isoformat() if ts else None,
                        "duration_s": duration_s,
                        "spread_bps": spread_bps,
                    })
                if remaining > 0:
                    sells[instrument].append((price, remaining, ts))

    durations = [m["duration_s"] for m in matched if m["duration_s"] is not None]
    spreads = [m["spread_bps"] for m in matched if m["duration_s"] is not None]
    summary = {
        "matched_count": len(matched),
        "matched_qty": sum(item["qty"] for item in matched),
        "spread_bps_mean": mean([m["spread_bps"] for m in matched]) if matched else None,
        "spread_bps_median": median([m["spread_bps"] for m in matched]) if matched else None,
        "spread_bps_min": min([m["spread_bps"] for m in matched]) if matched else None,
        "spread_bps_max": max([m["spread_bps"] for m in matched]) if matched else None,
        "duration_s_mean": mean(durations) if durations else None,
        "duration_s_median": median(durations) if durations else None,
        "spread_duration_corr": _pearson_corr(durations, spreads),
    }

    return {
        "fills_path": str(fills_path),
        "summary": summary,
        "matched": matched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute matched-pair realized spreads from fills_report.csv")
    parser.add_argument("--fills", required=True, help="Path to fills_report.csv")
    parser.add_argument("--out", help="Path to write JSON summary (default: <fills>_matched_spread.json)")
    args = parser.parse_args()

    fills_path = Path(args.fills)
    if not fills_path.exists():
        raise SystemExit(f"Fills file not found: {fills_path}")

    result = analyze_fills(fills_path)

    out_path = Path(args.out) if args.out else fills_path.with_name(fills_path.stem + "_matched_spread.json")
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
