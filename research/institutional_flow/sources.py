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

import html
import re
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


# ─────────────────────────────────────────────────────────────────────────────
# Moneycontrol — the aggregate, and the only multi-session history available free
# ─────────────────────────────────────────────────────────────────────────────
#
# Two different pages, and they are not interchangeable:
#
#   marketstats/index.php   the provisional NSE+BSE combined figures for the latest session,
#                           with gross purchase and gross sales. The same three numbers NSE
#                           publishes, so it cross-checks NSE and stands in when NSE blocks us.
#
#   markets/fii-dii-data/   a Next.js page carrying ~30 sessions as JSON: cash *net* for FII and
#                           DII, FII's four derivative books, and the index close for each day.
#                           No gross buy/sell — so history rows carry a net and say nothing about
#                           gross rather than inventing it.

MC_MARKETSTATS = "https://www.moneycontrol.com/stocks/marketstats/index.php"
MC_FII_DII = "https://www.moneycontrol.com/markets/fii-dii-data/"

ET_BASE = "https://economictimes.indiatimes.com/stocks/marketstats-technicals/"
ET_BOUGHT_BY_FII = ET_BASE + "bought-by-fii"
ET_SOLD_BY_FII = ET_BASE + "sold-by-fii"
ET_BOUGHT_BY_MF = ET_BASE + "bought-by-mf"
ET_SOLD_BY_MF = ET_BASE + "sold-by-mf"

# The same page shape is published for two institutions and no more. ET serves a bought-by-dii
# URL, but it comes back with no table in it — there is no DII stock list to read, which is why
# the DII panel explains itself instead of showing one.
ET_HOLDERS = {
    "FII": {"label": "FII", "column": "fii holding %",
            "bought": ET_BOUGHT_BY_FII, "sold": ET_SOLD_BY_FII},
    "MF": {"label": "mutual fund", "column": "mf holding %",
           "bought": ET_BOUGHT_BY_MF, "sold": ET_SOLD_BY_MF},
}

HISTORY_TTL_S = 900.0
ET_TTL_S = 1800.0

_SPACE_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_ET_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)


