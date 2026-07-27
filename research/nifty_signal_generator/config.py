"""
Configuration for the NIFTY Signal Generator.

Durable, editable-without-redeploy config using the same on-disk JSON pattern
as the other research modules (``research/nifty_sentiment.py``). All tunable
values live in ``DEFAULT_CONFIG`` — nothing in the engine is hardcoded.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger
from research.nifty_signal_generator.constants import (
    DEFAULT_TIMEFRAME, DEFAULT_STRIKE_INTERVAL, DEFAULT_STRIKE_COUNT,
    DEFAULT_MARKET, TIMEFRAME_MAP, STRIKE_INTERVALS, MARKETS,
)

logger = get_logger("research.nifty_signal_generator.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "nifty_signal_generator.json"

DEFAULT_CONFIG: dict = {
    "timeframe": DEFAULT_TIMEFRAME,          # see constants.TIMEFRAMES
    "strike_interval": DEFAULT_STRIKE_INTERVAL,  # 25 | 50 | 100
    "strike_count": DEFAULT_STRIKE_COUNT,    # strikes above/below ATM (>=1)
    "market": DEFAULT_MARKET,                # NIFTY (others reserved)
    "expiry_type": "weekly",                 # weekly | monthly (which OI series)
    "refresh_interval": 30,                  # front-end auto-refresh seconds (3–300)
    "show_previous_vwap": True,              # UI toggle; column always in data model
}


def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def sanitize(cfg: dict) -> dict:
    """Coerce a raw config dict into valid, in-range values (never raises)."""
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})

    if out["timeframe"] not in TIMEFRAME_MAP:
        out["timeframe"] = DEFAULT_TIMEFRAME
    if _clamp(out["strike_interval"], 1, 10_000, None) not in STRIKE_INTERVALS:
        out["strike_interval"] = DEFAULT_STRIKE_INTERVAL
    else:
        out["strike_interval"] = int(out["strike_interval"])
    out["strike_count"] = _clamp(out["strike_count"], 1, 20, DEFAULT_STRIKE_COUNT)
    if out["market"] not in MARKETS or not MARKETS[out["market"]]["enabled"]:
        out["market"] = DEFAULT_MARKET
    if out["expiry_type"] not in ("weekly", "monthly"):
        out["expiry_type"] = "weekly"
    out["refresh_interval"] = _clamp(out["refresh_interval"], 3, 300, 30)
    out["show_previous_vwap"] = bool(out["show_previous_vwap"])
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("nifty_signal_generator config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("nifty_signal_generator config save failed: %s", exc)
    return cfg
