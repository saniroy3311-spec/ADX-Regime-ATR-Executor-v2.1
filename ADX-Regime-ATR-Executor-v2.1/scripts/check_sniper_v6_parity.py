#!/usr/bin/env python3
"""Print exact native-trail geometry for the uploaded Sniper v6 profile."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from monitor.trail_loop import _activation_price, _trail_off, _trail_pts, _upgrade_stage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", type=float, required=True)
    ap.add_argument("--atr", type=float, required=True)
    ap.add_argument("--side", choices=["long", "short"], required=True)
    ap.add_argument("--price", type=float, help="Current price for stage calculation")
    ap.add_argument("--best", type=float, help="Current best favorable price")
    ap.add_argument("--committed-stage", type=int, default=0)
    args = ap.parse_args()

    is_long = args.side == "long"
    profit = None
    effective_stage = args.committed_stage
    if args.price is not None:
        profit = (args.price - args.entry) if is_long else (args.entry - args.price)
        effective_stage = _upgrade_stage(args.committed_stage, profit, args.atr)

    order_stage = effective_stage if effective_stage > 0 else 1
    pts = _trail_pts(order_stage, args.atr)
    off = _trail_off(order_stage, args.atr)
    act = _activation_price(args.entry, order_stage, args.atr, is_long)

    print(f"profile={config.PINE_PROFILE}")
    print(f"mintick={config.PINE_MINTICK}")
    print(f"committed_stage={args.committed_stage}")
    if profit is not None:
        print(f"profit_dist={profit:.4f}")
        print(f"effective_realtime_stage={effective_stage}")
    print(f"native_order_stage={order_stage}")
    print(f"trail_points_price_distance={pts:.4f}")
    print(f"trail_offset_price_distance={off:.4f}")
    print(f"activation_price={act:.4f}")
    if args.best is not None:
        stop = args.best - off if is_long else args.best + off
        print(f"best_price={args.best:.4f}")
        print(f"theoretical_trail_stop={stop:.4f}")
    print("stages=", config.TRAIL_STAGES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
