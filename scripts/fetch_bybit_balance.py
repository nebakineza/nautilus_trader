#!/usr/bin/env python3
import time
import hmac
import hashlib
import json
import os
import requests

def get_balance():
    # Try SENTINEL keys first, then standard keys
    api_key = os.getenv("BYBIT_API_KEY_SENTINEL") or os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET_SENTINEL") or os.getenv("BYBIT_API_SECRET")
    
    if not api_key or not api_secret:
        print("Error: BYBIT_API_KEY_SENTINEL/BYBIT_API_KEY or BYBIT_API_SECRET_SENTINEL/BYBIT_API_SECRET not set in environment.")
        return

    # Bybit V5
    url = "https://api.bybit.com"
    endpoint = "/v5/account/wallet-balance"
    params = "accountType=UNIFIED"
    
    timestamp = str(int(time.time() * 1000))
    recv_window = str(5000)
    
    # Signature
    payload = timestamp + api_key + recv_window + params
    signature = hmac.new(
        bytes(api_secret, "utf-8"),
        bytes(payload, "utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }
    
    full_url = url + endpoint + "?" + params
    
    try:
        response = requests.get(full_url, headers=headers)
        data = response.json()
        
        if data["retCode"] != 0:
            print(f"Bybit API Error: {data}")
            return

        # Parse Balance
        coins = data["result"]["list"][0]["coin"]
        print("\n=== BYBIT WALLET BALANCES ===")
        found_funds = False
        
        # Relevant for TriArb
        target_coins = ["USDT", "USDC", "BTC", "ETH", "SOL"]
        
        bal_map = {}
        
        for coin in coins:
            symbol = coin["coin"]
            equity = float(coin["equity"])
            wallet_bal = float(coin["walletBalance"])
            
            if wallet_bal > 0:
                print(f"{symbol}: {wallet_bal} (Equity: {equity})")
                bal_map[symbol] = wallet_bal
                found_funds = True
                
        if not found_funds:
            print("No positive balances found.")
            
        print("=============================\n")
        
        # Suggest config adjustment
        print("Suggested `starting_balances` for backtest:")
        print("starting_balances=[")
        for c in target_coins:
            amt = bal_map.get(c, 0.0)
            if amt > 0:
                print(f'    Money(Decimal("{amt}"), {c}),')
            elif c == "USDT":
                 # Fallback if empty
                 print(f'    Money(Decimal("1000.0"), {c}), # Default if empty')
        print("]")

    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    get_balance()