def _plain(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace collapsed."""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _row_cells(tr: str) -> list[str]:
    return [_plain(c) for c in _CELL_RE.findall(tr)]


def _table_rows(table_html: str) -> list[list[str]]:
    rows = [_row_cells(tr) for tr in _TR_RE.findall(table_html)]
    return [r for r in rows if any(x for x in r)]


def _web_session(referer: Optional[str] = None):
    """A plain browser-looking session. Moneycontrol and ET serve HTML, not an API."""
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })
    if referer:
        s.headers["Referer"] = referer
    return s


def _pct(v) -> Optional[float]:
    """'9.60%' -> 9.6 ; '-' -> None. A dash means not reported, which is not zero."""
    s = str(v or "").replace("%", "").replace(",", "").strip()
    if not s or s in {"-", "--", "NA", "N.A.", "nan", "None"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_html(url: str, referer: Optional[str] = None, timeout: int = 25) -> str:
    """GET a page, and name a refusal for what it is.

    A site that blocks us returns a small 403 page, which any parser then reports as "the data
    was not found" — sending whoever reads the error off to debug a parser that is working fine.
    The status is checked first so the message says "refused us", because the fix for a block is
    a different fix entirely.
    """
    r = _web_session(referer).get(url, timeout=timeout)
    if r.status_code == 403:
        raise ValueError("the source refused this request (HTTP 403) — this host appears to be "
                         "blocked, which is not something a retry will fix")
    if r.status_code == 429:
        raise ValueError("the source is rate-limiting this host (HTTP 429)")
    if r.status_code != 200:
        raise ValueError("the source returned HTTP %s" % r.status_code)
    if len(r.text) < 2000:
        raise ValueError("the source returned a %d-byte page, which is not the real one"
                         % len(r.text))
    return r.text


def _mc_block(page: str, div_id: str) -> Optional[dict]:
    """The one-row figures table inside a named Moneycontrol div.

    Keyed on the div id — ``instprovfii`` / ``instprovdii`` — rather than on table position,
    because the page carries four identically shaped tables and two of them are the lagged
    SEBI series, which must not be mistaken for the provisional one.
    """
    at = page.find("id='%s'" % div_id)
    if at < 0:
        at = page.find('id="%s"' % div_id)
    if at < 0:
        return None
    m = _TABLE_RE.search(page, at)
    if not m:
        return None
    rows = _table_rows(m.group(1))
    if len(rows) < 2 or "Gross Purchase" not in " ".join(rows[0]):
        return None
    head, data = rows[0], rows[1]
    rec: dict = {"buy": None, "sell": None, "net": None, "data_date": None}
    for label, value in zip(head, data):
        low = label.lower()
        if low == "date":
            d = _as_date(value)
            rec["data_date"] = d.isoformat() if d else None
        elif "purchase" in low and "net" not in low:
            rec["buy"] = _num(value)
        elif "sales" in low and "net" not in low:
            rec["sell"] = _num(value)
        elif "net" in low:
            rec["net"] = _num(value)
    if rec["net"] is None and rec["buy"] is not None and rec["sell"] is not None:
        rec["net"] = round(rec["buy"] - rec["sell"], 2)
    return rec if (rec["buy"] is not None or rec["net"] is not None) else None


def fetch_fii_dii_moneycontrol(force: bool = False, timeout: int = 20) -> dict:
    """Provisional NSE+BSE combined FII/DII cash activity for the latest session.

    Gross purchase, gross sales and net, in Rs crore — the same three numbers NSE publishes.
    """
    with _lock:
        c = _cache.get("mc_agg")
        if c and not force:
            age = time.time() - c["_at"]
            if (c.get("available") and age < FRESH_TTL_S) or (not c.get("available") and age < FAIL_RETRY_S):
                return {**c, "cached": True, "age_seconds": round(age, 1)}

    base = {"data_type": "aggregate_cash_flow",
            "source": "Moneycontrol — institutional trading activity (provisional, NSE+BSE)",
            "source_url": MC_MARKETSTATS}
    try:
        page = _fetch_html(MC_MARKETSTATS, timeout=timeout)
        fii = _mc_block(page, "instprovfii")
        dii = _mc_block(page, "instprovdii")
        if not fii and not dii:
            raise ValueError("the provisional FII/DII blocks were not found on the page")
        data_date = (fii or {}).get("data_date") or (dii or {}).get("data_date")
        out = {
            **base, "available": True,
            "fii": {k: v for k, v in (fii or {}).items() if k != "data_date"} if fii else None,
            "dii": {k: v for k, v in (dii or {}).items() if k != "data_date"} if dii else None,
            "data_date": data_date,
            "is_final": False,            # provisional, by the page's own label
            "retrieved_at": now_ist().isoformat(timespec="seconds"),
            "stale": False, "error": None, "_at": time.time(),
        }
    except Exception as exc:
        logger.debug("Moneycontrol aggregate failed: %s", exc)
        with _lock:
            prev = _cache.get("mc_agg")
        if prev and prev.get("available"):
            return {**prev, "stale": True, "cached": True, "error": str(exc)[:160],
                    "age_seconds": round(time.time() - prev["_at"], 1)}
        out = {**base, "available": False, "fii": None, "dii": None, "data_date": None,
               "is_final": False, "retrieved_at": now_ist().isoformat(timespec="seconds"),
               "stale": False, "error": str(exc)[:160], "_at": time.time()}
    with _lock:
        _cache["mc_agg"] = out
    return {**out, "cached": False, "age_seconds": 0.0}


def fetch_fii_dii_history(force: bool = False) -> dict:
    """~30 sessions of FII/DII cash net, FII's derivative books, and the index close per day.

    This is what makes a by-date table possible on a fresh install: without it the screen can
    only ever show the sessions the app happened to be running for.

    Cash *gross* is not on this page, so buy and sell are absent from these rows and the table
    fills them only for a session we read gross figures for ourselves.
    """
    with _lock:
        c = _cache.get("mc_hist")
        if c and not force:
            age = time.time() - c["_at"]
            if (c.get("available") and age < HISTORY_TTL_S) or (not c.get("available") and age < FAIL_RETRY_S):
                return {**c, "cached": True, "age_seconds": round(age, 1)}

    base = {"data_type": "aggregate_cash_flow",
            "source": "Moneycontrol — FII/DII activity history",
            "source_url": MC_FII_DII}
    try:
        import json as _json
        page = _fetch_html(MC_FII_DII, timeout=25)
        m = _NEXT_DATA_RE.search(page)
        if not m:
            raise ValueError("the page's data block was not found")
        blob = _json.loads(m.group(1))
        raw = (((blob.get("props") or {}).get("pageProps") or {})
               .get("FiiDiiData") or {}).get("fiiDiiData") or []
        if not raw:
            raise ValueError("the history series was empty")
        rows = []
        for r in raw:
            d = _as_date(r.get("date"))
            if not d:
                continue
            rows.append({
                "trading_date": d.isoformat(),
                "label": (r.get("fDate") or "").strip() or None,
                "fii_net": _num(r.get("fiiCM")),
                "dii_net": _num(r.get("diiCM")),
                "fii_index_futures": _num(r.get("fiiIdxFut")),
                "fii_index_options": _num(r.get("fiiIdxOpt")),
                "fii_stock_futures": _num(r.get("fiiStkFut")),
                "fii_stock_options": _num(r.get("fiiStkOpt")),
                "nifty_close": _num(r.get("niftyClose")),
                "nifty_change_pct": _num(r.get("niftyChangePer")),
                "sensex_close": _num(r.get("sensexClose")),
                "sensex_change_pct": _num(r.get("sensexChangePer")),
            })
        rows.sort(key=lambda x: x["trading_date"], reverse=True)
        out = {**base, "available": True, "rows": rows, "count": len(rows),
               "covers": ({"from": rows[-1]["trading_date"], "to": rows[0]["trading_date"]}
                          if rows else None),
               "has_gross": False,
               "retrieved_at": now_ist().isoformat(timespec="seconds"),
               "stale": False, "error": None, "_at": time.time()}
    except Exception as exc:
        logger.debug("Moneycontrol history failed: %s", exc)
        with _lock:
            prev = _cache.get("mc_hist")
        if prev and prev.get("available"):
            return {**prev, "stale": True, "cached": True, "error": str(exc)[:160],
                    "age_seconds": round(time.time() - prev["_at"], 1)}
        out = {**base, "available": False, "rows": [], "count": 0, "covers": None,
               "has_gross": False, "retrieved_at": now_ist().isoformat(timespec="seconds"),
               "stale": False, "error": str(exc)[:160], "_at": time.time()}
    with _lock:
        _cache["mc_hist"] = out
    return {**out, "cached": False, "age_seconds": 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# Economic Times — which stocks FIIs raised or cut their holding in
# ─────────────────────────────────────────────────────────────────────────────
#
# Read what this page actually says before using it. The columns are FII Holding %, the four
# previous quarters, and the quarter-on-quarter and year-on-year change in that percentage.
# That is a SHAREHOLDING change, reported on a quarterly clock. It is not a day's buying, and
# a percentage of equity cannot be turned into rupees here — so nothing downstream is allowed
# to render these as "FII bought Rs X crore".
#
# The page splits each row across two tables: the first holds the pinned company column with
# its current price and absolute change, the second holds the percentage columns. They are
# emitted in the same order, one header row each, so they zip by index — and the parser
# refuses the pairing outright if the two row counts disagree, rather than silently sliding
# one company's name onto another company's holding.

_ET_PRICE_HEAD = "company name"


def _et_name_and_slug(tr: str) -> tuple[Optional[str], Optional[str]]:
    """The company name, and the URL slug ET uses for it — a stable-ish identity hint."""
    m = _ET_LINK_RE.search(tr)
    if not m:
        cells = _row_cells(tr)
        return ((cells[0] or None) if cells else None), None
    href, inner = m.group(1), _plain(m.group(2))
    slug = href.strip("/").split("/")[0] or None
    return (inner or None), slug


def fetch_et_holder_stocks(institution: str, side: str, force: bool = False) -> dict:
    """The ET list of stocks where an institution's shareholding rose or fell.

    ``institution`` is FII or MF — the only two ET publishes a populated table for. Returns one
    row per company with its name, ET's price and absolute change, and the shareholding columns.
    Every row is labelled with the data type so that no caller can mistake a holding change for
    a transaction value.
    """
    who = str(institution).upper()
    spec = ET_HOLDERS.get(who)
    if spec is None:
        raise ValueError("no published stock list exists for %s" % who)
    want = "sold" if str(side).lower().startswith("s") else "bought"
    url = spec[want]
    key = "et_%s_%s" % (who.lower(), want)
    direction = "decrease" if want == "sold" else "increase"
    kind = "%s_shareholding_change" % who.lower()

    with _lock:
        c = _cache.get(key)
        if c and not force:
            age = time.time() - c["_at"]
            if (c.get("available") and age < ET_TTL_S) or (not c.get("available") and age < FAIL_RETRY_S):
                return {**c, "cached": True, "age_seconds": round(age, 1)}

    base = {"institution": who, "side": want, "direction": direction, "data_type": kind,
            "source": "Economic Times — %s by %s (%s shareholding %s)"
                      % (want, who, spec["label"], direction),
            "source_url": url,
            "measure": ("Change in %s shareholding as a percentage of equity, reported "
                        "quarterly. Not a transaction value and not a day's buying."
                        % spec["label"])}
    try:
        page = _fetch_html(url, referer="https://economictimes.indiatimes.com/markets", timeout=30)
        tables = _TABLE_RE.findall(page)
        if len(tables) < 2:
            raise ValueError("expected two tables on the page, found %d" % len(tables))

        left_trs = [tr for tr in _TR_RE.findall(tables[0]) if _row_cells(tr)]
        right_rows = _table_rows(tables[1])
        if not left_trs or not right_rows:
            raise ValueError("the page's tables were empty")
        if _ET_PRICE_HEAD not in " ".join(_row_cells(left_trs[0])).lower():
            raise ValueError("the first table is not the company column any more")

        head = [h.strip() for h in right_rows[0]]
        left_data, right_data = left_trs[1:], right_rows[1:]
        if len(left_data) != len(right_data):
            raise ValueError("the two tables disagree on row count (%d vs %d), so rows cannot "
                             "be paired safely" % (len(left_data), len(right_data)))

        def col(values: list, *names: str) -> Optional[float]:
            for i, h in enumerate(head):
                hl = h.lower()
                if any(n in hl for n in names) and i < len(values):
                    return _pct(values[i])
            return None

        rows = []
        for tr, vals in zip(left_data, right_data):
            name, slug = _et_name_and_slug(tr)
            if not name:
                continue
            cells = _row_cells(tr)
            rows.append({
                "company": name, "slug": slug,
                "et_price": _num(cells[1]) if len(cells) > 1 else None,
                "et_price_change": _num(cells[2]) if len(cells) > 2 else None,
                "et_change_pct": col(vals, "% change"),
                # the current-quarter column precedes its "1Q"/"2Q" siblings in the header and
                # the first index match wins, so this lands on the latest reported quarter
                "holding_pct": col(vals, spec["column"]),
                "holding_qoq_change_pct": col(vals, "qoq"),
                "holding_yoy_change_pct": col(vals, "yoy"),
                "dii_holding_pct": col(vals, "dii holding"),
                "institution": who,
                "data_type": kind,
                "direction": direction,
            })
        if not rows:
            raise ValueError("no company rows could be read")
        out = {**base, "available": True, "rows": rows, "count": len(rows), "columns": head,
               "retrieved_at": now_ist().isoformat(timespec="seconds"),
               "stale": False, "error": None, "_at": time.time()}
    except Exception as exc:
        logger.debug("ET %s-by-%s failed: %s", want, who, exc)
        with _lock:
            prev = _cache.get(key)
        if prev and prev.get("available"):
            return {**prev, "stale": True, "cached": True, "error": str(exc)[:200],
                    "age_seconds": round(time.time() - prev["_at"], 1)}
        out = {**base, "available": False, "rows": [], "count": 0, "columns": [],
               "retrieved_at": now_ist().isoformat(timespec="seconds"),
               "stale": False, "error": str(exc)[:200], "_at": time.time()}
    with _lock:
        _cache[key] = out
    return {**out, "cached": False, "age_seconds": 0.0}


def fetch_et_fii_stocks(side: str, force: bool = False) -> dict:
    """The FII lists, with the holding columns named as this module's callers expect them."""
    out = fetch_et_holder_stocks("FII", side, force=force)
    rows = [{**r,
             "fii_holding_pct": r["holding_pct"],
             "fii_holding_qoq_change_pct": r["holding_qoq_change_pct"],
             "fii_holding_yoy_change_pct": r["holding_yoy_change_pct"]}
            for r in out.get("rows") or []]
    return {**out, "rows": rows}


# ─────────────────────────────────────────────────────────────────────────────
# NSE delivery data — the one daily, stock-level number that is actually published
# ─────────────────────────────────────────────────────────────────────────────
#
# Nobody publishes which stocks FIIs bought today. What NSE does publish, every session for
# every stock, is how much of the traded volume was taken to delivery rather than squared off
# intraday. A day where most of the volume went to delivery is a day somebody wanted the shares,
# not the move — which is the closest honest proxy there is for accumulation.
#
# It attributes nothing. It does not say who took delivery, and this module never pretends it
# does. What it supports is a different and answerable question: which stocks are being taken
# home at a rate unlike their own recent norm.
#
# Two traps this handles:
#   * on a holiday the archive serves the previous session's file under the holiday's name, so
#     the session is read from DATE1 inside the file and never from the URL;
#   * the file includes ETFs and liquid funds, which sit at the top of any delivery ranking and
#     mean nothing there. They are removed by intersecting with NSE's own equity list rather
#     than by a hand-kept list of symbols to ignore.

BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_%s.csv"

DELIVERY_TTL_S = 6 * 3600.0
DELIVERY_MAX_DAYS = 30

_delivery: dict = {}        # "DD-MON-YYYY" -> {symbol: row}
_delivery_miss: dict = {}   # filename key -> when we last failed, so a 404 is not re-asked


def _f(v) -> Optional[float]:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s and s not in {"-", "", "NA"} else None
    except (TypeError, ValueError):
        return None


def _parse_bhav(text: str) -> tuple[Optional[str], dict]:
    """One bhavcopy into {symbol: row}, plus the ISO session it actually belongs to.

    The session is normalised here rather than carried as NSE prints it: the archive is not
    consistent about case between files ("06-Oct-2026" in the bhavcopy, "06-OCT-2026" in the
    bulk-deal file), and a de-duplication that keys on the raw string would let a holiday's
    duplicate through on a casing difference alone.
    """
    import csv as _csv
    import io as _io
    rows = {}
    raw_session = None
    for raw in _csv.DictReader(_io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if r.get("SERIES") != "EQ":
            continue
        sym = r.get("SYMBOL")
        if not sym:
            continue
        raw_session = raw_session or r.get("DATE1")
        close, prev = _f(r.get("CLOSE_PRICE")), _f(r.get("PREV_CLOSE"))
        turn = _f(r.get("TURNOVER_LACS"))
        rows[sym] = {
            "symbol": sym,
            "close": close, "previous_close": prev,
            "open": _f(r.get("OPEN_PRICE")), "high": _f(r.get("HIGH_PRICE")),
            "low": _f(r.get("LOW_PRICE")), "vwap": _f(r.get("AVG_PRICE")),
            "change_pct": (round((close - prev) / prev * 100, 2)
                           if (close and prev) else None),
            "volume": _f(r.get("TTL_TRD_QNTY")),
            "turnover_cr": round(turn / 100.0, 2) if turn is not None else None,
            "trades": _f(r.get("NO_OF_TRADES")),
            "delivery_qty": _f(r.get("DELIV_QTY")),
            "delivery_pct": _f(r.get("DELIV_PER")),
        }
    parsed = _as_date(raw_session)
    return (parsed.isoformat() if parsed else None), rows


def fetch_delivery_day(d: date, force: bool = False) -> dict:
    """One session's delivery and price data, keyed on the date the file itself declares.

    A holiday is served as a copy of the previous session, so the returned ``session`` may not
    be the date asked for. Callers key on what comes back, never on what they asked for.
    """
    key = d.strftime("%d%m%Y")
    now = time.time()
    with _lock:
        if not force:
            miss = _delivery_miss.get(key)
            if miss and now - miss < FAIL_RETRY_S:
                return {"available": False, "asked_for": d.isoformat(), "session": None,
                        "rows": {}, "error": "not published"}
            for sess, cached in _delivery.items():
                if cached.get("_asked") == key and now - cached["_at"] < DELIVERY_TTL_S:
                    return {"available": True, "asked_for": d.isoformat(), "session": sess,
                            "rows": cached["rows"], "cached": True}
    try:
        r = _web_session("https://www.nseindia.com").get(BHAV_URL % key, timeout=30)
        if r.status_code != 200 or "SYMBOL" not in r.text[:400]:
            raise ValueError("HTTP %s" % r.status_code)
        session, rows = _parse_bhav(r.text)
        if not rows or not session:
            raise ValueError("no EQ rows in the file")
    except Exception as exc:
        logger.debug("bhavcopy %s unavailable: %s", key, exc)
        with _lock:
            _delivery_miss[key] = now
        return {"available": False, "asked_for": d.isoformat(), "session": None,
                "rows": {}, "error": str(exc)[:120]}
    with _lock:
        _delivery[session] = {"rows": rows, "_at": now, "_asked": key}
        # never let the cache grow without bound
        if len(_delivery) > DELIVERY_MAX_DAYS * 2:
            for old in sorted(_delivery, key=lambda k: _delivery[k]["_at"])[:10]:
                _delivery.pop(old, None)
    return {"available": True, "asked_for": d.isoformat(), "session": session,
            "rows": rows, "cached": False}


def fetch_delivery_window(end: date, sessions: int = 11,
                          is_trading_day=None, previous_trading_day=None) -> dict:
    """The most recent N published sessions, newest first and de-duplicated by real session.

    Walks back over calendar days asking for each file, because which days NSE publishes for is
    its own business — a holiday that serves a duplicate is detected by the session inside the
    file and dropped rather than counted twice.
    """
    want = max(1, min(int(sessions), DELIVERY_MAX_DAYS))
    out: dict = {}
    order: list[str] = []
    cursor = end
    tried = 0
    while len(order) < want and tried < want * 3 + 10:
        tried += 1
        if cursor.weekday() < 5 and (is_trading_day is None or is_trading_day(cursor)):
            got = fetch_delivery_day(cursor)
            if got.get("available"):
                sess = got["session"]
                if sess not in out:
                    out[sess] = got["rows"]
                    order.append(sess)
        cursor = cursor - timedelta(days=1)
    if not order:
        return {"available": False, "sessions": [], "by_session": {},
                "source": "NSE — securities bhavcopy with delivery",
                "error": "no published session could be read",
                "retrieved_at": now_ist().isoformat(timespec="seconds")}
    return {"available": True, "sessions": order, "by_session": out,
            "latest": order[0], "count": len(order),
            "source": "NSE — securities bhavcopy with delivery",
            "source_url": BHAV_URL % end.strftime("%d%m%Y"),
            "data_type": "stock_delivery",
            "retrieved_at": now_ist().isoformat(timespec="seconds"), "error": None}


# ─────────────────────────────────────────────────────────────────────────────
# Index closes, from NSE's own archive
# ─────────────────────────────────────────────────────────────────────────────
#
# The index reaction beside each session used to come from Moneycontrol, which means it vanished
# the moment Moneycontrol refused us — and it does refuse, with a 403, from hosts it does not
# like. NSE publishes the same closes in its own archive, so the column is sourced from there
# instead and survives losing Moneycontrol entirely.
#
# Moneycontrol is still the only free source for FII's four derivative books, so that view keeps
# depending on it and says so when it is unavailable.

INDEX_CLOSE_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_%s.csv"
INDEX_TTL_S = 6 * 3600.0

_index_closes: dict = {}      # iso date -> {index name -> row}
_index_miss: dict = {}

_WANTED_INDICES = {"NIFTY 50": "nifty", "NIFTY BANK": "banknifty", "NIFTY NEXT 50": "next50"}


def _index_date(v) -> Optional[date]:
    """The index archive stamps rows '06-10-2026' — day first, numeric month.

    Parsed here rather than by widening the shared date parser: every other source in this
    module writes an alphabetic month, and teaching the shared parser to accept a numeric
    day-month pair would make '01-02-2026' ambiguous for all of them.
    """
    for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def fetch_index_close_day(d: date, force: bool = False) -> dict:
    """One session's index closes. Like the bhavcopy, the session is read from the file."""
    key = d.strftime("%d%m%Y")
    now = time.time()
    with _lock:
        if not force:
            miss = _index_miss.get(key)
            if miss and now - miss < FAIL_RETRY_S:
                return {"available": False, "session": None, "indices": {}}
            for sess, c in _index_closes.items():
                if c.get("_asked") == key and now - c["_at"] < INDEX_TTL_S:
                    return {"available": True, "session": sess, "indices": c["indices"],
                            "cached": True}
    try:
        import csv as _csv
        import io as _io
        r = _web_session("https://www.nseindia.com").get(INDEX_CLOSE_URL % key, timeout=25)
        if r.status_code != 200 or "Index Name" not in r.text[:400]:
            raise ValueError("HTTP %s" % r.status_code)
        out, session = {}, None
        for raw in _csv.DictReader(_io.StringIO(r.text)):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            name = row.get("Index Name", "").strip().upper()
            if name not in _WANTED_INDICES:
                continue
            session = session or _index_date(row.get("Index Date"))
            out[_WANTED_INDICES[name]] = {
                "close": _num(row.get("Closing Index Value")),
                "open": _num(row.get("Open Index Value")),
                "high": _num(row.get("High Index Value")),
                "low": _num(row.get("Low Index Value")),
                "points_change": _num(row.get("Points Change")),
                # NSE writes these without a leading zero (".98%", "-.76%"), which float() reads
                "change_pct": _num(str(row.get("Change(%)") or "").replace("%", "")),
            }
        if not out or not session:
            raise ValueError("no index rows in the file")
        iso = session.isoformat()
    except Exception as exc:
        logger.debug("index close %s unavailable: %s", key, exc)
        with _lock:
            _index_miss[key] = now
        return {"available": False, "session": None, "indices": {}, "error": str(exc)[:120]}
    with _lock:
        _index_closes[iso] = {"indices": out, "_at": now, "_asked": key}
        if len(_index_closes) > 90:
            for old in sorted(_index_closes, key=lambda k: _index_closes[k]["_at"])[:20]:
                _index_closes.pop(old, None)
    return {"available": True, "session": iso, "indices": out, "cached": False}


def fetch_index_closes(end: date, sessions: int = 12, is_trading_day=None) -> dict:
    """The most recent N sessions of index closes, newest first, keyed by real session."""
    want = max(1, min(int(sessions), 60))
    by_date: dict = {}
    cursor, tried = end, 0
    while len(by_date) < want and tried < want * 3 + 10:
        tried += 1
        if cursor.weekday() < 5 and (is_trading_day is None or is_trading_day(cursor)):
            got = fetch_index_close_day(cursor)
            if got.get("available") and got["session"] not in by_date:
                by_date[got["session"]] = got["indices"]
        cursor = cursor - timedelta(days=1)
    return {"available": bool(by_date), "by_session": by_date,
            "sessions": sorted(by_date, reverse=True),
            "source": "NSE — daily index close archive",
            "source_url": INDEX_CLOSE_URL % end.strftime("%d%m%Y"),
            "retrieved_at": now_ist().isoformat(timespec="seconds"),
            "error": None if by_date else "no index close file could be read"}
