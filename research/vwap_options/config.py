"""
Config for the VWAP Options Engine (NIFTY index options).

One versioned, durable config drives the SAME signal + simulation core across
Backtest, Single-day Simulate and Live/Paper, so the three can never diverge.
Stored in the AppSetting table (survives Railway restarts).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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
    "cur_week_vwap": "Current-week VWAP",
    "cur_month_vwap": "Current-month VWAP",
    "roll_15d_vwap": "Rolling 15-day VWAP",
    "roll_90d_vwap": "Rolling 90-day VWAP",
    # % bands measured off the previous-month VWAP (the Pine "levels")
    "pm_band_p1": "Prev-month VWAP +5%",
    "pm_band_p2": "Prev-month VWAP +10%",
    "pm_band_p3": "Prev-month VWAP +15%",
    "pm_band_p4": "Prev-month VWAP +20%",
    "pm_band_m1": "Prev-month VWAP -5%",
    "pm_band_m2": "Prev-month VWAP -10%",
    "pm_band_m3": "Prev-month VWAP -15%",
    "pm_band_m4": "Prev-month VWAP -20%",
}
ROLLING_DAYS = {"roll_15d_vwap": 15, "roll_90d_vwap": 90}
DEFAULT_BAND_PCTS = [5.0, 10.0, 15.0, 20.0]
BAND_KEYS = [("pm_band_p%d" % i, 1) for i in range(1, 5)] +             [("pm_band_m%d" % i, -1) for i in range(1, 5)]


def lines_for(cfg: dict | None = None) -> dict:
    """VWAP_LINES with the band labels rewritten to the configured percentages."""
    out = dict(VWAP_LINES)
    pcts = band_pcts(cfg or {})
    for i, p in enumerate(pcts, start=1):
        txt = ("%g" % p)
        out["pm_band_p%d" % i] = "Prev-month VWAP +%s%%" % txt
        out["pm_band_m%d" % i] = "Prev-month VWAP -%s%%" % txt
    return out


def band_pcts(cfg: dict) -> list:
    """Four positive percentages; falls back to the defaults on bad input."""
    raw = (cfg or {}).get("pm_band_pcts") or DEFAULT_BAND_PCTS
    out = []
    for i in range(4):
        try:
            v = float(raw[i])
        except (TypeError, ValueError, IndexError):
            v = DEFAULT_BAND_PCTS[i]
        out.append(v if v > 0 else DEFAULT_BAND_PCTS[i])
    return out


def warmup_days(cfg: dict, start: date | None = None) -> int:
    """Calendar days of history required BEFORE ``start`` so every VWAP line is warm.

    The previous-MONTH VWAP must accumulate the *whole* previous month, so the
    window has to reach the 1st of that month — up to 62 days, not a flat 35.
    A short window silently yields a PARTIAL-month VWAP under the previous-month
    label, which is a wrong trade level, so this is sized from the real date.
    """
    need = 14                                        # prev-day / prev-week floor
    if start is not None:
        first_prev_month = (start.replace(day=1) - timedelta(days=1)).replace(day=1)
        need = max(need, (start - first_prev_month).days + 3)      # +3 safety margin
    else:
        need = max(need, 62)                         # worst case: last day of a month
    for r in (cfg or {}).get("rules") or []:
        n = ROLLING_DAYS.get(r.get("line"))
        if r.get("enabled") and n:
            need = max(need, int(n * 1.5) + 10)      # trading → calendar days
    return need


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
    "pm_band_pcts": [5.0, 10.0, 15.0, 20.0],   # % bands off the prev-month VWAP
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
    out["pm_band_pcts"] = band_pcts(out)   # coerce UI strings to clean floats
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
