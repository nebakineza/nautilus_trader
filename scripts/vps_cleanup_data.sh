#!/usr/bin/env bash
set -euo pipefail

OB_DIR="/home/ubuntu/trading/data/ob_data_live"
TICK_DIR="/home/ubuntu/trading/data/tick_data"
RETENTION_DAYS=${RETENTION_DAYS:-7}

find "$OB_DIR" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
find "$TICK_DIR" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
