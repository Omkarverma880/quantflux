"""
Self-contained indicator maths for the Equity Strategy Workspace.

Deliberately standalone: the workspace is a read-only screener that must never
be able to affect a running strategy, so it carries its own EMA/ADX/VWAP/CV
implementations rather than importing them from a live engine module. Pure
functions, no I/O, no state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def ema_series(values: list[float], period: int) -> list[float]:
    """Exponential moving average, seeded with the first value (NaN-free)."""
    n = len(values)
    if n == 0 or period <= 0:
        return []
    out = [0.0] * n
    k = 2.0 / (period + 1.0)
    out[0] = float(values[0])
    for i in range(1, n):
        out[i] = float(values[i]) * k + out[i - 1] * (1.0 - k)
    return out


def adx_series(highs: list[float], lows: list[float], closes: list[float],
               period: int = 14) -> list[float]:
    """Wilder's ADX. Returns a same-length list (leading values are 0)."""
    n = len(closes)
    if n < period + 1:
        return [0.0] * n
    tr, plus_dm, minus_dm = [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    atr = sum(tr[1:period + 1]) or 1e-9
    sp = sum(plus_dm[1:period + 1])
    sm = sum(minus_dm[1:period + 1])
    dx: list[float] = [0.0] * n
    for i in range(period + 1, n):
        atr = atr - (atr / period) + tr[i]
        sp = sp - (sp / period) + plus_dm[i]
        sm = sm - (sm / period) + minus_dm[i]
        denom = atr or 1e-9
        pdi = 100.0 * sp / denom
        mdi = 100.0 * sm / denom
        s = (pdi + mdi) or 1e-9
        dx[i] = 100.0 * abs(pdi - mdi) / s
    out = [0.0] * n
    start = period * 2
    if start < n:
        out[start] = sum(dx[period + 1:start + 1]) / period if period else 0.0
        for i in range(start + 1, n):
            out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def running_vwap(candles: list[dict]) -> list[float]:
    """Cumulative (session) VWAP over the given candles, typical price × volume."""
    out: list[float] = []
    pv = vol = 0.0
    for c in candles:
        h, l, cl = float(c["high"]), float(c["low"]), float(c["close"])
        v = float(c.get("volume", 0) or 0)
        tp = (h + l + cl) / 3.0
        pv += tp * v
        vol += v
        out.append(pv / vol if vol else cl)
    return out


def signed_volume(c: dict) -> float:
    """Volume signed by the candle's direction — the building block of CV."""
    v = float(c.get("volume", 0) or 0)
    o, cl = float(c["open"]), float(c["close"])
    if cl > o:
        return v
    if cl < o:
        return -v
    return 0.0


def cumulative_volume(candles: list[dict]) -> list[float]:
    """Running sum of signed volume (green adds, red subtracts)."""
    out, tot = [], 0.0
    for c in candles:
        tot += signed_volume(c)
        out.append(tot)
    return out


def candle_dt(c: dict) -> Optional[datetime]:
    """Kite candles carry a tz-aware 'date'; normalise to naive local."""
    dt = c.get("date") if isinstance(c, dict) else None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None)
    return None


def last(seq: list[float], default: float = 0.0) -> float:
    return seq[-1] if seq else default


def pct(a: float, b: float) -> Optional[float]:
    """(a − b) / b as a percentage."""
    if not b:
        return None
    return round((a - b) / b * 100.0, 2)
