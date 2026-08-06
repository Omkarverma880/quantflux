"""
QMIE component engines — deterministic, explainable domain scorers (§13–§19).

Each engine returns a long-oriented component score (0–100, higher = more
constructive for a LONG thesis) plus structured evidence. Direction-neutral
engines (volume, volatility, liquidity) return a quality score. The scanner
composes validated outputs; no engine promotes a candidate or places an order.
"""
from __future__ import annotations

from research.qmie import indicators as ind


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _ev(text, direction):        # direction: +1 supports long, -1 opposes
    return {"text": text, "dir": direction}


def trend_engine(bars: list[dict], profile: dict) -> dict:
    cl = ind.closes(bars)
    e20, e50, e200 = ind.ema(cl, 20), ind.ema(cl, 50), ind.ema(cl, 200)
    lookback = profile["lookback"]
    slope = ind.slope_pct(cl, min(lookback, len(cl) - 1))
    st = ind.structure(bars, lookback)
    a = ind.atr(bars, profile["atr_period"]) or (cl[-1] * 0.02)
    ext = (cl[-1] - (e50 or cl[-1])) / a if a else 0.0

    s, ev = 50.0, []
    bias = "neutral"
    if e20 and e50 and e200:
        if e20 > e50 > e200:
            s += 22; bias = "bullish"; ev.append(_ev("Bullish EMA stack (20>50>200)", +1))
        elif e20 < e50 < e200:
            s -= 22; bias = "bearish"; ev.append(_ev("Bearish EMA stack (20<50<200)", -1))
        else:
            ev.append(_ev("Mixed EMA structure", 0))
    if slope > 0:
        s += 10; ev.append(_ev(f"Rising price slope ({slope:.2f}%/bar)", +1))
    elif slope < 0:
        s -= 10; ev.append(_ev(f"Falling price slope ({slope:.2f}%/bar)", -1))
    if st["bullish"]:
        s += 10; ev.append(_ev("Higher-high / higher-low structure", +1))
    elif st["bearish"]:
        s -= 10; ev.append(_ev("Lower-high / lower-low structure", -1))
    if abs(ext) > 3:            # very extended → poorer trend-entry quality
        s -= 8; ev.append(_ev(f"Price extended {ext:.1f} ATR from 50-EMA", -1))
    return {"score": round(_clamp(s), 1), "bias": bias, "slope": round(slope, 3),
            "extension_atr": round(ext, 2), "swing_high": st["swing_high"],
            "swing_low": st["swing_low"], "evidence": ev}


def relative_strength_engine(bars: list[dict], bench: list[dict], profile: dict) -> dict:
    cl, bc = ind.closes(bars), ind.closes(bench)
    w = min(profile["lookback"], len(cl) - 1, len(bc) - 1)
    if w < 5:
        return {"score": 50.0, "excess": None, "evidence": [_ev("Insufficient history for RS", 0)]}
    inst_ret = ind.cum_return(cl, w) or 0.0
    bench_ret = ind.cum_return(bc, w) or 0.0
    excess = inst_ret - bench_ret
    # slope of the price/benchmark ratio over the window
    ratio = [cl[-i] / bc[-i] for i in range(1, w + 1) if bc[-i]]
    ratio.reverse()
    ratio_slope = ind.slope_pct(ratio, len(ratio) - 1) if len(ratio) > 2 else 0.0

    s, ev = 50.0, []
    s += _clamp(excess * 2.0, -30, 30)     # ±15% excess → ±30 pts
    if excess >= 0:
        ev.append(_ev(f"Outperforming benchmark by {excess:.1f}% ({w} bars)", +1))
    else:
        ev.append(_ev(f"Lagging benchmark by {abs(excess):.1f}% ({w} bars)", -1))
    if ratio_slope > 0:
        s += 8; ev.append(_ev("Improving relative strength", +1))
    elif ratio_slope < 0:
        s -= 8; ev.append(_ev("Deteriorating relative strength", -1))
    return {"score": round(_clamp(s), 1), "excess": round(excess, 2),
            "ratio_slope": round(ratio_slope, 3), "evidence": ev}


def volume_engine(bars: list[dict], profile: dict) -> dict:
    vols = ind.volumes(bars)
    if len(vols) < 21:
        return {"score": 50.0, "rel_volume": None, "evidence": [_ev("Insufficient volume history", 0)]}
    base = ind.median(vols[-21:-1]) or 0.0
    cur = vols[-1]
    rel = (cur / base) if base else 1.0
    s, ev = 48.0, []
    if rel >= 2.0:
        s += 26; ev.append(_ev(f"Volume surge {rel:.1f}× median", +1))
    elif rel >= 1.3:
        s += 14; ev.append(_ev(f"Above-average volume {rel:.1f}×", +1))
    elif rel < 0.6:
        s -= 12; ev.append(_ev("Volume dry-up", -1))
    # persistence: fraction of last 5 bars above baseline
    if base:
        persist = sum(1 for v in vols[-5:] if v > base) / 5.0
        if persist >= 0.6:
            s += 8; ev.append(_ev("Sustained participation", +1))
    return {"score": round(_clamp(s), 1), "rel_volume": round(rel, 2), "evidence": ev}


