"""
OPEI confluence scoring engine + entry-level generator.

Pure and deterministic: it takes a *feature dict* (built by the service from
live Zerodha data + the indicator library) and produces, for one option side:

  • a per-category confluence score (0–100) with the reasons that fired,
  • a weighted overall probability score (0–100) + band,
  • the top-N premium ENTRY LEVELS above the current premium, each with a
    confidence, expected move, SL, targets, trailing SL and risk rating.

Weights are configurable (never equal). This is a weighted confluence model,
not a rule engine — every bucket contributes proportionally. Historical
success % is a model estimate until enough logged outcomes accrue (see log.py).
"""
from __future__ import annotations

from research.opei.constants import CATEGORIES, band_label, band_color


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# ── per-category scorers → (score 0-100, [reasons]) ───────────────
def _score_trend(f: dict):
    s, r = 50.0, []
    e9, e20, e50 = f.get("ema9"), f.get("ema20"), f.get("ema50")
    if e9 and e20 and e50:
        if e9 > e20 > e50:
            s += 22; r.append("EMA Alignment (9>20>50)")
        elif e9 < e20 < e50:
            s -= 18
    if f.get("premium_slope", 0) > 0:
        s += 12; r.append("Premium Trend Up")
    pa = f.get("pa", {})
    if pa.get("higher_high") and pa.get("higher_low"):
        s += 10; r.append("Higher-High / Higher-Low")
    if pa.get("outside_bar") and pa.get("bullish"):
        s += 6; r.append("Bullish Outside Bar")
    sw = f.get("swing", {})
    if sw.get("swing_high") and f.get("premium", 0) >= sw["swing_high"] * 0.999:
        s += 8; r.append("Break of Structure")
    if pa.get("body_strength", 0) >= 65 and pa.get("bullish"):
        s += 4; r.append("Strong Candle Body")
    return _clamp(s), r


def _score_momentum(f: dict):
    s, r = 50.0, []
    rsi = f.get("rsi")
    if rsi is not None:
        if 55 <= rsi <= 72:
            s += 16; r.append(f"RSI Strong ({rsi})")
        elif rsi > 80:
            s -= 8
        elif rsi < 40:
            s -= 12
    if f.get("macd_hist", 0) > 0:
        s += 12; r.append("MACD Positive")
    if f.get("roc", 0) > 0:
        s += 8; r.append("Positive ROC")
    adx = f.get("adx")
    if adx is not None and adx >= 22:
        s += 12; r.append(f"Trend Strength ADX {adx}")
    return _clamp(s), r


def _score_vwap(f: dict):
    s, r = 50.0, []
    p, vw = f.get("premium"), f.get("vwap")
    if p and vw:
        if p > vw:
            s += 24; r.append("Premium VWAP Reclaim")
            if (p - vw) / vw < 0.02:
                s += 6; r.append("VWAP Retest")
        else:
            s -= 16; r.append("Below Premium VWAP")
    return _clamp(s), r


def _score_volume(f: dict):
    s, r = 45.0, []
    rv = f.get("rel_volume", 1.0)
    if rv >= 2.0:
        s += 26; r.append(f"Volume Spike ({rv:.1f}x)")
    elif rv >= 1.3:
        s += 14; r.append(f"Above-avg Volume ({rv:.1f}x)")
    elif rv < 0.6:
        s -= 12; r.append("Volume Dry-up")
    if f.get("vol_expansion"):
        s += 8; r.append("Volume Expansion")
    return _clamp(s), r


def _score_oi(f: dict):
    s, r = 50.0, []
    bu = f.get("buildup")
    side = f.get("side")
    # For a CALL, bullish-for-premium OI = Long Buildup or Short Covering.
    good = {"Long Buildup", "Short Covering"}
    bad = {"Long Unwinding", "Short Buildup"}
    if bu in good:
        s += 20; r.append(f"OI {bu}")
    elif bu in bad:
        s -= 12; r.append(f"OI {bu}")
    pcr = f.get("pcr")
    if pcr is not None:
        # High PCR = put-heavy = supportive for CE premium; low PCR for PE.
        if side == "CE" and pcr >= 1.1:
            s += 8; r.append(f"PCR bullish ({pcr})")
        elif side == "PE" and pcr <= 0.9:
            s += 8; r.append(f"PCR bearish ({pcr})")
    return _clamp(s), r


