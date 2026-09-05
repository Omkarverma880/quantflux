"""
Indicator maths for the Chart Simulation workspace.

Self-contained and pure: candles in, aligned series out. Nothing here imports a
live strategy, so tuning a chart indicator can never move a running engine.

Everything is returned **aligned to the candle array** (same length, ``None``
where the value does not exist yet) so the front end can plot without any
index arithmetic of its own.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

# ── basics ───────────────────────────────────────────────────────────
def _f(c: dict, k: str) -> float:
    return float(c[k])


def _v(c: dict) -> float:
    return float(c.get("volume", 0) or 0)


def _tp(c: dict) -> float:
    return (_f(c, "high") + _f(c, "low") + _f(c, "close")) / 3.0


def ema(values: list[float], period: int) -> list[Optional[float]]:
    n = len(values)
    if n == 0 or period <= 1:
        return [None] * n
    out: list[Optional[float]] = [None] * n
    k = 2.0 / (period + 1.0)
    run = None
    for i, v in enumerate(values):
        run = float(v) if run is None else float(v) * k + run * (1.0 - k)
        if i >= period - 1:
            out[i] = round(run, 4)
    return out


def sma(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        out.append(round(run / min(i + 1, period), 4) if i >= period - 1 else None)
    return out


def cumulative_volume(candles: list[dict]) -> list[float]:
    """Signed volume run — green candles add, red candles subtract."""
    out, tot = [], 0.0
    for c in candles:
        v = _v(c)
        o, cl = _f(c, "open"), _f(c, "close")
        tot += v if cl > o else (-v if cl < o else 0.0)
        out.append(round(tot))
    return out


# ── period keys ──────────────────────────────────────────────────────
def _day_key(dt: datetime):
    return dt.date()


def _week_key(dt: datetime):
    iso = dt.isocalendar()
    return (iso[0], iso[1])


def _month_key(dt: datetime):
    return (dt.year, dt.month)


_KEYS = {"day": _day_key, "week": _week_key, "month": _month_key}


def anchored_vwap(candles: list[dict], basis: str) -> list[Optional[float]]:
    """Running VWAP re-anchored at the start of each day / week / month."""
    keyfn = _KEYS[basis]
    out: list[Optional[float]] = []
    cur = None
    pv = vol = 0.0
    for c in candles:
        dt = c["_dt"]
        k = keyfn(dt)
        if k != cur:
            cur, pv, vol = k, 0.0, 0.0
        v = _v(c)
        pv += _tp(c) * v
        vol += v
        out.append(round(pv / vol, 2) if vol else None)
    return out


def previous_period_vwap(candles: list[dict], basis: str) -> list[Optional[float]]:
    """The *previous* period's final VWAP, carried forward as a flat level."""
    keyfn = _KEYS[basis]
    finals: dict = {}
    order: list = []
    pv = vol = 0.0
    cur = None
    for c in candles:
        k = keyfn(c["_dt"])
        if k != cur:
            if cur is not None:
                finals[cur] = round(pv / vol, 2) if vol else None
                order.append(cur)
            cur, pv, vol = k, 0.0, 0.0
        v = _v(c)
        pv += _tp(c) * v
        vol += v
    if cur is not None:
        finals[cur] = round(pv / vol, 2) if vol else None
        order.append(cur)
    prev_of = {k: (finals.get(order[i - 1]) if i else None) for i, k in enumerate(order)}
    return [prev_of.get(keyfn(c["_dt"])) for c in candles]


def period_ohlc(candles: list[dict], basis: str) -> dict:
    """{period key → {high, low, close, open}} for pivots and prev-day H/L."""
    keyfn = _KEYS[basis]
    out: dict = {}
    for c in candles:
        k = keyfn(c["_dt"])
        row = out.get(k)
        if row is None:
            out[k] = {"open": _f(c, "open"), "high": _f(c, "high"),
                      "low": _f(c, "low"), "close": _f(c, "close")}
        else:
            row["high"] = max(row["high"], _f(c, "high"))
            row["low"] = min(row["low"], _f(c, "low"))
            row["close"] = _f(c, "close")
    return out


def floor_pivots(high: float, low: float, close: float) -> dict:
    """Classic floor-trader pivots from the previous period."""
    pp = (high + low + close) / 3.0
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return {k: round(v, 2) for k, v in
            {"pp": pp, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}.items()}


