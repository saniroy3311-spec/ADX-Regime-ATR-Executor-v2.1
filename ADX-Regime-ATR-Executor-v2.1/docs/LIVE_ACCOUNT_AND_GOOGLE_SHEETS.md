# ADX Regime ATR Executor — Live Account + Google Sheets

This build has two execution modes.

## Recommended first run: production market + paper orders

Use your Delta India production API credentials, but do not place real orders yet:

```env
DELTA_TESTNET=false
EXECUTION_MODE=paper
LIVE_TRADING_ENABLED=false
```

The bot reads the production market, evaluates the EMA 20/50 strategy, and simulates entry/exit fills from live prices. No order mutation is sent to Delta. Entry/exit events and completed trades are written to Google Sheets.

## Real production orders

Only after the paper run is behaving correctly:

```env
DELTA_TESTNET=false
EXECUTION_MODE=live
LIVE_TRADING_ENABLED=true
```

A production trading API key must have Trading permission and its server public IP must be whitelisted in Delta Exchange. Keep the key and secret only in `.env`; never commit or paste them into source files.

## Google Sheets setup

1. Create a Google Cloud project.
2. Enable Google Sheets API and Google Drive API.
3. Create a service account and download its JSON key.
4. Put it at `./secrets/google-service-account.json` (or another private path).
5. Create/open the target Google Sheet.
6. Share the Sheet with the service-account email as **Editor**.
7. Copy the spreadsheet ID from the Sheet URL into `GSHEET_SPREADSHEET_ID`.

Recommended settings:

```env
GSHEET_ENABLED=true
GSHEET_AUTO_CREATE=true
GSHEET_SPREADSHEET_ID=YOUR_GOOGLE_SHEET_ID
GSHEET_CREDENTIALS_FILE=./secrets/google-service-account.json
GSHEET_CREDENTIALS_JSON=
```

The bot creates/maintains:

- `Order Log` — entry fills, exit fills/recovered exits, entry failures, integration-test events.
- `Trade Log` — one row per completed trade including entry/exit, SL, TP, ATR, points, commission, net P/L, exit reason, and trail stage.
- `Dashboard` — summary formulas based on the Trade Log and Order Log.

## Preflight check — no trade is sent

From the repo root:

```bash
python scripts/verify_integrations.py
```

It checks market connectivity, authenticated account access (when credentials are present), Google Sheets access, and appends an `INTEGRATION_TEST` row to `Order Log`. It never places/cancels an exchange order.

## Start the bot

```bash
python validate_setup.py
python main.py
```

With `EXECUTION_MODE=paper`, the first valid strategy signal is simulated and logged. With `EXECUTION_MODE=live` plus `LIVE_TRADING_ENABLED=true`, the first valid strategy signal can place a real Delta order.
