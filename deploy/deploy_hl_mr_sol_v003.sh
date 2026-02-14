#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# Deploy Mean Reversion v003 JAMES — SOL on Hyperliquid
# ═══════════════════════════════════════════════════════════════════════════
#
# Deploys the SOL-tuned mean reversion strategy as a systemd service.
# Backtested: 80.0% WR | 70 trades/60d | $+16.39 | $0.27/day
#
# USAGE:
#   ./deploy/deploy_hl_mr_sol_v003.sh          # deploy & start
#   ./deploy/deploy_hl_mr_sol_v003.sh --stop   # stop service
#   ./deploy/deploy_hl_mr_sol_v003.sh --logs   # tail logs
#   ./deploy/deploy_hl_mr_sol_v003.sh --status # check status
#
# PREREQUISITES:
#   - HYPERLIQUID_MAINNET_PK must be set in .env
#   - nautilus_trader and strategy must be installed in .venv
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

SERVICE_NAME="hl_mr_sol_v003"
SERVICE_FILE="deploy/${SERVICE_NAME}.service"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# ── Handle flags ──
case "${1:-deploy}" in
    --stop)
        log "Stopping ${SERVICE_NAME}..."
        sudo systemctl stop "${SERVICE_NAME}.service"
        log "Stopped."
        exit 0
        ;;
    --logs)
        exec journalctl -u "${SERVICE_NAME}.service" -f --no-pager
        ;;
    --status)
        sudo systemctl status "${SERVICE_NAME}.service" --no-pager
        echo ""
        echo "Recent fills:"
        journalctl -u "${SERVICE_NAME}.service" --no-pager --since "1 hour ago" | grep -c "FILL\|OrderFilled" || echo "0"
        echo ""
        echo "Recent errors:"
        journalctl -u "${SERVICE_NAME}.service" --no-pager --since "1 hour ago" | grep -cE "ERROR|CRITICAL|Exception" || echo "0"
        exit 0
        ;;
    deploy|--deploy)
        ;;
    *)
        echo "Usage: $0 [deploy|--stop|--logs|--status]"
        exit 1
        ;;
esac

# ── Pre-flight checks ──
echo "═══════════════════════════════════════════════════════════════════"
echo " 🎯 Deploying Mean Reversion v003 JAMES — SOL-USD-PERP"
echo "═══════════════════════════════════════════════════════════════════"

cd "${PROJECT_DIR}"

# Check .env has the key
if ! grep -q "HYPERLIQUID_MAINNET_PK" .env 2>/dev/null; then
    err "HYPERLIQUID_MAINNET_PK not found in .env"
fi
log "Private key found in .env"

# Check strategy file exists
if [ ! -f "strategy/hl_mean_reversion_v003.py" ]; then
    err "Strategy file not found: strategy/hl_mean_reversion_v003.py"
fi
log "Strategy file exists"

# Check runner file exists
if [ ! -f "scripts/runners/run_hl_mean_reversion_sol_v003.py" ]; then
    err "Runner not found: scripts/runners/run_hl_mean_reversion_sol_v003.py"
fi
log "Runner file exists"

# Check service file exists
if [ ! -f "${SERVICE_FILE}" ]; then
    err "Service file not found: ${SERVICE_FILE}"
fi
log "Service file exists"

# Verify Python import
log "Verifying Python imports..."
.venv/bin/python -c "
from strategy.hl_mean_reversion_v003 import HLMeanReversion, HLMeanReversionConfig
from scripts.runners.run_hl_mean_reversion_sol_v003 import SOL_OPTIMAL
print('  Strategy + config: OK')
print(f'  SOL profile: BB={SOL_OPTIMAL[\"bb_period\"]}, entry={SOL_OPTIMAL[\"entry_band\"]}/{SOL_OPTIMAL[\"entry_band_outer\"]}σ')
" || err "Python import failed"

# ── Deploy service ──
log "Installing systemd service..."
sudo cp "${SERVICE_FILE}" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
log "Service installed"

# ── Stop existing if running ──
if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
    warn "Service already running — stopping first..."
    sudo systemctl stop "${SERVICE_NAME}.service"
    sleep 2
fi

# ── Start ──
log "Starting ${SERVICE_NAME}.service..."
sudo systemctl enable "${SERVICE_NAME}.service"
sudo systemctl start "${SERVICE_NAME}.service"
sleep 3

# ── Verify ──
if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    log "Service is RUNNING ✅"
else
    err "Service failed to start. Check: journalctl -u ${SERVICE_NAME}.service -n 50"
fi

# Show initial logs
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo " 📋 Initial logs:"
echo "═══════════════════════════════════════════════════════════════════"
journalctl -u "${SERVICE_NAME}.service" --no-pager -n 20

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo " 🚀 SOL Mean Reversion v003 is LIVE"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo " Monitor:  journalctl -u ${SERVICE_NAME}.service -f"
echo " Status:   sudo systemctl status ${SERVICE_NAME}.service"
echo " Stop:     sudo systemctl stop ${SERVICE_NAME}.service"
echo " Restart:  sudo systemctl restart ${SERVICE_NAME}.service"
echo " Logs:     ./deploy/deploy_hl_mr_sol_v003.sh --logs"
echo ""
echo " Profile:  OPTIMAL (80.0% WR, ~1.2 trades/day)"
echo " Pair:     SOL-USD-PERP.HYPERLIQUID"
echo " Size:     \$150 (max \$200)"
echo " Interval: 5m bars"
echo ""
