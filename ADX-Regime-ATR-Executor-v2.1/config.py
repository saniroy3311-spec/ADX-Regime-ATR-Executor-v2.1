"""Canonical configuration for ADX Regime ATR Executor.

Source of truth is the active TradingView settings shown by the user for
the supplied TradingView strategy and active screenshot profile: EMA 20/50,
ADX regime filter, ATR SL/TP, breakeven, and
a 5-stage ATR trail.  The matching Pine file in ``pine/`` uses the same defaults.

PINE_PARITY_MODE removes feed-divergence heuristics so the Python executor follows
the strategy rules as literally as possible. Real exchange fills can still differ
from TradingView because TradingView uses a broker emulator while Delta executes
against a live order book.
"""
from __future__ import annotations
import os

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


BOT_NAME = os.environ.get("BOT_NAME", "ADX Regime ATR Executor")
BOT_VERSION = os.environ.get("BOT_VERSION", "2.1.0-parity")
PINE_PARITY_MODE = _b("PINE_PARITY_MODE", True)
LIVE_TICK_RISK_ENGINE = _b("LIVE_TICK_RISK_ENGINE", True)

# Execution mode
#   paper = use the configured Delta environment for market/account connectivity,
#           but NEVER send or cancel exchange orders. Orders/fills are simulated.
#   live  = send real orders. Production live trading additionally requires
#           LIVE_TRADING_ENABLED=true as an explicit second safety switch.
EXECUTION_MODE = os.environ.get("EXECUTION_MODE", "paper").strip().lower()
if EXECUTION_MODE not in {"paper", "live"}:
    raise ValueError("EXECUTION_MODE must be either paper or live")
LIVE_TRADING_ENABLED = _b("LIVE_TRADING_ENABLED", False)
STRICT_MARKET_METADATA = _b("STRICT_MARKET_METADATA", True)

