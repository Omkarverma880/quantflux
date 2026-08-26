"""
Config for the VWAP Options Engine (NIFTY index options).

One versioned, durable config drives the SAME signal + simulation core across
Backtest, Single-day Simulate and Live/Paper, so the three can never diverge.
Stored in the AppSetting table (survives Railway restarts).
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.logger import get_logger

logger = get_logger("research.vwap_options.config")

ENGINE_VERSION = "1.0"
UNDERLYING = "NIFTY"
STRIKE_STEP = 50                    # NIFTY strike interval
LADDER_STEPS = 25                   # ±25 steps ⇒ a 50-strike ladder

# selectable VWAP lines (key → label). Rolling lines are generated from N days.
VWAP_LINES = {
    "day_vwap": "Current-day VWAP",
    "prev_day_vwap": "Previous-day VWAP",
    "prev_week_vwap": "Previous-week VWAP",
    "prev_month_vwap": "Previous-month VWAP",
    "roll_15d_vwap": "Rolling 15-day VWAP",
    "roll_90d_vwap": "Rolling 90-day VWAP",
}
ROLLING_DAYS = {"roll_15d_vwap": 15, "roll_90d_vwap": 90}

EVENTS = {
    "touch": "Touches the line",
    "cross_up": "Crosses up through the line",
    "cross_down": "Crosses down through the line",
    "cross_any": "Crosses the line (either way)",
}
ACTIONS = {"BUY_CE": "Buy CALL", "BUY_PE": "Buy PUT", "NONE": "No trade"}
TIMEFRAMES = ["minute", "3minute", "5minute", "15minute", "30minute", "60minute", "day"]

DEFAULT_RULES = [
    {"line": "prev_day_vwap", "event": "touch", "action": "BUY_CE", "enabled": True},
]

DEFAULT_CONFIG: dict = {
    "engine_version": ENGINE_VERSION,
    # ── data source ──
    "vwap_source": "futures",         # futures (true volume) | index (HLC3 average)
    "timeframe": "5minute",
    # ── signal rules ──
    "rules": [dict(r) for r in DEFAULT_RULES],
    "touch_buffer_pts": 5.0,          # how close counts as a "touch"
    "entry_start": "09:20",
    "entry_cutoff": "15:00",
    "one_signal_per_day": True,
    "max_trades_per_day": 3,
    # ── contract selection ──
    "expiry_type": "weekly",          # weekly | monthly
    "min_days_to_expiry": 0,
    "strike_mode": "fixed",           # fixed = use strike_offset_steps as-is
                                      # auto  = derive per option type from moneyness
    "strike_offset_steps": 0,         # 0 = ATM, +2 = ATM+100, -2 = ATM-100
    "auto_moneyness": "OTM",          # OTM | ATM | ITM  (auto mode)
    "auto_points": 100,               # distance from ATM in index points (auto mode)
    "lots": 1,
    # ── exits ──
    "target_mode": "percent",         # percent | points
    "sl_mode": "percent",
    "target_value": 30.0,
    "sl_value": 25.0,
    "exit_on": "premium",             # premium | index_points
    "square_off_time": "15:15",
    "hold_to_expiry": False,
    # ── fills / costs ──
    "fill_mode": "next_bar_open",     # next_bar_open (realistic) | signal_bar_close
    "apply_costs": True,
    "slippage_bps": 20.0,
    "brokerage_per_order": 20.0,
    "charges_pct": 0.10,
    # ── synthetic pricing fallback (expired contracts) ──
    "allow_modelled": True,           # off ⇒ skip signals with no real premium data
    "iv_source": "vix",               # vix | fixed
    "iv_fixed_pct": 14.0,
    "risk_free_pct": 6.5,
    # ── live / paper ──
    "paper_trade": True,
    "auto_start": False,
    "max_positions": 5,
    "telegram_alerts": False,
    "telegram_bot": "a",
}

_INT = {"min_days_to_expiry", "strike_offset_steps", "lots", "max_trades_per_day",
        "max_positions", "auto_points"}
_FLT = {"touch_buffer_pts", "target_value", "sl_value", "slippage_bps",
        "brokerage_per_order", "charges_pct", "iv_fixed_pct", "risk_free_pct"}


def sanitize_rule(r: dict) -> dict | None:
    line = str((r or {}).get("line", ""))
    event = str((r or {}).get("event", ""))
    action = str((r or {}).get("action", "NONE"))
    if line not in VWAP_LINES or event not in EVENTS or action not in ACTIONS:
        return None
    return {"line": line, "event": event, "action": action, "enabled": bool(r.get("enabled", True))}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    for k in _INT:
        try:
            out[k] = int(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    for k in _FLT:
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    if out["vwap_source"] not in ("futures", "index"):
        out["vwap_source"] = "futures"
    if out["timeframe"] not in TIMEFRAMES:
        out["timeframe"] = "5minute"
    if out["expiry_type"] not in ("weekly", "monthly"):
        out["expiry_type"] = "weekly"
    for k in ("target_mode", "sl_mode"):
        if out[k] not in ("percent", "points"):
            out[k] = "percent"
    if out["exit_on"] not in ("premium", "index_points"):
        out["exit_on"] = "premium"
    if out["fill_mode"] not in ("next_bar_open", "signal_bar_close"):
        out["fill_mode"] = "next_bar_open"
    if out["iv_source"] not in ("vix", "fixed"):
        out["iv_source"] = "vix"
    rules = [sanitize_rule(r) for r in (out.get("rules") or [])]
    out["rules"] = [r for r in rules if r] or [dict(r) for r in DEFAULT_RULES]
    if out["strike_mode"] not in ("fixed", "auto"):
        out["strike_mode"] = "fixed"
    if out["auto_moneyness"] not in ("OTM", "ATM", "ITM"):
        out["auto_moneyness"] = "OTM"
    out["auto_points"] = max(0, min(LADDER_STEPS * STRIKE_STEP, out["auto_points"]))
    out["strike_offset_steps"] = max(-LADDER_STEPS, min(LADDER_STEPS, out["strike_offset_steps"]))
    out["lots"] = max(1, out["lots"])
    out["max_trades_per_day"] = max(1, min(50, out["max_trades_per_day"]))
    out["max_positions"] = max(1, min(50, out["max_positions"]))
    out["touch_buffer_pts"] = max(0.0, out["touch_buffer_pts"])
    for b in ("one_signal_per_day", "hold_to_expiry", "apply_costs", "allow_modelled",
              "paper_trade", "auto_start", "telegram_alerts"):
        out[b] = bool(out[b])
    if out["telegram_bot"] not in ("a", "b"):
        out["telegram_bot"] = "a"
    out["engine_version"] = str(out.get("engine_version") or ENGINE_VERSION)
    return out


# ── durable storage (AppSetting) ──
_KEY = "vwap_options_config"


def load_config(db) -> dict:
    try:
        from core.models import AppSetting
        row = db.query(AppSetting).filter(AppSetting.key == _KEY).first()
        return sanitize((row.value if row else None) or {})
    except Exception as exc:
        logger.debug("vwap_options load_config failed: %s", exc)
        return sanitize({})


def save_config(db, partial: dict) -> dict:
    cfg = sanitize({**load_config(db), **(partial or {})})
    try:
        from core.models import AppSetting
        row = db.query(AppSetting).filter(AppSetting.key == _KEY).first()
        if row is None:
            db.add(AppSetting(key=_KEY, value=cfg, updated_at=datetime.now(timezone.utc)))
        else:
            row.value = cfg
            row.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.error("vwap_options save_config failed: %s", exc)
    return cfg
