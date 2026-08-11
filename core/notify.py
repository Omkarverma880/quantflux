"""
Telegram notification layer — shared across the whole application.

Supports two independent bots:
  • Bot A ("a") — the original universal bot (DB key ``telegram``); default.
  • Bot B ("b") — a second bot (DB key ``telegram_b``) modules can opt into.

Each module chooses which bot to send through. Configured once in Settings →
Telegram. Persisted in PostgreSQL (survives Railway redeploys). Stdlib-only,
non-blocking, fails soft. All functions default to bot="a" for backward
compatibility with existing callers.
"""
from __future__ import annotations

import json
import time as _time
import urllib.parse
import urllib.request

from config import settings
from core.logger import get_logger

logger = get_logger("notify")

# DB key per bot. Bot A keeps the original key (+ legacy-file migration).
_KEYS = {"a": "telegram", "b": "telegram_b"}
_LABELS = {"a": "Bot A", "b": "Bot B"}
_FILE = settings.DATA_DIR / "telegram.json"      # legacy, Bot A only
_DEFAULT = {"enabled": False, "bot_token": "", "chat_id": ""}

# per-bot in-memory cache so frequent enabled()/send() checks don't hit the DB
_cache: dict = {}
_CACHE_TTL = 20.0


def _dbkey(bot: str) -> str:
    return _KEYS.get(bot, _KEYS["a"])


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


def _write_to_db(db, bot: str, cfg: dict) -> None:
    from core.models import AppSetting
    key = _dbkey(bot)
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = cfg
    else:
        db.add(AppSetting(key=key, value=cfg))
    db.commit()


def _load_from_db(bot: str) -> dict:
    from core.database import get_db_session
    from core.models import AppSetting
    db = get_db_session()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _dbkey(bot)).first()
        if row and row.value:
            return _norm(row.value)
        if bot == "a":       # one-time legacy-file migration for the original bot
            filecfg = _read_file()
            if filecfg.get("bot_token") or filecfg.get("chat_id"):
                _write_to_db(db, "a", filecfg)
                return filecfg
        return dict(_DEFAULT)
    except Exception as exc:
        logger.debug("telegram DB read failed (%s)", exc)
        return _read_file() if bot == "a" else dict(_DEFAULT)
    finally:
        db.close()


def load_config(bot: str = "a") -> dict:
    now = _time.monotonic()
    c = _cache.get(bot)
    if c and now - c["ts"] < _CACHE_TTL:
        return dict(c["value"])
    cfg = _load_from_db(bot)
    _cache[bot] = {"value": cfg, "ts": now}
    return dict(cfg)


def save_config(partial: dict, bot: str = "a") -> dict:
    cfg = load_config(bot)
    for k in ("enabled", "bot_token", "chat_id"):
        if partial and k in partial and partial[k] is not None:
            cfg[k] = bool(partial[k]) if k == "enabled" else str(partial[k])
    cfg = _norm(cfg)
    from core.database import get_db_session
    db = get_db_session()
    try:
        _write_to_db(db, bot, cfg)
    except Exception as exc:
        db.rollback()
        logger.error("telegram DB save failed: %s", exc)
    finally:
        db.close()
    if bot == "a":                       # best-effort legacy file mirror (Bot A only)
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            _FILE.write_text(json.dumps(cfg, indent=2))
        except Exception:
            pass
    _cache[bot] = {"value": dict(cfg), "ts": _time.monotonic()}
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


def send(text: str, bot: str = "a", cfg: dict | None = None) -> dict:
    """Send using the selected bot's config. No-op (soft) if disabled/unconfigured."""
    cfg = cfg or load_config(bot)
    if not cfg.get("enabled"):
        return {"ok": False, "error": "Telegram disabled"}
    return _send_raw(cfg["bot_token"], cfg["chat_id"], text)


def test(bot_token: str, chat_id: str) -> dict:
    return _send_raw(bot_token, chat_id, "✅ <b>QuantFlux</b> — Telegram connected successfully.")


def enabled(bot: str = "a") -> bool:
    c = load_config(bot)
    return bool(c["enabled"] and c["bot_token"] and c["chat_id"])
