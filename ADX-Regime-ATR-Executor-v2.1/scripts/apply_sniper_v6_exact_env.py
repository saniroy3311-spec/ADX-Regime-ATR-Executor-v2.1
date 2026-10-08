#!/usr/bin/env python3
"""Update only Pine-parity keys in an existing .env, preserving secrets/mode."""
from __future__ import annotations
from pathlib import Path
import shutil
import sys
from datetime import datetime

VALUES = {
    "PINE_PROFILE": "sniper_v6_exact",
    "PINE_PARITY_MODE": "true",
    "LIVE_TICK_RISK_ENGINE": "true",
    "PINE_REALTIME_VAR_ROLLBACK": "true",
    "TRAIL_LEGACY_TV_TICK_SEMANTICS": "true",
    "DYNAMIC_REALTIME_ATR": "true",
    "SNIPER_V6_EXIT_PARITY": "false",
    "BREAKEVEN_ENABLED": "false",
    "TRAIL_TV_BAR_PATH": "false",
    "TRAIL_TV_ENTRY_CANDLE_LIVE": "false",
    "TRAIL_STAGE_UPDATE_MODE": "tick",
    "BREAKEVEN_UPDATE_MODE": "tick",
    "MAX_SL_EVAL_MODE": "tick",
    "PINE_TRAIL_PRICE_SOURCE": "signal",
    "TRAIL_TRACE": "true",
    "EMA_TREND_LEN": "50",
    "EMA_FAST_LEN": "20",
    "ATR_LEN": "14",
    "DI_LEN": "14",
    "ADX_SMOOTH": "14",
    "ADX_EMA": "5",
    "RSI_LEN": "14",
    "ADX_TREND_TH": "15",
    "ADX_RANGE_TH": "14",
    "ADX_TOLERANCE": "0",
    "FILTER_ATR_MULT": "1.5",
    "FILTER_BODY_MULT": "0.1",
    "FILTER_BODY_TOLERANCE": "0",
    "FILTER_VOL_ENABLED": "true",
    "FILTER_VOL_MULT": "1.0",
    "BREAKOUT_BUFFER_PTS": "0",
    "TREND_RR": "5.3",
    "RANGE_RR": "2.3",
    "TREND_ATR_MULT": "1.2",
    "RANGE_ATR_MULT": "0.8",
    "TRAIL1_TRIGGER": "1.1",
    "TRAIL1_PTS": "0.1",
    "TRAIL1_OFF": "0.5",
    "TRAIL2_TRIGGER": "0.6",
    "TRAIL2_PTS": "0.1",
    "TRAIL2_OFF": "0.5",
    "TRAIL3_TRIGGER": "2.3",
    "TRAIL3_PTS": "0.2",
    "TRAIL3_OFF": "0.35",
    "TRAIL4_TRIGGER": "4.1",
    "TRAIL4_PTS": "0.2",
    "TRAIL4_OFF": "0.15",
    "TRAIL5_TRIGGER": "6.0",
    "TRAIL5_PTS": "0.15",
    "TRAIL5_OFF": "0.1",
    "BE_MULT": "0.6",
    "MAX_SL_MULT": "1.8",
    "MAX_SL_POINTS": "350",
    "RSI_OB": "70",
    "RSI_OS": "20",
    "TP_HARD_EXIT": "true",
    "BAR_CLOSE_SL_EVAL": "false",
    "TRAIL_SL_PRE_FIRE_BUFFER": "0",
    "TRAIL_OFFSET_FLOOR_MULT": "0",
    "TRAIL_ARM_FLOOR_MULT": "0",
    "SL_CONFIRM_MS": "0",
    "SL_CONFIRM_TICKS": "1",
    "TRAIL_SL_CONFIRM_TICKS": "1",
    "TRAIL_FIRE_SL_ON_CANDLE_EXTREME": "false",
}


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    if not target.exists():
        print(f"ERROR: {target} does not exist")
        return 2

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(target.name + f".bak-{stamp}")
    shutil.copy2(target, backup)

    lines = target.read_text().splitlines()
    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in VALUES:
            out.append(f"{key}={VALUES[key]}")
            seen.add(key)
        else:
            out.append(line)

    missing = [k for k in VALUES if k not in seen]
    if missing:
        out.append("")
        out.append("# Sniper v6 exact Pine parity settings")
        out.extend(f"{k}={VALUES[k]}" for k in missing)

    target.write_text("\n".join(out) + "\n")
    print(f"Updated: {target}")
    print(f"Backup : {backup}")
    print("NOTE: BINANCE_SIGNAL_FEED and PINE_MINTICK were NOT changed.")
    print("      They must match the exact TradingView chart symbol/venue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
