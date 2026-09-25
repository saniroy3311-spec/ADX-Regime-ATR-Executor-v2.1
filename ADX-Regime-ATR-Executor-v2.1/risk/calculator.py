"""
risk/calculator.py — ADX Regime ATR Executor
══════════════════════════════════════════════════════════════════════════════

SL calculation matches Pine Script exactly:
    stopDist = math.min(atr * atrMultActive, maxSLPoints)
    Trend: atrMultActive = 0.6
    Range: atrMultActive = 0.5

    longSL  = entryPrice - stopDist
    longTP  = entryPrice + stopDist * rrActive
    shortSL = entryPrice + stopDist
    shortTP = entryPrice - stopDist * rrActive

CHANGE: TrailState now includes trail_armed and best_price fields.
  Previously these were set as dynamic attributes on the TrailState
  instance in trail_loop.py. Declaring them explicitly in the dataclass
  is cleaner and avoids AttributeError if the fields are accessed before
  trail_loop.start() runs.

  trail_armed — True once activation_price is crossed (trail engine is live)
  best_price  — Running lowest (short) or highest (long) since trail armed
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import (
    TREND_ATR_MULT, RANGE_ATR_MULT,
    TREND_RR, RANGE_RR,
    MAX_SL_POINTS,
    COMMISSION_PCT, DELTA_CONTRACT_VALUE, DELTA_GST_RATE, FEE_MODEL_INCLUDE_GST,
)


# ─── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class RiskLevels:
    """
    Immutable snapshot of SL / TP levels for one trade.

    entry_price  — actual fill price
    sl           — initial stop loss  (entry_price ± ATR × atr_mult)
    tp           — take-profit price  (entry_price ± stopDist × R:R)
    stop_dist    — abs distance from entry_price to SL (pts)
    atr          — entry-bar ATR (used for Max SL and trail math)
    is_long      — True = long, False = short
    is_trend     — True = trend regime, False = range regime
    signal_close — retained for diagnostics only; Pine SL/TP anchor is entry_price
    """
    entry_price:     float
    sl:              float
    tp:              float
    stop_dist:       float
    atr:             float
    is_long:         bool
    is_trend:        bool
    entry_bar_open:  float = 0.0
    signal_close:    float = 0.0  # bar close that generated the signal


@dataclass
class TrailState:
    """
    Mutable per-trade trailing stop state.

    stage        — current trail stage (0 = pre-arm, 1–5 active)
    current_sl   — live stop loss level (initial SL → trail SL once armed)
    peak_price   — legacy field (kept for DB/recovery compat; use best_price)
    be_done      — True once breakeven activated (once per trade)
    max_sl_fired — True once Max SL circuit breaker fired
    trail_armed  — True once activation_price is crossed (Pine trail active)
    best_price   — running extreme since trail armed (min for short, max for long)
    """
    stage:         int   = 0
    current_sl:    float = 0.0
    peak_price:    float = 0.0
    be_done:       bool  = False
    max_sl_fired:  bool  = False
    # Trail engine runtime state (set/reset by trail_loop.start() each trade)
    trail_armed:   bool  = False
    best_price:    float = 0.0


# ─── Core helpers ──────────────────────────────────────────────────────────────

def calc_levels(
    entry_price:    float,
    atr:            float,
    is_long:        bool,
    is_trend:       bool,
    entry_bar_open: float = 0.0,
    signal_close:   float = 0.0,  # diagnostics only; not the SL/TP anchor
) -> RiskLevels:
    """
    Compute initial SL and TP — Pine-exact formula.

    Pine Script:
        entryPrice := strategy.position_avg_price
        stopDist   = math.min(atr * atrMultActive, maxSLPoints)
        shortSL    = entryPrice + stopDist
        shortTP    = entryPrice - stopDist * rrActive

    The supplied Pine file explicitly anchors SL/TP to strategy.position_avg_price.
    signal_close is kept only for audit/debug output and never anchors risk levels.
    """
    atr_mult  = TREND_ATR_MULT if is_trend else RANGE_ATR_MULT
    rr        = TREND_RR       if is_trend else RANGE_RR
    stop_dist = min(atr * atr_mult, MAX_SL_POINTS)

    anchor = entry_price

    if is_long:
        sl = anchor - stop_dist
        tp = anchor + stop_dist * rr
    else:
        sl = anchor + stop_dist
        tp = anchor - stop_dist * rr

    return RiskLevels(
        entry_price    = entry_price,
        sl             = sl,
        tp             = tp,
        stop_dist      = stop_dist,
        atr            = atr,
        is_long        = is_long,
        is_trend       = is_trend,
        entry_bar_open = entry_bar_open,
        signal_close   = signal_close if signal_close > 0 else entry_price,
    )


def recalc_levels_from_fill(risk: RiskLevels, fill_price: float) -> RiskLevels:
    """
    Shift SL / TP by the fill-vs-signal-close difference.
    Used ONLY in the startup recovery path — NOT for new live entries.
    """
    delta = fill_price - risk.entry_price
    return RiskLevels(
        entry_price    = fill_price,
        sl             = risk.sl  + delta,
        tp             = risk.tp  + delta,
        stop_dist      = risk.stop_dist,
        atr            = risk.atr,
        is_long        = risk.is_long,
        is_trend       = risk.is_trend,
        entry_bar_open = risk.entry_bar_open,
        signal_close   = risk.signal_close,
    )


def calc_real_pl(
    entry_price: float,
    exit_price:  float,
    is_long:     bool,
    qty:         int,
    entry_fee:   float | None = None,
    exit_fee:    float | None = None,
) -> float:
    """Return live-style net P&L in USD.

    Gross point P&L uses Delta's BTCUSD contract value. If Delta/ccxt reports
    actual paid commissions, pass them as ``entry_fee`` / ``exit_fee`` and they
    are used directly. Missing sides fall back to the configured taker model;
    the fallback optionally adds GST for a closer India cash result.
    """
    cv = DELTA_CONTRACT_VALUE
    raw_pl = (
        (exit_price - entry_price) * qty * cv if is_long
        else (entry_price - exit_price) * qty * cv
    )
    fee_mult = (1.0 + DELTA_GST_RATE) if FEE_MODEL_INCLUDE_GST else 1.0
    modeled_entry = entry_price * qty * cv * COMMISSION_PCT * fee_mult
    modeled_exit = exit_price * qty * cv * COMMISSION_PCT * fee_mult
    fee_in = modeled_entry if entry_fee is None else abs(float(entry_fee))
    fee_out = modeled_exit if exit_fee is None else abs(float(exit_fee))
    return raw_pl - fee_in - fee_out


def calc_gross_pl(
    entry_price: float,
    exit_price:  float,
    is_long:     bool,
    qty:         int,
) -> float:
    """
    Gross P&L — no commission. Delta inverse-perp formula:
        points = exitPx - entryPx  (long)
               = entryPx - exitPx  (short)
        gross  = points * qty * 0.001
    """
    points = (
        (exit_price - entry_price) if is_long
        else (entry_price - exit_price)
    )
    return points * qty * DELTA_CONTRACT_VALUE


def lots_to_btc(lots: int, price: float = 0.0) -> float:
    """Delta BTCUSD base exposure represented by API contracts/lots."""
    return lots * DELTA_CONTRACT_VALUE


def calc_pl_breakdown(
    entry_price: float,
    exit_price:  float,
    qty:         int,
    is_long:     bool,
) -> dict:
    """Return raw_pl, commission, net_pl. Used by gsheet.py."""
    cv = DELTA_CONTRACT_VALUE
    raw_pl = (
        (exit_price - entry_price) * qty * cv if is_long
        else (entry_price - exit_price) * qty * cv
    )
    fee_mult = (1.0 + DELTA_GST_RATE) if FEE_MODEL_INCLUDE_GST else 1.0
    comm = (entry_price + exit_price) * qty * cv * COMMISSION_PCT * fee_mult
    net_pl = raw_pl - comm
    return {"raw_pl": raw_pl, "commission": comm, "net_pl": net_pl}
