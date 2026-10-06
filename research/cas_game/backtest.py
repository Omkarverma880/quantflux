"""
CAS Game Play — what this bet would have done, on the stored chain.

One session at a time: freeze the chain at each minute of the entry window, let
``strategy.choose`` pick the same way the live desk would, buy at the next minute's real price,
then follow those contracts to the target or the square-off.

Two limits are stated on the page rather than hidden:
  * the stored chain covers roughly the strikes near the money, so a ₹1 option that sat far out
    may never have been recorded — the backtest can only trade what was stored;
  * data ingested before this was fixed stops at 15:29, so the 15:30–15:40 minutes that the
    closing auction actually moves are missing from older sessions. The run reports how far each
    session's data really went.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.cas_game import strategy as ST
from research.market_store import store as MS
from research.options_lab.execution import CostModel

logger = get_logger("research.cas_game.backtest")

COST = CostModel(slippage_pts=0.0)       # slippage is applied to the fill price, not twice


def _months(start: str, end: str) -> list[tuple[str, str]]:
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    out, cur = [], pd.Timestamp(a.year, a.month, 1)
    while cur <= b:
        nxt = cur + pd.offsets.MonthBegin(1)
        out.append((str(max(cur, a).date()), str(min(nxt - pd.Timedelta(days=1), b).date())))
        cur = nxt
    return out


def _sessions(start: str, end: str, p: ST.Params) -> dict:
    """Per session: the chain as a minute × contract table, plus the spot."""
    o = MS.read("options", p.index.upper(), start=start, end=end,
                columns=["timestamp", "expiry_date", "strike", "option_type",
                         "open", "high", "low", "close", "volume", "spot"])
    if o.empty:
        return {}
    ts = pd.to_datetime(o["timestamp"])
    o = o.assign(date=ts.dt.date, m=(ts.dt.hour * 60 + ts.dt.minute).astype(int),
                 expiry_date=pd.to_datetime(o["expiry_date"]).dt.date)
    o = o[o["expiry_date"] >= o["date"]]
    o = o[o["expiry_date"] == o.groupby("date")["expiry_date"].transform("min")]
    if p.expiry_only:
        o = o[[(e - d).days == 0 for e, d in zip(o["expiry_date"], o["date"])]]
    out = {}
    for d, g in o.groupby("date"):
        out[d] = {"rows": g, "expiry": g["expiry_date"].iloc[0], "last_minute": int(g["m"].max())}
    return out


def run_session(d: date, day: dict, p: ST.Params) -> dict:
    """One expiry session: the picks, the fills, and what became of them."""
    g = day["rows"]
    last_minute = day["last_minute"]
    trades, notes = [], []
    entered = False

    for minute in range(p.entry_from, min(p.entry_to, last_minute - 1) + 1):
        if entered:
            break
        if ST.too_late(minute, p):
            notes.append(f"{ST.hhmm(minute)} is inside the auction's lead time — not opening")
            break
        snap = g[g["m"] == minute]
        if snap.empty:
            continue
        spot = float(snap["spot"].iloc[0]) if pd.notna(snap["spot"].iloc[0]) else 0.0
        chain = [{"option_type": r.option_type, "strike": float(r.strike),
                  "price": float(r.close), "volume": float(r.volume or 0)} for r in snap.itertuples()]
        decision = ST.choose(chain, spot, p)
        sized = ST.size(decision["picks"], p)
        buys = [s for s in sized if s.get("lots")]
        if not buys:
            continue

        # fill on the next minute, the way a live order would
        fill_min = minute + 1
        lot = p.lot
        filled = []
        for b in buys:
            fut = g[(g["option_type"] == b["option_type"]) & (g["strike"] == b["strike"])
                    & (g["m"] >= fill_min)].sort_values("m")
            if fut.empty:
                notes.append(f"{b['option_type']} {b['strike']:g} had no price after {ST.hhmm(minute)}")
                continue
            fill = fut.iloc[0]
            entry = float(fill["open"] if np.isfinite(fill["open"]) else fill["close"]) + p.slippage_ticks * ST.TICK
            if entry > 0:
                filled.append((b, fut, entry))
        if not filled:
            continue

        # A pair is ONE trade and must carry the same number of lots on each leg. Splitting the
        # budget by rupees would buy many lots of the cheap leg and few of the dear one, so the
        # "defined loss" would be whatever the cheap side happened to cost — not a choice at all.
        if ST.is_pair(p) and len(filled) > 1:
            per_lot = sum(e for _, _, e in filled) * lot
            lots_each = int(p.budget // per_lot) if per_lot > 0 else 0
            lots_for = {id(b): lots_each for b, _, _ in filled}
        else:
            lots_for = {id(b): int((p.budget / len(filled)) // (e * lot)) for b, _, e in filled}

        for b, fut, entry in filled:
            lots = lots_for.get(id(b), 0)
            if lots < 1:
                notes.append(f"{b['option_type']} {b['strike']:g} cost more than the budget allows")
                continue
            qty = lots * lot
            # a zero target means "no cap". Writing entry + 0 here would put the target AT the
            # entry price and sell the position the minute after it was opened.
            pos = {"target": (entry + p.target_points) if p.target_points and p.target_points > 0 else None,
                   "stop": ST.leg_stop(entry, p)}
            exit_px, why, exit_min = None, "SQUAREOFF", min(p.squareoff, last_minute)
            for r in fut.itertuples():
                # adverse first: inside one minute the stop is tested against the LOW before the
                # target is tested against the high, so a bar that touched both is read as a loss
                if pos["stop"] and float(r.low) <= pos["stop"]:
                    exit_px, why, exit_min = pos["stop"], "STOP", int(r.m)
                    break
                if pos["target"] and float(r.high) >= pos["target"]:
                    exit_px, why, exit_min = pos["target"], "TARGET", int(r.m)
                    break
                if int(r.m) >= min(p.squareoff, last_minute):
                    exit_px, why, exit_min = float(r.close), "SQUAREOFF", int(r.m)
                    break
            if exit_px is None:
                tail = fut.iloc[-1]
                exit_px, exit_min = float(tail["close"]), int(tail["m"])
            exit_px = max(0.0, exit_px - p.slippage_ticks * ST.TICK)
            gross = (exit_px - entry) * qty
            charges = COST.round_trip(entry, exit_px, qty)
            peak = float(fut["high"].max())
            trades.append({
                "date": str(d), "month": str(d)[:7], "signal_time": ST.hhmm(minute),
                "entry_time": ST.hhmm(int(fill["m"])), "exit_time": ST.hhmm(exit_min),
                "option_type": b["option_type"], "strike": float(b["strike"]),
                "spot_at_entry": round(spot, 2), "strike_gap": round(abs(float(b["strike"]) - spot)),
                "entry": round(entry, 2), "exit": round(exit_px, 2), "exit_reason": why,
                "lots": lots, "qty": qty, "cost": round(entry * qty, 2),
                "peak": round(peak, 2), "peak_gain_pts": round(peak - entry, 2),
                "charges": round(charges, 2), "pnl": round(gross - charges, 2),
            })
        entered = bool(trades)

    return {"date": str(d), "trades": trades, "notes": notes,
            "data_ends": ST.hhmm(last_minute),
            "covers_auction": last_minute >= p.squareoff}


def run(start: str, end: str, p: ST.Params = ST.P,
        progress: Optional[Callable[[str], None]] = None) -> dict:
    say = progress or (lambda _m: None)
    trades, notes, days, short_data = [], [], 0, 0
    for a, b in _months(start, end):
        say(f"{a[:7]} · {len(trades)} tickets so far")
        for d, day in sorted(_sessions(a, b, p).items()):
            days += 1
            res = run_session(d, day, p)
            trades.extend(res["trades"])
            notes.extend(res["notes"][:2])
            if not res["covers_auction"]:
                short_data += 1
    say(f"done · {len(trades)} tickets over {days} sessions")
    return {"status": "ok", "trades": trades, "sessions": days, "short_data_sessions": short_data,
            "notes": notes[:20], "params": p.as_dict(), "start": start, "end": end,
            "summary": summarise(trades, days, short_data, p)}


def _by_day(t) -> list:
    """One row per session: when it went on, when it came off, and which leg carried it."""
    out = []
    for d, g in t.groupby("date"):
        won = g.loc[g.pnl.idxmax()]
        lost = g.loc[g.pnl.idxmin()]
        out.append({
            "date": d,
            "entry_time": str(g.entry_time.min()),
            "exit_time": str(g.exit_time.max()),
            "tickets": int(len(g)),
            "lots": int(g.lots.max()) if "lots" in g else None,
            "spent": round(float(g.cost.sum()), 2),
            "net": round(float(g.pnl.sum()), 2),
            "winner": f"{won.option_type} {won.strike:g}",
            "winner_pnl": round(float(won.pnl), 2),
            "loser": f"{lost.option_type} {lost.strike:g}",
            "loser_pnl": round(float(lost.pnl), 2),
            "exits": " / ".join(sorted(set(g.exit_reason))),
        })
    return out


def _by_month(t) -> list:
    """The month-by-month record, which is the honest unit for judging this."""
    out = []
    for m, g in t.groupby("month"):
        day = g.groupby("date")["pnl"].sum()
        out.append({
            "month": m, "sessions": int(day.size), "tickets": int(len(g)),
            "spent": round(float(g.cost.sum()), 2),
            "net": round(float(g.pnl.sum()), 2),
            "green_days": int((day > 0).sum()),
            "best_day": round(float(day.max()), 2),
            "worst_day": round(float(day.min()), 2),
        })
    return out


def _timing(t) -> dict:
    """When the trade actually goes on and comes off, and how long a leg survives."""
    def mins(v):
        try:
            h, m = str(v).split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return None
    ins = [m for m in (mins(v) for v in t.entry_time) if m is not None]
    outs = [m for m in (mins(v) for v in t.exit_time) if m is not None]
    held = [b - a for a, b in zip(ins, outs) if b >= a]
    def hhmm_(x):
        return f"{int(x) // 60:02d}:{int(x) % 60:02d}"
    stopped = t[t.exit_reason == "STOP"]
    st_held = [b - a for a, b in zip(
        [mins(v) for v in stopped.entry_time], [mins(v) for v in stopped.exit_time])
        if a is not None and b is not None and b >= a]
    return {
        "first_entry": hhmm_(min(ins)) if ins else None,
        "last_entry": hhmm_(max(ins)) if ins else None,
        "median_entry": hhmm_(sorted(ins)[len(ins) // 2]) if ins else None,
        "median_exit": hhmm_(sorted(outs)[len(outs) // 2]) if outs else None,
        "median_held_min": int(sorted(held)[len(held) // 2]) if held else None,
        "longest_held_min": int(max(held)) if held else None,
        "median_minutes_to_stop": int(sorted(st_held)[len(st_held) // 2]) if st_held else None,
    }


def summarise(trades: list[dict], sessions: int, short_data: int, p: ST.Params) -> dict:
    t = pd.DataFrame(trades)
    base = {"sessions": sessions, "short_data_sessions": short_data, "tickets": int(len(t))}
    if t.empty:
        return {**base, "message": "no ₹1 option was available to buy in this window"}
    by_day = t.groupby("date").agg(pnl=("pnl", "sum"), cost=("cost", "sum"), tickets=("pnl", "size"))
    wins = t[t.pnl > 0]
    return {
        **base,
        "days_traded": int(len(by_day)),
        "net": round(float(t.pnl.sum()), 2),
        "spent": round(float(t.cost.sum()), 2),
        "win_rate": round(float((t.pnl > 0).mean() * 100), 1),
        "avg_ticket": round(float(t.pnl.mean()), 2),
        "best_ticket": round(float(t.pnl.max()), 2),
        "worst_ticket": round(float(t.pnl.min()), 2),
        "hit_target": int((t.exit_reason == "TARGET").sum()),
        "exits": {k: int(v) for k, v in t.exit_reason.value_counts().items()},
        "median_peak_gain_pts": round(float(t.peak_gain_pts.median()), 2),
        "best_peak_gain_pts": round(float(t.peak_gain_pts.max()), 2),
        "days_in_profit": int((by_day.pnl > 0).sum()),
        "best_day": round(float(by_day.pnl.max()), 2),
        "worst_day": round(float(by_day.pnl.min()), 2),
        "avg_day": round(float(by_day.pnl.mean()), 2),
        "by_day": _by_day(t),
        "by_side": [{"side": s, "tickets": int(len(x)), "net": round(float(x.pnl.sum()), 2)}
                    for s, x in t.groupby("option_type")],
        "by_month": _by_month(t),
        "timing": _timing(t),
        "note": (f"{short_data} of {sessions} sessions have data only up to 15:29, so the "
                 "15:30–15:40 minutes the auction moves are missing there"),
    }
