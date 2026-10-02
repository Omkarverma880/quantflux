"""
5 & 60 Minute Range — backtest on stored index bars and real option premiums.

No-look-ahead rules, the same ones the Options Lab enforces:

  RANGE COMPLETE   a range is only usable once every one of its minutes has printed.
  CROSS ON CLOSE   the decision is taken on the close of minute t …
  NEXT-BAR FILL    … and filled at the OPEN of minute t+1, in the real contract.
  STRIKE FIXED     the contract is chosen from spot at the decision bar and held by name. It
                   never switches because "ATM moved".
  ADVERSE FIRST    inside one bar the stop is tested before the target.
  REAL COSTS       Zerodha statutory charges plus slippage, through the shared CostModel.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS
from research.options_lab.execution import CostModel
from research.range_lab import strategy as ST

logger = get_logger("research.range_lab.backtest")
COST = CostModel()


def _sessions(start: str, end: str, index: str) -> list:
    spot = MS.read("spot", index, start=start, end=end, columns=["timestamp"])
    if spot is None or not len(spot):
        return []
    return sorted(pd.to_datetime(spot["timestamp"]).dt.date.unique())


def _day_frames(index: str, start: str, end: str):
    """Index bars and option bars for a date span, keyed by session."""
    spot = MS.read("spot", index, start=start, end=end,
                   columns=["timestamp", "open", "high", "low", "close"])
    opt = MS.read("options", index, start=start, end=end,
                  columns=["timestamp", "expiry_date", "strike", "option_type",
                           "open", "high", "low", "close", "spot"])
    if spot is None or not len(spot):
        return {}, {}
    ts = pd.to_datetime(spot["timestamp"])
    spot = spot.assign(date=ts.dt.date, minute=ts.dt.hour * 60 + ts.dt.minute)
    by_spot = {d: g.sort_values("minute") for d, g in spot.groupby("date")}
    by_opt: dict = {}
    if opt is not None and len(opt):
        to = pd.to_datetime(opt["timestamp"])
        opt = opt.assign(date=to.dt.date, minute=to.dt.hour * 60 + to.dt.minute)
        opt["expiry_date"] = pd.to_datetime(opt["expiry_date"]).dt.date
        # nearest expiry only: that is the series a day trade actually uses
        opt = opt[opt["expiry_date"] == opt.groupby("date")["expiry_date"].transform("min")]
        by_opt = {d: g for d, g in opt.groupby("date")}
    return by_spot, by_opt


def _contract(day_opt: pd.DataFrame, side: str, strike: float) -> Optional[pd.DataFrame]:
    c = day_opt[(day_opt.option_type == side) & (day_opt.strike == float(strike))]
    return c.sort_values("minute") if len(c) else None


def run_session(day: date, bars: pd.DataFrame, day_opt: Optional[pd.DataFrame],
                p: ST.Params) -> dict:
    """One session. Returns its trades and why nothing was taken when nothing was."""
    out = {"date": str(day), "trades": [], "signals": 0, "skipped": []}
    if day_opt is None or not len(day_opt):
        out["skipped"].append("no option data for this session")
        return out

    rows = bars.to_dict("records")
    idx = {int(r["minute"]): r for r in rows}
    lv_all = ST.levels([{"minute": r["minute"], "high": r["high"], "low": r["low"]} for r in rows], p)
    out["levels"] = lv_all
    if not lv_all.get("A"):
        out["skipped"].append("the 5-minute range never completed")
        return out

    taken, used_levels = 0, set()
    minute = p.first_entry
    while minute <= p.last_entry:
        if taken >= p.max_trades_per_day:
            break
        bar, prev = idx.get(minute), idx.get(minute - 1)
        if bar is None or prev is None:
            minute += 1
            continue
        which = ST.range_in_force(minute, p)
        lv = lv_all.get(which)
        if not lv:                                  # range B is not complete before 10:15
            minute += 1
            continue
        sig = ST.signal(float(prev["close"]), float(bar["close"]), lv, p)
        if not sig:
            minute += 1
            continue
        out["signals"] += 1
        key = (which, sig["level"], sig["side"])
        if p.one_trade_per_level and key in used_levels:
            minute += 1
            continue

        fill_bar = idx.get(minute + 1)              # decide on the close, fill on the next open
        if fill_bar is None:
            minute += 1
            continue
        spot_at = float(bar["close"])
        strike = ST.strike_for(spot_at, sig["side"], p)
        con = _contract(day_opt, sig["side"], strike)
        if con is None:
            out["skipped"].append(f"{ST.hhmm(minute)} {sig['side']} {strike:g} not in the store")
            minute += 1
            continue
        fut = con[con.minute >= minute + 1]
        if not len(fut):
            minute += 1
            continue
        entry = float(fut.iloc[0]["open"]) + p.slippage_ticks * ST.TICK
        if entry < p.min_premium:
            out["skipped"].append(f"{ST.hhmm(minute)} {sig['side']} {strike:g} priced {entry:.2f}")
            minute += 1
            continue

        ex = ST.exits(entry, p)
        stop_at, target_at = ex["stop"], ex["target"]
        idx_stop = spot_at - p.stop_points if sig["side"] == "CE" else spot_at + p.stop_points
        idx_tgt = spot_at + p.target_points if sig["side"] == "CE" else spot_at - p.target_points

        end_m = p.squareoff if p.max_hold_min <= 0 else min(minute + p.max_hold_min, p.squareoff)
        exit_px, exit_m, reason = None, None, "SQUAREOFF"
        for _, r in fut.iterrows():
            m = int(r["minute"])
            if m > end_m:
                break
            if p.exit_on_index:
                ib = idx.get(m)
                if ib is None:
                    continue
                hit_stop = (float(ib["low"]) <= idx_stop) if sig["side"] == "CE" else (float(ib["high"]) >= idx_stop)
                hit_tgt = (float(ib["high"]) >= idx_tgt) if sig["side"] == "CE" else (float(ib["low"]) <= idx_tgt)
            else:
                hit_stop = float(r["low"]) <= stop_at
                hit_tgt = float(r["high"]) >= target_at
            if hit_stop:                            # adverse first, always
                exit_px, exit_m, reason = (stop_at if not p.exit_on_index else float(r["close"])), m, "STOP"
                break
            if hit_tgt:
                exit_px, exit_m, reason = (target_at if not p.exit_on_index else float(r["close"])), m, "TARGET"
                break
            exit_px, exit_m = float(r["close"]), m
        if exit_px is None:
            minute += 1
            continue

        qty = p.lots * p.lot
        gross = (exit_px - entry) * qty
        charges = COST.round_trip(entry, exit_px, qty)
        trade = {
            "date": str(day), "time": ST.hhmm(minute), "fill_time": ST.hhmm(minute + 1),
            "range": which, "level": sig["level"], "level_price": round(sig["level_price"], 2),
            "why": sig["why"], "side": sig["side"], "strike": strike, "moneyness": p.moneyness,
            "spot_at_signal": round(spot_at, 2), "entry": round(entry, 2),
            "exit": round(float(exit_px), 2), "exit_time": ST.hhmm(exit_m or minute),
            "exit_reason": reason, "lots": p.lots, "qty": qty,
            "gross": round(gross, 2), "charges": round(charges, 2),
            "pnl": round(gross - charges, 2),
            "held_min": int((exit_m or minute) - minute),
        }
        out["trades"].append(trade)
        used_levels.add(key)
        taken += 1
        minute = (exit_m or minute) + 1             # no overlapping positions
    return out


def run(start: str, end: str, p: ST.Params, progress: Optional[Callable[[str], None]] = None) -> dict:
    say = progress or (lambda _m: None)
    say("reading the store…")
    by_spot, by_opt = _day_frames(p.index, start, end)
    days = sorted(by_spot)
    if not days:
        raise ValueError(f"no {p.index} index bars stored between {start} and {end}")
    say(f"{len(days)} sessions")
    trades, sessions, notes = [], [], []
    for i, d in enumerate(days):
        if i % 25 == 0:
            say(f"{i}/{len(days)} sessions…")
        r = run_session(d, by_spot[d], by_opt.get(d), p)
        sessions.append({"date": r["date"], "signals": r["signals"], "trades": len(r["trades"])})
        trades.extend(r["trades"])
        notes.extend(r["skipped"][:2])
    say("summarising…")
    return {"start": start, "end": end, "params": p.as_dict(), "trades": trades,
            "sessions": sessions, "summary": summarise(trades, sessions, notes, p)}


def summarise(trades: list, sessions: list, notes: list, p: ST.Params) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "sessions": len(sessions),
                "note": "no trade was taken — widen the window or check the strike offset",
                "why_not": notes[:8]}
    df = pd.DataFrame(trades)
    wins = df[df.pnl > 0]
    by_day = df.groupby("date")["pnl"].sum()
    mon = by_day.groupby(pd.PeriodIndex(pd.to_datetime(pd.Series(by_day.index)), freq="M").values).sum()
    eq = by_day.cumsum()
    return {
        "trades": n, "sessions": len(sessions), "traded_days": int(by_day.size),
        "net": round(float(df.pnl.sum()), 2),
        "gross": round(float(df.gross.sum()), 2),
        "charges": round(float(df.charges.sum()), 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_win": round(float(wins.pnl.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(df[df.pnl <= 0].pnl.mean()), 2) if len(df[df.pnl <= 0]) else 0.0,
        "per_trade": round(float(df.pnl.mean()), 2),
        "per_day": round(float(by_day.mean()), 2),
        "best_day": round(float(by_day.max()), 2), "worst_day": round(float(by_day.min()), 2),
        "green_days": int((by_day > 0).sum()),
        "green_months": int((mon > 0).sum()), "months": int(mon.size),
        "median_month": round(float(mon.median()), 2),
        "worst_month": round(float(mon.min()), 2),
        "max_drawdown": round(float((eq.cummax() - eq).max()), 2),
        "by_reason": {k: int(v) for k, v in df.exit_reason.value_counts().items()},
        "by_side": {k: int(v) for k, v in df.side.value_counts().items()},
        "monthly": {str(k): round(float(v), 2) for k, v in mon.items()},
        "why_not": notes[:8],
    }
