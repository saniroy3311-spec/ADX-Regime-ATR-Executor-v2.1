# Pine / Python Parity Notes v2.1

The parity target is the corrected custom risk state machine, not the old native Pine trailing-order implementation.

1. Signal candles must come from the same exchange, symbol and timeframe used on TradingView.
2. Entry signals are evaluated only from confirmed candles.
3. Both engines freeze entry ATR for the open trade.
4. Both engines anchor SL and TP to the filled entry price.
5. Stage upgrades ratchet upward and never downgrade.
6. Breakeven is a floor/ceiling on the active stop and never loosens risk.
7. Stage 0 has no trail.
8. Trail activation and gap are ATR price distances, not native Pine tick-unit arguments.
9. best price never resets on a stage change.
10. Only one effective protective stop exists in the corrected model.
11. Python rounds real orders to exchange precision and records the actual exchange fill.
12. An emergency exchange bracket is a safety fallback and can cause a live result to differ from Pine if it fires during a disconnect.
13. TradingView historical execution, even with improved historical tick processing, is not the same as a live exchange order book.
14. Compare state transitions and intended prices before comparing final PnL.
