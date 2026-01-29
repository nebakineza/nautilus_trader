"""Small example helpers that show how to use the built-in persistence wranglers
to convert CSV/Parquet orderbook deltas and trades into Nautilus objects.

These are illustrative. Real ingestion requires provider-specific parsing and
field-mapping; see your data vendor docs (Tardis, etc.).
"""

from nautilus_trader.persistence.wranglers import OrderBookDeltaWrangler, TradeWrangler


def load_orderbook_deltas(path):
    """Return a generator of OrderBookDelta objects from `path`."""
    wrangler = OrderBookDeltaWrangler(path)
    return wrangler.yield_objects()


def load_trades(path):
    """Return a generator of Trade objects from `path`."""
    wrangler = TradeWrangler(path)
    return wrangler.yield_objects()
