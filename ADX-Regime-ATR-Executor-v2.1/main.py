"""
main.py — ADX Regime ATR Executor  (Live Runner)
══════════════════════════════════════════════════════════════════════════════

Entry point launched by systemd / PM2 / Docker CMD.

WHAT THIS FILE DOES
───────────────────
  1. Starts CandleFeed (WS primary, REST fallback).
  2. On every confirmed bar close -> compute indicators -> evaluate Pine
     entry conditions -> submit a market entry when flat.
  3. TrailMonitor handles SL, TP, breakeven, stage upgrades and trailing at
     live-tick resolution after the entry fill.
  4. Sends Telegram notifications for entry and exit events.
  5. Persists trade records to SQLite (Journal).
  6. On restart mid-trade: detects the exchange position, recovers regime state
     from the journal when available, then re-establishes trail management.

PINE PARITY
───────────
  Entry  : confirmed-bar signal; Pine fills on the next available tick because
           process_orders_on_close=false. Python submits after confirmation.
  Exit   : Intrabar risk decisions use the same selected market source where
           possible. Delta is the default for the supplied BTCUSD.P profile.
  Volume : Always comes from that same selected OHLCV source.

RUNNING
───────
  python main.py
  systemctl start adx_regime_atr_executor
  docker run adx_regime_atr_executor_bot
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from typing import Optional

# ── Canonical module imports ───────────────────────────────────────────────────
from config import (
    TELEGRAM_ENABLED, BOT_NAME,
    SYMBOL, ALERT_QTY, CANDLE_TIMEFRAME, FILTER_VOL_ENABLED,
    POSITION_BTC_SIZE, TREND_ATR_MULT, RANGE_ATR_MULT, BINANCE_SIGNAL_FEED,
)
from feed.ws_feed            import CandleFeed
from feed.binance_price_feed import BinancePriceFeed
from feed.fills_feed         import FillsFeed
from indicators.engine  import compute
from strategy.signal    import evaluate, SignalType
from risk.calculator    import (
    RiskLevels, TrailState,
    calc_levels, recalc_levels_from_fill, calc_real_pl,
)
from monitor.trail_loop import TrailMonitor
from orders.manager     import OrderManager
from infra.telegram            import Telegram
from infra.telegram_controller import TelegramController, EngineState
from infra.whatsapp            import WhatsApp
# from infra.whatsapp_controller import WhatsAppController  # disabled
from infra.journal             import Journal
import threading as _threading
from infra import heartbeat as _heartbeat

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

MAX_ENTRY_SLIP_ATR_FRAC = float(os.environ.get("MAX_ENTRY_SLIP_ATR_FRAC", "0.3"))

# ══════════════════════════════════════════════════════════════════════════════
# ADXRegimeATRExecutor
# ══════════════════════════════════════════════════════════════════════════════

class ADXRegimeATRExecutor:
    def __init__(self) -> None:
        self._order_mgr = OrderManager()
        self._telegram  = Telegram()
        self._whatsapp  = WhatsApp()
        self._journal   = Journal()

        self._state    = EngineState(running=True)
        if TELEGRAM_ENABLED:
            self._tg_ctrl = TelegramController(
                engine_state = self._state,
                telegram     = self._telegram,
                journal      = self._journal,
                order_mgr    = self._order_mgr,
            )
        else:
            self._tg_ctrl = None
        # self._wa_ctrl  = WhatsAppController(  # disabled
        #     engine_state = self._state,
        #     whatsapp     = self._whatsapp,
        #     journal      = self._journal,
        #     order_mgr    = self._order_mgr,
        # )

        # 100 Delta BTCUSD contracts = 0.1 BTC with the default contract value.
        self._qty_lots = ALERT_QTY
        self._entry_fee_usd: Optional[float] = None

        self._trail_mon = TrailMonitor(
            order_mgr = self._order_mgr,
            telegram  = self._telegram,
            journal   = self._journal,
        )
        self._feed: Optional[CandleFeed] = None
        self._binance_px_feed: Optional[BinancePriceFeed] = None
        self._fills_feed: Optional[FillsFeed] = None

        self._in_position : bool                  = False
        self._risk        : Optional[RiskLevels]  = None
        self._trail_state : Optional[TrailState]  = None
        self._signal_type : str                   = "None"
        self._entry_bar_boundary_ms : int         = 0   # FIX-9: next bar open after entry

        # Guard against duplicate concurrent entry attempts.
        self._entry_lock = asyncio.Lock()

    # ── Startup ───────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        logger.info("═" * 70)
        logger.info(f"  {BOT_NAME} — Starting")
        logger.info(f"  Symbol={SYMBOL}  TF={CANDLE_TIMEFRAME}")
        logger.info(f"  Position size: {POSITION_BTC_SIZE} BTC → {self._qty_lots} lots")
        logger.info(f"  FILTER_VOL_ENABLED={FILTER_VOL_ENABLED}  (Pine source uses volume filter)")
        logger.info(f"  MAX_ENTRY_SLIP_ATR_FRAC={MAX_ENTRY_SLIP_ATR_FRAC}  (SL recalc threshold)")
        logger.info("═" * 70)

        await self._order_mgr.initialize()

        try:
            existing_check = await self._order_mgr.fetch_open_position()
            if existing_check is None:
                await self._order_mgr.cancel_bracket()
                logger.info("[STARTUP] Flat on Delta — cancelled stale bracket legs (clean slate)")
        except Exception as e:
            logger.warning(f"[STARTUP] Bracket cleanup failed (non-fatal): {e}")

        # ── Startup recovery: adopt any pre-existing open position ─────────────
        existing = await self._order_mgr.fetch_open_position()
        
        # Validate local journal vs actual exchange reality.
        open_row = None
        try:
            open_row = self._journal.get_open_trade()
            if open_row and not existing:
                logger.info("[STARTUP] Database ghost row detected but Delta Exchange is FLAT. Purging local trade memory.")
                self._journal.clear_open_trade()
        except Exception as je:
            logger.warning(f"[STARTUP] Local journal state verification anomaly: {je}")

        if existing:
            # Preserve the original regime across restarts when the journal has it.
            # Falling back to trend is only a last resort for externally-created trades.
            recovered_signal = str((open_row or {}).get("signal_type") or "RECOVERED")
            recovered_is_trend = not recovered_signal.lower().startswith("range")
            logger.warning(
                f"[STARTUP] Open position detected - recovering risk state. "
                f"is_long={existing['is_long']} "
                f"entry={existing['entry_price']:.2f} regime={'trend' if recovered_is_trend else 'range'}"
            )
            self._in_position = True
            self._risk = RiskLevels(
                entry_price = existing["entry_price"],
                sl          = 0.0,
                tp          = 0.0,
                stop_dist   = 0.0,
                atr         = 0.0,
                is_long     = existing["is_long"],
                is_trend    = recovered_is_trend,
            )
            self._signal_type = recovered_signal
            await self._telegram.send(
                f"⚠️ <b>Position Recovery</b>\n"
                f"Bot restarted mid-trade.\n"
                f"Direction: {'LONG' if existing['is_long'] else 'SHORT'}\n"
                f"Entry (approx): {existing['entry_price']:.2f}\n"
                f"Trail management is being recovered from live state."
            )

        await self._telegram.send(
            f"🟢 <b>{BOT_NAME} Started</b>\n"
            f"Symbol: <code>{SYMBOL}</code>  TF: <code>{CANDLE_TIMEFRAME}</code>\n"
            f"Qty: <code>{self._qty_lots} lots</code> "
            f"({POSITION_BTC_SIZE} BTC)\n"
            f"Volume filter: <code>{'ON' if FILTER_VOL_ENABLED else 'OFF'}</code>"
        )

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        self._trail_mon.stop()
        try:
            if self._tg_ctrl: self._tg_ctrl.stop()
        except Exception:
            pass
        if self._binance_px_feed is not None:
            self._binance_px_feed.stop()
        if self._fills_feed is not None:
            self._fills_feed.stop()
        try:
            await asyncio.shield(self._telegram.send(f"🔴 <b>{BOT_NAME} Stopped</b>"))
        except Exception:
            pass
        try:
            self._journal.close()
        except Exception:
            pass
        try:
            await self._order_mgr.close_exchange()
        except Exception:
            pass
        logger.info("Shutdown complete.")

    # ── Feed callbacks ────────────────────────────────────────────────────────

    async def _feed_ready(self) -> None:
        logger.info("Feed ready — waiting for first bar close...")

    async def _on_bar_close(self, df) -> None:
        if self._in_position and not self._entry_lock.locked():
            try:
                actual = await self._order_mgr.fetch_open_position(strict=True)
                if actual is None:
                    logger.warning(
                        "[BAR] State drift detected: in_position=True but Delta "
                        "is flat. Bracket SL/TP fired silently — recovering exit."
                    )

                    # FIX-10: Try to get the real fill price from Delta order/fill
                    # history before falling back to the bracket trigger price.
                    # This corrects the journal entry (was off by ~3-4 pts per lot).
                    real_fill: Optional[float] = None
                    try:
                        real_fill = await self._order_mgr.fetch_bracket_fill_price()
                    except Exception as fill_err:
                        logger.warning(f"[BAR] fetch_bracket_fill_price failed: {fill_err}")

                    if real_fill is not None:
                        exit_price = real_fill
                        logger.info(f"[BAR] Drift recovery: using real fill price {exit_price:.2f}")
                    elif self._trail_state is not None:
                        exit_price = float(self._trail_state.current_sl)
                        logger.info(f"[BAR] Drift recovery: using trail SL as exit price {exit_price:.2f}")
                    elif self._risk is not None and self._risk.sl > 0:
                        exit_price = float(self._risk.sl)
                        logger.info(f"[BAR] Drift recovery: using risk.sl as exit price {exit_price:.2f}")
                    else:
                        try:
                            exit_price = float(df["close"].iloc[-1])
                        except Exception:
                            exit_price = 0.0
                        logger.info(f"[BAR] Drift recovery: using bar close as exit price {exit_price:.2f}")

                    if self._trail_mon._running:
                        self._trail_mon.stop()

                    try:
                        await self._on_trail_exit(
                            exit_price = exit_price,
                            reason     = "Bracket SL/TP (recovered)",
                            source     = "drift-check",
                            position_already_closed = True,
                        )
                    except Exception as exit_err:
                        logger.error(f"[BAR] Drift-recovery exit failed: {exit_err}", exc_info=True)
                        self._in_position = False
                        self._risk        = None
                        self._trail_state = None
                        self._signal_type = "None"
            except Exception as e:
                logger.warning(f"[BAR] State sanity check failed: {e}")

        # ── 1. Compute indicators ─────────────────────────────────────────────
        try:
            snap = compute(df)
        except ValueError as e:
            logger.warning(f"[BAR] Not enough bars: {e}")
            return

        logger.info(
            f"[BAR] close={snap.close:.2f}  atr={snap.atr:.2f}  "
            f"adx={snap.adx:.1f}  rsi={snap.rsi:.1f}  "
            f"trend={snap.trend_regime}  range={snap.range_regime}  "
            f"filters={'OK' if snap.filters_ok else 'FAIL'}  "
            f"[atr={snap.atr_ok} body={snap.body_ok} vol={snap.vol_ok}]"
        )

        # ── 2. Trail update for open position ─────────────────────────────────
        if self._in_position:
            if self._trail_mon._running:
                # FIX-9: is_entry_bar=True on the bar where entry was taken.
                # Pine never evaluates SL/TP on the signal bar itself.
                _is_entry_bar = (
                    self._entry_bar_boundary_ms > 0
                    and int(snap.timestamp) + 1 <= self._entry_bar_boundary_ms
                )
                self._trail_mon.on_bar_close(
                    bar_close   = snap.close,
                    bar_high    = snap.high,
                    bar_low     = snap.low,
                    bar_open    = snap.open,
                    current_atr = snap.atr,
                    is_entry_bar = _is_entry_bar,
                )
            else:
                if self._risk is not None and self._risk.stop_dist == 0.0:
                    open_row = None
                    try:
                        open_row = self._journal.get_open_trade()
                    except Exception as _je:
                        logger.warning(f"[RECOVERY] Journal read failed: {_je}")

                    if open_row and open_row.get("sl", 0) > 0 and open_row.get("atr", 0) > 0:
                        _orig_sl  = float(open_row["sl"])
                        _orig_tp  = float(open_row["tp"])
                        _orig_atr = float(open_row["atr"])
                        _atr_mult = TREND_ATR_MULT if self._risk.is_trend else RANGE_ATR_MULT
                        
                        if self._risk.is_long:
                            _signal_close = _orig_sl + _atr_mult * _orig_atr
                        else:
                            _signal_close = _orig_sl - _atr_mult * _orig_atr
                            
                        rebuilt = RiskLevels(
                            entry_price    = self._risk.entry_price,
                            sl             = _orig_sl,
                            tp             = _orig_tp,
                            stop_dist      = abs(_orig_sl - self._risk.entry_price),
                            atr            = _orig_atr,
                            is_long        = self._risk.is_long,
                            is_trend       = self._risk.is_trend,
                            signal_close   = _signal_close,
                        )
                        current_sl = float(open_row.get("current_sl", open_row["sl"]))
                    else:
                        rebuilt = calc_levels(
                            entry_price = self._risk.entry_price,
                            atr         = snap.atr,
                            is_long     = self._risk.is_long,
                            is_trend    = self._risk.is_trend,
                        )
                        rebuilt = recalc_levels_from_fill(rebuilt, self._risk.entry_price)
                        current_sl = rebuilt.sl

                    self._risk        = rebuilt
                    _rec_stage = int(open_row.get("trail_stage", 0)) if open_row else 0
                    self._trail_state = TrailState(
                        stage       = _rec_stage,
                        current_sl  = current_sl if _rec_stage > 0 else rebuilt.sl,
                        peak_price  = self._risk.entry_price,
                        trail_armed = _rec_stage > 0 and current_sl != rebuilt.sl,
                        best_price  = float(open_row.get("peak_price", 0.0)) if open_row else 0.0,
                    )

                    original_wall_ms: Optional[int] = None
                    try:
                        if open_row and open_row.get("opened_at"):
                            from datetime import datetime, timezone as _tz
                            dt = datetime.fromisoformat(str(open_row["opened_at"]))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=_tz.utc)
                            original_wall_ms = int(dt.timestamp() * 1000)
                    except Exception as _te:
                        pass

                    self._trail_mon.start(
                        risk_levels       = rebuilt,
                        trail_state       = self._trail_state,
                        entry_bar_time_ms = int(time.time() * 1000),
                        on_trail_exit     = self._on_trail_exit,
                        entry_wall_ms     = original_wall_ms,
                    )
                    await self._telegram.send(f"♻️ <b>Trail Resumed (Recovery)</b>\nEntry: {rebuilt.entry_price:.2f}")
            return

        # ── 3. Evaluate entry signals (only when flat) ────────────────────────
        sig = evaluate(snap, has_position=False)

        if sig.signal_type == SignalType.NONE:
            return

        if not self._state.running:
            logger.info(f"[SIGNAL] {sig.signal_type.value} ignored — engine PAUSED via /stop_bot")
            return

        logger.info(f"[SIGNAL] {sig.signal_type.value}  is_long={sig.is_long}  regime={sig.regime}")

        # ── 4. Place entry ─────────────────────────────────────────────────────
        if self._entry_lock.locked():
            return

        async with self._entry_lock:
            if self._in_position:
                return

            risk_pre = calc_levels(snap.close, snap.atr, sig.is_long, sig.is_trend, entry_bar_open=snap.open, signal_close=snap.close)

            try:
                order = await self._order_mgr.place_entry(
                    is_long = sig.is_long,
                    sl      = risk_pre.sl,
                    tp        = risk_pre.tp,
                    atr       = snap.atr,
                    stop_dist = risk_pre.stop_dist,
                )
            except Exception as e:
                logger.error(f"[ENTRY] Order failed: {e}")
                try:
                    self._journal.log_order_event(
                        event="ENTRY_FAILED", signal_type=sig.signal_type.value,
                        is_long=sig.is_long, qty=self._qty_lots,
                        expected_price=snap.close, sl=risk_pre.sl, tp=risk_pre.tp,
                        atr=snap.atr, reason=str(e), source="bar_signal",
                    )
                except Exception:
                    pass
                await self._telegram.send(f"❌ <b>Entry Order FAILED</b>\nSignal: {sig.signal_type.value}\nError: <code>{e}</code>")
                return

            fill = float(order.get("average") or order.get("price") or snap.close)
            self._entry_fee_usd = self._order_mgr.order_fee_usd(order)
            if self._entry_fee_usd is not None:
                logger.info(f"[ENTRY] Delta-reported fee={self._entry_fee_usd:.6f} USD")

            # Read actual filled contracts from the order response.
            # The intended live size is ALERT_QTY contracts; use the confirmed
            # exchange fill amount if it differs.
            # The order response carries the real filled amount under "amount",
            # "filled", or "contracts". Use that if > 0, else fall back to the
            # pre-computed value so recovery/manual trades still work.
            _filled_contracts = (
                float(order.get("filled") or order.get("amount") or order.get("contracts") or 0)
            )
            if _filled_contracts > 0 and abs(_filled_contracts - self._qty_lots) > 0.01:
                logger.info(
                    f"[QTY-FIX] Using actual fill qty={_filled_contracts:.0f} contracts "
                    f"(configured was {self._qty_lots} contracts)"
                )
                self._qty_lots = int(round(_filled_contracts))
            else:
                logger.debug(
                    f"[QTY-FIX] Fill qty={_filled_contracts:.0f} matches pre-computed "
                    f"{self._qty_lots} lots — no correction needed"
                )

            slip = (fill - snap.close) if sig.is_long else (snap.close - fill)
            slip_limit = snap.atr * MAX_ENTRY_SLIP_ATR_FRAC
            if abs(slip) > slip_limit:
                logger.warning(
                    f"[ENTRY] Large fill-vs-signal move: {slip:+.2f} pts "
                    f"({abs(slip)/snap.atr:.2f} ATR)"
                )

            # Pine source of truth: entryPrice := strategy.position_avg_price.
            # Always anchor SL/TP to the actual fill; never to the signal close.
            risk = calc_levels(
                fill, snap.atr, sig.is_long, sig.is_trend,
                entry_bar_open=snap.open,
                signal_close=snap.close,
            )

            self._in_position  = True
            self._risk         = risk
            self._signal_type  = sig.signal_type.value
            
            # current_sl = risk.sl (= fill/position_avg_price ± ATR×atrMult)
            # DO NOT use entry+trail_pts here — that is the activation distance,
            # not the initial stop loss. Using it set SL ~80 pts tighter than Pine,
            # causing instant stop-outs when price reversed before trail armed.
            self._trail_state  = TrailState(
                stage        = 0,
                current_sl   = risk.sl,   # ← correct Pine initial SL
                peak_price   = fill,
                trail_armed  = False,
                best_price   = 0.0,
            )

            self._trail_mon.start(
                risk_levels       = risk,
                trail_state       = self._trail_state,
                entry_bar_time_ms = int(time.time() * 1000),
                on_trail_exit     = self._on_trail_exit,
                signal_bar_high   = snap.high,
                signal_bar_low    = snap.low,
                signal_bar_open   = snap.open,
                signal_bar_close  = snap.close,
            )

            try:
                _tf_str  = CANDLE_TIMEFRAME
                _unit    = _tf_str[-1]
                _n       = int(_tf_str[:-1])
                _mult_ms = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}.get(_unit, 60_000)
                _period_ms      = _n * _mult_ms
                # `snap.timestamp` is the OPEN time of the signal candle. The
                # market entry fills at the next candle open, so the entry bar
                # ends one full period after that: signal_open + 2*period.
                _entry_bar_end  = int(snap.timestamp) + (2 * _period_ms)
                self._entry_bar_boundary_ms = _entry_bar_end
                self._trail_mon.set_entry_bar_boundary(_entry_bar_end)
            except Exception as _gge:
                pass

            logger.info(
                f"[ENTRY] Filled | type={sig.signal_type.value}  "
                f"fill={fill:.2f}  sl={risk.sl:.2f}  tp={risk.tp:.2f}  "
                f"atr={snap.atr:.2f}  stop_dist={risk.stop_dist:.2f}"
            )

            try:
                self._journal.log_order_event(
                    event="ENTRY_FILLED",
                    order_id=str(order.get("id") or ""),
                    signal_type=sig.signal_type.value,
                    is_long=sig.is_long,
                    qty=self._qty_lots,
                    expected_price=snap.close,
                    fill_price=fill,
                    sl=risk.sl,
                    tp=risk.tp,
                    atr=snap.atr,
                    reason="Strategy entry",
                    source="bar_signal",
                )
            except Exception as _gse:
                logger.warning(f"[GSHEET] entry event failed: {_gse}")

            try:
                self._journal.open_trade(
                    signal_type = sig.signal_type.value,
                    is_long     = sig.is_long,
                    entry_price = fill,
                    sl          = risk.sl,
                    tp          = risk.tp,
                    atr         = snap.atr,
                    qty         = self._qty_lots,
                )
            except Exception:
                pass

            await self._telegram.notify_entry(
                signal_type = sig.signal_type.value,
                entry_price = fill,
                sl          = risk.sl,
                tp          = risk.tp,
                atr         = snap.atr,
                qty         = self._qty_lots,
            )

    async def _on_trail_exit(self, exit_price: float, reason: str, source: str = "tick", position_already_closed: bool = False) -> None:
        if not self._in_position:
            return

        if not position_already_closed:
            logger.warning(
                f"[EXIT] ⚠️  _on_trail_exit called with position_already_closed=False "
                f"— reason={reason} source={source}. "
            )

        risk = self._risk
        _exit_order = self._order_mgr.last_exit_order() or {}
        exit_fee = self._order_mgr.order_fee_usd(_exit_order)
        pl = (
            calc_real_pl(
                risk.entry_price, exit_price, risk.is_long, self._qty_lots,
                entry_fee=self._entry_fee_usd, exit_fee=exit_fee,
            )
            if risk else 0.0
        )

        logger.info(
            f"[EXIT] reason={reason}  source={source}  "
            f"entry={risk.entry_price if risk else '?'}  "
            f"exit={exit_price:.2f}  net_pl={pl:+.6f} USD  "
            f"entry_fee={'actual' if self._entry_fee_usd is not None else 'modeled'}  "
            f"exit_fee={'actual' if exit_fee is not None else 'modeled'}"
        )

        try:
            self._journal.log_order_event(
                event="EXIT_FILLED" if _exit_order else "EXIT_RECOVERED",
                order_id=str(_exit_order.get("id") or ""),
                signal_type=self._signal_type,
                is_long=risk.is_long if risk else None,
                qty=self._qty_lots,
                expected_price=exit_price,
                fill_price=float(_exit_order.get("average") or _exit_order.get("price") or exit_price),
                sl=risk.sl if risk else 0.0,
                tp=risk.tp if risk else 0.0,
                atr=risk.atr if risk else 0.0,
                reason=reason,
                source=source,
            )
        except Exception as _gse:
            logger.warning(f"[GSHEET] exit event failed: {_gse}")

        try:
            if risk:
                self._journal.log_trade(
                    signal_type = self._signal_type,
                    is_long     = risk.is_long,
                    entry_price = risk.entry_price,
                    exit_price  = exit_price,
                    sl          = risk.sl,
                    tp          = risk.tp,
                    atr         = risk.atr,
                    qty         = self._qty_lots,
                    real_pl     = pl,
                    exit_reason = reason,
                    trail_stage = self._trail_state.stage if self._trail_state else 0,
                )
                self._journal.close_open_trade()
        except Exception as e:
            logger.warning(f"[JOURNAL] log_trade failed: {e}")

        try:
            await self._telegram.notify_exit(
                reason      = reason,
                entry_price = risk.entry_price if risk else 0.0,
                exit_price  = exit_price,
                real_pl     = pl,
                is_long     = risk.is_long if risk else True,
                qty         = self._qty_lots,
            )
        except Exception:
            pass

        self._in_position  = False
        self._risk         = None
        self._trail_state  = None
        self._signal_type  = "None"
        self._entry_fee_usd = None

    async def run(self) -> None:
        await self.initialize()

        if self._tg_ctrl: self._tg_ctrl_task = asyncio.create_task(self._tg_ctrl.run())
        # self._wa_ctrl_task = asyncio.create_task(self._wa_ctrl.run())  # disabled

        feed = CandleFeed(
            on_bar_close  = self._on_bar_close,
            on_feed_ready = self._feed_ready,
        )
        feed.trail_monitor = self._trail_mon
        self._feed = feed

        if BINANCE_SIGNAL_FEED:
            self._binance_px_feed = BinancePriceFeed(self._trail_mon)
            self._binance_px_feed.start_task()

        self._fills_feed = FillsFeed(
            trail_monitor = self._trail_mon,
            order_manager = self._order_mgr,
        )
        self._fills_feed.start_task()

        _heartbeat.start(os.path.dirname(os.path.abspath(__file__)))
        # Dashboard intentionally disabled for this deployment. The user does not
        # use it, and disabling it removes the 0.0.0.0:8081 port conflict without
        # changing any trading/feed/order behavior.
        logger.info("Client dashboard disabled — trading engine only")
        try:
            await feed.start()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

def _start_client_dashboard() -> None:
    """
    Launch the ADX Regime ATR Executor FastAPI dashboard (dashboard/main.py)
    in a daemon thread so it doesn't block the async event loop.
    """
    import importlib
    import logging as _log

    logger = _log.getLogger(__name__)
    try:
        import uvicorn
        from dashboard.main import app as _client_app

        cfg = uvicorn.Config(
            _client_app,
            host="0.0.0.0",
            port=int(__import__("os").environ.get("CLIENT_DASHBOARD_PORT", "8081")),
            log_level="warning",
        )
        server = uvicorn.Server(cfg)

        def _run():
            import asyncio
            asyncio.run(server.serve())

        t = _threading.Thread(target=_run, daemon=True, name="client-dashboard")
        t.start()
        _p = int(__import__("os").environ.get("CLIENT_DASHBOARD_PORT", "8081"))
        logger.info(f"ADX Regime ATR Executor dashboard LIVE → http://0.0.0.0:{_p}")
    except Exception as exc:
        logger.warning(f"[CLIENT DASH] Could not start client dashboard: {exc}")


async def _main() -> None:
    bot  = ADXRegimeATRExecutor()
    loop = asyncio.get_running_loop()

    def _handle_signal(sig_num: int) -> None:
        for task in asyncio.all_tasks(loop):
            if task.get_name() != "bot_run":
                task.cancel()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, lambda sn=s: _handle_signal(sn))
        except NotImplementedError:
            pass 

    run_task = asyncio.create_task(bot.run(), name="bot_run")
    await run_task

if __name__ == "__main__":
    asyncio.run(_main())

