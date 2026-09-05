"""
Config for the Chart Simulation workspace.

A single-instrument charting desk: full history, any Kite timeframe, an
indicator rack you tick on and off, and horizontal levels that persist per
instrument. Read-only — it never places an order.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger

logger = get_logger("research.simulation.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "simulation.json"

TIMEFRAMES = ["minute", "3minute", "5minute", "10minute", "15minute", "30minute",
              "60minute", "day", "week", "month"]

# Kite's per-request history window, in calendar days. Longer ranges are fetched
# in chunks and stitched, so "all the data available till date" really works.
TF_MAX_DAYS = {
    "minute": 60, "3minute": 90, "5minute": 90, "10minute": 90, "15minute": 180,
    "30minute": 180, "60minute": 365, "day": 2000, "week": 2000, "month": 2000,
}
# How far back to reach by default, per timeframe (calendar days).
TF_DEFAULT_DAYS = {
    "minute": 90, "3minute": 365, "5minute": 730, "10minute": 730, "15minute": 1095,
    "30minute": 1460, "60minute": 1825, "day": 7300, "week": 7300, "month": 7300,
}

INDICATORS: list[dict] = [
    {"key": "volume", "name": "Volume", "group": "Volume", "pane": "volume",
     "note": "Traded volume per candle, coloured by the candle's direction."},
    {"key": "volume_ma", "name": "Volume MA (20)", "group": "Volume", "pane": "volume",
     "note": "Average volume — anything well above it is real participation, not noise."},
    {"key": "cum_volume", "name": "Cumulative Volume", "group": "Volume", "pane": "cv",
     "note": "Signed volume run (green adds, red subtracts) — net buying vs selling pressure."},
    {"key": "oi", "name": "Open Interest", "group": "Volume", "pane": "oi",
     "note": "Derivatives only. Rising OI into a move means fresh positions, not covering."},
    {"key": "vwap_day", "name": "VWAP — today", "group": "VWAP", "pane": "price",
     "note": "Running session VWAP, re-anchored each day."},
    {"key": "vwap_week", "name": "VWAP — this week", "group": "VWAP", "pane": "price",
     "note": "Running VWAP anchored to the start of the week."},
    {"key": "vwap_month", "name": "VWAP — this month", "group": "VWAP", "pane": "price",
     "note": "Running VWAP anchored to the start of the month."},
    {"key": "pvwap_day", "name": "Prev-day VWAP", "group": "VWAP", "pane": "price",
     "note": "Yesterday's closing VWAP, carried forward as a flat level."},
    {"key": "pvwap_week", "name": "Prev-week VWAP", "group": "VWAP", "pane": "price",
     "note": "Last week's closing VWAP as a flat level."},
    {"key": "pvwap_month", "name": "Prev-month VWAP", "group": "VWAP", "pane": "price",
     "note": "Last month's closing VWAP — the level Equity Strategy 1 holds against."},
    {"key": "pivots", "name": "Pivot · R1 R2 · S1 S2", "group": "Levels", "pane": "price",
     "note": "Classic floor pivots from the previous period's high/low/close."},
    {"key": "first_hour", "name": "First-hour high / low", "group": "Levels", "pane": "price",
     "note": "High and low of today's opening window — the range Strategy 10 breaks out of."},
    {"key": "first_hour_prev", "name": "Prev-day first-hour H/L", "group": "Levels", "pane": "price",
     "note": "Yesterday's opening-window high and low, carried across today."},
    {"key": "first_hour_stats", "name": "First-hour max / avg (N days)", "group": "Levels", "pane": "price",
     "note": "Highest, lowest and average opening-window high/low over the last N sessions."},
    {"key": "prev_day_hl", "name": "Prev-day high / low", "group": "Levels", "pane": "price",
     "note": "Yesterday's high and low — the most-watched intraday levels."},
    {"key": "fourth_candle", "name": "4th Candle setup", "group": "Strategies", "pane": "price",
     "note": "Marks the 4th candle's high/low when the first 3 are all one colour, and the breakout bar."},
    {"key": "hammer", "name": "Hammer at lookback low", "group": "Strategies", "pane": "price",
     "note": "Marks hammers at a lookback low after N red candles, and the level each one arms. Tune the wick/body limits below."},
    {"key": "ema_fast", "name": "EMA (20)", "group": "Trend", "pane": "price", "note": "Fast EMA."},
    {"key": "ema_slow", "name": "EMA (200)", "group": "Trend", "pane": "price", "note": "Slow EMA — the trend filter."},
]
INDICATOR_KEYS = [i["key"] for i in INDICATORS]

DEFAULT_CONFIG: dict = {
    "kind": "equity",                 # equity | fno_option | index | index_option
    "timeframe": "5minute",
    "indicators": ["volume"],          # opens simple — everything else is one tick away
    "refresh_secs": 15,
    "auto_refresh": True,
    "bars": 5000,                     # bars sent to the chart (pan reaches all of them)
    "history_days": 0,                # 0 = the per-timeframe default
    # ── indicator parameters ──
    "volume_ma": 20,
    "ema_fast": 20,
    "ema_slow": 200,
    "pivot_basis": "day",             # day | week | month
    "first_hour_minutes": 60,         # the opening window, in minutes
    "first_hour_days": 5,             # sessions behind the first-hour max/avg stats
    "hammer_lookback": 126,
    "hammer_red_before": 3,
    "hammer_lower_wick": 2.0,         # min lower wick, % of the candle's low
    "hammer_body_max": 2.0,           # max body %
    "hammer_upper_wick": 1.0,         # max upper wick %
    "level_near_pct": 0.5,            # "approaching" when price is inside this %
    "index_name": "NIFTY",
    "colors": {},                     # per-indicator colour overrides {key: "#rrggbb"}
}

_INT = {"refresh_secs", "bars", "history_days", "volume_ma", "ema_fast", "ema_slow",
        "first_hour_minutes", "first_hour_days", "hammer_lookback", "hammer_red_before"}
_FLOAT = {"level_near_pct", "hammer_lower_wick", "hammer_body_max", "hammer_upper_wick"}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    for k in _INT:
        try:
            out[k] = int(float(out[k]))
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    for k in _FLOAT:
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    if out["timeframe"] not in TIMEFRAMES:
        out["timeframe"] = "5minute"
    if out["kind"] not in ("equity", "fno_option", "index", "index_option"):
        out["kind"] = "equity"
    if out["pivot_basis"] not in ("day", "week", "month"):
        out["pivot_basis"] = "day"
    out["refresh_secs"] = max(5, min(3600, out["refresh_secs"]))
    out["bars"] = max(200, min(50000, out["bars"]))
    out["history_days"] = max(0, min(9000, out["history_days"]))
    out["volume_ma"] = max(2, min(200, out["volume_ma"]))
    out["ema_fast"] = max(2, min(400, out["ema_fast"]))
    out["ema_slow"] = max(3, min(400, out["ema_slow"]))
    out["first_hour_minutes"] = max(5, min(375, out["first_hour_minutes"]))
    out["first_hour_days"] = max(1, min(60, out["first_hour_days"]))
    out["hammer_lookback"] = max(2, min(750, out["hammer_lookback"]))
    out["hammer_red_before"] = max(0, min(10, out["hammer_red_before"]))
    out["hammer_lower_wick"] = max(0.0, min(50.0, out["hammer_lower_wick"]))
    out["hammer_body_max"] = max(0.01, min(50.0, out["hammer_body_max"]))
    out["hammer_upper_wick"] = max(0.01, min(50.0, out["hammer_upper_wick"]))
    out["level_near_pct"] = max(0.05, min(10.0, out["level_near_pct"]))
    out["auto_refresh"] = bool(out["auto_refresh"])
    keys = [k for k in (out.get("indicators") or []) if k in INDICATOR_KEYS]
    out["indicators"] = keys or list(DEFAULT_CONFIG["indicators"])
    colors = out.get("colors") or {}
    out["colors"] = {k: str(v) for k, v in colors.items()
                     if k in INDICATOR_KEYS and isinstance(v, str) and v.startswith("#")}
    out["index_name"] = str(out["index_name"] or "NIFTY").strip().upper()
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("simulation config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("simulation config save failed: %s", exc)
    return cfg
