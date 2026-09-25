from fastapi import FastAPI, HTTPException, Request, Query, Response
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
import secrets
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import os
import json
import httpx
from datetime import datetime, timedelta
from typing import Optional, List
from dashboard import database
from config import DELTA_REST_URL, DELTA_PRODUCT_SYMBOL, BINANCE_SIGNAL_FEED, BINANCE_SYMBOL, CANDLE_TIMEFRAME

# Resolve runtime file paths to repo root (parent of dashboard/)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _repo(filename: str) -> str:
    return os.path.join(_REPO_ROOT, filename)

app = FastAPI(title="ADX Regime ATR Executor Dashboard API")

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "CHANGE_THIS_PASSWORD")
SESSIONS = {}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    open_paths = ["/login", "/logout", "/health"]
    if request.url.path in open_paths or request.url.path.startswith("/static"):
        return await call_next(request)
    token = request.cookies.get("session")
    if not (token and SESSIONS.get(token)):
        if request.url.path.startswith("/api"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    err_html = "<div class=\"error\"><span>&#9888;</span> Invalid user ID or password</div>" if error else ""
    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
<title>ADX Regime ATR Executor &#8212; Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#F7F9F8;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:'Inter',sans-serif;}}
.wrapper{{width:100%;max-width:420px;padding:20px;}}
.card{{background:#fff;border:1px solid #E8EDEB;border-radius:20px;padding:48px 40px;}}
.logo-wrap{{display:flex;align-items:center;gap:14px;margin-bottom:32px;}}
.logo-text{{font-size:20px;font-weight:600;color:#0F1C17;}}
.logo-sub{{font-size:12px;color:#9BADA6;margin-top:3px;}}
.divider{{height:1px;background:#E8EDEB;margin-bottom:28px;}}
h2{{font-size:20px;font-weight:600;color:#0F1C17;margin-bottom:6px;}}
.subtitle{{font-size:13px;color:#6B7C75;margin-bottom:28px;}}
label{{font-size:11px;font-weight:600;color:#6B7C75;display:block;margin-bottom:6px;letter-spacing:0.5px;text-transform:uppercase;}}
input{{width:100%;background:#F7F9F8;border:1px solid #E8EDEB;border-radius:10px;padding:12px 14px;color:#0F1C17;font-size:14px;outline:none;margin-bottom:16px;}}
input:focus{{border-color:#00A878;background:#fff;}}
.error{{background:#fff5f5;border:1px solid #fca5a5;color:#dc2626;padding:11px 14px;border-radius:10px;font-size:13px;margin-bottom:16px;}}
button{{width:100%;background:#00A878;color:#fff;font-weight:600;font-size:14px;padding:13px;border:none;border-radius:10px;cursor:pointer;margin-top:4px;}}
button:hover{{background:#008f65;}}
.footer{{text-align:center;margin-top:20px;font-size:12px;color:#9BADA6;}}
</style>
</head>
<body>
<div class="wrapper"><div class="card">
  <div class="logo-wrap">
    <svg width="48" height="48" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="48" height="48" rx="12" fill="#00A878"/>
      <path d="M14 30L19 16L24 26L29 12L34 30" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="24" cy="35" r="2.5" fill="white"/>
    </svg>
    <div><div class="logo-text">ADX Regime ATR Executor</div><div class="logo-sub">Trading Dashboard</div></div>
  </div>
  <div class="divider"></div>
  <h2>Welcome back</h2>
  <p class="subtitle">Sign in to access your trading dashboard</p>
  {err_html}
  <form method="POST" action="/login">
    <label>User ID</label>
    <input type="text" name="username" placeholder="Enter your user ID" autofocus required>
    <label>Password</label>
    <input type="password" name="password" placeholder="Enter your password" required>
    <button type="submit">Sign In &rarr;</button>
  </form>
</div>
<div class="footer">&copy; 2026 ADX Regime ATR Executor &middot; Secured Dashboard</div>
</div>
</body></html>""")

@app.post("/login")
async def login(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    if username == DASHBOARD_USER and password == DASHBOARD_PASS:
        token = secrets.token_hex(32)
        SESSIONS[token] = True
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie("session", token, httponly=True, max_age=86400*7)
        return response
    return RedirectResponse(url="/login?error=1", status_code=302)

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        SESSIONS.pop(token, None)
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure databases are initialized on startup
@app.on_event("startup")
def startup_event():
    database.init_databases()

# Helper to fetch database connection dict rows
def get_db_rows(db_name=None, query="", params=()):
    conn = database.get_db_connection(db_name)
    cursor = conn.execute(query, tuple(params))
    rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    conn.close()
    return rows

def execute_db_write(db_name=None, query="", params=()):
    conn = database.get_db_connection(db_name)
    cursor = conn.execute(query, tuple(params))
    conn.commit()
    last_id = cursor.lastrowid
    conn.close()
    return last_id

# Pydantic Schemas
class ClientSchema(BaseModel):
    name: str
    qty_allocated: int
    capital: Optional[float] = 0.0
    start_date: str
    status: str
    profit_share: float
    fee_cap: Optional[float] = None
    floor: int = 1
    billing_cycle: str
    currency: str = "USD"
    notes: Optional[str] = ""

class SettingsUpdateSchema(BaseModel):
    business_name: str
    total_bot_lots: int
    default_billing_cycle: str
    usd_inr_rate: float
    whatsapp_webhook: str
    daily_drawdown_limit: float
    heartbeat_timeout: int

class InvoiceCreateSchema(BaseModel):
    client_id: int
    period: str
    trades_count: int
    gross_pnl: float
    fees: float
    net_pnl: float
    our_fee: float
    net_payout: float
    payment_method: str
    notes: Optional[str] = ""

# Serve frontend static files
@app.get("/", response_class=HTMLResponse)
def get_index():
    _here = os.path.dirname(os.path.abspath(__file__))
    if os.path.exists(os.path.join(_here, "index.html")):
        return FileResponse(os.path.join(_here, "index.html"))
    return "<h3>index.html not found!</h3>"

@app.get("/styles.css")
def get_styles():
    _here = os.path.dirname(os.path.abspath(__file__))
    _path = os.path.join(_here, "styles.css")
    if os.path.exists(_path):
        return FileResponse(_path)
    return HTTPException(status_code=404, detail="styles.css not found")

# Lightweight unauthenticated health endpoint for systemd/PM2/VPS checks.
@app.get("/health")
def health():
    return {"ok": True, "name": "ADX Regime ATR Executor"}

# --- API ENDPOINTS ---

# 1. Dashboard Health & Live Status
@app.get("/api/status")
async def get_status():
    # Read health.json written by the bot's main.py heartbeat
    health_data = {}
    health_path = _repo("health.json")
    
    try:
        with open(health_path, "r") as f:
            health_data = json.load(f)
    except Exception:
        health_data = {"timestamp": 0, "cpu": 0, "ram": 0, "ws_delta": False, "last_tick_age_s": 999}

    # Fetch active settings
    settings_rows = get_db_rows(database.CLIENTS_DB, "SELECT key, value FROM settings")
    settings = {r["key"]: r["value"] for r in settings_rows}
    
    # Calculate bot uptime/status
    now_ts = int(datetime.now().timestamp())
    hb_timeout = int(settings.get("heartbeat_timeout", 120))
    
    bot_live = False
    status_text = "STOPPED"
    if health_data.get("timestamp", 0) > 0:
        age = now_ts - health_data["timestamp"]
        if age < hb_timeout:
            bot_live = True
            status_text = "LIVE"
        elif age < hb_timeout * 3:
            status_text = "ERROR"
        else:
            status_text = "STOPPED"
            
    # Try calling Delta Exchange REST API for the current BTCUSD price.
    # No synthetic price is presented as live data; 0 means unavailable.
    btc_price = 0.0
    open_position = {"side": "FLAT", "entry_price": 0.0, "qty": 0, "unrealised_pnl": 0.0}
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{DELTA_REST_URL}/v2/tickers/{DELTA_PRODUCT_SYMBOL}", timeout=2.0)
            if res.status_code == 200:
                data = res.json()
                btc_price = float(data.get("result", {}).get("close", 0.0))
    except Exception:
        pass
        
    # Read the bot's persisted open trade from the same journal used by main.py.
    try:
        rows = get_db_rows(
            database.JOURNAL_DB,
            "SELECT signal_type,is_long,entry_price,sl,tp,atr,qty,trail_stage,current_sl,opened_at "
            "FROM open_trades ORDER BY id DESC LIMIT 1",
        )
        if rows:
            r = rows[0]
            open_position = {
                "side": "LONG" if bool(r.get("is_long")) else "SHORT",
                "is_long": bool(r.get("is_long")),
                "entry_price": float(r.get("entry_price") or 0),
                "qty": int(r.get("qty") or 0),
                "sl": float(r.get("sl") or 0),
                "current_sl": float(r.get("current_sl") or 0),
                "tp": float(r.get("tp") or 0),
                "trail_stage": int(r.get("trail_stage") or 0),
                "signal_type": r.get("signal_type") or "",
                "opened_at": r.get("opened_at") or "",
                "unrealised_pnl": 0.0,
            }
    except Exception:
        pass
            
    return {
        "bot_status": status_text,
        "last_heartbeat_ago": now_ts - health_data.get("timestamp", now_ts),
        "health": health_data,
        "btc_price": btc_price,
        "open_position": open_position,
        "settings": settings
    }

# 2. Trades API
@app.get("/api/trades")
def get_trades(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    side: Optional[str] = None,
    result: Optional[str] = None,
    min_pnl: Optional[float] = None,
    max_pnl: Optional[float] = None,
    min_points: Optional[float] = None,
    tag: Optional[str] = None
):
    query = "SELECT id, ts as exit_time, ts as entry_time, CASE WHEN is_long=1 THEN 'LONG' ELSE 'SHORT' END as side, entry_price, exit_price, qty, real_pl as net_pnl, real_pl as gross_pnl, 0 as fee, points_captured, exit_reason, signal_type FROM trades WHERE 1=1"
    params = []
    
    if start_date:
        query += " AND ts >= ?"
        params.append(start_date)
    if end_date:
        query += " AND ts <= ?"
        params.append(end_date)
    if side and side != "ALL":
        query += " AND is_long = ?"
        params.append(1 if side.upper() == "LONG" else 0)
    if result:
        if result == "WINNERS":
            query += " AND real_pl > 0"
        elif result == "LOSERS":
            query += " AND real_pl <= 0"
    if min_pnl is not None:
        query += " AND real_pl >= ?"
        params.append(min_pnl)
    if max_pnl is not None:
        query += " AND real_pl <= ?"
        params.append(max_pnl)
    if min_points is not None:
        query += " AND ABS(points_captured) >= ?"
        params.append(min_points)
    if tag:
        query += " AND exit_reason LIKE ?"
        params.append(f"%{tag}%")
        
    query += " ORDER BY ts DESC"
    trades = get_db_rows(database.JOURNAL_DB, query, params)
    return trades

# 3. Client Management CRUD
@app.get("/api/clients")
def get_clients():
    return get_db_rows(database.CLIENTS_DB, "SELECT * FROM clients")

@app.post("/api/clients")
def create_client(client: ClientSchema):
    # Check total capacity allocation
    settings_rows = get_db_rows(database.CLIENTS_DB, "SELECT value FROM settings WHERE key='total_bot_lots'")
    total_capacity = int(settings_rows[0]["value"]) if settings_rows else 100
    
    allocated_rows = get_db_rows(database.CLIENTS_DB, "SELECT SUM(qty_allocated) as total FROM clients WHERE status IN ('Active', 'Owner')")
    allocated = allocated_rows[0]["total"] or 0
    
    if client.status in ("Active", "Owner") and (allocated + client.qty_allocated > total_capacity):
        raise HTTPException(status_code=400, detail=f"Capacity exceeded! Total lots: {total_capacity}. Currently allocated: {allocated}. Requested: {client.qty_allocated}.")

    client_id = execute_db_write(
        database.CLIENTS_DB,
        """
        INSERT INTO clients (name, qty_allocated, capital, start_date, status, profit_share, fee_cap, floor, billing_cycle, currency, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (client.name, client.qty_allocated, client.capital, client.start_date, client.status, client.profit_share, client.fee_cap, client.floor, client.billing_cycle, client.currency, client.notes)
    )
    
    # Audit log
    execute_db_write(
        database.CLIENTS_DB,
        "INSERT INTO audit_logs (client_id, timestamp, change_description) VALUES (?, ?, ?)",
        (client_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Client onboarding. Assigned {client.qty_allocated} lots and capital of ${client.capital}.")
    )
    return {"id": client_id, "message": "Client created successfully"}

@app.put("/api/clients/{client_id}")
def update_client(client_id: int, client: ClientSchema):
    # Check total capacity allocation (excluding current client if active)
    settings_rows = get_db_rows(database.CLIENTS_DB, "SELECT value FROM settings WHERE key='total_bot_lots'")
    total_capacity = int(settings_rows[0]["value"]) if settings_rows else 100
    
    allocated_rows = get_db_rows(database.CLIENTS_DB, "SELECT SUM(qty_allocated) as total FROM clients WHERE status IN ('Active', 'Owner') AND id != ?", (client_id,))
    allocated = allocated_rows[0]["total"] or 0
    
    if client.status in ("Active", "Owner") and (allocated + client.qty_allocated > total_capacity):
        raise HTTPException(status_code=400, detail="Capacity exceeded!")
 
    # Fetch original client for audit logging
    original = get_db_rows(database.CLIENTS_DB, "SELECT * FROM clients WHERE id = ?", (client_id,))
    if not original:
        raise HTTPException(status_code=404, detail="Client not found")
        
    orig = original[0]
    changes = []
    if orig["qty_allocated"] != client.qty_allocated:
        changes.append(f"Lots changed from {orig['qty_allocated']} to {client.qty_allocated}")
    if orig.get("capital", 0.0) != client.capital:
        changes.append(f"Capital changed from ${orig.get('capital', 0.0)} to ${client.capital}")
    if orig["status"] != client.status:
        changes.append(f"Status changed from {orig['status']} to {client.status}")
    if orig["profit_share"] != client.profit_share:
        changes.append(f"Profit share changed from {orig['profit_share']}% to {client.profit_share}%")
        
    execute_db_write(
        database.CLIENTS_DB,
        """
        UPDATE clients
        SET name=?, qty_allocated=?, capital=?, start_date=?, status=?, profit_share=?, fee_cap=?, floor=?, billing_cycle=?, currency=?, notes=?
        WHERE id=?
        """,
        (client.name, client.qty_allocated, client.capital, client.start_date, client.status, client.profit_share, client.fee_cap, client.floor, client.billing_cycle, client.currency, client.notes, client_id)
    )
    
    if changes:
        execute_db_write(
            database.CLIENTS_DB,
            "INSERT INTO audit_logs (client_id, timestamp, change_description) VALUES (?, ?, ?)",
            (client_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ", ".join(changes))
        )
        
    return {"message": "Client updated successfully"}

@app.delete("/api/clients/{client_id}")
def delete_client(client_id: int):
    execute_db_write(database.CLIENTS_DB, "DELETE FROM clients WHERE id = ?", (client_id,))
    execute_db_write(database.CLIENTS_DB, "DELETE FROM audit_logs WHERE client_id = ?", (client_id,))
    return {"message": "Client deleted successfully"}

@app.get("/api/clients/{client_id}/audit")
def get_client_audit(client_id: int):
    return get_db_rows(database.CLIENTS_DB, "SELECT * FROM audit_logs WHERE client_id = ? ORDER BY timestamp DESC", (client_id,))

# 4. Invoices API
@app.get("/api/invoices")
def get_invoices():
    return get_db_rows(database.CLIENTS_DB, "SELECT * FROM invoices ORDER BY issue_date DESC")

@app.post("/api/invoices")
def create_invoice(invoice: InvoiceCreateSchema):
    issue_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Fetch client details for confirmation
    client = get_db_rows(database.CLIENTS_DB, "SELECT name FROM clients WHERE id = ?", (invoice.client_id,))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    client_name = client[0]["name"]
    
    # Generate automatic invoice number
    prefix = f"INV-{datetime.now().year}"
    count_rows = get_db_rows(database.CLIENTS_DB, "SELECT COUNT(*) as cnt FROM invoices")
    inv_num = f"{prefix}-{count_rows[0]['cnt'] + 1:04d}"
    
    inv_id = execute_db_write(
        database.CLIENTS_DB,
        """
        INSERT INTO invoices (invoice_number, client_id, client_name, period, trades_count, gross_pnl, fees, net_pnl, our_fee, net_payout, status, payment_method, issue_date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (inv_num, invoice.client_id, client_name, invoice.period, invoice.trades_count, invoice.gross_pnl, invoice.fees, invoice.net_pnl, invoice.our_fee, invoice.net_payout, "Pending", invoice.payment_method, issue_date, invoice.notes)
    )
    return {"id": inv_id, "invoice_number": inv_num, "message": "Invoice generated successfully"}

@app.post("/api/invoices/{invoice_id}/pay")
def mark_invoice_paid(invoice_id: int, request: Request):
    paid_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute_db_write(
        database.CLIENTS_DB,
        "UPDATE invoices SET status='Paid', paid_date=? WHERE id=?",
        (paid_date, invoice_id)
    )
    return {"message": "Invoice marked as paid"}

# 5. Settings API
@app.post("/api/settings")
def update_settings(settings: SettingsUpdateSchema):
    updates = [
        ("business_name", settings.business_name),
        ("total_bot_lots", str(settings.total_bot_lots)),
        ("default_billing_cycle", settings.default_billing_cycle),
        ("usd_inr_rate", str(settings.usd_inr_rate)),
        ("whatsapp_webhook", settings.whatsapp_webhook),
        ("daily_drawdown_limit", str(settings.daily_drawdown_limit)),
        ("heartbeat_timeout", str(settings.heartbeat_timeout))
    ]
    for key, value in updates:
        execute_db_write(
            database.CLIENTS_DB,
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    return {"message": "Settings updated successfully"}

@app.get("/api/candles")
async def get_candles(limit: int = 200):
    """Chart candles from the same configured source as the strategy."""
    limit = max(10, min(int(limit), 1000))
    try:
        async with httpx.AsyncClient() as client:
            if BINANCE_SIGNAL_FEED:
                symbol = BINANCE_SYMBOL.replace("/", "").replace(":USDT", "")
                res = await client.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": symbol, "interval": CANDLE_TIMEFRAME, "limit": limit},
                    timeout=5.0,
                )
                res.raise_for_status()
                data = res.json()
                return [
                    {"time": int(d[0] / 1000), "open": float(d[1]), "high": float(d[2]),
                     "low": float(d[3]), "close": float(d[4]), "volume": float(d[5])}
                    for d in data
                ]

            # Delta history candles are public and support 30m resolution.
            now_s = int(datetime.now().timestamp())
            # Give the request enough room for the desired number of 30m bars.
            unit = CANDLE_TIMEFRAME[-1].lower()
            n = int(CANDLE_TIMEFRAME[:-1])
            seconds = n * (60 if unit == "m" else 3600 if unit == "h" else 86400)
            start_s = now_s - seconds * (limit + 5)
            res = await client.get(
                f"{DELTA_REST_URL}/v2/history/candles",
                params={"resolution": CANDLE_TIMEFRAME, "symbol": DELTA_PRODUCT_SYMBOL,
                        "start": start_s, "end": now_s},
                timeout=5.0,
            )
            res.raise_for_status()
            rows = (res.json().get("result") or [])[-limit:]
            return [
                {"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("volume", 0))}
                for r in rows
            ]
    except Exception:
        return []


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("CLIENT_DASHBOARD_PORT", "8081")))
