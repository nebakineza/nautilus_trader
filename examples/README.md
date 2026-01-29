# Examples

The following code examples are organized by system environment context:

- **Backtest**: Historical data with simulated venues.
- **Sandbox**: Real-time data with simulated venues.
- **Live**: Real-time data with live venues (paper trading or real accounts).
- **Other**: Various examples beyond strategies.

Scripts within each environment context directory are organized by integration.

Ensure that the `nautilus_trader` package is either compiled from source or installed via pip before
running the examples. See the [installation guide](https://nautilustrader.io/docs/latest/getting_started/installation)
for more information.

To execute an example script from the `examples` directory, use a command similar to the following:

```
python backtest/crypto_ema_cross_ethusdt_trade_ticks.py
```

## Backtesting Guide

- **Install the package (editable)**: from the repository root run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements.txt  # optional, if present
```

- **Run an existing backtest example** (examples are under `examples/backtest`):

```bash
python examples/backtest/example_07_using_indicators/run_example.py
```

- **Create a quick backtest runner**: use `BacktestEngineConfig` and `BacktestEngine` as in the existing examples. Minimal snippet:

```python
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.model.identifiers import TraderId

config = BacktestEngineConfig(trader_id=TraderId("BACKTEST-001"))
engine = BacktestEngine(config=config)

# add your strategy to the engine and run (see examples/backtest for patterns)
```

- **Adapting a live strategy for backtesting**: copy or import your `Strategy` class (for example `examples/professional_hft.py`) into a backtest runner and subscribe the engine to historical ticks or bars (see `examples/backtest/*` for data-loading patterns).

- **Logs & results**: examples typically print or save results; search `examples/backtest` for `BacktestEngine` usage to find output handling (reports, PnL, trades).

If you want, I can add a dedicated backtest runner that exercises `examples/professional_hft.py` against a small sample dataset and commits it into `examples/backtest/`.
Also see the institutional backtesting write-up at the repository root: [01-backtesting.md](../01-backtesting.md)
