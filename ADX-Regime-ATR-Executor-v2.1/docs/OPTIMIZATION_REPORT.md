# ADX Regime ATR Executor v2.1 - Optimization Report

Date: 2026-09-25

## 1. Goal

The requested target is not merely to reproduce TradingView labels. The target is to make the Python execution bot and the Pine strategy use the same deterministic trading state machine for entry, initial stop, take profit, breakeven, staged trailing and hard-stop protection, while acknowledging that a real exchange fill can differ from a broker emulator.

## 2. Source profile used

The active screenshot settings were treated as the parameter source of truth:

- EMA Trend 50, EMA Fast 20
- ATR 14, DI 14, ADX smoothing 14, ADX EMA 5, RSI 14
- ADX trend threshold 22, ADX range threshold 18
- ATR filter 1.4, candle body filter 0.5 ATR, volume > SMA(volume,20)
- Trend RR 4.0, Range RR 2.5
- Trend initial SL 0.6 ATR, Range initial SL 0.5 ATR
- Stage 1: 0.8 / 0.50 / 0.40 ATR
- Stage 2: 1.5 / 0.40 / 0.30 ATR
- Stage 3: 2.5 / 0.30 / 0.25 ATR
- Stage 4: 4.0 / 0.20 / 0.15 ATR
- Stage 5: 6.0 / 0.15 / 0.10 ATR
- Breakeven 0.6 ATR
- Dynamic Max SL 1.5 ATR, Hard Max SL 500 points
- RSI 70 / 30
- Pine fixed quantity 0.1, commission 0.05 percent, slippage 2 ticks

## 3. Problems found and corrections

### A. Native Pine trailing unit mismatch

Original behavior:

`activePts = atr * multiplier`

`activeOff = atr * multiplier`

Those values were then passed to native `strategy.exit(... trail_points=..., trail_offset=...)`.

TradingView documents native `trail_points` and `trail_offset` in ticks. That means a value intended as a price distance can become a different distance after multiplication by `syminfo.mintick` inside the broker emulator.

Correction:

- Pine v2.1 no longer uses native trailing arguments for the staged ATR trail.
- Python and Pine both calculate activation/gap explicitly as ATR price distances.
- A legacy compatibility flag remains in Python only for regression comparison.

### B. Multiple exit-order interaction

The original strategy had four primary exit IDs plus separate breakeven exit IDs. Pine strategy exit orders participate in OCA/reduce behavior and multiple exits can reserve/reduce portions of a position.

Correction:

- Pine v2.1 uses one `Risk Exit` for the current position.
- Python keeps one `TrailState.current_sl` as the effective protective stop.
- Stop progression is monotonic: it can tighten, never loosen.

### C. ATR drift during an open trade

The original Pine recalculated stop distance, stage thresholds and trailing distances from the current ATR. This can change trade geometry after entry and make live reproduction unstable.

Correction:

- `entryATR` is frozen from the signal/entry snapshot.
- Initial SL, TP, breakeven, stage thresholds and trail distances use this frozen ATR for the life of the trade.

### D. Realtime Pine rollback/state persistence

Realtime Pine strategies can recalculate repeatedly on the open bar. Ordinary variables can be rolled back between executions.

Correction:

- Pine v2.1 uses `varip` for intrabar trade state.
- Stage, best price, breakeven and trail stop persist across realtime executions.

### E. Fill anchoring

A live market order rarely fills exactly at the signal close.

Correction:

- Pine uses `strategy.position_avg_price` after the simulated fill.
- Python reads the real exchange fill and recalculates SL/TP around that fill.
- Signal close is retained only for diagnostics.

### F. Historical/realtime execution mismatch

`calc_on_every_tick` alone does not guarantee that historical bars reproduce the exact realtime tick sequence. Current Pine v6 also exposes `calc_on_every_history_tick`.

Correction:

The reference strategy enables:

- `calc_on_every_tick=true`
- `calc_on_every_history_tick=true`
- `calc_on_order_fills=true`
- `process_orders_on_close=false`
- `use_bar_magnifier=true`

This improves model fidelity, but historical and live results can still differ because available historical tick detail and broker-emulator assumptions are not the live order book.

### G. Market precision and contract assumptions

Hardcoded decimal rounding and quantity assumptions are dangerous in live execution.

Correction:

- Startup loads market metadata from Delta through CCXT.
- Live mode validates configured contract value and price tick against the market metadata.
- Prices use `price_to_precision` with a tick-size fallback.
- Quantities use `amount_to_precision`.
- Strict metadata mismatch blocks live routing when enabled.

### H. Exchange-side crash protection

A pure local trailing loop leaves risk unmanaged if the process/server disconnects.

Correction:

