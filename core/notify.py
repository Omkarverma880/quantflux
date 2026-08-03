"""
Universal Telegram notification layer — shared across the whole application.

One place stores the bot token / chat id (Settings → Telegram); every module
(research live scans, live strategies, OPEI) sends through here so the user
configures Telegram once. Stdlib-only, non-blocking, fails soft.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from config import settings
from core.logger import get_logger

logger = get_logger("notify")

_FILE = settings.DATA_DIR / "telegram.json"
_DEFAULT = {"enabled": False, "bot_token": "", "chat_id": ""}


def load_config() -> dict:
    cfg = dict(_DEFAULT)
    try:
        if _FILE.exists():
            cfg.update(json.loads(_FILE.read_text()) or {})
    except Exception as exc:
        logger.debug("telegram config read failed: %s", exc)
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["bot_token"] = str(cfg.get("bot_token") or "")
    cfg["chat_id"] = str(cfg.get("chat_id") or "")
    return cfg


def save_config(partial: dict) -> dict:
    cfg = load_config()
    for k in ("enabled", "bot_token", "chat_id"):
        if partial and k in partial and partial[k] is not None:
            cfg[k] = bool(partial[k]) if k == "enabled" else str(partial[k])
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(cfg, indent=2))
    except Exception as exc:
        logger.error("telegram config save failed: %s", exc)
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
