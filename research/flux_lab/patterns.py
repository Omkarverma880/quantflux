"""
Flux Lab — the two hammer definitions, translated from Pine line by line.

Two TradingView indicators describe the same candle from different angles, and this file keeps
both, so a signal can require one, the other, or both at once.

REJECTION (from "Hammer and inverted Hammer")
    A long tail that pokes past recent prices and is rejected.
      lower tail  : low < min(open, close) − range × tail% ,  and  low < previous low
      consecutive : that must hold for N bars in a row
      new extreme : the low must undercut the lowest low of the previous `lookback` bars
      commitment  : at least `beyond%` of the candle's own range must sit below that old low
    The upper-tail version is the exact mirror and marks a rejection of higher prices.

SHAPE (from "Hammers & Stars")
    The body sits at the far end of the range, on a candle big enough to mean something.
      fib level   : hammer needs min(open, close) ≥ high − range × fib  (body in the top 60%)
      ATR filter  : range ≥ ATR(14) × atr_filter — no doji-sized signals
      swing filter: the bar (or the one before it) is the lowest low of the last 10
      colour      : optional — hammer must close up, star must close down
      EMA filter  : optional — hammer only above the EMA, star only below

Every check returns its value as well as its verdict, so the page can show why a bar qualified
and why the one next to it did not.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

BULLISH, BEARISH = "BULLISH", "BEARISH"


@dataclass(frozen=True)
class Params:
    # ── rejection (script one) ──
    tail_pct: float = 70.0          # the tail must be this % of the candle's range
    consecutive: int = 1            # bars in a row that must show the tail
    lookback: int = 5               # previous bars whose extreme must be broken
    beyond_pct: float = 50.0        # % of the candle's range beyond that old extreme
    # ── shape (script two) ──
    fib_level: float = 0.40         # body must sit beyond this fraction of the range
    atr_filter: float = 0.1         # range ≥ ATR(14) × this
    atr_length: int = 14
    swing_lookback: int = 10        # bars for the swing high/low filter
    colour_filter: bool = False     # hammer must close up, star must close down
    use_ema_filter: bool = False
    ema_length: int = 50
    # ── how the two are combined ──
    mode: str = "both"              # both | either | rejection | shape
    sides: str = "both"             # both | bullish | bearish

    def as_dict(self) -> dict:
        return asdict(self)


P = Params()
MODES = {
    "both": "a bar must satisfy both indicators",
    "either": "either indicator is enough",
    "rejection": "only the long-tail rejection rules",
    "shape": "only the body-at-the-end rules",
}


def prepare(df: pd.DataFrame, p: Params = P) -> pd.DataFrame:
    """Add every series both scripts need. No look-ahead: each row uses itself and earlier rows."""
    d = df.reset_index(drop=True).copy()
    o, h, l, c = (d[k].astype(float) for k in ("open", "high", "low", "close"))
    rng = (h - l).replace(0, np.nan)
    d["range"] = rng
    d["body_low"] = np.minimum(o, c)
    d["body_high"] = np.maximum(o, c)

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / p.atr_length, adjust=False).mean()
    d["ema"] = c.ewm(span=p.ema_length, adjust=False).mean()

    # ta.highest / ta.lowest include the current bar
    d["swing_high"] = (h == h.rolling(p.swing_lookback).max()) | (h.shift(1) == h.rolling(p.swing_lookback).max())
    d["swing_low"] = (l == l.rolling(p.swing_lookback).min()) | (l.shift(1) == l.rolling(p.swing_lookback).min())

    # lowestLowInPreviousCandles(count): low[1] … low[count+1]
    d["prev_lowest"] = l.shift(1).rolling(p.lookback + 1).min()
    d["prev_highest"] = h.shift(1).rolling(p.lookback + 1).max()
    d["pct_below"] = (d["prev_lowest"] - l) / rng * 100
    d["pct_above"] = (h - d["prev_highest"]) / rng * 100

    # one bar's tails, before the "N in a row" test
    d["has_lower_tail"] = (l < d["body_low"] - rng * p.tail_pct / 100) & (l < l.shift(1))
    d["has_upper_tail"] = (h > d["body_high"] + rng * p.tail_pct / 100) & (h > h.shift(1))
    n = max(1, int(p.consecutive))
    d["lower_tails"] = d["has_lower_tail"].rolling(n).sum() == n
    d["upper_tails"] = d["has_upper_tail"].rolling(n).sum() == n

    # bullFib = high − range × fib ; bearFib = low + range × fib
    d["bull_fib"] = h - (h - l) * p.fib_level
    d["bear_fib"] = l + (h - l) * p.fib_level
    return d


def _check(label: str, passed, detail: str) -> dict:
    return {"label": label, "passed": bool(passed), "detail": detail}


def rejection(row: pd.Series, side: str, p: Params = P) -> tuple[bool, list[dict]]:
    """Script one: a long tail that breaks a recent extreme and is rejected."""
    if side == BULLISH:
        checks = [
            _check(f"lower tail ≥ {p.tail_pct:g}% of the range, {p.consecutive} bar(s) in a row",
                   row["lower_tails"], f"low {row['low']:,.2f} vs body {row['body_low']:,.2f}"),
            _check(f"undercuts the lowest low of the last {p.lookback} bars",
                   row["low"] < row["prev_lowest"], f"{row['low']:,.2f} vs {row['prev_lowest']:,.2f}"),
            _check(f"at least {p.beyond_pct:g}% of the candle sits below that low",
                   (row["pct_below"] or 0) >= p.beyond_pct, f"{row['pct_below']:.0f}% of its range"),
        ]
    else:
        checks = [
            _check(f"upper tail ≥ {p.tail_pct:g}% of the range, {p.consecutive} bar(s) in a row",
                   row["upper_tails"], f"high {row['high']:,.2f} vs body {row['body_high']:,.2f}"),
            _check(f"clears the highest high of the last {p.lookback} bars",
                   row["high"] > row["prev_highest"], f"{row['high']:,.2f} vs {row['prev_highest']:,.2f}"),
            _check(f"at least {p.beyond_pct:g}% of the candle sits above that high",
                   (row["pct_above"] or 0) >= p.beyond_pct, f"{row['pct_above']:.0f}% of its range"),
        ]
    return all(c["passed"] for c in checks), checks


def shape(row: pd.Series, side: str, p: Params = P) -> tuple[bool, list[dict]]:
    """Script two: the body parked at one end of a candle worth trading."""
    big = row["range"] >= row["atr"] * p.atr_filter
    common = [
        _check(f"range ≥ ATR × {p.atr_filter:g}", big, f"{row['range']:,.2f} vs ATR {row['atr']:,.2f}"),
    ]
    if side == BULLISH:
        checks = [
            _check(f"body in the top {100 * (1 - p.fib_level):.0f}% of the range",
                   row["body_low"] >= row["bull_fib"], f"body low {row['body_low']:,.2f} vs {row['bull_fib']:,.2f}"),
            *common,
            _check(f"lowest low of the last {p.swing_lookback} bars", row["swing_low"], "swing filter"),
        ]
        if p.colour_filter:
            checks.append(_check("closes up", row["close"] > row["open"],
                                 f"{row['close']:,.2f} vs {row['open']:,.2f}"))
        if p.use_ema_filter:
            checks.append(_check(f"above the {p.ema_length}-bar EMA", row["close"] > row["ema"],
                                 f"{row['close']:,.2f} vs {row['ema']:,.2f}"))
    else:
        checks = [
            _check(f"body in the bottom {100 * (1 - p.fib_level):.0f}% of the range",
                   row["body_high"] <= row["bear_fib"], f"body high {row['body_high']:,.2f} vs {row['bear_fib']:,.2f}"),
            *common,
            _check(f"highest high of the last {p.swing_lookback} bars", row["swing_high"], "swing filter"),
        ]
        if p.colour_filter:
            checks.append(_check("closes down", row["close"] < row["open"],
                                 f"{row['close']:,.2f} vs {row['open']:,.2f}"))
        if p.use_ema_filter:
            checks.append(_check(f"below the {p.ema_length}-bar EMA", row["close"] < row["ema"],
                                 f"{row['close']:,.2f} vs {row['ema']:,.2f}"))
    return all(c["passed"] for c in checks), checks


def signal_on(row: pd.Series, p: Params = P) -> dict | None:
    """One prepared bar → a signal, with both indicators' verdicts, or nothing."""
    if not np.isfinite(row.get("range", np.nan)) or not np.isfinite(row.get("atr", np.nan)):
        return None
    for side in (BULLISH, BEARISH):
        if p.sides != "both" and p.sides != side.lower():
            continue
        rej_ok, rej = rejection(row, side, p)
        shp_ok, shp = shape(row, side, p)
        fired = {"both": rej_ok and shp_ok, "either": rej_ok or shp_ok,
                 "rejection": rej_ok, "shape": shp_ok}[p.mode]
        if fired:
            name = ("Hammer" if side == BULLISH else "Inverted hammer / shooting star")
            return {"side": side, "pattern": name,
                    "rejection_ok": rej_ok, "shape_ok": shp_ok,
                    "checks": {"rejection": rej, "shape": shp},
                    "close": round(float(row["close"]), 2), "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2), "open": round(float(row["open"]), 2)}
    return None


def describe(p: Params = P) -> dict:
    """What the page shows in the information panel."""
    return {
        "mode": p.mode, "mode_text": MODES.get(p.mode, ""),
        "rejection": [
            f"A tail at least {p.tail_pct:g}% of the candle's range, on {p.consecutive} bar(s) in a row, "
            "and the extreme must be below (or above) the previous bar's.",
            f"The bar must break the lowest low — or highest high — of the last {p.lookback} bars.",
            f"At least {p.beyond_pct:g}% of the candle's range must lie beyond that old extreme, so the "
            "rejection is a real excursion rather than a graze.",
        ],
        "shape": [
            f"The body must sit in the far {100 * (1 - p.fib_level):.0f}% of the range "
            f"(fib level {p.fib_level:g}) — a long tail with the close pushed to the other end.",
            f"The candle's range must be at least ATR({p.atr_length}) × {p.atr_filter:g}, which throws "
            "away the tiny bars that look like hammers on every chart.",
            f"The bar, or the one before it, must be the extreme of the last {p.swing_lookback} bars.",
            ("The candle must close in the trade's direction." if p.colour_filter
             else "Candle colour is ignored (colour filter off)."),
            (f"Only above the {p.ema_length}-bar EMA for longs, below it for shorts." if p.use_ema_filter
             else f"The {p.ema_length}-bar EMA filter is off."),
        ],
        "sides": p.sides,
    }
