"""
Company fundamentals and the sector tag for My Equity Workspace.

Zerodha's API is a trading API: it carries prices, not balance sheets, and its instrument dump
has no sector. So the fundamental side of the X-ray comes from Yahoo Finance's public endpoints
(``SYMBOL.NS`` / ``SYMBOL.BO``), which need a cookie and a crumb but no account and no key.

Everything degrades gracefully. If Yahoo is unreachable — or blocks the server — the X-ray
simply shows "not available" and the sector stays editable by hand; nothing else breaks. Values
are cached in memory and on disk beside the daily candles (fundamentals change once a quarter),
so a page load never waits on the network twice for the same stock.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from research.my_equity.cache import ROOT as CACHE_ROOT

logger = get_logger("research.my_equity.fundamentals")

TTL_S = 12 * 3600
TIMEOUT_S = 8
SUFFIX = {"NSE": ".NS", "BSE": ".BO"}
MODULES = ("assetProfile,defaultKeyStatistics,financialData,summaryDetail,price,"
           "calendarEvents,recommendationTrend")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9"}

_mem: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
_session = None
_crumb: Optional[str] = None
_crumb_at = 0.0


def _store_path() -> Path:
    return CACHE_ROOT / "fundamentals.json"


def _disk() -> dict:
    try:
        return json.loads(_store_path().read_text())
    except Exception:
        return {}


def _save_disk(key: str, payload: dict) -> None:
    try:
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        data = _disk()
        data[key] = {"at": time.time(), "payload": payload}
        _store_path().write_text(json.dumps(data)[:4_000_000])
    except Exception as exc:
        logger.debug("fundamentals cache write failed: %s", exc)


def _client():
    """A requests session carrying Yahoo's cookie, plus a crumb refreshed hourly."""
    global _session, _crumb, _crumb_at
    import requests
    if _session is None:
        _session = requests.Session()
        _session.headers.update(_HEADERS)
    if _crumb and time.time() - _crumb_at < 3600:
        return _session, _crumb
    try:
        _session.get("https://fc.yahoo.com", timeout=TIMEOUT_S)
    except Exception:
        pass                                    # the call still sets the cookie often enough
    try:
        r = _session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=TIMEOUT_S)
        if r.status_code == 200 and r.text.strip():
            _crumb, _crumb_at = r.text.strip(), time.time()
    except Exception as exc:
        logger.debug("yahoo crumb failed: %s", exc)
    return _session, _crumb


