"""
Live detection for the price-action signals, and the option each one implies.

Reads the session from the broker rather than the stored history, because the store is a
snapshot and today is never in it. Everything it shows is computed on CLOSED candles — the
forming bar is withheld entirely, which is what ``barstate.isconfirmed`` means on the chart.

Two things it reports that the indicator cannot:

    the option          the CE or PE a signal implies, with its live premium, so the signal is
                        something you can act on rather than a label on a chart
    the pending level   where the next break would be, and how far away, so the levels are
                        useful before anything triggers

It takes no positions. A signal is a signal.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.pas import strategy as ST

logger = get_logger("research.pas.live")

SPOT_KEY = "NSE:NIFTY 50"
STRIKE_STEP = 50
LOT = 65
SESSION_OPEN = 9 * 60 + 15


def _spot_token(broker) -> Optional[int]:
    try:
        exch, sym = SPOT_KEY.split(":", 1)
        for inst in broker.get_instruments(exch) or []:
            if inst.get("tradingsymbol") == sym:
                return int(inst["instrument_token"])
    except Exception as exc:
        logger.debug("spot token lookup failed: %s", exc)
    return None


def _candles(broker, token: int, start: date, end: date) -> pd.DataFrame:
    rows = broker.get_historical_data(
        token, datetime.combine(start, time(9, 0)),
        datetime.combine(end, time(15, 40)), "minute") or []
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    ts = d["date"] if "date" in d else d["timestamp"]
    d["timestamp"] = pd.to_datetime(ts).dt.tz_localize(None)
    d = d.sort_values("timestamp").drop_duplicates("timestamp")
    d["minute"] = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d["date"] = d["timestamp"].dt.date
    keep = ["timestamp", "open", "high", "low", "close", "minute", "date"]
    if "volume" in d.columns:
        keep.append("volume")
    return d[keep].reset_index(drop=True)


def resample(d: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """One-minute bars into the chart's candle, stamped with the minute each one STARTS."""
    if minutes <= 1 or d.empty:
        return d.copy()
    out = []
    for day, g in d.groupby("date", sort=True):
        g = g.sort_values("timestamp")
        bucket = SESSION_OPEN + ((g["minute"] - SESSION_OPEN) // minutes) * minutes
        agg = {"timestamp": ("timestamp", "first"), "open": ("open", "first"),
               "high": ("high", "max"), "low": ("low", "min"), "close": ("close", "last")}
        if "volume" in g.columns:
            agg["volume"] = ("volume", "sum")
        a = g.assign(bucket=bucket).groupby("bucket").agg(**agg).reset_index()
        a["minute"] = a["bucket"].astype(int)
        a["date"] = day
        out.append(a.drop(columns=["bucket"]))
    return pd.concat(out, ignore_index=True) if out else d.iloc[0:0].copy()


def option_for(broker, side: str, spot: float, session: date,
               offset: int = 0) -> Optional[dict]:
    """The contract a signal implies, with its live premium."""
    typ = ST.CE if side == ST.BULL else ST.PE
    atm = round(spot / STRIKE_STEP) * STRIKE_STEP
    away = 1 if typ == ST.CE else -1
    strike = float(atm + away * offset * STRIKE_STEP)
    try:
        from research.index_straddle.options import weekly_expiry
        from research.pmvwap_straddle.universe import Universe
        uni = Universe(broker)
        exp = weekly_expiry(session)
        rec = uni.resolve("NIFTY", exp, strike, typ)
        if not rec or not rec.get("tradingsymbol"):
            return {"option_type": typ, "strike": strike, "expiry": str(exp),
                    "tradingsymbol": None, "premium": None,
                    "note": "that strike is not listed for the nearest expiry"}
        sym = rec["tradingsymbol"]
        q = broker.get_ltp([f"NFO:{sym}"]) or {}
        px = float(list(q.values())[0]) if q else 0.0
        return {"option_type": typ, "strike": strike, "expiry": str(exp),
                "tradingsymbol": sym, "lot_size": LOT,
                "premium": round(px, 2) if px > 0 else None,
                "cost_per_lot": round(px * LOT, 0) if px > 0 else None,
                "note": None if px > 0 else "no quote on that contract right now"}
    except Exception as exc:
        logger.debug("option lookup failed: %s", exc)
        return {"option_type": typ, "strike": strike, "tradingsymbol": None,
                "premium": None, "note": f"contract lookup failed: {str(exc)[:90]}"}


def signals_now(broker, p: Optional[ST.Params] = None, timeframe: int = 5,
                session: Optional[date] = None, strike_offset: int = 0,
                history_days: int = 6) -> dict:
    """Today's signals, the live levels, and the option behind each."""
    p = p or ST.P
    if broker is None:
        return {"available": False,
                "message": "Connect Zerodha — the live session comes from the broker, not from "
                           "the stored history."}
    token = _spot_token(broker)
    if not token:
        return {"available": False, "message": "NIFTY 50 is not in the instrument list."}

    today = session or datetime.now().date()
    raw = _candles(broker, token, today - timedelta(days=history_days), today)
    if raw.empty:
        return {"available": False, "message": "The broker returned no candles."}
    live_day = sorted(raw["date"].unique())[-1]

    bars = resample(raw, timeframe)
    # the forming candle is withheld: a bar only counts once its last minute has passed
    now_min = int(raw[raw["date"] == live_day]["minute"].max())
    bars = bars[(bars["date"] < live_day) | (bars["minute"] + timeframe - 1 <= now_min)]
    bars = bars.reset_index(drop=True)
    if len(bars) < p.swing_len + p.ema_len + 2:
        return {"available": False,
                "message": f"only {len(bars)} closed candles so far — the {p.ema_len} EMA and "
                           f"{p.swing_len}-bar swing need more before a signal means anything"}

    d = ST.prepare(bars, p)
    day = d[d["date"] == live_day].reset_index(drop=True)
    if day.empty:
        return {"available": False, "message": "no closed candle for this session yet"}

    last = day.iloc[-1]
    spot = float(last["close"])

    fired = []
    for i in range(len(day)):
        sig = ST.signal_at(day, i, p)
        if not sig:
            continue
        m = int(day.iloc[i]["minute"]) + timeframe - 1
        fired.append({**sig, "minute": m, "time": f"{m // 60:02d}:{m % 60:02d}"})

    latest = fired[-1] if fired else None
    if latest:
        latest = {**latest, "option": option_for(broker, latest["side"], latest["close"],
                                                 live_day, strike_offset)}

    res, sup = last.get("resistance"), last.get("support")
    pending = {
        "resistance": (None if not np.isfinite(res) else {
            "price": round(float(res), 2),
            "distance": round(float(res) - spot, 2),
            "already_beyond": bool(spot > float(res)),
            "would_buy": ST.CE}),
        "support": (None if not np.isfinite(sup) else {
            "price": round(float(sup), 2),
            "distance": round(spot - float(sup), 2),
            "already_beyond": bool(spot < float(sup)),
            "would_buy": ST.PE}),
    }

    def f(v):
        return round(float(v), 2) if (v is not None and np.isfinite(v)) else None

    waiting = None
    if bool(last.get("waiting_bull")):
        waiting = {"side": ST.BULL, "level": f(last.get("bull_level")),
                   "needs": "a pullback into the level that closes back above it, green"}
    elif bool(last.get("waiting_bear")):
        waiting = {"side": ST.BEAR, "level": f(last.get("bear_level")),
                   "needs": "a pullback into the level that closes back below it, red"}

    blockers = []
    if p.use_vwap and f(last.get("vwap")) is None:
        blockers.append("VWAP is not available on these bars")
    trend = ("above" if spot > float(last["ema"]) else "below") if np.isfinite(last["ema"]) else "—"

    return {
        "available": True,
        "session": str(live_day), "is_today": live_day == datetime.now().date(),
        "as_of": f"{now_min // 60:02d}:{now_min % 60:02d}",
        "timeframe": timeframe,
        "candles": int(len(day)), "minutes": int(len(raw[raw['date'] == live_day])),
        "spot": round(spot, 2),
        "ema": f(last.get("ema")), "ema_len": p.ema_len,
        "vwap": f(last.get("vwap")),
        "price_vs_ema": trend,
        "price_vs_vwap": (None if f(last.get("vwap")) is None
                          else ("above" if spot > float(last["vwap"]) else "below")),
        "resistance": f(res), "support": f(sup),
        "pending": pending, "waiting_for_retest": waiting,
        "signals": fired, "signal_count": len(fired), "latest": latest,
        "mode": "retest" if p.use_retest else "breakout",
        "rules": ST.describe(p),
        "blockers": blockers,
        "source": "Zerodha candles for the live session",
        "note": ("Signals are taken on closed candles only — the bar still forming is not "
                 "shown. A signal is not a forecast and the option price beside it is the "
                 "live quote, not a fill."),
    }
