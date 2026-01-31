#!/usr/bin/env python3
"""Analyze Nautilus backtest CSV outputs (v002 strategy oriented).

This script is intentionally dependency-free (stdlib only), so it can run in
minimal environments.

Primary use:
  - Summarize fills/fees/position lifecycle
  - Provide market-making oriented KPIs (fill mix, order lifetimes, fee drag)

Optional markouts:
    - Provide a mid-price time series aligned to fill timestamps via `--mid-csv`.
    - The mid CSV can contain either `mid` or `best_bid` + `best_ask` columns.

Tip:
    - If you're using Bybit JSONL order book data in `data/ob_data/`, you can generate
        a compatible mid CSV with `scripts/extract_mid_from_bybit_ob.py`.

Example:
    python3 scripts/analyze_v002_backtest.py --results-dir outputs/backtests/backtest_results_v002
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


_MONEY_RE = re.compile(r"(-?[0-9]*\.?[0-9]+)")


def _parse_float_from_any(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    m = _MONEY_RE.search(s)
    return float(m.group(1)) if m else None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Example: 2026-01-15 00:00:01.250000+00:00
    # `datetime.fromisoformat` accepts this format.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _read_account_report_rows(path: Path) -> list[dict[str, str]]:
    """Read account report rows robustly.

    Some account report CSVs may contain embedded newlines in later columns (e.g.
    a margins column rendered as `[` newline `]`). Rather than relying on strict
    CSV parsing, we only parse the first 5 columns:

      timestamp,total,locked,free,currency
    """
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or not line[0].isdigit():
                continue
            parts = line.split(",", 5)
            if len(parts) < 5:
                continue
            ts, total, locked, free, currency = parts[:5]
            rows.append(
                {
                    "timestamp": ts,
                    "total": total,
                    "locked": locked,
                    "free": free,
                    "currency": currency,
                }
            )
    return rows


def _safe_div(n: float, d: float) -> float:
    return n / d if d else 0.0


@dataclass(frozen=True)
class FillSummary:
    fills: int
    maker_fills: int
    taker_fills: int
    buy_fills: int
    sell_fills: int
    commission_total_usdt: float
    commission_per_fill_usdt: float
    avg_order_lifetime_s: Optional[float]
    median_order_lifetime_s: Optional[float]
    avg_inter_fill_gap_s: Optional[float]
    instruments: dict[str, int]


@dataclass(frozen=True)
class PositionSummary:
    positions: int
    realized_pnl_total_usdt: float
    avg_duration_s: Optional[float]
    median_duration_s: Optional[float]
    max_duration_s: Optional[float]
    instruments: dict[str, int]


@dataclass(frozen=True)
class AccountSnapshot:
    timestamp: str
    currency: str
    total: float
    locked: float
    free: float


@dataclass(frozen=True)
class ReportSummary:
    results_dir: str
    fill_summary: FillSummary
    position_summary: PositionSummary
    last_balances: list[AccountSnapshot]
    equity_summary: Optional[dict[str, Any]] = None
    markout_summary: Optional["MarkoutSummary"] = None


def first_balances(rows: list[dict[str, str]]) -> list[AccountSnapshot]:
    first_by_ccy: dict[str, tuple[datetime, dict[str, str]]] = {}

    for r in rows:
        ts_raw = r.get("timestamp")
        ts = _parse_ts(ts_raw)
        if not ts:
            continue

        ccy = (r.get("currency") or "").strip()
        if not ccy:
            continue

        prev = first_by_ccy.get(ccy)
        if prev is None or ts < prev[0]:
            first_by_ccy[ccy] = (ts, r)

    snaps: list[AccountSnapshot] = []
    for ccy, (ts, r) in sorted(first_by_ccy.items()):
        def _to_float(v: Any) -> float:
            try:
                return float(v)
            except Exception:
                return float(_parse_float_from_any(v) or 0.0)

        snaps.append(
            AccountSnapshot(
                timestamp=ts.isoformat().replace("+00:00", "Z"),
                currency=ccy,
                total=_to_float(r.get("total") or 0.0),
                locked=_to_float(r.get("locked") or 0.0),
                free=_to_float(r.get("free") or 0.0),
            )
        )

    return snaps


def _infer_base_ccy_from_instrument_id(instrument_id: str) -> Optional[str]:
    # Handles formats like BTCUSDT-SPOT.BYBIT, ETHUSDT-SPOT.BYBIT
    m = re.match(r"^([A-Z0-9]+)USDT", instrument_id)
    return m.group(1) if m else None


def _compute_spot_equity_summary(
    *,
    account_rows: list[dict[str, str]],
    fills: list[dict[str, str]],
    mid_series: dict[str, tuple[list[float], list[float]]],
    tolerance_s: float,
) -> Optional[dict[str, Any]]:
    if not account_rows:
        return None

    first = first_balances(account_rows)
    last = last_balances(account_rows)

    first_by_ccy = {b.currency: b for b in first}
    last_by_ccy = {b.currency: b for b in last}

    # Choose an instrument key for mid lookup.
    instruments_in_fills = sorted({(r.get("instrument_id") or "").strip() for r in fills if (r.get("instrument_id") or "").strip()})
    preferred_keys: list[str] = []
    preferred_keys.extend([k for k in instruments_in_fills if k in mid_series])
    preferred_keys.extend([k for k in mid_series.keys() if k != "__SINGLE__"])
    if "__SINGLE__" in mid_series:
        preferred_keys.append("__SINGLE__")
    preferred_keys = [k for i, k in enumerate(preferred_keys) if k and k not in preferred_keys[:i]]
    if not preferred_keys:
        return None

    # Helper to fetch a mid at a timestamp for a given series key.
    def _mid_at(series_key: str, ts: datetime) -> Optional[float]:
        ts_s, mids = mid_series.get(series_key, ([], []))
        return _nearest_mid(ts_s, mids, ts.timestamp(), tolerance_s)

    # Build a mapping currency -> series_key.
    ccy_to_series: dict[str, str] = {}
    non_usdt_ccys = sorted([ccy for ccy in last_by_ccy.keys() if ccy and ccy != "USDT"])

    if len(mid_series) == 1 and "__SINGLE__" in mid_series and len(non_usdt_ccys) == 1:
        ccy_to_series[non_usdt_ccys[0]] = "__SINGLE__"
    else:
        for key in mid_series.keys():
            if key == "__SINGLE__":
                continue
            base = _infer_base_ccy_from_instrument_id(key)
            if base:
                ccy_to_series[base] = key

    # Determine first/last timestamps (use USDT row if available, else any row).
    def _snap_ts(snap: AccountSnapshot) -> Optional[datetime]:
        return _parse_ts(snap.timestamp)

    ts_first = _snap_ts(first_by_ccy.get("USDT") or next(iter(first_by_ccy.values())))
    ts_last = _snap_ts(last_by_ccy.get("USDT") or next(iter(last_by_ccy.values())))
    if not ts_first or not ts_last:
        return None

    # Compute equity at a given snapshot.
    def _equity_usdt(snap_by_ccy: dict[str, AccountSnapshot], ts: datetime) -> Optional[float]:
        usdt = float(snap_by_ccy.get("USDT").total) if snap_by_ccy.get("USDT") else 0.0
        assets_value = 0.0
        assets_detail: dict[str, float] = {}

        for ccy, snap in snap_by_ccy.items():
            if ccy == "USDT":
                continue
            series_key = ccy_to_series.get(ccy)
            if not series_key:
                continue
            mid = _mid_at(series_key, ts)
            if mid is None:
                continue
            val = float(snap.total) * float(mid)
            assets_value += val
            assets_detail[ccy] = val

        equity = usdt + assets_value
        return equity

    eq_first = _equity_usdt(first_by_ccy, ts_first)
    eq_last = _equity_usdt(last_by_ccy, ts_last)
    if eq_first is None or eq_last is None or not math.isfinite(eq_first) or not math.isfinite(eq_last):
        return None

    pnl = eq_last - eq_first
    pnl_pct = (pnl / eq_first) if eq_first else None

    # Also include end-of-run asset values at ts_last.
    assets_end: dict[str, float] = {}
    for ccy, snap in last_by_ccy.items():
        if ccy == "USDT":
            continue
        series_key = ccy_to_series.get(ccy)
        if not series_key:
            continue
        mid = _mid_at(series_key, ts_last)
        if mid is None:
            continue
        assets_end[ccy] = float(snap.total) * float(mid)

    return {
        "equity_start_usdt": eq_first,
        "equity_end_usdt": eq_last,
        "equity_pnl_usdt": pnl,
        "equity_pnl_pct": pnl_pct,
        "assets_value_end_usdt": assets_end,
    }


@dataclass(frozen=True)
class MarkoutHorizonStats:
    horizon_ms: int
    fills: int
    maker_fills: int
    taker_fills: int
    mean_bps: Optional[float]
    median_bps: Optional[float]
    p10_bps: Optional[float]
    p90_bps: Optional[float]
    qty_weighted_mean_bps: Optional[float]
    mean_pnl_usdt: Optional[float]
    adverse_pct: Optional[float]


@dataclass(frozen=True)
class SpreadCaptureStats:
    fills: int
    maker_fills: int
    taker_fills: int
    mean_bps: Optional[float]
    median_bps: Optional[float]
    p10_bps: Optional[float]
    p90_bps: Optional[float]
    qty_weighted_mean_bps: Optional[float]


@dataclass(frozen=True)
class MarkoutSummary:
    mid_csv: str
    markout_horizons_ms: list[int]
    fill_mid_tolerance_ms: int
    spread_capture: SpreadCaptureStats
    horizons: list[MarkoutHorizonStats]
    instrument_breakdown: dict[str, dict[str, dict[str, float]]]


def _parse_int_list_csv(value: str) -> list[int]:
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            continue
    return [x for x in out if x > 0]


def _mid_series_from_csv(
    path: Path,
    ts_col: str = "timestamp",
    instrument_col: str = "instrument_id",
    mid_col: str = "mid",
    bid_col: str = "best_bid",
    ask_col: str = "best_ask",
) -> dict[str, tuple[list[float], list[float]]]:
    """Read a mid series CSV.

    Returns mapping: instrument_id -> (ts_seconds_sorted, mid_sorted)
    """
    rows = _read_csv_rows(path)
    series: dict[str, list[tuple[float, float]]] = {}

    for r in rows:
        ts = _parse_ts(r.get(ts_col))
        if not ts:
            continue
        instrument = (r.get(instrument_col) or "").strip()
        if not instrument:
            instrument = "__SINGLE__"

        mid = _parse_float_from_any(r.get(mid_col))
        if mid is None:
            bid = _parse_float_from_any(r.get(bid_col))
            ask = _parse_float_from_any(r.get(ask_col))
            if bid is None or ask is None:
                continue
            mid = (bid + ask) / 2.0
        if not math.isfinite(mid) or mid <= 0:
            continue

        series.setdefault(instrument, []).append((ts.timestamp(), float(mid)))

    out: dict[str, tuple[list[float], list[float]]] = {}
    for instrument, points in series.items():
        points_sorted = sorted(points, key=lambda x: x[0])
        ts_s = [p[0] for p in points_sorted]
        mids = [p[1] for p in points_sorted]
        out[instrument] = (ts_s, mids)
    return out


def _nearest_mid(
    ts_s: list[float],
    mids: list[float],
    target_s: float,
    tolerance_s: float,
) -> Optional[float]:
    if not ts_s:
        return None
    i = bisect.bisect_left(ts_s, target_s)
    best_mid: Optional[float] = None
    best_dt = float("inf")
    for j in (i - 1, i):
        if 0 <= j < len(ts_s):
            dt = abs(ts_s[j] - target_s)
            if dt < best_dt:
                best_dt = dt
                best_mid = mids[j]
    return best_mid if best_dt <= tolerance_s else None


def _mid_at_or_after(ts_s: list[float], mids: list[float], target_s: float) -> Optional[float]:
    if not ts_s:
        return None
    i = bisect.bisect_left(ts_s, target_s)
    if i >= len(ts_s):
        return None
    return mids[i]


def _percentile(sorted_values: list[float], p: float) -> Optional[float]:
    if not sorted_values:
        return None
    if p <= 0:
        return sorted_values[0]
    if p >= 100:
        return sorted_values[-1]
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    d0 = sorted_values[int(f)] * (c - k)
    d1 = sorted_values[int(c)] * (k - f)
    return d0 + d1


def compute_markouts(
    fills: list[dict[str, str]],
    mid_series: dict[str, tuple[list[float], list[float]]],
    horizons_ms: list[int],
    fill_mid_tolerance_ms: int,
    mid_csv: str,
) -> Optional[MarkoutSummary]:
    if not horizons_ms:
        return None

    tol_s = fill_mid_tolerance_ms / 1000.0

    capture_bps: list[float] = []
    capture_qty: list[float] = []
    capture_is_maker: list[bool] = []

    horizon_bps: dict[int, list[float]] = {h: [] for h in horizons_ms}
    horizon_qty: dict[int, list[float]] = {h: [] for h in horizons_ms}
    horizon_pnl: dict[int, list[float]] = {h: [] for h in horizons_ms}
    horizon_is_maker: dict[int, list[bool]] = {h: [] for h in horizons_ms}

    # Instrument breakdown: inst -> horizon_ms -> {count, mean_bps, qty_w_mean_bps}
    breakdown: dict[str, dict[int, list[tuple[float, float]]]] = {}

    for r in fills:
        inst = (r.get("instrument_id") or "").strip()
        if not inst:
            inst = "__UNKNOWN__"

        series = mid_series.get(inst) or mid_series.get("__SINGLE__")
        if not series:
            continue
        ts_s, mids = series

        ts_last = _parse_ts(r.get("ts_last"))
        if not ts_last:
            continue
        t_fill = ts_last.timestamp()

        fill_px = _parse_float_from_any(r.get("avg_px"))
        if fill_px is None:
            fill_px = _parse_float_from_any(r.get("price"))
        if fill_px is None or fill_px <= 0:
            continue

        qty = _parse_float_from_any(r.get("quantity"))
        if qty is None or qty <= 0:
            continue

        side = (r.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        side_mult = 1.0 if side == "BUY" else -1.0

        liq = (r.get("liquidity_side") or "").upper()
        is_maker = liq == "MAKER"

        mid_fill = _nearest_mid(ts_s, mids, t_fill, tol_s)
        if mid_fill is not None and mid_fill > 0:
            cap = side_mult * (mid_fill - float(fill_px)) / float(mid_fill) * 1e4
            capture_bps.append(cap)
            capture_qty.append(qty)
            capture_is_maker.append(is_maker)

        for h in horizons_ms:
            mid_fut = _mid_at_or_after(ts_s, mids, t_fill + (h / 1000.0))
            if mid_fut is None or mid_fut <= 0:
                continue

            bps = side_mult * (float(mid_fut) - float(fill_px)) / float(fill_px) * 1e4
            pnl = side_mult * (float(mid_fut) - float(fill_px)) * float(qty)

            horizon_bps[h].append(bps)
            horizon_qty[h].append(qty)
            horizon_pnl[h].append(pnl)
            horizon_is_maker[h].append(is_maker)

            breakdown.setdefault(inst, {}).setdefault(h, []).append((bps, qty))

    def _capture_stats(values: list[float], qtys: list[float], makers: list[bool]) -> SpreadCaptureStats:
        values_sorted = sorted(values)
        if not values_sorted:
            return SpreadCaptureStats(
                fills=0,
                maker_fills=0,
                taker_fills=0,
                mean_bps=None,
                median_bps=None,
                p10_bps=None,
                p90_bps=None,
                qty_weighted_mean_bps=None,
            )
        mean = statistics.mean(values_sorted)
        med = statistics.median(values_sorted)
        p10 = _percentile(values_sorted, 10)
        p90 = _percentile(values_sorted, 90)
        w = sum(qtys)
        wmean = sum(v * q for v, q in zip(values, qtys)) / w if w else None
        maker_n = sum(1 for m in makers if m)
        return SpreadCaptureStats(
            fills=len(values_sorted),
            maker_fills=maker_n,
            taker_fills=len(values_sorted) - maker_n,
            mean_bps=float(mean),
            median_bps=float(med),
            p10_bps=float(p10) if p10 is not None else None,
            p90_bps=float(p90) if p90 is not None else None,
            qty_weighted_mean_bps=float(wmean) if wmean is not None else None,
        )

    capture_stats = _capture_stats(capture_bps, capture_qty, capture_is_maker)

    horizons_out: list[MarkoutHorizonStats] = []
    for h in horizons_ms:
        vals = horizon_bps[h]
        vals_sorted = sorted(vals)
        makers = horizon_is_maker[h]
        qtys = horizon_qty[h]
        pnls = horizon_pnl[h]

        if not vals_sorted:
            horizons_out.append(
                MarkoutHorizonStats(
                    horizon_ms=h,
                    fills=0,
                    maker_fills=0,
                    taker_fills=0,
                    mean_bps=None,
                    median_bps=None,
                    p10_bps=None,
                    p90_bps=None,
                    qty_weighted_mean_bps=None,
                    mean_pnl_usdt=None,
                    adverse_pct=None,
                )
            )
            continue

        mean = statistics.mean(vals_sorted)
        med = statistics.median(vals_sorted)
        p10 = _percentile(vals_sorted, 10)
        p90 = _percentile(vals_sorted, 90)

        w = sum(qtys)
        wmean = sum(v * q for v, q in zip(vals, qtys)) / w if w else None
        maker_n = sum(1 for m in makers if m)
        adverse = sum(1 for v in vals if v < 0) / len(vals) * 100.0 if vals else None
        mean_pnl = statistics.mean(pnls) if pnls else None

        horizons_out.append(
            MarkoutHorizonStats(
                horizon_ms=h,
                fills=len(vals),
                maker_fills=maker_n,
                taker_fills=len(vals) - maker_n,
                mean_bps=float(mean),
                median_bps=float(med),
                p10_bps=float(p10) if p10 is not None else None,
                p90_bps=float(p90) if p90 is not None else None,
                qty_weighted_mean_bps=float(wmean) if wmean is not None else None,
                mean_pnl_usdt=float(mean_pnl) if mean_pnl is not None else None,
                adverse_pct=float(adverse) if adverse is not None else None,
            )
        )

    breakdown_out: dict[str, dict[str, dict[str, float]]] = {}
    for inst, by_h in breakdown.items():
        inst_out: dict[str, dict[str, float]] = {}
        for h, pairs in by_h.items():
            bps_list = [p[0] for p in pairs]
            qty_list = [p[1] for p in pairs]
            mean = statistics.mean(bps_list) if bps_list else 0.0
            w = sum(qty_list)
            wmean = sum(b * q for b, q in pairs) / w if w else mean
            inst_out[str(h)] = {
                "count": float(len(pairs)),
                "mean_bps": float(mean),
                "qty_weighted_mean_bps": float(wmean),
            }
        breakdown_out[inst] = inst_out

    return MarkoutSummary(
        mid_csv=mid_csv,
        markout_horizons_ms=horizons_ms,
        fill_mid_tolerance_ms=fill_mid_tolerance_ms,
        spread_capture=capture_stats,
        horizons=horizons_out,
        instrument_breakdown=breakdown_out,
    )


def summarize_fills(rows: list[dict[str, str]]) -> FillSummary:
    maker = 0
    taker = 0
    buy = 0
    sell = 0
    commissions = 0.0
    lifetimes: list[float] = []
    fill_times: list[datetime] = []
    instruments: dict[str, int] = {}

    for r in rows:
        instruments[r.get("instrument_id", "")] = instruments.get(r.get("instrument_id", ""), 0) + 1

        side = (r.get("side") or "").upper()
        if side == "BUY":
            buy += 1
        elif side == "SELL":
            sell += 1

        liq = (r.get("liquidity_side") or "").upper()
        if liq == "MAKER":
            maker += 1
        elif liq == "TAKER":
            taker += 1

        commissions += _parse_float_from_any(r.get("commissions")) or 0.0

        ts_init = _parse_ts(r.get("ts_init"))
        ts_last = _parse_ts(r.get("ts_last"))
        if ts_init and ts_last:
            lifetimes.append((ts_last - ts_init).total_seconds())
            fill_times.append(ts_last)

    lifetimes_sorted = sorted(lifetimes)
    avg_life = statistics.mean(lifetimes_sorted) if lifetimes_sorted else None
    med_life = statistics.median(lifetimes_sorted) if lifetimes_sorted else None

    gaps: list[float] = []
    fill_times_sorted = sorted(fill_times)
    for i in range(1, len(fill_times_sorted)):
        gaps.append((fill_times_sorted[i] - fill_times_sorted[i - 1]).total_seconds())

    avg_gap = statistics.mean(gaps) if gaps else None

    return FillSummary(
        fills=len(rows),
        maker_fills=maker,
        taker_fills=taker,
        buy_fills=buy,
        sell_fills=sell,
        commission_total_usdt=commissions,
        commission_per_fill_usdt=_safe_div(commissions, float(len(rows))),
        avg_order_lifetime_s=avg_life,
        median_order_lifetime_s=med_life,
        avg_inter_fill_gap_s=avg_gap,
        instruments={k: v for k, v in instruments.items() if k},
    )


def summarize_positions(rows: list[dict[str, str]]) -> PositionSummary:
    pnl_total = 0.0
    durations_s: list[float] = []
    instruments: dict[str, int] = {}

    for r in rows:
        instruments[r.get("instrument_id", "")] = instruments.get(r.get("instrument_id", ""), 0) + 1
        pnl_total += _parse_float_from_any(r.get("realized_pnl")) or 0.0
        dur_ns = _parse_float_from_any(r.get("duration_ns"))
        if dur_ns is not None:
            durations_s.append(dur_ns / 1e9)

    durations_s_sorted = sorted(durations_s)
    return PositionSummary(
        positions=len(rows),
        realized_pnl_total_usdt=pnl_total,
        avg_duration_s=statistics.mean(durations_s_sorted) if durations_s_sorted else None,
        median_duration_s=statistics.median(durations_s_sorted) if durations_s_sorted else None,
        max_duration_s=max(durations_s_sorted) if durations_s_sorted else None,
        instruments={k: v for k, v in instruments.items() if k},
    )


def last_balances(rows: list[dict[str, str]]) -> list[AccountSnapshot]:
    last_by_ccy: dict[str, tuple[datetime, dict[str, str]]] = {}

    for r in rows:
        ts_raw = r.get("timestamp")
        ts = _parse_ts(ts_raw)
        if not ts:
            continue

        ccy = (r.get("currency") or "").strip()
        if not ccy:
            continue

        prev = last_by_ccy.get(ccy)
        if prev is None or ts > prev[0]:
            last_by_ccy[ccy] = (ts, r)

    snaps: list[AccountSnapshot] = []
    for ccy, (ts, r) in sorted(last_by_ccy.items()):
        def _to_float(v: Any) -> float:
            try:
                return float(v)
            except Exception:
                return float(_parse_float_from_any(v) or 0.0)

        snaps.append(
            AccountSnapshot(
                timestamp=ts.isoformat().replace("+00:00", "Z"),
                currency=ccy,
                total=_to_float(r.get("total") or 0.0),
                locked=_to_float(r.get("locked") or 0.0),
                free=_to_float(r.get("free") or 0.0),
            )
        )

    return snaps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=str, required=True)
    ap.add_argument("--output-json", type=str, default=None)
    ap.add_argument("--mid-csv", type=str, default=None)
    ap.add_argument("--markout-horizons-ms", type=str, default="250,1000,5000")
    ap.add_argument("--fill-mid-tolerance-ms", type=int, default=2000)
    ap.add_argument("--mid-ts-col", type=str, default="timestamp")
    ap.add_argument("--mid-instrument-col", type=str, default="instrument_id")
    ap.add_argument("--mid-mid-col", type=str, default="mid")
    ap.add_argument("--mid-bid-col", type=str, default="best_bid")
    ap.add_argument("--mid-ask-col", type=str, default="best_ask")
    args = ap.parse_args()

    results_dir = Path(args.results_dir)
    fills_path = results_dir / "fills_report.csv"
    positions_path = results_dir / "positions_report.csv"
    account_path = results_dir / "account_report.csv"

    missing = [p for p in (fills_path, positions_path, account_path) if not p.exists()]
    if missing:
        raise SystemExit(f"Missing required report(s): {', '.join(str(p) for p in missing)}")

    fills = _read_csv_rows(fills_path)
    positions = _read_csv_rows(positions_path)
    account = _read_account_report_rows(account_path)

    summary = ReportSummary(
        results_dir=str(results_dir),
        fill_summary=summarize_fills(fills),
        position_summary=summarize_positions(positions),
        last_balances=last_balances(account),
    )

    mid_path: Optional[Path] = None
    if args.mid_csv:
        mid_path = Path(args.mid_csv)
        if not mid_path.exists():
            raise SystemExit(f"Mid CSV not found: {mid_path}")
    else:
        default_mid = results_dir / "mid.csv"
        if default_mid.exists():
            mid_path = default_mid
        else:
            candidates = sorted(results_dir.glob("mid*.csv"))
            if candidates:
                mid_path = candidates[0]

    if mid_path is not None:
        horizons = _parse_int_list_csv(args.markout_horizons_ms)
        mid_series = _mid_series_from_csv(
            mid_path,
            ts_col=args.mid_ts_col,
            instrument_col=args.mid_instrument_col,
            mid_col=args.mid_mid_col,
            bid_col=args.mid_bid_col,
            ask_col=args.mid_ask_col,
        )
        equity_summary = _compute_spot_equity_summary(
            account_rows=account,
            fills=fills,
            mid_series=mid_series,
            tolerance_s=float(int(args.fill_mid_tolerance_ms)) / 1000.0,
        )
        markout_summary = None
        if horizons:
            markout_summary = compute_markouts(
                fills=fills,
                mid_series=mid_series,
                horizons_ms=horizons,
                fill_mid_tolerance_ms=int(args.fill_mid_tolerance_ms),
                mid_csv=str(mid_path),
            )

        summary = ReportSummary(
            results_dir=summary.results_dir,
            fill_summary=summary.fill_summary,
            position_summary=summary.position_summary,
            last_balances=summary.last_balances,
            equity_summary=equity_summary,
            markout_summary=markout_summary,
        )

    print("=" * 80)
    print("BACKTEST REPORT SUMMARY")
    print("=" * 80)
    print(f"Results dir: {summary.results_dir}")
    print("\nFills:")
    fs = summary.fill_summary
    print(f"  fills={fs.fills} maker={fs.maker_fills} taker={fs.taker_fills} buy={fs.buy_fills} sell={fs.sell_fills}")
    print(f"  commission_total_usdt={fs.commission_total_usdt:.8f} commission_per_fill_usdt={fs.commission_per_fill_usdt:.8f}")
    if fs.avg_order_lifetime_s is not None:
        print(f"  avg_order_lifetime_s={fs.avg_order_lifetime_s:.2f} median_order_lifetime_s={fs.median_order_lifetime_s:.2f}")
    if fs.avg_inter_fill_gap_s is not None:
        print(f"  avg_inter_fill_gap_s={fs.avg_inter_fill_gap_s:.2f}")
    if fs.instruments:
        print(f"  instruments={fs.instruments}")

    ps = summary.position_summary
    print("\nPositions:")
    print(f"  positions={ps.positions} realized_pnl_total_usdt={ps.realized_pnl_total_usdt:.8f}")
    if ps.avg_duration_s is not None:
        print(f"  avg_duration_s={ps.avg_duration_s:.2f} median_duration_s={ps.median_duration_s:.2f} max_duration_s={ps.max_duration_s:.2f}")
    if ps.instruments:
        print(f"  instruments={ps.instruments}")

    print("\nLast balances:")
    for b in summary.last_balances:
        print(f"  {b.currency}: total={b.total:.8f} locked={b.locked:.8f} free={b.free:.8f} @ {b.timestamp}")

    if summary.equity_summary is not None:
        es = summary.equity_summary
        print("\nSpot equity (mark-to-market):")
        print(
            "  "
            f"equity_start_usdt={es['equity_start_usdt']:.8f} "
            f"equity_end_usdt={es['equity_end_usdt']:.8f} "
            f"equity_pnl_usdt={es['equity_pnl_usdt']:.8f} "
            f"equity_pnl_pct={(es['equity_pnl_pct'] * 100.0) if es.get('equity_pnl_pct') is not None else float('nan'):.4f}%"
        )
        assets_end = es.get("assets_value_end_usdt") or {}
        if assets_end:
            assets_str = ", ".join(f"{k}={v:.8f}" for k, v in sorted(assets_end.items()))
            print(f"  assets_value_end_usdt: {assets_str}")

    if summary.markout_summary is not None:
        ms = summary.markout_summary
        print("\nMarkouts:")
        sc = ms.spread_capture
        if sc.fills:
            print(
                "  spread_capture_bps: "
                f"mean={sc.mean_bps:.3f} med={sc.median_bps:.3f} p10={sc.p10_bps:.3f} p90={sc.p90_bps:.3f} "
                f"qty_w_mean={sc.qty_weighted_mean_bps:.3f} (fills={sc.fills} maker={sc.maker_fills} taker={sc.taker_fills})"
            )
        else:
            print("  spread_capture_bps: (insufficient mid alignment)")

        for hs in ms.horizons:
            if hs.fills == 0:
                print(f"  {hs.horizon_ms}ms: (no markouts computed)")
                continue
            print(
                f"  {hs.horizon_ms}ms: mean_bps={hs.mean_bps:.3f} med_bps={hs.median_bps:.3f} "
                f"p10={hs.p10_bps:.3f} p90={hs.p90_bps:.3f} qty_w_mean={hs.qty_weighted_mean_bps:.3f} "
                f"mean_pnl_usdt={hs.mean_pnl_usdt:.6f} adverse_pct={hs.adverse_pct:.1f}% "
                f"(fills={hs.fills} maker={hs.maker_fills} taker={hs.taker_fills})"
            )

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(summary), f, indent=2, sort_keys=True)
        print(f"\nWrote JSON: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
