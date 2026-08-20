"""
QMRE engine — the single pipeline shared by Live, Replay and Backtest.

    candles(≤ cutoff) + stock_ctx + market_ctx + cfg
        → features (look-ahead-safe)
        → risk plan (entry/SL/targets/RR)
        → score + class + breakdown
        → sized paper candidate

``evaluate`` scores one stock at one cutoff; ``rank`` scores a set and orders them
by risk-adjusted opportunity. There is deliberately no separate "live" vs
"backtest" scorer — this is it.
"""
from __future__ import annotations

from typing import Optional

from research.qmre import features as feat
from research.qmre import scoring


def evaluate(candles: list[dict], stock_ctx: dict, market_ctx: dict, cfg: dict) -> Optional[dict]:
    """Score one stock at the cutoff implied by ``candles`` (last candle). Returns
    a candidate dict or None when there isn't enough data yet."""
    ctx = dict(stock_ctx)
    ctx["regime_score"] = float(market_ctx.get("regime_score", 0))
    f = feat.compute_features(candles, ctx, cfg)
    if not f:
        return None
    rp = scoring.risk_plan(f, cfg)
    ctx["rr"] = rp["rr"]
    sc = scoring.score_features(f, ctx, cfg)
    sz = scoring.size_position(rp["entry"], rp["sl"], cfg)
    cutoff = candles[-1]["_dt"]
    return {
        "symbol": stock_ctx.get("symbol"),
        "cutoff": cutoff.strftime("%Y-%m-%d %H:%M") if cutoff else None,
        "score": sc["score"], "class": sc["class"], "breakdown": sc["breakdown"],
        "features": f, "risk": rp, "sizing": sz,
        "signal_reason": _reason(f, sc, rp),
        "opportunity": round(sc["score"] * (1 + 0.08 * min(rp["rr"], 3)), 2),  # score, RR-tilted
    }


def _reason(f: dict, sc: dict, rp: dict) -> str:
    bits = []
    if f.get("rvol") is not None:
        bits.append(f"RVOL {f['rvol']}x")
    if f.get("breakout_confirmed"):
        bits.append("breakout confirmed")
    elif f.get("orb") == "breakout":
        bits.append("OR breakout")
    bits.append("above VWAP" if f.get("above_vwap") else "below VWAP")
    if f.get("rs") is not None:
        bits.append(f"RS {f['rs']:+.1f}%")
    bits.append(f"RR {rp['rr']}")
    return " · ".join(bits)


def rank(items: list[dict], market_ctx: dict, cfg: dict) -> list[dict]:
    """items: [{"symbol","candles","ctx"}]. Returns scored candidates ordered by
    risk-adjusted opportunity (not raw % gain), NO-TRADE demoted."""
    out = []
    for it in items:
        try:
            cand = evaluate(it["candles"], {**it.get("ctx", {}), "symbol": it["symbol"]},
                            market_ctx, cfg)
            if cand:
                out.append(cand)
        except Exception:
            continue
    trade = [c for c in out if c["class"] != "NO TRADE"]
    notrade = [c for c in out if c["class"] == "NO TRADE"]
    trade.sort(key=lambda c: (c["opportunity"], c["score"]), reverse=True)
    notrade.sort(key=lambda c: c["score"], reverse=True)
    ranked = trade + notrade
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
    return ranked
