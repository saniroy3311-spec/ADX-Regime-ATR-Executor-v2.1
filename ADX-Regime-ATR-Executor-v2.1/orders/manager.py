"""
orders/manager.py — ADX Regime ATR Executor | PROTECTIVE-BRACKET ARCHITECTURE
══════════════════════════════════════════════════════════════════════════════
ARCHITECTURE (FIX-BRACKET-CHURN):
─────────────────────────────────────────────────────────────────────────────
Previous design pushed every trail SL tighten to Delta via PUT /v2/orders/bracket.
Delta internally replaces the order on each amendment, issuing a new order ID.
The bot's cached _bracket_order_id became stale on every update, triggering a
continuous open_order_not_found → rediscovery loop (~3 API calls per tick for
the entire duration of the trade).
New design:
• Bracket is placed ONCE at entry with the INITIAL SL only (wide safety net).
• Bracket is NEVER amended after placement.
• Python (TrailMonitor) owns all trail/BE/tighten logic and fires exits via
market close_position() on tick.
• The bracket's only job is crash/disconnect protection — if the bot dies,
Delta's bracket catches the worst-case initial SL. No stale IDs, no
amendment API calls, no rediscovery loops.
API surface used:
POST   /v2/orders/bracket  place initial SL + TP after entry fill
GET    /v2/orders          discover generated bracket legs
DELETE /v2/orders          cancel discovered bracket leg IDs on clean exit
Delta Exchange endpoints
─────────────────────────────────────────────────────────────────────────────
Live:    https://api.india.delta.exchange
Testnet: https://cdn-ind.testnet.deltaex.org
Toggle:  DELTA_TESTNET=true in .env
══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import logging
import ssl
import time
import socket
from typing import Any, Optional
from decimal import Decimal, ROUND_HALF_UP
import aiohttp
import ccxt.async_support as ccxt
from config import (
    DELTA_API_KEY, DELTA_API_SECRET, DELTA_TESTNET, DELTA_REST_URL,
    SYMBOL, ALERT_QTY, BOT_NAME, EXECUTION_MODE, LIVE_TRADING_ENABLED,
    EMERGENCY_BRACKET_ENABLED, MAX_EXIT_SLIPPAGE_ATR_PCT,
    DELTA_CONTRACT_VALUE, DELTA_PRODUCT_SYMBOL, PINE_MINTICK, BINANCE_SIGNAL_FEED, STRICT_MARKET_METADATA,
)

logger = logging.getLogger("orders.manager")

_INDIA_LIVE    = "https://api.india.delta.exchange"
_INDIA_TESTNET = "https://cdn-ind.testnet.deltaex.org"

# Phrases in ccxt / Delta error messages that mean "position is already gone"
_ALREADY_CLOSED_PHRASES = (
    "no_position_for_reduce_only",
    "no open position",
    "position not found",
    "insufficient position",
)

# Phrases that mean "bracket is already gone" (already triggered or removed)
_BRACKET_GONE_PHRASES = (
    "bracket_not_found",
    "no_bracket",
    "no bracket order",
    "bracket order not found",
    "no_open_bracket_order_for_position",
)

# ─── Exchange factory ──────────────────────────────────────────────────────────
def build_exchange() -> ccxt.delta:
    """
    Build a ccxt.delta async instance pointed at Delta India.
    Pre-injects an IPv4-only session to bypass dual-stack IPv6 errors.
    """
    base_url = DELTA_REST_URL
    ex = ccxt.delta({
        "apiKey":          DELTA_API_KEY,
        "secret":          DELTA_API_SECRET,
        "enableRateLimit": True,
        "urls": {
            "api": {
                "public":  base_url,
                "private": base_url,
            }
        },
    })
    _ssl_ctx   = ssl.create_default_context()
    _connector = aiohttp.TCPConnector(
        family=socket.AF_INET,
        ssl=_ssl_ctx,
        enable_cleanup_closed=True,
    )
    ex.session    = aiohttp.ClientSession(connector=_connector)
    ex.own_session = True   
    return ex

# ─── Retry helper ─────────────────────────────────────────────────────────────
async def _retry(coro_fn, retries: int = 3, delay: float = 1.0):
    """Retry a coroutine-producing callable on network / timeout errors."""
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn()
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            if attempt == retries:
                raise
            wait = delay * (2 ** (attempt - 1))
            logger.warning(
                f"[OM] Retry {attempt}/{retries} after {wait:.1f}s — {exc}"
            )
            await asyncio.sleep(wait)

# ─── Delta India signed REST helper (for bracket endpoints) ──────────────────
def _sign(method: str, ts: str, path: str, body: str) -> str:
    msg = (method + ts + path + body).encode()
    return hmac.new(DELTA_API_SECRET.encode(), msg, hashlib.sha256).hexdigest()

async def _signed_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    body_obj: Optional[dict] = None,
) -> dict:
    """Make a signed HTTP request to Delta India for endpoints not in ccxt."""
    base   = DELTA_REST_URL
    url    = base + path
    body   = json.dumps(body_obj) if body_obj is not None else ""
    ts     = str(int(time.time()))
    sig    = _sign(method, ts, path, body)
    headers = {
        "api-key":      DELTA_API_KEY,
        "signature":    sig,
        "timestamp":    ts,
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "User-Agent":   "adx-regime-atr-executor/2.1",
    }
    async with session.request(method, url, data=body, headers=headers, timeout=10) as resp:
        text = await resp.text()
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {"_raw": text}
        if resp.status >= 400:
            raise ccxt.ExchangeError(
                f"Delta {method} {path} returned {resp.status}: {text}"
            )
        return data

# ─── OrderManager ─────────────────────────────────────────────────────────────
class OrderManager:
    """Async Delta Exchange order manager with emergency bracket-order support."""
    def __init__(self) -> None:
        self.exchange: ccxt.delta = build_exchange()

        # Emergency-bracket state — set on entry fill, cleared on exit.
        self._product_id:    Optional[int]   = None  
        self._product_symbol: Optional[str]  = None  
        self._bracket_active:        bool    = False
        self._bracket_order_ids: set[int] = set()
        self._current_sl:    Optional[float] = None
        self._current_tp:    Optional[float] = None 
        self._is_long:       Optional[bool]  = None  
        self._current_atr:   float           = 1.0  # For slippage calculation
        self._market_price_tick: float        = 0.0
        self._market_contract_value: float    = DELTA_CONTRACT_VALUE
        self._order_qty: float                = float(ALERT_QTY)

        # Reusable HTTP session for the signed-bracket endpoints.
        self._http: Optional[aiohttp.ClientSession] = None
        
        # Strong references to prevent background tasks from being garbage collected
        self._background_tasks: set[asyncio.Task] = set()

        # Paper-mode state. Paper mode may use the live Delta endpoint/market data
        # but never mutates the exchange account.
        self._paper_position: Optional[dict] = None
        self._paper_seq: int = 0
        self._last_entry_order: Optional[dict] = None
        self._last_exit_order: Optional[dict] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────
    async def initialize(self) -> None:
        """Load markets, validate symbol, and enforce execution safety switches."""
        if EXECUTION_MODE == "live" and (not DELTA_TESTNET) and (not LIVE_TRADING_ENABLED):
            raise RuntimeError(
                "Production order routing is locked. Set LIVE_TRADING_ENABLED=true "
                "only after paper/shadow validation is complete."
            )
        if EXECUTION_MODE == "live":
            if (not DELTA_API_KEY or DELTA_API_KEY.startswith(("YOUR_", "PASTE_"))) or \
               (not DELTA_API_SECRET or DELTA_API_SECRET.startswith(("YOUR_", "PASTE_"))):
                raise RuntimeError("Live execution requires DELTA_API_KEY and DELTA_API_SECRET")
        logger.warning(
            f"[OM] execution_mode={EXECUTION_MODE.upper()}  "
            f"environment={'TESTNET' if DELTA_TESTNET else 'PRODUCTION'}  "
            f"live_switch={LIVE_TRADING_ENABLED}"
        )
        await self.exchange.load_markets()
        if SYMBOL not in self.exchange.markets:
            raise ValueError(f"SYMBOL '{SYMBOL}' not found on Delta India.")

        market = self.exchange.markets[SYMBOL]
        info   = market.get("info") or {}
        pid    = info.get("id") or info.get("product_id") or market.get("id")

        try:
            self._product_id = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            self._product_id = None
        self._product_symbol = info.get("symbol") or market.get("id") or DELTA_PRODUCT_SYMBOL

        # Pull execution metadata from the exchange instead of trusting hardcoded
        # assumptions. Delta currently documents BTCUSD as 0.001 BTC/contract and
        # a 0.5 USD tick, but live metadata is the authority at startup.
        def _as_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        ccxt_contract_size = _as_float(market.get("contractSize"), DELTA_CONTRACT_VALUE)
        ccxt_price_precision = _as_float((market.get("precision") or {}).get("price"), 0.0)
        self._market_contract_value = _as_float(info.get("contract_value"), ccxt_contract_size)
        self._market_price_tick = _as_float(info.get("tick_size"), ccxt_price_precision)

        try:
            qty_txt = self.exchange.amount_to_precision(SYMBOL, ALERT_QTY)
            self._order_qty = float(qty_txt)
        except Exception:
            self._order_qty = float(ALERT_QTY)

        metadata_errors = []
        if self._market_contract_value > 0 and abs(self._market_contract_value - DELTA_CONTRACT_VALUE) > 1e-12:
            metadata_errors.append(
                f"contract_value exchange={self._market_contract_value} config={DELTA_CONTRACT_VALUE}"
            )
        if (not BINANCE_SIGNAL_FEED) and self._market_price_tick > 0 and abs(self._market_price_tick - PINE_MINTICK) > 1e-12:
            metadata_errors.append(
                f"tick_size exchange={self._market_price_tick} PINE_MINTICK={PINE_MINTICK}"
            )
        if self._order_qty <= 0:
            metadata_errors.append(f"order quantity rounds to invalid value: {self._order_qty}")

        if metadata_errors:
            msg = "[OM] Market metadata mismatch: " + "; ".join(metadata_errors)
            if STRICT_MARKET_METADATA and EXECUTION_MODE == "live":
                raise RuntimeError(msg)
            logger.warning(msg)

        logger.info(
            f"[OM] Market metadata | symbol={SYMBOL} product={self._product_symbol} "
            f"contract_value={self._market_contract_value} tick={self._market_price_tick or 'n/a'} "
            f"order_qty={self._order_qty:g}"
        )

        if self._product_id is None:
            logger.warning(
                f"[OM] Could not resolve numeric product_id for {SYMBOL};  "
                f"bracket orders will be DISABLED for this run."
            )
        else:
            logger.info(
                f"[OM] Resolved product_id={self._product_id}  "
                f"product_symbol={self._product_symbol}"
            )

    async def close_exchange(self) -> None:
        """Close the ccxt session and the bracket-endpoint HTTP session."""
        try:
            await self.exchange.close()
        except Exception as exc:
            logger.warning(f"[OM] close_exchange error (ignored): {exc}")
        if self._http is not None:
            try:
                await self._http.close()
            except Exception as exc:
                logger.warning(f"[OM] http session close error (ignored): {exc}")
            self._http = None

    async def _http_session(self) -> aiohttp.ClientSession:
        """Lazily create the aiohttp session for bracket endpoints."""
        if self._http is None or self._http.closed:
            _connector = aiohttp.TCPConnector(family=socket.AF_INET)
            self._http = aiohttp.ClientSession(connector=_connector)
        return self._http

    def _format_price(self, price: float) -> str:
        """Return a Delta-valid price string using live market precision."""
        try:
            return str(self.exchange.price_to_precision(SYMBOL, float(price)))
        except Exception:
            tick = self._market_price_tick or PINE_MINTICK
            if tick <= 0:
                return f"{float(price):.8f}".rstrip("0").rstrip(".")
            q = (Decimal(str(price)) / Decimal(str(tick))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            snapped = q * Decimal(str(tick))
            return format(snapped.normalize(), "f")

    def _format_amount(self, amount: float) -> float:
        """Return an exchange-valid contract quantity."""
        try:
            return float(self.exchange.amount_to_precision(SYMBOL, float(amount)))
        except Exception:
            return float(amount)

    # ── Position query ────────────────────────────────────────────────────────
    async def fetch_open_position(self, strict: bool = False) -> Optional[dict]:
        """Return a simplified position dict if an open position exists, else None.

        strict=True re-raises API/network errors instead of returning None, so a
        temporary error can never be mistaken for "position is closed".
        """
        if EXECUTION_MODE == "paper":
            return dict(self._paper_position) if self._paper_position else None
        try:
            positions = await _retry(
                lambda: self.exchange.fetch_positions([SYMBOL])
            )
            for pos in positions:
                size = float(pos.get("contracts", 0) or 0)
                if abs(size) > 0 and pos.get("symbol") == SYMBOL:
                    side      = pos.get("side", "long").lower()
                    is_long   = side == "long"
                    entry_raw = (
                        pos.get("entryPrice")
                        or (pos.get("info") or {}).get("entry_price")
                        or 0.0
                    )
                    return {
                        "is_long":     is_long,
                        "entry_price": float(entry_raw),
                        "contracts":   abs(size),
                    }
        except Exception as exc:
            logger.warning(f"[OM] fetch_open_position failed: {exc}")
            if strict:
                raise
        return None

    async def fetch_position(self) -> Optional[dict]:
        """Backward-compatibility wrapper for legacy layout logic execution passes."""
        return await self.fetch_open_position()

    # ── Order placement ───────────────────────────────────────────────────────
    async def place_entry(
        self,
        is_long: bool,
        sl: float,
        tp: float,
        atr: float = 1.0,
        stop_dist: Optional[float] = None,
    ) -> dict:
        """
        Place a market entry, then attach an exchange-side initial SL + TP.
        Dynamic trail/breakeven tightening remains owned by TrailMonitor.
        """
        side = "buy" if is_long else "sell"
        logger.info(
            f"[OM] Placing entry | mode={EXECUTION_MODE} side={side}  qty={self._order_qty:g}   "
            f"sl={sl:.2f}  tp={tp:.2f}"
        )

        # ── 1. Market entry / paper fill ──
        if EXECUTION_MODE == "paper":
            ticker = await self.fetch_ticker()
            fill = float((ticker or {}).get("last") or (ticker or {}).get("markPrice") or 0.0)
            if fill <= 0:
                raise RuntimeError("Paper entry could not obtain a live market price")
            self._paper_seq += 1
            order = {
                "id": f"paper-entry-{self._paper_seq}", "average": fill, "price": fill,
                "amount": self._order_qty, "filled": self._order_qty,
                "status": "closed", "side": side, "paper": True,
            }
            self._paper_position = {
                "is_long": is_long, "entry_price": fill, "contracts": self._order_qty
            }
            logger.warning(f"[OM] PAPER entry filled @ {fill:.2f}; no exchange order was sent")
        else:
            order = await _retry(lambda: self.exchange.create_order(
                symbol=SYMBOL,
                type="market",
                side=side,
                amount=self._order_qty,
            ))
        fill = float(order.get("average") or order.get("price") or 0.0)
        self._last_entry_order = dict(order) if isinstance(order, dict) else {"raw": str(order)}
        logger.info(f"[OM] Entry filled | id={order.get('id')}  fill={fill:.2f}")

        # ── 2. Cache state ──
        self._is_long          = is_long
        self._current_sl       = float(sl)
        self._current_tp       = float(tp)
        self._bracket_active   = False 
        self._current_atr      = atr if atr > 0 else 1.0

        # ── 3. Optional initial SL+TP bracket (crash/disconnect protection) ──
        if EXECUTION_MODE == "paper":
            return order
        if not EMERGENCY_BRACKET_ENABLED:
            logger.info("[OM] Emergency bracket disabled by config; Python owns all exits.")
            return order

        if self._product_id is None:
            logger.warning("[OM] Emergency bracket disabled (no product_id).")
            return order

        try:
            # Re-anchor the pre-entry Pine levels to the ACTUAL market fill.
            # risk_pre was built around the signal close; stop_dist lets us
            # reconstruct that anchor without changing place_entry's API.
            sl_dist = float(stop_dist) if stop_dist and stop_dist > 0 else abs(sl - fill)
            pre_anchor = (sl + sl_dist) if is_long else (sl - sl_dist)
            fill_shift = fill - pre_anchor
            exact_sl = sl + fill_shift
            exact_tp = tp + fill_shift

            await self._place_bracket(sl=exact_sl, tp=exact_tp)
            self._current_sl = exact_sl
            self._current_tp = exact_tp
            self._bracket_active = True
            logger.info(
                f"[OM] ✅ Initial protective bracket placed on Delta | "
                f"fill={fill:.2f} sl={exact_sl:.2f} tp={exact_tp:.2f} "
                f"stop_dist={sl_dist:.2f}"
            )
        except Exception as exc:
            logger.error(
                f"[OM] ⚠️  Emergency bracket FAILED — trade is open with no  "
                f"exchange-side safety net. TrailMonitor is sole protection. Error: {exc}"
            )

        return order

    # ── Bracket management ─────────────────────────────────────────────────────
    async def _place_bracket(self, sl: float, tp: float) -> dict:
        """POST /v2/orders/bracket with market SL and market TP triggers.

        Delta's schema requires exactly one product selector. We use product_id
        because it was resolved from live market metadata at startup.
        """
        body = {
            "product_id": self._product_id,
            "stop_loss_order": {
                "order_type": "market_order",
                "stop_price": self._format_price(sl),
            },
            "take_profit_order": {
                "order_type": "market_order",
                "stop_price": self._format_price(tp),
            },
            "bracket_stop_trigger_method": "last_traded_price",
        }
        session = await self._http_session()
        result = await _signed_request(session, "POST", "/v2/orders/bracket", body)

        # The position-bracket endpoint returns success rather than leg IDs. Cache
        # the generated bracket leg IDs via GET /orders so later cancellation can
        # delete only this bot's bracket legs instead of all user orders.
        try:
            orders = await self._discover_bracket_orders()
            self._bracket_order_ids = {
                int(o["id"]) for o in orders if o.get("id") is not None
            }
        except Exception as exc:
            logger.warning(f"[OM] Bracket placed but leg discovery was delayed/failed: {exc}")
        return result

    async def _discover_bracket_orders(self) -> list[dict]:
        """Return active bracket-generated order legs for this product."""
        if self._product_id is None:
            return []
        session = await self._http_session()
        path = (
            f"/v2/orders?product_ids={self._product_id}"
            "&states=open,pending&page_size=100"
        )
        data = await _signed_request(session, "GET", path)
        orders = data.get("result") or []
        if isinstance(orders, dict):
            orders = orders.get("data") or orders.get("orders") or []
        out: list[dict] = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            try:
                pid = int(order.get("product_id"))
            except (TypeError, ValueError):
                pid = None
            if pid != self._product_id:
                continue
            bracket_flag = order.get("bracket_order")
            if bracket_flag is True or str(bracket_flag).lower() == "true":
                out.append(order)
        return out

    async def _cancel_order_id(self, order_id: int) -> None:
        """Cancel one Delta order by ID using the documented DELETE /orders API."""
        if self._product_id is None:
            return
        session = await self._http_session()
        body = {"id": int(order_id), "product_id": self._product_id}
        await _signed_request(session, "DELETE", "/v2/orders", body)

    async def cancel_bracket(self) -> None:
        """Cancel only this product's active bracket-generated legs.

        Delta documents POST/PUT for /orders/bracket but no DELETE endpoint for
        a position bracket. A position bracket creates ordinary active order
        legs, so cancellation is performed through DELETE /orders for those IDs.
        """
        if EXECUTION_MODE == "paper":
            self._bracket_active = False
            self._bracket_order_ids.clear()
            return
        if self._product_id is None:
            self._bracket_active = False
            self._bracket_order_ids.clear()
            return

        try:
            orders = await self._discover_bracket_orders()
            discovered_ids = {
                int(o["id"]) for o in orders if o.get("id") is not None
            }
            ids = sorted(self._bracket_order_ids | discovered_ids)
            if not ids:
                logger.info("[OM] No active bracket legs found for cancellation")
            for oid in ids:
                try:
                    await self._cancel_order_id(oid)
                    logger.info(f"[OM] ✅ Cancelled bracket leg id={oid}")
                except Exception as exc:
                    msg = str(exc).lower()
                    if any(p in msg for p in _BRACKET_GONE_PHRASES) or "not_found" in msg:
                        logger.info(f"[OM] Bracket leg id={oid} already gone")
                    else:
                        logger.warning(f"[OM] Failed to cancel bracket leg id={oid}: {exc}")
        except Exception as exc:
            logger.warning(f"[OM] cancel_bracket discovery failed (ignored): {exc}")
        finally:
            self._bracket_active   = False
            self._bracket_order_ids.clear()
            self._current_sl       = None
            self._current_tp       = None
            self._is_long          = None

    # ── Order management ──────────────────────────────────────────────────────
    async def cancel_all_orders(self) -> None:
        """Cancel all open orders for the bot product using Delta's /orders/all endpoint.

        This is intentionally broader than cancel_bracket() and should be used
        only for an explicit clean-slate operation on a dedicated bot product.
        """
        if EXECUTION_MODE == "paper":
            return
        try:
            if self._product_id is not None:
                body = {
                    "product_id": self._product_id,
                    "cancel_limit_orders": True,
                    "cancel_stop_orders": True,
                    "cancel_reduce_only_orders": True,
                }
                session = await self._http_session()
                await _signed_request(session, "DELETE", "/v2/orders/all", body)
                logger.debug("[OM] cancel_all_orders: done")
            else:
                logger.debug("[OM] cancel_all_orders: no product_id yet — skipping")
        except Exception as exc:
            logger.warning(f"[OM] cancel_all_orders failed (ignored): {exc}")
        finally:
            self._bracket_active = False
            self._bracket_order_ids.clear()

    async def close_position(
        self,
        is_long: bool,
        reason: str = "Exit",
        expected_price: Optional[float] = None,
    ) -> dict:
        """Close position with reduce-only market order and sweep up safety bracket."""
        side = "sell" if is_long else "buy"
        logger.info(f"[OM] Closing position | mode={EXECUTION_MODE} side={side}  reason={reason}")

        if EXECUTION_MODE == "paper":
            # PINE-EXIT-PARITY:
            # The risk engine has already calculated the Pine-equivalent stop,
            # TP, BE or trailing-stop level. Paper mode must record THAT level.
            # Fetching a newer ticker here can give back 50-200+ points during a
            # fast reversal and makes a correct strategy exit look wrong.
            if expected_price is not None and float(expected_price) > 0:
                fill = float(expected_price)
                fill_source = "strategy_exit_level"
            else:
                ticker = await self.fetch_ticker()
                fill = float((ticker or {}).get("last") or (ticker or {}).get("markPrice") or 0.0)
                fill_source = "ticker_fallback"

            if fill <= 0:
                raise RuntimeError("Paper exit could not determine an exit price")

            qty = float((self._paper_position or {}).get("contracts") or self._order_qty)
            self._paper_seq += 1
            order = {
                "id": f"paper-exit-{self._paper_seq}", "average": fill, "price": fill,
                "amount": qty, "filled": qty, "status": "closed", "side": side,
                "reduce_only": True, "paper": True, "fill_source": fill_source,
            }
            self._paper_position = None
            self._last_exit_order = dict(order)
            logger.warning(
                f"[OM] PAPER exit filled @ {fill:.2f} "
                f"(source={fill_source}, reason={reason}); no exchange order was sent"
            )
            return order
        
        # FIX: Slippage check before closing
        if expected_price and self._current_atr > 0:
            try:
                ticker = await self.fetch_ticker()
                if ticker:
                    current_price = float(ticker.get("last") or ticker.get("markPrice") or 0)
                    if current_price > 0:
                        slippage_pts = abs(current_price - expected_price)
                        slippage_atr_pct = (slippage_pts / self._current_atr) * 100
                        
                        logger.info(f"[OM] Slippage check: {slippage_pts:.2f}pts ({slippage_atr_pct:.1f}% ATR)")
                        
                        if slippage_atr_pct > MAX_EXIT_SLIPPAGE_ATR_PCT:
                            logger.critical(
                                f"[OM] ⚠️ HIGH SLIPPAGE: {slippage_pts:.2f}pts ({slippage_atr_pct:.1f}% ATR) | "
                                f"Expected: {expected_price}, Current: {current_price}"
                            )
            except Exception as e:
                logger.warning(f"[OM] Slippage check failed: {e}")
        
        try:
            pos = await self.fetch_open_position()
            close_qty = float(pos.get("contracts", 0)) if pos else self._order_qty
            if close_qty <= 0:
                close_qty = self._order_qty
            close_qty = self._format_amount(close_qty)
            order = await _retry(lambda: self.exchange.create_order(
                symbol=SYMBOL,
                type="market",
                side=side,
                amount=close_qty,
                params={"reduce_only": True},
            ))
            fill = float(order.get("average") or order.get("price") or 0.0)
            self._last_exit_order = dict(order) if isinstance(order, dict) else {"raw": str(order)}
            logger.info(f"[OM] Position closed | id={order.get('id')}  fill={fill:.2f}")
            return order
        except ccxt.ExchangeError as exc:
            msg = str(exc).lower()
            if any(phrase in msg for phrase in _ALREADY_CLOSED_PHRASES):
                logger.info(f"[OM] close_position: position already gone. Returning sentinel.")
                return {"info": "already_closed"}
            raise

    @staticmethod
    def order_fee_usd(order: Optional[dict]) -> Optional[float]:
        """Best-effort extraction of the fee actually reported by Delta/ccxt.

        Returns None when the exchange response does not contain a settled fee.
        The caller can then fall back to the configured fee model.
        """
        if not isinstance(order, dict):
            return None
        try:
            fee = order.get("fee")
            if isinstance(fee, dict) and fee.get("cost") is not None:
                value = abs(float(fee["cost"]))
                if value >= 0:
                    return value
            fees = order.get("fees")
            if isinstance(fees, list) and fees:
                vals = []
                for item in fees:
                    if isinstance(item, dict) and item.get("cost") is not None:
                        vals.append(abs(float(item["cost"])))
                if vals:
                    return sum(vals)
            raw_top = order.get("paid_commission")
            if raw_top not in (None, ""):
                return abs(float(raw_top))
            info = order.get("info") or {}
            if isinstance(info, dict):
                raw = info.get("paid_commission")
                if raw not in (None, ""):
                    return abs(float(raw))
        except (TypeError, ValueError):
            return None
        return None

    def last_entry_order(self) -> Optional[dict]:
        return dict(self._last_entry_order) if self._last_entry_order else None

    def last_exit_order(self) -> Optional[dict]:
        return dict(self._last_exit_order) if self._last_exit_order else None

    # ── Price feed / Recovery metrics ──────────────────────────────────────────
    async def fetch_ticker(self) -> Optional[dict]:
        """Fetch current asset quote mark data."""
        try:
            ticker = await _retry(lambda: self.exchange.fetch_ticker(SYMBOL))
            return ticker
        except Exception as exc:
            logger.warning(f"[OM] fetch_ticker failed: {exc}")
            return None

    async def fetch_bracket_fill_price(self) -> Optional[float]:
        """Fetch exact executed price data from history if bracket triggered silently."""
        if self._product_symbol is None:
            return None

        session = await self._http_session()
        close_side = "sell" if (self._is_long is not False) else "buy"

        # Layer 1: Fill History Match
        try:
            fills_path = f"/v2/fills?product_symbol={self._product_symbol}&page_size=5"
            data = await _signed_request(session, "GET", fills_path)
            fills = (data.get("result") or [])
            if isinstance(fills, dict):
                fills = fills.get("data") or fills.get("fills") or []
            for fill in fills:
                if not isinstance(fill, dict):
                    continue
                if str(fill.get("side") or " ").lower() == close_side:
                    price_raw = fill.get("fill_price") or fill.get("price") or fill.get("average")
                    if price_raw and float(price_raw) > 0:
                        self._last_exit_order = dict(fill)
                        return float(price_raw)
        except Exception as exc:
            logger.warning(f"[OM] fetch_bracket_fill_price layer-1 failed: {exc}")

        # Layer 2: Order State Audit Match
        try:
            hist_path = f"/v2/history/orders?product_symbol={self._product_symbol}&states=filled&page_size=10"
            data = await _signed_request(session, "GET", hist_path)
            orders = (data.get("result") or [])
            if isinstance(orders, dict):
                orders = orders.get("data") or orders.get("orders") or []
            for order in orders:
                if not isinstance(order, dict):
                    continue
                if str(order.get("side") or " ").lower() == close_side and "fill" in str(order.get("state") or " ").lower():
                    price_raw = order.get("average_fill_price") or order.get("average") or order.get("price")
                    if price_raw and float(price_raw) > 0:
                        self._last_exit_order = dict(order)
                        return float(price_raw)
        except Exception as exc:
            logger.warning(f"[OM] fetch_bracket_fill_price layer-2 failed: {exc}")

        return None
