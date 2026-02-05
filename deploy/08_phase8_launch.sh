#!/bin/bash
# PHASE 8: Launch Live Trading
# Starts the strategy (TESTNET FIRST, then MAINNET)

set -e

echo "========================================================================="
echo "PHASE 8: LAUNCH LIVE TRADING"
echo "========================================================================="
echo ""

cd ~/trading/nautilus_trader
source .venv/bin/activate

# Check if trading is already running
echo "[1/3] Checking for existing trading processes..."
if pgrep -f "run_live_lowcap.py" > /dev/null; then
    echo "⚠️  Trading process already running!"
    echo "   PID: $(pgrep -f 'run_live_lowcap.py')"
    echo "   Stop it first or use emergency_stop.sh"
    exit 1
fi
echo "✓ No existing trading process"

# Check testnet setting
echo ""
echo "[2/3] Checking trading mode..."
testnet=$(grep "BYBIT_TESTNET=" .env | cut -d'=' -f2 | tr -d ' ')

if [ "$testnet" == "true" ]; then
    echo "⚠️  TESTNET MODE ACTIVE"
    echo "   Trading on: BYBIT Testnet"
    echo "   This is SAFE - no real money at risk"
else
    echo "⚠️  MAINNET MODE ACTIVE"
    echo "   Trading on: BYBIT Mainnet"
    echo "   Real money will be traded!"
    echo ""
    read -p "Continue with MAINNET? (type 'yes' to confirm): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Cancelled"
        exit 1
    fi
fi

# Start trading
echo ""
echo "[3/3] Starting trading..."
echo ""
echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║                   🚀 LAUNCHING LIVE TRADING 🚀                         ║"
echo "║                                                                        ║"
echo "║  Strategy: Specialized Low-Capital OBI Market Maker                   ║"
echo "║  Capital: \$500 USDT                                                  ║"
echo "║  Kill Switch: -\$100 USD (active)                                     ║"
echo "║  Mode: $([ "$testnet" == "true" ] && echo "TESTNET (safe)" || echo "MAINNET (live!)")                                     ║"
echo "║  Timestamp: $(date '+%Y-%m-%d %H:%M:%S')                                         ║"
echo "║                                                                        ║"
echo "║  Press Ctrl+C to stop gracefully                                      ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

# Create log file
log_file="logs/trading_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs

# Start the trading process
python run_live_lowcap.py 2>&1 | tee "$log_file"

echo ""
echo "✓ Trading process exited"
echo "Logs saved to: $log_file"
