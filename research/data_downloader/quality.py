"""
Data-quality validation for a normalized dataset (pure).

Surfaces problems — it never silently hides them. Missing-interval detection is
exact for daily bars (business days) and a conservative intraday-gap estimate
for intraday (no exchange session calendar is assumed here).
"""
from __future__ import annotations

from datetime import datetime, timedelta

_STEP_MIN = {"1minute": 1, "3minute": 3, "5minute": 5, "10minute": 10,
             "15minute": 15, "30minute": 30, "60minute": 60}


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", ""))
    except Exception:
        return None


def quality_report(rows: list[dict], interval_label: str) -> dict:
    n = len(rows)
    if n == 0:
        return {"rows": 0, "duplicates": 0, "missing_ohlc": 0, "invalid": 0,
                "out_of_order": 0, "missing_intervals": 0, "status": "warnings",
                "messages": ["No rows downloaded"]}
    seen = set()
    dups = null_ohlc = invalid = out_of_order = 0
    prev_dt = None
    parsed = []
    for r in rows:
        ts = r.get("timestamp")
        if ts in seen:
            dups += 1
        seen.add(ts)
        o, h, l, c = r.get("open"), r.get("high"), r.get("low"), r.get("close")
        if None in (o, h, l, c):
            null_ohlc += 1
        else:
            if h < l or min(o, h, l, c) < 0 or not (l <= o <= h) or not (l <= c <= h):
                invalid += 1
        dt = _parse(ts)
        parsed.append(dt)
        if prev_dt and dt and dt < prev_dt:
            out_of_order += 1
        if dt:
            prev_dt = dt

    missing = _missing(parsed, interval_label)

    messages = []
    if dups:
        messages.append(f"{dups} duplicate timestamps")
    if null_ohlc:
        messages.append(f"{null_ohlc} rows with null OHLC")
    if invalid:
        messages.append(f"{invalid} invalid OHLC rows")
    if out_of_order:
        messages.append(f"{out_of_order} out-of-order timestamps")
    if missing:
        messages.append(f"~{missing} missing intervals (gaps)")
    status = "valid" if not (dups or null_ohlc or invalid or out_of_order) else "warnings"
    return {"rows": n, "duplicates": dups, "missing_ohlc": null_ohlc, "invalid": invalid,
            "out_of_order": out_of_order, "missing_intervals": missing,
            "status": status, "messages": messages or ["No issues detected"]}


def _missing(parsed, interval_label: str) -> int:
    dts = [d for d in parsed if d]
    if len(dts) < 2:
        return 0
    if interval_label == "day":
        miss = 0
        for a, b in zip(dts, dts[1:]):
            d = a.date() + timedelta(days=1)
            while d < b.date():
                if d.weekday() < 5:          # business day gap (holidays counted too)
                    miss += 1
                d += timedelta(days=1)
        return miss
    step = _STEP_MIN.get(interval_label)
    if not step:
        return 0
    gap = 0
    tol = timedelta(minutes=step * 1.5)
    session = timedelta(hours=7)             # ignore overnight/lunch spanning gaps
    for a, b in zip(dts, dts[1:]):
        delta = b - a
        if tol < delta < session:
            gap += int(delta.total_seconds() // (step * 60)) - 1
    return gap
