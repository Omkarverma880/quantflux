"""
Flux Lab — backtest of the failed-breakout iron fly on the stored Market Store history.

Honesty rules (the same ones the research used):
  * the signal is found by ``strategy.scan`` on completed 1-minute NIFTY closes only
  * the fly is filled at the NEXT minute's real option opens: sold legs 0.5 pt worse, bought legs
    0.5 pt worse (``SLIPPAGE`` per leg per side); every leg must have actually traded in the 6
    minutes before the fill, otherwise the day is skipped, never priced from nothing
  * exits are decided on each minute's closing prices and filled at the next minute's open
  * Zerodha charges from the shared ``CostModel`` on every leg, both ways
  * one lot = 65 (today's NIFTY lot) across the whole history, so years compare like for like
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.flux_lab import strategy as ST
from research.market_store import store as MS
from research.options_lab.execution import CostModel

logger = get_logger("research.flux_lab.backtest")

LOT = 65
SLIPPAGE = 0.5
COST = CostModel(slippage_pts=0.0)       # slippage is applied on the fill prices, not twice
GRID = np.arange(9 * 60 + 15, 15 * 60 + 30)


def coverage() -> dict:
    spot = MS.read("spot", "NIFTY", columns=["timestamp"])
    opts = MS.read("options", "NIFTY", columns=["timestamp"])
    out = {}
    for k, df in (("spot", spot), ("options", opts)):
        if not df.empty:
            ts = pd.to_datetime(df["timestamp"])
            out[k] = {"first": str(ts.min().date()), "last": str(ts.max().date()),
                      "sessions": int(ts.dt.date.nunique())}
    return out


def _months(start: str, end: str) -> list[tuple[str, str]]:
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    out, cur = [], pd.Timestamp(a.year, a.month, 1)
    while cur <= b:
        nxt = cur + pd.offsets.MonthBegin(1)
        out.append((str(max(cur, a).date()), str(min(nxt - pd.Timedelta(days=1), b).date())))
        cur = nxt
    return out


def _load_month(start: str, end: str) -> dict:
    """Per session: NIFTY closes by minute, and nearest-expiry option open/close grids."""
    spot = MS.read("spot", "NIFTY", start=start, end=end, columns=["timestamp", "close"])
    opts = MS.read("options", "NIFTY", start=start, end=end,
                   columns=["timestamp", "expiry_date", "strike", "option_type", "open", "close"])
    if spot.empty or opts.empty:
        return {}
    ts = pd.to_datetime(spot["timestamp"])
    spot = spot.assign(date=ts.dt.date, m=(ts.dt.hour * 60 + ts.dt.minute).astype(int))
    ts = pd.to_datetime(opts["timestamp"])
    opts = opts.assign(date=ts.dt.date, m=(ts.dt.hour * 60 + ts.dt.minute).astype(int),
                       expiry_date=pd.to_datetime(opts["expiry_date"]).dt.date)
    opts = opts[opts["expiry_date"] >= opts["date"]]
    opts = opts[opts["expiry_date"] == opts.groupby("date")["expiry_date"].transform("min")]
    days = {}
    spot_by_day = dict(tuple(spot.groupby("date")))
    for d, g in opts.groupby("date"):
        s = spot_by_day.get(d)
        if s is None or s.empty:
            continue
        closes = s.drop_duplicates("m", keep="last").set_index("m")["close"].astype(float)
        op = g.pivot_table(index="m", columns=["option_type", "strike"], values="open", aggfunc="last").reindex(GRID)
        cl = g.pivot_table(index="m", columns=["option_type", "strike"], values="close", aggfunc="last").reindex(GRID)
        printed = cl.notna()
        cl = cl.ffill()
        op = op.where(printed).fillna(cl)          # a minute with no print trades at the last price
        # plain Python floats: NumPy scalars travel badly into JSON and into the database
        days[d] = {"closes": {int(k): float(v) for k, v in closes.items()}, "op": op, "cl": cl, "printed": printed,
                   "expiry": g["expiry_date"].iloc[0]}
    return days


def _col(frame: pd.DataFrame, typ: str, strike: float):
    for k in ((typ, float(strike)), (typ, int(strike)), (typ, strike)):
        if k in frame.columns:
            return k
    return None


def simulate_day(d: date, day: dict, lots: int = 1, rule: ST.Rule = ST.RULE) -> Optional[dict]:
    """One session: find the signal the way paper trading would, then trade the fly."""
    closes = day["closes"]
    res = ST.scan(closes, rule)
    sig = res["signal"]
    if not sig:
        return None
    fill = sig["minute"] + 1
    legs = ST.legs(sig["spot"], rule)
    op, cl, pr = day["op"], day["cl"], day["printed"]
    cols = [_col(cl, l["type"], l["strike"]) for l in legs]
    if any(c is None for c in cols):
        return {"skipped": "a leg's strike is not in the stored chain", "date": str(d), "signal": sig}
    if any(not pr[c].loc[fill - 6:fill].any() for c in cols):
        return {"skipped": "a leg had not traded in the minutes before the fill", "date": str(d), "signal": sig}
    mins = np.arange(fill, rule.squareoff + 1)
    CL = np.vstack([cl[c].reindex(mins).to_numpy(float) for c in cols])
    OP = np.vstack([op[c].reindex(mins).to_numpy(float) for c in cols])
    if np.isnan(CL).any() or np.isnan(OP).any():
        return {"skipped": "missing option prices after the fill", "date": str(d), "signal": sig}
    q = np.array([l["q"] for l in legs], float)
    entry = OP[:, 0] + q * SLIPPAGE                  # sold legs fill lower, bought legs higher
    credit = float(-(q * entry).sum())
    if credit <= 5:
        return {"skipped": f"credit only {credit:.2f} pts", "date": str(d), "signal": sig}
    path = credit - (-(q[:, None] * CL)).sum(0)     # per-unit P&L on each minute's close
    reason, xi = "EOD", len(mins) - 1
    for i in range(len(mins) - 1):
        r = ST.exit_reason(float(path[i]), credit, int(mins[i]), rule)
        if r:
            reason, xi = r, i + 1                    # decided on this close, filled next open
            break
    px = CL[:, -1] if reason == "EOD" else OP[:, xi]
    exit_ = px - q * SLIPPAGE
    qty = LOT * lots
    gross = float((-q * (entry - exit_)).sum() * qty)
    charges = 0.0
    for i in range(4):
        charges += (COST.round_trip(exit_[i], entry[i], qty) if q[i] < 0
                    else COST.round_trip(entry[i], exit_[i], qty))
    debit = float(-(q * exit_).sum())
    exit_min = int(mins[xi]) if reason != "EOD" else rule.squareoff
    spot_exit = closes.get(exit_min - 1) or closes.get(exit_min)
    for l, e, x in zip(legs, entry, exit_):
        l.update(entry=round(float(e), 2), exit=round(float(x), 2))
    return {
        "date": str(d), "month": str(d)[:7], "signal_time": sig["time"], "entry_time": ST.hhmm(fill),
        "exit_time": ST.hhmm(exit_min), "exit_reason": reason, "held_min": exit_min - fill,
        "expiry": str(day["expiry"]), "dte": (day["expiry"] - d).days,
        "atm": float(legs[0]["strike"]), "spot_entry": round(float(sig["spot"]), 2),
        "spot_exit": round(float(spot_exit), 2) if spot_exit else None,
        "credit": round(credit, 2), "debit": round(debit, 2), "gross_pts": round(credit - debit, 2),
        "lots": lots, "qty": qty, "gross": round(gross, 2), "charges": round(charges, 2),
        "pnl": round(gross - charges, 2), "legs": legs, "signal": sig,
        "events": res["events"],
    }


def summarise(trades: list[dict], sessions: int, skipped: list[dict]) -> dict:
    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades": 0, "sessions": sessions, "skipped": len(skipped),
                "message": "no trades in this range"}
    w, l = t.pnl[t.pnl > 0], t.pnl[t.pnl <= 0]
    eq = t.pnl.cumsum()
    months = t.groupby("month").agg(trades=("pnl", "size"), net=("pnl", "sum"),
                                    wins=("pnl", lambda s: int((s > 0).sum())))
    monthly = [{"month": m, "trades": int(r.trades), "wins": int(r.wins), "net": round(float(r.net), 2)}
               for m, r in months.iterrows()]
    years = t.assign(year=t.date.str[:4]).groupby("year").pnl.agg(["size", "sum"])
    return {
        "trades": int(len(t)), "sessions": sessions, "skipped": len(skipped),
        "trade_days_pct": round(len(t) / sessions * 100, 1) if sessions else None,
        "wins": int(len(w)), "win_rate": round(len(w) / len(t) * 100, 1),
        "avg_win": round(float(w.mean()), 2) if len(w) else 0.0,
        "avg_loss": round(float(l.mean()), 2) if len(l) else 0.0,
        "profit_factor": round(float(w.sum() / -l.sum()), 2) if l.sum() < 0 else None,
        "net": round(float(t.pnl.sum()), 2), "charges": round(float(t.charges.sum()), 2),
        "avg_trade": round(float(t.pnl.mean()), 2),
        "best": round(float(t.pnl.max()), 2), "worst": round(float(t.pnl.min()), 2),
        "max_drawdown": round(float((eq - eq.cummax()).min()), 2),
        "avg_credit_pts": round(float(t.credit.mean()), 2),
        "exits": {k: int(v) for k, v in t.exit_reason.value_counts().items()},
        "months": len(monthly), "green_months": int(sum(1 for m in monthly if m["net"] > 0)),
        "avg_month": round(float(months.net.mean()), 2),
        "worst_month": round(float(months.net.min()), 2), "best_month": round(float(months.net.max()), 2),
        "monthly": monthly,
        "yearly": [{"year": y, "trades": int(r["size"]), "net": round(float(r["sum"]), 2)} for y, r in years.iterrows()],
        "skip_reasons": {str(k): int(v) for k, v in
                         pd.Series([s["skipped"] for s in skipped]).value_counts().items()} if skipped else {},
    }


def run(start: str, end: str, lots: int = 1, progress: Optional[Callable[[str], None]] = None) -> dict:
    say = progress or (lambda m: None)
    trades, skipped, sessions = [], [], 0
    for a, b in _months(start, end):
        say(f"{a[:7]} · {len(trades)} trades so far")
        days = _load_month(a, b)
        for d in sorted(days):
            sessions += 1
            r = simulate_day(d, days[d], lots)
            if r is None:
                continue
            (skipped if "skipped" in r else trades).append(r)
    say(f"done · {len(trades)} trades in {sessions} sessions")
    return {"status": "ok", "strategy": ST.STRATEGY_NAME, "engine_version": ST.ENGINE_VERSION,
            "rule": ST.RULE.as_dict(), "start": start, "end": end, "lots": lots,
            "summary": summarise(trades, sessions, skipped), "trades": trades, "skipped": skipped}
