"""
Pure indicator library for QMIE (no I/O, no state) — deterministic and testable.

Operates on plain lists of candle dicts ({date?,open,high,low,close,volume}) or
float series. Used by the component engines. Nothing here can place an order.
"""
from __future__ import annotations

from typing import Optional


def closes(bars): return [float(b["close"]) for b in bars]
def highs(bars): return [float(b["high"]) for b in bars]
def lows(bars): return [float(b["low"]) for b in bars]
def volumes(bars): return [float(b.get("volume", 0) or 0) for b in bars]


def sma(series, period) -> Optional[float]:
    if len(series) < period or period <= 0:
        return None
    return sum(series[-period:]) / period


def ema(series, period) -> Optional[float]:
    if not series or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    e = float(series[0])
    for v in series[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def slope_pct(series, period) -> float:
    """Normalised slope over ``period`` bars, in % of the start value per bar."""
    if len(series) < period + 1 or series[-period - 1] == 0:
        return 0.0
    return (series[-1] - series[-period - 1]) / abs(series[-period - 1]) * 100.0 / period


def rsi(series, period: int = 14) -> Optional[float]:
    if len(series) < period + 1:
        return None
    g = l = 0.0
    for i in range(-period, 0):
        d = series[i] - series[i - 1]
        g += max(d, 0.0); l += max(-d, 0.0)
    if l == 0:
        return 100.0
    rs = (g / period) / (l / period)
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def atr(bars, period: int = 14) -> Optional[float]:
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l = float(bars[i]["high"]), float(bars[i]["low"])
        pc = float(bars[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return round(sum(trs[-period:]) / period, 4)


def cum_return(series, window: int) -> Optional[float]:
    """Simple cumulative return over the last ``window`` bars, in %."""
    if len(series) < window + 1 or series[-window - 1] == 0:
        return None
    return (series[-1] - series[-window - 1]) / abs(series[-window - 1]) * 100.0


def percentile_of(value: float, population) -> Optional[float]:
    """Percentile rank (0–100) of ``value`` within ``population``."""
    pop = [p for p in population if p is not None]
    if not pop:
        return None
    below = sum(1 for p in pop if p <= value)
    return round(below / len(pop) * 100.0, 1)


def structure(bars, lookback: int = 20) -> dict:
    """Confirmed higher-high/higher-low (bullish) or lower-low/lower-high (bearish)."""
    if len(bars) < 6:
        return {"bullish": False, "bearish": False, "swing_high": None, "swing_low": None}
    w = bars[-lookback:] if len(bars) >= lookback else bars
    hs, ls = highs(w), lows(w)
    n = len(hs)
    mid = n // 2
    bullish = (max(hs[mid:]) > max(hs[:mid])) and (min(ls[mid:]) > min(ls[:mid]))
    bearish = (max(hs[mid:]) < max(hs[:mid])) and (min(ls[mid:]) < min(ls[:mid]))
    return {"bullish": bullish, "bearish": bearish,
            "swing_high": round(max(hs), 2), "swing_low": round(min(ls), 2)}


def median(series) -> Optional[float]:
    s = sorted(v for v in series if v is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
