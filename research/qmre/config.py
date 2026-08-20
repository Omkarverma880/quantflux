"""
Config for Research #13 — Quantflux Momentum & Market Replay Engine (QMRE).

A single, versioned, data-driven config powers the SAME engine across Live,
Replay and Backtest. Weights, thresholds, risk, sizing and cost knobs are all
here so nothing is hard-coded in the pipeline. Durable via the AppSetting table
(survives Railway restarts); profiles are stored the same way.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.logger import get_logger

logger = get_logger("research.qmre.config")

STRATEGY_VERSION = "1.0"          # bump when the scoring pipeline changes materially
TIMEFRAME = "5minute"

# 11 scoring components → 100 points (normalised at runtime if edited).
DEFAULT_WEIGHTS = {
    "market_regime": 10, "sector_strength": 10, "price_trend": 10,
    "relative_strength": 10, "volume": 15, "breakout": 15,
    "vwap": 10, "volatility": 5, "liquidity": 5, "order_book": 5, "risk_reward": 5,
}

# score → signal class (lower-bound inclusive)
DEFAULT_CLASS_BANDS = [[85, "A+"], [75, "A"], [60, "B"], [45, "WATCH"], [0, "NO TRADE"]]

DEFAULT_CONFIG: dict = {
    "strategy_name": "Morning Momentum",
    "strategy_version": STRATEGY_VERSION,
    "weights": dict(DEFAULT_WEIGHTS),
    "class_bands": [list(b) for b in DEFAULT_CLASS_BANDS],
    # ── universe / eligibility ──
    "universe": "fno",                # fno | all | watchlist | single
    "min_price": 50.0,
    "max_price": 100000.0,
    "min_avg_value_cr": 5.0,          # min avg daily traded value (₹ cr) for liquidity
    "max_stocks": 0,                  # 0 = whole selected universe
    "top_n": 5,                       # default display count
    # ── feature params ──
    "opening_range_min": 15,          # 5 | 10 | 15 | 30
    "rvol_lookback_days": 20,
    "atr_period": 14,
    "vwap_slope_lookback": 6,         # candles for VWAP slope
    "rs_benchmark": "NIFTY 50",
    # ── breakout confirmation ──
    "breakout_rvol_min": 1.5,
    "breakout_needs_vwap": True,
    # ── risk / targets ──
    "sl_mode": "atr",                 # percent | atr | structure
    "sl_value": 1.5,                  # percent → %, atr → ATR multiple
    "target_mode": "atr",             # percent | atr | rr
    "target_value": 3.0,              # percent → %, atr → ATR multiple, rr → R multiple
    "min_rr": 1.5,                    # flag setups below this
    "trailing": "none",               # none | percent | atr
    "trailing_value": 1.0,
    # ── position sizing ──
    "capital_per_stock": 20000,
    "starting_capital": 1000000,
    "max_concurrent": 10,
    "max_capital_deployed_pct": 100,
    "max_trades_per_day": 20,
    "max_daily_loss_pct": 3.0,        # daily loss guard (of starting capital)
    "max_sector_positions": 4,
    # ── paper trading ──
    "mode": "intraday",               # intraday | swing
    "eod_exit_time": "15:15",         # intraday square-off
    "swing_max_hold_days": 5,
    "auto_paper_entry": False,        # auto-open paper positions on qualifying signals
    "entry_classes": ["A+", "A"],     # which classes auto-enter / alert
    # ── costs / slippage ──
    "apply_costs": True,
    "slippage_bps": 5.0,
    "brokerage_per_order": 0.0,
    "charges_pct": 0.12,
    # ── telegram ──
    "telegram_alerts": False,
    "telegram_bot": "a",              # a | b | both
    "alert_cooldown_min": 15,
    "alert_categories": {"a_plus": True, "a": True, "entry": True, "exit": True, "daily": True},
    # ── replay / backtest ──
    "replay_interval_min": 5,
}

_NUM_INT = {"max_stocks", "top_n", "opening_range_min", "rvol_lookback_days", "atr_period",
            "vwap_slope_lookback", "capital_per_stock", "starting_capital", "max_concurrent",
            "max_capital_deployed_pct", "max_trades_per_day", "max_sector_positions",
            "swing_max_hold_days", "alert_cooldown_min", "replay_interval_min"}
_NUM_FLOAT = {"min_price", "max_price", "min_avg_value_cr", "breakout_rvol_min", "sl_value",
              "target_value", "min_rr", "trailing_value", "max_daily_loss_pct",
              "slippage_bps", "brokerage_per_order", "charges_pct"}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    w = dict(DEFAULT_WEIGHTS)
    w.update({k: v for k, v in (out.get("weights") or {}).items() if k in w and v is not None})
    for k in w:
        try:
            w[k] = max(0.0, float(w[k]))
        except (TypeError, ValueError):
            w[k] = DEFAULT_WEIGHTS[k]
    out["weights"] = w
    for k in _NUM_INT:
        try:
            out[k] = int(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    for k in _NUM_FLOAT:
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    if out["universe"] not in ("fno", "all", "watchlist", "single"):
        out["universe"] = "fno"
    if out["sl_mode"] not in ("percent", "atr", "structure"):
        out["sl_mode"] = "atr"
    if out["target_mode"] not in ("percent", "atr", "rr"):
        out["target_mode"] = "atr"
    if out["mode"] not in ("intraday", "swing"):
        out["mode"] = "intraday"
    if out["telegram_bot"] not in ("a", "b", "both"):
        out["telegram_bot"] = "a"
    if out["trailing"] not in ("none", "percent", "atr"):
        out["trailing"] = "none"
    out["top_n"] = max(1, min(100, out["top_n"]))
    out["opening_range_min"] = out["opening_range_min"] if out["opening_range_min"] in (5, 10, 15, 30) else 15
    out["rvol_lookback_days"] = max(3, min(120, out["rvol_lookback_days"]))
    out["max_concurrent"] = max(1, min(200, out["max_concurrent"]))
    out["capital_per_stock"] = max(1000, out["capital_per_stock"])
    for b in ("breakout_needs_vwap", "apply_costs", "telegram_alerts", "auto_paper_entry"):
        out[b] = bool(out[b])
    cats = dict(DEFAULT_CONFIG["alert_categories"])
    cats.update({k: bool(v) for k, v in (out.get("alert_categories") or {}).items() if k in cats})
    out["alert_categories"] = cats
    out["entry_classes"] = [c for c in (out.get("entry_classes") or ["A+", "A"]) if c in ("A+", "A", "B")]
    bands = out.get("class_bands") or DEFAULT_CLASS_BANDS
    try:
        out["class_bands"] = sorted([[float(a), str(b)] for a, b in bands], key=lambda x: -x[0])
    except Exception:
        out["class_bands"] = [list(b) for b in DEFAULT_CLASS_BANDS]
    out["strategy_version"] = str(out.get("strategy_version") or STRATEGY_VERSION)
    return out


# ── durable config + named profiles via AppSetting (Postgres) ──
_KEY = "qmre_config"
_PROFILES_KEY = "qmre_profiles"


def _get(db, key):
    from core.models import AppSetting
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return (row.value if row else None)


def _set(db, key, value):
    from core.models import AppSetting
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        row = AppSetting(key=key, value=value, updated_at=datetime.now(timezone.utc))
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    db.commit()


def load_config(db) -> dict:
    try:
        return sanitize(_get(db, _KEY) or {})
    except Exception as exc:
        logger.debug("qmre load_config failed: %s", exc)
        return sanitize({})


def save_config(db, partial: dict) -> dict:
    cfg = sanitize({**load_config(db), **(partial or {})})
    try:
        _set(db, _KEY, cfg)
    except Exception as exc:
        logger.error("qmre save_config failed: %s", exc)
    return cfg


def list_profiles(db) -> dict:
    try:
        return _get(db, _PROFILES_KEY) or {}
    except Exception:
        return {}


def save_profile(db, name: str, cfg: dict) -> dict:
    profs = list_profiles(db)
    profs[name] = sanitize(cfg)
    _set(db, _PROFILES_KEY, profs)
    return profs
