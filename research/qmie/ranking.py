"""
QMIE scanner: gate → score → rank → explain (§12, §40, §50).

Turns one instrument's read-only bars into a candidate record: eligibility gates,
direction-adjusted composite score, separate confidence, indicative research
plan (entry / invalidation / target — hypotheses only), analytical risk grade,
and an explanation built from stored evidence. Deterministic. No orders.
"""
from __future__ import annotations

from datetime import date, datetime

from research.qmie import engines as eng
from research.qmie import indicators as ind
from research.qmie.constants import (
    COMPONENTS, STATE_ELIGIBLE, STATE_WARNING, STATE_RESTRICTED, STATE_UNAVAILABLE,
    RISK_GRADES, RISK_COLORS, score_band,
)


def _bar_date(b):
    d = b.get("date")
    if isinstance(d, (datetime, date)):
        return d.date() if isinstance(d, datetime) else d
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d.replace("Z", "").split("T")[0]).date()
        except Exception:
            return None
    return None


def evaluate(symbol: str, exchange: str, token: int, bars: list[dict], bench: list[dict],
             cfg: dict, profile: dict, floor: float, min_rr: float, now: datetime,
             mctx: dict | None = None) -> dict:
    base = {"symbol": symbol, "exchange": exchange, "token": token,
            "horizon": cfg["horizon"], "as_of": now.isoformat()}

    # ── Gate: minimum history (Unavailable) ──
    if len(bars) < profile["min_bars"]:
        return {**base, "state": STATE_UNAVAILABLE, "direction": "n/a",
                "reason": f"Insufficient history ({len(bars)}/{profile['min_bars']} bars)"}

    close = float(bars[-1]["close"])
    warnings, blockers = [], []

    # ── Component engines ──
    tr = eng.trend_engine(bars, profile)
    rs = eng.relative_strength_engine(bars, bench, profile)
    vol = eng.volume_engine(bars, profile)
    vlt = eng.volatility_engine(bars, profile)
    liq = eng.liquidity_engine(bars, floor, profile)

    # ── Direction thesis ──
    bias = tr["bias"]
    allow_short = cfg.get("direction") == "long_short"
    if bias == "bullish":
        direction, dsign = "long", +1
    elif bias == "bearish" and allow_short:
        direction, dsign = "short", -1
    else:
        # no tradeable directional thesis under the active policy → watch-only
        return {**base, "state": STATE_RESTRICTED, "direction": "watch",
                "reason": "No directional research thesis under current policy",
                "score": 0, "confidence": 0,
                "components": {"trend": tr["score"], "relative_strength": rs["score"],
                               "volume": vol["score"], "volatility": vlt["score"], "liquidity": liq["score"]}}

    # direction-adjusted component scores (trend & RS mirror for shorts)
    comp = {
        "trend": tr["score"] if dsign > 0 else 100 - tr["score"],
        "relative_strength": rs["score"] if dsign > 0 else 100 - rs["score"],
        "volume": vol["score"], "volatility": vlt["score"], "liquidity": liq["score"],
    }

    # ── Gate: freshness ──
    stale = False
    bd = _bar_date(bars[-1])
    if profile["interval"] == "day" and bd:
        age = (now.date() - bd).days
        if age > profile["stale_days"]:
            stale = True
            warnings.append(f"Data stale ({age} sessions old)")

    # ── Gate: liquidity floor ──
    if not liq["passes_floor"]:
        blockers.append("Below liquidity floor")

    # ── Gate: volatility computable ──
    if vlt["regime"] == "unavailable" or not vlt.get("atr"):
        return {**base, "state": STATE_UNAVAILABLE, "direction": direction,
                "reason": "Volatility (ATR) not computable"}
    atr = float(vlt["atr"])

    # ── Research plan (hypotheses only) ──
    if dsign > 0:
        invalidation = round(close - profile["stop_atr"] * atr, 2)
        target1 = round(close + profile["target_atr"] * atr, 2)
    else:
        invalidation = round(close + profile["stop_atr"] * atr, 2)
        target1 = round(close - profile["target_atr"] * atr, 2)
    risk_per_unit = abs(close - invalidation)
    reward = abs(target1 - close)
    rr = round(reward / risk_per_unit, 2) if risk_per_unit > 0 else None

    if rr is None or risk_per_unit <= 0:
        return {**base, "state": STATE_UNAVAILABLE, "direction": direction,
                "reason": "Thesis invalidation not computable"}
    if rr < min_rr:
        warnings.append(f"Reward-to-risk {rr} below {min_rr}")

    # ── Composite score (weighted, quality-capped) ──
    weights = cfg["weights"]
    tot_w = sum(max(0.0, float(weights.get(c, 0))) for c in COMPONENTS) or 1.0
    raw = sum(comp[c] * max(0.0, float(weights.get(c, 0))) for c in COMPONENTS) / tot_w
    quality_cap = 0.85 if stale else 1.0
    score = round(_clamp(raw * quality_cap), 1)

    # ── Confidence (separate from score) ──
    align = 1.0 if ((tr["bias"] == "bullish") == (dsign > 0) and
                    ((rs.get("excess") or 0) >= 0) == (dsign > 0)) else 0.85
    vol_pen = 0.8 if vlt["regime"] == "extreme" else 1.0
    completeness = min(1.0, len(bars) / (profile["min_bars"] * 1.5))
    confidence = round(_clamp(100 * quality_cap * align * vol_pen * (0.7 + 0.3 * completeness)), 1)

    # ── Risk grade ──
    grade = _risk_grade(vlt, liq, tr, stale)

    # ── State ──
    if blockers:
        state, reason = STATE_RESTRICTED, "; ".join(blockers)
    elif rr < min_rr:
        state, reason = STATE_WARNING, "; ".join(warnings)
    elif warnings:
        state, reason = STATE_WARNING, "; ".join(warnings)
    else:
        state, reason = STATE_ELIGIBLE, ""

    # ── Evidence (supporting = agrees with direction; opposing = against) ──
    all_ev = tr["evidence"] + rs["evidence"] + vol["evidence"] + vlt["evidence"] + liq["evidence"]
    supporting = [e["text"] for e in all_ev if e["dir"] == dsign]
    opposing = [e["text"] for e in all_ev if e["dir"] == -dsign]

    # ── Market context (breadth + PCR) adjusts CONFIDENCE only (score stays
    # a function of the instrument's own features). Explainable, bounded. ──
    if mctx and mctx.get("bias") in ("bullish", "bearish"):
        aligned = ((mctx["bias"] == "bullish") == (dsign > 0))
        reg = mctx.get("regime", "")
        if aligned:
            confidence = round(_clamp(confidence + 4))
            supporting.append(f"Market breadth ({reg}) supports {direction}")
        else:
            confidence = round(_clamp(confidence - 6))
            opposing.append(f"Market breadth ({reg}) counters {direction}")
    band_label, band_color = score_band(score)

    explanation = _explain(symbol, direction, cfg["horizon"], state, score,
                           band_label, supporting, opposing, rr, grade, stale)

    return {
        **base, "state": state, "reason": reason, "direction": direction,
        "score": score, "band": band_label, "band_color": band_color,
        "confidence": confidence, "risk_grade": grade, "risk_color": RISK_COLORS[grade],
        "indicative_entry": round(close, 2), "invalidation": invalidation,
        "first_target": target1, "reward_to_risk": rr,
        "atr": round(atr, 3), "atr_pct": vlt["atr_pct"], "vol_regime": vlt["regime"],
        "rel_strength_excess": rs.get("excess"), "rel_volume": vol.get("rel_volume"),
        "median_value": liq.get("median_value"), "sector": None,
        "components": comp, "supporting": supporting[:6], "opposing": opposing[:6],
        "warnings": warnings, "explanation": explanation, "fresh": not stale,
    }


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _risk_grade(vlt, liq, tr, stale) -> str:
    pts = 0
    pts += {"compressed": 0, "normal": 0, "elevated": 1, "extreme": 3, "unavailable": 2}.get(vlt["regime"], 1)
    if not liq["passes_floor"]:
        pts += 2
    elif liq.get("continuity", 1) < 0.9:
        pts += 1
    if abs(tr.get("extension_atr", 0)) > 3:
        pts += 1
    if stale:
        pts += 1
    idx = min(pts, 3)
    return RISK_GRADES[idx]


def _explain(symbol, direction, horizon, state, score, band, supporting, opposing, rr, grade, stale) -> str:
    lead = (f"{band} {horizon} {direction} research thesis for {symbol} "
            f"(score {score}, {grade.lower()} analytical risk, R:R {rr}).")
    body = ""
    if supporting:
        body += " Supported by: " + "; ".join(supporting[:3]) + "."
    if opposing:
        body += " Countered by: " + "; ".join(opposing[:2]) + "."
    if stale:
        body += " Data is stale — confidence is capped."
    return lead + body