def pivot_series(candles: list[dict], basis: str) -> dict[str, list[Optional[float]]]:
    """Per-bar pivot levels derived from the PREVIOUS period — the way a floor
    trader would have them on the sheet before the session opens."""
    keyfn = _KEYS[basis]
    per = period_ohlc(candles, basis)
    order = list(per.keys())
    piv_of: dict = {}
    for i, k in enumerate(order):
        if i == 0:
            piv_of[k] = None
            continue
        p = per[order[i - 1]]
        piv_of[k] = floor_pivots(p["high"], p["low"], p["close"])
    names = ["pp", "r1", "r2", "r3", "s1", "s2", "s3"]
    out = {n: [] for n in names}
    for c in candles:
        p = piv_of.get(keyfn(c["_dt"]))
        for n in names:
            out[n].append(p[n] if p else None)
    return out


def prev_day_hl(candles: list[dict]) -> dict[str, list[Optional[float]]]:
    per = period_ohlc(candles, "day")
    order = list(per.keys())
    prev = {k: (per[order[i - 1]] if i else None) for i, k in enumerate(order)}
    hi, lo = [], []
    for c in candles:
        p = prev.get(c["_dt"].date())
        hi.append(round(p["high"], 2) if p else None)
        lo.append(round(p["low"], 2) if p else None)
    return {"prev_high": hi, "prev_low": lo}


def first_hour_levels(candles: list[dict], minutes: int,
                      session_start: dtime = dtime(9, 15)) -> dict[str, list[Optional[float]]]:
    """High / low of the opening window for each session, carried across the day.

    ``minutes`` is yours to set — 15, 30, 60 or anything else; the window is
    measured from the session open, so it works on every intraday timeframe."""
    end_minutes = session_start.hour * 60 + session_start.minute + minutes
    per_day: dict = {}
    for c in candles:
        dt = c["_dt"]
        mins = dt.hour * 60 + dt.minute
        if mins < session_start.hour * 60 + session_start.minute or mins >= end_minutes:
            continue
        d = dt.date()
        row = per_day.get(d)
        if row is None:
            per_day[d] = {"high": _f(c, "high"), "low": _f(c, "low")}
        else:
            row["high"] = max(row["high"], _f(c, "high"))
            row["low"] = min(row["low"], _f(c, "low"))
    hi, lo = [], []
    for c in candles:
        row = per_day.get(c["_dt"].date())
        hi.append(round(row["high"], 2) if row else None)
        lo.append(round(row["low"], 2) if row else None)
    return {"fh_high": hi, "fh_low": lo}


def _first_hour_by_day(candles: list[dict], minutes: int,
                       session_start: dtime = dtime(9, 15)) -> dict:
    """{session date → {high, low}} for the opening window."""
    start_m = session_start.hour * 60 + session_start.minute
    end_m = start_m + minutes
    per: dict = {}
    for c in candles:
        dt = c["_dt"]
        m = dt.hour * 60 + dt.minute
        if m < start_m or m >= end_m:
            continue
        d = dt.date()
        row = per.get(d)
        if row is None:
            per[d] = {"high": _f(c, "high"), "low": _f(c, "low")}
        else:
            row["high"] = max(row["high"], _f(c, "high"))
            row["low"] = min(row["low"], _f(c, "low"))
    return per


def first_hour_prev(candles: list[dict], minutes: int) -> dict[str, list[Optional[float]]]:
    """The PREVIOUS session's opening-window high/low, carried across today."""
    per = _first_hour_by_day(candles, minutes)
    order = sorted(per.keys())
    prev = {d: (per[order[i - 1]] if i else None) for i, d in enumerate(order)}
    hi, lo = [], []
    for c in candles:
        row = prev.get(c["_dt"].date())
        hi.append(round(row["high"], 2) if row else None)
        lo.append(round(row["low"], 2) if row else None)
    return {"fhp_high": hi, "fhp_low": lo}