def _score_volatility(f: dict):
    s, r = 48.0, []
    if f.get("vix_change", 0) > 0:
        s += 14; r.append("India VIX Rising")
    if f.get("atr_expansion"):
        s += 16; r.append("ATR Expansion")
    ivr = f.get("iv_rank")
    if ivr is not None and ivr < 40:
        s += 8; r.append("Low IV Rank (expansion room)")
    return _clamp(s), r


def _score_liquidity(f: dict):
    s, r = 50.0, []
    sp = f.get("spread_pct")
    if sp is not None:
        if sp < 0.3:
            s += 12; r.append("Tight Spread")
        elif sp > 1.5:
            s -= 14; r.append("Wide Spread")
    imb = f.get("depth_imbalance")     # 0..1 fraction of buy depth
    if imb is not None:
        if imb >= 0.6:
            s += 16; r.append("Bid-side Absorption")
        elif imb <= 0.4:
            s -= 10
    return _clamp(s), r


def _score_breadth(f: dict):
    s, r = 50.0, []
    b = f.get("breadth", 0.0)          # weighted %chg of top-10 (signed)
    side = f.get("side")
    aligned = b if side == "CE" else -b
    if aligned >= 0.35:
        s += 24; r.append("Market Breadth Aligned")
    elif aligned >= 0.1:
        s += 12
    elif aligned <= -0.35:
        s -= 18; r.append("Breadth Against")
    return _clamp(s), r


def _score_premium_structure(f: dict):
    s, r = 48.0, []
    bb = f.get("bb", {})
    pct_b = bb.get("pct_b", 0.5)
    if pct_b >= 0.8:
        s += 18; r.append("Near Upper Band (breakout)")
    dc = f.get("donchian", {})
    p = f.get("premium")
    if dc.get("upper") and p and p >= dc["upper"] * 0.998:
        s += 16; r.append("Premium Breakout (Donchian)")
    if f.get("compression_then_expansion"):
        s += 12; r.append("Compression → Expansion")
    return _clamp(s), r


def _score_time(f: dict):
    s, r = 50.0, []
    sess = f.get("session")
    if sess == "opening":
        s += 12; r.append("Opening Session Momentum")
    elif sess == "closing":
        s += 8; r.append("Closing Session")
    elif sess == "lunch":
        s -= 12; r.append("Lunch Slowdown")
    if f.get("expiry_day"):
        s += 8; r.append("Expiry-day Volatility")
    return _clamp(s), r


_SCORERS = {
    "trend": _score_trend, "momentum": _score_momentum, "vwap": _score_vwap,
    "volume": _score_volume, "oi": _score_oi, "volatility": _score_volatility,
    "liquidity": _score_liquidity, "breadth": _score_breadth,
    "premium_structure": _score_premium_structure, "time": _score_time,
}


def score_side(feat: dict, weights: dict) -> dict:
    cats: dict = {}
    reasons: list[str] = []
    tot_w = sum(max(0.0, float(weights.get(c, 0))) for c in CATEGORIES) or 1.0
    overall = 0.0
    for c in CATEGORIES:
        sc, rs = _SCORERS[c](feat)
        w = max(0.0, float(weights.get(c, 0)))
        cats[c] = {"score": round(sc, 1), "weight": round(w, 1), "reasons": rs}
        overall += sc * w
        reasons.extend(rs)
    overall = float(round(overall / tot_w))     # integer → stops the score flickering
    return {"overall": overall, "band": band_label(overall), "color": band_color(overall),
            "categories": cats, "reasons": reasons}


# ── entry-level generator ─────────────────────────────────────────
def _round_half(x: float) -> float:
    """Snap to the nearest 0.5 so levels don't jitter on tiny tick moves."""
    return round(x * 2.0) / 2.0


