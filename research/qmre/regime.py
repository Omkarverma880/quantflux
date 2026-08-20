"""QMRE market-regime classification (reuses Market Pulse confirmations)."""
from __future__ import annotations


def regime_from_pulse(pulse: dict) -> dict:
    """Map a Market-Pulse snapshot to a regime score in [-1,1] + label.
    Blends the confirmation net with the NIFTY day change so a single indicator
    can't dominate."""
    if not pulse or pulse.get("status") != "ok":
        return {"score": 0.0, "label": "NEUTRAL", "available": False}
    conf = pulse.get("confirmation") or {}
    total = max(1, int(conf.get("total", 0)))
    net = int(conf.get("net", 0)) / total                     # -1..1
    dc = float(pulse.get("day_change_pct", 0) or 0)
    day = max(-1.0, min(1.0, dc / 1.0))                       # ±1% ≈ full tilt
    score = round(0.6 * net + 0.4 * day, 3)
    label = ("STRONG BULLISH" if score >= 0.5 else "BULLISH" if score >= 0.15
             else "STRONG BEARISH" if score <= -0.5 else "BEARISH" if score <= -0.15
             else "NEUTRAL")
    return {"score": score, "label": label, "available": True,
            "day_change_pct": round(dc, 2), "net": conf.get("net", 0), "total": total}
