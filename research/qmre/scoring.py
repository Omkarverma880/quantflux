"""
QMRE scoring, risk and sizing — PURE and configurable.

Turns look-ahead-safe features into a 0-100 momentum score with a full component
breakdown, a signal class (A+/A/B/WATCH/NO TRADE), and a paper risk plan
(entry/SL/targets/RR + quantity). Never a bare "BUY" — every point is explained.
It is a RANKING score, not a probability, unless calibrated against history.
"""
from __future__ import annotations

import math
from typing import Optional


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


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


def _components(f: dict, ctx: dict, cfg: dict) -> dict:
    """Each component → sub-score in [0,1]."""
    regime_score = float(ctx.get("regime_score", 0))          # -1..1
    depth = f.get("depth")
    min_val = float(cfg.get("min_avg_value_cr", 5) or 1)
    rr = float(ctx.get("rr", 0) or 0)
    min_rr = float(cfg.get("min_rr", 1.5))

    sub = {
        "market_regime": _clamp01((regime_score + 1) / 2),
        "sector_strength": _interp([[-2, 0.15], [0, 0.5], [1, 0.85], [3, 1.0]], f.get("rs_sector")),
        "price_trend": _interp([[-1, 0.1], [-0.2, 0.4], [0, 0.5], [0.4, 0.75], [1.2, 1.0]], f.get("momentum")),
        "relative_strength": _interp([[-2, 0.1], [-0.3, 0.4], [0, 0.5], [0.5, 0.8], [2, 1.0]], f.get("rs")),
        "volume": _interp([[0.5, 0.1], [1, 0.4], [1.5, 0.6], [2, 0.8], [3, 1.0]], f.get("rvol")),
        "breakout": (1.0 if f.get("breakout_confirmed")
                     else 0.6 if (f.get("pdh_break") or f.get("orb") == "breakout")
                     else 0.3 if f.get("or_ready") else 0.15),
        "vwap": _clamp01((0.4 if f.get("above_vwap") else 0.0)
                         + 0.4 * float(f.get("vwap_hold", 0))
                         + (0.2 if float(f.get("vwap_slope", 0)) > 0 else 0.0)),
        "volatility": _interp([[0, 0.55], [1, 0.9], [2, 0.7], [3, 0.4], [5, 0.15]], f.get("ext_vwap_atr")),
        "liquidity": (_interp([[min_val * 0.5, 0.2], [min_val, 0.6], [min_val * 4, 1.0]], f.get("avg_day_value_cr"))
                      if f.get("avg_day_value_cr") is not None else 0.5),
        "order_book": ((float(depth.get("imbalance", 0)) + 1) / 2 if isinstance(depth, dict) and depth.get("available") else 0.5),
        "risk_reward": _interp([[0.5, 0.1], [1, 0.35], [min_rr, 0.6], [2, 0.8], [3, 1.0]], rr),
    }
    return {k: _clamp01(v) for k, v in sub.items()}


def signal_class(score: Optional[float], cfg: dict) -> str:
    if score is None:
        return "NO TRADE"
    for lb, label in cfg.get("class_bands", []):
        if score >= lb:
            return label
    return "NO TRADE"


def score_features(f: dict, ctx: dict, cfg: dict) -> dict:
    """Returns {score, breakdown, class, sub}. ``ctx`` carries regime_score + rr."""
    w = cfg["weights"]
    sub = _components(f, ctx, cfg)
    total_w = sum(w.values()) or 1.0
    breakdown, raw = {}, 0.0
    for name, s in sub.items():
        weight = float(w.get(name, 0))
        pts = s * weight
        raw += pts
        breakdown[name] = {"points": round(pts, 1), "max": round(weight, 1), "sub": round(s * 100, 0)}
    score = round(raw / total_w * 100.0, 1)
    return {"score": score, "breakdown": breakdown, "class": signal_class(score, cfg), "sub": sub}


def risk_plan(f: dict, cfg: dict) -> dict:
    """Entry / SL / targets / RR from the configured mode. Long-only momentum
    (paper). ``day_low`` / ``or_low`` give the structure stop."""
    entry = float(f["ltp"])
    atr = float(f.get("atr") or 0) or entry * 0.01
    sl_mode, sl_v = cfg["sl_mode"], float(cfg["sl_value"])
    if sl_mode == "percent":
        sl = entry * (1 - sl_v / 100.0)
    elif sl_mode == "structure":
        base = min(x for x in [f.get("day_low"), f.get("or_low"), f.get("vwap")] if x)
        sl = float(base) * 0.999
    else:  # atr
        sl = entry - atr * sl_v
    sl = max(0.01, round(sl, 2))
    risk = max(0.01, entry - sl)

    t_mode, t_v = cfg["target_mode"], float(cfg["target_value"])
    if t_mode == "percent":
        primary = entry * (1 + t_v / 100.0)
    elif t_mode == "rr":
        primary = entry + risk * t_v
    else:  # atr
        primary = entry + atr * t_v
    reward = max(0.01, primary - entry)
    t1 = round(entry + reward, 2)
    t2 = round(entry + reward * 1.7, 2)
    t3 = round(entry + reward * 2.5, 2)
    rr = round(reward / risk, 2)
    return {"entry": round(entry, 2), "sl": sl, "risk_per_share": round(risk, 2),
            "target1": t1, "target2": t2, "target3": t3, "rr": rr,
            "poor_rr": rr < float(cfg.get("min_rr", 1.5))}


def size_position(entry: float, sl: float, cfg: dict) -> dict:
    cap = float(cfg["capital_per_stock"])
    qty = int(cap // entry) if entry > 0 else 0
    risk_amt = round(qty * max(0.0, entry - sl), 2)
    return {"qty": qty, "capital_used": round(qty * entry, 2), "risk_amount": risk_amt}
