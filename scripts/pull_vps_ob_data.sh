#!/usr/bin/env bash
set -euo pipefail

VPS_HOST=${VPS_HOST:-"ubuntu@13.228.94.51"}
SSH_KEY=${SSH_KEY:-"/home/seb/.ssh/hummingbot-aws"}
REMOTE_DIR=${REMOTE_DIR:-"/home/ubuntu/trading/data/ob_data_live"}
LOCAL_DIR=${LOCAL_DIR:-"/home/seb/nebakineza/nautilus_trader/data/ob_data_live"}
OB_DIR=${OB_DIR:-"/home/seb/nebakineza/nautilus_trader/data/ob_data"}
LOG_DIR=${LOG_DIR:-"/home/seb/nebakineza/nautilus_trader/logs"}
PRUNE_REMOTE=${PRUNE_REMOTE:-false}

mkdir -p "$LOCAL_DIR" "$OB_DIR" "$LOG_DIR"

rsync -az --append-verify --partial \
  -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
  "$VPS_HOST:$REMOTE_DIR/" \
  "$LOCAL_DIR/"

if [[ "$PRUNE_REMOTE" == "true" ]]; then
  rsync -az --append-verify --partial --remove-source-files \
    -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
    "$VPS_HOST:$REMOTE_DIR/" \
    "$LOCAL_DIR/" >/dev/null
fi

rsync -az --append-verify --partial \
  "$LOCAL_DIR/" \
  "$OB_DIR/"
