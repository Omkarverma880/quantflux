"""
Market structure and the relative-volume engine.

Two jobs, kept apart on purpose.

Structure answers *where* a level is: the opening range, the previous day's extremes, and the
swing points the session has actually printed. None of these is a signal — a level being touched
is an event, not a reason to trade, and the breakout engine treats them that way.

The volume engine answers *whether anyone showed up*, and it is deliberately awkward to use. It
will not return a number unless it knows what the number means: the source must be named, the
baseline must exclude the candle being judged, and if the series is missing it returns
UNAVAILABLE rather than a neutral-looking 1.0 that every threshold would quietly pass.

Everything is computed from completed candles only. A function that takes a frame takes it up to
and including the decision bar, never beyond.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from research.fvb.data import FUTURES, NONE, OPTIONS, SESSION_OPEN, UNAVAILABLE

# what a volume reading means, as a verdict rather than a bare number
VOL_CONFIRMED = "CONFIRMED"              # above threshold
VOL_WEAK = "WEAK"                        # measured, below threshold
VOL_UNAVAILABLE = "UNAVAILABLE"          # no usable series — never treated as either of the above

# what price and volume did together on the breakout bar
PUSH_UP = "volume up, price advanced"
PUSH_DOWN = "volume up, price declined"
CHURN = "volume up, price went nowhere"
DRIFT = "price advanced without volume"
QUIET = "neither"


@dataclass(frozen=True)
class Params:
    # ── opening range ──
    or_start: int = SESSION_OPEN             # 09:15
    or_minutes: int = 15                     # 09:15-09:30
    # ── levels in play ──
    use_opening_range: bool = True
    use_prev_day: bool = True
    use_swings: bool = False
    swing_lookback: int = 10
    # ── breakout geometry ──
    buffer_mode: str = "atr"                 # atr | points | percent
    buffer_atr_mult: float = 0.15            # volatility-adjusted, the default
    buffer_points: float = 10.0
    buffer_percent: float = 0.05
    atr_length: int = 14
    # ── participation ──
    participation: str = FUTURES             # futures | options | none
    rvol_lookback: int = 20
    rvol_threshold: float = 1.5
    rvol_time_normalised: bool = False       # compare against the same minute on earlier days
    # ── candle quality on the breakout bar ──
    max_wick_pct: float = 50.0               # rejection wick as a share of the candle's range
    min_close_location: float = 0.5          # where the close sits in the range, 0=low 1=high
    # ── regime ──
    choppy_filter: bool = False
    choppy_atr_ratio: float = 0.5            # session range vs ATR; below this is chop
    vix_max: float = 0.0                     # 0 = off
    # ── higher timeframe ──
    htf_minutes: int = 5
    require_htf_trend: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


P = Params()


def atr(d: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average true range on the frame's own bars, shifted so a bar never sees itself."""
    h, l, c = d["high"], d["low"], d["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(length, min_periods=max(2, length // 2)).mean()


def opening_range(day: pd.DataFrame, p: Params = P) -> Optional[dict]:
    """High and low of the opening window, available only once that window has CLOSED.

    The last minute of a 09:15-09:30 range is the candle stamped 09:29, so the range is not
    knowable until 09:30. Returning it earlier would hand the breakout engine a level built
    partly from the future.
    """
    end = p.or_start + p.or_minutes
    w = day[(day["minute"] >= p.or_start) & (day["minute"] < end)]
    if w.empty:
        return None
    return {"high": float(w["high"].max()), "low": float(w["low"].min()),
            "from": int(p.or_start), "until": int(end), "bars": int(len(w)),
            "known_from": int(end)}


def previous_day(spot: pd.DataFrame, d: date) -> Optional[dict]:
    """Yesterday's high, low and close — the only levels that exist before the bell."""
    prior = spot[spot["date"] < d]
    if prior.empty:
        return None
    last = prior["date"].max()
    g = prior[prior["date"] == last]
    return {"date": str(last), "high": float(g["high"].max()),
            "low": float(g["low"].min()), "close": float(g["close"].iloc[-1]),
            "known_from": SESSION_OPEN}


def swings(day: pd.DataFrame, upto_minute: int, lookback: int = 10) -> dict:
    """The session's own structure so far: the last confirmed swing high and low.

    A swing point is only confirmed once ``lookback`` bars have printed on both sides of it, so
    the most recent candidate is always withheld until the right-hand side exists.
    """
    g = day[day["minute"] <= upto_minute]
    if len(g) < lookback * 2 + 1:
        return {"high": None, "low": None, "structure": "not enough bars"}
    h, l = g["high"].to_numpy(), g["low"].to_numpy()
    hi = lo = None
    for i in range(len(g) - lookback - 1, lookback - 1, -1):
        if hi is None and h[i] == h[i - lookback:i + lookback + 1].max():
            hi = float(h[i])
        if lo is None and l[i] == l[i - lookback:i + lookback + 1].min():
            lo = float(l[i])
        if hi is not None and lo is not None:
            break
    closes = g["close"].to_numpy()
    trend = "sideways"
    if hi is not None and lo is not None and len(closes) > 2:
        if closes[-1] > hi:
            trend = "break of structure, up"
        elif closes[-1] < lo:
            trend = "break of structure, down"
        elif closes[-1] > closes[0]:
            trend = "higher"
        elif closes[-1] < closes[0]:
            trend = "lower"
    return {"high": hi, "low": lo, "structure": trend}


def levels_for(day: pd.DataFrame, spot: pd.DataFrame, d: date,
               upto_minute: int, p: Params = P) -> dict:
    """Every level in play at this minute, each carrying the minute it became knowable."""
    out: dict = {"resistance": [], "support": []}
    if p.use_opening_range:
        orr = opening_range(day, p)
        if orr and upto_minute >= orr["known_from"]:
            out["resistance"].append({"name": "opening range high", "price": orr["high"],
                                      "known_from": orr["known_from"]})
            out["support"].append({"name": "opening range low", "price": orr["low"],
                                   "known_from": orr["known_from"]})
            out["opening_range"] = orr
    if p.use_prev_day:
        pv = previous_day(spot, d)
        if pv:
            out["resistance"].append({"name": "previous day high", "price": pv["high"],
                                      "known_from": pv["known_from"]})
            out["support"].append({"name": "previous day low", "price": pv["low"],
                                   "known_from": pv["known_from"]})
            out["previous_day"] = pv
    if p.use_swings:
        sw = swings(day, upto_minute, p.swing_lookback)
        if sw["high"] is not None:
            out["resistance"].append({"name": "swing high", "price": sw["high"],
                                      "known_from": upto_minute})
        if sw["low"] is not None:
            out["support"].append({"name": "swing low", "price": sw["low"],
                                   "known_from": upto_minute})
        out["swings"] = sw
    return out


def buffer_for(p: Params, atr_now: Optional[float], price: float) -> float:
    """How far past a level a close has to be before it counts as a break."""
    if p.buffer_mode == "points":
        return float(p.buffer_points)
    if p.buffer_mode == "percent":
        return float(price) * p.buffer_percent / 100.0
    if atr_now is None or not np.isfinite(atr_now):
        return float(p.buffer_points)          # ATR not warm yet: fall back, never zero
    return float(atr_now) * p.buffer_atr_mult


# ── the volume engine ────────────────────────────────────────────────────────
def _series_for(source: str, bars: pd.DataFrame) -> tuple[Optional[pd.Series], Optional[str]]:
    """The volume column to judge, or the reason there isn't one."""
    if source == NONE:
        return None, "no participation filter configured"
    if source == FUTURES:
        if "fut_volume" not in bars.columns or bars["fut_volume"].notna().sum() == 0:
            return None, ("NIFTY futures volume is not available — spot/NIFTYFUT has never "
                          "been ingested. The index volume column is not a substitute and is "
                          "not read.")
        return bars["fut_volume"], None
    if source == OPTIONS:
        if "opt_volume" not in bars.columns or bars["opt_volume"].notna().sum() == 0:
            return None, "option volume was not supplied for this session"
        return bars["opt_volume"], None
    return None, f"unknown participation source {source!r}"


def relative_volume(bars: pd.DataFrame, at_minute: int, p: Params = P) -> dict:
    """How this completed candle's volume compares with the ones before it.

    The candle being judged is excluded from its own baseline, which is the difference between
    a ratio that can exceed one and a ratio that is structurally dragged toward it.

    With ``rvol_time_normalised`` the baseline is the SAME minute on earlier sessions instead of
    the preceding minutes of this one, because 09:20 and 14:20 are not comparable and a flat
    lookback quietly flags every morning as heavy.
    """
    series, why = _series_for(p.participation, bars)
    if series is None:
        return {"state": VOL_UNAVAILABLE, "rvol": None, "reason": why,
                "source": p.participation, "current": None, "baseline": None}

    frame = bars.loc[bars["minute"] <= at_minute]
    if frame.empty:
        return {"state": VOL_UNAVAILABLE, "rvol": None, "reason": "no bars at or before "
                "the decision minute", "source": p.participation,
                "current": None, "baseline": None}
    cur = series.loc[frame.index[-1]]
    if not np.isfinite(cur):
        return {"state": VOL_UNAVAILABLE, "rvol": None,
                "reason": "the decision candle has no volume", "source": p.participation,
                "current": None, "baseline": None}

    if p.rvol_time_normalised and "date" in bars.columns:
        same = bars[(bars["minute"] == at_minute) & (bars["date"] < frame["date"].iloc[-1])]
        hist = _series_for(p.participation, same)[0]
        hist = hist.dropna().tail(p.rvol_lookback) if hist is not None else pd.Series(dtype=float)
        basis = "same minute on the previous sessions"
    else:
        prior = frame.iloc[:-1].tail(p.rvol_lookback)
        hist = series.loc[prior.index].dropna()
        basis = "the preceding candles of this session"

    if len(hist) < max(3, p.rvol_lookback // 3):
        return {"state": VOL_UNAVAILABLE, "rvol": None,
                "reason": f"only {len(hist)} baseline candles, too few to judge against",
                "source": p.participation, "current": float(cur), "baseline": None}
    mean = float(hist.mean())
    if mean <= 0:
        return {"state": VOL_UNAVAILABLE, "rvol": None,
                "reason": "the baseline is zero, so a ratio would be meaningless",
                "source": p.participation, "current": float(cur), "baseline": mean}
    rv = float(cur) / mean
    return {"state": VOL_CONFIRMED if rv >= p.rvol_threshold else VOL_WEAK,
            "rvol": round(rv, 3), "threshold": p.rvol_threshold, "source": p.participation,
            "current": float(cur), "baseline": round(mean, 1), "basis": basis,
            "samples": int(len(hist)), "reason": None}


def price_volume_character(bar: pd.Series, vol: dict, atr_now: Optional[float]) -> str:
    """What price did while that volume arrived. High volume alone names no winner."""
    rng = float(bar["high"]) - float(bar["low"])
    body = float(bar["close"]) - float(bar["open"])
    heavy = vol.get("state") == VOL_CONFIRMED
    small = (abs(body) < 0.25 * rng) if rng > 0 else True
    if heavy and small:
        return CHURN
    if heavy and body > 0:
        return PUSH_UP
    if heavy and body < 0:
        return PUSH_DOWN
    if not heavy and abs(body) > 0.5 * (atr_now or rng or 1):
        return DRIFT
    return QUIET


def candle_shape(bar: pd.Series, bullish: bool) -> dict:
    """Close location and rejection wick — whether the bar closed where it claimed to go."""
    hi, lo = float(bar["high"]), float(bar["low"])
    cl, op = float(bar["close"]), float(bar["open"])
    rng = hi - lo
    if rng <= 0:
        return {"range": 0.0, "close_location": 0.5, "wick_pct": 0.0, "ok": False,
                "why": "the candle has no range"}
    loc = (cl - lo) / rng if bullish else (hi - cl) / rng
    wick = (hi - max(op, cl)) / rng if bullish else (min(op, cl) - lo) / rng
    return {"range": round(rng, 2), "close_location": round(loc, 3),
            "wick_pct": round(wick * 100, 1), "ok": True, "why": None}


def choppy(day: pd.DataFrame, upto_minute: int, atr_now: Optional[float], p: Params) -> dict:
    """Is the session going nowhere? Measured, and only applied when switched on."""
    if not p.choppy_filter:
        return {"choppy": False, "checked": False}
    g = day[day["minute"] <= upto_minute]
    if g.empty or not atr_now or not np.isfinite(atr_now):
        return {"choppy": False, "checked": False, "why": "no ATR yet"}
    rng = float(g["high"].max() - g["low"].min())
    bars = max(1, len(g))
    expected = float(atr_now) * np.sqrt(bars)
    ratio = rng / expected if expected > 0 else 1.0
    return {"choppy": ratio < p.choppy_atr_ratio, "checked": True,
            "session_range": round(rng, 1), "ratio": round(ratio, 3),
            "threshold": p.choppy_atr_ratio}
