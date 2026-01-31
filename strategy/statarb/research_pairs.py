
import requests
import pandas as pd
import numpy as np
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint, adfuller
from itertools import combinations
import time

CANDIDATES = [
    "BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOT", "MATIC", "LINK", "ATOM", "OP", "ARB", "LTC", "DOGE", "UNI"
]
QUOTE = "USDT"
INTERVAL = "15" # 15 minutes
LIMIT = 1000 # Max limit per request is usually 1000 for Bybit

def fetch_history(symbol: str):
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "spot",
        "symbol": f"{symbol}{QUOTE}",
        "interval": INTERVAL,
        "limit": LIMIT,
    }
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if data["retCode"] != 0:
            print(f"Error fetching {symbol}: {data['retMsg']}")
            return None
        
        # Bybit returns [startTime, open, high, low, close, volume, turnover]
        # We need startTime and close
        records = []
        for x in data["result"]["list"]:
            records.append({
                "timestamp": int(x[0]),
                "close": float(x[4])
            })
        
        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df["close"]
    except Exception as e:
        print(f"Exception fetching {symbol}: {e}")
        return None

def check_cointegration(series_a, series_b):
    # Align data
    df = pd.concat([series_a, series_b], axis=1, join="inner").dropna()
    if len(df) < 100:
        return False, 1.0, 0.0
    
    s1 = df.iloc[:, 0]
    s2 = df.iloc[:, 1]
    
    # Engage-Granger Test
    # The null hypothesis is no cointegration. 
    # If p-value < 0.05, we reject the null hypothesis (So they are cointegrated).
    score, p_value, _ = coint(s1, s2)
    return p_value < 0.05, p_value, score

def calculate_half_life(spread):
    spread = spread.dropna()
    if len(spread) < 2:
        return 0
        
    spread_lag = spread.shift(1)
    spread_lag.iloc[0] = spread_lag.iloc[1]
    spread_ret = spread - spread_lag
    spread_ret.iloc[0] = spread_ret.iloc[1]
    spread_lag2 = sm.add_constant(spread_lag)
    model = sm.OLS(spread_ret, spread_lag2)
    res = model.fit()
    
    # Check if mean reverting (params[1] should be negative)
    if res.params[1] >= 0:
        return 9999 # Not mean reverting
        
    halflife = -np.log(2) / res.params[1]
    return halflife

def main():
    print(f"Fetching data for {len(CANDIDATES)} pairs from Bybit Spot...")
    data_map = {}
    
    for symbol in CANDIDATES:
        print(f"Fetching {symbol}...", end="\r")
        series = fetch_history(symbol)
        if series is not None:
            data_map[symbol] = series
        time.sleep(0.1) 
    print(f"\nFetched {len(data_map)} series.")

    print("\nRunning Cointegration Tests...")
    results = []
    
    for sym_a, sym_b in combinations(data_map.keys(), 2):
        s1 = data_map[sym_a]
        s2 = data_map[sym_b]
        
        is_coint, p_val, score = check_cointegration(s1, s2)
        
        if is_coint:
            # Calculate hedge ratio via OLS
            # s1 = beta * s2 + alpha
            X = sm.add_constant(s2)
            model = sm.OLS(s1, X).fit()
            hedge_ratio = model.params.iloc[1]
            spread = s1 - hedge_ratio * s2
            hl = calculate_half_life(spread)
            
            results.append({
                "pair": f"{sym_a}-{sym_b}",
                "p_value": p_val,
                "score": score,
                "half_life_bars": hl,
                "hedge_ratio": hedge_ratio
            })

    # Sort by p-value (strongest confidence first)
    results.sort(key=lambda x: x["p_value"])

    print("\n--- TOP COINTEGRATED PAIRS (15min / 10 days) ---")
    print(f"{'Pair':<15} | {'P-Value':<10} | {'Half-Life':<10} | {'Hedge Ratio':<10}")
    print("-" * 55)
    
    for r in results[:10]:
        print(f"{r['pair']:<15} | {r['p_value']:.6f}   | {r['half_life_bars']:.1f}       | {r['hedge_ratio']:.4f}")

    if not results:
        print("No cointegrated pairs found with p < 0.05")

if __name__ == "__main__":
    main()
