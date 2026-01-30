from decimal import Decimal
from nautilus_trader.model.identifiers import InstrumentId
from strategy.statarb.pairs_strategy import StatArbPairsConfig

DOT_LINK_CONFIG = StatArbPairsConfig(
    instrument_a_id=InstrumentId.from_str("DOTUSDT-SPOT.BYBIT"),
    instrument_b_id=InstrumentId.from_str("LINKUSDT-SPOT.BYBIT"),
    bar_type="15-MINUTE-BID",
    notional_per_trade=Decimal("20.0"), # Conservative start
    entry_z_score=2.0,
    exit_z_score=0.2, # Exit near mean
    stop_loss_z_score=4.5,
    kalman_delta=1e-4, # Default adaptive speed
)
