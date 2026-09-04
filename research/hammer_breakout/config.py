"""
Config for the Hammer-at-3/6-Month-Low Breakout Strategy (Equity Strategy #4).

Daily positional swing. Everything in the setup is measured on the PREVIOUS
day's DAILY candle (the "signal candle"); the trade triggers when the CURRENT
day trades above that candle's high. Long only — the Pine study has no short
leg. Percentages are measured off the signal candle's LOW, exactly like the
Pine script (`lowerWick / sigLow * 100`).
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger

logger = get_logger("research.hammer_breakout.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "hammer_breakout.json"

TIMEFRAME = "day"

DEFAULT_CONFIG: dict = {
    # ── the hammer (all measured on the previous daily candle) ──
    "lower_wick_min": 2.0,            # lower wick ≥ 2 % of the candle low
    "body_max": 2.0,                  # body      < 2 %
    "upper_wick_max": 1.0,            # upper wick < 1 %
    "low_lookback": 126,              # 126 bars ≈ 6 months (63 ≈ 3 months)
    "red_before": 3,                  # candles before the hammer that must be red
    # ── entry ──
    "entry_cutoff": "15:10",          # breakout must trigger by this time (live)
    "square_off": "15:15",            # exit time on the square-off day
    # ── target / SL (on the stock price) ──
    "target_mode": "percent",         # percent | points
    "target_value": 10.0,             # swing target — daily bars, wide moves
    "sl_mode": "signal_low",          # percent | points | signal_low (hammer low)
    "sl_value": 5.0,
    # ── product / sizing ──
    "product": "CNC",                 # CNC (delivery swing) | MIS
    "max_hold_days": 30,              # calendar days to hold before square-off
    "qty_mode": "capital",            # capital | fixed
    "capital_per_trade": 100000,      # qty = capital // entry
    "fixed_qty": 1,
    # ── portfolio (paper/live) ──
    "max_positions": 10,
    "max_long": 10,
    # ── universe / scan ──
    "max_stocks": 0,                  # 0 = whole universe
    "scan_interval": 60,
    # ── realistic costs ──
    "apply_costs": False,
    "slippage_bps": 5.0,
    "brokerage_per_order": 20.0,
    "charges_pct": 0.05,
    # ── live control ──
    "paper_trade": True,
    "auto_start": False,
    "symbols": [],
    # ── telegram ──
    "telegram_alerts": False,
    "telegram_bot": "a",
    # ── Today's Stocks tab ──
    "today_source": "fno",            # fno | strategy | watchlist
    "today_watchlist_id": "",         # when today_source == "watchlist"
    "today_refresh_secs": 15,         # live re-price cadence (5 s … 24 h)
    "today_auto_alert": False,        # push each new breakout to Telegram as it happens
}

_INT = {"low_lookback", "red_before", "max_hold_days", "capital_per_trade", "fixed_qty",
        "max_positions", "max_long", "max_stocks", "scan_interval", "today_refresh_secs"}
_FLOAT = {"lower_wick_min", "body_max", "upper_wick_max", "target_value", "sl_value",
          "slippage_bps", "brokerage_per_order", "charges_pct"}


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
    if out["target_mode"] not in ("percent", "points"):
        out["target_mode"] = "percent"
    if out["sl_mode"] not in ("percent", "points", "signal_low"):
        out["sl_mode"] = "signal_low"
    if out["product"] not in ("MIS", "CNC"):
        out["product"] = "CNC"
    if out["qty_mode"] not in ("capital", "fixed"):
        out["qty_mode"] = "capital"
    out["lower_wick_min"] = max(0.0, out["lower_wick_min"])
    out["body_max"] = max(0.0, out["body_max"])
    out["upper_wick_max"] = max(0.0, out["upper_wick_max"])
    out["target_value"] = max(0.0, out["target_value"])
    out["sl_value"] = max(0.0, out["sl_value"])
    out["low_lookback"] = max(2, min(750, out["low_lookback"]))
    out["red_before"] = max(0, min(10, out["red_before"]))
    out["max_hold_days"] = max(1, min(365, out["max_hold_days"]))
    out["capital_per_trade"] = max(0, out["capital_per_trade"])
    out["fixed_qty"] = max(1, out["fixed_qty"])
    out["max_positions"] = max(1, min(500, out["max_positions"]))
    out["max_long"] = max(1, min(500, out["max_long"]))
    out["max_stocks"] = max(0, out["max_stocks"])
    out["scan_interval"] = max(10, min(600, out["scan_interval"]))
    out["today_refresh_secs"] = max(5, min(86400, out["today_refresh_secs"]))
    for b in ("apply_costs", "paper_trade", "auto_start", "telegram_alerts", "today_auto_alert"):
        out[b] = bool(out[b])
    if out["telegram_bot"] not in ("a", "b"):
        out["telegram_bot"] = "a"
    if out["today_source"] not in ("fno", "strategy", "watchlist"):
        out["today_source"] = "fno"
    out["today_watchlist_id"] = str(out.get("today_watchlist_id") or "")
    out["symbols"] = [str(s).strip().upper() for s in (out.get("symbols") or []) if str(s).strip()]
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("hammer_breakout config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("hammer_breakout config save failed: %s", exc)
    return cfg