def generate_levels(feat: dict, overall: float, cfg: dict, anchor: float | None = None) -> list[dict]:
    # Anchor the ladder to the last COMPLETED candle close (stable within the
    # candle) — not the live tick — so the recommended levels stop changing every
    # second. They now only step when a new candle closes.
    base = float(anchor if anchor else (feat.get("premium") or 0))
    p = float(feat.get("premium") or base)     # live premium (for confidence/active only)
    a = float(feat.get("atr") or 0) or max(base * 0.02, 0.5)
    if base <= 0:
        return []
    mults = cfg.get("level_atr_mult") or [0.6, 1.2, 2.0, 3.0, 4.5]
    n = int(cfg.get("num_levels", 5))
    vw = float(feat.get("vwap") or base)
    levels = []
    for i, m in enumerate(mults[:n]):
        lvl = _round_half(base + m * a)
        dist = (lvl - base) / base
        # confidence: overall, decayed by distance and ladder index (integer → stable)
        conf = round(_clamp(overall - dist * 60 - i * 3, 5, 99))
        move_pct = round((a * (1.5 + m) / lvl) * 100.0, 1)
        sl = _round_half(max(vw - a * 0.5, base - a * 0.9, base * 0.5))
        risk = "Low" if dist < 0.06 else ("Medium" if dist < 0.15 else "High")
        # holding time estimate from premium velocity (atr per bar → bars to target)
        vel = abs(float(feat.get("roc", 0))) or 1.0
        hold_min = int(_clamp((m * a) / (max(vel, 0.5) / 100.0 * base) * float(cfg.get("_tf_min", 5)), 3, 240))
        levels.append({
            "level": lvl,
            "confidence": conf,
            "expected_move_pct": move_pct,
            "expected_momentum": "Explosive" if conf >= 90 else ("Strong" if conf >= 80 else "Moderate"),
            "risk": risk,
            "historical_success_pct": round(_clamp(conf * 0.82 + 8, 5, 96), 1),   # model estimate
            "expected_hold_min": hold_min,
            "sl": sl,
            "targets": [_round_half(lvl + a), _round_half(lvl + 2 * a), _round_half(lvl + 3 * a)],
            "trailing_sl": _round_half(a * 0.5),
            "band": band_label(conf), "color": band_color(conf),
        })
    if levels:
        best = max(range(len(levels)), key=lambda i: levels[i]["confidence"])
        levels[best]["is_best"] = True
    return levels


def evaluate_side(feat: dict, weights: dict, cfg: dict,
                  bias_dir: str = "neutral", bias_strength: float = 0.0) -> dict:
    """Score one side and apply the market directional bias.

    CE and PE can't both be high-probability explosive-UP at the same moment —
    the underlying can only move one way. So when there is a clear bias we
    SUPPRESS the counter-trend side (a CALL when the market is bearish, or a PUT
    when bullish), so only the aligned side is recommended."""
    scored = score_side(feat, weights)
    overall = scored["overall"]
    side = feat.get("side")
    aligned = (bias_dir == "neutral"
               or (bias_dir == "bullish" and side == "CE")
               or (bias_dir == "bearish" and side == "PE"))
    if not aligned and bias_dir != "neutral":
        overall = float(round(overall * (1.0 - 0.45 * float(bias_strength))))
        scored["reasons"] = ["⚠ Counter-trend to market bias"] + scored.get("reasons", [])
    scored["overall"] = overall
    scored["band"] = band_label(overall)
    scored["color"] = band_color(overall)
    scored["aligned"] = aligned

    levels = generate_levels(feat, overall, {**cfg}, anchor=feat.get("anchor"))
    # entry-active: has live premium crossed any recommended level?
    active = None
    p = float(feat.get("premium") or 0)
    for lv in levels:
        if p >= lv["level"]:
            active = lv
    return {**scored, "premium": round(p, 2), "levels": levels, "entry_active": active}
