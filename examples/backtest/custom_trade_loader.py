"""Custom loader for CSV.gz trade data.

This loader parses CSV.gz files containing trade data and converts them
to Nautilus Trade objects for backtesting.
"""

import pandas as pd
from pathlib import Path

from nautilus_trader.persistence.wranglers import TradeTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider


class CustomTradeLoader:
    """Loader for custom CSV.gz trade data format."""

    @classmethod
    def load(cls, file_path: str | Path, instrument_id: str) -> pd.DataFrame:
        """
        Load trade data from CSV.gz file and return as DataFrame.

        Parameters
        ----------
        file_path : str | Path
            Path to the CSV.gz file
        instrument_id : str
            The instrument ID (e.g., "BTCUSDT-SPOT.BYBIT")

        Returns
        -------
        pd.DataFrame
            DataFrame with columns: timestamp, price, size, side, trade_id
        """
        file_path = Path(file_path)
        
        # Read CSV.gz file
        df = pd.read_csv(
            file_path,
            compression='gzip',
            dtype={
                'id': 'int64',
                'timestamp': 'int64',
                'price': 'float64',
                'volume': 'float64',
                'side': 'str',
                'rpi': 'int64'
            }
        )

        # Convert timestamp from milliseconds to nanoseconds
        df['timestamp'] = df['timestamp'] * 1_000_000

        # Convert timestamp to datetime
        df['ts_event'] = pd.to_datetime(df['timestamp'], unit='ns', utc=True)

        # Map side strings to OrderSide enum values
        side_map = {'buy': 'BUY', 'sell': 'SELL'}
        df['side'] = df['side'].str.lower().map(side_map)

        # Rename columns to match expected format
        df = df.rename(columns={
            'volume': 'quantity',
            'id': 'trade_id'
        })

        # Select and order required columns
        df = df[['ts_event', 'price', 'quantity', 'side', 'trade_id']]

        # Set ts_event as index (required by TradeTickDataWrangler)
        df.set_index('ts_event', inplace=True)

        return df

    @classmethod
    def yield_trades(cls, file_path: str | Path, instrument_id: str):
        """
        Yield Trade objects from CSV.gz file.

        Parameters
        ----------
        file_path : str | Path
            Path to the CSV.gz file
        instrument_id : str
            The instrument ID (e.g., "BTCUSDT-SPOT.BYBIT")

        Yields
        ------
        Trade
            Nautilus Trade objects
        """
        # Load data
        df = cls.load(file_path, instrument_id)

        # Get or create instrument
        from nautilus_trader.model.identifiers import InstrumentId
        instrument = TestInstrumentProvider.btcusdt_binance()
        instrument.id = InstrumentId.from_str(instrument_id)

        # Create wrangler
        wrangler = TradeTickDataWrangler(instrument=instrument)

        # Process each row
        for _, row in df.iterrows():
            yield wrangler.process(
                ts_event=row['ts_event'],
                price=row['price'],
                size=row['quantity'],
                buyer_maker=False if row['side'] == 'BUY' else True,
                trade_id=str(row['trade_id']),
            )
