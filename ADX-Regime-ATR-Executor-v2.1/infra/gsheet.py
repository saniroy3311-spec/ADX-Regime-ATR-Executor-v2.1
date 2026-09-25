"""Google Sheets logging for ADX Regime ATR Executor.

Two tabs are maintained:
  * Trade Log  - one row per completed trade
  * Order Log  - entry/exit/failure audit events as they happen

Authentication supports either:
  GSHEET_CREDENTIALS_FILE=/absolute/path/service-account.json
or
  GSHEET_CREDENTIALS_JSON={...one-line service account json...}

Share the target spreadsheet with the service account email as Editor.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config import (
    COMMISSION_PCT,
    EXECUTION_MODE,
    GSHEET_AUTO_CREATE,
    GSHEET_CREDENTIALS_FILE,
    GSHEET_CREDENTIALS_JSON,
    GSHEET_ENABLED,
    GSHEET_SPREADSHEET_ID,
)
from risk.lot_sizing import lots_to_btc, compute_points, compute_pnl_usd

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

TRADE_LOG_TAB = "Trade Log"
ORDER_LOG_TAB = "Order Log"
TRADE_HEADER_ROW = 5

TRADE_HEADERS = [
    "Timestamp (IST)", "Date", "Month", "Signal Type", "Direction",
    "Qty Lots", "BTC Qty", "Entry Price", "Exit Price", "SL", "TP", "ATR",
    "Points Captured", "Gross P&L (USD)", "Commission (USD)", "Net P&L (USD)",
    "Exit Reason", "Trail Stage", "Result", "Cumulative Net P&L (USD)",
]

ORDER_HEADERS = [
    "Timestamp (IST)", "Event", "Execution Mode", "Order ID", "Signal Type",
    "Direction", "Qty Lots", "Expected Price", "Fill Price", "SL", "TP", "ATR",
    "Reason / Error", "Source",
]


def _credentials():
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if GSHEET_CREDENTIALS_FILE:
        path = os.path.expanduser(GSHEET_CREDENTIALS_FILE)
        if not os.path.exists(path):
            raise FileNotFoundError(f"GSHEET_CREDENTIALS_FILE not found: {path}")
        return Credentials.from_service_account_file(path, scopes=scopes)

    if GSHEET_CREDENTIALS_JSON:
        try:
            info = json.loads(GSHEET_CREDENTIALS_JSON)
        except json.JSONDecodeError as exc:
            raise ValueError(f"GSHEET_CREDENTIALS_JSON is invalid JSON: {exc}") from exc
        return Credentials.from_service_account_info(info, scopes=scopes)

    raise ValueError(
        "Google Sheets credentials missing. Set GSHEET_CREDENTIALS_FILE or "
        "GSHEET_CREDENTIALS_JSON."
    )


class GSheet:
    def __init__(self):
        self._enabled = bool(
            GSHEET_ENABLED
            and GSHEET_SPREADSHEET_ID
            and (GSHEET_CREDENTIALS_FILE or GSHEET_CREDENTIALS_JSON)
        )
        self._gc = None
        self._sh = None
        if GSHEET_ENABLED and not self._enabled:
            logger.warning(
                "Google Sheets requested but configuration is incomplete. "
                "Need GSHEET_SPREADSHEET_ID and credentials."
            )
        elif not self._enabled:
            logger.info("Google Sheets logging disabled")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _connect(self) -> None:
        if not self._enabled or self._sh is not None:
            return
        import gspread

        creds = _credentials()
        self._gc = gspread.authorize(creds)
        self._sh = self._gc.open_by_key(GSHEET_SPREADSHEET_ID)
        logger.info("Connected to Google Sheet: %s", self._sh.title)
        self._ensure_tabs()

    def _ensure_tabs(self) -> None:
        if self._sh is None:
            return
        existing = {ws.title: ws for ws in self._sh.worksheets()}

        trade_ws = existing.get(TRADE_LOG_TAB)
        if trade_ws is None and GSHEET_AUTO_CREATE:
            trade_ws = self._sh.add_worksheet(title=TRADE_LOG_TAB, rows=2000, cols=24)
            logger.info("Created Google Sheet tab: %s", TRADE_LOG_TAB)
        if trade_ws is not None:
            # Keep room for a title/notes section while matching the existing dashboard formulas.
            trade_ws.update("A1", [["ADX Regime ATR Executor - Completed Trades"]])
            trade_ws.update(f"A{TRADE_HEADER_ROW}:T{TRADE_HEADER_ROW}", [TRADE_HEADERS])

        order_ws = existing.get(ORDER_LOG_TAB)
        if order_ws is None and GSHEET_AUTO_CREATE:
            order_ws = self._sh.add_worksheet(title=ORDER_LOG_TAB, rows=4000, cols=16)
            logger.info("Created Google Sheet tab: %s", ORDER_LOG_TAB)
        if order_ws is not None:
            order_ws.update("A1:N1", [ORDER_HEADERS])

        if "Dashboard" not in existing and GSHEET_AUTO_CREATE:
            ws = self._sh.add_worksheet(title="Dashboard", rows=50, cols=10)
            self._write_dashboard_formulas(ws)

    def _write_dashboard_formulas(self, ws) -> None:
        rows = [
            ["ADX Regime ATR Executor - Trade Dashboard", ""],
            ["", ""],
            ["SUMMARY", "Value"],
            ["Total Trades", "=COUNTA('Trade Log'!A6:A)"],
            ["Wins", "=COUNTIF('Trade Log'!P6:P,\">0\")"],
            ["Losses", "=COUNTIF('Trade Log'!P6:P,\"<0\")"],
            ["Win Rate %", "=IFERROR(B5/B4*100,0)"],
            ["", ""],
            ["P/L SUMMARY", ""],
            ["Total Net P/L (USD)", "=SUM('Trade Log'!P6:P)"],
            ["Best Trade (USD)", "=MAX('Trade Log'!P6:P)"],
            ["Worst Trade (USD)", "=MIN('Trade Log'!P6:P)"],
            ["Total Commission (USD)", "=SUM('Trade Log'!O6:O)"],
            ["", ""],
            ["ORDER AUDIT", ""],
            ["Order Events", "=COUNTA('Order Log'!A2:A)"],
            ["Last Order Event", "=MAX('Order Log'!A2:A)"],
        ]
        ws.update(f"A1:B{len(rows)}", rows)

    def test_connection(self) -> bool:
        if not self._enabled:
            return False
        try:
            self._connect()
            return self._sh is not None
        except Exception as exc:
            logger.error("Google Sheets connection test failed: %s", exc)
            return False

    @staticmethod
    def _pl_breakdown(entry_price: float, exit_price: float, qty: int, is_long: bool) -> dict:
        points = compute_points(entry_price, exit_price, is_long)
        qty_btc = lots_to_btc(qty)
        gross = compute_pnl_usd(entry_price, exit_price, qty, is_long)
        commission = (entry_price + exit_price) * qty_btc * COMMISSION_PCT
        net = gross - commission
        return {
            "points": round(points, 2),
            "qty_btc": round(qty_btc, 6),
            "gross": round(gross, 6),
            "commission": round(commission, 6),
            "net": round(net, 6),
        }

    def log_order_event(
        self,
        *,
        event: str,
        order_id: str = "",
        signal_type: str = "",
        is_long: Optional[bool] = None,
        qty: int = 0,
        expected_price: float = 0.0,
        fill_price: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        atr: float = 0.0,
        reason: str = "",
        source: str = "",
    ) -> bool:
        if not self._enabled:
            return True
        try:
            self._connect()
            ws = self._sh.worksheet(ORDER_LOG_TAB)
            direction = "LONG" if is_long is True else ("SHORT" if is_long is False else "")
            row = [
                datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
                event,
                EXECUTION_MODE.upper(),
                str(order_id or ""),
                signal_type,
                direction,
                qty,
                round(expected_price, 2) if expected_price else "",
                round(fill_price, 2) if fill_price else "",
                round(sl, 2) if sl else "",
                round(tp, 2) if tp else "",
                round(atr, 2) if atr else "",
                reason,
                source,
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            return True
        except Exception as exc:
            logger.error("GSheet log_order_event failed: %s", exc)
            return False

    def log_trade(
        self,
        signal_type: str,
        is_long: bool,
        entry_price: float,
        exit_price: float,
        sl: float,
        tp: float,
        atr: float,
        qty: int,
        real_pl: float = None,
        exit_reason: str = "",
        trail_stage: int = 0,
        points_captured: float = None,
    ) -> bool:
        if not self._enabled:
            return True
        try:
            self._connect()
            ws = self._sh.worksheet(TRADE_LOG_TAB)
            pl = self._pl_breakdown(entry_price, exit_price, qty, is_long)
            points = points_captured if points_captured is not None else pl["points"]
            net = real_pl if real_pl is not None else pl["net"]
            now = datetime.now(IST)
            result = "WIN" if net > 0 else ("LOSS" if net < 0 else "BE")
            row = [
                now.strftime("%Y-%m-%d %H:%M:%S IST"),
                now.strftime("%Y-%m-%d"),
                now.strftime("%B %Y"),
                signal_type,
                "LONG" if is_long else "SHORT",
                qty,
                pl["qty_btc"],
                round(entry_price, 2),
                round(exit_price, 2),
                round(sl, 2),
                round(tp, 2),
                round(atr, 2),
                round(points, 2),
                pl["gross"],
                pl["commission"],
                round(net, 4),
                exit_reason,
                trail_stage,
                result,
                "",
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info("GSheet trade logged: %s %s net=%+.4f", signal_type, row[4], net)
            return True
        except Exception as exc:
            logger.error("GSheet log_trade failed: %s", exc)
            return False
