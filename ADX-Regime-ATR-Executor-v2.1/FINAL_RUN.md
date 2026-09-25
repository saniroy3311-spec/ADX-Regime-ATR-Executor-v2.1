# ADX Regime ATR Executor v2.1 - First Run

The package is safe by default. `.env.example` starts in paper mode on Delta testnet and does not send live orders.

## Windows launcher

Keep `ADX_Regime_ATR_Executor.exe` in the repository root and run it. The launcher:

1. Creates `.env` from `.env.example` if `.env` does not exist.
2. Finds Python 3.11+.
3. Creates `.venv` if needed.
4. Installs `requirements.txt` on the first run.
5. Runs `validate_setup.py`.
6. Starts `main.py`.

## Manual run

```bash
cp .env.example .env
python validate_setup.py
pytest -q
python main.py
```

## Real production routing

Production orders require all three settings to be changed intentionally:

```text
EXECUTION_MODE=live
DELTA_TESTNET=false
LIVE_TRADING_ENABLED=true
```

Keep paper/testnet mode until the corrected Pine and bot have been compared trade-by-trade. Live results can differ from TradingView because of real fills, spread, latency, liquidity, fees and exchange behavior.
