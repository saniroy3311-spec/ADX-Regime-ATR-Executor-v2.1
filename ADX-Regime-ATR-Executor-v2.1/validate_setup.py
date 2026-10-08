"""Pre-flight validation for the uploaded BTCUSDT Sniper v6 Pine profile."""
from __future__ import annotations
import config

EXPECTED = {
    "EMA_FAST_LEN": 20,
    "EMA_TREND_LEN": 50,
    "ATR_LEN": 14,
    "DI_LEN": 14,
    "ADX_SMOOTH": 14,
    "ADX_EMA": 5,
    "RSI_LEN": 14,
    "ADX_TREND_TH": 15.0,
    "ADX_RANGE_TH": 14.0,
    "FILTER_ATR_MULT": 1.5,
    "FILTER_BODY_MULT": 0.1,
    "TREND_RR": 5.3,
    "RANGE_RR": 2.3,
    "TREND_ATR_MULT": 1.2,
    "RANGE_ATR_MULT": 0.8,
    "MAX_SL_MULT": 1.8,
    "MAX_SL_POINTS": 350.0,
    "BE_MULT": 0.6,
    "RSI_OB": 70,
    "RSI_OS": 20,
}
EXPECTED_STAGES = [
    (1.1, 0.10, 0.50),
    (0.6, 0.10, 0.50),
    (2.3, 0.20, 0.35),
    (4.1, 0.20, 0.15),
    (6.0, 0.15, 0.10),
]


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    if config.PINE_PROFILE != "sniper_v6_exact":
        errors.append(f"PINE_PROFILE={config.PINE_PROFILE!r}; expected 'sniper_v6_exact'")

    for key, expected in EXPECTED.items():
        actual = getattr(config, key)
        if actual != expected:
            errors.append(f"{key}={actual!r}; TradingView screenshot requires {expected!r}")

    if config.TRAIL_STAGES != EXPECTED_STAGES:
        errors.append(f"TRAIL_STAGES={config.TRAIL_STAGES!r}; expected {EXPECTED_STAGES!r}")

    if not config.PINE_PARITY_MODE:
        errors.append("PINE_PARITY_MODE must be true")
    if not config.LIVE_TICK_RISK_ENGINE:
        errors.append("LIVE_TICK_RISK_ENGINE must be true (Pine calc_on_every_tick=true)")
    if not config.TRAIL_LEGACY_TV_TICK_SEMANTICS:
        errors.append("Native Pine strategy.exit trail requires tick semantics")
    if not config.DYNAMIC_REALTIME_ATR:
        errors.append("DYNAMIC_REALTIME_ATR must be true (ta.atr recalculates on realtime ticks)")
    if not config.PINE_REALTIME_VAR_ROLLBACK:
        errors.append("PINE_REALTIME_VAR_ROLLBACK must be true (`trailStage` uses var, not varip)")
    if config.TRAIL_STAGE_UPDATE_MODE != "tick":
        errors.append("TRAIL_STAGE_UPDATE_MODE must be tick")
    if config.MAX_SL_EVAL_MODE != "tick":
        errors.append("MAX_SL_EVAL_MODE must be tick")
    if config.BREAKEVEN_ENABLED:
        errors.append("BREAKEVEN_ENABLED must be false: primary strategy.exit reserves the position before BE exits")
    if config.TRAIL_TV_BAR_PATH:
        errors.append("TRAIL_TV_BAR_PATH must be false for live Pine parity")
    if config.SNIPER_V6_EXIT_PARITY:
        errors.append("SNIPER_V6_EXIT_PARITY must be false for this calc_on_every_tick Pine")
    if config.BAR_CLOSE_SL_EVAL:
        errors.append("BAR_CLOSE_SL_EVAL must be false")
    if config.TRAIL_SL_PRE_FIRE_BUFFER != 0:
        errors.append("TRAIL_SL_PRE_FIRE_BUFFER must be 0")
    if config.SL_CONFIRM_TICKS != 1 or config.TRAIL_SL_CONFIRM_TICKS != 1:
        errors.append("SL_CONFIRM_TICKS and TRAIL_SL_CONFIRM_TICKS must both be 1")
    if config.BREAKOUT_BUFFER_PTS != 0 or config.ADX_TOLERANCE != 0 or config.FILTER_BODY_TOLERANCE != 0:
        errors.append("Parity tolerances/buffers must be zero")

    expected_source = "binance" if config.BINANCE_SIGNAL_FEED else "delta"
    if config.PINE_TRAIL_PRICE_SOURCE != expected_source:
        errors.append(
            f"PINE_TRAIL_PRICE_SOURCE={config.PINE_TRAIL_PRICE_SOURCE}; must match signal feed {expected_source}"
        )

    if config.PINE_MINTICK <= 0:
        errors.append("PINE_MINTICK must match syminfo.mintick on the TradingView chart")

    if config.DELTA_API_KEY.startswith(("YOUR_", "PASTE_")) or config.DELTA_API_SECRET.startswith(("YOUR_", "PASTE_")):
        warnings.append("Delta API credentials are placeholders")
    if config.EXECUTION_MODE == "paper":
        warnings.append("EXECUTION_MODE=paper: no live exchange orders will be sent")
    if config.EXECUTION_MODE == "live" and not config.DELTA_TESTNET and not config.LIVE_TRADING_ENABLED:
        errors.append("Production live routing is locked; LIVE_TRADING_ENABLED=true is required")

    print(f"profile={config.PINE_PROFILE}")
    print(f"EMA={config.EMA_FAST_LEN}/{config.EMA_TREND_LEN} timeframe={config.CANDLE_TIMEFRAME}")
    print(f"feed={expected_source} trail_source={config.PINE_TRAIL_PRICE_SOURCE} mintick={config.PINE_MINTICK}")
    print(f"dynamic_atr={config.DYNAMIC_REALTIME_ATR} native_tick_trail={config.TRAIL_LEGACY_TV_TICK_SEMANTICS}")
    print(f"rollback_stage={config.PINE_REALTIME_VAR_ROLLBACK} BE_effective={config.BREAKEVEN_ENABLED}")
    print(f"trail_stages={config.TRAIL_STAGES}")
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        return 1
    print("Sniper v6 exact Pine profile: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
