"""
Pure technical-indicator library for OPEI.

Every function is side-effect free and operates on plain Python lists of candle
dicts ({open,high,low,close,volume}) or float series — no pandas, no I/O — so
it is fast, memory-light and trivially unit-testable. Used to derive the
premium-structure / momentum / trend features the scoring engine consumes.
"""
from __future__ import annotations

from typing import Optional


# ── series helpers ────────────────────────────────────────────────
def closes(candles: list[dict]) -> list[float]:
    return [float(c["close"]) for c in candles]


def highs(candles: list[dict]) -> list[float]:
    return [float(c["high"]) for c in candles]


def lows(candles: list[dict]) -> list[float]:
    return [float(c["low"]) for c in candles]


def volumes(candles: list[dict]) -> list[float]:
    return [float(c.get("volume", 0) or 0) for c in candles]


# ── moving averages ───────────────────────────────────────────────
def ema(series: list[float], period: int) -> Optional[float]:
    if not series or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    e = float(series[0])
    for v in series[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def ema_series(series: list[float], period: int) -> list[float]:
    if not series:
        return []
    k = 2.0 / (period + 1.0)
    out = [float(series[0])]
    for v in series[1:]:
        out.append(float(v) * k + out[-1] * (1.0 - k))
    return out


def sma(series: list[float], period: int) -> Optional[float]:
    if len(series) < period or period <= 0:
        return None
    return sum(series[-period:]) / period


def slope(series: list[float], period: int = 5) -> float:
    """Normalised slope of the last ``period`` points (%/bar of last value)."""
    if len(series) < period + 1 or series[-1] == 0:
        return 0.0
    return (series[-1] - series[-period - 1]) / abs(series[-period - 1]) * 100.0 / period


# ── oscillators ───────────────────────────────────────────────────
def rsi(series: list[float], period: int = 14) -> Optional[float]:
    if len(series) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        d = series[i] - series[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def macd(series: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(series) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    ef = ema_series(series, fast)
    es = ema_series(series, slow)
    line = [a - b for a, b in zip(ef, es)]
    sig = ema_series(line, signal)
    return {"macd": round(line[-1], 3), "signal": round(sig[-1], 3),
            "hist": round(line[-1] - sig[-1], 3)}


def roc(series: list[float], period: int = 9) -> float:
    if len(series) < period + 1 or series[-period - 1] == 0:
        return 0.0
    return round((series[-1] - series[-period - 1]) / abs(series[-period - 1]) * 100.0, 2)


def atr(candles: list[dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l = float(candles[i]["high"]), float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return round(sum(trs[-period:]) / period, 3)


def adx(candles: list[dict], period: int = 14) -> Optional[float]:
    if len(candles) < period * 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        up = float(candles[i]["high"]) - float(candles[i - 1]["high"])
        dn = float(candles[i - 1]["low"]) - float(candles[i]["low"])
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        h, l, pc = float(candles[i]["high"]), float(candles[i]["low"]), float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr_ = sum(trs[-period:]) / period
    if atr_ == 0:
        return None
    pdi = 100.0 * (sum(plus_dm[-period:]) / period) / atr_
    mdi = 100.0 * (sum(minus_dm[-period:]) / period) / atr_
    denom = pdi + mdi
    if denom == 0:
        return 0.0
    dx = 100.0 * abs(pdi - mdi) / denom
    return round(dx, 2)


def bollinger(series: list[float], period: int = 20, mult: float = 2.0) -> dict:
    if len(series) < period:
        return {"mid": None, "upper": None, "lower": None, "width": 0.0, "pct_b": 0.5}
    window = series[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    sd = var ** 0.5
    upper, lower = mid + mult * sd, mid - mult * sd
    width = (upper - lower) / mid * 100.0 if mid else 0.0
    pct_b = (series[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {"mid": round(mid, 3), "upper": round(upper, 3), "lower": round(lower, 3),
            "width": round(width, 2), "pct_b": round(pct_b, 3)}


def donchian(candles: list[dict], period: int = 20) -> dict:
    if len(candles) < period:
        period = len(candles)
    if period == 0:
        return {"upper": None, "lower": None}
    return {"upper": round(max(highs(candles[-period:])), 2),
            "lower": round(min(lows(candles[-period:])), 2)}


# ── running VWAP (session) on candles carrying volume ─────────────
def running_vwap(candles: list[dict]) -> Optional[float]:
    pv = v = 0.0
    for c in candles:
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        vol = float(c.get("volume", 0) or 0)
        pv += tp * vol
        v += vol
    if v > 0:
        return round(pv / v, 3)
    # no volume — equal-weighted typical price fallback
    if candles:
        return round(sum((float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0 for c in candles) / len(candles), 3)
    return None


# ── price-action detectors (on the last few candles) ──────────────
def price_action(candles: list[dict]) -> dict:
    if len(candles) < 3:
        return {}
    a, b, c = candles[-3], candles[-2], candles[-1]
    ah, al, bh, bl, ch, cl = (float(a["high"]), float(a["low"]), float(b["high"]),
                              float(b["low"]), float(c["high"]), float(c["low"]))
    body = abs(float(c["close"]) - float(c["open"]))
    rng = ch - cl
    body_pct = (body / rng * 100.0) if rng else 0.0
    ranges = [float(x["high"]) - float(x["low"]) for x in candles[-7:]]
    return {
        "higher_high": ch > bh > ah,
        "lower_low": cl < bl < al,
        "higher_low": cl > bl,
        "lower_high": ch < bh,
        "inside_bar": ch <= bh and cl >= bl,
        "outside_bar": ch > bh and cl < bl,
        "nr7": (ch - cl) == min(ranges) if len(ranges) >= 7 else False,
        "body_strength": round(body_pct, 1),
        "bullish": float(c["close"]) > float(c["open"]),
    }


def swing_levels(candles: list[dict], lookback: int = 20) -> dict:
    if not candles:
        return {"swing_high": None, "swing_low": None}
    window = candles[-lookback:]
    return {"swing_high": round(max(highs(window)), 2), "swing_low": round(min(lows(window)), 2)}
