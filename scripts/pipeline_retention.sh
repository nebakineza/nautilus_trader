#!/usr/bin/env bash
set -euo pipefail

RETENTION_DAYS=${RETENTION_DAYS:-14}
OB_DIR=${OB_DIR:-"/home/seb/nebakineza/nautilus_trader/data/ob_data"}
TICK_DIR=${TICK_DIR:-"/home/seb/nebakineza/nautilus_trader/data/tick_data"}
LOG_DIR=${LOG_DIR:-"/home/seb/nebakineza/nautilus_trader/logs/vps"}

find "$OB_DIR" -type f -name "*.data" -mtime +"$RETENTION_DAYS" -print -delete || true
find "$TICK_DIR" -type f \( -name "*.csv" -o -name "*.csv.gz" \) -mtime +"$RETENTION_DAYS" -print -delete || true
find "$LOG_DIR" -type f -name "*.log*" -mtime +"$RETENTION_DAYS" -print -delete || true
