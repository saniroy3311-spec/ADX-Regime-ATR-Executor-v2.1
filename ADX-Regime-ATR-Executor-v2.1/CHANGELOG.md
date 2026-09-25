# Changelog

## v2.1.0-parity - 2026-09-25

- Renamed the strategy/bot to ADX Regime ATR Execution Engine / Executor.
- Updated defaults to the supplied active TradingView profile (EMA 20/50 and matching ADX/ATR/RR/trail values).
- Replaced native Pine tick-unit trailing parameters with a custom ATR price-distance trail state machine.
- Added frozen entry ATR and actual-fill SL/TP anchoring.
- Consolidated Pine exits to one effective protective stop.
- Added `varip` intrabar state, `calc_on_order_fills`, Bar Magnifier and current historical-tick strategy support.
- Added live-tick stage/BE/max-SL parity behavior in Python.
- Added market metadata/precision checks and exchange-valid price/quantity formatting.
- Added emergency Delta bracket protection and actual-fee aware PnL handling.
- Added parity backtester, validation checks and expanded unit tests.
- Added Windows x64 launcher.
- Safe defaults remain paper + testnet with production routing locked behind an explicit second switch.
