"""
File-backed, versioned config for the Data Downloader — EXTENDS the existing
settings mechanism (uses ``config.settings.DATA_DIR``); it does not introduce a
second configuration system.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger
from research.data_downloader.constants import INTERVALS, FORMATS

logger = get_logger("research.data_downloader.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "data_downloader.json"
# All datasets live under the existing data directory (Railway-ephemeral; the
# storage layer is the single seam to later swap for S3/object storage).
DATA_ROOT = settings.DATA_DIR / "historical"

DEFAULT_CONFIG: dict = {
    "default_exchange": "NSE",
    "default_interval": "day",
    "default_format": "parquet",
    "timezone": "Asia/Kolkata",
    "max_retries": 3,
    "retry_delay": 2.0,             # seconds, exponential base
    "request_gap": 0.4,             # min seconds between Kite historical calls (<3/s)
    "enable_resume": True,
    "enable_quality_checks": True,
    "include_oi_default": True,
    "max_rows_view": 200,           # rows returned by the preview endpoint
}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    if out["default_interval"] not in INTERVALS:
        out["default_interval"] = "day"
    if out["default_format"] not in FORMATS:
        out["default_format"] = "parquet"
    try:
        out["max_retries"] = max(1, min(6, int(out["max_retries"])))
    except (TypeError, ValueError):
        out["max_retries"] = 3
    try:
        out["retry_delay"] = max(0.5, min(30.0, float(out["retry_delay"])))
    except (TypeError, ValueError):
        out["retry_delay"] = 2.0
    try:
        out["request_gap"] = max(0.34, min(5.0, float(out["request_gap"])))
    except (TypeError, ValueError):
        out["request_gap"] = 0.4
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("data_downloader config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("data_downloader config save failed: %s", exc)
    return cfg
