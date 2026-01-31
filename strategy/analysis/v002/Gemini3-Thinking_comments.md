Created the VIP1 acquisition variant.

New strategy: lead_lag_bybit_binance_mm_v003_primer.py
Defaults: spread_bps=16, min_profit_bps=0, maker_fee_bps=7.5, ofi_enabled=True, ofi_max_bps=15
Daily loss kill‑switch: $20 limit with 24h reset
To use it, swap your runner import to LeadLagMMv3Primer and LeadLagMMv3PrimerConfig.

If you want me to wire a dedicated primer runner (SOL/DOGE/AVAX presets) and deploy it to the VPS, say the word.