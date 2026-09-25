# Live Account / Paper Mode / Google Sheets Verification

Release: ADX Regime ATR Executor v2.1
Date: 2026-09-25

Local core validation for this release:

- `pytest -q`: 14 passed, 1 skipped.
- `python validate_setup.py`: passed in safe paper/testnet mode.
- `python -m compileall -q .`: passed.

Network-bound exchange authentication and Google Sheets writes are not certified by the local unit test suite because they require the user's credentials and account permissions.

Recommended integration sequence:

1. Copy `.env.example` to `.env`.
2. Keep `EXECUTION_MODE=paper`.
3. Choose testnet or production market data deliberately with `DELTA_TESTNET`.
4. Add Delta credentials only if the chosen endpoint/account operation requires them.
5. Enable Google Sheets only after adding the Sheet ID and service-account credentials.
6. Run `python validate_setup.py`.
7. Run `python scripts/verify_integrations.py`.
8. Run `python main.py` and inspect the order/trade logs.
9. Compare bot state transitions against the corrected Pine script before enabling real order routing.
