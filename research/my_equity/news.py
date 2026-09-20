"""
Per-stock headlines for the X-ray.

The market-wide feed in ``research.news_sentiment`` answers "how is the market feeling";
this answers "what is being said about THIS company". Google News' RSS search is used
because it aggregates the Indian financial press, needs no API key and no billing — the
same reasoning behind the publisher feeds in the market-wide module, whose finance lexicon
scores the headlines here too.

Every call is time-boxed and cached; a dead feed degrades to "no headlines", never an error
page, and never blocks the rest of the X-ray.
"""
from __future__ import annotations

import html
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

from core.logger import get_logger
from research.news_sentiment import _HEADERS, _bias, _score_headline

logger = get_logger("research.my_equity.news")

TTL_S = 900             # headlines move slowly; 15 minutes is plenty
TIMEOUT_S = 6
FEED = ("https://news.google.com/rss/search?q={q}+when:21d&hl=en-IN&gl=IN&ceid=IN:en")

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _query(symbol: str, company: Optional[str]) -> str:
    """Company name when we know it (far less noisy than a ticker like BSE or IDEA)."""
    name = (company or "").strip()
    base = f'"{name}"' if len(name) > 3 else f'"{symbol}"'
    return quote_plus(f"{base} share price NSE")


def _age(pub: str) -> Optional[str]:
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except Exception:
        return None
    if mins < 60:
        return f"{int(mins)} min ago"
    if mins < 1440:
        return f"{int(mins / 60)} h ago"
    return f"{int(mins / 1440)} d ago"


def _fetch(symbol: str, company: Optional[str], limit: int) -> dict:
    url = FEED.format(q=_query(symbol, company))
    try:
        import requests
        r = requests.get(url, headers=_HEADERS, timeout=TIMEOUT_S)
        if r.status_code != 200 or not r.content:
            return {"status": "ok", "available": False, "items": [],
                    "message": f"news feed returned {r.status_code}"}
        root = ET.fromstring(r.content)
    except Exception as exc:
        logger.debug("news for %s failed: %s", symbol, exc)
        return {"status": "ok", "available": False, "items": [], "message": "news feed unreachable"}
    items = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        if not title:
            continue
        source = (it.findtext("source") or "").strip()
        # Google appends " - Publisher" to the headline; keep the headline clean
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        score = _score_headline(title)
        items.append({"title": title, "link": (it.findtext("link") or "").strip(),
                      "source": source or "Google News", "published": (it.findtext("pubDate") or "").strip(),
                      "age": _age(it.findtext("pubDate") or ""), "score": score,
                      "tone": "pos" if score > 0 else "neg" if score < 0 else "neu"})
        if len(items) >= limit:
            break
    if not items:
        return {"status": "ok", "available": False, "items": [], "message": "no recent headlines"}
    avg = sum(i["score"] for i in items) / len(items)
    return {"status": "ok", "available": True, "items": items, "bias": _bias(avg),
            "avg_score": round(avg, 3),
            "positive": sum(1 for i in items if i["score"] > 0),
            "negative": sum(1 for i in items if i["score"] < 0),
            "query": _query(symbol, company)}


def headlines(symbol: str, company: Optional[str] = None, limit: int = 10,
              force: bool = False) -> dict:
    key = f"{symbol.upper()}|{company or ''}|{limit}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and not force and now - hit[0] < TTL_S:
            return {**hit[1], "cached": True}
    out = _fetch(symbol.upper(), company, limit)
    out["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _cache[key] = (now, out)
    return out
