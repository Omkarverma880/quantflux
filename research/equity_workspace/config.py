"""
Config for the Equity Strategy Workspace (read-only consolidated screener).

The workspace never trades: it answers one question — *which of my strategies
does this stock satisfy right now, on this timeframe?* Every knob here mirrors
the defaults of the strategy it screens for, so a tick in the workspace means
the same thing as a signal in that strategy.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger

logger = get_logger("research.equity_workspace.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "equity_workspace.json"

# Kite-native intervals. Intraday-only strategies are skipped (marked N/A) on
# the day/week/month timeframes — see signals.APPLIES.
TIMEFRAMES = ["minute", "3minute", "5minute", "10minute", "15minute", "30minute",
              "60minute", "day", "week", "month"]

# History (calendar days) to pull per timeframe so the slow indicators converge.
TF_HISTORY_DAYS = {
    "minute": 7, "3minute": 12, "5minute": 20, "10minute": 30, "15minute": 45,
    "30minute": 75, "60minute": 130, "day": 420, "week": 1500, "month": 4000,
}

DEFAULT_CONFIG: dict = {
    "timeframe": "5minute",
    # which strategy columns to evaluate (all on by default)
    "enabled": [],                    # [] = every strategy
    # ── per-strategy knobs (mirrors of the live strategies' defaults) ──
    "fourth_candle_reverse": False,
    "hammer_lookback": 126,
    "hammer_red_before": 3,
    "hammer_lower_wick_min": 2.0,
    "hammer_body_max": 2.0,
    "hammer_upper_wick_max": 1.0,
    "pmvwap_buffer_pct": 0.25,
    "cv_lookback": 20,
    "ema_fast": 20,
    "ema_slow": 200,
    "ema_touch_pct": 0.5,
    "adx_period": 14,
    "adx_threshold": 20.0,
    "cv_threshold": 0.0,
    "first_hour_days": 5,
    "wyckoff_lookback": 120,
    "wyckoff_min_tests": 5,
    # ── scan behaviour ──
    "max_stocks": 0,                  # 0 = no cap
    "min_score": 0,                   # only report stocks at/above this score
    "live_scan": False,               # opt-in auto-refresh
    "refresh_secs": 60,
    # ── telegram ──
    "telegram_alerts": False,
    "telegram_bot": "a",
    "telegram_top_n": 15,
    "telegram_min_score": 3,
    "symbols": [],
}

_INT = {"hammer_lookback", "hammer_red_before", "cv_lookback", "ema_fast", "ema_slow",
        "adx_period", "first_hour_days", "wyckoff_lookback", "wyckoff_min_tests",
        "max_stocks", "min_score", "refresh_secs",
        "telegram_top_n", "telegram_min_score"}
_FLOAT = {"hammer_lower_wick_min", "hammer_body_max", "hammer_upper_wick_max",
          "pmvwap_buffer_pct", "ema_touch_pct", "adx_threshold", "cv_threshold"}


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
    out["hammer_lookback"] = max(2, min(750, out["hammer_lookback"]))
    out["hammer_red_before"] = max(0, min(10, out["hammer_red_before"]))
    out["cv_lookback"] = max(2, min(500, out["cv_lookback"]))
    out["ema_fast"] = max(2, min(400, out["ema_fast"]))
    out["ema_slow"] = max(3, min(400, out["ema_slow"]))
    out["adx_period"] = max(2, min(100, out["adx_period"]))
    out["adx_threshold"] = max(0.0, min(100.0, out["adx_threshold"]))
    out["ema_touch_pct"] = max(0.0, min(25.0, out["ema_touch_pct"]))
    out["pmvwap_buffer_pct"] = max(0.0, min(25.0, out["pmvwap_buffer_pct"]))
    out["first_hour_days"] = max(1, min(60, out["first_hour_days"]))
    out["wyckoff_lookback"] = max(40, min(600, out["wyckoff_lookback"]))
    out["wyckoff_min_tests"] = max(1, min(9, out["wyckoff_min_tests"]))
    out["max_stocks"] = max(0, out["max_stocks"])
    out["min_score"] = max(0, min(20, out["min_score"]))
    out["refresh_secs"] = max(15, min(86400, out["refresh_secs"]))
    out["telegram_top_n"] = max(1, min(50, out["telegram_top_n"]))
    out["telegram_min_score"] = max(0, min(20, out["telegram_min_score"]))
    for b in ("fourth_candle_reverse", "live_scan", "telegram_alerts"):
        out[b] = bool(out[b])
    if out["telegram_bot"] not in ("a", "b"):
        out["telegram_bot"] = "a"
    out["enabled"] = [str(k) for k in (out.get("enabled") or [])]
    out["symbols"] = [str(s).strip().upper() for s in (out.get("symbols") or []) if str(s).strip()]
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("equity_workspace config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("equity_workspace config save failed: %s", exc)
    return cfg
