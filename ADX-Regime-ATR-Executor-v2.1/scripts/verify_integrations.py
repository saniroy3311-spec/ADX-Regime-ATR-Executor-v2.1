#!/usr/bin/env python3
"""Read-only preflight for Delta + Google Sheets.

This script never places, cancels, or modifies an order.
"""
from __future__ import annotations

import asyncio

import config
from infra.gsheet import GSheet
from orders.manager import OrderManager


async def main() -> int:
    print(f"Bot: {config.BOT_NAME} {config.BOT_VERSION}")
    print(f"Execution mode: {config.EXECUTION_MODE}")
    print(f"Delta environment: {'TESTNET' if config.DELTA_TESTNET else 'PRODUCTION'}")

    om = OrderManager()
    try:
        await om.initialize()
        ticker = await om.fetch_ticker()
        if not ticker:
            raise RuntimeError("Delta ticker unavailable")
        last = ticker.get("last") or ticker.get("markPrice") or (ticker.get("info") or {}).get("mark_price")
        print(f"Delta market connection: OK | {config.SYMBOL} last={last}")

        # Verify authenticated account access without placing an order.
        if (config.DELTA_API_KEY and not config.DELTA_API_KEY.startswith(("YOUR_", "PASTE_"))):
            try:
                bal = await om.exchange.fetch_balance()
                print("Delta authenticated account access: OK")
            except Exception as exc:
                raise RuntimeError(f"Delta API credentials/account access failed: {exc}") from exc

        pos = await om.fetch_open_position()
        if config.EXECUTION_MODE == "paper":
            print("Paper position state: flat" if not pos else f"Paper position: {pos}")
        else:
            print("Live/testnet position: flat" if not pos else f"Live/testnet position: {pos}")
    finally:
        await om.close_exchange()

    gs = GSheet()
    if config.GSHEET_ENABLED:
        if not gs.test_connection():
            raise RuntimeError("Google Sheets connection failed")
        gs.log_order_event(
            event="INTEGRATION_TEST",
            reason="Read-only setup check; no order sent",
            source="scripts/verify_integrations.py",
        )
        print("Google Sheets: OK | integration-test row appended to Order Log")
    else:
        print("Google Sheets: disabled")

    print("Preflight complete. No exchange order was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