- The bot can place one exchange-side initial SL+TP bracket after entry.
- Delta documents only one bracket order for an open position, so Python owns the dynamic trail and breakeven while the bracket is an emergency fallback.
- Dynamic local exits close with reduce-only market orders.

### I. Real PnL model

The TradingView model uses configured commission and slippage. Real Delta fills can differ.

Correction:

- Live PnL uses actual reported fees when available.
- Missing fee values fall back to the configured taker-fee model.
- The model supports optional GST in the live cash-PnL estimate.
- The code records intended stop level separately from actual fill behavior.


### J. Delta bracket cancellation / selector schema

Current Delta API documentation exposes `POST /orders/bracket` and `PUT /orders/bracket`, but not a `DELETE /orders/bracket` operation. It also specifies that only one of `product_id` or `product_symbol` should be sent for the relevant order schemas.

Correction:

- Position brackets are created with `product_id` only.
- The bot discovers the generated bracket legs through `GET /orders` using the documented `open,pending` states.
- It cancels those generated legs individually through the documented `DELETE /orders` endpoint.
- Normal bot exits use bracket-only cleanup so unrelated manual BTCUSD orders are not intentionally cancelled.
- A second bracket cleanup pass runs after a successful market close to cover cancellation/close races.
- The generic `cancel_all_orders()` helper now uses the documented `DELETE /orders/all` endpoint and is reserved for explicit clean-slate operations.

## 4. Entry logic retained

Trend Long:

`ADX > 22 AND EMA20 > EMA50 AND DI+ > DI- AND close > previous high AND filters`

Trend Short is the mirror condition.

Range Long:

`ADX < 18 AND RSI < 30 AND filters`

Range Short:

`ADX < 18 AND RSI > 70 AND filters`

Entries are evaluated on confirmed 30-minute candles. Order fill happens on the next available execution tick because `process_orders_on_close=false` is retained in Pine and the Python bot sends the market order after the confirmed-bar signal.

## 5. Corrected risk state machine

For a long position:

1. `stopDist = min(entryATR * atrMult, 500)`
2. `initialSL = fill - stopDist`
3. `TP = fill + stopDist * RR`
4. Breakeven activates only after favorable distance is strictly greater than `0.6 * entryATR`.
5. The highest unlocked stage is selected from the stage thresholds.
6. Stage 0 has no trail.
7. After a stage is unlocked, activation distance is `stagePts * entryATR`.
8. After activation, `bestPrice` is the highest live price.
9. Candidate trail stop = `bestPrice - stageOff * entryATR`.
10. Effective stop = max(initial SL, breakeven floor if active, trail stop if armed).
11. The effective stop never decreases.

Short positions use the mirrored formulas.

## 6. Delta BTCUSD mapping

Current Delta contract specifications list BTCUSD with:

- Lot size: 0.001 BTC
- Tick size: 0.5 USD
- Taker fee: 0.05 percent

Therefore the default `ALERT_QTY=100` represents 0.1 BTC of base exposure, matching the Pine fixed quantity of 0.1 for the intended mapping. Startup validation still checks live metadata rather than blindly trusting the configured values.

## 7. Tests completed

Local package validation:

- `pytest -q`: 14 passed, 1 skipped
- `python validate_setup.py`: passed in safe paper/testnet mode
- `python -m compileall -q .`: passed

The skipped test is environment-dependent and is not a failed core parity test.

## 8. What this package cannot truthfully guarantee

It cannot guarantee that live fills or PnL will exactly equal the TradingView Strategy Tester. Real trading adds spread, order-book depth, network latency, exchange latency, partial fills, rejected orders, outages, fee changes and data-source differences.

The TradingView screenshot showing the old strategy result is also not a valid expected result for the corrected v2.1 strategy because the trailing implementation and ATR behavior were intentionally fixed. Re-run the corrected Pine on the same chart/date range to establish the new reference result.

## 9. Recommended validation sequence before production

1. Add the corrected Pine v2.1 to the same Delta BTCUSD.P 30m chart.
2. Confirm the input values exactly match the profile above.
3. Export its trade list for the desired date range.
4. Run Python paper mode against the same market source.
5. Compare signal timestamp, direction, fill, entry ATR, initial SL, TP, BE activation, each stage transition, trail stop and exit reason trade-by-trade.
6. Run Delta testnet live-tick execution.
7. Only after those checks, decide whether to enable production live routing.

## 10. Research references

TradingView strategy trailing stops and execution:
https://www.tradingview.com/pine-script-docs/concepts/strategies/

TradingView strategy declaration parameters:
https://www.tradingview.com/pine-script-docs/language/declaration-statements/

Delta Exchange API / bracket orders:
https://docs.delta.exchange/

Delta BTCUSD contract specifications:
https://prod-mobile-india.delta.exchange/contracts
