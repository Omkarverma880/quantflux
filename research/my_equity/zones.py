"""
Entry zones for My Equity Workspace.

Two things are going on here, and the UI keeps them apart:

* **Where** — price levels that matter: your own research levels, swing highs and lows that
  price has respected more than once, the 200 EMA, the lower Bollinger band and the lower
  VWAP band. These are geometry, not opinion.

* **What usually happened there** — every zone carries a setup, and each setup is replayed
  over this stock's own daily history: how many times it triggered, how often price was
  higher N sessions later, the median move, and the median drawdown before it. The most
  recent fifth of the history is held out and reported separately, so a setup that only
  worked in the old data is visible as such.

No model is fitted and nothing is extrapolated — the numbers are counts from this stock's
past. A setup with fewer than ``MIN_SAMPLES`` triggers is reported as untested, never hidden
behind a confident-looking score.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from research.my_equity import metrics as MX

HORIZON = 10          # sessions to look forward when scoring a setup
MIN_SAMPLES = 8       # below this the setup is reported as untested
HOLDOUT = 0.2         # the most recent fifth is scored separately
MAX_REACH_PCT = 12.0  # a zone further below price than this is not what you are waiting for
UNTESTED_PENALTY_PCT = 1.5   # how much closer an untested zone must be to win the pick


# ── geometry ─────────────────────────────────────────────────────────
def pivots(d: pd.DataFrame, span: int = 5) -> tuple[list[float], list[float]]:
    """Swing highs and lows: a bar whose high (low) is the extreme of ±``span`` bars."""
    hi, lo = d["high"].to_numpy(), d["low"].to_numpy()
    highs, lows = [], []
    for i in range(span, len(d) - span):
        w = slice(i - span, i + span + 1)
        if hi[i] == hi[w].max():
            highs.append(float(hi[i]))
        if lo[i] == lo[w].min():
            lows.append(float(lo[i]))
    return highs, lows


def cluster(levels: list[float], tol_pct: float) -> list[dict]:
    """Merge nearby swing levels into zones; more touches means a level price respects."""
    out: list[dict] = []
    for lv in sorted(levels):
        if out and (lv - out[-1]["price"]) / max(out[-1]["price"], 1e-9) * 100 <= tol_pct:
            z = out[-1]
            z["touches"] += 1
            z["lo"], z["hi"] = min(z["lo"], lv), max(z["hi"], lv)
            z["price"] = (z["lo"] + z["hi"]) / 2
        else:
            out.append({"price": lv, "lo": lv, "hi": lv, "touches": 1})
    return out


# ── setups, measured on this stock's own history ─────────────────────
def _forward(d: pd.DataFrame, mask: pd.Series, horizon: int = HORIZON) -> dict:
    """What happened in the ``horizon`` sessions after every bar the mask marks."""
    close = d["close"].to_numpy(float)
    high, low = d["high"].to_numpy(float), d["low"].to_numpy(float)
    idx = np.where(mask.fillna(False).to_numpy())[0]
    idx = idx[idx + horizon < len(d)]
    if idx.size == 0:
        return {"samples": 0}
    rets, maes, mfes = [], [], []
    for i in idx:
        entry = close[i]
        fwd = slice(i + 1, i + 1 + horizon)
        rets.append((close[i + horizon] - entry) / entry * 100)
        maes.append((low[fwd].min() - entry) / entry * 100)
        mfes.append((high[fwd].max() - entry) / entry * 100)
    rets, maes, mfes = np.array(rets), np.array(maes), np.array(mfes)
    return {
        "samples": int(rets.size),
        "win_rate": round(float((rets > 0).mean() * 100), 1),
        "median_return": round(float(np.median(rets)), 2),
        "avg_return": round(float(rets.mean()), 2),
        "median_mae": round(float(np.median(maes)), 2),
        "median_mfe": round(float(np.median(mfes)), 2),
        "last_trigger": str(d["date"].iloc[int(idx[-1])]),
    }


def _split_stats(d: pd.DataFrame, mask: pd.Series, horizon: int = HORIZON) -> dict:
    """Full-history stats plus the same numbers on the most recent fifth only."""
    cut = int(len(d) * (1 - HOLDOUT))
    full = _forward(d, mask, horizon)
    recent = _forward(d.iloc[cut:].reset_index(drop=True),
                      mask.iloc[cut:].reset_index(drop=True), horizon)
    full["recent"] = recent
    full["tested"] = full.get("samples", 0) >= MIN_SAMPLES
    return full


SETUPS = {
    "ema200_pullback": {
        "label": "Pullback into the 200 EMA",
        "why": "price returns to the long-term trend line while the trend is still up",
    },
    "bb_oversold": {
        "label": "Oversold at the lower Bollinger band",
        "why": "price is stretched below its 20-day range and RSI is depressed",
    },
    "vwap_lower": {
        "label": "Lower VWAP band",
        "why": "price trades below where the last 20 sessions' volume actually changed hands",
    },
    "support_retest": {
        "label": "Retest of a swing support",
        "why": "a level price has bounced from before",
    },
    "breakout": {
        "label": "Break above a swing resistance on volume",
        "why": "price clears a level that has capped it, with volume confirming",
    },
}


def _masks(d: pd.DataFrame, supports: list[dict], resistances: list[dict]) -> dict:
    near = d["atr"] / d["close"] * 100
    m = {
        "ema200_pullback": ((d["close"] - d["ema200"]).abs() / d["close"] * 100 <= near)
                           & (d["ema200"] > d["ema200"].shift(20)) & (d["rsi"] < 55),
        "bb_oversold": (d["close"] <= d["bb_lower"]) & (d["rsi"] < 40),
        "vwap_lower": (d["close"] <= d["vwap_lower"]),
    }
    sup = pd.Series(False, index=d.index)
    for z in supports:
        sup |= (d["low"] <= z["hi"] * 1.01) & (d["close"] >= z["lo"] * 0.99)
    m["support_retest"] = sup
    res = pd.Series(False, index=d.index)
    for z in resistances:
        res |= (d["close"] > z["hi"]) & (d["close"].shift(1) <= z["hi"])
    m["breakout"] = res & (d["volume"] > 1.5 * d["vol_avg20"])
    return m


# ── the zone list the UI shows ───────────────────────────────────────
def _zone(kind: str, label: str, lo: float, hi: float, ltp: float, why: str,
          setup: Optional[str] = None, stats: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
    lo, hi = (min(lo, hi), max(lo, hi))
    mid = (lo + hi) / 2
    return {
        "kind": kind, "label": label, "low": round(lo, 2), "high": round(hi, 2),
        "mid": round(mid, 2), "distance_pct": round((mid - ltp) / ltp * 100, 2),
        "inside": bool(lo <= ltp <= hi), "why": why,
        "setup": setup, "stats": stats or {}, **(extra or {}),
    }


def entry_zones(d: pd.DataFrame, ltp: float, user_levels: Optional[list[float]] = None,
                lookback: int = 750) -> dict:
    """Every zone worth watching below and above price, each with its tested record.

    ``d`` must already carry the indicator columns from ``metrics.enrich``.
    """
    if d.empty or not ltp:
        return {"status": "error", "message": "no daily history yet", "zones": []}
    recent = d.tail(lookback).reset_index(drop=True)
    r = d.iloc[-1]
    atr = float(r["atr"] or 0) or float(d["close"].iloc[-1]) * 0.02
    highs, lows = pivots(recent, 5)
    tol = max(0.6, atr / float(r["close"]) * 100)
    supports = [z for z in cluster(lows, tol) if z["price"] < ltp]
    resistances = [z for z in cluster(highs, tol) if z["price"] > ltp]
    supports.sort(key=lambda z: (-z["touches"], abs(z["price"] - ltp)))
    resistances.sort(key=lambda z: (-z["touches"], abs(z["price"] - ltp)))

    stats = {k: _split_stats(d, m) for k, m in _masks(d, supports[:6], resistances[:6]).items()}
    zones: list[dict] = []

    for lv in sorted(set(float(x) for x in (user_levels or []))):
        band = max(atr * 0.25, lv * 0.0025)
        zones.append(_zone("research", "Your research level", lv - band, lv + band, ltp,
                           "the level you wrote down when you added this stock",
                           extra={"level": round(lv, 2), "priority": 0}))

    e200, bb_lo, vw_lo = MX._f(r["ema200"]), MX._f(r["bb_lower"]), MX._f(r["vwap_lower"])
    if e200:
        zones.append(_zone("ema200", "200 EMA", e200 - atr * 0.5, e200 + atr * 0.5, ltp,
                           SETUPS["ema200_pullback"]["why"], "ema200_pullback",
                           stats["ema200_pullback"], {"priority": 1}))
    if bb_lo:
        zones.append(_zone("bollinger", "Lower Bollinger band", bb_lo - atr * 0.3, bb_lo + atr * 0.3, ltp,
                           SETUPS["bb_oversold"]["why"], "bb_oversold", stats["bb_oversold"], {"priority": 2}))
    if vw_lo:
        zones.append(_zone("vwap", "Lower VWAP band", vw_lo - atr * 0.3, vw_lo + atr * 0.3, ltp,
                           SETUPS["vwap_lower"]["why"], "vwap_lower", stats["vwap_lower"], {"priority": 2}))
    for z in supports[:3]:
        zones.append(_zone("support", f"Swing support · {z['touches']} touches",
                           z["lo"] - atr * 0.2, z["hi"] + atr * 0.2, ltp,
                           SETUPS["support_retest"]["why"], "support_retest",
                           stats["support_retest"], {"touches": z["touches"], "priority": 1}))
    for z in resistances[:3]:
        zones.append(_zone("resistance", f"Swing resistance · {z['touches']} touches",
                           z["lo"] - atr * 0.2, z["hi"] + atr * 0.2, ltp,
                           SETUPS["breakout"]["why"], "breakout",
                           stats["breakout"], {"touches": z["touches"], "priority": 1}))

    zones.sort(key=lambda z: abs(z["distance_pct"]))
    return {"status": "ok", "zones": zones, "setups": stats, "atr": round(atr, 2),
            "horizon": HORIZON, "min_samples": MIN_SAMPLES}


def best_entry(d: pd.DataFrame, ltp: float, zones: list[dict], sens: Optional[dict] = None) -> dict:
    """The one zone to watch, with a plan built from this stock's own numbers.

    Picked among zones at or below price (an equity buyer's entry), preferring a tested setup
    with a positive median move, then proximity. Returns a stand-aside verdict when nothing
    qualifies rather than inventing a trade.
    """
    if d.empty or not ltp:
        return {"status": "none", "message": "no history"}
    atr = float(d["atr"].iloc[-1] or 0) or ltp * 0.02
    cands = [z for z in zones
             if z["kind"] != "resistance" and (z["inside"] or -MAX_REACH_PCT <= z["distance_pct"] <= 1.0)]
    if not cands:
        return {"status": "none",
                "message": f"no zone within {MAX_REACH_PCT:.0f}% below price — nothing to wait for yet"}

    def rank(z):
        """Nearest first; a level of your own or a setup with a record wins a close call."""
        st = z.get("stats") or {}
        credible = z["kind"] == "research" or bool(st.get("tested"))
        return (0 if z["inside"] else 1, abs(z["distance_pct"]) + (0.0 if credible else UNTESTED_PENALTY_PCT))

    cands.sort(key=rank)
    z = cands[0]
    st = z.get("stats") or {}
    entry_lo, entry_hi = z["low"], z["high"]
    stop = round(entry_lo - atr * 1.0, 2)
    mfe = st.get("median_mfe")
    target = round(z["mid"] * (1 + (mfe or 4.0) / 100), 2)
    risk = max(z["mid"] - stop, 1e-9)
    reward = max(target - z["mid"], 0)
    lines = [f"{z['label']} — {z['why']}"]
    if st.get("tested"):
        lines.append(f"Over {st['samples']} past triggers on this stock, price was higher "
                     f"{HORIZON} sessions later {st['win_rate']}% of the time "
                     f"(median {st['median_return']:+.2f}%, median dip first {st['median_mae']:.2f}%).")
        rec = st.get("recent") or {}
        if rec.get("samples"):
            lines.append(f"In the most recent fifth of the history: {rec['samples']} triggers, "
                         f"{rec['win_rate']}% higher after {HORIZON} sessions "
                         f"(median {rec['median_return']:+.2f}%).")
        else:
            lines.append("No trigger in the most recent fifth of the history — the record is older data.")
    elif z["kind"] == "research":
        lines.append("Your own level: it has no tested record here, it is the price you decided on.")
    else:
        lines.append(f"Fewer than {MIN_SAMPLES} past triggers on this stock — treat the odds as unknown.")
    if sens and sens.get("label"):
        lines.append(f"Trend backdrop right now: {sens['label']} ({sens['score']:+.0f}/100).")
    return {
        "status": "ok", "zone": z, "entry_low": entry_lo, "entry_high": entry_hi,
        "stop": stop, "target": target, "rr": round(reward / risk, 2) if risk else None,
        "risk_pct": round((z["mid"] - stop) / z["mid"] * 100, 2),
        "tested": bool(st.get("tested")), "reasons": lines,
        "waiting": not z["inside"],
    }
