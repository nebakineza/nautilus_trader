#!/usr/bin/env bash
set -euo pipefail

QDB_HOST=${QDB_HOST:-"127.0.0.1"}
QDB_ILP_PORT=${QDB_ILP_PORT:-"9009"}
QDB_HTTP_PORT=${QDB_HTTP_PORT:-"9003"}

LOG_FILE=${LOG_FILE:-"/home/seb/nebakineza/nautilus_trader/logs/pipeline_health.log"}
OB_DIR=${OB_DIR:-"/home/seb/nebakineza/nautilus_trader/data/ob_data"}
TICK_DIR=${TICK_DIR:-"/home/seb/nebakineza/nautilus_trader/data/tick_data"}
LOG_DIR=${LOG_DIR:-"/home/seb/nebakineza/nautilus_trader/logs/vps"}

stamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "[$stamp] healthcheck" >> "$LOG_FILE"

# QuestDB ILP port
if ss -ltn | grep -q ":${QDB_ILP_PORT}"; then
  echo "  questdb_ilp: OK (${QDB_HOST}:${QDB_ILP_PORT})" >> "$LOG_FILE"
else
  echo "  questdb_ilp: DOWN (${QDB_HOST}:${QDB_ILP_PORT})" >> "$LOG_FILE"
fi

# QuestDB health endpoint
if curl -fsS "http://${QDB_HOST}:${QDB_HTTP_PORT}/" >/dev/null 2>&1; then
  echo "  questdb_http: OK (${QDB_HOST}:${QDB_HTTP_PORT})" >> "$LOG_FILE"
else
  echo "  questdb_http: DOWN (${QDB_HOST}:${QDB_HTTP_PORT})" >> "$LOG_FILE"
fi

# Data lag checks
latest_ob=$(find "$OB_DIR" -type f -name "*.data" -printf "%T@\n" 2>/dev/null | sort -n | tail -n 1)
latest_tick=$(find "$TICK_DIR" -type f -name "*.csv" -o -name "*.csv.gz" -printf "%T@\n" 2>/dev/null | sort -n | tail -n 1)
latest_log=$(find "$LOG_DIR" -type f -name "*.log*" -printf "%T@\n" 2>/dev/null | sort -n | tail -n 1)

epoch_now=$(date +%s)

if [[ -n "$latest_ob" ]]; then
  lag=$((epoch_now - ${latest_ob%.*}))
  echo "  ob_data_lag_sec: ${lag}" >> "$LOG_FILE"
else
  echo "  ob_data_lag_sec: NA" >> "$LOG_FILE"
fi

if [[ -n "$latest_tick" ]]; then
  lag=$((epoch_now - ${latest_tick%.*}))
  echo "  tick_data_lag_sec: ${lag}" >> "$LOG_FILE"
else
  echo "  tick_data_lag_sec: NA" >> "$LOG_FILE"
fi

if [[ -n "$latest_log" ]]; then
  lag=$((epoch_now - ${latest_log%.*}))
  echo "  vps_log_lag_sec: ${lag}" >> "$LOG_FILE"
else
  echo "  vps_log_lag_sec: NA" >> "$LOG_FILE"
fi

# Disk usage
usage=$(df -h /home/seb | tail -n 1 | awk '{print $5}')
echo "  disk_usage_home: ${usage}" >> "$LOG_FILE"
