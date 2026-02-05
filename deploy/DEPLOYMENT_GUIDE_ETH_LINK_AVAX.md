# VIP ETH/LINK/AVAX Production Deployment Guide

## Overview
Three new production services for the top-performing pairs, each using the LLMM API key.

## Services Created

### 1. vip_eth_production.service
- **Symbol**: ETHUSDT
- **Historical P&L**: +$501.38 (3,747 trades)
- **Order Size**: 0.02 ETH (~$60)
- **Max Position**: 0.5 ETH (~$1500)
- **Spread**: 8 bps (tighter for ETH liquidity)
- **Trader ID**: VIP1-PRODUCTION-ETH

### 2. vip_link_production.service
- **Symbol**: LINKUSDT  
- **Historical P&L**: +$149.66 (5 trades)
- **Order Size**: 5 LINK (~$50)
- **Max Position**: 50 LINK (~$500)
- **Spread**: 12 bps
- **Trader ID**: VIP1-PRODUCTION-LINK

### 3. vip_avax_production.service
- **Symbol**: AVAXUSDT
- **Historical P&L**: +$85.59 (269 trades)
- **Order Size**: 5 AVAX (~$50)
- **Max Position**: 100 AVAX (~$1000)
- **Spread**: 15 bps
- **Trader ID**: VIP1-PRODUCTION-AVAX

## Fee Configuration
**VIP0 + MNT Discount:**
- Maker: 0.075% (0.00075)
- Taker: 0.075% (0.00075)

## Deployment Steps

### 1. Deploy to VPS
```bash
cd /home/seb/nebakineza/nautilus_trader
./deploy/deploy_vip_eth_link_avax.sh
```

### 2. SSH to VPS
```bash
ssh -i /home/seb/nebakineza/nautilus_trader/id_ed25519 ubuntu@13.228.94.51
```

### 3. Verify Environment
```bash
# Check .env has BYBIT_API_KEY_LLMM and BYBIT_API_SECRET_LLMM
cat /home/ubuntu/trading/.env | grep LLMM

# Check strategy files exist
ls -lh /home/ubuntu/trading/strategy_pkg/run_vip_*_production.py
```

### 4. Start Services (One at a Time for Testing)
```bash
# Start ETH first
sudo systemctl start vip_eth_production
sudo systemctl status vip_eth_production

# Monitor logs (Ctrl+C to exit)
sudo journalctl -u vip_eth_production -f

# If ETH looks good, start LINK
sudo systemctl start vip_link_production
sudo systemctl status vip_link_production

# If LINK looks good, start AVAX
sudo systemctl start vip_avax_production
sudo systemctl status vip_avax_production
```

### 5. Monitor All Services
```bash
# Check status
sudo systemctl status vip_eth_production vip_link_production vip_avax_production

# View combined logs
sudo journalctl -u vip_eth_production -u vip_link_production -u vip_avax_production -f

# Or individual file logs
tail -f /home/ubuntu/trading/logs/VIP1-PRODUCTION-ETH.service.log
tail -f /home/ubuntu/trading/logs/VIP1-PRODUCTION-LINK.service.log
tail -f /home/ubuntu/trading/logs/VIP1-PRODUCTION-AVAX.service.log
```

### 6. Stop Services (if needed)
```bash
sudo systemctl stop vip_eth_production
sudo systemctl stop vip_link_production
sudo systemctl stop vip_avax_production
```

## Log Locations
- **Systemd logs**: `journalctl -u <service-name>`
- **File logs**: `/home/ubuntu/trading/logs/VIP1-PRODUCTION-<PAIR>.service.log`

## Health Checks
After starting each service, verify:
1. ✅ No errors in `journalctl -u <service> -n 50`
2. ✅ Connected to Binance (orderbook subscription)
3. ✅ Connected to Bybit (orderbook subscription + execution)
4. ✅ Strategy initialized
5. ✅ Quotes being placed within 30 seconds

## Expected Behavior
Each service should:
- Subscribe to Binance leader orderbook (read-only)
- Subscribe to Bybit follower orderbook  
- Place bid/ask quotes on Bybit within spread parameters
- Modify quotes on orderbook updates
- Execute when profitable opportunities arise

## Rollback
If any service has issues:
```bash
# Stop problematic service
sudo systemctl stop vip_<pair>_production

# Check logs for errors
sudo journalctl -u vip_<pair>_production -n 100

# Disable auto-start if needed
sudo systemctl disable vip_<pair>_production
```

## API Key Usage
All three services currently use:
- `BYBIT_API_KEY_LLMM`
- `BYBIT_API_SECRET_LLMM`

**Rate limits** (per API key):
- 50 orders/second (shared across all 3 services)
- This should be sufficient as each pair only places 2-4 orders at a time

**Future**: When separate subaccounts are set up, update each service's runner to use dedicated keys.

## Monitoring Dashboard
Check trading performance via QuestDB:
```sql
-- Real-time P&L by symbol
SELECT 
    symbol,
    sum(CASE WHEN side='SELL' THEN filled_value ELSE -filled_value END) as net_pnl,
    sum(fees) as total_fees,
    count(*) as trades
FROM order_history_spot
WHERE timestamp > dateadd('h', -24, now())
GROUP BY symbol
ORDER BY net_pnl DESC
```

## Success Criteria
Within first 24 hours:
- ✅ All 3 services running without crashes
- ✅ Quotes being maintained consistently
- ✅ At least 5-10 fills per service
- ✅ No excessive adverse selection (> -1% P&L)
- ✅ Positive or neutral P&L trend

## Next Steps (After Validation)
1. Create 3 separate Bybit subaccounts
2. Generate API keys for each
3. Update .env with separate keys
4. Restart services with dedicated keys
5. Scale up position sizes gradually
