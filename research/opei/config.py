"""
Durable, file-backed configuration for OPEI — strike selection, timeframe,
configurable confluence weights, entry-level tuning and Telegram settings.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger
from research.opei.constants import (
    STRIKE_OFFSETS, DEFAULT_STRIKE, CATEGORIES, DEFAULT_WEIGHTS,
)

logger = get_logger("research.opei.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "opei.json"

DEFAULT_CONFIG: dict = {
    "strike": DEFAULT_STRIKE,            # ATM | 100/200/300 ITM/OTM
    "timeframe": "5m",                   # premium candle timeframe for indicators
    "expiry_type": "weekly",             # weekly | monthly
    "refresh_interval": 3,               # front-end auto-refresh seconds
    "weights": dict(DEFAULT_WEIGHTS),    # per-category weights (configurable)
    "num_levels": 5,                     # entry levels per side
    "level_atr_mult": [0.6, 1.2, 2.0, 3.0, 4.5],   # premium-ATR ladders for levels
    "institutional_threshold": 95,
    # ── Telegram ──
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "alert_on_institutional": True,      # push when a qualifying best-level appears
    "alert_min_confidence": 95,          # min best-level confidence to log + alert (50–100)
}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    if out["strike"] not in STRIKE_OFFSETS:
        out["strike"] = DEFAULT_STRIKE
    if out["expiry_type"] not in ("weekly", "monthly"):
        out["expiry_type"] = "weekly"
    try:
        out["refresh_interval"] = max(1, min(30, int(out["refresh_interval"])))
    except (TypeError, ValueError):
        out["refresh_interval"] = 3
    # weights: keep only known categories, coerce to float, default missing
    w = out.get("weights") or {}
    out["weights"] = {c: float(w.get(c, DEFAULT_WEIGHTS[c]) or 0) for c in CATEGORIES}
    for b in ("telegram_enabled", "alert_on_institutional"):
        out[b] = bool(out[b])
    try:
        out["alert_min_confidence"] = max(50, min(100, int(out["alert_min_confidence"])))
    except (TypeError, ValueError):
        out["alert_min_confidence"] = 95
    out["telegram_bot_token"] = str(out.get("telegram_bot_token") or "")
    out["telegram_chat_id"] = str(out.get("telegram_chat_id") or "")
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("opei config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("opei config save failed: %s", exc)
    return cfg
