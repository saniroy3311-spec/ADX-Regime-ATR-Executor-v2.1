"""OHLC parity backtester for ADX Regime ATR Execution Engine v2.1.

This backtester shares the Python signal/risk functions used by the live executor.
It models the matching Pine v6 strategy's key execution rules:
- confirmed-bar entries; market fill on the next available tick/open,
- actual fill anchoring for SL/TP,
- frozen entry ATR,
- tick-style breakeven and 5-stage trailing state,
- one ratcheting protective stop plus one TP,
- TradingView-style configured slippage and commission.

A plain OHLC CSV cannot reproduce TradingView's 2026 historical-tick detail or a
real exchange order book. The intrabar path therefore uses TradingView's classic
OHLC broker-emulator path heuristic. For the strongest comparison, feed the same
symbol/timeframe data used on TradingView and compare exported trades.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
import json
import math

import pandas as pd

from config import (
    ADX_TREND_TH, ADX_RANGE_TH, FILTER_ATR_MULT, FILTER_BODY_MULT,
    FILTER_VOL_ENABLED, FILTER_VOL_MULT, PINE_MINTICK, PINE_SLIPPAGE_TICKS,
    PINE_POINT_VALUE, PINE_ORDER_QTY, COMMISSION_PCT, MAX_SL_MULT,
    MAX_SL_POINTS,
)
from indicators.engine import compute_full_series, IndicatorSnapshot, evaluate, SignalType
from risk.calculator import calc_levels
from strategy_logic import upgrade_trail_stage, get_trail_params, should_trigger_be


@dataclass
class Trade:
    trade_no: int
    signal_type: str
    is_long: bool
    signal_ts: int
    entry_ts: int
    entry_price: float
    exit_ts: int
    exit_price: float
    exit_reason: str
    max_stage: int
    entry_atr: float
    gross_pnl: float
    commission: float
    net_pnl: float


@dataclass
class Position:
    signal_type: str
    is_long: bool
    is_trend: bool
    signal_ts: int
    entry_ts: int
    entry_price: float
    qty: float
    point_value: float
    entry_atr: float
    initial_sl: float
    tp: float
    effective_stop: float
    stage: int = 0
    max_stage: int = 0
    be_done: bool = False
    trail_armed: bool = False
    best_price: Optional[float] = None
    trail_stop: Optional[float] = None


SLIP = PINE_SLIPPAGE_TICKS * PINE_MINTICK


def _market_fill(price: float, is_buy: bool) -> float:
    return price + SLIP if is_buy else price - SLIP


def _stop_fill(stop: float, is_long: bool) -> float:
    # Exit long = sell market/stop; adverse slippage is lower. Short is higher.
    return stop - SLIP if is_long else stop + SLIP


def _pnl(entry: float, exit_: float, is_long: bool, qty: float, point_value: float) -> tuple[float, float, float]:
    points = (exit_ - entry) if is_long else (entry - exit_)
    gross = points * qty * point_value
    # Pine commission_value=0.05 percent applies to each fill. No GST is added to
    # this parity calculation; live cash accounting can include actual Delta fees.
    fees = (entry + exit_) * qty * point_value * COMMISSION_PCT
    return gross, fees, gross - fees


def _row_to_snap(row: pd.Series, prev: pd.Series) -> IndicatorSnapshot:
    atr = float(row.atr)
    atr_sma = float(row.atr_sma)
    vol_sma = float(row.vol_sma)
    vol = float(row.volume)
    atr_ok = atr < atr_sma * FILTER_ATR_MULT
    vol_ok = (vol > vol_sma * FILTER_VOL_MULT) if FILTER_VOL_ENABLED else True
    body_ok = abs(float(row.close) - float(row.open)) > atr * FILTER_BODY_MULT
    adx = float(row.adx)
    return IndicatorSnapshot(
        ema_trend=float(row.ema200), ema_fast=float(row.ema50), atr=atr,
        rsi=float(row.rsi), dip=float(row.dip), dim=float(row.dim),
        adx=adx, adx_raw=float(row.adx_raw), vol_sma=vol_sma,
        atr_sma=atr_sma, trend_regime=adx > ADX_TREND_TH,
        range_regime=adx < ADX_RANGE_TH,
        filters_ok=bool(atr_ok and vol_ok and body_ok), atr_ok=bool(atr_ok),
        vol_ok=bool(vol_ok), body_ok=bool(body_ok), open=float(row.open),
        high=float(row.high), low=float(row.low), close=float(row.close),
        volume=vol, prev_high=float(prev.high), prev_low=float(prev.low),
        timestamp=int(row.timestamp),
    )


def _profit(pos: Position, price: float) -> float:
    return price - pos.entry_price if pos.is_long else pos.entry_price - price


def _recompute_effective_stop(pos: Position) -> None:
    stop = pos.initial_sl
    if pos.be_done:
        stop = max(stop, pos.entry_price) if pos.is_long else min(stop, pos.entry_price)
    if pos.trail_armed and pos.trail_stop is not None:
        stop = max(stop, pos.trail_stop) if pos.is_long else min(stop, pos.trail_stop)
    # Never loosen protection.
    pos.effective_stop = max(pos.effective_stop, stop) if pos.is_long else min(pos.effective_stop, stop)


def _update_state(pos: Position, price: float) -> None:
    """Run the Pine risk state machine at one available price tick."""
    profit = _profit(pos, price)
    new_stage = upgrade_trail_stage(pos.stage, profit, pos.entry_atr)
    if new_stage > pos.stage:
        pos.stage = new_stage
        pos.max_stage = max(pos.max_stage, new_stage)

    if (not pos.trail_armed) and pos.stage > 0:
        activation, _ = get_trail_params(pos.stage, pos.entry_atr)
        if profit >= activation:
            pos.trail_armed = True
            pos.best_price = price

    if pos.trail_armed:
        if pos.best_price is None:
            pos.best_price = price
        elif pos.is_long:
            pos.best_price = max(pos.best_price, price)
        else:
            pos.best_price = min(pos.best_price, price)
        _, offset = get_trail_params(pos.stage, pos.entry_atr)
        candidate = pos.best_price - offset if pos.is_long else pos.best_price + offset
        if pos.trail_stop is None:
            pos.trail_stop = candidate
        elif pos.is_long:
            pos.trail_stop = max(pos.trail_stop, candidate)
        else:
            pos.trail_stop = min(pos.trail_stop, candidate)

    if (not pos.be_done) and should_trigger_be(profit, pos.entry_atr):
        pos.be_done = True

    _recompute_effective_stop(pos)


def _stop_reason(pos: Position) -> str:
    if pos.trail_armed and pos.trail_stop is not None:
        if abs(pos.effective_stop - pos.trail_stop) <= max(PINE_MINTICK, 1e-9):
            return "Trail SL"
    if pos.be_done and abs(pos.effective_stop - pos.entry_price) <= max(PINE_MINTICK, 1e-9):
        return "Breakeven"
    return "Initial SL"


def _gap_exit(pos: Position, open_: float) -> Optional[tuple[float, str]]:
    if pos.is_long:
        if open_ <= pos.effective_stop:
            return _stop_fill(open_, True), _stop_reason(pos) + " gap"
        if open_ >= pos.tp:
            return open_, "TP gap"
    else:
        if open_ >= pos.effective_stop:
            return _stop_fill(open_, False), _stop_reason(pos) + " gap"
        if open_ <= pos.tp:
            return open_, "TP gap"
    return None


def _walk_segment(pos: Position, a: float, b: float) -> Optional[tuple[float, str]]:
    """Process one monotonic OHLC path segment."""
    if a == b:
        _update_state(pos, b)
        return None

    favorable = b > a if pos.is_long else b < a
    if favorable:
        # TP is an already-working limit order; it fills before a later endpoint.
        if pos.is_long and a < pos.tp <= b:
            return pos.tp, "TP"
        if (not pos.is_long) and b <= pos.tp < a:
            return pos.tp, "TP"
        _update_state(pos, b)
        return None

    # Adverse segment: the protective stop is already working from the previous
    # available tick. State cannot improve while price moves against the trade.
    stop = pos.effective_stop
    if pos.is_long and b <= stop < a:
        return _stop_fill(stop, True), _stop_reason(pos)
    if (not pos.is_long) and a < stop <= b:
        return _stop_fill(stop, False), _stop_reason(pos)

    # Max-SL is a fail-safe market-close condition. Under the supplied profile the
    # initial SL is tighter, but model it for changed parameters as well.
    max_dist = min(pos.entry_atr * MAX_SL_MULT, MAX_SL_POINTS)
    max_level = pos.entry_price - max_dist if pos.is_long else pos.entry_price + max_dist
    if pos.is_long and b <= max_level < a:
        return _stop_fill(max_level, True), "Max SL"
    if (not pos.is_long) and a < max_level <= b:
        return _stop_fill(max_level, False), "Max SL"

    _update_state(pos, b)
    return None


def _process_bar(pos: Position, open_: float, high: float, low: float, close: float, *, new_entry: bool) -> Optional[tuple[float, str]]:
    if not new_entry:
        gap = _gap_exit(pos, open_)
        if gap:
            return gap
    _update_state(pos, open_)

    # TradingView's classic OHLC broker-emulator path for a plain OHLC replay.
    path = [open_, high, low, close] if abs(open_ - high) < abs(open_ - low) else [open_, low, high, close]
    for a, b in zip(path, path[1:]):
        hit = _walk_segment(pos, a, b)
        if hit:
            return hit
    return None


def run(df: pd.DataFrame) -> list[Trade]:
    data = compute_full_series(df).reset_index(drop=True)
    trades: list[Trade] = []
    pos: Optional[Position] = None
    pending_entry = None  # (Signal, IndicatorSnapshot)
    trade_no = 0

    for i in range(1, len(data)):
        row = data.iloc[i]
        prev = data.iloc[i - 1]
        ts = int(row.timestamp)
        o, h, l, c = map(float, (row.open, row.high, row.low, row.close))
        exited_this_bar = False
        new_entry = False

        if pos is None and pending_entry is not None:
            sig, sig_snap = pending_entry
            fill = _market_fill(o, is_buy=sig.is_long)
            risk = calc_levels(fill, sig_snap.atr, sig.is_long, sig.is_trend,
                               entry_bar_open=o, signal_close=sig_snap.close)
            pos = Position(
                signal_type=sig.signal_type.value,
                is_long=sig.is_long,
                is_trend=sig.is_trend,
                signal_ts=int(sig_snap.timestamp),
                entry_ts=ts,
                entry_price=fill,
                qty=PINE_ORDER_QTY,
                point_value=PINE_POINT_VALUE,
                entry_atr=sig_snap.atr,
                initial_sl=risk.sl,
                tp=risk.tp,
                effective_stop=risk.sl,
            )
            pending_entry = None
            new_entry = True

        if pos is not None:
            hit = _process_bar(pos, o, h, l, c, new_entry=new_entry)
            if hit:
                exit_px, reason = hit
                gross, fees, net = _pnl(pos.entry_price, exit_px, pos.is_long, pos.qty, pos.point_value)
                trade_no += 1
                trades.append(Trade(
                    trade_no=trade_no,
                    signal_type=pos.signal_type,
                    is_long=pos.is_long,
                    signal_ts=pos.signal_ts,
                    entry_ts=pos.entry_ts,
                    entry_price=pos.entry_price,
                    exit_ts=ts,
                    exit_price=exit_px,
                    exit_reason=reason,
                    max_stage=pos.max_stage,
                    entry_atr=pos.entry_atr,
                    gross_pnl=gross,
                    commission=fees,
                    net_pnl=net,
                ))
                pos = None
                exited_this_bar = True

        # Same-bar re-entry is deliberately blocked, matching the Pine guard.
        if pos is None and not exited_this_bar:
            snap = _row_to_snap(row, prev)
            sig = evaluate(snap, has_position=False)
            if sig.signal_type != SignalType.NONE:
                pending_entry = (sig, snap)

    return trades


def summary(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0}
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [t.net_pnl for t in trades if t.net_pnl <= 0]
    gp = sum(wins)
    gl = -sum(losses)
    return {
        "trades": len(trades),
        "net_pnl": sum(t.net_pnl for t in trades),
        "gross_profit": gp,
        "gross_loss": gl,
        "win_rate": 100 * len(wins) / len(trades),
        "profit_factor": gp / gl if gl > 0 else float("inf"),
    }


def compare(bot: pd.DataFrame, expected_path: str) -> dict:
    exp = pd.read_csv(expected_path)
    result = {"expected_trades": len(exp), "bot_trades": len(bot)}
    for col in ("entry_ts", "exit_ts"):
        if col in exp.columns and col in bot.columns:
            a = set(pd.to_numeric(exp[col], errors="coerce").dropna().astype("int64"))
            b = set(pd.to_numeric(bot[col], errors="coerce").dropna().astype("int64"))
            result[f"{col}_missing_in_bot"] = len(a - b)
            result[f"{col}_extra_in_bot"] = len(b - a)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="ADX Regime ATR TradingView parity backtest")
    ap.add_argument("ohlcv_csv", help="CSV with timestamp,open,high,low,close,volume")
    ap.add_argument("--out", default="data/parity_trades.csv")
    ap.add_argument("--expected-trades", default=None, help="Optional TradingView exported trade CSV")
    args = ap.parse_args()

    df = pd.read_csv(args.ohlcv_csv)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Missing columns: {sorted(missing)}")
    trades = run(df)
    out = pd.DataFrame([asdict(t) for t in trades])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    report = summary(trades)
    if args.expected_trades:
        report["comparison"] = compare(out, args.expected_trades)
    print(json.dumps(report, indent=2))
    print(f"Trades written to {args.out}")


if __name__ == "__main__":
    main()
