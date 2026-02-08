#!/usr/bin/env python3
"""Calculate BPS after fees for each trading pair."""

import os
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone
from collections import defaultdict

API_KEY = os.environ['BYBIT_API_KEY_MAINACC_01']
API_SECRET = os.environ['BYBIT_API_SECRET_MAINACC_01']

def sign(p, s):
    return hmac.new(s.encode(), '&'.join(f'{k}={v}' for k,v in sorted(p.items())).encode(), hashlib.sha256).hexdigest()

def get_trades(start_ts, end_ts=None, cursor=None):
    ts = str(int(time.time()*1000))
    p = {'category':'spot','limit':'100','api_key':API_KEY,'timestamp':ts,'recv_window':'5000','startTime':str(start_ts)}
    if end_ts: p['endTime'] = str(end_ts)
    if cursor: p['cursor'] = cursor
    p['sign'] = sign(p, API_SECRET)
    return requests.get('https://api.bybit.com/v5/execution/list', params=p).json()

# Get today's trades
today = datetime(2026, 2, 6, 0, 0, 0, tzinfo=timezone.utc)
start_ts = int(today.timestamp() * 1000)

all_trades = []
cursor = None
while True:
    r = get_trades(start_ts, cursor=cursor)
    if r['retCode'] != 0: break
    trades = r['result']['list']
    if not trades: break
    all_trades.extend(trades)
    cursor = r['result'].get('nextPageCursor')
    if not cursor: break
    time.sleep(0.05)

# Group by symbol
by_symbol = defaultdict(list)
for t in all_trades:
    by_symbol[t['symbol']].append(t)

print('=' * 70)
print('     BPS AFTER FEES - Feb 6, 2026')
print('=' * 70)
print()
print(f"{'Symbol':<12} {'Trades':>7} {'Volume':>12} {'Gross BPS':>10} {'Fee BPS':>9} {'Net BPS':>10}")
print('-' * 70)

total_volume = 0
total_gross_pnl = 0
total_fees = 0

for symbol in sorted(by_symbol.keys(), key=lambda s: len(by_symbol[s]), reverse=True):
    trades = by_symbol[symbol]
    
    # Separate buys and sells
    buys = [t for t in trades if t['side'] == 'Buy']
    sells = [t for t in trades if t['side'] == 'Sell']
    
    buy_value = sum(float(t['execValue']) for t in buys)
    sell_value = sum(float(t['execValue']) for t in sells)
    buy_qty = sum(float(t['execQty']) for t in buys)
    sell_qty = sum(float(t['execQty']) for t in sells)
    
    # Volume = total value traded
    volume = buy_value + sell_value
    total_volume += volume
    
    # Calculate fees in USD
    fees_usd = 0
    for t in trades:
        fee = float(t['execFee'])
        if t['feeCurrency'] == 'USDT':
            fees_usd += fee
        else:
            fees_usd += fee * float(t['execPrice'])
    total_fees += fees_usd
    
    # Matched quantity for P&L calc
    matched_qty = min(buy_qty, sell_qty)
    
    if matched_qty > 0 and buy_qty > 0 and sell_qty > 0:
        avg_buy = buy_value / buy_qty
        avg_sell = sell_value / sell_qty
        
        # Gross P&L on matched trades
        gross_pnl = matched_qty * (avg_sell - avg_buy)
        total_gross_pnl += gross_pnl
        
        # BPS calculation
        matched_value = matched_qty * avg_buy
        gross_bps = (gross_pnl / matched_value) * 10000 if matched_value > 0 else 0
        fee_bps = (fees_usd / volume) * 10000 if volume > 0 else 0
        net_bps = gross_bps - fee_bps
    else:
        gross_bps = 0
        fee_bps = (fees_usd / volume) * 10000 if volume > 0 else 0
        net_bps = -fee_bps
    
    icon = '✅' if net_bps > 0 else '❌' if net_bps < -5 else '➖'
    print(f"{symbol:<12} {len(trades):>7} ${volume:>11.2f} {gross_bps:>+10.1f} {fee_bps:>9.1f} {net_bps:>+10.1f} {icon}")

print('-' * 70)
overall_gross_bps = (total_gross_pnl / (total_volume/2)) * 10000 if total_volume > 0 else 0
overall_fee_bps = (total_fees / total_volume) * 10000 if total_volume > 0 else 0
overall_net_bps = overall_gross_bps - overall_fee_bps
print(f"{'TOTAL':<12} {len(all_trades):>7} ${total_volume:>11.2f} {overall_gross_bps:>+10.1f} {overall_fee_bps:>9.1f} {overall_net_bps:>+10.1f}")

print()
print(f"Gross P&L: ${total_gross_pnl:+.2f}")
print(f"Total Fees: ${total_fees:.2f}")
print(f"Net P&L: ${total_gross_pnl - total_fees:+.2f}")
