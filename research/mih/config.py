"""Config for Research #14 — Market Intelligence Hub (MIH). Read-only research."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.logger import get_logger

logger = get_logger("research.mih.config")
MODULE_VERSION = "1.0"

DEFAULT_CONFIG: dict = {
    "universe": "fno",            # fno | watchlist
    "max_stocks": 0,              # 0 = all in universe
    "top_n": 6,                   # rows per card
    "min_price": 20.0,
    "min_volume": 10000,
    # scanner tolerances
    "open_eq_tol_pct": 0.15,      # |open-low|/open ≤ this ⇒ "open = low"
    "gap_pct": 1.0,               # gap up/down threshold
    "vol_shocker_rvol": 3.0,
    "breakout_rvol": 2.0,
    "breakout_change_pct": 2.0,
    "near_52w_pct": 1.0,          # within x% of the 52-week extreme
    # stock score weights (technical only — see scoring.py for the honesty note)
    "score_weights": {"trend": 25, "momentum": 25, "volume": 20, "vwap": 15, "range": 15},
    # trade ideas
    "idea_min_score": 6.5,        # 0-10 score needed to publish an idea
    "idea_sl_atr": 1.5,
    "idea_target_atr": 3.0,
    "idea_max": 12,
    # enrichment (daily history for 52w / avg-vol) — capped per scan, day-cached
    "enrich_cap": 60,
    "enrich_lookback_days": 260,
}

_INT = {"max_stocks", "top_n", "min_volume", "idea_max", "enrich_cap", "enrich_lookback_days"}
_FLT = {"min_price", "open_eq_tol_pct", "gap_pct", "vol_shocker_rvol", "breakout_rvol",
        "breakout_change_pct", "near_52w_pct", "idea_min_score", "idea_sl_atr", "idea_target_atr"}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    w = dict(DEFAULT_CONFIG["score_weights"])
    w.update({k: v for k, v in (out.get("score_weights") or {}).items() if k in w and v is not None})
    for k in w:
        try:
            w[k] = max(0.0, float(w[k]))
        except (TypeError, ValueError):
            w[k] = DEFAULT_CONFIG["score_weights"][k]
    out["score_weights"] = w
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
    out["top_n"] = max(1, min(50, out["top_n"]))
    out["enrich_cap"] = max(0, min(250, out["enrich_cap"]))
    out["idea_max"] = max(1, min(50, out["idea_max"]))
    if out["universe"] not in ("fno", "watchlist"):
        out["universe"] = "fno"
    return out


_KEY = "mih_config"


def load_config(db) -> dict:
    try:
        from core.models import AppSetting
        row = db.query(AppSetting).filter(AppSetting.key == _KEY).first()
        return sanitize((row.value if row else None) or {})
    except Exception as exc:
        logger.debug("mih load_config failed: %s", exc)
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
        logger.error("mih save_config failed: %s", exc)
    return cfg
