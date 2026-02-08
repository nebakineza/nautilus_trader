#!/bin/bash
# VIP1 Acquisition Strategy - Interactive Launcher
# Usage: ./vip1_launcher.sh

set -e

VENV_PYTHON="/home/seb/nebakineza/nautilus_trader/.venv/bin/python"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Banner
echo -e "${BLUE}"
cat << 'EOF'
╔════════════════════════════════════════════════════════════════════════════════╗
║                    VIP1 ACQUISITION STRATEGY LAUNCHER                          ║
║                                                                                ║
║  Target: $33,333 daily volume @ Net PnL > $0 (ZERO LOSS RULE)                 ║
╚════════════════════════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

# Main menu
echo ""
echo "Select an option:"
echo ""
echo "  1) 🔍 Quick Reference Card"
echo "  2) 📥 Ingest Binance Data (Single Pair)"
echo "  3) 📥 Ingest Binance Data (All 5 Pairs)"
echo "  4) 🧪 Run Quick Test Sweep (SUIUSDT only)"
echo "  5) 🚀 Run Full Sweep (5-pair portfolio)"
echo "  6) 📊 Analyze Latest Sweep Results"
echo "  7) 📚 View Documentation"
echo "  8) ❌ Exit"
echo ""
read -p "Enter choice [1-8]: " choice

case $choice in
  1)
    # Quick reference
    echo -e "\n${GREEN}=== QUICK REFERENCE CARD ===${NC}\n"
    bash "$SCRIPT_DIR/vip1_quick_reference.sh"
    ;;
    
  2)
    # Single pair ingestion
    echo -e "\n${YELLOW}=== INGEST SINGLE PAIR ===${NC}\n"
    
    read -p "Enter pair symbol (e.g., SUIUSDT): " PAIR
    read -p "Enter date (YYYY-MM-DD): " DATE
    
    # Construct CoinAPI symbol ID
    BASE="${PAIR%USDT}"
    COINAPI_ID="BINANCE_SPOT_${BASE}_USDT"
    
    echo -e "\n${BLUE}Ingesting ${PAIR} for ${DATE}...${NC}\n"
    
    $VENV_PYTHON "$PROJECT_ROOT/scripts/coinapi_flatfiles_ingest_orderbook.py" \
        --date "$DATE" \
        --exchange BINANCE \
        --symbol "$PAIR" \
        --coinapi-symbol-id "$COINAPI_ID" \
        --batch-size 5000
    
    echo -e "\n${GREEN}✅ Ingestion complete!${NC}"
    ;;
    
  3)
    # All 5 pairs ingestion
    echo -e "\n${YELLOW}=== INGEST ALL 5 PAIRS ===${NC}\n"
    
    read -p "Enter date (YYYY-MM-DD): " DATE
    
    PAIRS=("ETHUSDT" "SUIUSDT" "DOGEUSDT" "AVAXUSDT" "LINKUSDT")
    
    for PAIR in "${PAIRS[@]}"; do
        BASE="${PAIR%USDT}"
        COINAPI_ID="BINANCE_SPOT_${BASE}_USDT"
        
        echo -e "\n${BLUE}Ingesting ${PAIR}...${NC}"
        
        $VENV_PYTHON "$PROJECT_ROOT/scripts/coinapi_flatfiles_ingest_orderbook.py" \
            --date "$DATE" \
            --exchange BINANCE \
            --symbol "$PAIR" \
            --coinapi-symbol-id "$COINAPI_ID" \
            --batch-size 5000
    done
    
    echo -e "\n${GREEN}✅ All 5 pairs ingested!${NC}"
    ;;
    
  4)
    # Quick test sweep
    echo -e "\n${YELLOW}=== QUICK TEST SWEEP ===${NC}\n"
    
    read -p "Enter date (YYYY-MM-DD): " DATE
    
    echo -e "\n${BLUE}Running quick sweep for SUIUSDT...${NC}"
    echo -e "${YELLOW}(This will take ~5-10 minutes)${NC}\n"
    
    $VENV_PYTHON "$PROJECT_ROOT/scripts/run_vip_acquisition_sweep.py" \
        --date "$DATE" \
        --pairs SUIUSDT \
        --quick \
        --questdb
    
    echo -e "\n${GREEN}✅ Quick sweep complete!${NC}"
    echo -e "${BLUE}Run option 6 to analyze results.${NC}"
    ;;
    
  5)
    # Full sweep
    echo -e "\n${YELLOW}=== FULL 5-PAIR SWEEP ===${NC}\n"
    
    read -p "Enter date (YYYY-MM-DD): " DATE
    read -p "Include expansion pairs (SOLUSDT, XRPUSDT)? [y/N]: " EXPAND
    
    if [[ "$EXPAND" =~ ^[Yy]$ ]]; then
        PAIR_LIST="ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,SOLUSDT,XRPUSDT"
        echo -e "\n${BLUE}Running full sweep for 7 pairs...${NC}"
    else
        PAIR_LIST="ETHUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT"
        echo -e "\n${BLUE}Running full sweep for 5 pairs...${NC}"
    fi
    
    echo -e "${YELLOW}(This will take ~4-6 hours)${NC}\n"
    
    $VENV_PYTHON "$PROJECT_ROOT/scripts/run_vip_acquisition_sweep.py" \
        --date "$DATE" \
        --pairs "$PAIR_LIST" \
        --profile balanced \
        --questdb
    
    echo -e "\n${GREEN}✅ Full sweep complete!${NC}"
    echo -e "${BLUE}Run option 6 to analyze results.${NC}"
    ;;
    
  6)
    # Analyze results
    echo -e "\n${YELLOW}=== ANALYZE RESULTS ===${NC}\n"
    
    $VENV_PYTHON "$PROJECT_ROOT/scripts/analyze_vip_progress.py" --auto
    
    echo -e "\n${GREEN}✅ Analysis complete!${NC}"
    echo -e "${BLUE}Check portfolio_summary.json for deployment configs.${NC}"
    ;;
    
  7)
    # View documentation
    echo -e "\n${YELLOW}=== DOCUMENTATION ===${NC}\n"
    echo "Available documentation:"
    echo ""
    echo "  • VIP1_ACQUISITION_README.md       - Complete reference"
    echo "  • VIP1_ACQUISITION_WORKFLOW.md     - Step-by-step guide"
    echo "  • VIP1_DELIVERY_SUMMARY.md         - Delivery summary"
    echo "  • scripts/vip1_quick_reference.sh  - Quick commands"
    echo ""
    read -p "Open README.md? [y/N]: " OPEN_README
    
    if [[ "$OPEN_README" =~ ^[Yy]$ ]]; then
        if command -v less &> /dev/null; then
            less "$PROJECT_ROOT/VIP1_ACQUISITION_README.md"
        else
            cat "$PROJECT_ROOT/VIP1_ACQUISITION_README.md"
        fi
    fi
    ;;
    
  8)
    # Exit
    echo -e "\n${BLUE}Goodbye!${NC}\n"
    exit 0
    ;;
    
  *)
    echo -e "\n${RED}Invalid choice. Please select 1-8.${NC}\n"
    exit 1
    ;;
esac

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}                        Operation Complete ✅                        ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
echo ""
