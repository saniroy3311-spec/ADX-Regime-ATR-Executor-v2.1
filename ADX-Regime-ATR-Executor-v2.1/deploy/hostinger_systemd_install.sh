#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/adx-regime-atr-executor}"
SERVICE_NAME="adx_regime_atr_executor"

if [ -z "$REPO_URL" ]; then
  echo "Set REPO_URL, e.g. REPO_URL=git@github.com:OWNER/adx-regime-atr-executor.git bash deploy/hostinger_systemd_install.sh"
  exit 1
fi

apt-get update
apt-get install -y git python3 python3-venv python3-pip rsync curl

if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -U pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/secrets"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/ADX-Regime-ATR-Executor.final.env" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "Created $APP_DIR/.env. Fill secrets before starting the service."
fi

cp "$APP_DIR/systemd/adx_regime_atr_executor.service" /etc/systemd/system/adx_regime_atr_executor.service
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

cd "$APP_DIR"
"$APP_DIR/.venv/bin/python" validate_setup.py || true

echo "Install complete. Edit $APP_DIR/.env, add Google service account if using file credentials, then:"
echo "  systemctl start $SERVICE_NAME"
echo "  systemctl status $SERVICE_NAME"
echo "Dashboard: http://SERVER_IP:8081"
