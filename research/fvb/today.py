"""
Today's levels, read from the broker rather than from the store.

The store is a snapshot someone has to refresh. Today is not in it and never will be until the
next ingest, which is why a backtest dated today returns nothing — and why asking the lab
"where are the levels right now" through the backtest was always going to fail.

This takes the other route: pull the session's own candles from the broker, build exactly the
same levels, buffers and participation the backtest uses, and say what would have to happen for
a trade to trigger. Same engine, live input.

What it does NOT do is take a position or claim one. It answers three questions and stops:

    where are the levels          opening range, previous day, and the buffer around each
    what has happened so far      breaks attempted, the firewall's verdict on each
    what would trigger next       the exact price a close would have to clear, both ways

A level is not a signal. The trigger prices below are what the rules require, not a prediction
that they will be met, and nothing here is an instruction to trade.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.fvb import data as D
from research.fvb import signals as SG
from research.fvb import structure as ST
from research.fvb.firewall import Firewall, FirewallParams

logger = get_logger("research.fvb.today")

SPOT_KEY = "NSE:NIFTY 50"


def _spot_token(broker) -> Optional[int]:
    """The NIFTY 50 index token from the instrument dump."""
    try:
        exch, sym = SPOT_KEY.split(":", 1)
        for inst in broker.get_instruments(exch) or []:
            if inst.get("tradingsymbol") == sym:
                return int(inst["instrument_token"])
    except Exception as exc:
        logger.debug("spot token lookup failed: %s", exc)
    return None


def _candles(broker, token: int, start: date, end: date) -> pd.DataFrame:
    """One-minute candles from the broker, shaped the way the engine expects."""
    rows = broker.get_historical_data(
        token,
        datetime.combine(start, time(9, 0)),
        datetime.combine(end, time(15, 40)),
        "minute") or []
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d["timestamp"] = pd.to_datetime(d["date"] if "date" in d else d["timestamp"]).dt.tz_localize(None)
    d = d.sort_values("timestamp").drop_duplicates("timestamp")
    d["minute"] = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d["date"] = d["timestamp"].dt.date
    return d[["timestamp", "open", "high", "low", "close", "minute", "date"]].reset_index(drop=True)


def _option_volume(broker, spot_now: float, session: date) -> tuple[Optional[pd.Series], str]:
    """Traded volume on the near-the-money contracts, as its own participation source.

    Real exchange volume on real contracts. It is not futures participation and is reported
    under its own name, exactly as the backtest does.
    """
    try:
        from research.pmvwap_straddle.universe import Universe
        from research.index_straddle.options import weekly_expiry
        uni = Universe(broker)
        exp = weekly_expiry(session)
        atm = round(spot_now / D.STRIKE_STEP) * D.STRIKE_STEP
        frames = []
        for step in (-1, 0, 1):
            k = atm + step * D.STRIKE_STEP
            for typ in ("CE", "PE"):
                rec = uni.resolve("NIFTY", exp, float(k), typ)
                if not rec or not rec.get("token"):
                    continue
                rows = broker.get_historical_data(
                    int(rec["token"]),
                    datetime.combine(session, time(9, 0)),
                    datetime.combine(session, time(15, 40)), "minute") or []
                if not rows:
                    continue
                f = pd.DataFrame(rows)
                f["timestamp"] = pd.to_datetime(f["date"] if "date" in f else f["timestamp"]).dt.tz_localize(None)
                f["minute"] = f["timestamp"].dt.hour * 60 + f["timestamp"].dt.minute
                frames.append(f[["minute", "volume"]])
        if not frames:
            return None, "no near-the-money contract returned candles"
        allv = pd.concat(frames).groupby("minute")["volume"].sum()
        return allv, ""
    except Exception as exc:
        logger.debug("option volume unavailable: %s", exc)
        return None, f"option volume could not be read: {str(exc)[:100]}"


def levels_now(broker, p: Optional[ST.Params] = None, ep: Optional[SG.EntryParams] = None,
               fp: Optional[FirewallParams] = None, session: Optional[date] = None) -> dict:
    """Where the levels are right now, and what would have to happen to trigger a trade."""
    p = p or ST.P
    ep = ep or SG.EP
    fp = fp or FirewallParams()
    if broker is None:
        return {"available": False,
                "message": "Connect Zerodha — today's candles come from the broker, not from "
                           "the stored history."}
    token = _spot_token(broker)
    if not token:
        return {"available": False,
                "message": "NIFTY 50 is not in the instrument list for this session."}

    today = session or datetime.now().date()
    # a few calendar days back so the previous session and the ATR are both warm
    bars = _candles(broker, token, today - timedelta(days=9), today)
    if bars.empty:
        return {"available": False,
                "message": "The broker returned no candles for this window."}
    sessions = sorted(bars["date"].unique())
    live_day = sessions[-1]
    day = bars[bars["date"] == live_day].reset_index(drop=True)
    if day.empty:
        return {"available": False, "message": "No candles for the current session yet."}

    now_min = int(day["minute"].max())
    spot = float(day["close"].iloc[-1])

    vol_series, vol_why = (None, "")
    if p.participation == D.OPTIONS:
        vol_series, vol_why = _option_volume(broker, spot, live_day)
    if vol_series is not None:
        day = day.merge(vol_series.rename("opt_volume"), left_on="minute",
                        right_index=True, how="left")

    atr_s = ST.atr(day, p.atr_length)
    atr_now = float(atr_s.iloc[-1]) if len(atr_s) and np.isfinite(atr_s.iloc[-1]) else None
    lv = ST.levels_for(day, bars, live_day, now_min, p)
    buf = ST.buffer_for(p, atr_now, spot)
    rvol = ST.relative_volume(day, now_min, p)
    if rvol.get("state") == ST.VOL_UNAVAILABLE and vol_why and not rvol.get("reason"):
        rvol["reason"] = vol_why
    chop = ST.choppy(day, now_min, atr_now, p)

    # replay the session through the firewall so today's attempts are classified the same way
    fw = Firewall(live_day, p, fp)
    for row in day.itertuples():
        m = int(row.minute)
        if m < ep.first_entry:
            continue
        marks = ST.levels_for(day, bars, live_day, m, p)
        for direction, side in ((SG.BULL, "resistance"), (SG.BEAR, "support")):
            for one in marks[side]:
                if m < one["known_from"]:
                    continue
                fw.observe_break(day.loc[day["minute"] == m].iloc[0], m, direction,
                                 float(one["price"]), one["name"], buf, rvol,
                                 {"checked": False}, chop)
    for att in list(fw.attempts):
        fw.follow(att, day, now_min)

    def trigger(price: float, bullish: bool) -> dict:
        need = price + buf if bullish else price - buf
        return {"close_beyond": round(need, 2),
                "distance": round((need - spot) if bullish else (spot - need), 2),
                "already_beyond": bool(spot > need) if bullish else bool(spot < need)}

    out_r = [{**one, "trigger": trigger(float(one["price"]), True)} for one in lv["resistance"]]
    out_s = [{**one, "trigger": trigger(float(one["price"]), False)} for one in lv["support"]]

    blockers = []
    if now_min < ep.first_entry:
        blockers.append(f"entries do not open until "
                        f"{ep.first_entry // 60:02d}:{ep.first_entry % 60:02d}")
    if now_min > ep.last_entry:
        blockers.append(f"the entry window closed at "
                        f"{ep.last_entry // 60:02d}:{ep.last_entry % 60:02d}")
    if p.participation != D.NONE and rvol.get("state") == ST.VOL_UNAVAILABLE:
        blockers.append(f"participation cannot be measured: {rvol.get('reason')}")
    elif p.participation != D.NONE and rvol.get("state") == ST.VOL_WEAK:
        blockers.append(f"relative volume {rvol.get('rvol')} is below the "
                        f"{rvol.get('threshold')} threshold")
    if chop.get("checked") and chop.get("choppy"):
        blockers.append("the session is in a choppy regime by the configured measure")

    return {
        "available": True,
        "session": str(live_day),
        "is_today": live_day == datetime.now().date(),
        "as_of": f"{now_min // 60:02d}:{now_min % 60:02d}",
        "bars": int(len(day)),
        "spot": round(spot, 2),
        "atr": round(atr_now, 2) if atr_now else None,
        "buffer": round(buf, 2),
        "opening_range": lv.get("opening_range"),
        "previous_day": lv.get("previous_day"),
        "resistance": out_r, "support": out_s,
        "relative_volume": rvol,
        "choppy": chop,
        "firewall": fw.summary(),
        "attempts": [a.as_dict() for a in fw.attempts][-20:],
        "blockers": blockers,
        "tradeable_now": not blockers,
        "source": "Zerodha historical candles for the live session",
        "note": ("These are the levels the rules watch and the price a close would have to "
                 "clear. A level is not a signal, and a trigger being met is not a "
                 "prediction that the move continues."),
    }
