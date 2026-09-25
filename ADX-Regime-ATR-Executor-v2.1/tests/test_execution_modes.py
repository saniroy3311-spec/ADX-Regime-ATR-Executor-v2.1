import importlib.util
import unittest
from unittest.mock import AsyncMock

HAS_CCXT = importlib.util.find_spec("ccxt") is not None

@unittest.skipUnless(HAS_CCXT, "ccxt not installed in this build environment")
class PaperExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        global omod
        import orders.manager as omod
        self.omod = omod

    async def test_paper_entry_and_exit_never_call_create_order(self):
        old_mode = self.omod.EXECUTION_MODE
        self.omod.EXECUTION_MODE = "paper"
        om = self.omod.OrderManager()
        try:
            om.fetch_ticker = AsyncMock(return_value={"last": 80000.0})
            om.exchange.create_order = AsyncMock(side_effect=AssertionError("exchange order must not be called"))

            entry = await om.place_entry(True, sl=79000.0, tp=85000.0, atr=500.0, stop_dist=1000.0)
            self.assertTrue(entry["paper"])
            self.assertEqual(entry["average"], 80000.0)
            self.assertIsNotNone(await om.fetch_open_position())
            om.exchange.create_order.assert_not_called()

            om.fetch_ticker = AsyncMock(return_value={"last": 80500.0})
            exit_order = await om.close_position(True, reason="test", expected_price=80500.0)
            self.assertTrue(exit_order["paper"])
            self.assertEqual(exit_order["average"], 80500.0)
            self.assertIsNone(await om.fetch_open_position())
            om.exchange.create_order.assert_not_called()
        finally:
            self.omod.EXECUTION_MODE = old_mode
            await om.close_exchange()

if __name__ == "__main__":
    unittest.main()
