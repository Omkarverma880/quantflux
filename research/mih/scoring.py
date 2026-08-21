"""
MIH Stock Score — a 0-10 TECHNICAL strength score with a visible breakdown.

Honesty note: the broker feed provides price/volume/depth only — no earnings,
balance-sheet or valuation data — so this is explicitly a *technical* score
(trend, momentum, participation, VWAP posture, range position). It is NOT a
fundamentals or valuation rating, and the UI must not present it as one.
"""
from __future__ import annotations

from typing import Optional


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _interp(anchors, x: Optional[float]) -> float:
    if x is None:
        return 0.5
    pts = sorted(anchors, key=lambda p: p[0])
    if x <= pts[0][0]:
        return float(pts[0][1])
    if x >= pts[-1][0]:
        return float(pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y1
    return 0.5


def score_stock(r: dict, cfg: dict) -> dict:
    """Returns {score(0-10), grade, breakdown{component:{pts,max,sub}}, available}."""
    w = cfg["score_weights"]
    ltp = r.get("ltp") or 0

    # trend — position inside the 52-week range (or 20-day range as fallback)
    hi, lo = r.get("high_52w"), r.get("low_52w")
    if hi and lo and hi > lo:
        trend = _clamp((ltp - lo) / (hi - lo))
    elif r.get("high_20d") and r.get("low_20d") and r["high_20d"] > r["low_20d"]:
        trend = _clamp((ltp - r["low_20d"]) / (r["high_20d"] - r["low_20d"]))
    else:
        trend = 0.5

    momentum = _interp([[-4, 0], [-1, 0.25], [0, 0.5], [1, 0.7], [3, 0.9], [6, 1.0]], r.get("change_pct"))
    volume = _interp([[0.4, 0.1], [0.8, 0.35], [1.0, 0.5], [1.5, 0.7], [2.5, 0.9], [4, 1.0]], r.get("rvol"))
    vwap_sub = 0.5
    if r.get("vwap") and ltp:
        vwap_sub = _interp([[-2, 0.05], [-0.3, 0.35], [0, 0.55], [0.5, 0.75], [2, 1.0]],
                           (ltp - r["vwap"]) / r["vwap"] * 100.0)
    # range — where the close sits within today's own bar (strong close = high)
    dh, dl = r.get("high"), r.get("low")
    rng = _clamp((ltp - dl) / (dh - dl)) if (dh and dl and dh > dl) else 0.5

    subs = {"trend": trend, "momentum": momentum, "volume": volume, "vwap": vwap_sub, "range": rng}
    total_w = sum(w.values()) or 1.0
    breakdown, raw = {}, 0.0
    for k, s in subs.items():
        pts = s * float(w.get(k, 0))
        raw += pts
        breakdown[k] = {"pts": round(pts, 1), "max": round(float(w.get(k, 0)), 1), "sub": round(s * 100)}
    score10 = round(raw / total_w * 10.0, 1)
    grade = ("Very Strong" if score10 >= 8 else "Strong" if score10 >= 6.5
             else "Neutral" if score10 >= 4.5 else "Weak" if score10 >= 3 else "Very Weak")
    return {"score": score10, "grade": grade, "breakdown": breakdown,
            "basis": "technical", "fundamentals_available": False}
