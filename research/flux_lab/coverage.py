"""
Flux Lab — what the stored history actually covers.

Reading the whole timestamp column of three years of option data costs over half a gigabyte and
this runs on every page load, so the dates are collected one month file at a time and the answer
is cached until a file changes.
"""
from __future__ import annotations

import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.flux_lab.coverage")

_CACHE: dict = {}


def _sessions_of(kind: str, underlying: str = "NIFTY") -> dict:
    import pyarrow.parquet as pq
    base = MS.ROOT / f"kind={kind}" / f"underlying={underlying.upper()}"
    if not base.exists():
        return {}
    days: set = set()
    for f in sorted(base.rglob("*.parquet")):
        try:
            tbl = pq.read_table(f, columns=["timestamp"])
        except Exception as exc:
            logger.debug("coverage: %s unreadable (%s)", f.name, exc)
            continue
        days.update(pd.to_datetime(tbl.column("timestamp").to_pandas()).dt.date.unique())
        del tbl
    if not days:
        return {}
    return {"first": str(min(days)), "last": str(max(days)), "sessions": len(days)}


def coverage() -> dict:
    """First date, last date and session count for the index and the option chain."""
    try:
        files = sorted(MS.ROOT.rglob("*.parquet"))
        stamp = (len(files), max((f.stat().st_mtime for f in files), default=0))
    except Exception:
        stamp = (0, 0)
    if _CACHE.get("stamp") == stamp:
        return _CACHE["value"]
    out = {}
    for kind in ("spot", "options"):
        got = _sessions_of(kind)
        if got:
            out[kind] = got
    _CACHE.update(stamp=stamp, value=out)
    return out
