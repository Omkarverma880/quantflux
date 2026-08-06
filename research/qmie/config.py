"""
Durable, file-backed, versioned configuration for QMIE (§10, §58).

Governs universe selection, horizon, component weights, gate thresholds and
result limits. Changing config never mutates a past snapshot — each scan records
the config_version it used. There is NO setting that can enable order placement;
such a key is rejected by ``sanitize`` (§58.3).
"""
from __future__ import annotations

import json
import time

from config import settings
from core.logger import get_logger
from research.qmie.constants import (
    HORIZONS, DEFAULT_HORIZON, COMPONENTS, DEFAULT_WEIGHTS, LIQUIDITY_FLOOR,
)

logger = get_logger("research.qmie.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "qmie.json"

# Any key whose name hints at execution is refused outright (defence in depth).
_FORBIDDEN_SUBSTRINGS = ("order", "execute", "buy", "sell", "trade", "gtt", "place", "square")

DEFAULT_CONFIG: dict = {
    "universe": "fno",            # fno | watchlist | custom
    "custom_symbols": [],         # explicit symbols when universe == custom
    "horizon": DEFAULT_HORIZON,
    "direction": "long_only",     # long_only | long_short  (short is research-only too)
    "weights": dict(DEFAULT_WEIGHTS),
    "max_instruments": 40,        # cap per scan (keeps a manual scan responsive)
    "top_n": 20,                  # default display limit
    "liquidity_floor": None,      # override; None → horizon default
    "min_rr": None,               # override; None → horizon default
    "config_version": "qmie-cfg-1",
}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    for k, v in (cfg or {}).items():
        if k not in DEFAULT_CONFIG or v is None:
            continue
        if any(bad in k.lower() for bad in _FORBIDDEN_SUBSTRINGS):
            logger.error("QMIE config rejected forbidden key: %s", k)
            continue
        out[k] = v
    if out["horizon"] not in HORIZONS:
        out["horizon"] = DEFAULT_HORIZON
    if out["universe"] not in ("fno", "watchlist", "custom"):
        out["universe"] = "fno"
    if out["direction"] not in ("long_only", "long_short"):
        out["direction"] = "long_only"
    try:
        out["max_instruments"] = max(5, min(250, int(out["max_instruments"])))
    except (TypeError, ValueError):
        out["max_instruments"] = 40
    try:
        out["top_n"] = max(2, min(100, int(out["top_n"])))
    except (TypeError, ValueError):
        out["top_n"] = 20
    w = out.get("weights") or {}
    out["weights"] = {c: float(w.get(c, DEFAULT_WEIGHTS[c]) or 0) for c in COMPONENTS}
    out["custom_symbols"] = [str(s).strip().upper() for s in (out.get("custom_symbols") or []) if str(s).strip()]
    return out


def liquidity_floor(cfg: dict) -> float:
    if cfg.get("liquidity_floor"):
        try:
            return float(cfg["liquidity_floor"])
        except (TypeError, ValueError):
            pass
    return float(LIQUIDITY_FLOOR.get(cfg["horizon"], 20_000_000))


def min_rr(cfg: dict) -> float:
    if cfg.get("min_rr"):
        try:
            return float(cfg["min_rr"])
        except (TypeError, ValueError):
            pass
    return float(HORIZONS[cfg["horizon"]]["min_rr"])


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("qmie config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    cfg["config_version"] = f"qmie-cfg-{int(time.time())}"     # new immutable version on change
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("qmie config save failed: %s", exc)
    return cfg
