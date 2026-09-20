"""
Flux Lab — data access and data honesty.

There is no second data store. Everything comes from the Market Store the rest of Quantflux
already uses (`kind=spot` for the index, India VIX and futures; `kind=options` for the chain),
read through its own predicate-pushdown reader so only the months in range are opened.

Before a backtest runs, the range is inspected and anything that could fake a result is reported
up front: missing sessions, short sessions, duplicate or out-of-order timestamps, invalid OHLC,
strikes that vanish mid-session, expiries with no data. Gaps are **never** filled with invented
prices — a contract that did not trade simply cannot be entered on that bar, and the engine
records the skip.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.flux_lab.data")

SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 29
FULL_SESSION_BARS = SESSION_CLOSE_MIN - SESSION_OPEN_MIN + 1      # 375 one-minute bars


def coverage(underlying: str = "NIFTY") -> dict:
    """What the store actually holds — the honest bounds for any backtest."""
    spot = MS.read("spot", underlying, columns=["timestamp"])
    opts = MS.read("options", underlying, columns=["timestamp", "expiry_date", "strike"])
    out = {"underlying": underlying, "spot": {}, "options": {}}
    if not spot.empty:
        ts = pd.to_datetime(spot["timestamp"])
        out["spot"] = {"first": str(ts.min().date()), "last": str(ts.max().date()),
                       "sessions": int(ts.dt.date.nunique()), "bars": int(len(spot))}
    if not opts.empty:
        ts = pd.to_datetime(opts["timestamp"])
        out["options"] = {"first": str(ts.min().date()), "last": str(ts.max().date()),
                          "sessions": int(ts.dt.date.nunique()), "rows": int(len(opts)),
                          "expiries": int(opts["expiry_date"].nunique()),
                          "strikes": int(opts["strike"].nunique())}
    out["vix"] = _series_range("INDIAVIX")
    out["futures"] = _series_range(f"{underlying}FUT")
    out["tradable"] = bool(out["spot"] and out["options"])
    return out


def _series_range(name: str) -> dict:
    df = MS.read("spot", name, columns=["timestamp"])
    if df.empty:
        return {}
    ts = pd.to_datetime(df["timestamp"])
    return {"first": str(ts.min().date()), "last": str(ts.max().date()),
            "sessions": int(ts.dt.date.nunique())}


def load(underlying: str, start: str, end: str, with_options: bool = True) -> dict:
    """Index bars, option chain, VIX and futures for one date range."""
    bars = MS.read("spot", underlying, start=start, end=end,
                   columns=["timestamp", "open", "high", "low", "close", "volume"])
    if not bars.empty:
        bars = bars.sort_values("timestamp").reset_index(drop=True)
    options = pd.DataFrame()
    if with_options:
        options = MS.read("options", underlying, start=start, end=end,
                          columns=["timestamp", "expiry_date", "strike", "option_type",
                                   "open", "high", "low", "close", "volume", "oi", "spot"])
        if not options.empty:
            options = options.sort_values("timestamp").reset_index(drop=True)
            options["contract"] = (options["option_type"].astype(str) + " "
                                   + options["strike"].astype(float).round(2).map(lambda x: f"{x:g}")
                                   + " " + options["expiry_date"].astype(str))
    vix = MS.read("spot", "INDIAVIX", start=start, end=end, columns=["timestamp", "close"])
    fut = MS.read("spot", f"{underlying}FUT", start=start, end=end, columns=["timestamp", "close"])
    return {"bars": bars, "options": options, "vix": vix, "futures": fut}


def check(bundle: dict, underlying: str = "NIFTY") -> dict:
    """Data-quality report. Warnings never silently change the data — they are shown to you."""
    bars, options = bundle.get("bars"), bundle.get("options")
    problems: list[dict] = []
    stats: dict = {}
    if bars is None or bars.empty:
        return {"ok": False, "problems": [{"level": "error", "text": "no index bars in this range"}],
                "stats": {}}

    ts = pd.to_datetime(bars["timestamp"])
    day = ts.dt.date
    stats["sessions"] = int(day.nunique())
    stats["bars"] = int(len(bars))
    stats["first"] = str(ts.min())
    stats["last"] = str(ts.max())

    if not ts.is_monotonic_increasing:
        problems.append({"level": "error", "text": "index bars are not in time order"})
    dupes = int(ts.duplicated().sum())
    if dupes:
        problems.append({"level": "error", "text": f"{dupes} duplicate index timestamps"})
    bad = ((bars["high"] < bars["low"]) | (bars["close"] > bars["high"] + 1e-6)
           | (bars["close"] < bars["low"] - 1e-6) | (bars["close"] <= 0))
    if int(bad.sum()):
        problems.append({"level": "error", "text": f"{int(bad.sum())} index bars with impossible OHLC"})

    per_day = bars.groupby(day).size()
    short = per_day[per_day < FULL_SESSION_BARS * 0.9]
    stats["short_sessions"] = int(len(short))
    if len(short):
        worst = short.sort_values().head(3)
        problems.append({"level": "warn",
                         "text": f"{len(short)} sessions have under 90% of a full day's bars "
                                 f"(e.g. {', '.join(f'{d} = {n}' for d, n in worst.items())})"})

    # weekdays inside the range with no data at all
    all_days = pd.date_range(ts.min().date(), ts.max().date(), freq="B").date
    missing = sorted(set(all_days) - set(day.unique()))
    stats["missing_weekdays"] = len(missing)
    if missing:
        problems.append({"level": "info",
                         "text": f"{len(missing)} weekdays have no index data (market holidays "
                                 f"or gaps): {', '.join(str(d) for d in missing[:5])}"
                                 + (" …" if len(missing) > 5 else "")})

    if options is None or options.empty:
        problems.append({"level": "error",
                         "text": "no option data in this range — the lab can measure index moves "
                                 "but cannot price a trade"})
        return {"ok": False, "problems": problems, "stats": stats}

    ots = pd.to_datetime(options["timestamp"])
    oday = ots.dt.date
    stats["option_rows"] = int(len(options))
    stats["option_sessions"] = int(oday.nunique())
    stats["expiries"] = int(options["expiry_date"].nunique())
    stats["strikes_per_session"] = _f(options.groupby(oday)["strike"].nunique().mean())
    nodata = sorted(set(day.unique()) - set(oday.unique()))
    if nodata:
        problems.append({"level": "warn",
                         "text": f"{len(nodata)} sessions have index bars but no option data — "
                                 "signals on those days are recorded and skipped, never filled"})
    obad = ((options["close"] <= 0) | (options["high"] < options["low"]))
    if int(obad.sum()):
        problems.append({"level": "warn", "text": f"{int(obad.sum())} option rows with zero/negative or impossible prices"})
    if "oi" in options.columns and float((options["oi"] == 0).mean()) > 0.5:
        problems.append({"level": "info", "text": "over half the option rows carry no OI — OI filters will be ineffective"})
    thin = options.groupby(oday)["strike"].nunique()
    if (thin < 5).any():
        problems.append({"level": "warn",
                         "text": f"{int((thin < 5).sum())} sessions carry fewer than 5 strikes — "
                                 "strike selection will often fall outside the stored data"})
    return {"ok": not any(p["level"] == "error" for p in problems), "problems": problems, "stats": stats}


def _f(v):
    try:
        x = float(v)
        return round(x, 2) if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def split_range(first: str, last: str, train: float = 0.6, validation: float = 0.2) -> dict:
    """Split a date range into train / validation / out-of-sample, in that chronological order.

    Chronological, never random: shuffling days would leak tomorrow into today's parameters.
    """
    a, b = pd.Timestamp(first).date(), pd.Timestamp(last).date()
    total = (b - a).days
    if total < 30:
        return {"insufficient_data": True, "days": total,
                "message": "under a month of data — a split would not mean anything"}
    t_end = a + timedelta(days=int(total * train))
    v_end = a + timedelta(days=int(total * (train + validation)))
    return {
        "train": {"start": str(a), "end": str(t_end)},
        "validation": {"start": str(t_end + timedelta(days=1)), "end": str(v_end)},
        "test": {"start": str(v_end + timedelta(days=1)), "end": str(b)},
        "days": total,
    }
