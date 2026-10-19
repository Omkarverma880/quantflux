"""
Quantflux Price Action Signals — the Pine script, translated line for line.

The original is short and precise, so this follows it exactly rather than improving it. Where a
Pine construct has no direct Python equivalent the comment says what the original did, because
a translation that quietly "fixes" the source is no longer a translation and its results cannot
be compared with what you see on the chart.

    resistance = ta.highest(high[1], swingLen)      the highest high of the PREVIOUS swingLen
    support    = ta.lowest(low[1],  swingLen)       bars, never including the current one

    bullBreak  = close > resistance and close[1] <= resistance[1]

The second half of that line is the part people drop. It makes the break a FRESH cross: the bar
before must have been at or below its own resistance. Without it every bar of a move above the
level repeats the signal, which is what turns one breakout into forty.

    bullRetest = waitBull and low <= bullLevel and close > bullLevel and close > open

A retest needs three things at once: the bar traded back down into the level, it closed back
above it, and it closed green. ``waitBull`` latches on a break and clears on the retest, so one
break offers one retest and no more.

    callSignal = barstate.isconfirmed and bullSetup and bullTrend

``barstate.isconfirmed`` is the whole discipline: the bar must be CLOSED. Everything here is
computed on completed candles, and the live engine never shows a signal from a bar still
forming.

One honest note on VWAP. Pine's ``ta.vwap`` weights by the chart's volume. NIFTY is an index,
and the volume it carries is not a traded quantity in the way a stock's is — so the VWAP below
is weighted by exactly the column the chart uses, and is labelled as such rather than presented
as a traded average price.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

BULL, BEAR = "BULL", "BEAR"
CE, PE = "CE", "PE"


@dataclass(frozen=True)
class Params:
    ema_len: int = 20              # emaLen
    swing_len: int = 10            # swingLen — the breakout lookback
    use_vwap: bool = True          # useVWAP
    use_retest: bool = False       # useRetest
    # ── the parts the indicator leaves to the trader ──
    session_vwap: bool = True      # reset VWAP each session, as an intraday chart does
    allow_bull: bool = True
    allow_bear: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


P = Params()


def ema(series: pd.Series, length: int) -> pd.Series:
    """ta.ema — Pine seeds with an SMA and then runs the usual recursion."""
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def vwap(d: pd.DataFrame, session_reset: bool = True) -> pd.Series:
    """ta.vwap(hlc3), weighted by whatever volume the chart carries.

    Reset per session when ``session_reset`` is on, which is what an intraday TradingView chart
    does. With no usable volume the weights fall back to 1, which makes this the running mean
    of hlc3 — stated here because silently returning a flat line would look like a working VWAP.
    """
    hlc3 = (d["high"] + d["low"] + d["close"]) / 3.0
    vol = d["volume"] if "volume" in d.columns else pd.Series(1.0, index=d.index)
    vol = pd.to_numeric(vol, errors="coerce").fillna(0.0)
    if vol.sum() <= 0:
        vol = pd.Series(1.0, index=d.index)
    if session_reset and "date" in d.columns:
        g = d["date"]
        pv = (hlc3 * vol).groupby(g).cumsum()
        vv = vol.groupby(g).cumsum()
    else:
        pv = (hlc3 * vol).cumsum()
        vv = vol.cumsum()
    return pv / vv.replace(0, np.nan)


def prepare(bars: pd.DataFrame, p: Params = P) -> pd.DataFrame:
    """Every series the indicator plots, on completed bars only.

    Each row sees itself and the rows before it. ``shift(1)`` on the swing windows is the
    ``high[1]`` / ``low[1]`` of the original: the level a bar breaks is built from the bars
    BEFORE it, never from its own high.
    """
    d = bars.reset_index(drop=True).copy()
    d["ema"] = ema(d["close"], p.ema_len)
    d["vwap"] = vwap(d, p.session_vwap)

    # resistance = ta.highest(high[1], swingLen) ; support = ta.lowest(low[1], swingLen)
    d["resistance"] = d["high"].shift(1).rolling(p.swing_len, min_periods=p.swing_len).max()
    d["support"] = d["low"].shift(1).rolling(p.swing_len, min_periods=p.swing_len).min()

    prev_res = d["resistance"].shift(1)
    prev_sup = d["support"].shift(1)
    prev_close = d["close"].shift(1)

    # the fresh-cross condition, both halves
    d["bull_break"] = (d["close"] > d["resistance"]) & (prev_close <= prev_res)
    d["bear_break"] = (d["close"] < d["support"]) & (prev_close >= prev_sup)

    # bullTrend = close > ema20 and (not useVWAP or close > vwapValue)
    d["bull_trend"] = (d["close"] > d["ema"]) & ((not p.use_vwap) | (d["close"] > d["vwap"]))
    d["bear_trend"] = (d["close"] < d["ema"]) & ((not p.use_vwap) | (d["close"] < d["vwap"]))

    _retest_state(d, p)

    bull_setup = d["bull_retest"] if p.use_retest else d["bull_break"]
    bear_setup = d["bear_retest"] if p.use_retest else d["bear_break"]
    d["bull_setup"], d["bear_setup"] = bull_setup, bear_setup

    # callSignal / putSignal. barstate.isconfirmed is honoured by only ever running this on
    # closed bars — the live engine withholds the forming candle entirely.
    d["call_signal"] = bull_setup & d["bull_trend"] & p.allow_bull
    d["put_signal"] = bear_setup & d["bear_trend"] & p.allow_bear
    return d


def _retest_state(d: pd.DataFrame, p: Params) -> None:
    """The var/latch block, which has to be walked bar by bar because it carries state.

    Pine's ``var`` persists across bars and the assignments happen in source order, so a break
    on this bar sets the latch for the NEXT bar's retest check — reproduced here exactly, with
    the state read before it is written.
    """
    n = len(d)
    bull_level = np.full(n, np.nan)
    bear_level = np.full(n, np.nan)
    wait_bull = np.zeros(n, dtype=bool)
    wait_bear = np.zeros(n, dtype=bool)
    bull_retest = np.zeros(n, dtype=bool)
    bear_retest = np.zeros(n, dtype=bool)

    bl = br = np.nan
    wb = wr = False
    bb = d["bull_break"].to_numpy()
    bd = d["bear_break"].to_numpy()
    res = d["resistance"].to_numpy()
    sup = d["support"].to_numpy()
    lo, hi = d["low"].to_numpy(), d["high"].to_numpy()
    op, cl = d["open"].to_numpy(), d["close"].to_numpy()

    for i in range(n):
        if bb[i]:
            bl, wb, wr = res[i], True, False
        if bd[i]:
            br, wr, wb = sup[i], True, False
        # The retest test runs AFTER the latches are set, exactly as in the source. One
        # consequence is worth knowing rather than quietly removing: a breakout bar whose own
        # low reaches back to the level satisfies the retest on the same bar, so the break and
        # its retest can be the same candle. That is what the Pine script does, so it is what
        # this does.
        rb = bool(wb and np.isfinite(bl) and lo[i] <= bl and cl[i] > bl and cl[i] > op[i])
        rr = bool(wr and np.isfinite(br) and hi[i] >= br and cl[i] < br and cl[i] < op[i])
        if rb:
            wb = False
        if rr:
            wr = False
        bull_level[i], bear_level[i] = bl, br
        wait_bull[i], wait_bear[i] = wb, wr
        bull_retest[i], bear_retest[i] = rb, rr

    d["bull_level"], d["bear_level"] = bull_level, bear_level
    d["waiting_bull"], d["waiting_bear"] = wait_bull, wait_bear
    d["bull_retest"], d["bear_retest"] = bull_retest, bear_retest


def signal_at(d: pd.DataFrame, i: int, p: Params = P) -> Optional[dict]:
    """The signal on one completed bar, with every condition that produced it."""
    if i < 0 or i >= len(d):
        return None
    r = d.iloc[i]
    if bool(r["call_signal"]):
        side, typ = BULL, CE
    elif bool(r["put_signal"]):
        side, typ = BEAR, PE
    else:
        return None
    bull = side is BULL
    level = float(r["resistance"] if bull else r["support"])
    # Only the conditions that actually gated THIS signal are listed. In breakout mode the
    # retest flag is left out on purpose: because of the same-bar quirk above it is often
    # true on the breakout candle itself, and showing it there reads as though a separate
    # pullback had happened when none did.
    checks = {"fresh_cross": bool(r["bull_break"] if bull else r["bear_break"])}
    if p.use_retest:
        checks["retest"] = bool(r["bull_retest"] if bull else r["bear_retest"])
    checks["above_ema" if bull else "below_ema"] = (
        bool(r["close"] > r["ema"]) if bull else bool(r["close"] < r["ema"]))
    if p.use_vwap:
        checks["above_vwap" if bull else "below_vwap"] = (
            bool(r["close"] > r["vwap"]) if bull else bool(r["close"] < r["vwap"]))
    return {
        "side": side, "option_type": typ,
        "close": float(r["close"]), "open": float(r["open"]),
        "high": float(r["high"]), "low": float(r["low"]),
        "level": level,
        "level_name": "swing high" if bull else "swing low",
        "ema": float(r["ema"]) if np.isfinite(r["ema"]) else None,
        "vwap": float(r["vwap"]) if np.isfinite(r["vwap"]) else None,
        "mode": "retest" if p.use_retest else "breakout",
        "checks": checks,
    }


def describe(p: Params = P) -> str:
    bits = [f"a close beyond the {p.swing_len}-bar swing high or low, as a fresh cross"]
    if p.use_retest:
        bits.append("then a pullback into the level that closes back through it, in colour")
    bits.append(f"with price on the right side of the {p.ema_len} EMA")
    if p.use_vwap:
        bits.append("and of VWAP")
    return "; ".join(bits) + ". Signals are taken on closed bars only."
