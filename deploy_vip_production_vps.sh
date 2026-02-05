#!/usr/bin/env bash
# Deploy + run VIP SUI-heavy runner on a VPS over SSH.
#
# This script does NOT copy your .env or secrets.
# You must set BYBIT_API_KEY/BYBIT_API_SECRET on the VPS (e.g. in a systemd unit or exported in the shell).

set -euo pipefail

: "${VPS_SSH:?Set VPS_SSH to an SSH target, e.g. ubuntu@your-vps or an ssh config alias}"

# Optional: explicit SSH identity file (private key)
SSH_KEY=${SSH_KEY:-}

# Common SSH options (override via SSH_OPTS if needed)
SSH_OPTS=${SSH_OPTS:-"-o BatchMode=yes -o StrictHostKeyChecking=accept-new"}
if [ -n "${SSH_KEY}" ]; then
  SSH_OPTS="-i ${SSH_KEY} ${SSH_OPTS}"
fi

REMOTE_DIR=${REMOTE_DIR:-/home/ubuntu/trading/nautilus_trader}
REMOTE_PY=${REMOTE_PY:-/home/ubuntu/trading/.venv/bin/python}
REMOTE_LOG_DIR=${REMOTE_LOG_DIR:-/home/ubuntu/trading/logs}
TRADER_ID=${TRADER_ID:-VIP1-PRODUCTION-SUI-HEAVY}
BYBIT_TESTNET=${BYBIT_TESTNET:-false}

echo "==> Syncing repo to ${VPS_SSH}:${REMOTE_DIR}"
rsync -az --delete -e "ssh ${SSH_OPTS}" \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'data' \
  --exclude 'ob_data' \
  --exclude 'tick_data' \
  --exclude 'backtest_results*' \
  --exclude 'outputs' \
  ./ "${VPS_SSH}:${REMOTE_DIR}/"

echo "==> Sanity check: compile runner on VPS"
ssh ${SSH_OPTS} "${VPS_SSH}" "cd '${REMOTE_DIR}' && '${REMOTE_PY}' -m py_compile run_vip_production.py"

echo "==> Stopping previous process (if any)"
ssh ${SSH_OPTS} "${VPS_SSH}" "cd '${REMOTE_DIR}' && if [ -f vip_sui_heavy.pid ]; then kill \"$(cat vip_sui_heavy.pid)\" 2>/dev/null || true; rm -f vip_sui_heavy.pid; fi"

echo "==> Starting runner (nohup)"
ssh ${SSH_OPTS} "${VPS_SSH}" "cd '${REMOTE_DIR}' \
  && mkdir -p '${REMOTE_LOG_DIR}' \
  && export NAUTILUS_LOG_DIR='${REMOTE_LOG_DIR}' \
  && export TRADER_ID='${TRADER_ID}' \
  && export BYBIT_TESTNET='${BYBIT_TESTNET}' \
  && nohup '${REMOTE_PY}' run_vip_production.py > vip_sui_heavy.out 2>&1 & echo \$! > vip_sui_heavy.pid"

echo "==> Started. Follow logs with:"
echo "    ssh ${VPS_SSH} 'cd ${REMOTE_DIR} && tail -n 200 -f vip_sui_heavy.out'"
