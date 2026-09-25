#!/usr/bin/env bash
set -euo pipefail

: "${VPS_IP:?Set VPS_IP, e.g. VPS_IP=203.0.113.10 bash scripts/deploy.sh}"
VPS_USER="${VPS_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/opt/adx-regime-atr-executor}"
SERVICE_NAME="adx_regime_atr_executor"

echo "Deploying ADX Regime ATR Executor to ${VPS_USER}@${VPS_IP}:${REMOTE_DIR}"
ssh "${VPS_USER}@${VPS_IP}" "apt-get update -qq && apt-get install -y python3 python3-venv python3-pip rsync"
ssh "${VPS_USER}@${VPS_IP}" "mkdir -p '${REMOTE_DIR}'"
rsync -avz --delete \
  --exclude='.git' --exclude='.env' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='data/*.db' --exclude='logs/*' \
  ./ "${VPS_USER}@${VPS_IP}:${REMOTE_DIR}/"
ssh "${VPS_USER}@${VPS_IP}" "cd '${REMOTE_DIR}' && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt"
ssh "${VPS_USER}@${VPS_IP}" "[ -f '${REMOTE_DIR}/.env' ] || cp '${REMOTE_DIR}/.env.example' '${REMOTE_DIR}/.env'"
ssh "${VPS_USER}@${VPS_IP}" "cp '${REMOTE_DIR}/systemd/adx_regime_atr_executor.service' /etc/systemd/system/adx_regime_atr_executor.service && systemctl daemon-reload && systemctl enable adx_regime_atr_executor"

echo "Deployment copied. Edit ${REMOTE_DIR}/.env, run validate_setup.py, then start with: systemctl start ${SERVICE_NAME}"