def first_hour_stats(candles: list[dict], minutes: int, days: int) -> dict[str, list[Optional[float]]]:
    """Max / min / average of the opening window over the last ``days`` COMPLETED
    sessions, carried as flat levels through the current session."""
    per = _first_hour_by_day(candles, minutes)
    order = sorted(per.keys())
    stats: dict = {}
    for i, d in enumerate(order):
        window = [per[x] for x in order[max(0, i - days):i]]      # strictly previous sessions
        if not window:
            stats[d] = None
            continue
        highs = [w["high"] for w in window]
        lows = [w["low"] for w in window]
        stats[d] = {"max_high": round(max(highs), 2), "min_low": round(min(lows), 2),
                    "avg_high": round(sum(highs) / len(highs), 2),
                    "avg_low": round(sum(lows) / len(lows), 2), "days": len(window)}
    out = {k: [] for k in ("fhs_max_high", "fhs_min_low", "fhs_avg_high", "fhs_avg_low")}
    for c in candles:
        st = stats.get(c["_dt"].date())
        out["fhs_max_high"].append(st["max_high"] if st else None)
        out["fhs_min_low"].append(st["min_low"] if st else None)
        out["fhs_avg_high"].append(st["avg_high"] if st else None)
        out["fhs_avg_low"].append(st["avg_low"] if st else None)
    return out


# ── strategy overlays ────────────────────────────────────────────────
def fourth_candle_marks(candles: list[dict]) -> dict:
    """Per session: the colours of the first three candles, the 4th candle's
    high/low, and the bar that broke it. Intraday timeframes only."""
    by_day: dict = {}
    for i, c in enumerate(candles):
        by_day.setdefault(c["_dt"].date(), []).append(i)
    marks, levels = [], []
    high_line: list[Optional[float]] = [None] * len(candles)
    low_line: list[Optional[float]] = [None] * len(candles)
    for d, idxs in by_day.items():
        if len(idxs) < 5:
            continue
        first3 = idxs[:3]
        cols = []
        for i in first3:
            o, cl = _f(candles[i], "open"), _f(candles[i], "close")
            cols.append("green" if cl > o else "red" if cl < o else "doji")
        if not (all(x == "red" for x in cols) or all(x == "green" for x in cols)):
            continue
        bias = "call" if all(x == "red" for x in cols) else "put"
        f = idxs[3]
        fh, fl = round(_f(candles[f], "high"), 2), round(_f(candles[f], "low"), 2)
        for i in idxs[3:]:
            high_line[i] = fh
            low_line[i] = fl
        hit = None
        for i in idxs[4:]:
            if bias == "call" and _f(candles[i], "high") >= fh:
                hit = i
                break
            if bias == "put" and _f(candles[i], "low") <= fl:
                hit = i
                break
        marks.append({"idx": f, "type": "4C", "label": f"4th ({'3 RED' if bias == 'call' else '3 GREEN'})",
                      "price": fh if bias == "call" else fl})
        if hit is not None:
            marks.append({"idx": hit, "type": "BRK", "label": "breakout",
                          "price": round(_f(candles[hit], "close"), 2)})
        levels.append({"date": d.isoformat(), "bias": bias, "high": fh, "low": fl,
                       "broke": hit is not None})
    return {"high": high_line, "low": low_line, "marks": marks, "days": levels}


def hammer_marks(candles: list[dict], lookback: int, red_before: int,
                 lower_wick_min: float = 2.0, body_max: float = 2.0,
                 upper_wick_max: float = 1.0) -> dict:
    """Bars that are a hammer at a lookback low after N red candles, with the
    breakout level they arm. Meant for the daily timeframe."""
    marks = []
    trigger: list[Optional[float]] = [None] * len(candles)
    n = len(candles)
    for i in range(max(lookback, red_before), n):
        c = candles[i]
        o, h, l, cl = _f(c, "open"), _f(c, "high"), _f(c, "low"), _f(c, "close")
        if l <= 0:
            continue
        lw = (min(o, cl) - l) / l * 100.0
        uw = (h - max(o, cl)) / l * 100.0
        body = abs(cl - o) / l * 100.0
        if not (lw >= lower_wick_min and body < body_max and uw < upper_wick_max):
            continue
        window = candles[i - lookback + 1: i + 1]
        if l > min(_f(x, "low") for x in window):
            continue
        if red_before and not all(_f(candles[i - k], "close") < _f(candles[i - k], "open")
                                  for k in range(1, red_before + 1)):
            continue
        marks.append({"idx": i, "type": "HAM", "label": "hammer", "price": round(l, 2)})
        for j in range(i, min(n, i + 10)):
            trigger[j] = round(h, 2)
    return {"marks": marks, "trigger": trigger}


