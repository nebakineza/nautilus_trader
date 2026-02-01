#!/usr/bin/env bash
set -euo pipefail

# Sync Bybit live data/logs for LLMMv3_Primer and ingest into QuestDB.
# Usage:
#   PRUNE_REMOTE=true ./scripts/sync_primer_data.sh
# Optional overrides:
#   VPS_HOST, SSH_KEY, REMOTE_OB_DIR, REMOTE_TICK_DIR, REMOTE_LOG_DIR
#   QDB_HOST, QDB_PORT

VPS_HOST=${VPS_HOST:-"ubuntu@13.228.94.51"}
SSH_KEY=${SSH_KEY:-"/home/seb/.ssh/hummingbot-aws"}

REMOTE_OB_DIR=${REMOTE_OB_DIR:-"/home/ubuntu/trading/data/ob_data_live"}
REMOTE_TICK_DIR=${REMOTE_TICK_DIR:-"/home/ubuntu/trading/data/tick_data"}
REMOTE_LOG_DIR=${REMOTE_LOG_DIR:-"/home/ubuntu/trading/logs"}

LOCAL_ROOT="/home/seb/nebakineza/nautilus_trader"
LOCAL_OB_LIVE="$LOCAL_ROOT/data/ob_data_live"
LOCAL_TICK_LIVE="$LOCAL_ROOT/data/tick_data_live"
LOCAL_LOG_DIR="$LOCAL_ROOT/logs/vps"

QDB_HOST=${QDB_HOST:-"127.0.0.1"}
QDB_PORT=${QDB_PORT:-"9009"}
INGEST_TICKS=${INGEST_TICKS:-"true"}
SKIP_INGEST=${SKIP_INGEST:-"false"}
FORCE_PRUNE=${FORCE_PRUNE:-"false"}

mkdir -p "$LOCAL_OB_LIVE" "$LOCAL_TICK_LIVE" "$LOCAL_LOG_DIR"

rsync -az --append-verify --partial \
  -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
  "$VPS_HOST:$REMOTE_OB_DIR/" \
  "$LOCAL_OB_LIVE/"

rsync -az --append-verify --partial \
  -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
  "$VPS_HOST:$REMOTE_TICK_DIR/" \
  "$LOCAL_TICK_LIVE/"

rsync -az --partial \
  -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
  "$VPS_HOST:$REMOTE_LOG_DIR/"*.log* \
  "$LOCAL_LOG_DIR/" || true

# Mirror into canonical data folders
rsync -az --append-verify --partial "$LOCAL_OB_LIVE/" "$LOCAL_ROOT/data/ob_data/"
rsync -az --append-verify --partial "$LOCAL_TICK_LIVE/" "$LOCAL_ROOT/data/tick_data/"

if [[ "$SKIP_INGEST" != "true" ]]; then
  # Ingest into QuestDB (orderbook + ticks)
  /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
    "$LOCAL_ROOT/scripts/questdb_ingest_ob_data.py" \
    --data-dir "$LOCAL_ROOT/data/ob_data" \
    --host "$QDB_HOST" --port "$QDB_PORT"

  if [[ "$INGEST_TICKS" == "true" ]]; then
    if ! /home/seb/nebakineza/nautilus_trader/.venv/bin/python \
      "$LOCAL_ROOT/scripts/questdb_ingest_tick_data.py" \
      --data-dir "$LOCAL_ROOT/data/tick_data" \
      --host "$QDB_HOST" --port "$QDB_PORT" --timeout 60; then
      echo "Tick ingest failed; leaving tick_data for next run." >&2
    fi
  fi
fi

if [[ "${PRUNE_REMOTE:-false}" == "true" ]]; then
  if [[ "$SKIP_INGEST" == "true" && "$FORCE_PRUNE" != "true" ]]; then
    echo "PRUNE_REMOTE requested but SKIP_INGEST=true; skipping prune without FORCE_PRUNE=true." >&2
    exit 0
  fi
  rsync -az --append-verify --partial --remove-source-files \
    -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
    "$VPS_HOST:$REMOTE_OB_DIR/" \
    "$LOCAL_OB_LIVE/" >/dev/null || true

  rsync -az --append-verify --partial --remove-source-files \
    -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
    "$VPS_HOST:$REMOTE_TICK_DIR/" \
    "$LOCAL_TICK_LIVE/" >/dev/null || true

  rsync -az --partial --remove-source-files \
    -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
    "$VPS_HOST:$REMOTE_LOG_DIR/"*.log.*.gz \
    "$LOCAL_LOG_DIR/" >/dev/null || true
fi
