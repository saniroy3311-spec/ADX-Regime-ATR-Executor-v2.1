# GitHub -> Hostinger VPS deployment

## Recommended: Hostinger GitHub Actions deployment

1. Create a private GitHub repository and push this project to branch `main`.
2. In Hostinger hPanel, use a Docker-capable VPS (Hostinger's Docker template is supported).
3. Generate a Hostinger API key and obtain the VPS VM ID.
4. In GitHub -> Settings -> Secrets and variables -> Actions, configure:

### Secrets
- `HOSTINGER_API_KEY`
- `PERSONAL_ACCESS_TOKEN` (needed for a private repository)
- `DELTA_API_KEY`
- `DELTA_API_SECRET`
- `GSHEET_SPREADSHEET_ID`
- `GSHEET_CREDENTIALS_JSON`
- `DASHBOARD_USER`
- `DASHBOARD_PASS`

### Variables
- `HOSTINGER_VM_ID`
- `BOT_NAME=ADX Regime ATR Executor`
- `BOT_VERSION=2.1.0-parity`
- `PINE_PARITY_MODE=true`
- `LIVE_TICK_RISK_ENGINE=true`
- `EXECUTION_MODE=live`
- `LIVE_TRADING_ENABLED=false`
- `DELTA_TESTNET=false`
- `SYMBOL=BTC/USD:USD`
- `DELTA_PRODUCT_SYMBOL=BTCUSD`
- `ALERT_QTY=100`
- `CANDLE_TIMEFRAME=30m`
- `EMA_FAST_LEN=20`
- `EMA_TREND_LEN=50`
- `GSHEET_ENABLED=true`
- `GSHEET_AUTO_CREATE=true`

A GitHub Actions deployment can be added if desired. This release does not require GitHub Actions; the systemd and Docker paths below are self-contained.

Keep `LIVE_TRADING_ENABLED=false` for the first deployment. After integration verification, change it deliberately to `true` in GitHub Actions variables and redeploy.

## Alternative: plain Ubuntu VPS + systemd

On the VPS, create a GitHub deploy key for the private repository, then run:

```bash
REPO_URL=git@github.com:OWNER/REPO.git bash deploy/hostinger_systemd_install.sh
```

Edit `/opt/adx-regime-atr-executor/.env`, then validate and start:

```bash
cd /opt/adx-regime-atr-executor
.venv/bin/python validate_setup.py
.venv/bin/python scripts/verify_integrations.py
systemctl start adx_regime_atr_executor
systemctl status adx_regime_atr_executor
journalctl -u adx_regime_atr_executor -f
```

Dashboard defaults to port 8081.
