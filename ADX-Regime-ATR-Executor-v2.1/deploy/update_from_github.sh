#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/adx-regime-atr-executor}"
BRANCH="${BRANCH:-main}"
cd "$APP_DIR"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
"$APP_DIR/.venv/bin/pip" install -r requirements.txt
systemctl restart adx_regime_atr_executor
systemctl --no-pager --full status adx_regime_atr_executor
