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


def entry_plan(f: dict, cfg: dict) -> dict:
    """Smart LONG entry (paper). Decides WHERE and HOW to enter from market
    structure instead of blindly using LTP:

      • BREAK    — price hasn't cleared the trigger yet → buy-stop just above it.
      • NOW      — cleared the trigger and not over-extended → enter at market.
      • PULLBACK — already stretched from VWAP (chasing risk) → wait for a retest
                   toward VWAP; entry is BELOW the current price.

    The stop is anchored to structure (below VWAP / trigger / OR-low), targets are
    measured from the ENTRY (not LTP), and an ``entry_quality`` (0-1) rewards clean
    setups and penalises extended/late ones so the scanner doesn't chase."""
    ltp = float(f["ltp"])
    atr = float(f.get("atr") or 0) or ltp * 0.01
    vwap = float(f.get("vwap") or ltp) or ltp
    or_high = float(f.get("or_high") or 0)
    prev_high = float(f.get("prev_high") or 0)
    trigger = max(or_high, prev_high, 0) or ltp
    ext_atr = (ltp - vwap) / atr if atr else 0.0       # how many ATRs above VWAP
    ext_ok, ext_hot = 1.0, 2.2
    buf = 0.0005                                        # 5 bps trigger buffer

    if ltp < trigger * (1 - 0.0002):                   # approaching, not yet broken
        etype, entry = "BREAK", round(trigger * (1 + buf), 2)
        note = f"Buy-stop on break above ₹{round(trigger, 2)}"
    elif ext_atr <= ext_ok:                            # broken, not extended
        etype, entry = "NOW", round(ltp, 2)
        note = "Confirmed — enter at market"
    else:                                              # extended → don't chase
        etype, entry = "PULLBACK", round(max(vwap, (trigger + vwap) / 2), 2)
        note = f"Extended {ext_atr:.1f} ATR — enter on pullback toward VWAP ₹{round(vwap, 2)}"

    zone = round(max(0.02, atr * 0.15), 2)
    entry_low, entry_high = round(entry - zone, 2), round(entry + zone, 2)

    sl_mode, sl_v = cfg["sl_mode"], float(cfg["sl_value"])
    struct = min([x for x in [f.get("day_low"), f.get("or_low"), vwap, trigger * (1 - 0.001)] if x] or [entry * 0.99])
    if sl_mode == "percent":
        sl = entry * (1 - sl_v / 100.0)
    elif sl_mode == "structure":
        sl = float(struct) * 0.999
    else:  # atr
        sl = entry - atr * sl_v
    sl = max(0.01, round(min(sl, entry * 0.999), 2))   # always below entry
    risk = max(0.01, entry - sl)

    t_mode, t_v = cfg["target_mode"], float(cfg["target_value"])
    if t_mode == "percent":
        primary = entry * (1 + t_v / 100.0)
    elif t_mode == "rr":
        primary = entry + risk * t_v
    else:  # atr
        primary = entry + atr * t_v
    reward = max(0.01, primary - entry)
    rr = round(reward / risk, 2)

    if etype == "NOW":
        eq = 1.0 - min(0.5, max(0.0, ext_atr - 0.3) * 0.4)
    elif etype == "BREAK":
        eq = 0.85
    else:                                              # PULLBACK / extended
        eq = max(0.2, 0.6 - (ext_atr - ext_ok) * 0.15)
    if ext_atr > ext_hot:                              # exhaustion risk
        eq *= 0.6

    return {"entry": entry, "entry_low": entry_low, "entry_high": entry_high,
            "entry_type": etype, "entry_note": note, "ext_atr": round(ext_atr, 2),
            "sl": sl, "risk_per_share": round(risk, 2),
            "target1": round(entry + reward, 2), "target2": round(entry + reward * 1.7, 2),
            "target3": round(entry + reward * 2.5, 2), "rr": rr,
            "poor_rr": rr < float(cfg.get("min_rr", 1.5)),
            "entry_quality": round(_clamp01(eq), 2)}


# backwards-compat alias
risk_plan = entry_plan


def size_position(entry: float, sl: float, cfg: dict) -> dict:
    cap = float(cfg["capital_per_stock"])
    qty = int(cap // entry) if entry > 0 else 0
    risk_amt = round(qty * max(0.0, entry - sl), 2)
    return {"qty": qty, "capital_used": round(qty * entry, 2), "risk_amount": risk_amt}
