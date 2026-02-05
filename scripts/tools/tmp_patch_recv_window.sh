#!/usr/bin/env bash
set -euo pipefail
CONFIG_FILE=/home/ubuntu/trading/nautilus_env/lib/python3.12/site-packages/nautilus_trader/adapters/bybit/config.py
sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
sudo sed -i \
	-e "s/default 5000/default 20000/g" \
	-e "s/5_000/20_000/g" \
	"$CONFIG_FILE"
grep -n recv_window_ms "$CONFIG_FILE"
