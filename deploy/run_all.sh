#!/bin/bash
# MASTER DEPLOYMENT SCRIPT
# Execute all phases in sequence with safety checks

set -e

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║                    NAUTILUS TRADER DEPLOYMENT                         ║"
echo "║                 Specialized Low-Capital Strategy v1.0                  ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

DEPLOY_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DEPLOY_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to run phase
run_phase() {
    local phase_num=$1
    local phase_name=$2
    local script=$3
    
    echo ""
    echo "════════════════════════════════════════════════════════════════════════"
    echo "PHASE $phase_num: $phase_name"
    echo "════════════════════════════════════════════════════════════════════════"
    echo ""
    
    if [ ! -f "$script" ]; then
        echo -e "${RED}✗ Script not found: $script${NC}"
        return 1
    fi
    
    bash "$script"
    
    echo ""
    read -p "Continue to next phase? (press Enter or type 'skip' to stop): " response
    if [ "$response" == "skip" ]; then
        echo "Deployment paused. Run individual scripts to continue:"
        echo "  bash $script"
        exit 0
    fi
}

# Phase 1: Assessment
run_phase 1 "INITIAL ASSESSMENT & BACKUP" "01_phase1_assessment.sh" || exit 1

# Phase 2: Cleanup
run_phase 2 "CLEAN UP HUMMINGBOT" "02_phase2_cleanup.sh" || exit 1

# Phase 3: Optimization
run_phase 3 "SYSTEM OPTIMIZATION" "03_phase3_optimization.sh" || exit 1

# Phase 4: Install NautilusTrader
run_phase 4 "INSTALL NAUTILUS TRADER" "04_phase4_install_nautilus.sh" || exit 1

# Phase 5: Deploy Strategy
run_phase 5 "DEPLOY STRATEGY" "05_phase5_deploy_strategy.sh" || exit 1

# Phase 6: Safety Checks
run_phase 6 "SAFETY CHECKS" "06_phase6_safety_checks.sh" || exit 1

# Phase 7: Pre-flight
run_phase 7 "PRE-FLIGHT CHECKLIST" "07_phase7_preflight.sh" || exit 1

# Deployment complete
echo ""
echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║                   ✓ DEPLOYMENT COMPLETE                               ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Next Steps:"
echo "==========="
echo ""
echo "1. SSH to AWS Instance:"
echo "   ssh ubuntu@13.228.94.51"
echo ""
echo "2. Run deployment script:"
echo "   bash ~/trading/nautilus_trader/deploy/run_all.sh"
echo ""
echo "3. After deployment, set API keys in .env:"
echo "   nano ~/trading/nautilus_trader/.env"
echo ""
echo "4. Start trading (TESTNET FIRST):"
echo "   cd ~/trading/nautilus_trader"
echo "   source .venv/bin/activate"
echo "   bash deploy/08_phase8_launch.sh"
echo ""
echo "5. Monitor in another terminal:"
echo "   bash ~/trading/nautilus_trader/monitor.sh"
echo ""
echo "Emergency Stop:"
echo "   bash ~/trading/nautilus_trader/emergency_stop.sh"
echo ""
