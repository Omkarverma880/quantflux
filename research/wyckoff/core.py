"""
The Wyckoff engine — pure, instrument-agnostic structural analysis.

Give it OHLCV candles (a stock, an index, a future, even an option premium) and
it returns the Wyckoff read: the trading range, the events inside it (SC, AR,
ST, Spring, SOS, LPS / BC, AR, ST, UTAD, SOW, LPSY), the phase, Wyckoff's nine
buying / selling tests, the effort-vs-result verdict, and — the part that
matters at the desk — a plain statement of **when to enter and when to stay
out**, with the level that invalidates the read.

The three laws drive everything here:

  1. Supply & Demand   → where price sits inside the range, and which side of it
                         gets rejected.
  2. Cause & Effect    → the width × duration of the range is the cause; a range
                         too small to matter never earns an entry call.
  3. Effort vs Result  → volume is effort, bar spread is result. High effort with
                         no result at a range edge is absorption — the single
                         most useful tell Wyckoff left us.

No I/O, no state, no orders. Every number it returns is derived from the candles
you passed in.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

# ── event catalogue (also drives the UI legend) ──────────────────────
EVENTS: dict[str, dict] = {
    "PS":   {"name": "Preliminary Support", "side": "accum",
             "meaning": "First serious buying inside a decline — volume widens, the fall slows."},
    "SC":   {"name": "Selling Climax", "side": "accum",
             "meaning": "Panic low on the heaviest volume. Supply exhausts; the composite operator absorbs it."},
    "AR":   {"name": "Automatic Rally", "side": "accum",
             "meaning": "Supply is spent, so price springs back. Its high sets the top of the range."},
    "ST":   {"name": "Secondary Test", "side": "accum",
             "meaning": "Price returns to the climax low on LOWER volume — proof that sellers are gone."},
    "SPRING": {"name": "Spring / Shakeout", "side": "accum",
               "meaning": "A poke below the range low that snaps back inside — breakout sellers get trapped. The highest-conviction Wyckoff entry."},
    "TEST": {"name": "Test of Spring", "side": "accum",
             "meaning": "A higher low on light volume after the spring. This is the confirmation to act on."},
    "SOS":  {"name": "Sign of Strength", "side": "accum",
             "meaning": "Wide-spread advance closing near its high on expanding volume — demand is now in charge."},
    "LPS":  {"name": "Last Point of Support", "side": "accum",
             "meaning": "The pullback after the SOS holds above the old resistance on drying volume. The conservative entry."},
    "PSY":  {"name": "Preliminary Supply", "side": "distrib",
             "meaning": "First significant selling into a rise — the advance starts to labour."},
    "BC":   {"name": "Buying Climax", "side": "distrib",
             "meaning": "Euphoric high on the heaviest volume. Demand exhausts into supply."},
    "ARD":  {"name": "Automatic Reaction", "side": "distrib",
             "meaning": "Demand is spent, so price drops. Its low sets the bottom of the range."},
    "STD":  {"name": "Secondary Test", "side": "distrib",
             "meaning": "Price returns to the climax high on lower volume — buyers can no longer follow through."},
    "UTAD": {"name": "Upthrust After Distribution", "side": "distrib",
             "meaning": "A poke above the range high that fails back inside — breakout buyers get trapped. The bear-side mirror of the spring."},
    "SOW":  {"name": "Sign of Weakness", "side": "distrib",
             "meaning": "Wide-spread decline closing near its low on expanding volume — supply is in charge."},
    "LPSY": {"name": "Last Point of Supply", "side": "distrib",
             "meaning": "The weak rally after the SOW fails below the old support on thin volume. The short entry."},
}

PHASES: dict[str, str] = {
    "A": "Phase A — the trend is being stopped (climax, automatic reaction, secondary test).",
    "B": "Phase B — building the cause. Price oscillates while stock changes hands. No edge here.",
    "C": "Phase C — the test: spring (accumulation) or upthrust (distribution). Where the entry is set up.",
    "D": "Phase D — the trend inside the range asserts itself: SOS/LPS up, or SOW/LPSY down.",
    "E": "Phase E — the mark-up / mark-down is under way outside the range.",
    "?": "No usable Wyckoff structure — price is trending or the range is too immature to read.",
}

BUY_TESTS = [
    ("downside_objective", "Downside objective reached"),
    ("climax_support", "Preliminary support + selling climax"),
    ("secondary_test", "Secondary test on lower volume"),
    ("activity_bullish", "Activity bullish (volume up on rallies)"),
    ("stride_broken", "Downward stride broken"),
    ("higher_lows", "Higher lows"),
    ("higher_highs", "Higher highs"),
    ("spring_present", "Spring / terminal shakeout"),
    ("cause_built", "Base built (enough cause)"),
]

SELL_TESTS = [
    ("upside_objective", "Upside objective reached"),
    ("climax_supply", "Preliminary supply + buying climax"),
    ("secondary_test", "Secondary test on lower volume"),
    ("activity_bearish", "Activity bearish (volume up on declines)"),
    ("stride_broken", "Upward stride broken"),
    ("lower_highs", "Lower highs"),
    ("lower_lows", "Lower lows"),
    ("upthrust_present", "Upthrust / UTAD"),
    ("cause_built", "Top built (enough cause)"),
]


# ── small pure helpers ───────────────────────────────────────────────
def _f(c: dict, k: str) -> float:
    return float(c[k])


def _vol(c: dict) -> float:
    return float(c.get("volume", 0) or 0)


def _dt(c: dict) -> Optional[datetime]:
    v = c.get("_dt") or c.get("date")
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v).replace(tzinfo=None)
        except Exception:
            return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return None


def _label(c: dict) -> str:
    dt = _dt(c)
    if not dt:
        return ""
    return dt.strftime("%d-%b") if dt.hour == 0 and dt.minute == 0 else dt.strftime("%d-%b %H:%M")


def sma(values: list[float], period: int) -> list[float]:
    out, run = [], 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        out.append(run / min(i + 1, period))
    return out


def atr_series(candles: list[dict], period: int) -> list[float]:
    out, prev_close, run = [], None, []
    for c in candles:
        h, l = _f(c, "high"), _f(c, "low")
        tr = h - l if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        run.append(tr)
        if len(run) > period:
            run.pop(0)
        out.append(sum(run) / len(run))
        prev_close = _f(c, "close")
    return out


def pivots(candles: list[dict], k: int) -> tuple[list[int], list[int]]:
    """Fractal swing highs / lows with ``k`` bars confirming each side."""
    highs, lows = [], []
    n = len(candles)
    for i in range(k, n - k):
        h, l = _f(candles[i], "high"), _f(candles[i], "low")
        if all(h >= _f(candles[j], "high") for j in range(i - k, i + k + 1) if j != i):
            highs.append(i)
        if all(l <= _f(candles[j], "low") for j in range(i - k, i + k + 1) if j != i):
            lows.append(i)
    return highs, lows


def _close_pos(c: dict) -> float:
    """Where the close sits inside its own bar, 0 (low) … 1 (high)."""
    h, l, cl = _f(c, "high"), _f(c, "low"), _f(c, "close")
    rng = h - l
    return 0.5 if rng <= 0 else (cl - l) / rng


def _pct(a: float, b: float) -> float:
    return 0.0 if not b else (a - b) / b * 100.0


# ── the analysis ─────────────────────────────────────────────────────
def analyze(candles: list[dict], cfg: dict, *, oi_series: Optional[list[float]] = None,
            instrument: str = "", kind: str = "equity") -> dict:
    """Full Wyckoff read for one instrument. ``kind`` only colours the wording
    (equity / index / option / future); the structural maths is identical."""
    n = len(candles)
    look = int(cfg["lookback"])
    if n < max(30, int(cfg["min_range_bars"]) * 2):
        return _empty(instrument, kind, f"only {n} candles — need at least "
                                        f"{max(30, int(cfg['min_range_bars']) * 2)}")

    off = max(0, n - look)
    win = candles[off:]
    m = len(win)
    highs = [_f(c, "high") for c in win]
    lows = [_f(c, "low") for c in win]
    closes = [_f(c, "close") for c in win]
    opens = [_f(c, "open") for c in win]
    vols = [_vol(c) for c in win]
    atr = atr_series(win, int(cfg["atr_period"]))
    vma = sma(vols, int(cfg["vol_ma_period"]))
    has_volume = sum(vols) > 0

    last = m - 1
    ltp = closes[last]

    # Which structure is on the table? Anchor on the extreme that price moved
    # INTO and then stopped at — and look for it only in the part of the window
    # old enough to have built a range behind it, because a spring (or an
    # upthrust) deliberately breaks the extreme late in the story.
    min_bars = int(cfg["min_range_bars"])
    limit = max(3, m - min_bars)
    span = max(highs) - min(lows) or 1e-9
    low_band = min(lows) + span * 0.33
    high_band = max(highs) - span * 0.33
    lo_cands = [i for i in range(limit) if lows[i] <= low_band] or [
        min(range(limit), key=lambda i: lows[i])]
    hi_cands = [i for i in range(limit) if highs[i] >= high_band] or [
        max(range(limit), key=lambda i: highs[i])]
    # The climax is the bar that did the work, not the lowest tick: pick the
    # heaviest-volume visit to the extreme band (and, with no volume, the FIRST
    # visit — a later poke at the same level is a test or a spring, not the climax).
    if has_volume:
        anchor_low = max(lo_cands, key=lambda i: (vols[i], -i))
        anchor_high = max(hi_cands, key=lambda i: (vols[i], -i))
    else:
        anchor_low, anchor_high = lo_cands[0], hi_cands[0]
    # The more RECENT anchor is the one the market has been working off: a low
    # after a high means we declined into a base; a high after a low means we
    # advanced into a top.
    side = "accum" if anchor_low > anchor_high else "distrib"
    anchor = anchor_low if side == "accum" else anchor_high

    ctx = {"win": win, "highs": highs, "lows": lows, "closes": closes, "opens": opens,
           "vols": vols, "atr": atr, "vma": vma, "m": m, "last": last, "ltp": ltp,
           "cfg": cfg, "has_volume": has_volume}

    if side == "accum":
        struct = _accumulation(ctx, anchor)
    else:
        struct = _distribution(ctx, anchor)

    struct["events"].sort(key=lambda e: (e["idx"], e["type"]))
    # keep the two most recent secondary tests — the rest is noise on the chart
    for st in ("ST", "STD"):
        sts = [e for e in struct["events"] if e["type"] == st]
        for e in sts[:-2]:
            struct["events"].remove(e)

    rng = struct["range"]
    # Law of cause and effect: a "range" price barely respects is a trend, not a
    # range, and Wyckoff has nothing to say about it.
    span = range(struct["key"].get("sc", struct["key"].get("bc", 0)), m)
    inside = sum(1 for i in span if rng["low"] <= closes[i] <= rng["high"])
    contained = inside / max(1, len(list(span)))
    if contained < 0.5:
        return _empty(instrument, kind,
                      "price is trending — it spends too little time inside any range to read structurally",
                      series=_series(win, cfg), range_hint={"high": max(highs), "low": min(lows)})

    events = struct["events"]
    ev_types = {e["type"] for e in events}

    effort = _effort_result(ctx, rng)
    tests = _nine_tests(ctx, side, struct)
    oi = _oi_read(ctx, oi_series, cfg) if oi_series else None
    phase, phase_note = _phase(side, ev_types, ctx, rng)
    guidance = _guidance(side, phase, ev_types, ctx, rng, tests, effort, oi, struct, kind)

    return {
        "status": "ok", "instrument": instrument, "kind": kind, "bars": m,
        "side": side, "bias": struct["bias"], "phase": phase,
        "phase_label": PHASES.get(phase, ""), "phase_note": phase_note,
        "range": rng, "events": events, "tests": tests, "effort": effort, "oi": oi,
        "guidance": guidance, "ltp": round(ltp, 2), "has_volume": has_volume,
        "series": _series(win, cfg, events=events),
    }


def _empty(instrument: str, kind: str, why: str, series=None, range_hint=None) -> dict:
    return {"status": "ok", "instrument": instrument, "kind": kind, "bars": 0,
            "side": None, "bias": "NO STRUCTURE", "phase": "?", "phase_label": PHASES["?"],
            "phase_note": why, "range": range_hint or {}, "events": [], "tests": None,
            "effort": None, "oi": None, "series": series or [],
            "guidance": {"action": "AVOID", "confidence": 0, "headline": "No Wyckoff structure",
                         "reasons": [], "avoid": [why], "invalidation": None, "trigger": None,
                         "risk": "n/a"}}


# ── accumulation structure ───────────────────────────────────────────
def _accumulation(ctx: dict, ll: int) -> dict:
    cfg, win, m, last = ctx["cfg"], ctx["win"], ctx["m"], ctx["last"]
    highs, lows, closes, vols, atr, vma = (ctx["highs"], ctx["lows"], ctx["closes"],
                                           ctx["vols"], ctx["atr"], ctx["vma"])
    events: list[dict] = []

    # SC — the climax bar at (or within two bars of) the low, heaviest volume there.
    lo_i = max(0, ll - 2)
    hi_i = min(m - 1, ll + 2)
    sc = max(range(lo_i, hi_i + 1), key=lambda i: vols[i]) if ctx["has_volume"] else ll
    climactic = (not ctx["has_volume"]) or (vols[sc] >= vma[sc] * float(cfg["climax_vol_mult"]))
    events.append(_ev("SC", sc, win, lows[sc],
                      "climactic volume" if climactic else "low of the range (volume not climactic)"))

    # PS — an earlier high-volume down bar before the climax.
    ps = None
    for i in range(max(0, sc - int(cfg["ar_window"])), sc):
        if closes[i] < ctx["opens"][i] and vols[i] >= vma[i] * float(cfg["climax_vol_mult"]) * 0.8:
            ps = i
            break
    if ps is not None:
        events.insert(0, _ev("PS", ps, win, lows[ps], "first heavy buying inside the decline"))

    # AR — the rally high after the climax; its high tops the range.
    ar_end = min(m - 1, sc + int(cfg["ar_window"]))
    ar = max(range(sc, ar_end + 1), key=lambda i: highs[i]) if ar_end > sc else sc
    events.append(_ev("AR", ar, win, highs[ar], "supply spent — this high caps the range"))

    r_low, r_high = lows[sc], highs[ar]
    width = max(r_high - r_low, 1e-9)
    tol = width * float(cfg["st_tolerance_pct"]) / 100.0

    # ST — returns to the low area on lighter volume than the climax.
    for i in range(ar + 1, m):
        if lows[i] <= r_low + tol and lows[i] >= r_low:
            lighter = (not ctx["has_volume"]) or vols[i] < vols[sc]
            events.append(_ev("ST", i, win, lows[i],
                              "retest on lighter volume" if lighter else "retest but volume still heavy"))
            if len(events) > 12:
                break

    # SPRING — undercuts the range low then closes back inside.
    spring = None
    max_under = r_low * (1.0 - float(cfg["spring_max_pct"]) / 100.0)
    for i in range(ar + 1, m):
        if lows[i] < r_low and lows[i] >= max_under and closes[i] > r_low:
            spring = i
            events.append(_ev("SPRING", i, win, lows[i],
                              "undercut the range low and closed back inside — sellers trapped"))
    # TEST — a higher low on lighter volume after the spring.
    test = None
    if spring is not None:
        for i in range(spring + 1, m):
            if lows[i] > lows[spring] and lows[i] <= r_low + tol:
                lighter = (not ctx["has_volume"]) or vols[i] <= vols[spring]
                if lighter:
                    test = i
                    events.append(_ev("TEST", i, win, lows[i], "higher low on lighter volume — spring confirmed"))
                    break

    # SOS — wide-spread advance closing strong on expanding volume, above the range top.
    sos = None
    start = (test or spring or ar) + 1
    for i in range(start, m):
        wide = (highs[i] - lows[i]) >= atr[i] * float(cfg["climax_spread_mult"]) * 0.8
        strong = _close_pos(win[i]) >= float(cfg["sos_close_pct"]) / 100.0
        loud = (not ctx["has_volume"]) or vols[i] >= vma[i] * float(cfg["sos_vol_mult"])
        if closes[i] > r_high and wide and strong and loud:
            sos = i
            events.append(_ev("SOS", i, win, closes[i], "wide-spread advance out of the range on volume"))
            break

    # LPS — the pullback after the SOS that holds above the old resistance.
    lps = None
    if sos is not None:
        floor_ = r_high - width * float(cfg["lps_tolerance_pct"]) / 100.0
        for i in range(sos + 1, m):
            if lows[i] >= floor_ and closes[i] > floor_ and lows[i] < highs[sos]:
                quiet = (not ctx["has_volume"]) or vols[i] < vma[i]
                if quiet:
                    lps = i
                    events.append(_ev("LPS", i, win, lows[i], "pullback holds the breakout level on drying volume"))
                    break

    bars_out = 0
    if closes[last] > r_high:
        for i in range(last, -1, -1):
            if closes[i] <= r_high:
                break
            bars_out += 1

    bias = "ACCUMULATION"
    if closes[last] > r_high and bars_out >= 2:
        bias = "MARK-UP"
    elif closes[last] < r_low:
        bias = "FAILED BASE"

    return {"range": _range(r_low, r_high, sc, last, win, ctx), "events": events, "bias": bias,
            "key": {"sc": sc, "ar": ar, "spring": spring, "test": test, "sos": sos, "lps": lps,
                    "bars_out": bars_out}}


# ── distribution structure ───────────────────────────────────────────
def _distribution(ctx: dict, hh: int) -> dict:
    cfg, win, m, last = ctx["cfg"], ctx["win"], ctx["m"], ctx["last"]
    highs, lows, closes, vols, atr, vma = (ctx["highs"], ctx["lows"], ctx["closes"],
                                           ctx["vols"], ctx["atr"], ctx["vma"])
    events: list[dict] = []

    lo_i, hi_i = max(0, hh - 2), min(m - 1, hh + 2)
    bc = max(range(lo_i, hi_i + 1), key=lambda i: vols[i]) if ctx["has_volume"] else hh
    climactic = (not ctx["has_volume"]) or (vols[bc] >= vma[bc] * float(cfg["climax_vol_mult"]))
    events.append(_ev("BC", bc, win, highs[bc],
                      "climactic volume" if climactic else "high of the range (volume not climactic)"))

    psy = None
    for i in range(max(0, bc - int(cfg["ar_window"])), bc):
        if closes[i] > ctx["opens"][i] and vols[i] >= vma[i] * float(cfg["climax_vol_mult"]) * 0.8:
            psy = i
            break
    if psy is not None:
        events.insert(0, _ev("PSY", psy, win, highs[psy], "first heavy selling into the advance"))

    ar_end = min(m - 1, bc + int(cfg["ar_window"]))
    ar = min(range(bc, ar_end + 1), key=lambda i: lows[i]) if ar_end > bc else bc
    events.append(_ev("ARD", ar, win, lows[ar], "demand spent — this low floors the range"))

    r_high, r_low = highs[bc], lows[ar]
    width = max(r_high - r_low, 1e-9)
    tol = width * float(cfg["st_tolerance_pct"]) / 100.0

    for i in range(ar + 1, m):
        if highs[i] >= r_high - tol and highs[i] <= r_high:
            lighter = (not ctx["has_volume"]) or vols[i] < vols[bc]
            events.append(_ev("STD", i, win, highs[i],
                              "retest on lighter volume" if lighter else "retest but volume still heavy"))
            if len(events) > 12:
                break

    utad = None
    max_over = r_high * (1.0 + float(cfg["spring_max_pct"]) / 100.0)
    for i in range(ar + 1, m):
        if highs[i] > r_high and highs[i] <= max_over and closes[i] < r_high:
            utad = i
            events.append(_ev("UTAD", i, win, highs[i],
                              "poked above the range and failed back inside — buyers trapped"))

    sow = None
    start = (utad or ar) + 1
    for i in range(start, m):
        wide = (highs[i] - lows[i]) >= atr[i] * float(cfg["climax_spread_mult"]) * 0.8
        weak = _close_pos(win[i]) <= 1.0 - float(cfg["sos_close_pct"]) / 100.0
        loud = (not ctx["has_volume"]) or vols[i] >= vma[i] * float(cfg["sos_vol_mult"])
        if closes[i] < r_low and wide and weak and loud:
            sow = i
            events.append(_ev("SOW", i, win, closes[i], "wide-spread decline out of the range on volume"))
            break

    lpsy = None
    if sow is not None:
        ceil_ = r_low + width * float(cfg["lps_tolerance_pct"]) / 100.0
        for i in range(sow + 1, m):
            if highs[i] <= ceil_ and closes[i] < ceil_ and highs[i] > lows[sow]:
                quiet = (not ctx["has_volume"]) or vols[i] < vma[i]
                if quiet:
                    lpsy = i
                    events.append(_ev("LPSY", i, win, highs[i], "weak rally fails under old support"))
                    break

    bars_out = 0
    if closes[last] < r_low:
        for i in range(last, -1, -1):
            if closes[i] >= r_low:
                break
            bars_out += 1

    bias = "DISTRIBUTION"
    if closes[last] < r_low and bars_out >= 2:
        bias = "MARK-DOWN"
    elif closes[last] > r_high:
        bias = "FAILED TOP"

    return {"range": _range(r_low, r_high, bc, last, win, ctx), "events": events, "bias": bias,
            "key": {"bc": bc, "ar": ar, "utad": utad, "sow": sow, "lpsy": lpsy,
                    "bars_out": bars_out}}


def _ev(kind: str, idx: int, win: list[dict], price: float, note: str) -> dict:
    meta = EVENTS.get(kind, {})
    return {"type": kind, "name": meta.get("name", kind), "meaning": meta.get("meaning", ""),
            "idx": idx, "at": _label(win[idx]), "price": round(float(price), 2),
            "volume": round(_vol(win[idx])), "note": note}


def _range(low: float, high: float, start: int, last: int, win: list[dict], ctx: dict) -> dict:
    width = high - low
    mid = (high + low) / 2.0
    ltp = ctx["ltp"]
    pos = 0.0 if width <= 0 else max(0.0, min(1.0, (ltp - low) / width))
    return {"low": round(low, 2), "high": round(high, 2), "mid": round(mid, 2),
            "width_pct": round(_pct(high, low), 2), "duration": last - start + 1,
            "start": start, "start_at": _label(win[start]), "position_pct": round(pos * 100, 1),
            "atr": round(ctx["atr"][-1], 2)}


# ── effort vs result ─────────────────────────────────────────────────
def _effort_result(ctx: dict, rng: dict) -> dict:
    cfg, win, m = ctx["cfg"], ctx["win"], ctx["m"]
    vols, vma, atr, closes, opens = ctx["vols"], ctx["vma"], ctx["atr"], ctx["closes"], ctx["opens"]
    if not ctx["has_volume"]:
        return {"available": False, "verdict": "no volume on this series",
                "note": "Indices and some feeds carry no volume — read structure only.",
                "bars": []}
    look = min(m, max(5, int(cfg["activity_bars"]) // 2))
    rows = []
    absorb_low = absorb_high = 0
    for i in range(m - look, m):
        eff = vols[i] / vma[i] if vma[i] else 0.0
        res = abs(closes[i] - opens[i]) / atr[i] if atr[i] else 0.0
        flag = None
        if eff >= float(cfg["effort_vol_mult"]) and res <= float(cfg["effort_result_max"]):
            near_low = ctx["lows"][i] <= rng["low"] + (rng["high"] - rng["low"]) * 0.35
            near_high = ctx["highs"][i] >= rng["high"] - (rng["high"] - rng["low"]) * 0.35
            if near_low:
                flag = "absorption"
                absorb_low += 1
            elif near_high:
                flag = "supply"
                absorb_high += 1
            else:
                flag = "churn"
        rows.append({"at": _label(win[i]), "effort": round(eff, 2), "result": round(res, 2),
                     "flag": flag})
    if absorb_low > absorb_high and absorb_low:
        verdict, note = "ABSORPTION", (f"{absorb_low} high-effort / low-result bar(s) at the range low — "
                                       "supply is being absorbed by a larger buyer.")
    elif absorb_high > absorb_low and absorb_high:
        verdict, note = "SUPPLY", (f"{absorb_high} high-effort / low-result bar(s) at the range high — "
                                   "demand is being met by supply.")
    else:
        verdict, note = "NEUTRAL", "No effort/result divergence in the recent bars."
    return {"available": True, "verdict": verdict, "note": note,
            "absorption_bars": absorb_low, "supply_bars": absorb_high, "bars": rows}


# ── open interest (F&O only) ─────────────────────────────────────────
def _oi_read(ctx: dict, oi_series: list[float], cfg: dict) -> dict:
    closes = ctx["closes"]
    n = min(len(oi_series), len(closes))
    if n < 3:
        return {"available": False, "verdict": "no OI history", "note": ""}
    oi_now, oi_prev = float(oi_series[-1]), float(oi_series[-2])
    px_now, px_prev = closes[-1], closes[-2]
    d_oi = _pct(oi_now, oi_prev)
    d_px = _pct(px_now, px_prev)
    thr = float(cfg["oi_change_pct"])
    if d_px > 0 and d_oi > thr:
        verdict, note = "LONG BUILD-UP", "Price up on rising OI — fresh longs. Effort and result agree."
    elif d_px > 0 and d_oi < -thr:
        verdict, note = "SHORT COVERING", "Price up on falling OI — a squeeze, not fresh demand. Treat rallies with suspicion."
    elif d_px < 0 and d_oi > thr:
        verdict, note = "SHORT BUILD-UP", "Price down on rising OI — fresh shorts. Supply is real."
    elif d_px < 0 and d_oi < -thr:
        verdict, note = "LONG UNWINDING", "Price down on falling OI — longs leaving, not new selling. Often precedes a base."
    else:
        verdict, note = "FLAT", "No meaningful OI change — positions are not committing yet."
    return {"available": True, "verdict": verdict, "note": note,
            "oi": round(oi_now), "oi_change_pct": round(d_oi, 2), "price_change_pct": round(d_px, 2)}


# ── Wyckoff's nine tests ─────────────────────────────────────────────
def _nine_tests(ctx: dict, side: str, struct: dict) -> dict:
    cfg, m, last = ctx["cfg"], ctx["m"], ctx["last"]
    highs, lows, closes, opens, vols = (ctx["highs"], ctx["lows"], ctx["closes"],
                                        ctx["opens"], ctx["vols"])
    rng, ev_types = struct["range"], {e["type"] for e in struct["events"]}
    ph, pl = pivots(ctx["win"], int(cfg["pivot_k"]))
    act = min(m, int(cfg["activity_bars"]))
    up_v = [vols[i] for i in range(m - act, m) if closes[i] > opens[i]]
    dn_v = [vols[i] for i in range(m - act, m) if closes[i] < opens[i]]
    up_avg = sum(up_v) / len(up_v) if up_v else 0.0
    dn_avg = sum(dn_v) / len(dn_v) if dn_v else 0.0
    novol = not ctx["has_volume"]

    def rising(idxs, series):
        pts = [series[i] for i in idxs[-3:]]
        return len(pts) >= 2 and all(b > a for a, b in zip(pts, pts[1:]))

    def falling(idxs, series):
        pts = [series[i] for i in idxs[-3:]]
        return len(pts) >= 2 and all(b < a for a, b in zip(pts, pts[1:]))

    if side == "accum":
        results = {
            "downside_objective": lows[struct["key"]["sc"]] <= min(lows) + (rng["high"] - rng["low"]) * 0.20,
            "climax_support": "SC" in ev_types,
            "secondary_test": any(e["type"] == "ST" and "lighter" in e["note"] for e in struct["events"]),
            "activity_bullish": novol or (up_avg > dn_avg),
            "stride_broken": closes[last] > rng["mid"],
            "higher_lows": rising(pl, lows),
            "higher_highs": rising(ph, highs),
            "spring_present": "SPRING" in ev_types,
            "cause_built": rng["duration"] >= int(cfg["min_range_bars"]),
        }
        defs = BUY_TESTS
    else:
        results = {
            "upside_objective": highs[struct["key"]["bc"]] >= max(highs) - (rng["high"] - rng["low"]) * 0.20,
            "climax_supply": "BC" in ev_types,
            "secondary_test": any(e["type"] == "STD" and "lighter" in e["note"] for e in struct["events"]),
            "activity_bearish": novol or (dn_avg > up_avg),
            "stride_broken": closes[last] < rng["mid"],
            "lower_highs": falling(ph, highs),
            "lower_lows": falling(pl, lows),
            "upthrust_present": "UTAD" in ev_types,
            "cause_built": rng["duration"] >= int(cfg["min_range_bars"]),
        }
        defs = SELL_TESTS

    items = [{"key": k, "label": lbl, "ok": bool(results.get(k))} for k, lbl in defs]
    passed = sum(1 for i in items if i["ok"])
    return {"side": side, "items": items, "passed": passed, "total": len(items)}


# ── phase ────────────────────────────────────────────────────────────
def _phase(side: str, ev_types: set, ctx: dict, rng: dict) -> tuple[str, str]:
    closes, last = ctx["closes"], ctx["last"]
    if side == "accum":
        if closes[last] > rng["high"] and ("SOS" in ev_types or "LPS" in ev_types):
            if "LPS" in ev_types:
                return "D", "SOS printed and the pullback held — demand is in control inside the range."
            return "E" if closes[last] > rng["high"] * 1.01 else "D", "Broken out on strength; watch for the last point of support."
        if "SPRING" in ev_types:
            return "C", ("Spring confirmed by a test." if "TEST" in ev_types
                         else "Spring printed — waiting for the confirming test.")
        if "ST" in ev_types:
            return "B", "Secondary tests are absorbing supply — the cause is still being built."
        if "SC" in ev_types and "AR" in ev_types:
            return "A", "Selling climax and automatic rally are in — the decline has been stopped, not reversed."
    else:
        if closes[last] < rng["low"] and ("SOW" in ev_types or "LPSY" in ev_types):
            if "LPSY" in ev_types:
                return "D", "SOW printed and the rally failed — supply is in control inside the range."
            return "E" if closes[last] < rng["low"] * 0.99 else "D", "Broken down on weakness; watch for the last point of supply."
        if "UTAD" in ev_types:
            return "C", "Upthrust after distribution — buyers trapped above the range."
        if "STD" in ev_types:
            return "B", "Secondary tests are distributing stock — the top is still being built."
        if "BC" in ev_types and "ARD" in ev_types:
            return "A", "Buying climax and automatic reaction are in — the advance has been stopped, not reversed."
    return "?", "Structure incomplete."


# ── the call: enter, prepare, or stay out ────────────────────────────
def _guidance(side: str, phase: str, ev_types: set, ctx: dict, rng: dict, tests: dict,
              effort: dict, oi: Optional[dict], struct: dict, kind: str) -> dict:
    cfg, closes, last = ctx["cfg"], ctx["closes"], ctx["last"]
    ltp = closes[last]
    passed = tests["passed"]
    need_enter = int(cfg["min_tests_enter"])
    need_prep = int(cfg["min_tests_prepare"])
    key = struct["key"]
    reasons: list[str] = []
    avoid: list[str] = []
    action = "WAIT"
    trigger = invalidation = None

    long_side = side == "accum"
    dirn = "LONG" if long_side else "SHORT"

    # ── the disqualifiers come first: Wyckoff is mostly about not trading ──
    if rng["duration"] < int(cfg["min_range_bars"]):
        avoid.append(f"The range is only {rng['duration']} bars — not enough cause for a durable move.")
    if rng["width_pct"] < 1.0:
        avoid.append(f"The range is {rng['width_pct']}% wide — too tight to pay for the risk.")
    if phase == "B":
        avoid.append("Phase B is the middle of the range — the least rewarding place to act. Wait for the test.")
    if phase == "A":
        avoid.append("Phase A only stops the prior trend. Supply/demand has not been tested yet.")
    if phase == "E" and key.get("bars_out", 0) > int(cfg["chase_bars"]):
        avoid.append(f"Price has been outside the range for {key['bars_out']} bars — entering here is chasing.")
    if long_side and "UTAD" in ev_types:
        avoid.append("An upthrust sits inside this base — the structure is compromised.")
    if long_side and effort and effort.get("verdict") == "SUPPLY":
        avoid.append("Effort/result shows supply meeting demand at the range high.")
    if (not long_side) and effort and effort.get("verdict") == "ABSORPTION":
        avoid.append("Effort/result shows absorption at the range low — the top may fail.")
    if oi and oi.get("available"):
        if long_side and oi["verdict"] == "SHORT COVERING":
            avoid.append("The advance is short covering, not fresh longs (OI falling into a rising price).")
        if (not long_side) and oi["verdict"] == "LONG UNWINDING":
            avoid.append("The decline is long unwinding, not fresh shorts (OI falling into a falling price).")
    if struct["bias"] in ("FAILED BASE", "FAILED TOP"):
        avoid.append(f"{struct['bias'].title()} — price has broken the wrong side of the range.")

    # ── the setups, best first ──
    if long_side:
        spring_i, test_i, sos_i, lps_i = key.get("spring"), key.get("test"), key.get("sos"), key.get("lps")
        if phase == "C" and spring_i is not None and test_i is not None and passed >= need_enter:
            action = "ENTER_LONG"
            trigger = round(ctx["highs"][test_i], 2)
            invalidation = round(ctx["lows"][spring_i], 2)
            reasons.append("Phase C: spring confirmed by a higher-low test on lighter volume — the classic Wyckoff long.")
        elif phase == "D" and lps_i is not None and passed >= need_enter:
            action = "ENTER_LONG"
            trigger = round(ctx["highs"][lps_i], 2)
            invalidation = round(ctx["lows"][lps_i], 2)
            reasons.append("Phase D: sign of strength followed by a last point of support — the conservative long.")
        elif phase == "C" and spring_i is not None and passed >= need_prep:
            action = "PREPARE_LONG"
            trigger = round(rng["mid"], 2)
            invalidation = round(ctx["lows"][spring_i], 2)
            reasons.append("Spring is in but untested. Wait for a higher low on lighter volume, then act.")
        elif phase == "D" and sos_i is not None and lps_i is None:
            action = "PREPARE_LONG"
            trigger = round(rng["high"], 2)
            invalidation = round(rng["mid"], 2)
            reasons.append("Sign of strength printed. Do not chase the bar — wait for the pullback (LPS) to the breakout level.")
        elif phase in ("A", "B"):
            action = "AVOID"
            reasons.append("The base is still being built; the edge is in Phase C, not here.")
    else:
        utad_i, sow_i, lpsy_i = key.get("utad"), key.get("sow"), key.get("lpsy")
        if phase == "D" and lpsy_i is not None and passed >= need_enter:
            action = "ENTER_SHORT"
            trigger = round(ctx["lows"][lpsy_i], 2)
            invalidation = round(ctx["highs"][lpsy_i], 2)
            reasons.append("Phase D: sign of weakness then a failed rally (LPSY) — the conservative short.")
        elif phase == "C" and utad_i is not None and passed >= need_enter:
            action = "ENTER_SHORT"
            trigger = round(rng["mid"], 2)
            invalidation = round(ctx["highs"][utad_i], 2)
            reasons.append("Phase C: upthrust after distribution — buyers trapped above the range.")
        elif phase == "C" and utad_i is not None and passed >= need_prep:
            action = "PREPARE_SHORT"
            trigger = round(rng["mid"], 2)
            invalidation = round(ctx["highs"][utad_i], 2)
            reasons.append("Upthrust printed but unconfirmed — wait for a lower high on thin volume.")
        elif phase == "D" and sow_i is not None and lpsy_i is None:
            action = "PREPARE_SHORT"
            trigger = round(rng["low"], 2)
            invalidation = round(rng["mid"], 2)
            reasons.append("Sign of weakness printed. Wait for the weak rally back to the broken support.")
        elif phase in ("A", "B"):
            action = "AVOID"
            reasons.append("The top is still being built; the edge is in Phase C, not here.")

    if effort and effort.get("available") and effort.get("verdict") != "NEUTRAL":
        reasons.append(f"Effort vs result: {effort['note']}")
    if oi and oi.get("available") and oi["verdict"] not in ("FLAT",):
        reasons.append(f"Open interest: {oi['note']}")

    # hard disqualifiers override an entry call
    hard = [a for a in avoid if a.startswith(("Phase B", "Phase A")) or "compromised" in a
            or "chasing" in a or "not enough cause" in a or "too tight" in a
            or a.startswith(("Failed", "Failed base", "Failed top"))]
    if hard and action in ("ENTER_LONG", "ENTER_SHORT"):
        action = "PREPARE_LONG" if action == "ENTER_LONG" else "PREPARE_SHORT"
    if len(avoid) >= 3 or (avoid and action == "WAIT"):
        action = "AVOID"

    conf = 0
    conf += int(passed / tests["total"] * 45)
    conf += {"C": 30, "D": 26, "E": 12, "B": 6, "A": 8, "?": 0}.get(phase, 0)
    if effort and effort.get("available"):
        good = (effort["verdict"] == "ABSORPTION") if long_side else (effort["verdict"] == "SUPPLY")
        conf += 15 if good else (0 if effort["verdict"] == "NEUTRAL" else -10)
    if oi and oi.get("available"):
        good_oi = ("LONG BUILD-UP" if long_side else "SHORT BUILD-UP") == oi["verdict"]
        conf += 10 if good_oi else 0
    conf = max(0, min(100, conf - 8 * len(avoid)))

    headline = {
        "ENTER_LONG": f"Enter long — Phase {phase} {dirn} setup is complete",
        "ENTER_SHORT": f"Enter short — Phase {phase} setup is complete",
        "PREPARE_LONG": "Stalk it — the long setup is forming, not ready",
        "PREPARE_SHORT": "Stalk it — the short setup is forming, not ready",
        "AVOID": "Stay out",
        "WAIT": "Nothing actionable yet",
    }[action]
    risk = "low" if conf >= 70 else "medium" if conf >= 45 else "high"
    return {"action": action, "direction": dirn, "confidence": conf, "headline": headline,
            "reasons": reasons, "avoid": avoid, "trigger": trigger, "invalidation": invalidation,
            "risk": risk, "tests_passed": passed, "tests_total": tests["total"]}


# ── chart payload ────────────────────────────────────────────────────
def _series(win: list[dict], cfg: dict, events: Optional[list[dict]] = None) -> list[dict]:
    keep = min(len(win), int(cfg.get("chart_bars", 120)))
    off = len(win) - keep
    marks: dict[int, list[str]] = {}
    for e in (events or []):
        i = e["idx"] - off
        if i >= 0:
            marks.setdefault(i, []).append(e["type"])
    out = []
    for i, c in enumerate(win[off:]):
        out.append({"at": _label(c), "open": round(_f(c, "open"), 2), "high": round(_f(c, "high"), 2),
                    "low": round(_f(c, "low"), 2), "close": round(_f(c, "close"), 2),
                    "volume": round(_vol(c)), "marks": marks.get(i, [])})
    return out
