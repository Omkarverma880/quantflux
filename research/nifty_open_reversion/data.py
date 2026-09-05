"""
Data loading and validation for the NIFTY open-reversion backtest.

Two sources, one shape: a Zerodha-style 1-minute CSV, or a live pull from the
broker. Both come out as a tz-naive IST-indexed DataFrame of clean, de-duplicated,
in-session OHLC bars — because a backtest is only as honest as the bars it reads.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from core.logger import get_logger

logger = get_logger("research.nifty_open_reversion.data")

IST = "Asia/Kolkata"
SESSION_START = dtime(9, 15)
SESSION_END = dtime(15, 30)
REQUIRED = ["open", "high", "low", "close"]

# Kite serves at most this many days of minute data per request.
_MINUTE_CHUNK_DAYS = 60


def _to_ist_naive(series: pd.Series) -> pd.Series:
    """Parse timestamps to IST, then drop the tz so every later comparison is
    plain wall-clock IST. Handles tz-aware, tz-naive and mixed-offset input."""
    s = pd.to_datetime(series, errors="coerce", utc=True, format="mixed")
    return s.dt.tz_convert(IST).dt.tz_localize(None)


def clean_frame(df: pd.DataFrame, *, session_only: bool = True) -> pd.DataFrame:
    """Validate, normalise and sort. Returns a frame indexed by IST timestamp."""
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED)
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    ts_col = next((c for c in ("timestamp", "date", "datetime", "time") if c in df.columns), None)
    if ts_col is None:
        raise ValueError("No timestamp column found (expected 'timestamp' or 'date')")
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLC column(s): {', '.join(missing)}")

    df["_ts"] = _to_ist_naive(df[ts_col])
    df = df.dropna(subset=["_ts"])
    for c in REQUIRED:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    n0 = len(df)
    # ── validation: OHLC must be internally consistent and positive ──
    df = df.dropna(subset=REQUIRED)
    df = df[(df[REQUIRED] > 0).all(axis=1)]
    df = df[(df["high"] >= df["low"])
            & (df["high"] >= df["open"]) & (df["high"] >= df["close"])
            & (df["low"] <= df["open"]) & (df["low"] <= df["close"])]
    bad = n0 - len(df)

    df = df.sort_values("_ts")
    dupes = int(df.duplicated(subset=["_ts"]).sum())
    df = df.drop_duplicates(subset=["_ts"], keep="last")

    if session_only:
        t = df["_ts"].dt.time
        df = df[(t >= SESSION_START) & (t <= SESSION_END)]

    df = df.set_index("_ts")
    df.index.name = "timestamp"
    keep = [c for c in REQUIRED if c in df.columns]
    out = df[keep].astype(float)
    out.attrs["rows_dropped"] = int(bad)
    out.attrs["duplicates_removed"] = dupes
    logger.info("open-reversion data: %d bars, %d dropped, %d duplicates, %s → %s",
                len(out), bad, dupes,
                out.index[0] if len(out) else "-", out.index[-1] if len(out) else "-")
    return out


def load_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    df = pd.read_csv(p)
    return clean_frame(df)


def load_from_broker(broker, token: int, start: date, end: date,
                     interval: str = "minute") -> pd.DataFrame:
    """Pull 1-minute candles in broker-sized chunks and stitch them."""
    rows: list[dict] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=_MINUTE_CHUNK_DAYS - 1), end)
        frm = datetime.combine(cur, SESSION_START)
        to = min(datetime.combine(chunk_end, SESSION_END), datetime.now())
        try:
            rows.extend(broker.get_historical_data(token, frm, to, interval) or [])
        except Exception as exc:
            logger.warning("open-reversion history chunk failed (%s → %s): %s", cur, chunk_end, exc)
        cur = chunk_end + timedelta(days=1)
    if not rows:
        return pd.DataFrame(columns=REQUIRED)
    return clean_frame(pd.DataFrame(rows))


def slice_dates(df: pd.DataFrame, start: str = "", end: str = "") -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if start:
        out = out[out.index >= pd.Timestamp(start)]
    if end:
        out = out[out.index <= pd.Timestamp(end) + pd.Timedelta(days=1)]
    return out


def session_frames(df: pd.DataFrame):
    """Yield (date, day_frame) for every trading day, chronologically."""
    if df.empty:
        return
    for d, day in df.groupby(df.index.date, sort=True):
        yield d, day


def first_candle_at(day: pd.DataFrame, at: dtime = SESSION_START) -> Optional[pd.Series]:
    """The 09:15 bar itself — never 'the first row that happens to exist'.

    Falls back to the first bar of the session only when 09:15 is genuinely
    absent (a late open or a data gap), and the caller is told which it was.
    """
    if day.empty:
        return None
    exact = day[day.index.time == at]
    if len(exact):
        row = exact.iloc[0].copy()
        row["_exact_open"] = True
        return row
    row = day.iloc[0].copy()
    row["_exact_open"] = False
    return row
