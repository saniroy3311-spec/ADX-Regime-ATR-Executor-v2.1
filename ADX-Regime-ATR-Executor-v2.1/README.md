# ADX Regime ATR Executor v2.1

ADX Regime ATR Executor is a Python/Delta Exchange execution bot paired with a corrected Pine Script v6 reference strategy. The project is built around the supplied ADX/EMA/RSI strategy and the active TradingView settings shown in the screenshots.

## What is included

- `pine/ADX_Regime_ATR_Execution_Engine_v2_1.pine` - corrected TradingView reference.
- `main.py` - live/paper trading runner.
- `monitor/trail_loop.py` - live risk state machine.
- `orders/manager.py` - Delta order execution and emergency bracket protection.
- `parity_backtest.py` - offline trade-sequence comparison tool.
- `dashboard/` - local monitoring dashboard.
- `validate_setup.py` and `tests/` - configuration and core parity checks.
- `ADX_Regime_ATR_Executor.exe` - Windows x64 launcher when included in a release package.

## Strategy profile

The v2.1 defaults match the active settings supplied with the project:

| Setting | Value |
|---|---:|
| Timeframe | 30m |
| EMA Fast / Trend | 20 / 50 |
| ATR / DI / ADX smoothing / ADX EMA / RSI | 14 / 14 / 14 / 5 / 14 |
| ADX Trend / Range threshold | 22 / 18 |
| ATR volatility filter | 1.4 |
| Candle body filter | 0.5 ATR |
| Trend / Range RR | 4.0 / 2.5 |
| Trend / Range initial SL | 0.6 ATR / 0.5 ATR |
| Breakeven | > 0.6 entry ATR |
| Max SL | min(1.5 entry ATR, 500 points) |
| Stage 1 | trigger 0.8 ATR, activation 0.50 ATR, gap 0.40 ATR |
| Stage 2 | trigger 1.5 ATR, activation 0.40 ATR, gap 0.30 ATR |
| Stage 3 | trigger 2.5 ATR, activation 0.30 ATR, gap 0.25 ATR |
| Stage 4 | trigger 4.0 ATR, activation 0.20 ATR, gap 0.15 ATR |
| Stage 5 | trigger 6.0 ATR, activation 0.15 ATR, gap 0.10 ATR |
| TradingView commission | 0.05 percent per fill |
| TradingView slippage | 2 ticks |

## Main corrections from the supplied Pine

The original Pine multiplies ATR by the trail multipliers and passes those values directly to native `trail_points` and `trail_offset`. TradingView defines those native arguments in ticks, not price units. v2.1 replaces that ambiguous/native behavior with an explicit custom trail state machine using ATR price distances in both Pine and Python.

The old strategy also issued primary `strategy.exit()` orders and separate breakeven exit IDs. v2.1 uses one effective protective stop in Pine and one matching state in Python so the stop progresses only in the favorable direction:

`Initial SL -> Breakeven floor -> Stage 1 -> Stage 2 -> Stage 3 -> Stage 4 -> Stage 5`

Entry ATR is frozen when the signal/entry is created. SL, TP, breakeven thresholds, stage triggers and trail gaps therefore do not drift because ATR changes later in the trade.

The Python bot anchors final risk levels to the actual exchange fill, validates live market precision/contract metadata, rounds order prices and quantities through exchange precision, and keeps an exchange-side emergency bracket for disconnect/crash protection. Bracket cleanup follows Delta's documented active-order flow: the generated bracket legs are discovered and cancelled individually, rather than calling a non-existent bracket-delete endpoint or cancelling unrelated product orders during a normal exit.

See `docs/OPTIMIZATION_REPORT.md` for the complete audit.

## Pine and live execution model

Entries are generated from a confirmed strategy candle. The Pine v6 reference uses `calc_on_every_tick=true`, `calc_on_every_history_tick=true`, `calc_on_order_fills=true`, `process_orders_on_close=false`, and Bar Magnifier. The Python executor receives the actual fill and then manages SL/TP/breakeven/stage trailing continuously from live prices.

No live exchange can be expected to reproduce every TradingView fill exactly. Spread, latency, partial fills, order-book depth, fees, exchange outages and differences in market data can change the real execution price. Compare state transitions and intended stop/target levels first, then compare fills separately.

## Safe first run

The canonical `.env.example` is intentionally safe:

```text
EXECUTION_MODE=paper
DELTA_TESTNET=true
LIVE_TRADING_ENABLED=false
PINE_PARITY_MODE=true
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python validate_setup.py
pytest -q
python main.py
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe validate_setup.py
.\.venv\Scripts\python.exe main.py
```

If you use the supplied Windows launcher, keep the EXE in the project folder and run `ADX_Regime_ATR_Executor.exe`. It creates `.env` from `.env.example` if needed, prepares `.venv`, installs dependencies on first run, validates the setup, and starts the bot. Python 3.11+ must be installed for the launcher package.

## Live mode safety gate

Real production order routing requires all of these to be intentional:

```text
EXECUTION_MODE=live
DELTA_TESTNET=false
LIVE_TRADING_ENABLED=true
```

Do not change those values until paper/testnet behavior has been checked trade-by-trade against the corrected Pine script. The bot does not guarantee profit and the historical screenshot is not a guarantee of future or live results.

## Quantity mapping for Delta BTCUSD

The TradingView strategy uses a fixed quantity of `0.1`. Delta currently specifies BTCUSD as 0.001 BTC per contract, so the default live mapping is 100 contracts = 0.1 BTC exposure. Startup metadata validation checks the exchange values before live order routing.

## Parity test workflow

Export OHLCV from the same exchange/symbol/timeframe used on TradingView with columns:

```text
timestamp,open,high,low,close,volume
```

Then run:

```bash
python parity_backtest.py data/ohlcv.csv --out data/parity_trades.csv
```

If you have a normalized TradingView trade export containing `entry_ts` and `exit_ts`:

```bash
python parity_backtest.py data/ohlcv.csv --out data/parity_trades.csv --expected-trades data/tradingview_trades.csv
```

An OHLCV backtest is still an approximation of tick ordering. Use the same market source and the most granular replay data available for serious parity work.

## Validation commands

```bash
python validate_setup.py
pytest -q
python -m compileall -q .
```

The packaged v2.1 release was validated with 14 passing core/release tests and 1 environment-dependent test skipped.

## Research references

- TradingView Pine strategy documentation: https://www.tradingview.com/pine-script-docs/concepts/strategies/
- TradingView declaration parameters: https://www.tradingview.com/pine-script-docs/language/declaration-statements/
- Delta Exchange API documentation: https://docs.delta.exchange/
- Delta Exchange BTCUSD contract specifications: https://prod-mobile-india.delta.exchange/contracts

