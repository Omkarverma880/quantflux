"""
MIH Trade Ideas — structured, explainable research setups (NOT advice).

Each idea carries Entry / Stop-Loss / Target with a live progress marker so the
UI can render the reference-style track, plus a status derived from where price
actually is. Nothing here places an order; there is no broker-order import.
"""
from __future__ import annotations


def build_idea(r: dict, score: dict, cfg: dict) -> dict | None:
    """Build a long research idea from a scored snapshot row. Returns None when
    the setup doesn't qualify or the data needed for a stop isn't available."""
    if score["score"] < float(cfg["idea_min_score"]):
        return None
    ltp = r.get("ltp") or 0
    atr = r.get("atr") or (ltp * 0.015 if ltp else 0)
    if ltp <= 0 or atr <= 0:
        return None

    # Entry: prefer a pullback toward VWAP when extended, else current price.
    vwap = r.get("vwap") or ltp
    ext_atr = (ltp - vwap) / atr if atr else 0
    if ext_atr > 1.5:
        entry, etype = round(max(vwap, ltp - atr * 0.8), 2), "AWAITING ENTRY"
    else:
        entry, etype = round(ltp, 2), "ACTIVE"

    sl = round(max(0.01, entry - atr * float(cfg["idea_sl_atr"])), 2)
    target = round(entry + atr * float(cfg["idea_target_atr"]), 2)
    risk, reward = max(0.01, entry - sl), max(0.01, target - entry)

    # status from where price actually is now
    if ltp >= target:
        status = "TARGET HIT"
    elif ltp <= sl:
        status = "SL HIT"
    elif etype == "AWAITING ENTRY" and ltp > entry:
        status = "AWAITING ENTRY"
    else:
        status = "ACTIVE"

    span = max(0.01, target - sl)
    progress = round(min(1.0, max(0.0, (ltp - sl) / span)) * 100, 1)   # marker on the SL→Target track
    return {
        "symbol": r["symbol"], "sector": r.get("sector"), "ltp": round(ltp, 2),
        "change_pct": r.get("change_pct"), "score": score["score"], "grade": score["grade"],
        "entry": entry, "sl": sl, "target": target,
        "risk_pct": round(risk / entry * 100, 2), "reward_pct": round(reward / entry * 100, 2),
        "upside_pct": round((target - ltp) / ltp * 100, 2),
        "rr": round(reward / risk, 2), "status": status, "progress": progress,
        "rationale": _rationale(r, score),
        "horizon": "Intraday / short term", "disclaimer": "Research idea — not investment advice.",
    }


def _rationale(r: dict, score: dict) -> str:
    bits = []
    if (r.get("rvol") or 0) >= 1.5:
        bits.append(f"volume {r['rvol']}x average")
    if r.get("vwap") and r.get("ltp", 0) >= r["vwap"]:
        bits.append("holding above VWAP")
    if r.get("high_20d") and r.get("ltp", 0) >= r["high_20d"]:
        bits.append("cleared the 20-day high")
    if r.get("high_52w") and r.get("ltp", 0) >= r["high_52w"] * 0.99:
        bits.append("near a 52-week high")
    if (r.get("change_pct") or 0) > 0:
        bits.append(f"up {r['change_pct']}% today")
    top = max(score["breakdown"].items(), key=lambda kv: kv[1]["sub"])[0]
    bits.append(f"strongest factor: {top}")
    return " · ".join(bits) if bits else "technical strength across trend and momentum"
