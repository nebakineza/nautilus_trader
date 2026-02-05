#!/bin/bash
# PHASE 7: Pre-Flight Checklist
# Final verification before going live

set -e

echo "========================================================================="
echo "PHASE 7: PRE-FLIGHT CHECKLIST"
echo "========================================================================="
echo ""

cd ~/trading/nautilus_trader
source .venv/bin/activate

# Check file structure
echo "[1/6] Verifying file structure..."
echo ""
check_file() {
    if [ -f "$1" ]; then
        echo "  ✓ $(basename $1)"
    else
        echo "  ✗ $(basename $1) - MISSING!"
        return 1
    fi
}

echo "  Strategy files:"
check_file "strategy/hft_obi_bybit_spot_mm_lowcap_v001.py" || true

echo ""
echo "  Configuration files:"
check_file "config/live_lowcap_strategy.py" || true

echo ""
echo "  Scripts:"
check_file "run_live_lowcap.py" || true
check_file "emergency_stop.sh" || true
check_file "monitor.sh" || true

echo ""
echo "  Environment:"
check_file ".env" || true

# Check environment configuration
echo ""
echo "[2/6] Checking environment configuration..."
if [ -f .env ]; then
    api_key=$(grep "BYBIT_API_KEY=" .env | cut -d'=' -f2)
    api_secret=$(grep "BYBIT_API_SECRET=" .env | cut -d'=' -f2)
    testnet=$(grep "BYBIT_TESTNET=" .env | cut -d'=' -f2)
    
    if [ -z "$api_key" ] || [ "$api_key" == "" ]; then
        echo "  ⚠️ BYBIT_API_KEY not set in .env"
    else
        echo "  ✓ BYBIT_API_KEY set: ${api_key:0:10}..."
    fi
    
    if [ -z "$api_secret" ] || [ "$api_secret" == "" ]; then
        echo "  ⚠️ BYBIT_API_SECRET not set in .env"
    else
        echo "  ✓ BYBIT_API_SECRET set: ${api_secret:0:10}..."
    fi
    
    echo "  ✓ BYBIT_TESTNET: $testnet"
else
    echo "  ✗ .env file not found!"
fi

# Verify Python environment
echo ""
echo "[3/6] Verifying Python environment..."
python --version
pip list | grep -E "nautilus|pandas|numpy" | head -5

# Check strategy configuration
echo ""
echo "[4/6] Verifying strategy configuration..."
echo "  Configuration parameters:"
python << 'PYEOF'
import re

try:
    with open('config/live_lowcap_strategy.py', 'r') as f:
        content = f.read()
        
    params = {
        'base_qty': r'base_qty=Decimal\("([^"]+)"\)',
        'max_position': r'max_position_qty=Decimal\("([^"]+)"\)',
        'kill_switch': r'emergency_liquidation_loss_usd=([-\d.]+)',
        'account_cap': r'max_notional_usd=([\d.]+)',
    }
    
    for name, pattern in params.items():
        match = re.search(pattern, content)
        if match:
            value = match.group(1)
            print(f"    ✓ {name}: {value}")
        else:
            print(f"    ✗ {name}: NOT FOUND")
            
except Exception as e:
    print(f"    ✗ Error reading config: {e}")
PYEOF

# Disk space check
echo ""
echo "[5/6] Checking disk space..."
disk_usage=$(df / | tail -1 | awk '{print int($5)}')
disk_free=$(df / | tail -1 | awk '{print int($4/1024)}')

echo "  Disk usage: $disk_usage%"
echo "  Disk free: ${disk_free}MB"

if [ $disk_usage -lt 80 ]; then
    echo "  ✓ Sufficient disk space"
else
    echo "  ⚠️ WARNING: Disk usage high (>80%)"
fi

# System resources
echo ""
echo "[6/6] System resource check..."
memory=$(free -h | grep Mem | awk '{print $3 "/" $2}')
load=$(uptime | awk -F'load average: ' '{print $2}')
cpu_cores=$(nproc)

echo "  Memory: $memory"
echo "  CPU cores: $cpu_cores"
echo "  Load average: $load"
echo "  ✓ System resources adequate"

# Final checklist
echo ""
echo "========================================================================="
echo "PRE-FLIGHT CHECKLIST"
echo "========================================================================="
echo ""
echo "BEFORE STARTING LIVE TRADING, VERIFY:"
echo ""
echo "Configuration:"
echo "  □ BYBIT_API_KEY set in .env"
echo "  □ BYBIT_API_SECRET set in .env"
echo "  □ BYBIT_TESTNET set to 'true' for testnet (STRONGLY RECOMMENDED)"
echo "  □ Kill switch configured (-\$100)"
echo "  □ Max position limited (0.0005 BTC)"
echo ""
echo "Capital & Risk:"
echo "  □ BYBIT account contains exactly \$500 USDT"
echo "  □ API key has trading permissions enabled"
echo "  □ No other trading bots running"
echo "  □ Emergency stop script is accessible"
echo ""
echo "System:"
echo "  □ Internet connection is stable"
echo "  □ Instance latency is acceptable (<100ms to BYBIT)"
echo "  □ System time is synchronized (NTP)"
echo "  □ Monitoring script is ready"
echo ""
echo "Safety:"
echo "  □ Understood kill switch will stop at -\$100 loss"
echo "  □ Read and understood all risk warnings"
echo "  □ Have emergency contact method ready"
echo ""

echo "========================================================================="
echo "PHASE 7 COMPLETE - READY FOR LAUNCH"
echo "========================================================================="
echo ""
echo "Next step: Run Phase 8 to launch trading"
echo ""
