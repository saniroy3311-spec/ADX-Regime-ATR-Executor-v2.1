import asyncio
import unittest

import pandas as pd

import config
from indicators.engine import IndicatorSnapshot, evaluate, SignalType, _ema
from risk.calculator import calc_levels, calc_real_pl, TrailState
from monitor.trail_loop import TrailMonitor, _trail_pts, _trail_off, _upgrade_stage
from strategy_logic import get_trail_params, max_sl_threshold, should_trigger_be


class ParityCoreTests(unittest.TestCase):
    def test_active_profile_defaults_match_uploaded_screenshots(self):
        self.assertEqual(config.PINE_PROFILE, "sniper_v6_exact")
        self.assertEqual(config.EMA_FAST_LEN, 20)
        self.assertEqual(config.EMA_TREND_LEN, 50)
        self.assertEqual(config.ADX_TREND_TH, 15.0)
        self.assertEqual(config.ADX_RANGE_TH, 14.0)
        self.assertEqual(config.FILTER_ATR_MULT, 1.5)
        self.assertEqual(config.FILTER_BODY_MULT, 0.1)
        self.assertEqual(config.TREND_RR, 5.3)
        self.assertEqual(config.RANGE_RR, 2.3)
        self.assertEqual(config.TREND_ATR_MULT, 1.2)
        self.assertEqual(config.RANGE_ATR_MULT, 0.8)
        self.assertEqual(config.MAX_SL_MULT, 1.8)
        self.assertEqual(config.MAX_SL_POINTS, 350.0)
        self.assertEqual(config.RSI_OS, 20)
        self.assertEqual(config.TRAIL_STAGES, [
            (1.1, 0.10, 0.50),
            (0.6, 0.10, 0.50),
            (2.3, 0.20, 0.35),
            (4.1, 0.20, 0.15),
            (6.0, 0.15, 0.10),
        ])

    def test_native_pine_tick_trail_geometry(self):
        self.assertTrue(config.TRAIL_LEGACY_TV_TICK_SEMANTICS)
        activation, offset = get_trail_params(1, 276.12)
        self.assertAlmostEqual(activation, 276.12 * 0.1 * config.PINE_MINTICK)
        self.assertAlmostEqual(offset, 276.12 * 0.5 * config.PINE_MINTICK)
        self.assertAlmostEqual(_trail_pts(1, 276.12), activation)
        self.assertAlmostEqual(_trail_off(1, 276.12), offset)

    def test_stage2_precedes_stage1_with_uploaded_settings(self):
        # The Pine checks stage 5 -> 1. Since Stage 2 trigger is 0.6 ATR and
        # Stage 1 is 1.1 ATR, a fresh trade jumps directly 0 -> 2 at 0.6 ATR.
        self.assertEqual(_upgrade_stage(0, 59.9, 100.0), 0)
        self.assertEqual(_upgrade_stage(0, 60.0, 100.0), 2)
        self.assertEqual(_upgrade_stage(0, 110.0, 100.0), 2)
        self.assertEqual(_upgrade_stage(0, 230.0, 100.0), 3)

    def test_realtime_execution_flags_match_uploaded_pine(self):
        self.assertTrue(config.LIVE_TICK_RISK_ENGINE)
        self.assertTrue(config.DYNAMIC_REALTIME_ATR)
        self.assertTrue(config.PINE_REALTIME_VAR_ROLLBACK)
        self.assertEqual(config.TRAIL_STAGE_UPDATE_MODE, "tick")
        self.assertEqual(config.MAX_SL_EVAL_MODE, "tick")
        self.assertFalse(config.TRAIL_TV_BAR_PATH)
        self.assertFalse(config.SNIPER_V6_EXIT_PARITY)
        self.assertFalse(config.BREAKEVEN_ENABLED)

    def test_risk_anchors_to_fill_with_uploaded_settings(self):
        r = calc_levels(1000.0, 100.0, True, True)
        self.assertAlmostEqual(r.sl, 880.0)
        self.assertAlmostEqual(r.tp, 1636.0)
        rr = calc_levels(1000.0, 100.0, True, False)
        self.assertAlmostEqual(rr.sl, 920.0)
        self.assertAlmostEqual(rr.tp, 1184.0)

    def test_max_sl(self):
        self.assertAlmostEqual(max_sl_threshold(100.0), 180.0)
        self.assertAlmostEqual(max_sl_threshold(1000.0), 350.0)

    def test_breakeven_formula_still_uses_strict_greater_than(self):
        self.assertFalse(should_trigger_be(60.0, 100.0))
        self.assertTrue(should_trigger_be(60.0001, 100.0))
        self.assertFalse(config.BREAKEVEN_ENABLED)

    def test_trend_long_signal(self):
        s = IndicatorSnapshot(
            ema_trend=100, ema_fast=110, atr=10, rsi=50, dip=30, dim=20,
            adx=20, adx_raw=20, vol_sma=100, atr_sma=10,
            trend_regime=True, range_regime=False, filters_ok=True,
            atr_ok=True, vol_ok=True, body_ok=True, open=105, high=120,
            low=100, close=121, volume=150, prev_high=120, prev_low=100,
            timestamp=1,
        )
        self.assertEqual(evaluate(s).signal_type, SignalType.TREND_LONG)

    def test_ema_uses_pine_recursive_seed(self):
        src = pd.Series([10.0, 20.0, 30.0, 40.0])
        out = _ema(src, 3)
        self.assertAlmostEqual(out.iloc[0], 10.0)
        self.assertAlmostEqual(out.iloc[1], 15.0)
        self.assertAlmostEqual(out.iloc[2], 22.5)
        self.assertAlmostEqual(out.iloc[3], 31.25)

    def test_delta_contract_pnl_and_commission_model(self):
        entry, exit_, qty = 80000.0, 80100.0, 30
        expected_gross = 100.0 * qty * config.DELTA_CONTRACT_VALUE
        fee_mult = (1.0 + config.DELTA_GST_RATE) if config.FEE_MODEL_INCLUDE_GST else 1.0
        expected_comm = (entry + exit_) * qty * config.DELTA_CONTRACT_VALUE * config.COMMISSION_PCT * fee_mult
        self.assertAlmostEqual(calc_real_pl(entry, exit_, True, qty), expected_gross - expected_comm)


class RealtimeRollbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_intrabar_stage_uses_committed_stage_as_rollback_base(self):
        risk = calc_levels(1000.0, 100.0, True, True)
        state = TrailState(current_sl=risk.sl)
        mon = TrailMonitor()
        mon._risk = risk
        mon._state = state
        mon._running = True
        mon._current_atr = 100.0
        mon._confirmed_atr = 100.0
        mon._prev_close = 1000.0
        mon._static_orders_active = True
        mon._committed_stage = 0
        # Touch >0.6 ATR -> transient Stage 2.
        await mon._evaluate_tick(1070.0, source="delta")
        self.assertEqual(state.stage, 2)
        # Next tick rolls script state back to committed Stage 0, and 20 pts is
        # below 0.6 ATR -> stage displays 0 again. Native stop itself stays armed.
        await mon._evaluate_tick(1020.0, source="delta")
        self.assertEqual(state.stage, 0)
        self.assertTrue(state.trail_armed)


if __name__ == "__main__":
    unittest.main()
