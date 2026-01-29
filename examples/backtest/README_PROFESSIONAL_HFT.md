# Professional HFT Strategy Backtest

## Overview

This directory contains the backtest setup for the `InstitutionalHFT` strategy from `examples/professional_hft.py`.

## Files Created

1. **custom_trade_loader.py** - Custom loader for CSV.gz trade data
2. **run_professional_hft.py** - Backtest runner script (has syntax issues, see manual setup below)

## Data Structure

- **Trade Data**: `tick_data/BTCUSDT_Spot/` - CSV.gz files with columns: id, timestamp, price, volume, side, rpi
- **Order Book Data**: `ob_data/BTCUSDT_Spot/` - JSON files with order book deltas (200 levels)

## Manual Backtest Setup

Due to Python syntax issues in the automated runner, here's how to manually run the backtest:

### Option 1: Use Existing Working Example

The `examples/backtest/crypto_orderbook_imbalance.py` is a working example. Modify it to use your data:

```python
# In crypto_orderbook_imbalance.py, modify the data loading section:

# Load your trade data
from examples.backtest.custom_trade_loader import CustomTradeLoader

df_trades = CustomTradeLoader.load(
    "tick_data/BTCUSDT_Spot/BTCUSDT_2026-01-15.csv.gz",
    "BTCUSDT-SPOT.BYBIT"
)

# Load your order book data (JSON format)
import json
import pandas as pd

rows = []
with open("ob_data/BTCUSDT_Spot/2026-01-15_BTCUSDT_ob200.data", 'r') as f:
    for line in f:
        obj = json.loads(line.strip())
        # Process order book data...

df_ob = pd.DataFrame(rows)
```

### Option 2: Direct Python Script

Create a simple script:

```python
#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from examples.professional_hft import InstitutionalHFT, ProfessionalHFTConfig
from examples.backtest.custom_trade_loader import CustomTradeLoader

# Setup
config = BacktestEngineConfig(
    trader_id=TraderId("HFT-BACKTESTER-001"),
    ref_currency=USDT,
    initial_balance=[Money(10000, USDT)],
)

engine = BacktestEngine(config=config)

# Add venue
engine.add_venue(
    venue=BYBIT,
    oms_type=OmsType.NETTING,
    account_type=AccountType.CASH,
    base_currency=None,
    starting_balances=[Money(10000, USDT), Money(0.1, BTC)],
    book_type=BookType.L2_MBP,
    oms_latency_ns=5000000,
)

# Add instrument
BTCUSDT = TestInstrumentProvider.btcusdt_binance()
BTCUSDT.id = InstrumentId.from_str("BTCUSDT-SPOT.BYBIT")
engine.add_instrument(BTCUSDT)

# Load and add trade data
df_trades = CustomTradeLoader.load(
    "tick_data/BTCUSDT_Spot/BTCUSDT_2026-01-15.csv.gz",
    "BTCUSDT-SPOT.BYBIT"
)

wrangler = TradeTickDataWrangler(instrument=BTCUSDT)
trades = wrangler.process(df_trades)
engine.add_data(trades)

# Add strategy
strat_config = ProfessionalHFTConfig(
    instrument_id="BTCUSDT-SPOT.BYBIT",
    base_qty=0.01,
    num_layers=5,
    max_notional_exposure=5000.0,
    inventory_alpha=0.1,
    toxic_flow_threshold=2.5,
    update_interval_ms=20,
)

strategy = InstitutionalHFT(config=strat_config)
engine.add_strategy(strategy=strategy)

# Run backtest
engine.run()

# Generate reports
print(engine.trader.generate_account_report(BYBIT))
print(engine.trader.generate_order_fills_report())
print(engine.trader.generate_positions_report())

# Cleanup
engine.reset()
engine.dispose()
```

## Strategy Configuration

The `ProfessionalHFTConfig` has these parameters:

- `instrument_id`: "BTCUSDT-SPOT.BYBIT"
- `base_qty`: 0.01 (trade size in BTC)
- `num_layers`: 5 (number of price levels)
- `max_notional_exposure`: 5000.0 (max position value in USDT)
- `inventory_alpha`: 0.1 (inventory skew factor)
- `toxic_flow_threshold`: 2.5 (OFI ratio threshold)
- `update_interval_ms`: 20 (50Hz update rate)

## Running the Backtest

From the project root directory:

```bash
# Activate virtual environment
source .venv/bin/activate  # On Linux/macOS
# or
.venv\\Scripts\\activate  # On Windows

# Run your backtest script
python your_backtest_script.py
```

## Troubleshooting

1. **Import Errors**: Make sure nautilus_trader is installed in the virtual environment
   ```bash
   .venv/bin/python -c "import nautilus_trader; print('OK')"
   ```

2. **Data Not Found**: Check file paths are correct relative to project root

3. **Memory Issues**: Limit data with `MAX_TRADE_ROWS` parameter

4. **Build Issues**: Run `make install` to rebuild the project

## Next Steps

1. Fix the `run_professional_hft.py` script (currently has syntax errors)
2. Add order book data loading support
3. Test with different parameter values
4. Analyze backtest results and optimize strategy parameters