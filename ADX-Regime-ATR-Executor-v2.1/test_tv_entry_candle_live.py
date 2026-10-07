"""Tests for TRAIL_TV_ENTRY_CANDLE_LIVE (TV candle mode + live entry candle).

Short trade, entry 84,343, ATR 276, new trail settings
(Stage 1: Pts 2.0 -> activation 276 pts, Off 1.2 -> gap 165.6 pts).
"""
import asyncio
import importlib
import os
import unittest
from unittest.mock import AsyncMock

BASE_ENV = {
    "SNIPER_V6_EXIT_PARITY": "true",
    "TRAIL_LEGACY_TV_TICK_SEMANTICS": "true",
    "LIVE_TICK_RISK_ENGINE": "true",
    "TRAIL_STAGE_UPDATE_MODE": "bar_close",
    "BREAKEVEN_UPDATE_MODE": "bar_close",
    "MAX_SL_EVAL_MODE": "bar_close",
    "BREAKEVEN_ENABLED": "false",
    "DYNAMIC_REALTIME_ATR": "false",
    "TP_HARD_EXIT": "true",
    "PINE_MINTICK": "0.5",
    "TRAIL_OFFSET_FLOOR_MULT": "0",
    "TRAIL_SL_CONFIRM_TICKS": "1",
    "SL_CONFIRM_TICKS": "1",
    "TRAIL1_TRIGGER": "1.1", "TRAIL1_PTS": "2.0", "TRAIL1_OFF": "1.2",
    "TRAIL2_TRIGGER": "1.6", "TRAIL2_PTS": "2.0", "TRAIL2_OFF": "1.0",
    "TRAIL3_TRIGGER": "2.3", "TRAIL3_PTS": "2.0", "TRAIL3_OFF": "0.8",
    "TRAIL4_TRIGGER": "4.1", "TRAIL4_PTS": "2.0", "TRAIL4_OFF": "0.6",
    "TRAIL5_TRIGGER": "6.0", "TRAIL5_PTS": "2.0", "TRAIL5_OFF": "0.5",
    "TRAIL_TV_BAR_PATH": "true",
}

ENTRY, ATR = 84343.0, 276.0
BAR_MS = 30 * 60 * 1000


def load(entry_live: bool):
    # The live .env must not override the test settings (config.py calls
    # load_dotenv(override=True)), so make it a no-op for this test.
    try:
        import dotenv
        dotenv.load_dotenv = lambda *a, **k: False
    except ImportError:
        pass
    os.environ.update(BASE_ENV)
    os.environ["TRAIL_TV_ENTRY_CANDLE_LIVE"] = "true" if entry_live else "false"
    import config
    importlib.reload(config)
    import monitor.trail_loop as tl
    importlib.reload(tl)
    from risk.calculator import RiskLevels, TrailState
    return tl, RiskLevels, TrailState


class TVEntryCandleLiveTests(unittest.IsolatedAsyncioTestCase):
    async def _start(self, entry_live: bool):
        tl, RiskLevels, TrailState = load(entry_live)
        mon = tl.TrailMonitor(order_mgr=AsyncMock())
        mon._fire_exit = AsyncMock()
        risk = RiskLevels(entry_price=ENTRY, sl=ENTRY + 331.2, tp=ENTRY - 1755.4,
                          stop_dist=331.2, atr=ATR, is_long=False, is_trend=True)
        state = TrailState(current_sl=risk.sl)
        mon.start(risk, state, entry_bar_time_ms=1_000 * BAR_MS, on_trail_exit=AsyncMock())
        if mon._task:
            mon._task.cancel()
        return mon, state

    async def test_entry_candle_exits_live_when_enabled(self):
        mon, state = await self._start(entry_live=True)
        for p in (84343.0, 84000.0, 84100.0):        # +343 -> trail armed, best 84,000
            await mon._evaluate_tick(p, source="delta")
        mon._fire_exit.assert_not_called()
        self.assertTrue(state.trail_armed)
        self.assertAlmostEqual(state.current_sl, 84000.0 + 165.6, places=1)
        await mon._evaluate_tick(84170.0, source="delta")   # bounce > gap
        mon._fire_exit.assert_awaited_once()
        price, reason = mon._fire_exit.await_args.args[:2]
        self.assertAlmostEqual(price, 84165.6, places=1)
        self.assertIn("Trail SL", reason)

    async def test_entry_candle_frozen_when_disabled(self):
        mon, state = await self._start(entry_live=False)
        for p in (84343.0, 84000.0, 84100.0, 84170.0):
            await mon._evaluate_tick(p, source="delta")
        mon._fire_exit.assert_not_called()          # old behaviour: nothing in entry candle
        self.assertFalse(state.trail_armed)

    async def test_candle_two_stays_tv_mode(self):
        mon, state = await self._start(entry_live=True)
        for p in (84343.0, 84000.0, 84100.0):
            await mon._evaluate_tick(p, source="delta")
        # entry candle closes: no replay, stop kept from live ticks
        mon.on_bar_close(bar_close=84050.0, bar_high=84360.0, bar_low=84000.0,
                         bar_open=84343.0, current_atr=ATR, is_entry_bar=True)
        await asyncio.sleep(0)
        mon._fire_exit.assert_not_called()
        stop_after_entry = state.current_sl
        # candle 2: new low then a bounce smaller than the frozen stop -> no exit,
        # and the trail must NOT move on live ticks
        for p in (83800.0, 83950.0):
            await mon._evaluate_tick(p, source="delta")
        mon._fire_exit.assert_not_called()
        self.assertEqual(state.current_sl, stop_after_entry)
        # candle 2 close: TV path O-H-L-C, low 83,700 then close 83,950
        # -> TV trail stop 83,700 + 165.6 = 83,865.6 hit -> exit at the close
        mon.on_bar_close(bar_close=83950.0, bar_high=84100.0, bar_low=83700.0,
                         bar_open=84050.0, current_atr=ATR)
        await asyncio.sleep(0)
        mon._fire_exit.assert_awaited_once()
        self.assertEqual(mon._fire_exit.await_args.args[0], 83950.0)


if __name__ == "__main__":
    unittest.main()
