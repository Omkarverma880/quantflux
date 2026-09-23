"""
Hunter — what a setup is, in one place.

Every number shown on a card and every stage a stock sits in is computed here, from daily bars
only, with no look-ahead: each row uses that session's close and everything before it.

The swing setup, in plain words:
  a strong stock (above its rising moving averages, near its 52-week high, outperforming the
  market) pauses in a tight, quiet range under a ceiling — the trading drying up as it coils.
  A close above that ceiling on heavy volume starts it off.

Stages a stock can be in:
  FORMING   in a valid base, still under the ceiling
  BREAKOUT  closed above the ceiling within the last 5 sessions, still holding
  CLIMBING  broke out earlier, still above its trailing stop
  PLAYED_OUT broke out in the last year and has since lost the stop
  NONE      nothing structural right now
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

MIN_BARS = 220                      # a year of history, so the 200-day average means something


@dataclass(frozen=True)
class Params:
    # trend
    near_high_pct: float = 30.0     # within this % of the 52-week high
    above_low_pct: float = 30.0     # at least this % above the 52-week low
    # base
    base_min: int = 10              # sessions
    base_max: int = 60
    base_max_depth: float = 35.0    # % from base high to base low
    dry_up: float = 1.0             # last 10 days' volume vs the base's own average
    near_pivot_pct: float = 20.0    # how close to the ceiling counts as "ready"
    strict_base: bool = False       # True = require contraction AND dry-up; off = both are just measures
    # breakout
    pivot_lookback: int = 40        # the ceiling is the highest high of this many sessions
    breakout_volume: float = 1.3    # × the 50-day average volume on the breakout day
    fresh_days: int = 5
    climb_days: int = 120
    stop_pct: float = 8.0           # a close this far under the breakout price ends the trade
    max_extended_pct: float = 15.0  # further than this above the pivot is chasing
    # liquidity
    min_turnover_cr: float = 2.0    # ₹ crore median daily turnover over 50 sessions

    def as_dict(self) -> dict:
        return asdict(self)


P = Params()


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Daily bars → every derived series the scan needs."""
    d = df.copy().reset_index(drop=True)
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"]
    d["sma50"] = c.rolling(50).mean()
    d["sma150"] = c.rolling(150).mean()
    d["sma200"] = c.rolling(200).mean()
    d["sma200_up"] = d["sma200"] > d["sma200"].shift(20)
    d["high_52w"] = h.rolling(250, min_periods=60).max()
    d["low_52w"] = l.rolling(250, min_periods=60).min()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["atr_pct"] = d["atr14"] / c * 100
    d["vol50"] = v.rolling(50).mean()
    d["vol10"] = v.rolling(10).mean()
    d["turnover_cr"] = (c * v / 1e7).rolling(50).median()
    d["pivot_hist"] = h.rolling(P.pivot_lookback).max().shift(1)
    return d


def relative_strength(d: pd.DataFrame) -> float | None:
    """IBD-style weighted 12-month return: the most recent quarter counts double."""
    c = d["close"]
    if len(c) < 260:
        return None
    def r(n):
        past = c.iloc[-n - 1]
        return (c.iloc[-1] / past - 1) * 100 if past > 0 else None
    q = [r(63), r(126), r(189), r(252)]
    if any(x is None for x in q):
        return None
    return round(0.4 * q[0] + 0.2 * q[1] + 0.2 * q[2] + 0.2 * q[3], 2)


