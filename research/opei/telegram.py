"""
Telegram notification layer for OPEI (stdlib-only, no extra deps).

Sends alerts via the Telegram Bot API. Used to push institutional-grade entry
appearances, activations and outcome events. Never blocks the engine — callers
should fire-and-forget or handle failures gracefully.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from core.logger import get_logger

logger = get_logger("research.opei.telegram")


def send_message(bot_token: str, chat_id: str, text: str, timeout: float = 6.0) -> dict:
    """Send a Telegram message. Returns {ok, error}."""
    if not bot_token or not chat_id:
        return {"ok": False, "error": "Bot token / chat id not configured"}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        return {"ok": bool(body.get("ok")), "error": None if body.get("ok") else body.get("description")}
    except Exception as exc:
        logger.debug("telegram send failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def test_connection(bot_token: str, chat_id: str) -> dict:
    return send_message(bot_token, chat_id,
                        "✅ <b>QuantFlux OPEI</b> — Telegram connected successfully.")


def format_entry(rec: dict) -> str:
    """Build the alert message for a recommended/activated entry."""
    reasons = "\n".join(f"✅ {r}" for r in (rec.get("reasons") or [])[:8])
    tgts = rec.get("targets") or []
    tgt_lines = "\n".join(f"Target {i+1}\n₹{t}" for i, t in enumerate(tgts))
    return (
        "🚨 <b>OPTION PREMIUM ENTRY</b>\n\n"
        f"Instrument\n{rec.get('symbol','')}\n\n"
        f"Current Premium\n₹{rec.get('premium','')}\n\n"
        f"Recommended Entry\n₹{rec.get('level','')}\n\n"
        f"Confidence\n{rec.get('confidence','')}%\n\n"
        f"Probability\n{rec.get('band','')}\n\n"
        f"SL\n₹{rec.get('sl','')}\n\n"
        f"{tgt_lines}\n\n"
        "Reasons\n"
        f"{reasons}\n\n"
        f"Time\n{rec.get('time','')}\n\n"
        "Research\nOption Premium Entry Intelligence Engine"
    )
