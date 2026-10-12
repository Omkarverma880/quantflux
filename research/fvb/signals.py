"""
NIFTY_FUTURES_VOLUME_BREAKOUT — the signal engine.

A level breaking is an event. This module decides whether that event is worth a trade, and the
honest answer is usually no. Every path through ``evaluate`` either returns a signal carrying
the measurements that justified it, or a NO_TRADE naming the single condition that stopped it —
there is no silent third outcome.

Bullish and bearish are evaluated independently and never inferred from each other. A failed
upside breakout says the buyers could not hold a level; it does not say the sellers can take
one, and a system that flips on that assumption is trading its own disappointment.

Two entry modes:

    confirmation    the breakout candle itself, once it has closed beyond the level and passed
                    the firewall
    retest          the breakout must hold, price must come back to the level, and a later
                    candle must leave it again in the original direction. Fewer trades, and the
                    backtest reports whether the patience actually paid.

Timing discipline, enforced rather than documented: a decision is taken on the LAST minute of a
completed candle, and the fill is the NEXT minute's real printed option open. A five-minute
candle stamped 10:15 is decided at 10:19 and filled at 10:20.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from research.fvb import structure as ST
from research.fvb.data import FUTURES, NONE, OPTIONS
from research.fvb.firewall import (FAILED_BREAKOUT_CONFIRMED, VALID_BREAKOUT, Firewall,
                                   FirewallParams)

BULL, BEAR = "BULL", "BEAR"
NO_TRADE = "NO_TRADE"

# every reason the engine can decline, named so the backtest can count them
R_NO_LEVEL = "no validated level is in play yet"
R_NOT_BEYOND = "no completed candle has closed beyond a level plus its buffer"
R_DATA = "the session's data is not fit to trade on"
R_STALE = "spot and futures are misaligned or stale"
R_VOLUME = "the participation filter could not be evaluated"
R_WEAK_VOLUME = "participation was measured and did not meet the threshold"
R_CHOPPY = "the session is in a choppy regime"
R_FIREWALL = "the false-breakout firewall blocked this attempt"
R_NO_RETEST = "the configured retest did not happen"
R_SESSION = "outside the entry window"
R_LIMIT = "a daily trade or risk limit is in force"
R_COOLDOWN = "this level is in cooldown after a failure"
R_DISAGREE = "spot and futures disagree on direction"


@dataclass(frozen=True)
class EntryParams:
    mode: str = "confirmation"           # confirmation | retest
    first_entry: int = 9 * 60 + 35       # nothing before the opening range has settled
    last_entry: int = 14 * 60 + 30
    squareoff: int = 15 * 60 + 15
    max_trades_per_day: int = 2
    one_at_a_time: bool = True
    allow_bull: bool = True
    allow_bear: bool = True
    # retest mode
    retest_within: int = 15              # bars the pullback must arrive in
    retest_tolerance_pts: float = 10.0   # how close to the level counts as a retest
    retest_confirm_bars: int = 1         # candles leaving the level again
    # the minimum the move must promise to be worth taking
    min_rr: float = 1.0
    require_futures_agreement: bool = True
    # "with" takes the breakout's own direction. "fade" takes the other side, because across
    # every variant tested these breakouts resolved against themselves about two times in
    # three — a 34% win rate is not noise, it is a signal pointing the other way.
    side: str = "with"                   # with | fade

    def as_dict(self) -> dict:
        return asdict(self)


EP = EntryParams()


@dataclass
class Signal:
    session: date
    direction: str
    decision_minute: int
    fill_minute: int
    level: float
    level_name: str
    buffer: float
    spot_close: float
    stop_level: float            # the underlying's invalidation, in index points
    target_level: Optional[float]
    rr: Optional[float]
    volume: dict
    futures: dict
    shape: dict
    character: str
    firewall_state: str
    event_id: str
    mode: str
    retest: Optional[dict] = None
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["session"] = str(self.session)
        return d


def futures_agreement(row: pd.Series, prev: Optional[pd.Series], bullish: bool) -> dict:
    """Did futures move with the index on this bar, and is the basis behaving?

    Compared on MOVES, never on levels: futures carry a basis to spot that would otherwise make
    every comparison wrong by that offset.
    """
    if prev is None or "fut_close" not in row.index or not np.isfinite(row.get("fut_close", np.nan)):
        return {"checked": False, "available": False,
                "reason": "no futures bar aligned to this minute"}
    f_now, f_prev = float(row["fut_close"]), float(prev["fut_close"])
    s_now, s_prev = float(row["close"]), float(prev["close"])
    if not np.isfinite(f_prev):
        return {"checked": False, "available": False, "reason": "no previous futures bar"}
    f_move, s_move = f_now - f_prev, s_now - s_prev
    agrees = (f_move > 0) == (s_move > 0) if (f_move != 0 and s_move != 0) else True
    if bullish:
        agrees = agrees and f_move >= 0
    else:
        agrees = agrees and f_move <= 0
    basis_now = f_now - s_now
    basis_prev = f_prev - s_prev
    return {"checked": True, "available": True, "agrees": bool(agrees),
            "futures_move": round(f_move, 2), "spot_move": round(s_move, 2),
            "basis": round(basis_now, 2), "basis_change": round(basis_now - basis_prev, 2)}


def _stop_and_target(direction: str, level: float, buffer: float, close: float,
                     atr_now: Optional[float], p: ST.Params, ep: EntryParams,
                     fading: bool = False) -> tuple[float, Optional[float], Optional[float]]:
    """The underlying's invalidation and the reward that justifies risking it.

    Going WITH a break, the stop is structural: the level itself plus a volatility cushion, so
    ordinary noise around it does not count as invalidation. Price is already beyond the level,
    so that distance is positive by construction.

    FADING a break needs the opposite geometry and this is easy to get wrong. The entry is
    beyond the level, so putting the stop at the level would place it on the profitable side of
    the trade and produce a negative risk — which is what silently produced zero trades the
    first time. The thing that invalidates a fade is the breakout CONTINUING, so the stop sits
    a cushion beyond the entry, and the target is back toward the level that broke.
    """
    cushion = (atr_now * p.buffer_atr_mult) if (atr_now and np.isfinite(atr_now)) else buffer
    cushion = max(float(cushion), 1.0)
    if fading:
        if direction == BULL:                 # fading a breakdown: long, stop below the entry
            stop = close - cushion
            risk = cushion
            target = close + risk * ep.min_rr
        else:                                 # fading a breakout: short, stop above the entry
            stop = close + cushion
            risk = cushion
            target = close - risk * ep.min_rr
    elif direction == BULL:
        stop = level - cushion
        risk = close - stop
        target = close + risk * ep.min_rr if risk > 0 else None
    else:
        stop = level + cushion
        risk = stop - close
        target = close - risk * ep.min_rr if risk > 0 else None
    rr = ep.min_rr if (target is not None and risk > 0) else None
    return float(stop), (float(target) if target is not None else None), rr


def evaluate(day: pd.DataFrame, spot_all: pd.DataFrame, d: date, decision_minute: int,
             p: ST.Params, ep: EntryParams, fw: Firewall,
             taken_today: int = 0, position_open: bool = False,
             atr_series: Optional[pd.Series] = None) -> tuple[Optional[Signal], dict]:
    """Judge one decision minute. Returns (signal or None, a record of why)."""
    def no(reason: str, **extra) -> tuple[None, dict]:
        return None, {"verdict": NO_TRADE, "reason": reason, "minute": decision_minute,
                      "time": f"{decision_minute // 60:02d}:{decision_minute % 60:02d}", **extra}

    if decision_minute < ep.first_entry or decision_minute > ep.last_entry:
        return no(R_SESSION)
    if taken_today >= ep.max_trades_per_day:
        return no(R_LIMIT, taken=taken_today)
    if position_open and ep.one_at_a_time:
        return no(R_LIMIT, detail="a position is already open")

    upto = day[day["minute"] <= decision_minute]
    if len(upto) < 5:
        return no(R_DATA, bars=len(upto))
    row = upto.iloc[-1]
    prev = upto.iloc[-2] if len(upto) > 1 else None
    atr_now = None
    if atr_series is not None and len(atr_series):
        try:
            atr_now = float(atr_series.loc[upto.index[-1]])
        except (KeyError, IndexError):
            atr_now = None

    levels = ST.levels_for(day, spot_all, d, decision_minute, p)
    if not levels["resistance"] and not levels["support"]:
        return no(R_NO_LEVEL)

    chop = ST.choppy(day, decision_minute, atr_now, p)
    if chop.get("checked") and chop.get("choppy"):
        return no(R_CHOPPY, **chop)

    vol = ST.relative_volume(day, decision_minute, p)
    close = float(row["close"])

    for direction, side in ((BULL, "resistance"), (BEAR, "support")):
        if direction == BULL and not ep.allow_bull:
            continue
        if direction == BEAR and not ep.allow_bear:
            continue
        for lv in levels[side]:
            if decision_minute < lv["known_from"]:
                continue
            level = float(lv["price"])
            buf = ST.buffer_for(p, atr_now, close)
            beyond = (close > level + buf) if direction == BULL else (close < level - buf)
            if not beyond:
                fw.observe_break(row, decision_minute, direction, level, lv["name"], buf,
                                 vol, {"checked": False}, chop)
                continue

            fut = futures_agreement(row, prev, direction == BULL)
            attempt = fw.observe_break(row, decision_minute, direction, level, lv["name"],
                                       buf, vol, fut, chop)
            if attempt is None:
                continue
            if attempt.state == "COOLDOWN":
                return no(R_COOLDOWN, level=level, until=fw.in_cooldown(direction, level,
                                                                        decision_minute))
            if attempt.blocked:
                return no(R_FIREWALL, event_id=attempt.event_id,
                          conditions=[w["what"] for w in attempt.warnings],
                          state=attempt.state)

            # participation is a hard gate only when a source was asked for
            if p.participation != NONE:
                if vol["state"] == ST.VOL_UNAVAILABLE:
                    return no(R_VOLUME, detail=vol.get("reason"), source=p.participation)
                if vol["state"] == ST.VOL_WEAK:
                    return no(R_WEAK_VOLUME, rvol=vol.get("rvol"),
                              threshold=vol.get("threshold"))
            if ep.require_futures_agreement and fut.get("checked") and not fut.get("agrees"):
                return no(R_DISAGREE, **fut)

            shape = ST.candle_shape(row, direction == BULL)
            character = ST.price_volume_character(row, vol, atr_now)
            retest = None
            fill_minute = decision_minute + 1

            if ep.mode == "retest":
                retest = _find_retest(day, decision_minute, direction, level, ep)
                if retest is None:
                    return no(R_NO_RETEST, level=level, within=ep.retest_within)
                fill_minute = retest["confirm_minute"] + 1
                close = retest["confirm_close"]

            trade_dir = direction
            if ep.side == "fade":
                # the level that broke is still the reference, but the position is the other
                # way round, so the stop sits beyond the breakout rather than behind it
                trade_dir = BEAR if direction == BULL else BULL
            stop, target, rr = _stop_and_target(trade_dir, level, buf, close, atr_now, p, ep,
                                                fading=(ep.side == "fade"))
            risk = abs(close - stop)
            if risk <= 0:
                return no("the stop is at or through the entry")
            if rr is None or rr < ep.min_rr:
                return no(f"reward-to-risk below the {ep.min_rr} minimum", rr=rr)

            return Signal(
                session=d, direction=trade_dir, decision_minute=decision_minute,
                fill_minute=fill_minute, level=level, level_name=lv["name"], buffer=buf,
                spot_close=close, stop_level=stop, target_level=target, rr=rr,
                volume=vol, futures=fut, shape=shape, character=character,
                firewall_state=attempt.state, event_id=attempt.event_id, mode=ep.mode,
                retest=retest), {"verdict": "SIGNAL", "minute": decision_minute}

    return no(R_NOT_BEYOND)


def _find_retest(day: pd.DataFrame, broke_at: int, direction: str,
                 level: float, ep: EntryParams) -> Optional[dict]:
    """Price must come back to the level and then leave it again, on completed candles.

    Looked for only in bars AFTER the break, and the confirmation candle must close beyond the
    level again — a touch that never resolves is not an entry.
    """
    window = day[(day["minute"] > broke_at) &
                 (day["minute"] <= broke_at + ep.retest_within)]
    if window.empty:
        return None
    touched_at = None
    for row in window.itertuples():
        near = (abs(float(row.low) - level) <= ep.retest_tolerance_pts) if direction == BULL \
            else (abs(float(row.high) - level) <= ep.retest_tolerance_pts)
        if touched_at is None and near:
            touched_at = int(row.minute)
            continue
        if touched_at is not None:
            back = (float(row.close) > level) if direction == BULL else (float(row.close) < level)
            if back:
                return {"touched_minute": touched_at, "confirm_minute": int(row.minute),
                        "confirm_close": float(row.close),
                        "bars_to_retest": touched_at - broke_at}
            # closed through the level the wrong way: the retest failed
            through = (float(row.close) < level) if direction == BULL else (float(row.close) > level)
            if through:
                return None
    return None
