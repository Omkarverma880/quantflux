"""
Hunter — the universe being scanned.

The NIFTY 500 constituent list (with each stock's industry) comes from NSE's own published CSV,
cached to disk so a scan never depends on that site being up. Zerodha tokens are resolved with the
existing ``pmvwap_straddle.Universe`` helper — no second instrument master, no second broker client.

If NSE cannot be reached and no cache exists, the scan falls back to the most-traded NSE equities
from the instrument dump, and says so, rather than silently scanning a different universe.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.hunter.universe")

ROOT = Path(MS.ROOT).parent / "hunter"
LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
CACHE = ROOT / "universe.json"
MAX_AGE_DAYS = 7
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*", "Accept-Language": "en-US,en"}


def _fetch_list() -> list[dict]:
    with urllib.request.urlopen(urllib.request.Request(LIST_URL, headers=HEADERS), timeout=30) as r:
        text = r.read().decode("utf-8-sig")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("Symbol") or "").strip().upper()
        if sym and (row.get("Series") or "EQ").strip().upper() == "EQ":
            rows.append({"symbol": sym, "name": (row.get("Company Name") or "").strip(),
                         "industry": (row.get("Industry") or "Unclassified").strip()})
    if len(rows) < 100:
        raise ValueError(f"NSE returned only {len(rows)} names")
    return rows


def _read_cache() -> Optional[dict]:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return None


def _write_cache(payload: dict) -> None:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(payload, default=str))
    except Exception as exc:
        logger.debug("universe cache write failed: %s", exc)


def load(refresh: bool = False) -> dict:
    """The constituent list, refreshed at most weekly. Never fails the scan."""
    cached = _read_cache()
    fresh_enough = False
    if cached:
        try:
            age = (date.today() - datetime.fromisoformat(cached["fetched"]).date()).days
            fresh_enough = age <= MAX_AGE_DAYS
        except Exception:
            fresh_enough = False
    if cached and fresh_enough and not refresh:
        return cached
    try:
        rows = _fetch_list()
        payload = {"source": "NSE NIFTY 500 list", "fetched": date.today().isoformat(),
                   "count": len(rows), "stocks": rows}
        _write_cache(payload)
        return payload
    except Exception as exc:
        logger.warning("NIFTY 500 list fetch failed (%s)", exc)
        if cached:
            return {**cached, "stale": True, "note": f"NSE list unreachable ({type(exc).__name__}); using the cached list from {cached.get('fetched')}"}
        return {"source": "unavailable", "fetched": None, "count": 0, "stocks": [],
                "note": f"NSE list unreachable ({type(exc).__name__}) and nothing cached yet"}


def with_tokens(broker, refresh: bool = False) -> tuple[list[dict], list[str]]:
    """Each constituent with its Zerodha token; names that cannot be resolved are reported."""
    from research.pmvwap_straddle.universe import Universe
    u = Universe(broker)
    out, missing = [], []
    for s in load(refresh).get("stocks", []):
        token, exch = u.resolve_equity_token(s["symbol"])
        if token:
            out.append({**s, "token": int(token), "exchange": exch or "NSE"})
        else:
            missing.append(s["symbol"])
    return out, missing


def industries(stocks: list[dict]) -> list[str]:
    return sorted({s.get("industry") or "Unclassified" for s in stocks})
