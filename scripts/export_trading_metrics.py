#!/usr/bin/env python3
"""Export key trading metrics from QuestDB into a single JSONL file.

Each line is a JSON object with a `record_type` field.

Example:
  python scripts/export_trading_metrics.py \
    --host 127.0.0.1 --port 9000 \
    --since-hours 24 \
    --output /tmp/trading_metrics.jsonl
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
import urllib.request


TABLE_QUERIES = {
    "live_wallet_equity": "select * from live_wallet_equity where timestamp > {since}",
    "live_wallet_asset": "select * from live_wallet_asset where timestamp > {since}",
    "live_account_snapshot": "select * from live_account_snapshot where timestamp > {since}",
    "live_fills": "select * from live_fills where timestamp > {since}",
    "statarb_signals": "select * from statarb_signals where timestamp > {since}",
    "live_strategy_pnl": "select * from live_strategy_pnl where timestamp > {since}",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export QuestDB trading metrics to JSONL.")
    parser.add_argument("--host", default="127.0.0.1", help="QuestDB HTTP host")
    parser.add_argument("--port", type=int, default=9000, help="QuestDB HTTP port")
    parser.add_argument(
        "--period",
        default="24h",
        help="Lookback window (e.g. 1day, 24h, 7d, 30m)",
    )
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--limit", type=int, default=0, help="Optional per-table row limit")
    return parser.parse_args()


def query_json(host: str, port: int, sql: str) -> dict[str, Any]:
    url = f"http://{host}:{port}/exec?query={quote(sql)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def rows_to_dicts(columns: list[dict[str, Any]], dataset: list[list[Any]]) -> list[dict[str, Any]]:
    keys = [col["name"] for col in columns]
    return [dict(zip(keys, row)) for row in dataset]


def _parse_period(period: str) -> tuple[str, int]:
    value = "".join(ch for ch in period if ch.isdigit())
    unit = "".join(ch for ch in period if ch.isalpha()).lower()
    aliases = {
        "m": "m",
        "min": "m",
        "mins": "m",
        "minute": "m",
        "minutes": "m",
        "h": "h",
        "hr": "h",
        "hrs": "h",
        "hour": "h",
        "hours": "h",
        "d": "d",
        "day": "d",
        "days": "d",
    }
    if not value or unit not in aliases:
        raise SystemExit("Invalid --period. Use formats like 30m, 24h, 1d, 7days.")
    unit = aliases[unit]
    return unit, int(value)


def main() -> None:
    args = parse_args()
    unit, value = _parse_period(args.period)
    since = f"dateadd('{unit}', -{value}, now())"

    metadata = {
        "record_type": "export_metadata",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": args.period,
    }

    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(metadata) + "\n")

        for table, template in TABLE_QUERIES.items():
            sql = template.format(since=since)
            if args.limit > 0:
                sql += f" limit {args.limit}"

            payload = query_json(args.host, args.port, sql)
            columns = payload.get("columns") or []
            dataset = payload.get("dataset") or []
            records = rows_to_dicts(columns, dataset)

            for record in records:
                record["record_type"] = table
                handle.write(json.dumps(record) + "\n")

    print(f"Exported {args.period} metrics to {args.output}")


if __name__ == "__main__":
    main()
