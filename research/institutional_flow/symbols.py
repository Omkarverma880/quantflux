"""
Turning a company name into an NSE trading symbol, without guessing.

The stock-level lists this screen reads are published by name — "Shriram Finance", "M&M",
"Sterlite Tech" — while everything downstream (quotes, volume, the daily cache) is keyed by
trading symbol. Something has to bridge the two, and the way it bridges matters: a wrong match
puts one company's price beside another company's holding, which is worse than no match at all.

So this resolver only ever accepts a match it can justify:

  symbol        the name *is* a trading symbol — "HFCL", "IGL", "M&M"
  name          the normalised name equals exactly one listed company's normalised name
  name-prefix   it is a prefix of exactly one listed company — "Sterlite Tech" -> STLTECH
  slug          the source's own URL slug equals or prefixes exactly one listed company

Anything ambiguous returns ``None`` and the caller shows the row without a symbol. There is no
edit-distance fallback and no hand-maintained alias table, because both quietly invent matches.

Normalisation drops "Limited"/"Ltd"/"The" and treats "&" and "and" as noise, which is what makes
"M&M" meet "Mahindra & Mahindra" and "J&K Bank" meet "J&KBANK". On the live lists this resolves
all 200 names ET publishes.

The list itself comes from NSE's own EQUITY_L.csv rather than the broker, so name resolution
works before a broker session exists.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from typing import Optional

from core.logger import get_logger
from research.institutional_flow.sources import UA, now_ist

logger = get_logger("research.institutional_flow.symbols")

EQUITY_LIST_CSV = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
LIST_TTL_S = 12 * 3600.0

# Tokens that carry no identity. "AND" is here because "&" expands to it, and the two spellings
# have to meet in the middle for M&M and J&K Bank to resolve at all.
_NOISE = {"LIMITED", "LTD", "THE", "AND"}
_KEEP_RE = re.compile(r"[^A-Z0-9 ]")

# British and American spellings of the same word. NSE writes "Fertilizers", the press writes
# "Fertilisers", and they are the same company. Applying the same rewrite to both sides can only
# ever merge two spellings of one name; if it ever did collide two genuinely different names, the
# ambiguity check below refuses the match rather than picking one.
_SPELLING = (("ISATION", "IZATION"), ("ISER", "IZER"), ("ISING", "IZING"),
             ("ISED", "IZED"), ("ISE", "IZE"), ("YSE", "YZE"))

_lock = threading.Lock()
_index: dict = {}


def normalise(text: str) -> str:
    """A company name or symbol reduced to its identifying letters and digits."""
    s = str(text or "").upper().replace("&", " AND ")
    s = _KEEP_RE.sub(" ", s)
    s = "".join(t for t in s.split() if t and t not in _NOISE)
    for a, b in _SPELLING:
        s = s.replace(a, b)
    return s


def _build() -> dict:
    """Fetch NSE's listed-equity CSV and index it by symbol and by normalised name."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get("https://www.nseindia.com", timeout=6)      # the archive wants a seeded session
    except Exception:
        pass
    r = s.get(EQUITY_LIST_CSV, timeout=25)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    by_symbol: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    companies: dict[str, str] = {}
    for row in rows:
        sym = (row.get("SYMBOL") or "").strip()
        if not sym:
            continue
        name = (row.get("NAME OF COMPANY") or "").strip()
        by_symbol[sym.upper()] = sym
        by_symbol.setdefault(normalise(sym), sym)
        by_name.setdefault(normalise(name), []).append(sym)
        companies[sym] = name
    if not by_symbol:
        raise ValueError("the equity list came back empty")
    return {"by_symbol": by_symbol, "by_name": by_name, "companies": companies,
            "count": len(by_symbol), "source": "NSE — list of equities available for trading",
            "source_url": EQUITY_LIST_CSV,
            "retrieved_at": now_ist().isoformat(timespec="seconds"), "_at": time.time()}


def index(force: bool = False) -> dict:
    """The day-cached name index. A failed refresh keeps serving the previous one."""
    with _lock:
        cur = _index.get("eq")
        if cur and not force and time.time() - cur["_at"] < LIST_TTL_S:
            return cur
    try:
        built = _build()
    except Exception as exc:
        logger.debug("equity list fetch failed: %s", exc)
        with _lock:
            cur = _index.get("eq")
        return cur or {"by_symbol": {}, "by_name": {}, "companies": {}, "count": 0,
                       "source": "NSE — list of equities available for trading",
                       "source_url": EQUITY_LIST_CSV, "error": str(exc)[:160], "_at": time.time()}
    with _lock:
        _index["eq"] = built
    return built


def _unique_prefix(by_name: dict, key: str) -> Optional[str]:
    """The one company whose normalised name starts with ``key`` — or nothing, if several do."""
    if len(key) < 4:            # too short to be distinctive; "ADANI" must not pick a winner
        return None
    found: set[str] = set()
    for k, syms in by_name.items():
        if k.startswith(key):
            found.update(syms)
            if len(found) > 1:
                return None
    return found.pop() if len(found) == 1 else None


def resolve(name: str, slug: Optional[str] = None) -> dict:
    """Resolve one published company name. Always returns a verdict, never a guess.

    ``symbol`` is None when nothing matched unambiguously, and ``via`` says which rule fired so
    a surprising pairing can be traced back to the reason it was accepted.
    """
    idx = index()
    by_symbol, by_name = idx.get("by_symbol") or {}, idx.get("by_name") or {}
    out = {"symbol": None, "via": "unresolved", "company": None}
    if not by_symbol:
        return {**out, "via": "no index"}

    def done(sym: str, via: str) -> dict:
        return {"symbol": sym, "via": via, "company": (idx.get("companies") or {}).get(sym)}

    n = normalise(name)
    if not n:
        return {**out, "via": "no name"}
    if n in by_symbol:
        return done(by_symbol[n], "symbol")
    exact = by_name.get(n)
    if exact and len(exact) == 1:
        return done(exact[0], "name")
    pre = _unique_prefix(by_name, n)
    if pre:
        return done(pre, "name-prefix")
    if slug:
        sn = normalise(str(slug).replace("-", " "))
        if sn:
            exact = by_name.get(sn)
            if exact and len(exact) == 1:
                return done(exact[0], "slug")
            pre = _unique_prefix(by_name, sn)
            if pre:
                return done(pre, "slug-prefix")
            if sn in by_symbol:
                return done(by_symbol[sn], "slug-symbol")
    return out


def resolve_many(pairs: list[tuple]) -> dict:
    """Resolve a batch of (name, slug) pairs, keyed by the name as published."""
    return {str(name): resolve(name, slug) for name, slug in pairs}
