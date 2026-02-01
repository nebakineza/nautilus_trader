#!/usr/bin/env bash
set -euo pipefail

VPS_HOST=${VPS_HOST:-"ubuntu@13.228.94.51"}
SSH_KEY=${SSH_KEY:-"/home/seb/.ssh/hummingbot-aws"}
REMOTE_DIR=${REMOTE_DIR:-"/home/ubuntu/trading"}
LOCAL_DIR=${LOCAL_DIR:-"/home/seb/nebakineza/nautilus_trader/logs/vps"}
PRUNE_REMOTE=${PRUNE_REMOTE:-false}

mkdir -p "$LOCAL_DIR"

# Pull current logs and rotated logs
rsync -az --partial \
  -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
  "$VPS_HOST:$REMOTE_DIR/"*.log* \
  "$LOCAL_DIR/" || true

if [[ "$PRUNE_REMOTE" == "true" ]]; then
  rsync -az --partial --remove-source-files \
    -e "ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -o ConnectTimeout=15" \
    "$VPS_HOST:$REMOTE_DIR/"*.log.*.gz \
    "$LOCAL_DIR/" >/dev/null || true
fi
