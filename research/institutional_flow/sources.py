"""
Where the institutional numbers come from, and what each source can honestly tell us.

NSE publishes ONE thing in this area: the end-of-day aggregate cash-market turnover of
FII/FPIs and DIIs, as buy, sell and net in ₹ crore. That is the whole of it. There is no
public intraday series, and no daily stock-level attribution of who bought what.

So this module fetches that one number well — with the anti-bot cookie seeding NSE requires,
a cache that refuses to hammer the endpoint, and a last-known-good fallback that is always
labelled stale rather than passed off as fresh — and everything else it shows is computed from
market data the app already has.

What it will NOT do is turn one daily aggregate into twelve hourly rows, or read a quarterly
shareholding change as "FII bought today". Those are the two ways this screen could lie, and
both are refused in code rather than in a comment.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from core.logger import get_logger

logger = get_logger("research.institutional_flow.sources")

IST = timezone(timedelta(hours=5, minutes=30))

NSE_FII_DII = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_SEED = ("https://www.nseindia.com", "https://www.nseindia.com/reports/fii-dii")
NIFTY50_CSV = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# NSE refreshes this once, after the close. Polling it every few seconds would be rude and
# pointless, so a successful read is held for a while and a failure is retried sooner.
FRESH_TTL_S = 300.0
FAIL_RETRY_S = 60.0
UNIVERSE_TTL_S = 12 * 3600.0

_lock = threading.Lock()
_cache: dict = {}            # the last FII/DII read, good or bad
_universe: dict = {}         # the NIFTY 50 list, cached for the day


def now_ist() -> datetime:
    """Naive IST wall clock — the only clock an Indian market screen should use."""
    return datetime.now(IST).replace(tzinfo=None)


def _session():
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/reports/fii-dii",
        "Connection": "keep-alive",
    })
    # NSE hands out the cookies its API checks only to something that looks like a browser
    # arriving from its own pages, so visit those first and ignore whatever they return.
    for url in NSE_SEED:
        try:
            s.get(url, timeout=6)
        except Exception:
            pass
    return s


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _as_date(v) -> Optional[date]:
    """NSE dates arrive as '06-Oct-2026'."""
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def fetch_fii_dii_daily(force: bool = False) -> dict:
    """The one real institutional number NSE publishes: end-of-day aggregate cash flow.

    Returns a record that always says where it came from, when it was read, and whether it is
    fresh or a cached last-known-good. It never returns zeros to stand in for a failed fetch —
    a missing number comes back as ``available: False`` so the screen can say so.
    """
    with _lock:
        c = _cache.get("fii_dii")
        if c and not force:
            age = time.time() - c["_at"]
            if (c.get("available") and age < FRESH_TTL_S) or (not c.get("available") and age < FAIL_RETRY_S):
                return {**c, "cached": True, "age_seconds": round(age, 1)}

    rows, err = [], None
    try:
        s = _session()
        r = s.get(NSE_FII_DII, timeout=10)
        if r.status_code == 200:
            rows = r.json() or []
        else:
            err = f"NSE returned HTTP {r.status_code}"
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:120]}"
        logger.debug("FII/DII fetch failed: %s", err)

    fii = dii = None
    data_date = None
    for row in rows:
        cat = str(row.get("category") or "").upper()
        rec = {"buy": _num(row.get("buyValue")), "sell": _num(row.get("sellValue")),
               "net": _num(row.get("netValue"))}
        data_date = _as_date(row.get("date")) or data_date
        if "FII" in cat or "FPI" in cat:
            fii = rec
        elif "DII" in cat:
            dii = rec

    if fii or dii:
        out = {
            "available": True, "fii": fii, "dii": dii,
            "data_date": data_date.isoformat() if data_date else None,
            "data_type": "aggregate_cash_flow",
            "source": "NSE — FII/DII trading activity (cash market)",
            "source_url": NSE_FII_DII,
            "is_final": True,          # NSE publishes this after the close, already settled
            "retrieved_at": now_ist().isoformat(timespec="seconds"),
            "stale": False, "error": None, "_at": time.time(),
        }
        with _lock:
            _cache["fii_dii"] = out
        return {**out, "cached": False, "age_seconds": 0.0}

    # Failed. Hand back the last good read if we have one, clearly marked — never silently,
    # and never as zeros.
    with _lock:
        prev = _cache.get("fii_dii")
    if prev and prev.get("available"):
        return {**prev, "stale": True, "cached": True, "error": err,
                "age_seconds": round(time.time() - prev["_at"], 1)}
    miss = {"available": False, "fii": None, "dii": None, "data_date": None,
            "data_type": "aggregate_cash_flow",
            "source": "NSE — FII/DII trading activity (cash market)",
            "source_url": NSE_FII_DII, "is_final": False,
            "retrieved_at": now_ist().isoformat(timespec="seconds"),
            "stale": False, "error": err or "no rows returned", "_at": time.time()}
    with _lock:
        _cache["fii_dii"] = miss
    return {**miss, "cached": False, "age_seconds": 0.0}


def fetch_intraday_cash_flow() -> dict:
    """There is no public intraday FII/DII series, and this says so rather than inventing one.

    Slicing the daily aggregate into hourly buckets would produce a chart that looks like
    information and contains none. The screen shows this message instead.
    """
    return {
        "available": False,
        "data_type": "intraday_cash_flow",
        "rows": [],
        "message": ("Intraday institutional transaction values are not available from the "
                    "current source. NSE publishes FII/DII cash-market activity once, after "
                    "the close."),
        "source": "NSE — FII/DII trading activity (cash market)",
        "retrieved_at": now_ist().isoformat(timespec="seconds"),
    }


def fetch_stock_activity(institution: str) -> dict:
    """Daily stock-level FII/DII attribution does not exist publicly, and is not guessed at.

    What NSE and the exchanges publish per stock is *shareholding*, quarterly — a different
    data type on a different clock. Reading a quarterly shareholding change as a day's buying
    is the single most common way this screen could mislead, so it is refused outright.
    """
    who = "FII" if str(institution).upper().startswith("F") else "DII"
    return {
        "available": False,
        "institution": who,
        "data_type": f"{who.lower()}_shareholding_change",
        "rows": [],
        "message": (f"Stock-level daily {who} transaction data is not available from the "
                    "current source. Exchange filings publish shareholding quarterly, which "
                    "is a different measure and cannot be read as a day's buying or selling."),
        "retrieved_at": now_ist().isoformat(timespec="seconds"),
    }


def nifty50_symbols(force: bool = False) -> dict:
    """The current NIFTY 50 constituents, read from NSE rather than hard-coded.

    An index's membership changes; a list pasted into source goes quietly wrong.
    """
    with _lock:
        c = _universe.get("nifty50")
        if c and not force and time.time() - c["_at"] < UNIVERSE_TTL_S:
            return {**c, "cached": True}
    try:
        import csv
        import io as _io
        s = _session()
        r = s.get(NIFTY50_CSV, timeout=15)
        r.raise_for_status()
        rows = list(csv.DictReader(_io.StringIO(r.text)))
        syms = [{"symbol": (x.get("Symbol") or "").strip(),
                 "company": (x.get("Company Name") or "").strip(),
                 "industry": (x.get("Industry") or "").strip()}
                for x in rows if (x.get("Symbol") or "").strip()]
        out = {"available": True, "symbols": syms, "count": len(syms),
               "source": "NSE — NIFTY 50 constituent list", "source_url": NIFTY50_CSV,
               "retrieved_at": now_ist().isoformat(timespec="seconds"),
               "error": None, "_at": time.time()}
        with _lock:
            _universe["nifty50"] = out
        return {**out, "cached": False}
    except Exception as exc:
        logger.debug("NIFTY 50 list fetch failed: %s", exc)
        with _lock:
            prev = _universe.get("nifty50")
        if prev and prev.get("available"):
            return {**prev, "stale": True, "cached": True, "error": str(exc)[:120]}
        return {"available": False, "symbols": [], "count": 0,
                "source": "NSE — NIFTY 50 constituent list", "source_url": NIFTY50_CSV,
                "retrieved_at": now_ist().isoformat(timespec="seconds"),
                "error": str(exc)[:160]}
