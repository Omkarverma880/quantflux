"""
Constants for the NIFTY Signal Generator research module.

Everything the engine treats as "fixed vocabulary" lives here — the supported
timeframes, strike intervals, markets and signal labels. Nothing in this file
places orders or touches live strategy state; the module is research-only.

The timeframe table maps each user-facing option to a Zerodha *base* candle
interval plus an aggregation factor. Zerodha natively supports:
    minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute,
    day, week, month
2-hour and 4-hour candles are not native, so they are built by aggregating
``agg`` consecutive 60-minute candles inside the engine.
"""
from __future__ import annotations

# ── Timeframes ────────────────────────────────────────────────────
# key      : stable identifier used by the API / config
# label    : user-facing label
# interval : Zerodha base candle interval to fetch
# agg      : number of base candles aggregated into one output candle
# intraday : True → VWAP is anchored (reset) at each session open
TIMEFRAMES: list[dict] = [
    {"key": "1m",  "label": "1 Minute",   "interval": "minute",   "agg": 1, "intraday": True},
    {"key": "3m",  "label": "3 Minutes",  "interval": "3minute",  "agg": 1, "intraday": True},
    {"key": "5m",  "label": "5 Minutes",  "interval": "5minute",  "agg": 1, "intraday": True},
    {"key": "10m", "label": "10 Minutes", "interval": "10minute", "agg": 1, "intraday": True},
    {"key": "15m", "label": "15 Minutes", "interval": "15minute", "agg": 1, "intraday": True},
    {"key": "30m", "label": "30 Minutes", "interval": "30minute", "agg": 1, "intraday": True},
    {"key": "1h",  "label": "1 Hour",     "interval": "60minute", "agg": 1, "intraday": True},
    {"key": "2h",  "label": "2 Hours",    "interval": "60minute", "agg": 2, "intraday": True},
    {"key": "4h",  "label": "4 Hours",    "interval": "60minute", "agg": 4, "intraday": True},
    {"key": "1d",  "label": "1 Day",      "interval": "day",      "agg": 1, "intraday": False},
    {"key": "1w",  "label": "1 Week",     "interval": "week",     "agg": 1, "intraday": False},
    {"key": "1M",  "label": "1 Month",    "interval": "month",    "agg": 1, "intraday": False},
]
TIMEFRAME_MAP: dict[str, dict] = {t["key"]: t for t in TIMEFRAMES}
DEFAULT_TIMEFRAME = "15m"

# ── Strike interval (ATM rounding + spacing) ──────────────────────
STRIKE_INTERVALS = [25, 50, 100]
DEFAULT_STRIKE_INTERVAL = 50

# ── Strikes above / below ATM ─────────────────────────────────────
DEFAULT_STRIKE_COUNT = 2          # ATM ± 2 → 5 strikes total

# ── Markets ───────────────────────────────────────────────────────
# Each index resolves its own spot token (NSE) and option chain (NFO `name`),
# so instruments never mix. `default_interval` is the natural ATM grid for the
# index (NIFTY 50, BANKNIFTY 100) — used unless the caller overrides it.
MARKETS: dict[str, dict] = {
    "NIFTY": {
        "label": "NIFTY",
        "name": "NIFTY",                 # NFO instrument `name`
        "spot_tradingsymbol": "NIFTY 50",  # NSE index tradingsymbol
        "default_interval": 50,
        "enabled": True,
    },
    "BANKNIFTY": {
        "label": "BANK NIFTY",
        "name": "BANKNIFTY",
        "spot_tradingsymbol": "NIFTY BANK",
        "default_interval": 100,
        "enabled": True,
    },
}
DEFAULT_MARKET = "NIFTY"
ENABLED_MARKETS = [k for k, v in MARKETS.items() if v.get("enabled")]

# ── Signal labels + colour hints (UI reads `color` for styling) ───
SIGNAL_BUY = "BUY"
SIGNAL_SELL = "SELL"
SIGNAL_NEUTRAL = "NEUTRAL"

SIGNAL_COLORS = {
    SIGNAL_BUY: "green",
    SIGNAL_SELL: "red",
    SIGNAL_NEUTRAL: "orange",
}
