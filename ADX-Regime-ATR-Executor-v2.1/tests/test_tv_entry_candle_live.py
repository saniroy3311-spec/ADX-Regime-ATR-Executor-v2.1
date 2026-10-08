"""Regression checks for the uploaded Sniper-v6 realtime/native trail profile."""
import unittest
import config
from monitor.trail_loop import _trail_pts, _trail_off, _activation_price


class SniperV6NativeTrailTests(unittest.TestCase):
    def test_stage0_uses_stage1_native_trail_values(self):
        entry = 84343.0
        atr = 276.12
        act_dist = _trail_pts(1, atr)
        off = _trail_off(1, atr)
        self.assertAlmostEqual(act_dist, 13.806, places=3)
        self.assertAlmostEqual(off, 69.03, places=2)
        self.assertAlmostEqual(_activation_price(entry, 1, atr, False), 84329.194, places=3)
        self.assertAlmostEqual(84200.0 + off, 84269.03, places=2)

    def test_exact_profile_is_live_not_historical_bar_path(self):
        self.assertFalse(config.TRAIL_TV_BAR_PATH)
        self.assertFalse(config.SNIPER_V6_EXIT_PARITY)
        self.assertTrue(config.DYNAMIC_REALTIME_ATR)
        self.assertTrue(config.PINE_REALTIME_VAR_ROLLBACK)


if __name__ == "__main__":
    unittest.main()
