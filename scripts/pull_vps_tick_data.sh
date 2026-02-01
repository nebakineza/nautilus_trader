#!/usr/bin/env bash
set -euo pipefail

VPS_HOST=${VPS_HOST:-"ubuntu@13.228.94.51"}
SSH_KEY=${SSH_KEY:-"/home/seb/.ssh/hummingbot-aws"}
REMOTE_DIR=${REMOTE_DIR:-"/home/ubuntu/trading/data/tick_data"}
LOCAL_DIR=${LOCAL_DIR:-"/home/seb/nebakineza/nautilus_trader/data/tick_data_live"}
TICK_DIR=${TICK_DIR:-"/home/seb/nebakineza/nautilus_trader/data/tick_data"}
PRUNE_REMOTE=${PRUNE_REMOTE:-false}

mkdir -p "$LOCAL_DIR" "$TICK_DIR"

rsync -az --append-verify --partial \
  -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
  "$VPS_HOST:$REMOTE_DIR/" \
  "$LOCAL_DIR/"

rsync -az --append-verify --partial \
  "$LOCAL_DIR/" \
  "$TICK_DIR/"

if [[ "$PRUNE_REMOTE" == "true" ]]; then
  rsync -az --append-verify --partial --remove-source-files \
    -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
    "$VPS_HOST:$REMOTE_DIR/" \
    "$LOCAL_DIR/" >/dev/null
fi
