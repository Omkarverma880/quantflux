"""
Universal Telegram notification layer — shared across the whole application.

One place stores the bot token / chat id (Settings → Telegram); every module
(research live scans, live strategies, OPEI) sends through here so the user
configures Telegram once. Stdlib-only, non-blocking, fails soft.
"""
from __future__ import annotations

import json
import time as _time
import urllib.parse
import urllib.request

from config import settings
from core.logger import get_logger

logger = get_logger("notify")

# Legacy file (kept only for a one-time migration into the DB). The DB is now the
# source of truth because Railway's filesystem is ephemeral (the file was wiped
# on every redeploy, which is why the token kept disappearing).
_FILE = settings.DATA_DIR / "telegram.json"
_KEY = "telegram"
_DEFAULT = {"enabled": False, "bot_token": "", "chat_id": ""}

# tiny in-memory cache so frequent enabled()/send() checks don't hit the DB each time
_cache: dict = {"value": None, "ts": 0.0}
_CACHE_TTL = 20.0


def _norm(d: dict) -> dict:
    return {"enabled": bool(d.get("enabled")),
            "bot_token": str(d.get("bot_token") or ""),
            "chat_id": str(d.get("chat_id") or "")}


def _read_file() -> dict:
    try:
        if _FILE.exists():
            return _norm(json.loads(_FILE.read_text()) or {})
    except Exception as exc:
        logger.debug("telegram file read failed: %s", exc)
    return dict(_DEFAULT)


def _load_from_db() -> dict:
    from core.database import get_db_session
    from core.models import AppSetting
    db = get_db_session()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _KEY).first()
        if row and row.value:
            return _norm(row.value)
        # One-time migration: if a legacy file still exists, persist it to the DB.
        filecfg = _read_file()
        if filecfg.get("bot_token") or filecfg.get("chat_id"):
            _write_to_db(db, filecfg)
            return filecfg
        return dict(_DEFAULT)
    except Exception as exc:
        logger.debug("telegram DB read failed (%s) — falling back to file", exc)
        return _read_file()
    finally:
        db.close()


def _write_to_db(db, cfg: dict) -> None:
    from core.models import AppSetting
    row = db.query(AppSetting).filter(AppSetting.key == _KEY).first()
    if row:
        row.value = cfg
    else:
        db.add(AppSetting(key=_KEY, value=cfg))
    db.commit()


def load_config() -> dict:
    now = _time.monotonic()
    if _cache["value"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return dict(_cache["value"])
    cfg = _load_from_db()
    _cache["value"] = cfg
    _cache["ts"] = now
    return dict(cfg)


def save_config(partial: dict) -> dict:
    cfg = load_config()
    for k in ("enabled", "bot_token", "chat_id"):
        if partial and k in partial and partial[k] is not None:
            cfg[k] = bool(partial[k]) if k == "enabled" else str(partial[k])
    cfg = _norm(cfg)
    # persist to PostgreSQL (survives redeploys); best-effort file mirror too
    from core.database import get_db_session
    db = get_db_session()
    try:
        _write_to_db(db, cfg)
    except Exception as exc:
        db.rollback()
        logger.error("telegram DB save failed: %s", exc)
    finally:
        db.close()
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass
    _cache["value"] = dict(cfg)          # refresh cache immediately
    _cache["ts"] = _time.monotonic()
    return cfg


def _send_raw(bot_token: str, chat_id: str, text: str, timeout: float = 6.0) -> dict:
    if not bot_token or not chat_id:
        return {"ok": False, "error": "Bot token / chat id not configured"}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        return {"ok": bool(body.get("ok")), "error": None if body.get("ok") else body.get("description")}
    except Exception as exc:
        logger.debug("telegram send failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def send(text: str, cfg: dict | None = None) -> dict:
    """Send using the universal config. No-op (soft) if disabled/unconfigured."""
    cfg = cfg or load_config()
    if not cfg.get("enabled"):
        return {"ok": False, "error": "Telegram disabled"}
    return _send_raw(cfg["bot_token"], cfg["chat_id"], text)


def test(bot_token: str, chat_id: str) -> dict:
    return _send_raw(bot_token, chat_id, "✅ <b>QuantFlux</b> — Telegram connected successfully.")


def enabled() -> bool:
    c = load_config()
    return bool(c["enabled"] and c["bot_token"] and c["chat_id"])
