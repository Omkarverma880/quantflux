"""
QMIE leakage-safe point-in-time backtest + calibration (§35, §36, §46).

Pure helpers here; the walk lives in the service (it needs read-only bars). The
discipline: a decision at bar *i* is evaluated using ONLY bars[:i+1] (and the
benchmark up to the same date), and its outcome is measured ONLY on bars[i+1:].
No look-ahead. Same-bar target+stop is resolved conservatively as a loss.

This is a research simulator — it never places or simulates sending an order.
"""
from __future__ import annotations

# forward observation window (bars) and decision spacing (bars) per horizon
BT_PROFILE = {
    "intraday":   {"max_hold": 26, "cadence": 6},
    "swing":      {"max_hold": 10, "cadence": 3},
    "positional": {"max_hold": 30, "cadence": 5},
    "monthly":    {"max_hold": 60, "cadence": 8},
}
MAX_DECISIONS_PER_INSTRUMENT = 150      # bound compute for a manual research run


def simulate_outcome(direction: str, entry: float, target: float, stop: float,
                     forward: list[dict]) -> dict:
    """Walk forward bars; return {outcome, r_multiple, bars_held}.

    outcome ∈ {target, stop, none}. r_multiple is in units of initial risk.
    Same-bar target+stop → conservative loss (stop)."""
    risk = abs(entry - stop)
    if risk <= 0 or not forward:
        return {"outcome": "none", "r_multiple": 0.0, "bars_held": 0}
    rr = abs(target - entry) / risk
    for n, b in enumerate(forward, 1):
        hi, lo = float(b["high"]), float(b["low"])
        if direction == "long":
            hit_stop, hit_tgt = lo <= stop, hi >= target
        else:
            hit_stop, hit_tgt = hi >= stop, lo <= target
        if hit_stop and hit_tgt:                      # ambiguous → conservative loss
            return {"outcome": "stop", "r_multiple": -1.0, "bars_held": n}
        if hit_stop:
            return {"outcome": "stop", "r_multiple": -1.0, "bars_held": n}
        if hit_tgt:
            return {"outcome": "target", "r_multiple": round(rr, 3), "bars_held": n}
    # neither hit → mark to last close in risk units (signed by direction)
    last = float(forward[-1]["close"])
    mtm = (last - entry) if direction == "long" else (entry - last)
    return {"outcome": "none", "r_multiple": round(mtm / risk, 3), "bars_held": len(forward)}


def _conf_bucket(c: float) -> str:
    c = float(c or 0)
    if c >= 90:
        return "90-100"
    if c >= 75:
        return "75-90"
    if c >= 60:
        return "60-75"
    return "<60"


def aggregate(records: list[dict]) -> dict:
    """Win-rate, expectancy and calibration by score-band and confidence bucket."""
    n = len(records)
    if not n:
        return {"count": 0}
    tgt = sum(1 for r in records if r["outcome"] == "target")
    stp = sum(1 for r in records if r["outcome"] == "stop")
    none = n - tgt - stp
    rs = [r["r_multiple"] for r in records]
    decided = tgt + stp
    hold = [r["bars_held"] for r in records if r["bars_held"]]

    def _seg(items):
        m = len(items)
        if not m:
            return None
        t = sum(1 for r in items if r["outcome"] == "target")
        s = sum(1 for r in items if r["outcome"] == "stop")
        return {"n": m, "target_rate": round(t / m * 100, 1),
                "stop_rate": round(s / m * 100, 1),
                "avg_r": round(sum(r["r_multiple"] for r in items) / m, 3),
                "win_rate": round(t / (t + s) * 100, 1) if (t + s) else None}

    bands = {}
    for band in ("Exceptional", "Strong", "Constructive", "Developing", "Weak"):
        seg = _seg([r for r in records if r["band"] == band])
        if seg:
            bands[band] = seg
    confs = {}
    for cb in ("90-100", "75-90", "60-75", "<60"):
        seg = _seg([r for r in records if _conf_bucket(r["confidence"]) == cb])
        if seg:
            confs[cb] = seg
    dirs = {d: _seg([r for r in records if r["direction"] == d]) for d in ("long", "short")
            if any(r["direction"] == d for r in records)}

    return {
        "count": n, "target": tgt, "stop": stp, "none": none,
        "target_rate": round(tgt / n * 100, 1), "stop_rate": round(stp / n * 100, 1),
        "win_rate": round(tgt / decided * 100, 1) if decided else None,
        "avg_r": round(sum(rs) / n, 3), "expectancy": round(sum(rs) / n, 3),
        "avg_hold": round(sum(hold) / len(hold), 1) if hold else None,
        "insufficient": n < 30,             # §46.2 minimum sample for performance claims
        "by_band": bands, "by_confidence": confs, "by_direction": dirs,
    }
