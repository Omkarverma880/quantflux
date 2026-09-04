"""
Config for the Wyckoff Method analyser (equity · NIFTY options · equity F&O).

Every threshold that decides "is this a climax", "is this a spring", "is this a
sign of strength" lives here, so the read can be tuned per instrument class
without touching the engine. Read-only analysis — nothing here places orders.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger

logger = get_logger("research.wyckoff.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "wyckoff.json"

TIMEFRAMES = ["minute", "3minute", "5minute", "10minute", "15minute", "30minute",
              "60minute", "day", "week", "month"]

# Calendar days of history to pull per timeframe (enough bars for a range + cause).
TF_HISTORY_DAYS = {
    "minute": 5, "3minute": 10, "5minute": 18, "10minute": 30, "15minute": 45,
    "30minute": 80, "60minute": 140, "day": 500, "week": 1800, "month": 4500,
}

DEFAULT_CONFIG: dict = {
    "timeframe": "day",
    "lookback": 120,              # bars examined for the trading range
    # ── structure ──
    "min_range_bars": 12,         # a range needs this many bars to build "cause"
    "atr_period": 14,
    "vol_ma_period": 20,
    "pivot_k": 2,                 # bars each side of a swing pivot
    "ar_window": 15,              # bars after the climax to find the automatic reaction
    # ── event thresholds ──
    "climax_vol_mult": 1.8,       # climax volume vs the volume average
    "climax_spread_mult": 1.3,    # climax spread vs ATR
    "st_tolerance_pct": 25.0,     # a secondary test sits in this % of the range from its edge
    "spring_max_pct": 3.0,        # a spring may undercut the range low by up to this %
    "sos_close_pct": 65.0,        # SOS closes in the top % of its own bar range
    "sos_vol_mult": 1.2,
    "lps_tolerance_pct": 30.0,    # LPS holds within this % of the range from the breakout edge
    "effort_vol_mult": 1.5,       # "effort" = volume this many × the average
    "effort_result_max": 0.5,     # "no result" = bar body under this × ATR
    "activity_bars": 20,          # window for the up-volume vs down-volume test
    # ── decision ──
    "min_tests_enter": 5,         # of the 9 Wyckoff tests, to allow an ENTER call
    "min_tests_prepare": 3,
    "chase_bars": 8,              # bars out of the range after which entry is "chasing"
    # ── F&O confirmation ──
    "oi_change_pct": 1.0,         # OI move that counts as a real build-up
    # ── options ──
    "index_name": "NIFTY",
    "option_mode": "index",       # index | premium (premium reads are confirmatory only)
    # ── workspace / scan ──
    "chart_bars": 120,
    "telegram_bot": "a",
}

_INT = {"lookback", "min_range_bars", "atr_period", "vol_ma_period", "pivot_k", "ar_window",
        "activity_bars", "min_tests_enter", "min_tests_prepare", "chase_bars", "chart_bars"}
_FLOAT = {"climax_vol_mult", "climax_spread_mult", "st_tolerance_pct", "spring_max_pct",
          "sos_close_pct", "sos_vol_mult", "lps_tolerance_pct", "effort_vol_mult",
          "effort_result_max", "oi_change_pct"}


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
        out["timeframe"] = "day"
    if out["option_mode"] not in ("index", "premium"):
        out["option_mode"] = "index"
    out["lookback"] = max(30, min(1000, out["lookback"]))
    out["min_range_bars"] = max(5, min(200, out["min_range_bars"]))
    out["atr_period"] = max(2, min(100, out["atr_period"]))
    out["vol_ma_period"] = max(2, min(200, out["vol_ma_period"]))
    out["pivot_k"] = max(1, min(10, out["pivot_k"]))
    out["ar_window"] = max(3, min(100, out["ar_window"]))
    out["activity_bars"] = max(5, min(200, out["activity_bars"]))
    out["min_tests_enter"] = max(1, min(9, out["min_tests_enter"]))
    out["min_tests_prepare"] = max(1, min(9, out["min_tests_prepare"]))
    out["chase_bars"] = max(1, min(50, out["chase_bars"]))
    out["chart_bars"] = max(30, min(400, out["chart_bars"]))
    out["climax_vol_mult"] = max(1.0, min(10.0, out["climax_vol_mult"]))
    out["spring_max_pct"] = max(0.1, min(25.0, out["spring_max_pct"]))
    out["st_tolerance_pct"] = max(1.0, min(60.0, out["st_tolerance_pct"]))
    out["sos_close_pct"] = max(10.0, min(100.0, out["sos_close_pct"]))
    out["lps_tolerance_pct"] = max(1.0, min(80.0, out["lps_tolerance_pct"]))
    if out["telegram_bot"] not in ("a", "b"):
        out["telegram_bot"] = "a"
    out["index_name"] = str(out["index_name"] or "NIFTY").strip().upper()
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("wyckoff config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("wyckoff config save failed: %s", exc)
    return cfg