def pct_of(a: float, b: float) -> float | None:
    """(a / b − 1) as a percentage, or None when b cannot be divided by."""
    try:
        b = float(b)
        if not np.isfinite(b) or b == 0:
            return None
        return round((float(a) / b - 1) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def ratio(a: float, b: float, nd: int = 2) -> float | None:
    try:
        b = float(b)
        if not np.isfinite(b) or b == 0:
            return None
        return round(float(a) / b, nd)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def trend_ok(row: pd.Series, p: Params = P) -> tuple[bool, list[dict]]:
    """The leadership filter, with every condition and its value, for the card."""
    c = float(row["close"])
    checks = [
        ("above 50-day average", c > row["sma50"], f"{c:,.1f} vs {row['sma50']:,.1f}"),
        ("50 > 150 > 200-day averages", row["sma50"] > row["sma150"] > row["sma200"],
         f"{row['sma50']:,.0f} / {row['sma150']:,.0f} / {row['sma200']:,.0f}"),
        ("200-day average rising", bool(row["sma200_up"]), "over the last month"),
        (f"within {p.near_high_pct:g}% of the 52-week high",
         c >= row["high_52w"] * (1 - p.near_high_pct / 100),
         f"{(c / row['high_52w'] - 1) * 100:+.1f}% from {row['high_52w']:,.1f}"),
        (f"at least {p.above_low_pct:g}% above the 52-week low",
         c >= row["low_52w"] * (1 + p.above_low_pct / 100),
         f"{(c / row['low_52w'] - 1) * 100:+.0f}% above {row['low_52w']:,.1f}"),
    ]
    rows = [{"label": t, "passed": bool(ok), "detail": det} for t, ok, det in checks]
    return all(r["passed"] for r in rows), rows


def find_base(d: pd.DataFrame, p: Params = P) -> dict | None:
    """The tightest valid consolidation ending at the latest bar.

    Walks candidate lengths and keeps the longest one that is shallow enough, whose volatility is
    contracting and whose trading has dried up — a stock resting, not falling.
    """
    if len(d) < p.base_max + 5:
        return None
    best = None
    for L in range(p.base_max, p.base_min - 1, -1):
        w = d.iloc[-L:]
        hi, lo = float(w["high"].max()), float(w["low"].min())
        if hi <= 0:
            continue
        depth = (hi - lo) / hi * 100
        if depth > p.base_max_depth:
            continue
        third = max(3, L // 3)
        early, late = w.iloc[:third], w.iloc[-third:]
        contracting = float(late["atr14"].mean()) < float(early["atr14"].mean())
        dry = float(w["volume"].tail(10).mean()) < p.dry_up * float(w["volume"].mean())
        close = float(d["close"].iloc[-1])
        # tightening and dry-up are reported on the card; only strict mode makes them gates
        if p.strict_base and not (contracting and dry):
            continue
        best = {"length": L, "pivot": hi, "low": lo, "depth_pct": round(depth, 1),
                "from_pivot_pct": round((close / hi - 1) * 100, 2),
                "contracting": contracting, "dry_volume": dry,
                "volume_ratio": round(float(w["volume"].tail(10).mean()) / max(float(w["volume"].mean()), 1), 2)}
        break
    return best


def last_breakout(d: pd.DataFrame, p: Params = P) -> dict | None:
    """The most recent close through the ceiling on real volume, and what has happened since."""
    c, pv, v, av = d["close"], d["pivot_hist"], d["volume"], d["vol50"]
    crossed = (c > pv) & (c.shift(1) <= pv.shift(1)) & (v >= p.breakout_volume * av)
    idx = np.flatnonzero(crossed.to_numpy())
    if not len(idx):
        return None
    i = int(idx[-1])
    days_since = len(d) - 1 - i
    if days_since > p.climb_days:
        return None
    after = d.iloc[i:]
    entry = float(c.iloc[i])
    stop = entry * (1 - p.stop_pct / 100)
    under = (after["close"] < after["sma50"]).to_numpy()
    two_under = bool(np.any(under[:-1] & under[1:])) if len(under) > 1 else False
    broke_stop = bool((after["close"] < stop).any() or two_under)
    peak = float(after["high"].max())
    now = float(c.iloc[-1])
    return {
        "date": str(pd.Timestamp(d["date"].iloc[i]).date()), "price": round(entry, 2),
        "pivot": round(float(pv.iloc[i]), 2), "days_since": int(days_since),
        "volume_x": round(float(v.iloc[i] / max(av.iloc[i], 1)), 1),
        "stop": round(stop, 2), "alive": not broke_stop,
        "gain_pct": round((now / entry - 1) * 100, 2),
        "peak_gain_pct": round((peak / entry - 1) * 100, 2),
    }


SCREENS = {
    "base_break": {"name": "Base breakout", "blurb": "A strong stock coiling under a ceiling, and the close that clears it."},
    "pullback_50": {"name": "Pull-back to the 50-day", "blurb": "An established leader easing back into its 50-day average and holding there."},
    "high_52w": {"name": "New 52-week high", "blurb": "Closing at a new one-year high on heavier trading than usual."},
    "squeeze": {"name": "Volatility squeeze", "blurb": "Daily range at a six-month low — the coil before a move, in either direction."},
    "pocket_pivot": {"name": "Pocket pivot", "blurb": "An up day on bigger volume than any down day in the last ten — buyers stepping in first."},
}


def screens(d: pd.DataFrame, trend: bool, p: Params = P) -> dict:
    """Every screen, evaluated on the latest bar. Each one says whether it fired and why."""
    out: dict = {}
    row = d.iloc[-1]
    c = float(row["close"])
    v, av = float(row["volume"]), float(row["vol50"])
    sma50 = float(row["sma50"])

    near_pct = pct_of(c, sma50)
    near = abs(near_pct) if near_pct is not None else 999.0
    out["pullback_50"] = {
        "hit": bool(trend and sma50 > 0 and c >= sma50 and near <= 3.0),
        "why": f"{near:.1f}% from its 50-day average at {sma50:,.1f}, still above it",
    }
    hi250 = float(d["high"].rolling(250, min_periods=60).max().iloc[-1])
    out["high_52w"] = {
        "hit": bool(trend and hi250 > 0 and c >= hi250 * 0.999 and v >= 1.3 * av),
        "why": f"closed at a new 52-week high on {v / max(av, 1.0):.1f}× its usual trading",
    }
    atr_now = float(row["atr_pct"])
    atr_floor = float(d["atr_pct"].tail(126).min())
    out["squeeze"] = {
        "hit": bool(trend and np.isfinite(atr_floor) and atr_floor > 0 and atr_now <= atr_floor * 1.05),
        "why": f"daily range {atr_now:.2f}% — the quietest it has been in six months",
    }
    tail = d.tail(11).iloc[:-1]
    down_vol = tail.loc[tail["close"] < tail["close"].shift(1), "volume"]
    biggest_down = float(down_vol.max()) if len(down_vol) else 0.0
    out["pocket_pivot"] = {
        "hit": bool(trend and c > float(d["close"].iloc[-2]) and c > sma50 and biggest_down > 0 and v > biggest_down),
        "why": f"up day on {v / max(biggest_down, 1.0):.1f}× the biggest down-day volume of the last ten",
    }
    return out


def extra_measures(d: pd.DataFrame, base: dict | None, p: Params = P) -> dict:
    """The rest of what a card shows, each one defined here so the tooltip can be exact."""
    c, h, v = d["close"], d["high"], d["volume"]
    out: dict = {}
    # tightening: how much calmer the end of the base is than its start (under 1 = contracting)
    if base:
        w = d.iloc[-base["length"]:]
        third = max(3, base["length"] // 3)
        early = float(w["atr14"].head(third).mean())
        late = float(w["atr14"].tail(third).mean())
        out["tightening"] = round(late / early, 2) if early > 0 else None
        out["base_weeks"] = round(base["length"] / 5, 1)
    # up/down volume: volume on rising days minus falling days, over the total, last 50 sessions
    tail = d.tail(50)
    up = float(tail.loc[tail["close"] >= tail["close"].shift(1), "volume"].sum())
    dn = float(tail.loc[tail["close"] < tail["close"].shift(1), "volume"].sum())
    out["up_down_volume"] = round((up - dn) / (up + dn), 2) if (up + dn) > 0 else None
    # blue sky: at the highest price in everything stored for it
    all_high = float(h.max())
    out["blue_sky"] = bool(float(c.iloc[-1]) >= all_high * 0.98)
    # quiet day: today's range and volume both well below normal — the coil tightening
    rng = float(d["high"].iloc[-1] - d["low"].iloc[-1])
    out["quiet_day"] = bool(rng < 0.6 * float(d["atr14"].iloc[-1]) and float(v.iloc[-1]) < 0.8 * float(d["vol50"].iloc[-1]))
    # powering up: closing in the top half of the last five sessions on rising volume
    five = d.tail(5)
    span = float(five["high"].max() - five["low"].min())
    out["powering_up"] = bool(span > 0
                              and (float(c.iloc[-1]) - float(five["low"].min())) / span > 0.6
                              and float(v.tail(3).mean()) > float(v.tail(10).mean()))
    return out


def breakout_history(d: pd.DataFrame, p: Params = P) -> dict:
    """Every breakout this year: how many bases it has built, and what the first one has returned."""
    c, pv, v, av = d["close"], d["pivot_hist"], d["volume"], d["vol50"]
    crossed = (c > pv) & (c.shift(1) <= pv.shift(1)) & (v >= p.breakout_volume * av)
    year = pd.to_datetime(d["date"]).dt.year
    this_year = int(year.iloc[-1])
    idx = [i for i in np.flatnonzero(crossed.to_numpy()) if int(year.iloc[i]) == this_year]
    events = [{"date": str(pd.Timestamp(d["date"].iloc[i]).date()), "price": round(float(c.iloc[i]), 2),
               "i": int(i)} for i in idx]
    out = {"count_this_year": len(events), "events": events}
    if events:
        first = events[0]
        out["first"] = {**{k: first[k] for k in ("date", "price")},
                        "held_pct": round((float(c.iloc[-1]) / first["price"] - 1) * 100, 1)}
    return out


def to_weekly(d: pd.DataFrame) -> pd.DataFrame:
    """Daily bars folded into weeks, for the longer view on the stock page."""
    w = d.copy()
    w["date"] = pd.to_datetime(w["date"])
    g = w.resample("W-FRI", on="date").agg(open=("open", "first"), high=("high", "max"),
                                           low=("low", "min"), close=("close", "last"),
                                           volume=("volume", "sum")).dropna().reset_index()
    return indicators(g)


def chart_payload(d: pd.DataFrame, base: dict | None, bo: dict | None, bars: int = 140,
                  p: Params = P) -> dict:
    """The last few months of candles, plus where the ceiling, the base and past breakouts sit."""
    w = d.tail(bars).reset_index(drop=True)
    hist = breakout_history(d, p)
    start = len(d) - len(w)
    flags = [{"i": e["i"] - start, "date": e["date"]} for e in hist["events"] if e["i"] - start >= 0]
    ma = {k: [None if pd.isna(x) else round(float(x), 2) for x in w[k]] for k in ("sma50", "sma150", "sma200")}
    out = {
        "candles": [{"d": str(pd.Timestamp(r.date).date()), "o": round(float(r.open), 2),
                     "h": round(float(r.high), 2), "l": round(float(r.low), 2),
                     "c": round(float(r.close), 2), "v": float(r.volume)} for r in w.itertuples()],
        **ma, "history": hist["events"], "first": hist.get("first"),
        "flags": flags, "pivot": None, "base_from": None, "base_to": None,
    }
    if base:
        out["pivot"] = round(base["pivot"], 2)
        out["base_from"] = max(0, len(w) - base["length"])
        out["base_to"] = len(w) - 1
    elif bo:
        out["pivot"] = bo["pivot"]
        out["breakout_i"] = len(w) - 1 - bo["days_since"]
        out["stop"] = bo["stop"]
    return out


def classify(d: pd.DataFrame, p: Params = P) -> dict:
    """One stock → its stage now, with the measures behind it."""
    if len(d) < MIN_BARS:
        return {"stage": "NONE", "why": "not enough history yet"}
    row = d.iloc[-1]
    close = float(row["close"])
    # a suspended or badly recorded stock can carry zeros; nothing below can divide by those
    needed = [close, float(row["sma50"]), float(row["sma150"]), float(row["sma200"]),
              float(row["high_52w"]), float(row["low_52w"])]
    if any((not np.isfinite(x)) or x <= 0 for x in needed):
        return {"stage": "NONE", "why": "prices for this stock are zero or missing in the stored candles"}
    ok, checks = trend_ok(row, p)
    bo = last_breakout(d, p)
    base = find_base(d, p)
    out = {
        "close": round(close, 2), "trend_ok": ok, "trend_checks": checks,
        "atr_pct": round(float(row["atr_pct"]), 2),
        "turnover_cr": round(float(row["turnover_cr"]), 2) if pd.notna(row["turnover_cr"]) else None,
        "from_52w_high_pct": pct_of(close, row["high_52w"]),
        "above_50dma_pct": pct_of(close, row["sma50"]),
        "volume_x": ratio(row["volume"], max(float(row["vol50"]), 1.0)),
        "rs_raw": relative_strength(d), "base": base, "breakout": bo,
        "day": {"date": str(pd.Timestamp(row["date"]).date()), "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2), "low": round(float(row["low"]), 2),
                "close": round(close, 2), "volume": float(row["volume"]),
                "change_pct": pct_of(close, d["close"].iloc[-2]) if len(d) > 1 else None},
        **extra_measures(d, base, p),
    }
    hist = breakout_history(d, p)
    out["bases_this_year"] = hist["count_this_year"]
    out["first_breakout"] = hist.get("first")
    out["screens"] = screens(d, ok, p)
    if bo and bo["alive"] and bo["days_since"] <= p.fresh_days:
        out.update(stage="BREAKOUT", why=f"closed above {bo['pivot']:,.1f} on {bo['volume_x']}× its usual trading",
                   entry=bo["price"], stop=bo["stop"],
                   extended_pct=round((close / bo["pivot"] - 1) * 100, 2))
    elif bo and bo["alive"] and bo["days_since"] <= p.climb_days and close > bo["price"]:
        out.update(stage="CLIMBING", why=f"up {bo['gain_pct']:.1f}% since breaking out {bo['days_since']} sessions ago",
                   entry=bo["price"], stop=bo["stop"])
    elif bo and not bo["alive"]:
        out.update(stage="PLAYED_OUT", why=f"broke out {bo['days_since']} sessions ago and has since lost its stop",
                   entry=bo["price"], stop=bo["stop"])
    elif ok and base and base["from_pivot_pct"] <= 0 and abs(base["from_pivot_pct"]) <= p.near_pivot_pct:
        out.update(stage="FORMING",
                   why=(f"{base['length']}-session base, {base['depth_pct']}% deep, trading down to "
                        f"{base['volume_ratio']}× its own average — {abs(base['from_pivot_pct']):.1f}% under {base['pivot']:,.1f}"),
                   entry=round(base["pivot"], 2), stop=round(base["low"], 2))
    else:
        out.update(stage="NONE", why="no base and no live breakout")
    return out
