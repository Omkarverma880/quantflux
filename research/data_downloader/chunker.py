"""
Date-range chunking that respects Kite's per-request historical limits.

Pure and deterministic. A wide range is split into windows no larger than the
interval's documented maximum so a single Kite call never over-asks.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from research.data_downloader.constants import CHUNK_DAYS


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v).split("T")[0]).date()


def build_chunks(from_dt, to_dt, kite_interval: str) -> list[dict]:
    """Return ordered chunks: [{start, end, status, rows}] covering [from, to]."""
    start, end = _as_date(from_dt), _as_date(to_dt)
    if end < start:
        return []
    span = CHUNK_DAYS.get(kite_interval, 60)
    chunks: list[dict] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=span - 1), end)
        chunks.append({"start": cur.isoformat(), "end": chunk_end.isoformat(),
                       "status": "pending", "rows": 0})
        cur = chunk_end + timedelta(days=1)
    return chunks
