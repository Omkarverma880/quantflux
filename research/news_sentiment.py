"""
Research helper — Indian market NEWS sentiment.

Read-only. Pulls headlines from trusted, FREE, no-API-key RSS feeds of major
Indian financial publishers (Economic Times Markets, Moneycontrol, Business
Standard, LiveMint) and scores them with a finance bull/bear lexicon to produce
a domestic news bias for the Market Dashboard.

Why RSS (not a paid API): these are official publisher feeds — free, trusted,
no key, no rate-limit billing, production-safe. If you later want a licensed
provider (NewsAPI.org, Marketaux, Alpha Vantage NEWS_SENTIMENT, Finnhub), set
an API key in config and add a fetcher — the scoring/aggregation here is reused.

Cached ~10 minutes (news moves slowly); every network call is time-boxed and
failures degrade gracefully (one dead feed never breaks the module).
"""
from __future__ import annotations

import html
import re
import threading
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

from core.logger import get_logger

logger = get_logger("research.news_sentiment")

FEEDS = [
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("LiveMint", "https://www.livemint.com/rss/markets"),
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantFluxResearch/1.0)"}

# Finance-tuned sentiment lexicon (headline-level).
BULL = {
    "surge", "surges", "surged", "rally", "rallies", "rallied", "gain", "gains", "gained",
    "jump", "jumps", "jumped", "soar", "soars", "soared", "rise", "rises", "rose", "rising",
    "high", "record", "all-time", "boost", "boosts", "upgrade", "upgraded", "beat", "beats",
    "profit", "profits", "strong", "strengthen", "outperform", "bullish", "buy", "recovery",
    "rebound", "rebounds", "up", "advance", "advances", "top", "tops", "optimism", "boom",
    "positive", "expand", "expands", "growth", "grows", "hike", "inflow", "inflows",
}
BEAR = {
    "fall", "falls", "fell", "falling", "drop", "drops", "dropped", "plunge", "plunges",
    "plunged", "slump", "slumps", "slumped", "slide", "slides", "tumble", "tumbles",
    "tumbled", "sink", "sinks", "crash", "crashes", "loss", "losses", "weak", "weakness",
    "downgrade", "downgraded", "miss", "misses", "sell", "selloff", "sell-off", "bearish",
    "down", "decline", "declines", "declined", "cut", "cuts", "fear", "fears", "worst",
    "low", "lows", "pressure", "drag", "drags", "outflow", "outflows", "concern", "concerns",
    "recession", "slowdown", "warn", "warns", "warning", "default", "risk", "risks",
}

_WORD = re.compile(r"[a-zA-Z][a-zA-Z\-']+")


def _score_headline(title: str) -> int:
    words = [w.lower() for w in _WORD.findall(title or "")]
    return sum(1 for w in words if w in BULL) - sum(1 for w in words if w in BEAR)


def _bias(score: float) -> str:
    if score > 0.08:
        return "Bullish"
    if score < -0.08:
        return "Bearish"
    return "Neutral"


class NewsSentiment:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Optional[dict] = None
        self._cache_at: Optional[datetime] = None

    def _fetch_feed(self, name: str, url: str, limit: int = 15) -> list[dict]:
        try:
            import requests
            r = requests.get(url, headers=_HEADERS, timeout=6)
            if r.status_code != 200 or not r.content:
                return []
            root = ET.fromstring(r.content)
        except Exception as exc:
            logger.debug("news feed %s failed: %s", name, exc)
            return []
        items = []
        for it in root.iter("item"):
            t = it.findtext("title") or ""
            t = html.unescape(t).strip()
            if not t:
                continue
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            items.append({"title": t, "link": link, "pub": pub, "source": name,
                          "score": _score_headline(t)})
            if len(items) >= limit:
                break
        return items

    def snapshot(self, force: bool = False, ttl_seconds: int = 600) -> dict:
        with self._lock:
            now = datetime.now()
            if not force and self._cache and self._cache_at and (now - self._cache_at).total_seconds() < ttl_seconds:
                return self._cache

            headlines: list[dict] = []
            sources_ok = []
            # Fetch all feeds concurrently so a cold call is bounded by the
            # slowest single feed (~6s), not the sum of all of them.
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
                futs = {pool.submit(self._fetch_feed, name, url): name for name, url in FEEDS}
                for fut in as_completed(futs):
                    try:
                        rows = fut.result()
                    except Exception:
                        rows = []
                    if rows:
                        sources_ok.append(futs[fut])
                        headlines.extend(rows)

            if not headlines:
                out = {"status": "error", "message": "No news feeds reachable",
                       "bias": "Neutral", "available": False,
                       "updated_at": now.strftime("%Y-%m-%d %H:%M:%S")}
                # cache the failure briefly so we don't hammer dead feeds
                self._cache, self._cache_at = out, now
                return out

            pos = sum(1 for h in headlines if h["score"] > 0)
            neg = sum(1 for h in headlines if h["score"] < 0)
            neu = len(headlines) - pos - neg
            total_score = sum(h["score"] for h in headlines)
            avg = total_score / len(headlines) if headlines else 0.0
            bias = _bias(avg)
            # 0–100 gauge (±0.6 avg → 0/100)
            gauge = round(max(0.0, min(100.0, 50.0 + avg * 83.0)), 1)
            pct_pos = round(pos / len(headlines) * 100, 1) if headlines else 0.0

            # most-polarising sample headlines for display
            sample = sorted(headlines, key=lambda h: abs(h["score"]), reverse=True)[:8]
            sample = [{"title": h["title"], "source": h["source"], "link": h["link"],
                       "score": h["score"],
                       "tone": "pos" if h["score"] > 0 else "neg" if h["score"] < 0 else "neu"}
                      for h in sample]

            out = {
                "status": "ok", "available": True, "bias": bias, "gauge": gauge,
                "avg_score": round(avg, 3), "positive": pos, "negative": neg, "neutral": neu,
                "total": len(headlines), "pct_positive": pct_pos,
                "sources": sources_ok, "headlines": sample,
                "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._cache, self._cache_at = out, now
            return out
