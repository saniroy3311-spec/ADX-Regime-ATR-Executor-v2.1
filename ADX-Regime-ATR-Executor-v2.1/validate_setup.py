"""Pre-flight validation for ADX Regime ATR Executor v2.1."""
from __future__ import annotations
import config

EXPECTED = {
    "EMA_FAST_LEN": 20, "EMA_TREND_LEN": 50,
    "ATR_LEN": 14, "DI_LEN": 14, "ADX_SMOOTH": 14, "ADX_EMA": 5, "RSI_LEN": 14,
    "ADX_TREND_TH": 22.0, "ADX_RANGE_TH": 18.0,
    "FILTER_ATR_MULT": 1.4, "FILTER_BODY_MULT": 0.5,
    "TREND_RR": 4.0, "RANGE_RR": 2.5,
    "TREND_ATR_MULT": 0.6, "RANGE_ATR_MULT": 0.5,
    "MAX_SL_MULT": 1.5, "MAX_SL_POINTS": 500.0, "BE_MULT": 0.6,
}
EXPECTED_STAGES = [
    (0.8, 0.50, 0.40), (1.5, 0.40, 0.30), (2.5, 0.30, 0.25),
    (4.0, 0.20, 0.15), (6.0, 0.15, 0.10),
]


def main() -> int:
    errors = []
    warnings = []
    for key, value in EXPECTED.items():
        if getattr(config, key) != value:
            errors.append(f"{key}={getattr(config, key)!r}; active Pine profile requires {value!r}")
    if config.TRAIL_STAGES != EXPECTED_STAGES:
        errors.append(f"TRAIL_STAGES={config.TRAIL_STAGES!r}; active Pine profile requires {EXPECTED_STAGES!r}")
    if config.BREAKOUT_BUFFER_PTS != 0:
        errors.append("BREAKOUT_BUFFER_PTS must be 0 for strict Pine signal parity")
    if config.ADX_TOLERANCE != 0:
        errors.append("ADX_TOLERANCE must be 0 for strict Pine signal parity")
    if config.FILTER_BODY_TOLERANCE != 0:
        errors.append("FILTER_BODY_TOLERANCE must be 0 for strict Pine signal parity")
    if not config.FILTER_VOL_ENABLED or config.FILTER_VOL_MULT != 1.0:
        errors.append("Volume filter must be enabled at 1.0x for the supplied Pine profile")
    if not config.TP_HARD_EXIT:
        errors.append("TP_HARD_EXIT must be true")
    if config.BAR_CLOSE_SL_EVAL:
        errors.append("BAR_CLOSE_SL_EVAL must be false for live-tick protection")
    if not config.LIVE_TICK_RISK_ENGINE:
        errors.append("LIVE_TICK_RISK_ENGINE must be true")
    if config.TRAIL_STAGE_UPDATE_MODE != "tick":
        errors.append("TRAIL_STAGE_UPDATE_MODE must be tick for v2.1 realtime parity")
    if config.BREAKEVEN_UPDATE_MODE != "tick":
        errors.append("BREAKEVEN_UPDATE_MODE must be tick for v2.1 realtime parity")
    if config.MAX_SL_EVAL_MODE != "tick":
        errors.append("MAX_SL_EVAL_MODE must be tick for v2.1 realtime parity")
    if config.TRAIL_LEGACY_TV_TICK_SEMANTICS:
        errors.append("TRAIL_LEGACY_TV_TICK_SEMANTICS must be false; it reproduces the old unit bug")
    if config.PINE_MINTICK <= 0:
        errors.append("PINE_MINTICK must be > 0 and match the TradingView symbol")
    if config.PINE_POINT_VALUE <= 0:
        errors.append("PINE_POINT_VALUE must be > 0 for backtest accounting")
    if config.ALERT_QTY <= 0:
        errors.append("ALERT_QTY must be > 0")

    placeholder_key = config.DELTA_API_KEY.startswith(("YOUR_", "PASTE_"))
    placeholder_secret = config.DELTA_API_SECRET.startswith(("YOUR_", "PASTE_"))
    if placeholder_key or placeholder_secret:
        warnings.append("Delta API credentials are placeholders")
    if config.EXECUTION_MODE == "live" and (not config.DELTA_TESTNET) and (not config.LIVE_TRADING_ENABLED):
        errors.append("Production live mode is locked: set LIVE_TRADING_ENABLED=true deliberately")
    if config.EXECUTION_MODE == "paper":
        warnings.append("EXECUTION_MODE=paper: no exchange orders will be sent")
    elif not config.DELTA_TESTNET:
        warnings.append("PRODUCTION LIVE MODE: real orders can be placed")
    if config.GSHEET_ENABLED and not config.GSHEET_SPREADSHEET_ID:
        warnings.append("GSHEET_ENABLED=true but GSHEET_SPREADSHEET_ID is empty")
    if config.EMERGENCY_BRACKET_ENABLED:
        warnings.append("Emergency exchange bracket is enabled as disconnect/crash protection")

    print(f"{config.BOT_NAME} {config.BOT_VERSION}")
    print(f"EMA {config.EMA_FAST_LEN}/{config.EMA_TREND_LEN} | timeframe {config.CANDLE_TIMEFRAME}")
    print(f"signal feed={'Binance' if config.BINANCE_SIGNAL_FEED else 'Delta'} | mintick={config.PINE_MINTICK}")
    print(f"risk updates stage/BE/maxSL={config.TRAIL_STAGE_UPDATE_MODE}/{config.BREAKEVEN_UPDATE_MODE}/{config.MAX_SL_EVAL_MODE}")
    print(f"execution={config.EXECUTION_MODE.upper()} | delta={'TESTNET' if config.DELTA_TESTNET else 'PRODUCTION'} | live_switch={config.LIVE_TRADING_ENABLED}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("Core ADX Regime ATR Executor v2.1 configuration: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
