#!/bin/bash
# Deploy Multi-Pair v002 Strategy to AWS
# Phase 1: Multi-pair foundation

set -e

echo "========================================================================"
echo "DEPLOYING MULTI-PAIR OBI MARKET MAKER v002 TO AWS"
echo "========================================================================"
echo ""

# Configuration
AWS_HOST="ubuntu@13.228.94.51"
AWS_DIR="~/trading"
STRATEGY_FILE="strategy/hft_obi_bybit_spot_mm_lowcap_multipair_v002.py"
BACKTEST_FILE="run_multipair_backtest_v002.py"

echo "Step 1: Uploading v002 strategy file..."
scp "$STRATEGY_FILE" "$AWS_HOST:$AWS_DIR/"
echo "✓ Strategy uploaded"

echo ""
echo "Step 2: Uploading backtest runner..."
scp "$BACKTEST_FILE" "$AWS_HOST:$AWS_DIR/"
echo "✓ Backtest runner uploaded"

echo ""
echo "Step 3: Creating runner script for live trading..."
ssh "$AWS_HOST" << 'RUNNER_SCRIPT'
cd ~/trading
source nautilus_env/bin/activate

cat > run_live_multipair_v002.py << 'PYTHON_EOF'
#!/usr/bin/env python3
"""Live trading runner for Multi-Pair OBI MM v002."""

import os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

from nautilus_trader.adapters.bybit.config import BybitDataClientConfig
from nautilus_trader.adapters.bybit.config import BybitExecClientConfig
from nautilus_trader.adapters.bybit.factories import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit.factories import BybitLiveExecClientFactory
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId

# Import v002 strategy
import sys
sys.path.insert(0, '/home/ubuntu/trading')
from hft_obi_bybit_spot_mm_lowcap_multipair_v002 import (
    MultiPairLowCapitalOBIMarketMaker,
    MultiPairMMConfig,
)

# Configuration
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise ValueError("BYBIT API credentials not set!")

print("\n" + "="*70)
print("NAUTILUS TRADER - MULTI-PAIR OBI MARKET MAKER v002")
print("="*70)
print(f"Mode: {'TESTNET' if BYBIT_TESTNET else 'MAINNET (LIVE!)'}")
print(f"Instruments: BTC-USDT + ETH-USDT")
print(f"Capital: $500 USD")
print(f"Kill Switch: -$100 USD")
print("="*70 + "\n")

# Strategy configuration
strategy_config = MultiPairMMConfig(
    instruments=[
        {
            'instrument_id': 'BTCUSDT-SPOT.BYBIT',
            'base_qty': '0.0001',
            'max_position_qty': '0.0003',
            'weight': 0.6,
            'obi_sensitivity': 1.0,
            'min_spread_bps': 1,
            'max_spread_bps': 5,
        },
        {
            'instrument_id': 'ETHUSDT-SPOT.BYBIT',
            'base_qty': '0.005',
            'max_position_qty': '0.015',
            'weight': 0.4,
            'obi_sensitivity': 1.2,
            'min_spread_bps': 1,
            'max_spread_bps': 6,
        },
    ],
    total_capital_usd=500.0,
    emergency_liquidation_loss_usd=-100.0,
)

# BYBIT adapter configuration - load both instruments
instrument_provider_config = InstrumentProviderConfig(
    load_ids=(
        InstrumentId.from_str("BTCUSDT-SPOT.BYBIT"),
        InstrumentId.from_str("ETHUSDT-SPOT.BYBIT"),
    ),
)

bybit_data_config = BybitDataClientConfig(
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
    testnet=BYBIT_TESTNET,
    instrument_provider=instrument_provider_config,
)

bybit_exec_config = BybitExecClientConfig(
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
    testnet=BYBIT_TESTNET,
)

# Trading node configuration
config = TradingNodeConfig(
    trader_id="MULTIPAIR-002",
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="DEBUG",
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        snapshot_orders=True,
        snapshot_positions=True,
    ),
    data_clients={
        "BYBIT": bybit_data_config,
    },
    exec_clients={
        "BYBIT": bybit_exec_config,
    },
    timeout_connection=30.0,
    timeout_reconciliation=10.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
)

# Create trading node
node = TradingNode(config=config)

# Add client factories
node.add_data_client_factory("BYBIT", BybitLiveDataClientFactory)
node.add_exec_client_factory("BYBIT", BybitLiveExecClientFactory)

# Add strategy
node.trader.add_strategy(MultiPairLowCapitalOBIMarketMaker(config=strategy_config))

# Build and run
try:
    node.build()
    node.run()
except KeyboardInterrupt:
    print("\n\n✓ Shutdown requested")
finally:
    node.stop()
    node.dispose()
PYTHON_EOF

chmod +x run_live_multipair_v002.py
echo "✓ Live runner created"
RUNNER_SCRIPT

echo "✓ Runner script created on AWS"

echo ""
echo "========================================================================"
echo "DEPLOYMENT COMPLETE ✓"
echo "========================================================================"
echo ""
echo "Phase 1 Implementation Status:"
echo "  [✓] Multi-pair strategy file deployed"
echo "  [✓] Live trading runner configured"
echo "  [✓] Dual instrument loading (BTC + ETH)"
echo "  [✓] Capital allocation (60% BTC / 40% ETH)"
echo ""
echo "Next steps:"
echo "  1. Test v002 on AWS:"
echo "     ssh $AWS_HOST"
echo "     cd ~/trading"
echo "     source nautilus_env/bin/activate"
echo "     python run_live_multipair_v002.py"
echo ""
echo "  2. Monitor both BTC and ETH positions"
echo "  3. Validate capital allocation across pairs"
echo "  4. Begin Phase 2: Inventory Management"
echo ""
