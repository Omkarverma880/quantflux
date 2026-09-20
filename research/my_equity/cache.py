"""
Daily-candle cache for My Equity Workspace.

Lifetime high/low, 52-week levels and the daily indicators all need years of history, and
Zerodha serves at most 2000 days per request — far too slow to repeat on every page load.
So each symbol's daily bars are kept in one small Parquet file and extended incrementally:
the first load walks back to the listing date, every later load asks only for the sessions
since the last one.

The cache lives beside the Market Store (``/data`` on the Railway volume), so it survives
deploys. Losing it costs nothing but a slower first refresh.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.my_equity.cache")

ROOT = Path(os.environ.get("EQUITY_CACHE_DIR") or (MS.ROOT.parent / "equity_daily"))
CHUNK_DAYS = 2000                 # Kite's maximum span for one `day` request
FIRST_DATE = date(2000, 1, 1)     # Kite starts far later; it simply returns what it has
CALL_SPACING_S = 0.35             # ~3 historical calls per second
COLS = ["date", "open", "high", "low", "close", "volume"]

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_today: dict[str, tuple[float, pd.DataFrame]] = {}    # today's forming bar, kept out of the file
# The live price comes from the quote, so today's candle only feeds the daily indicators —
# three minutes is fresh enough and keeps an auto-refreshing table down to one quote call.
TODAY_TTL_S = 180


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _path(symbol: str, exchange: str) -> Path:
    return ROOT / f"{exchange.upper()}_{symbol.upper()}.parquet"


def _read(symbol: str, exchange: str) -> pd.DataFrame:
    p = _path(symbol, exchange)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    try:
        return pd.read_parquet(p)
    except Exception as exc:                        # a truncated file must not break the page
        logger.warning("daily cache unreadable for %s (%s) — refetching", symbol, exc)
        return pd.DataFrame(columns=COLS)


def _write(symbol: str, exchange: str, df: pd.DataFrame) -> None:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        tmp = _path(symbol, exchange).with_suffix(".tmp")
        df.to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, _path(symbol, exchange))
    except Exception as exc:
        logger.warning("could not save daily cache for %s: %s", symbol, exc)


def _normalise(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(rows)
    if "date" not in df.columns:
        return pd.DataFrame(columns=COLS)
    ts = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["date"] = ts.dt.tz_convert("Asia/Kolkata").dt.date
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0)
    return df[COLS]


def _fetch(broker, token: int, start: date, end: date) -> pd.DataFrame:
    out = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        t0 = time.monotonic()
        try:
            out.extend(broker.get_historical_data(token, cur, stop, "day") or [])
        except Exception as exc:
            logger.warning("daily fetch %s %s→%s failed: %s", token, cur, stop, exc)
        finally:
            time.sleep(max(0.0, CALL_SPACING_S - (time.monotonic() - t0)))
        cur = stop + timedelta(days=1)
    return _normalise(out)


def daily(broker, symbol: str, token: int, exchange: str = "NSE", *,
          today: Optional[date] = None, refresh: bool = True) -> pd.DataFrame:
    """Every daily bar Zerodha has for this stock, oldest first, today's included.

    Cheap to call repeatedly: only the sessions after the cached last bar are fetched. The
    file holds completed sessions only — today's bar is still forming, so it is kept in
    memory (60s) and never written, otherwise a half-day would be frozen into the history.
    """
    symbol, exchange = symbol.upper(), (exchange or "NSE").upper()
    key = f"{exchange}:{symbol}"
    today = today or date.today()
    with _lock_for(key):
        df = _read(symbol, exchange)
        if not refresh:
            return df
        live = _today.get(key)
        if live and time.time() - live[0] < TODAY_TTL_S:
            return _with_today(df, live[1])
        last = pd.Timestamp(df["date"].max()).date() if not df.empty else None
        start = (last + timedelta(days=1)) if last else FIRST_DATE
        fresh = _fetch(broker, token, start, today) if start <= today else pd.DataFrame(columns=COLS)
        done = fresh[fresh["date"] < today] if not fresh.empty else fresh
        forming = fresh[fresh["date"] >= today] if not fresh.empty else fresh
        if not done.empty:
            df = (pd.concat([df, done], ignore_index=True)
                    .drop_duplicates("date", keep="last")
                    .sort_values("date", ignore_index=True))
            _write(symbol, exchange, df)
        _today[key] = (time.time(), forming)
        return _with_today(df, forming)


def _with_today(df: pd.DataFrame, forming: pd.DataFrame) -> pd.DataFrame:
    """Completed history plus the session in progress (if the market has traded today)."""
    if forming is None or forming.empty:
        return df
    return (pd.concat([df, forming], ignore_index=True)
              .drop_duplicates("date", keep="last")
              .sort_values("date", ignore_index=True))


def status(symbol: str, exchange: str = "NSE") -> dict:
    df = _read(symbol.upper(), (exchange or "NSE").upper())
    if df.empty:
        return {"cached": False, "sessions": 0}
    return {"cached": True, "sessions": int(len(df)),
            "first": str(df["date"].min()), "last": str(df["date"].max())}


def drop(symbol: str, exchange: str = "NSE") -> None:
    try:
        _path(symbol.upper(), (exchange or "NSE").upper()).unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("cache drop failed for %s: %s", symbol, exc)