def _raw(v):
    """Yahoo wraps numbers as {"raw": …, "fmt": …}; unwrap to a plain number."""
    if isinstance(v, dict):
        v = v.get("raw")
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _date(v) -> Optional[str]:
    ts = _raw(v)
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _shape(res: dict) -> dict:
    """Pick the fields worth showing, in the units an Indian trader expects."""
    prof = res.get("assetProfile") or {}
    ks = res.get("defaultKeyStatistics") or {}
    fin = res.get("financialData") or {}
    sd = res.get("summaryDetail") or {}
    cal = res.get("calendarEvents") or {}
    price = res.get("price") or {}
    earnings_dates = (cal.get("earnings") or {}).get("earningsDate") or []
    return {
        "available": True,
        "name": price.get("longName") or price.get("shortName"),
        "sector": prof.get("sector"), "industry": prof.get("industry"),
        "employees": prof.get("fullTimeEmployees"), "website": prof.get("website"),
        "summary": (prof.get("longBusinessSummary") or "")[:1200] or None,
        "market_cap": _raw(price.get("marketCap")) or _raw(sd.get("marketCap")),
        "pe": _raw(sd.get("trailingPE")), "forward_pe": _raw(sd.get("forwardPE")),
        "eps": _raw(ks.get("trailingEps")), "forward_eps": _raw(ks.get("forwardEps")),
        "peg": _raw(ks.get("pegRatio")),
        "book_value": _raw(ks.get("bookValue")), "pb": _raw(ks.get("priceToBook")),
        "revenue": _raw(fin.get("totalRevenue")), "revenue_growth": _pctify(fin.get("revenueGrowth")),
        "earnings_growth": _pctify(fin.get("earningsGrowth")),
        "gross_margin": _pctify(fin.get("grossMargins")),
        "operating_margin": _pctify(fin.get("operatingMargins")),
        "profit_margin": _pctify(fin.get("profitMargins")),
        "roe": _pctify(fin.get("returnOnEquity")), "roa": _pctify(fin.get("returnOnAssets")),
        "debt_to_equity": _raw(fin.get("debtToEquity")),
        "current_ratio": _raw(fin.get("currentRatio")),
        "total_cash": _raw(fin.get("totalCash")), "total_debt": _raw(fin.get("totalDebt")),
        "free_cashflow": _raw(fin.get("freeCashflow")),
        "dividend_yield": _pctify(sd.get("dividendYield"), already_pct=True),
        "payout_ratio": _pctify(sd.get("payoutRatio")),
        "beta": _raw(sd.get("beta")) or _raw(ks.get("beta")),
        "target_mean": _raw(fin.get("targetMeanPrice")),
        "target_high": _raw(fin.get("targetHighPrice")), "target_low": _raw(fin.get("targetLowPrice")),
        "recommendation": fin.get("recommendationKey"),
        "analysts": _raw(fin.get("numberOfAnalystOpinions")),
        "held_insiders": _pctify(ks.get("heldPercentInsiders")),
        "held_institutions": _pctify(ks.get("heldPercentInstitutions")),
        "shares_out": _raw(ks.get("sharesOutstanding")),
        "float_shares": _raw(ks.get("floatShares")),
        "short_ratio": _raw(ks.get("shortRatio")),
        "earnings_date": _date(earnings_dates[0]) if earnings_dates else None,
        "ex_dividend_date": _date(cal.get("exDividendDate")),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Yahoo Finance",
    }


def _pctify(v, already_pct: bool = False) -> Optional[float]:
    r = _raw(v)
    if r is None:
        return None
    # Yahoo returns fractions (0.066 = 6.6%); dividendYield is sometimes already a percent
    return round(r if already_pct and abs(r) > 1 else r * 100, 2)


def fetch(symbol: str, exchange: str = "NSE", force: bool = False) -> dict:
    """Fundamentals for one stock. Never raises; returns ``available: False`` when unknown."""
    symbol, exchange = symbol.upper(), (exchange or "NSE").upper()
    key = f"{exchange}:{symbol}"
    now = time.time()
    with _lock:
        hit = _mem.get(key)
    if hit and not force and now - hit[0] < TTL_S:
        return {**hit[1], "cached": True}
    if not force:
        row = _disk().get(key)
        if row and now - row.get("at", 0) < TTL_S:
            with _lock:
                _mem[key] = (row["at"], row["payload"])
            return {**row["payload"], "cached": True}

    out = {"available": False, "message": "fundamentals not available for this stock"}
    try:
        session, crumb = _client()
        if crumb:
            url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}{SUFFIX.get(exchange, '.NS')}"
                   f"?modules={MODULES}&crumb={crumb}")
            r = session.get(url, timeout=TIMEOUT_S)
            if r.status_code == 200:
                results = (r.json().get("quoteSummary") or {}).get("result") or []
                if results:
                    out = _shape(results[0])
            else:
                out["message"] = f"fundamentals source returned {r.status_code}"
    except Exception as exc:
        logger.debug("fundamentals for %s failed: %s", symbol, exc)
        out["message"] = "fundamentals source unreachable"
    with _lock:
        _mem[key] = (now, out)
    if out.get("available"):
        _save_disk(key, out)
    return out


def sector_of(symbol: str, exchange: str = "NSE") -> tuple[Optional[str], Optional[str]]:
    """Just the sector and industry — used to fill the column when a stock is added."""
    data = fetch(symbol, exchange)
    return data.get("sector"), data.get("industry")
