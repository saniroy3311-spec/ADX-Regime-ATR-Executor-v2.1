import asyncio
import unittest

import pandas as pd

import config
from indicators.engine import IndicatorSnapshot, evaluate, SignalType, _ema
from risk.calculator import calc_levels, calc_real_pl, TrailState
from monitor.trail_loop import TrailMonitor
from strategy_logic import (
    get_trail_params, max_sl_threshold, upgrade_trail_stage, should_trigger_be,
)


class ParityCoreTests(unittest.TestCase):
    def test_active_profile_defaults(self):
        self.assertEqual(config.EMA_FAST_LEN, 20)
        self.assertEqual(config.EMA_TREND_LEN, 50)
        self.assertEqual(config.ADX_TREND_TH, 22.0)
        self.assertEqual(config.ADX_RANGE_TH, 18.0)
        self.assertEqual(config.FILTER_ATR_MULT, 1.4)
        self.assertEqual(config.FILTER_BODY_MULT, 0.5)
        self.assertEqual(config.TREND_RR, 4.0)
        self.assertEqual(config.RANGE_RR, 2.5)
        self.assertEqual(config.TREND_ATR_MULT, 0.6)
        self.assertEqual(config.RANGE_ATR_MULT, 0.5)
        self.assertEqual(config.MAX_SL_MULT, 1.5)
        self.assertEqual(config.MAX_SL_POINTS, 500.0)
        self.assertEqual(config.BE_MULT, 0.6)
        self.assertEqual(config.TRAIL_STAGES[0], (0.8, 0.50, 0.40))
        self.assertEqual(config.TRAIL_STAGES[-1], (6.0, 0.15, 0.10))

    def test_risk_anchors_to_actual_fill_and_frozen_atr(self):
        r = calc_levels(1000.0, 100.0, True, True, signal_close=970.0)
        self.assertAlmostEqual(r.sl, 940.0)
        self.assertAlmostEqual(r.tp, 1240.0)
        self.assertAlmostEqual(r.entry_price, 1000.0)
        self.assertAlmostEqual(r.atr, 100.0)

    def test_range_risk_profile(self):
        r = calc_levels(1000.0, 100.0, True, False)
        self.assertAlmostEqual(r.sl, 950.0)
        self.assertAlmostEqual(r.tp, 1125.0)

    def test_corrected_trail_uses_atr_price_units_not_tv_ticks(self):
        self.assertFalse(config.TRAIL_LEGACY_TV_TICK_SEMANTICS)
        activation, offset = get_trail_params(1, 100.0)
        self.assertAlmostEqual(activation, 50.0)
        self.assertAlmostEqual(offset, 40.0)

    def test_stage_ratchet(self):
        self.assertEqual(upgrade_trail_stage(0, 79.9, 100.0), 0)
        self.assertEqual(upgrade_trail_stage(0, 80.0, 100.0), 1)
        self.assertEqual(upgrade_trail_stage(1, 600.0, 100.0), 5)
        self.assertEqual(upgrade_trail_stage(5, 0.0, 100.0), 5)

    def test_breakeven_is_strict_greater_than(self):
        self.assertFalse(should_trigger_be(60.0, 100.0))
        self.assertTrue(should_trigger_be(60.0001, 100.0))

    def test_trend_long_signal(self):
        s = IndicatorSnapshot(
            ema_trend=100, ema_fast=110, atr=10, rsi=50, dip=30, dim=20,
            adx=25, adx_raw=25, vol_sma=100, atr_sma=10,
            trend_regime=True, range_regime=False, filters_ok=True,
            atr_ok=True, vol_ok=True, body_ok=True, open=105, high=120,
            low=100, close=121, volume=150, prev_high=120, prev_low=100,
            timestamp=1,
        )
        self.assertEqual(evaluate(s).signal_type, SignalType.TREND_LONG)

    def test_max_sl(self):
        self.assertAlmostEqual(max_sl_threshold(100.0), 150.0)
        self.assertAlmostEqual(max_sl_threshold(1000.0), 500.0)

    def test_ema_uses_pine_recursive_seed(self):
        src = pd.Series([10.0, 20.0, 30.0, 40.0])
        out = _ema(src, 3)
        self.assertAlmostEqual(out.iloc[0], 10.0)
        self.assertAlmostEqual(out.iloc[1], 15.0)
        self.assertAlmostEqual(out.iloc[2], 22.5)
        self.assertAlmostEqual(out.iloc[3], 31.25)

    def test_live_tick_risk_engine_enabled(self):
        self.assertTrue(config.LIVE_TICK_RISK_ENGINE)
        self.assertEqual(config.TRAIL_STAGE_UPDATE_MODE, "tick")
        self.assertEqual(config.BREAKEVEN_UPDATE_MODE, "tick")
        self.assertEqual(config.MAX_SL_EVAL_MODE, "tick")

    def test_live_tick_upgrades_stage_breakeven_and_ratchets_stop(self):
        risk = calc_levels(1000.0, 100.0, True, True)
        state = TrailState(current_sl=risk.sl)
        mon = TrailMonitor()
        mon._risk = risk
        mon._state = state
        mon._current_atr = 100.0
        mon._static_orders_active = True
        asyncio.run(mon._evaluate_tick(1101.0, source="delta"))
        self.assertEqual(state.stage, 1)
        self.assertTrue(state.be_done)
        self.assertTrue(state.trail_armed)
        first_sl = state.current_sl
        self.assertGreater(first_sl, risk.entry_price)
        asyncio.run(mon._evaluate_tick(1080.0, source="delta"))
        self.assertAlmostEqual(state.current_sl, first_sl)

    def test_delta_contract_pnl_and_commission_model(self):
        entry, exit_, qty = 80000.0, 80100.0, 30
        expected_gross = 100.0 * qty * config.DELTA_CONTRACT_VALUE
        fee_mult = (1.0 + config.DELTA_GST_RATE) if config.FEE_MODEL_INCLUDE_GST else 1.0
        expected_comm = (entry + exit_) * qty * config.DELTA_CONTRACT_VALUE * config.COMMISSION_PCT * fee_mult
        self.assertAlmostEqual(calc_real_pl(entry, exit_, True, qty), expected_gross - expected_comm)


if __name__ == "__main__":
    unittest.main()
