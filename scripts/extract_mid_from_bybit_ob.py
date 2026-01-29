#!/usr/bin/env python3
"""Extract a mid-price time series from Bybit JSONL order book data.

Produces a dependency-free CSV suitable for `scripts/analyze_v002_backtest.py --mid-csv ...`.

Input format:
- JSON lines
- Each line contains either a `snapshot` or `delta` with:
  - `ts` (milliseconds)
  - `data` with `b` bids and `a` asks arrays of [price, size] strings

Output columns:
- timestamp (ISO8601, UTC, Z)
- instrument_id
- best_bid
- best_ask
- mid

Example:
  python3 scripts/extract_mid_from_bybit_ob.py \
    --ob-file ob_data/BTCUSDT_Spot/2026-01-15_BTCUSDT_ob200.data \
    --output-csv backtest_results_v002/mid.csv \
    --instrument-id BTCUSDT-SPOT.BYBIT \
    --min-interval-ms 10
"""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _iso_utc_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _infer_instrument_id(symbol: Optional[str]) -> Optional[str]:
    if not symbol:
        return None
    # Matches the repo's convention: e.g. BTCUSDT-SPOT.BYBIT
    return f"{symbol}-SPOT.BYBIT"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ob-file", type=str, required=True, help="Path to Bybit .data JSONL orderbook file")
    ap.add_argument("--output-csv", type=str, required=True, help="Path for output mid CSV")
    ap.add_argument("--instrument-id", type=str, default=None, help="InstrumentId for output rows (optional)")
    ap.add_argument("--min-interval-ms", type=int, default=10, help="Minimum time between output rows")
    ap.add_argument("--max-lines", type=int, default=None, help="Process at most N input lines (debug)")
    args = ap.parse_args()

    ob_path = Path(args.ob_file)
    if not ob_path.exists():
        raise SystemExit(f"Orderbook file not found: {ob_path}")

    out_path = Path(args.output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None

    instrument_id: Optional[str] = args.instrument_id
    last_written_ms: Optional[int] = None

    def recompute_best_bid() -> Optional[float]:
        return max(bids) if bids else None

    def recompute_best_ask() -> Optional[float]:
        return min(asks) if asks else None

    rows_written = 0
    lines_read = 0

    def _iter_lines() -> tuple[object, Iterable[str]]:
        """Return (handle, iterable_of_lines) where handle must be closed."""
        if ob_path.suffix == ".zip":
            zf = zipfile.ZipFile(ob_path, "r")
            members = [m for m in zf.namelist() if not m.endswith("/")]
            if not members:
                zf.close()
                raise SystemExit(f"Zip contains no files: {ob_path}")
            # Prefer inner file matching '<date>_<symbol>_ob200.data'
            preferred = next((m for m in members if m.endswith("_ob200.data")), members[0])
            fh = zf.open(preferred, "r")
            # Text wrapper
            import io

            txt = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
            # We'll close via closing the ZipFile which closes members.
            return zf, txt
        return ob_path.open("r", encoding="utf-8", errors="replace"), None

    with out_path.open("w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(
            f_out,
            fieldnames=["timestamp", "instrument_id", "best_bid", "best_ask", "mid"],
        )
        writer.writeheader()

        handle, zip_text = _iter_lines()
        try:
            f_in = zip_text if zip_text is not None else handle
            for line in f_in:
                lines_read += 1
                if args.max_lines and lines_read > args.max_lines:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_ms = msg.get("ts")
                if ts_ms is None:
                    continue
                try:
                    ts_ms = int(ts_ms)
                except Exception:
                    continue

                data = msg.get("data") or {}

                if instrument_id is None:
                    instrument_id = _infer_instrument_id(data.get("s"))

                mtype = msg.get("type")
                if mtype == "snapshot":
                    bids.clear()
                    asks.clear()
                    for px_s, sz_s in (data.get("b") or []):
                        try:
                            px = float(px_s)
                            sz = float(sz_s)
                        except Exception:
                            continue
                        if sz > 0:
                            bids[px] = sz
                    for px_s, sz_s in (data.get("a") or []):
                        try:
                            px = float(px_s)
                            sz = float(sz_s)
                        except Exception:
                            continue
                        if sz > 0:
                            asks[px] = sz
                    best_bid = recompute_best_bid()
                    best_ask = recompute_best_ask()

                elif mtype == "delta":
                    for px_s, sz_s in (data.get("b") or []):
                        try:
                            px = float(px_s)
                            sz = float(sz_s)
                        except Exception:
                            continue
                        if sz <= 0:
                            if px in bids:
                                del bids[px]
                            if best_bid is not None and px == best_bid:
                                best_bid = recompute_best_bid()
                        else:
                            bids[px] = sz
                            if best_bid is None or px > best_bid:
                                best_bid = px

                    for px_s, sz_s in (data.get("a") or []):
                        try:
                            px = float(px_s)
                            sz = float(sz_s)
                        except Exception:
                            continue
                        if sz <= 0:
                            if px in asks:
                                del asks[px]
                            if best_ask is not None and px == best_ask:
                                best_ask = recompute_best_ask()
                        else:
                            asks[px] = sz
                            if best_ask is None or px < best_ask:
                                best_ask = px
                else:
                    continue

                if best_bid is None or best_ask is None:
                    continue
                if best_bid <= 0 or best_ask <= 0:
                    continue

                if last_written_ms is not None and (ts_ms - last_written_ms) < int(args.min_interval_ms):
                    continue

                mid = (best_bid + best_ask) / 2.0
                writer.writerow(
                    {
                        "timestamp": _iso_utc_from_ms(ts_ms),
                        "instrument_id": instrument_id or "__SINGLE__",
                        "best_bid": f"{best_bid:.10f}",
                        "best_ask": f"{best_ask:.10f}",
                        "mid": f"{mid:.10f}",
                    }
                )
                rows_written += 1
                last_written_ms = ts_ms

        finally:
            try:
                handle.close()
            except Exception:
                pass

    print(f"Read {lines_read:,} lines, wrote {rows_written:,} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
