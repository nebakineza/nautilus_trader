"""Analyze price micro-structure from Bybit OB data (NDJSON format)."""
from pathlib import Path
import json
import numpy as np

f = Path("data/ob_data/SOLUSDT_Spot/2026-01-29_SOLUSDT_bybit_ob50.data")

ts_list = []
mid_list = []
sprd_list = []
count = 0

with open(f, "r") as fp:
    for line in fp:
        count += 1
        if count % 20 != 0:
            continue
        if count > 300000:
            break
        try:
            rec = json.loads(line)
            d = rec.get("data", rec)
            bids = d.get("b", [])
            asks = d.get("a", [])
            if bids and asks:
                bid1 = float(bids[0][0])
                ask1 = float(asks[0][0])
                ts_list.append(rec.get("ts", 0))
                mid_list.append((bid1 + ask1) / 2)
                sprd_list.append(ask1 - bid1)
        except Exception:
            pass

print(f"Got {len(mid_list)} samples from {count} lines (every 20th)")
P = np.array(mid_list)
T = np.array(ts_list, dtype=np.float64)
S = np.array(sprd_list)
dur = (T[-1] - T[0]) / 1000
print(f"Span: {dur:.0f}s = {dur / 3600:.1f}h")

print("\n=== Return distribution by horizon ===")
for h in [2, 5, 10, 20, 50, 100, 200, 500]:
    if h < len(P):
        r = (P[h:] - P[:-h]) / P[:-h] * 10000
        th = np.mean(T[h:] - T[:-h]) / 1000
        print(f"  {h:4d}-samp ({th:6.1f}s): mean={np.mean(r):+.3f}bps std={np.std(r):.2f}bps")

print(f"\nSpread: {np.mean(S):.4f}$ = {np.mean(S / P * 10000):.2f}bps")
print(f"Price: {P.min():.2f} -> {P.max():.2f}")

print("\n=== Achievability: random entry -> +Xbps ===")
header = f"{'target':>8s}"
for w in [10, 20, 50, 100, 200]:
    if w < len(P):
        tw = w * (dur / len(mid_list))
        header += f"  {w}s({tw:.0f}s)"
print(header)

for tb in [3, 5, 8, 10, 15, 20, 30]:
    row = f"  +{tb:2d}bps: "
    for w in [10, 20, 50, 100, 200]:
        if w >= len(P):
            continue
        ok = 0
        n = 0
        for i in range(0, min(len(P) - w, 12000), 3):
            if (np.max(P[i + 1 : i + w]) - P[i]) / P[i] * 10000 >= tb:
                ok += 1
            n += 1
        row += f"  {ok / n * 100:5.1f}%  "
    print(row)

# Key insight: what if we only enter when price DROPPED significantly?
print("\n=== Conditional: enter after X bps DROP from 50-sample high ===")
for drop_thresh in [5, 10, 15, 20, 30]:
    for tp in [3, 5, 8, 10]:
        ok = 0
        n = 0
        for i in range(50, min(len(P) - 50, 12000)):
            recent_high = np.max(P[i - 50 : i])
            drawdown = (recent_high - P[i]) / recent_high * 10000
            if drawdown >= drop_thresh:
                best_fwd = np.max(P[i + 1 : i + 50])
                if (best_fwd - P[i]) / P[i] * 10000 >= tp:
                    ok += 1
                n += 1
        if n > 0:
            tw = 50 * (dur / len(mid_list))
            print(f"  After -{drop_thresh:2d}bps drop -> +{tp:2d}bps in {tw:.0f}s: {ok}/{n} = {ok / n * 100:.1f}%")

# Cross-venue lead-lag analysis
print("\n=== Lead-lag: Binance leads, Bybit follows? ===")
bf = Path("data/ob_data/SOLUSDT_Spot/2026-01-29_SOLUSDT_binance_ob50.data")
b_ts = []
b_mid = []
bcount = 0
with open(bf, "r") as fp2:
    for line in fp2:
        bcount += 1
        if bcount % 20 != 0:
            continue
        if bcount > 200000:
            break
        try:
            rec = json.loads(line)
            d = rec.get("data", rec)
            bids = d.get("bids", d.get("b", []))
            asks = d.get("asks", d.get("a", []))
            if bids and asks:
                bid1 = float(bids[0][0])
                ask1 = float(asks[0][0])
                b_ts.append(rec.get("ts", rec.get("T", rec.get("E", 0))))
                b_mid.append((bid1 + ask1) / 2)
        except Exception:
            pass

print(f"Binance: {len(b_mid)} samples from {bcount} lines")
if len(b_mid) > 100:
    BP = np.array(b_mid)
    BT = np.array(b_ts, dtype=np.float64)

    for leader_thresh in [3, 5, 8, 10, 15]:
        for tp in [3, 5, 8]:
            b_rets = (BP[10:] - BP[:-10]) / BP[:-10] * 10000
            up_moves = np.where(b_rets >= leader_thresh)[0]
            if len(up_moves) == 0:
                continue
            ok = 0
            n = 0
            for idx in up_moves[:500]:
                b_time = BT[idx + 10]
                by_idx = np.searchsorted(T, b_time)
                if by_idx >= len(P) - 50 or by_idx < 1:
                    continue
                best_fwd = np.max(P[by_idx : by_idx + 50])
                if (best_fwd - P[by_idx]) / P[by_idx] * 10000 >= tp:
                    ok += 1
                n += 1
            if n > 0:
                print(f"  Binance +{leader_thresh:2d}bps -> Bybit +{tp}bps (50samp): {ok}/{n} = {ok / n * 100:.1f}%")
