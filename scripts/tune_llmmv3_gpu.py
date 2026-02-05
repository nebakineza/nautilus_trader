#!/usr/bin/env python3
"""GPU tuner for LLMMv3 sweep parameters using QuestDB fills.

Objective:
    Score = Sharpe (or Sortino) * log(1 + total_volume)
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
from torch import nn

from examples.backtest.questdb_orderbook_loader import QuestDbConfig, _query_json


@dataclass
class RunSample:
    run_id: str
    spread_bps: float
    min_profit_bps: float
    ofi_max_bps: float
    volume: float
    fees: float
    sharpe: float
    sortino: float
    score: float


def _parse_range(value: str) -> tuple[float, float]:
    left, right = value.split(",")
    return float(left), float(right)


def _parse_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_fee(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    digits = "".join(ch for ch in text if (ch.isdigit() or ch in ".-"))
    return float(digits) if digits else 0.0


def _to_ts_ns(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value) * 1_000_000  # QuestDB ms timestamps
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    return 0


def _columns_for_table(cfg: QuestDbConfig, table: str) -> list[str]:
    data = _query_json(cfg, f"select * from {table} limit 1")
    cols = data.get("columns", [])
    return [col.get("name") for col in cols if isinstance(col, dict) and col.get("name")]


def _pick_column(columns: list[str], candidates: list[str]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def _load_fills(
    cfg: QuestDbConfig,
    table: str,
    symbol: str | None,
    trader_id: str | None,
    start_ts: str | None,
    end_ts: str | None,
) -> list[dict[str, Any]]:
    columns = _columns_for_table(cfg, table)
    ts_col = _pick_column(columns, ["timestamp", "ts_event", "ts_init", "ts_last"])
    side_col = _pick_column(columns, ["side", "order_side"])
    qty_col = _pick_column(columns, ["quantity", "qty", "filled_qty", "fill_qty", "last_qty"])
    price_col = _pick_column(columns, ["price", "avg_px", "last_px", "fill_price"])
    fee_col = _pick_column(columns, ["fee", "commission", "commission_amount", "fees", "commissions"])
    inst_col = _pick_column(columns, ["instrument_id", "instrument", "symbol"])
    trader_col = _pick_column(columns, ["trader_id"])

    select_cols = [c for c in [ts_col, side_col, qty_col, price_col, fee_col, inst_col, trader_col] if c]
    if not select_cols:
        return []

    where = []
    if symbol and inst_col:
        where.append(f"{inst_col} like '{symbol}%'")
    if trader_id and trader_col:
        where.append(f"{trader_col}='{trader_id}'")
    if ts_col and start_ts:
        where.append(f"{ts_col} >= '{start_ts}'")
    if ts_col and end_ts:
        where.append(f"{ts_col} < '{end_ts}'")

    where_clause = " where " + " and ".join(where) if where else ""
    sql = f"select {', '.join(select_cols)} from {table}{where_clause} order by {ts_col}"
    data = _query_json(cfg, sql)
    dataset = data.get("dataset", [])

    fills = []
    for row in dataset:
        row_map = dict(zip(select_cols, row))
        fills.append(row_map)
    return fills


def _compute_score(
    fills: list[dict[str, Any]],
    bucket_seconds: int,
    metric: str,
) -> tuple[float, float, float, float, float]:
    if not fills:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    position = 0.0
    avg_cost = 0.0
    realized_pnl = 0.0
    volume = 0.0
    fees = 0.0

    bucket_ns = bucket_seconds * 1_000_000_000
    bucket_pnl: dict[int, float] = {}

    for fill in fills:
        ts_ns = _to_ts_ns(fill.get("timestamp") or fill.get("ts_event") or fill.get("ts_init") or fill.get("ts_last"))
        side = str(fill.get("side") or fill.get("order_side") or "").upper()
        qty = _parse_float(fill.get("quantity") or fill.get("qty") or fill.get("filled_qty") or fill.get("fill_qty") or fill.get("last_qty"))
        price = _parse_float(fill.get("price") or fill.get("avg_px") or fill.get("last_px") or fill.get("fill_price"))
        fee = _parse_fee(fill.get("fee") or fill.get("commission") or fill.get("commission_amount") or fill.get("fees") or fill.get("commissions"))

        if qty == 0 or price == 0:
            continue

        volume += abs(price * qty)
        fees += fee

        realized_before = realized_pnl
        if side == "BUY":
            if position >= 0:
                total_cost = position * avg_cost + qty * price
                position += qty
                avg_cost = total_cost / position if position else 0.0
            else:
                cover_qty = min(qty, abs(position))
                realized_pnl += (avg_cost - price) * cover_qty
                position += cover_qty
                remaining = qty - cover_qty
                if remaining > 0:
                    position += remaining
                    avg_cost = price
        elif side == "SELL":
            if position <= 0:
                total_cost = abs(position) * avg_cost + qty * price
                position -= qty
                avg_cost = total_cost / abs(position) if position else 0.0
            else:
                close_qty = min(qty, position)
                realized_pnl += (price - avg_cost) * close_qty
                position -= close_qty
                remaining = qty - close_qty
                if remaining > 0:
                    position -= remaining
                    avg_cost = price
        realized_pnl -= fee

        delta = realized_pnl - realized_before
        bucket = ts_ns // bucket_ns if bucket_ns else ts_ns
        bucket_pnl[bucket] = bucket_pnl.get(bucket, 0.0) + delta

    returns = list(bucket_pnl.values())
    if len(returns) < 2:
        return volume, fees, 0.0, 0.0, 0.0

    mean_ret = statistics.mean(returns)
    stdev = statistics.pstdev(returns)
    sharpe = (mean_ret / stdev) * math.sqrt(len(returns)) if stdev > 0 else 0.0

    downside = [r for r in returns if r < 0]
    downside_var = statistics.mean([r * r for r in downside]) if downside else 0.0
    downside_std = math.sqrt(downside_var)
    sortino = (mean_ret / downside_std) * math.sqrt(len(returns)) if downside_std > 0 else 0.0

    score = (sharpe if metric == "sharpe" else sortino) * math.log1p(volume)
    return volume, fees, sharpe, sortino, score


def _parse_params_from_run_id(run_id: str) -> tuple[float, float, float] | None:
    if "spread" not in run_id or "minp" not in run_id or "ofi" not in run_id:
        return None
    try:
        spread = run_id.split("spread", 1)[1].split("_", 1)[0]
        minp = run_id.split("minp", 1)[1].split("_", 1)[0]
        ofi = run_id.split("ofi", 1)[1].split("_", 1)[0]
        return float(spread), float(minp), float(ofi)
    except Exception:
        return None


def _load_runs_from_lake(cfg: QuestDbConfig, table: str, symbol: str | None) -> list[dict[str, Any]]:
    where = ""
    if symbol:
        where = f" where override_symbol='{symbol}'"
    data = _query_json(cfg, f"select run_id,out_dir,date from {table}{where}")
    dataset = data.get("dataset", [])
    runs = []
    for run_id, out_dir, date_str in dataset:
        if not run_id:
            continue
        runs.append({"run_id": str(run_id), "out_dir": str(out_dir), "date": str(date_str)})
    return runs


def _pareto_frontier(samples: list[RunSample]) -> list[RunSample]:
    frontier = []
    for sample in samples:
        dominated = False
        for other in samples:
            if other.volume >= sample.volume and other.fees <= sample.fees:
                if other.volume > sample.volume or other.fees < sample.fees:
                    dominated = True
                    break
        if not dominated:
            frontier.append(sample)
    return frontier


class MLP(nn.Module):
    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def _train_model(features: torch.Tensor, targets: torch.Tensor, device: torch.device, epochs: int) -> tuple[MLP, torch.Tensor, torch.Tensor]:
    mean = features.mean(dim=0, keepdim=True)
    std = features.std(dim=0, keepdim=True).clamp(min=1e-6)
    x = (features - mean) / std

    model = MLP(features.shape[1]).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()

    for _ in range(epochs):
        preds = model(x)
        loss = loss_fn(preds, targets)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    return model, mean, std


def main() -> None:
    parser = argparse.ArgumentParser(description="GPU tuner for LLMMv3 sweeps")
    parser.add_argument("--questdb-host", default="127.0.0.1")
    parser.add_argument("--questdb-port", type=int, default=9000)
    parser.add_argument("--fills-table", default="fills")
    parser.add_argument("--lake-table", default="backtest_results_lake")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--start-ts", default=None, help="ISO start timestamp (UTC)")
    parser.add_argument("--end-ts", default=None, help="ISO end timestamp (UTC)")
    parser.add_argument("--trader-id-prefix", default="BACKTEST-LLMMV3-PRIMER-")
    parser.add_argument("--bucket-seconds", type=int, default=300)
    parser.add_argument("--metric", choices=("sharpe", "sortino"), default="sharpe")
    parser.add_argument("--spread-range", default=None, help="min,max")
    parser.add_argument("--min-profit-range", default=None, help="min,max")
    parser.add_argument("--ofi-range", default=None, help="min,max")
    parser.add_argument("--num-candidates", type=int, default=2000)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--epochs", type=int, default=500)
    parser.add_argument("--output", default="outputs/optuna/llmmv3_gpu_tuner.json")
    args = parser.parse_args()

    cfg = QuestDbConfig(host=args.questdb_host, port=args.questdb_port)
    runs = _load_runs_from_lake(cfg, args.lake_table, args.symbol)
    samples: list[RunSample] = []

    for run in runs:
        run_id = run["run_id"]
        params = _parse_params_from_run_id(run_id)
        if not params:
            continue
        spread, minp, ofi = params
        trader_id = f"{args.trader_id_prefix}{run_id}"

        fills = _load_fills(
            cfg,
            args.fills_table,
            args.symbol,
            trader_id,
            args.start_ts,
            args.end_ts,
        )
        volume, fees, sharpe, sortino, score = _compute_score(
            fills,
            bucket_seconds=args.bucket_seconds,
            metric=args.metric,
        )
        if volume <= 0:
            continue

        samples.append(
            RunSample(
                run_id=run_id,
                spread_bps=spread,
                min_profit_bps=minp,
                ofi_max_bps=ofi,
                volume=volume,
                fees=fees,
                sharpe=sharpe,
                sortino=sortino,
                score=score,
            )
        )

    if not samples:
        raise SystemExit("No samples found. Ensure fills are tagged with run-specific trader_id.")

    features = torch.tensor(
        [[s.spread_bps, s.min_profit_bps, s.ofi_max_bps] for s in samples],
        dtype=torch.float32,
    )
    targets = torch.tensor([[s.score] for s in samples], dtype=torch.float32)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    features = features.to(device)
    targets = targets.to(device)

    model, mean, std = _train_model(features, targets, device, args.epochs)

    spread_min, spread_max = _parse_range(args.spread_range) if args.spread_range else (
        min(s.spread_bps for s in samples),
        max(s.spread_bps for s in samples),
    )
    minp_min, minp_max = _parse_range(args.min_profit_range) if args.min_profit_range else (
        min(s.min_profit_bps for s in samples),
        max(s.min_profit_bps for s in samples),
    )
    ofi_min, ofi_max = _parse_range(args.ofi_range) if args.ofi_range else (
        min(s.ofi_max_bps for s in samples),
        max(s.ofi_max_bps for s in samples),
    )

    candidates = []
    for _ in range(args.num_candidates):
        spread = random.uniform(spread_min, spread_max)
        minp = random.uniform(minp_min, minp_max)
        ofi = random.uniform(ofi_min, ofi_max)
        candidates.append([spread, minp, ofi])

    cand_tensor = torch.tensor(candidates, dtype=torch.float32, device=device)
    with torch.no_grad():
        preds = model((cand_tensor - mean) / std).cpu().numpy().flatten()

    ranked = sorted(
        zip(preds, candidates),
        key=lambda item: item[0],
        reverse=True,
    )[: args.top_k]

    frontier = _pareto_frontier(samples)

    output = {
        "metric": args.metric,
        "objective": "score = risk_adjusted * log(1 + volume)",
        "device": str(device),
        "samples": [s.__dict__ for s in samples],
        "pareto_frontier": [s.__dict__ for s in frontier],
        "suggestions": [
            {
                "predicted_score": float(score),
                "spread_bps": float(params[0]),
                "min_profit_bps": float(params[1]),
                "ofi_max_bps": float(params[2]),
            }
            for score, params in ranked
        ],
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))
    print(f"Wrote tuner output to {output_path}")


if __name__ == "__main__":
    main()
