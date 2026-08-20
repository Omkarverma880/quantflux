"""
QMRE feature engine — PURE and LOOK-AHEAD-SAFE.

``compute_features`` receives ONLY the intraday candles at/before a cutoff plus a
context of facts knowable at that cutoff (prior-day levels, ATR from prior days,
expected volume-by-now, benchmark return). It never sees a future candle, so the
same function is correct for Live (cutoff = now), Replay (cutoff = replay clock)
and Backtest (cutoff iterated). This is the mechanism that guarantees no leakage
and that Live and Backtest share identical logic.
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Optional


def hlc3(c) -> float:
    return (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0


def vwap_of(candles: list[dict]) -> float:
    """Cumulative VWAP (HLC3 × vol / vol; falls back to HLC3 mean if no volume)."""
    pv = v = tp = 0.0
    n = 0
    for c in candles:
        vol = float(c.get("volume", 0) or 0)
        t = hlc3(c)
        pv += t * vol; v += vol; tp += t; n += 1
    if v > 0:
        return pv / v
    return tp / n if n else 0.0


def _slope_pct(series: list[float]) -> float:
    """Simple % slope of a short series (last vs first) — cheap trend proxy."""
    series = [x for x in series if x]
    if len(series) < 2 or series[0] == 0:
        return 0.0
    return (series[-1] - series[0]) / series[0] * 100.0


def compute_features(candles: list[dict], ctx: dict, cfg: dict) -> Optional[dict]:
    """candles: today's intraday candles with ``_dt`` ≤ cutoff (ascending).
    ctx: {prev_close, prev_high, prev_low, atr, atr_pct, expected_cum_vol,
          avg_day_value, bench_ret_pct, sector_ret_pct(optional), depth(optional)}.
    Returns a flat feature dict, or None if not enough data yet."""
    if not candles:
        return None
    candles = [c for c in candles if c.get("_dt") is not None]
    if not candles:
        return None
    ltp = float(candles[-1]["close"])
    if ltp <= 0:
        return None
    prev_close = float(ctx.get("prev_close") or 0) or ltp
    change_pct = (ltp - prev_close) / prev_close * 100.0 if prev_close else 0.0

    day_high = max(float(c["high"]) for c in candles)
    day_low = min(float(c["low"]) for c in candles)
    cum_vol = sum(float(c.get("volume", 0) or 0) for c in candles)

    # ── VWAP structure ──
    vwap = vwap_of(candles)
    vwap_dist = (ltp - vwap) / vwap * 100.0 if vwap else 0.0
    slope_n = int(cfg.get("vwap_slope_lookback", 6))
    vseries = []
    for i in range(max(1, len(candles) - slope_n), len(candles) + 1):
        vseries.append(vwap_of(candles[:i]))
    vwap_slope = _slope_pct(vseries)
    # how consistently price has held above VWAP (fraction of candles closing > their running vwap)
    above_cnt = 0
    for i in range(1, len(candles) + 1):
        if float(candles[i - 1]["close"]) >= vwap_of(candles[:i]):
            above_cnt += 1
    vwap_hold = above_cnt / len(candles)

    # ── opening range ──
    or_min = int(cfg.get("opening_range_min", 15))
    open_ts = candles[0]["_dt"]
    or_candles = [c for c in candles
                  if (c["_dt"] - open_ts).total_seconds() < or_min * 60]
    or_high = max((float(c["high"]) for c in or_candles), default=day_high)
    or_low = min((float(c["low"]) for c in or_candles), default=day_low)
    or_ready = len(candles) > len(or_candles)     # past the opening range window
    orb = "none"
    if or_ready:
        if ltp > or_high:
            orb = "breakout"
        elif ltp < or_low:
            orb = "breakdown"

    # ── relative volume (time-of-day normalised) ──
    expected = float(ctx.get("expected_cum_vol") or 0)
    rvol = (cum_vol / expected) if expected > 0 else None

    # ── breakout vs prior-day high / OR high, volume-confirmed ──
    prev_high = float(ctx.get("prev_high") or 0)
    pdh_break = bool(prev_high and ltp > prev_high)
    rvol_ok = (rvol is not None and rvol >= float(cfg.get("breakout_rvol_min", 1.5)))
    vwap_ok = (not cfg.get("breakout_needs_vwap", True)) or (ltp >= vwap)
    breakout_confirmed = bool((pdh_break or orb == "breakout") and rvol_ok and vwap_ok)

    # ── relative strength vs benchmark (and sector if provided) ──
    bench = float(ctx.get("bench_ret_pct") or 0)
    rs = change_pct - bench
    sector_ret = ctx.get("sector_ret_pct")
    rs_sector = (change_pct - float(sector_ret)) if sector_ret is not None else None

    # ── volatility / ATR ──
    atr = float(ctx.get("atr") or 0)
    atr_pct = float(ctx.get("atr_pct") or 0)
    ext_vwap_atr = (abs(ltp - vwap) / atr) if atr else 0.0     # how many ATRs from VWAP (exhaustion)

    # ── momentum (recent candle push) ──
    closes = [float(c["close"]) for c in candles]
    mom = _slope_pct(closes[-min(len(closes), slope_n):])

    return {
        "ltp": round(ltp, 2), "prev_close": round(prev_close, 2),
        "change_pct": round(change_pct, 2),
        "day_high": round(day_high, 2), "day_low": round(day_low, 2),
        "vwap": round(vwap, 2), "vwap_dist": round(vwap_dist, 2),
        "vwap_slope": round(vwap_slope, 3), "vwap_hold": round(vwap_hold, 2),
        "above_vwap": ltp >= vwap,
        "or_high": round(or_high, 2), "or_low": round(or_low, 2),
        "or_ready": or_ready, "orb": orb,
        "cum_vol": int(cum_vol), "rvol": (round(rvol, 2) if rvol is not None else None),
        "pdh_break": pdh_break, "breakout_confirmed": breakout_confirmed,
        "rs": round(rs, 2), "rs_sector": (round(rs_sector, 2) if rs_sector is not None else None),
        "bench_ret": round(bench, 2),
        "atr": round(atr, 2), "atr_pct": round(atr_pct, 2), "ext_vwap_atr": round(ext_vwap_atr, 2),
        "momentum": round(mom, 3),
        "avg_day_value_cr": ctx.get("avg_day_value_cr"),
        "depth": ctx.get("depth"),
        "n_candles": len(candles),
    }