def volatility_engine(bars: list[dict], profile: dict) -> dict:
    cl = ind.closes(bars)
    a = ind.atr(bars, profile["atr_period"])
    if not a or cl[-1] == 0:
        return {"score": 50.0, "atr": a, "atr_pct": None, "regime": "unavailable",
                "evidence": [_ev("ATR unavailable", 0)]}
    atr_pct = a / cl[-1] * 100.0
    # percentile of ATR% over history to classify regime
    hist = []
    for i in range(profile["atr_period"] + 1, len(bars)):
        sub = bars[max(0, i - profile["atr_period"] - 1):i + 1]
        av = ind.atr(sub, profile["atr_period"])
        c = float(bars[i]["close"])
        if av and c:
            hist.append(av / c * 100.0)
    pctile = ind.percentile_of(atr_pct, hist) if hist else 50.0
    if pctile is None:
        pctile = 50.0
    if pctile < 20:
        regime = "compressed"
    elif pctile < 60:
        regime = "normal"
    elif pctile < 85:
        regime = "elevated"
    else:
        regime = "extreme"
    # middle-preference fit: normal/elevated are best for a directional swing
    fit = {"compressed": 60, "normal": 85, "elevated": 75, "extreme": 40}[regime]
    ev = [_ev(f"Volatility regime: {regime} (ATR {atr_pct:.1f}%, {pctile:.0f}th pct)",
              +1 if regime in ("normal", "elevated") else -1)]
    return {"score": round(float(fit), 1), "atr": round(a, 3), "atr_pct": round(atr_pct, 2),
            "regime": regime, "percentile": round(pctile, 1), "evidence": ev}


def breadth_engine(all_bars: list[list[dict]]) -> dict:
    """Market participation from the scanned universe (§27) — advancing/declining
    and % above the 50-EMA. Computed once per scan from data we already have."""
    adv = dec = above = total = 0
    for bars in all_bars:
        if len(bars) < 51:
            continue
        cl = ind.closes(bars)
        if cl[-1] > cl[-2]:
            adv += 1
        elif cl[-1] < cl[-2]:
            dec += 1
        e50 = ind.ema(cl, 50)
        if e50 and cl[-1] > e50:
            above += 1
        total += 1
    if total == 0:
        return {"regime": "unavailable", "total": 0, "bias": "neutral"}
    ad_pct = adv / total * 100.0
    pct_above = above / total * 100.0
    if ad_pct >= 60 and pct_above >= 60:
        regime, bias = "broad_risk_on", "bullish"
    elif ad_pct <= 40 and pct_above <= 40:
        regime, bias = "broad_risk_off", "bearish"
    elif pct_above >= 55:
        regime, bias = "constructive", "bullish"
    elif pct_above <= 45:
        regime, bias = "deteriorating", "bearish"
    else:
        regime, bias = "mixed", "neutral"
    return {"advancing": adv, "declining": dec, "total": total,
            "ad_ratio": round(adv / max(1, dec), 2), "ad_pct": round(ad_pct, 1),
            "pct_above_ema50": round(pct_above, 1), "regime": regime, "bias": bias}


def liquidity_engine(bars: list[dict], floor: float, profile: dict) -> dict:
    cl, vols = ind.closes(bars), ind.volumes(bars)
    tv = [cl[i] * vols[i] for i in range(len(bars))]
    med = ind.median(tv[-21:]) if len(tv) >= 21 else ind.median(tv)
    med = med or 0.0
    passes = med >= floor
    # continuity: fraction of recent sessions with volume
    cont = sum(1 for v in vols[-21:] if v > 0) / max(1, len(vols[-21:]))
    s = _clamp((med / floor) * 45.0, 0, 80) if floor else 50.0
    s += cont * 20.0
    ev = [_ev(f"Median traded value ₹{med/1e7:.1f} Cr ({'≥' if passes else '<'} floor)",
              +1 if passes else -1)]
    if cont < 0.9:
        ev.append(_ev("Irregular trading continuity", -1))
    return {"score": round(_clamp(s), 1), "median_value": round(med, 0),
            "passes_floor": passes, "continuity": round(cont, 2), "evidence": ev}
