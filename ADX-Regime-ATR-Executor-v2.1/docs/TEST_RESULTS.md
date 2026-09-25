# Test Results - v2.1

Date: 2026-09-25

Commands executed from the repository root:

```text
pytest -q
python validate_setup.py
python -m compileall -q .
```

Result:

```text
14 passed, 1 skipped
validate_setup.py: PASS
compileall: PASS
```

Core tests cover:

- Active EMA/ADX/filter/RR/ATR/BE/stage defaults.
- Trend and range SL/TP anchored to actual fill.
- Corrected ATR price-unit trail distances.
- Stage ratchet behavior.
- Strict `>` breakeven trigger.
- Trend signal evaluation.
- Dynamic/hard Max-SL threshold.
- Pine-style EMA recursive seed.
- Live-tick risk configuration.
- Tick-driven stage upgrade, BE activation, trail arming and non-loosening stop.
- Delta contract PnL/commission fallback model.

The skipped test is environment-dependent. It does not represent a core logic failure.

## Delta bracket API audit

The release was checked against the current Delta Exchange API documentation. The position bracket uses `POST /orders/bracket`; bracket legs are discovered with `GET /orders` and cancelled with `DELETE /orders`. The generic all-order cleanup helper uses `DELETE /orders/all`. The release no longer depends on an undocumented `DELETE /orders/bracket` route.