# ── saved levels ─────────────────────────────────────────────────────
def level_status(candles: list[dict], price: float, near_pct: float) -> dict:
    """How price is behaving around a saved level: last touch, how many touches,
    whether it is being approached from below or above, and by how much."""
    if not candles or price <= 0:
        return {"state": "unknown"}
    touches = []
    for c in candles:
        if _f(c, "low") <= price <= _f(c, "high"):
            touches.append(c["_dt"])
    last = candles[-1]
    ltp = _f(last, "close")
    dist_pct = (ltp - price) / price * 100.0
    near = abs(dist_pct) <= near_pct
    today = last["_dt"].date()
    touched_today = any(t.date() == today for t in touches)
    # direction of approach over the recent bars
    look = candles[-6:] if len(candles) >= 6 else candles
    first_close = _f(look[0], "close")
    moving_up = ltp > first_close
    if touched_today:
        state = "TOUCHED"
    elif near:
        state = "NEAR"
    elif dist_pct > 0:
        state = "ABOVE"
    else:
        state = "BELOW"
    if state in ("NEAR", "ABOVE", "BELOW"):
        approach = ("rising into it" if (dist_pct < 0 and moving_up)
                    else "falling into it" if (dist_pct > 0 and not moving_up)
                    else "moving away")
    else:
        approach = "at the level"
    return {
        "state": state, "distance_pct": round(dist_pct, 2),
        "distance": round(ltp - price, 2), "near": near,
        "touches": len(touches),
        "last_touch": touches[-1].strftime("%d-%b-%Y %H:%M") if touches else None,
        "last_touch_date": touches[-1].date().isoformat() if touches else None,
        "touched_today": touched_today, "approach": approach,
        "side": "above" if dist_pct > 0 else "below" if dist_pct < 0 else "at",
    }


def compute(candles: list[dict], keys: list[str], cfg: dict) -> dict:
    """Run the requested indicators over the candle array."""
    out: dict = {}
    if not candles:
        return out
    closes = [_f(c, "close") for c in candles]
    vols = [_v(c) for c in candles]
    want = set(keys)

    if "volume" in want:
        out["volume"] = [round(v) for v in vols]
    if "volume_ma" in want:
        out["volume_ma"] = sma(vols, int(cfg["volume_ma"]))
    if "cum_volume" in want:
        out["cum_volume"] = cumulative_volume(candles)
    if "oi" in want:
        oi = [float(c.get("oi", 0) or 0) for c in candles]
        out["oi"] = [round(v) for v in oi] if any(oi) else None
    if "vwap_day" in want:
        out["vwap_day"] = anchored_vwap(candles, "day")
    if "vwap_week" in want:
        out["vwap_week"] = anchored_vwap(candles, "week")
    if "vwap_month" in want:
        out["vwap_month"] = anchored_vwap(candles, "month")
    if "pvwap_day" in want:
        out["pvwap_day"] = previous_period_vwap(candles, "day")
    if "pvwap_week" in want:
        out["pvwap_week"] = previous_period_vwap(candles, "week")
    if "pvwap_month" in want:
        out["pvwap_month"] = previous_period_vwap(candles, "month")
    if "pivots" in want:
        out["pivots"] = pivot_series(candles, cfg["pivot_basis"])
    if "prev_day_hl" in want:
        out["prev_day_hl"] = prev_day_hl(candles)
    if "first_hour" in want:
        out["first_hour"] = first_hour_levels(candles, int(cfg["first_hour_minutes"]))
    if "first_hour_prev" in want:
        out["first_hour_prev"] = first_hour_prev(candles, int(cfg["first_hour_minutes"]))
    if "first_hour_stats" in want:
        out["first_hour_stats"] = first_hour_stats(candles, int(cfg["first_hour_minutes"]),
                                                   int(cfg.get("first_hour_days", 5)))
    if "ema_fast" in want:
        out["ema_fast"] = ema(closes, int(cfg["ema_fast"]))
    if "ema_slow" in want:
        out["ema_slow"] = ema(closes, int(cfg["ema_slow"]))
    tf = cfg.get("timeframe", "")
    intraday = tf not in ("day", "week", "month")
    if "fourth_candle" in want and intraday:
        # a "first three candles of the session" setup only exists intraday
        out["fourth_candle"] = fourth_candle_marks(candles)
    if "hammer" in want and not intraday:
        # the hammer strategy is defined on daily candles
        out["hammer"] = hammer_marks(candles, int(cfg["hammer_lookback"]),
                                     int(cfg["hammer_red_before"]))
    return out
