# Grafana / InfluxDB Monitoring Guide (Quick)

This short guide explains how to stream runtime metrics from a NautilusTrader
process into InfluxDB and visualize them in Grafana.

1. Expose metrics from your strategy/process (example metrics to emit):
   - `latency_gateway_ms`
   - `inventory_size`
   - `pnl_total`
   - `orders_submitted`
   - `orders_filled`

2. Send metrics to InfluxDB using the Python client:

```python
from influxdb_client import InfluxDBClient, Point

client = InfluxDBClient(url="http://localhost:8086", token="<TOKEN>")
write_api = client.write_api()

point = Point("nautilus_metrics").tag("trader", "HFT-BACKTESTER").field("latency_gateway_ms", 5.2)
write_api.write(bucket="nautilus", record=point)
```

3. Grafana:
   - Add InfluxDB as a data source.
   - Create dashboards for latency, inventory, PnL, fill ratio.

4. Heartbeat monitor (Kill Switch):
   - Emit a timestamped `heartbeat` metric every 100ms.
   - Alert rule: if heartbeat missing > 500ms, run a notification or call a process that issues a CancelAll via Bybit REST API.

This is a high-level starter. If you want, I can add a simple `examples/monitoring/emit_metrics.py` that shows how to collect and push these sample metrics from within a running strategy.