# Exchange / product
DELTA_API_KEY = os.environ.get("DELTA_API_KEY", "YOUR_DELTA_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "YOUR_DELTA_API_SECRET")
DELTA_TESTNET = _b("DELTA_TESTNET", True)  # safe-by-default
DELTA_LIVE_REST_URL = "https://api.india.delta.exchange"
DELTA_TESTNET_REST_URL = "https://cdn-ind.testnet.deltaex.org"
DELTA_REST_URL = DELTA_TESTNET_REST_URL if DELTA_TESTNET else DELTA_LIVE_REST_URL
SYMBOL = os.environ.get("SYMBOL", "BTC/USD:USD")          # ccxt unified symbol
DELTA_PRODUCT_SYMBOL = os.environ.get("DELTA_PRODUCT_SYMBOL", "BTCUSD")
DELTA_CONTRACT_VALUE = _f("DELTA_CONTRACT_VALUE", 0.001)
ALERT_QTY = _i("ALERT_QTY", 100)
POSITION_BTC_SIZE = ALERT_QTY * DELTA_CONTRACT_VALUE  # 100 BTCUSD lots = 0.1 BTC

# Strategy execution model
CANDLE_TIMEFRAME = os.environ.get("CANDLE_TIMEFRAME", "30m")
PINE_MINTICK = _f("PINE_MINTICK", 0.5)
PINE_SLIPPAGE_TICKS = _i("PINE_SLIPPAGE_TICKS", 2)
# TradingView strategy.fixed size from the uploaded Pine. For the Delta BTCUSD
# live executor, 0.1 BTC exposure maps to 100 API contracts at 0.001 BTC each.
PINE_POINT_VALUE = _f("PINE_POINT_VALUE", 1.0)
PINE_ORDER_QTY = _f("PINE_ORDER_QTY", 0.1)

# Active TradingView profile shown in the supplied screenshots: EMA 20 / 50
EMA_FAST_LEN = _i("EMA_FAST_LEN", 20)
EMA_TREND_LEN = _i("EMA_TREND_LEN", 50)
ATR_LEN = _i("ATR_LEN", 14)
DI_LEN = _i("DI_LEN", 14)
ADX_SMOOTH = _i("ADX_SMOOTH", 14)
ADX_EMA = _i("ADX_EMA", 5)
RSI_LEN = _i("RSI_LEN", 14)

# Exact Pine thresholds
ADX_TREND_TH = _f("ADX_TREND_TH", 22.0)
ADX_RANGE_TH = _f("ADX_RANGE_TH", 18.0)
ADX_TOLERANCE = _f("ADX_TOLERANCE", 0.0)
FILTER_ATR_MULT = _f("FILTER_ATR_MULT", 1.4)
FILTER_BODY_MULT = _f("FILTER_BODY_MULT", 0.5)
FILTER_BODY_TOLERANCE = _f("FILTER_BODY_TOLERANCE", 0.0)
FILTER_VOL_ENABLED = _b("FILTER_VOL_ENABLED", True)
FILTER_VOL_MULT = _f("FILTER_VOL_MULT", 1.0)
BREAKOUT_BUFFER_PTS = _f("BREAKOUT_BUFFER_PTS", 0.0)
RSI_OB = _i("RSI_OB", 70)
RSI_OS = _i("RSI_OS", 30)

# Exact Pine risk / reward
TREND_RR = _f("TREND_RR", 4.0)
RANGE_RR = _f("RANGE_RR", 2.5)
TREND_ATR_MULT = _f("TREND_ATR_MULT", 0.6)
RANGE_ATR_MULT = _f("RANGE_ATR_MULT", 0.5)
MAX_SL_MULT = _f("MAX_SL_MULT", 1.5)
MAX_SL_POINTS = _f("MAX_SL_POINTS", 500.0)
BE_MULT = _f("BE_MULT", 0.6)

# (stage trigger ATR, trail_points ATR multiplier, trail_offset ATR multiplier)
# Corrected semantics:
# - There is NO trail while stage == 0.
# - A stage unlocks only after profit reaches trigger * ENTRY_ATR.
# - Once unlocked, the TradingView-style trail activation distance is
#   trail_points * ENTRY_ATR and the trail gap is trail_offset * ENTRY_ATR.
# - All distances are PRICE units in Python and in the matching custom Pine
#   trail state machine. They are NOT passed as native trail_points/ticks.
# - ENTRY_ATR is frozen at the signal/entry, so risk does not drift mid-trade.
# Legacy mode is retained only for regression comparison with the old script bug.
TRAIL_LEGACY_TV_TICK_SEMANTICS = _b("TRAIL_LEGACY_TV_TICK_SEMANTICS", False)
# ── SNIPER v6 EXIT PARITY ────────────────────────────────────────────────
# Copies how "BTCUSDT Sniper v6" really exits on TradingView when
# Script execution = "On bar close":
#   * ATR only changes when a 30m candle closes (no live intrabar ATR)
#   * NO initial SL / TP during the entry candle (only the native trail)
#   * Max-SL is also checked at the close of the entry candle
#   * the first price after a candle close that is already beyond a level
#     that just moved is filled at that price (TradingView fills at bar open)
SNIPER_V6_EXIT_PARITY = _b("SNIPER_V6_EXIT_PARITY", False)
DYNAMIC_REALTIME_ATR = _b("DYNAMIC_REALTIME_ATR", not SNIPER_V6_EXIT_PARITY)
# In Sniper v6 the BE-* strategy.exit orders sit behind the main exit order,
# which already reserves 100% of the position, so they normally never fill.
BREAKEVEN_ENABLED = _b("BREAKEVEN_ENABLED", True)

# ── TV BAR-PATH TRAIL (Pine "List of Trades" exit parity) ─────────────────
# TradingView's broker emulator never sees real ticks inside a historical
# bar. It walks each bar as straight lines:
#     open -> high -> low -> close   (if the high is nearer the open)
#     open -> low  -> high -> close  (otherwise)
# and moves the native trail only along that path. Intrabar dips between
# the low and the high are invisible to it.
# When true, the bot copies that model:
#   * live ticks only test the FROZEN stop / TP set at the last bar close
#     (no intrabar best-price ratchet, no intrabar arming);
#   * at every bar close the bar's O/H/L/C is replayed on TV's path to arm
#     / ratchet the trail, and to detect a same-bar trail exit (filled at
#     market = bar close, the earliest moment it can be known).
TRAIL_TV_BAR_PATH = _b("TRAIL_TV_BAR_PATH", False)
# Only used with TRAIL_TV_BAR_PATH=true. When true, the ENTRY candle is
# managed on live ticks (trail can arm and exit inside the running entry
# candle); TV candle mode starts from the first candle close.
TRAIL_TV_ENTRY_CANDLE_LIVE = _b("TRAIL_TV_ENTRY_CANDLE_LIVE", False)

TRAIL_STAGE_UPDATE_MODE = os.environ.get("TRAIL_STAGE_UPDATE_MODE", "tick").strip().lower()
BREAKEVEN_UPDATE_MODE = os.environ.get("BREAKEVEN_UPDATE_MODE", "tick").strip().lower()
MAX_SL_EVAL_MODE = os.environ.get("MAX_SL_EVAL_MODE", "tick").strip().lower()
if TRAIL_STAGE_UPDATE_MODE not in {"bar_close", "tick"}:
    raise ValueError("TRAIL_STAGE_UPDATE_MODE must be bar_close or tick")
if BREAKEVEN_UPDATE_MODE not in {"bar_close", "tick"}:
    raise ValueError("BREAKEVEN_UPDATE_MODE must be bar_close or tick")
if MAX_SL_EVAL_MODE not in {"bar_close", "tick"}:
    raise ValueError("MAX_SL_EVAL_MODE must be bar_close or tick")

TRAIL_STAGES = [
    (_f("TRAIL1_TRIGGER", 0.8), _f("TRAIL1_PTS", 0.50), _f("TRAIL1_OFF", 0.40)),
    (_f("TRAIL2_TRIGGER", 1.5), _f("TRAIL2_PTS", 0.40), _f("TRAIL2_OFF", 0.30)),
    (_f("TRAIL3_TRIGGER", 2.5), _f("TRAIL3_PTS", 0.30), _f("TRAIL3_OFF", 0.25)),
    (_f("TRAIL4_TRIGGER", 4.0), _f("TRAIL4_PTS", 0.20), _f("TRAIL4_OFF", 0.15)),
    (_f("TRAIL5_TRIGGER", 6.0), _f("TRAIL5_PTS", 0.15), _f("TRAIL5_OFF", 0.10)),
]

# Pine strategy() commission_value=0.05 percent per fill.
COMMISSION_PCT = _f("COMMISSION_PCT", 0.05) / 100.0
DELTA_GST_RATE = _f("DELTA_GST_RATE", 0.18)
FEE_MODEL_INCLUDE_GST = _b("FEE_MODEL_INCLUDE_GST", True)

# Signal market data. For exact parity this MUST match the TradingView chart.
# The supplied Pine alerts route to Delta BTCUSD.P, so Delta is the conservative
# default. Set BINANCE_SIGNAL_FEED=true only if your TradingView chart is
# BINANCE:BTCUSDT (and set PINE_MINTICK accordingly, commonly 0.1).
BINANCE_SIGNAL_FEED = _b("BINANCE_SIGNAL_FEED", False)
BINANCE_SYMBOL = os.environ.get("BINANCE_SYMBOL", "BTC/USDT")
WS_RECONNECT_SEC = _f("WS_RECONNECT_SEC", 5.0)
TRAIL_LOOP_SEC = _f("TRAIL_LOOP_SEC", 0.25)
TRAIL_EXIT_FROM_DELTA_WS = _b("TRAIL_EXIT_FROM_DELTA_WS", True)
TRAIL_FIRE_SL_ON_CANDLE_EXTREME = _b("TRAIL_FIRE_SL_ON_CANDLE_EXTREME", False)

# Live execution behavior. LIVE_TICK_RISK_ENGINE keeps protective price orders
# responsive to live ticks. Stage, breakeven, and Max-SL timing are independently
# selectable above; defaults are tick to match realtime Pine execution.
# Initial SL / TP / an already-armed trail still react to live prices for safety.
TP_HARD_EXIT = _b("TP_HARD_EXIT", True)  # master switch
TREND_HARD_TP_ENABLED = _b("TREND_HARD_TP_ENABLED", True)
RANGE_HARD_TP_ENABLED = _b("RANGE_HARD_TP_ENABLED", True)
BAR_CLOSE_SL_EVAL = _b("BAR_CLOSE_SL_EVAL", False)
TIME_EXIT_MINUTES = _i("TIME_EXIT_MINUTES", 0)
TRAIL_SL_PRE_FIRE_BUFFER = _f("TRAIL_SL_PRE_FIRE_BUFFER", 0.0)
TRAIL_OFFSET_FLOOR_MULT = _f("TRAIL_OFFSET_FLOOR_MULT", 0.0)
TRAIL_ARM_FLOOR_MULT = _f("TRAIL_ARM_FLOOR_MULT", 0.0)

# Optional real-world protections. Defaults are parity-friendly and therefore
# disabled/minimal. Turn them on only after parity has been proved and record the
# fact that production behavior will then differ from Pine.
SL_CONFIRM_MS = _i("SL_CONFIRM_MS", 0)
SL_CONFIRM_TICKS = _i("SL_CONFIRM_TICKS", 1)
TRAIL_SL_CONFIRM_TICKS = _i("TRAIL_SL_CONFIRM_TICKS", 1)
MAX_EXIT_SLIPPAGE_ATR_PCT = _f("MAX_EXIT_SLIPPAGE_ATR_PCT", 25.0)
EMERGENCY_BRACKET_ENABLED = _b("EMERGENCY_BRACKET_ENABLED", True)
BRACKET_SL_WIDEN_MULT = _f("BRACKET_SL_WIDEN_MULT", 1.0)
BRACKET_SL_MIN_PTS = _f("BRACKET_SL_MIN_PTS", 0.0)
BRACKET_SL_BUFFER = _f("BRACKET_SL_BUFFER", 0.0)
SL_FIRE_VIA_BRACKET = _b("SL_FIRE_VIA_BRACKET", False)

# Notifications
TELEGRAM_ENABLED = _b("TELEGRAM_ENABLED", False)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_TO_NUMBER = os.environ.get("WHATSAPP_TO_NUMBER", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_TEMPLATE_NAME = os.environ.get("WHATSAPP_TEMPLATE_NAME", "")
WHATSAPP_TEMPLATE_LANG = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en")

# Storage / logs
LOG_FILE = os.environ.get("LOG_FILE", "./data/adx_regime_atr_executor.db")
GSHEET_ENABLED = _b("GSHEET_ENABLED", False)
GSHEET_SPREADSHEET_ID = os.environ.get("GSHEET_SPREADSHEET_ID", "")
GSHEET_CREDENTIALS_FILE = os.environ.get("GSHEET_CREDENTIALS_FILE", "")
GSHEET_CREDENTIALS_JSON = os.environ.get("GSHEET_CREDENTIALS_JSON", "")
GSHEET_AUTO_CREATE = _b("GSHEET_AUTO_CREATE", True)

# Flat aliases retained for old verification scripts.
ADX_EMA_LEN = ADX_EMA
TRAIL_T1_TRIG, TRAIL_T1_PTS, TRAIL_T1_OFF = TRAIL_STAGES[0]
TRAIL_T2_TRIG, TRAIL_T2_PTS, TRAIL_T2_OFF = TRAIL_STAGES[1]
TRAIL_T3_TRIG, TRAIL_T3_PTS, TRAIL_T3_OFF = TRAIL_STAGES[2]
TRAIL_T4_TRIG, TRAIL_T4_PTS, TRAIL_T4_OFF = TRAIL_STAGES[3]
TRAIL_T5_TRIG, TRAIL_T5_PTS, TRAIL_T5_OFF = TRAIL_STAGES[4]
