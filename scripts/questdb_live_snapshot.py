#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import hmac
import hashlib
from decimal import Decimal
from urllib.parse import urlencode
import urllib.request

from strategy.metrics.questdb_writer import QuestDbILPWriter

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

ASSETS = ["USDT", "BTC", "ETH", "SOL", "DOT", "LINK", "OP"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOTUSDT", "LINKUSDT", "OPUSDT"]


def _signed_get(path: str, params: dict[str, str]) -> dict:
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise RuntimeError("BYBIT API credentials not set")

    base_url = "https://api.bybit.com"
    recv_window = "20000"
    timestamp = str(int(time.time() * 1000))
    query = urlencode(params)
    prehash = timestamp + BYBIT_API_KEY + recv_window + query
    signature = hmac.new(BYBIT_API_SECRET.encode(), prehash.encode(), hashlib.sha256).hexdigest()

    url = base_url + path + "?" + query
    req = urllib.request.Request(url)
    req.add_header("X-BAPI-API-KEY", BYBIT_API_KEY)
    req.add_header("X-BAPI-TIMESTAMP", timestamp)
    req.add_header("X-BAPI-SIGN", signature)
    req.add_header("X-BAPI-RECV-WINDOW", recv_window)

    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_wallet_balances() -> dict[str, Decimal]:
    data = _signed_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    coins = data.get("result", {}).get("list", [{}])[0].get("coin", [])
    balances: dict[str, Decimal] = {}
    for coin in coins:
        symbol = coin.get("coin")
        if symbol in ASSETS:
            balances[symbol] = Decimal(str(coin.get("walletBalance", "0")))
    return balances


def _get_prices() -> dict[str, Decimal]:
    prices = {"USDT": Decimal("1")}
    for symbol in SYMBOLS:
        data = urllib.request.urlopen(
            f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}",
            timeout=10,
        )
        payload = json.loads(data.read().decode("utf-8"))
        last = payload["result"]["list"][0]["lastPrice"]
        prices[symbol.replace("USDT", "")] = Decimal(str(last))
    return prices


def main() -> None:
    writer = QuestDbILPWriter.from_env("wallet_snapshot")
    interval = int(os.getenv("SNAPSHOT_INTERVAL_SECS", "10"))

    while True:
        try:
            balances = _get_wallet_balances()
            prices = _get_prices()
            ts_ns = int(time.time() * 1_000_000_000)

            total = Decimal("0")
            for coin, qty in balances.items():
                price = prices.get(coin, Decimal("0"))
                value = qty * price
                total += value
                writer.send(
                    table="live_wallet_asset",
                    tags={"coin": coin},
                    fields={
                        "qty": qty,
                        "price_usdt": price,
                        "value_usdt": value,
                    },
                    ts_ns=ts_ns,
                )

            writer.send(
                table="live_wallet_equity",
                tags={},
                fields={
                    "equity_usdt": total,
                },
                ts_ns=ts_ns,
            )
        except Exception:
            pass

        time.sleep(interval)


if __name__ == "__main__":
    main()
