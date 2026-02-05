#!/usr/bin/env python3
"""Capture Binance spot order book deltas to Nautilus JSONL format.

Outputs JSON lines matching bybit/binance loaders:
  {"type":"snapshot|delta","ts":<ms>,"data":{"s":"SOLUSDT","b":[[p,q],...],"a":[[p,q],...]}}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import websockets  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'websockets'. Install with: "
        "/home/seb/nebakineza/nautilus_trader/.venv/bin/pip install websockets"
    ) from exc


def _utc_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _server_time_ms() -> int:
    url = "https://api.binance.com/api/v3/time"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.load(resp)
    return int(data.get("serverTime", int(time.time() * 1000)))


def _snapshot(symbol: str, depth: int) -> dict:
    params = urllib.parse.urlencode({"symbol": symbol, "limit": depth})
    url = f"https://api.binance.com/api/v3/depth?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.load(resp)
    return data


def _write_line(out, record: dict) -> None:
    out.write(json.dumps(record) + "\n")
    out.flush()


async def capture(args: argparse.Namespace) -> None:
    symbol = args.symbol.upper()
    stream = f"{symbol.lower()}@depth@100ms"
    ws_url = f"wss://stream.binance.com:9443/ws/{stream}"

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_utc_date_str()}_{symbol}_binance_ob{args.depth}.data"

    snapshot = _snapshot(symbol, args.depth)
    ts_ms = _server_time_ms()

    with out_path.open("a", encoding="utf-8") as out:
        _write_line(
            out,
            {
                "type": "snapshot",
                "ts": ts_ms,
                "data": {"s": symbol, "b": snapshot.get("bids", []), "a": snapshot.get("asks", [])},
            },
        )

        last_update_id = snapshot.get("lastUpdateId", 0)
        start = time.time()
        msg_count = 0

        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
            async for raw in ws:
                event = json.loads(raw)
                if "u" not in event:
                    continue
                if event["u"] <= last_update_id:
                    continue
                if event["U"] <= last_update_id + 1 <= event["u"]:
                    last_update_id = event["u"]
                    _write_line(
                        out,
                        {
                            "type": "delta",
                            "ts": int(event.get("E", _server_time_ms())),
                            "data": {"s": symbol, "b": event.get("b", []), "a": event.get("a", [])},
                        },
                    )
                    msg_count += 1

                if args.max_messages and msg_count >= args.max_messages:
                    break
                if args.max_seconds and (time.time() - start) >= args.max_seconds:
                    break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture Binance order book updates to JSONL")
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--depth", type=int, default=1000)
    parser.add_argument("--out-dir", default="/home/seb/nebakineza/nautilus_trader/data/ob_data_ingest")
    parser.add_argument("--max-seconds", type=int, default=0)
    parser.add_argument("--max-messages", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.max_seconds = args.max_seconds or None
    args.max_messages = args.max_messages or None
    asyncio.run(capture(args))


if __name__ == "__main__":
    main()
