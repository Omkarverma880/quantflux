"""
The calendar ratio spread over stored history, on real option prints.

Every leg is priced from the Market Store at the minute it is entered and the minute it is
closed. Nothing is modelled: if a contract has no print in the exit minute, its last traded
price is carried and the minute is counted in ``gap_min`` rather than interpolated, and if a
leg cannot be filled at all the day is skipped with the reason recorded.

The rules that keep the number honest, in the same spirit as the Options Lab engine:

    NO LOOK-AHEAD     The chain is read at the entry minute and the structure is decided from
                      that snapshot alone. Strikes are then fixed BY NAME for the life of the
                      trade — they never re-centre because spot moved.
    REAL COSTS        Zerodha statutory charges for the actual side of each leg, plus
                      slippage, on all six legs in and all six out.
    NO SETTLEMENT MATHS  Holding to weekly expiry squares off at the real traded price on the
                      expiry day rather than computing an intrinsic settlement, because the
                      settlement price is not in the store and inventing one would be the
                      largest single assumption in the result.
    SAY WHEN IT CANNOT  A day whose chain cannot supply the six legs is reported as untraded,
                      with the reason. That count is as much the result as the P&L is.

A caution that applies to this strategy specifically: it is short two near-dated options and
long three far-dated ones on each side, so it carries real risk on a gap. The summary reports
the worst day and the drawdown alongside the total, and no position sizing here should be read
as a recommendation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.calendar_ratio import structure as ST
from research.market_store import store as MS
from research.options_lab.execution import CostModel

logger = get_logger("research.calendar_ratio.backtest")

# Lot sizes change by exchange notice, so these are defaults the caller can override.
LOT_SIZE = {"NIFTY": 65, "BANKNIFTY": 35, "SENSEX": 20, "BANKEX": 30,
            "FINNIFTY": 65, "MIDCPNIFTY": 120}
SESSION_LAST = 15 * 60 + 29


@dataclass
class Config:
    start: str = ""
    end: str = ""
    params: Optional[ST.Params] = None
    lot_size: int = 0                  # 0 = look it up from the index
    slippage_pts: float = 0.5          # per leg, per side
    lots_multiplier: int = 1           # scale the whole structure

    def __post_init__(self):
        if self.params is None:
            self.params = ST.Params()
        if not self.lot_size:
            self.lot_size = LOT_SIZE.get(self.params.index.upper(), 65)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["params"] = self.params.as_dict()
        return d


# ── data ────────────────────────────────────────────────────────────────────
def _options(index: str, start: str, end: str) -> pd.DataFrame:
    d = MS.read("options", index.upper(), start, end,
                columns=["timestamp", "contract", "expiry_date", "strike", "option_type",
                         "open", "high", "low", "close", "volume"])
    if d.empty:
        return d
    d["ts"] = pd.to_datetime(d["timestamp"])
    d["date"] = d["ts"].dt.date
    d["m"] = d["ts"].dt.hour * 60 + d["ts"].dt.minute
    d["expiry_date"] = pd.to_datetime(d["expiry_date"]).dt.date
    return d


def _snapshot(day_df: pd.DataFrame, minute: int) -> pd.DataFrame:
    """The chain as of one minute: the last print at or before it, per contract."""
    upto = day_df[day_df["m"] <= minute]
    if upto.empty:
        return upto
    last = upto.sort_values("m").groupby("contract", as_index=False).last()
    last["premium"] = last["close"]
    return last


def _price_at(day_df: pd.DataFrame, contract: str, minute: int) -> tuple[Optional[float], bool]:
    """A contract's price at a minute, carrying the last print forward.

    The second value is True when the price had to be carried — the contract did not print in
    that minute. Those are counted rather than hidden, because a carried price is the one
    place a backtest on thin strikes quietly becomes fiction.
    """
    g = day_df[(day_df["contract"] == contract) & (day_df["m"] <= minute)]
    if g.empty:
        return None, False
    row = g.sort_values("m").iloc[-1]
    return float(row["close"]), bool(int(row["m"]) != minute)


# ── one trade ───────────────────────────────────────────────────────────────
def _exit_day_and_minute(entry_day: date, built: dict, p: ST.Params,
                         trading_days: list[date]) -> tuple[Optional[date], int]:
    if p.exit_mode == "intraday":
        return entry_day, p.exit_minute
    if p.exit_mode == "fixed_days":
        later = [d for d in trading_days if d > entry_day]
        n = max(1, int(p.hold_days))
        return (later[n - 1] if len(later) >= n else (later[-1] if later else None)), p.exit_minute
    weekly = built["expiries"]["weekly"]
    on_or_before = [d for d in trading_days if entry_day <= d <= weekly]
    return (on_or_before[-1] if on_or_before else None), p.exit_minute


def _run_one(opts: pd.DataFrame, entry_day: date, trading_days: list[date],
             cfg: Config) -> Optional[dict]:
    p = cfg.params
    day_df = opts[opts["date"] == entry_day]
    if day_df.empty:
        return None
    snap = _snapshot(day_df, p.entry_minute)
    if snap.empty:
        return {"date": str(entry_day), "taken": False,
                "why": f"no option prints at or before {p.entry_minute // 60:02d}:"
                       f"{p.entry_minute % 60:02d}"}

    built = ST.build(snap, entry_day, p)
    if not built["tradable"]:
        return {"date": str(entry_day), "taken": False,
                "why": "; ".join(built["blockers"])[:400], "structure": built}

    exit_day, exit_min = _exit_day_and_minute(entry_day, built, p, trading_days)
    if exit_day is None:
        return {"date": str(entry_day), "taken": False,
                "why": "no trading day available to close the position"}

    exit_df = opts[opts["date"] == exit_day]
    qty = cfg.lot_size * cfg.lots_multiplier
    costs = CostModel(slippage_pts=cfg.slippage_pts)

    legs, gross, charges, gaps = [], 0.0, 0.0, 0
    for l in built["legs"]:
        sym = l["tradingsymbol"]
        entry_px = float(l["premium"])
        px, carried = _price_at(exit_df, sym, exit_min)
        if px is None:
            return {"date": str(entry_day), "taken": False,
                    "why": f"{sym} has no print on {exit_day} to close against",
                    "structure": built}
        gaps += int(carried)
        n = l["lots"] * qty
        long_leg = l["side"] == ST.BUY
        # slippage always works against the position, on both sides
        fill_in = entry_px + cfg.slippage_pts if long_leg else entry_px - cfg.slippage_pts
        fill_out = px - cfg.slippage_pts if long_leg else px + cfg.slippage_pts
        pts = (fill_out - fill_in) if long_leg else (fill_in - fill_out)
        leg_gross = pts * n
        buy_px, sell_px = (fill_in, fill_out) if long_leg else (fill_out, fill_in)
        leg_cost = costs.round_trip(buy_px, sell_px, n)
        gross += leg_gross
        charges += leg_cost
        legs.append({**l, "entry_price": round(entry_px, 2), "exit_price": round(px, 2),
                     "qty": n, "points": round(pts, 2),
                     "gross_rs": round(leg_gross, 0), "costs_rs": round(leg_cost, 0),
                     "pnl_rs": round(leg_gross - leg_cost, 0), "carried": carried})

    pnl = gross - charges
    return {
        "date": str(entry_day), "taken": True,
        "exit_date": str(exit_day),
        "exit_time": f"{exit_min // 60:02d}:{exit_min % 60:02d}",
        "held_days": (exit_day - entry_day).days,
        "weekly_expiry": str(built["expiries"]["weekly"]),
        "monthly_expiry": str(built["expiries"]["monthly"]),
        "weekly_dte": built["expiries"]["weekly_dte"],
        "monthly_dte": built["expiries"]["monthly_dte"],
        # how much longer-dated the far leg is than the near one. On historical dates the
        # month's own monthly contract is usually delisted, so the far leg lands further out
        # than the specification intends — reported per cycle rather than averaged away.
        "gap_days": built["expiries"].get("gap_days"),
        "net_premium": built.get("net_premium"),
        "legs": legs,
        "gross_rs": round(gross, 0), "costs_rs": round(charges, 0),
        "pnl_rs": round(pnl, 0),
        "carried_legs": gaps,
    }


# ── statistics ──────────────────────────────────────────────────────────────
def _stats(pnl: np.ndarray) -> dict:
    base = {"trades": 0, "net": 0.0, "avg": None, "median": None, "win_rate": None,
            "best": None, "worst": None, "profit_factor": None, "max_drawdown": None,
            "t_stat": None, "p_value": None}
    if not len(pnl):
        return base
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq = np.cumsum(pnl)
    gl = float(-losses.sum())
    t = (float(pnl.mean() / (pnl.std(ddof=1) / np.sqrt(len(pnl))))
         if len(pnl) > 1 and pnl.std(ddof=1) > 0 else None)
    p = None
    if len(pnl) >= 5:
        rng = np.random.default_rng(7)
        p = float((rng.choice(pnl, size=(5000, len(pnl)), replace=True).mean(axis=1) <= 0).mean())
    base.update({
        "trades": int(len(pnl)), "net": round(float(pnl.sum()), 0),
        "avg": round(float(pnl.mean()), 1), "median": round(float(np.median(pnl)), 1),
        "win_rate": round(100.0 * len(wins) / len(pnl), 1),
        "best": round(float(pnl.max()), 0), "worst": round(float(pnl.min()), 0),
        "profit_factor": round(float(wins.sum()) / gl, 2) if gl > 0 else None,
        "max_drawdown": round(float((np.maximum.accumulate(eq) - eq).max()), 0),
        "t_stat": round(t, 2) if t is not None else None,
        "p_value": round(p, 4) if p is not None else None,
    })
    return base


def _verdict(s: dict, skipped: int, days: int) -> dict:
    n, t, net = s["trades"], s.get("t_stat"), s.get("net") or 0.0
    if n == 0:
        return {"tone": "none", "headline": "The structure could not be built on any day.",
                "detail": f"{skipped} of {days} sessions were skipped. The reasons are listed "
                          f"below — usually the chain in the store does not reach the premium "
                          f"a leg asks for, or only one expiry is stored so there is no "
                          f"calendar to build."}
    if n < 30:
        return {"tone": "thin", "headline": f"{n} trades is too few to conclude anything.",
                "detail": f"Net {net:,.0f}. A short-dated short position can look calm for "
                          f"weeks and give it all back on one gap, so a small sample flatters "
                          f"this structure more than most."}
    if net <= 0:
        return {"tone": "bad", "headline": f"This lost money: {net:,.0f} over {n} trades.",
                "detail": "After real charges on twelve fills per cycle, the decay captured "
                          "did not cover the cost of being in the trade."}
    if t is None or t < 2.0:
        return {"tone": "weak",
                "headline": f"Profitable but not significant: {net:,.0f}, t={t}.",
                "detail": "A t-statistic under 2 is an ordinary run of luck. For a short-"
                          "premium structure this matters more than usual: the losses are "
                          "rare and large, so a quiet sample understates the risk."}
    return {"tone": "ok", "headline": f"Net {net:,.0f} over {n} trades, t={t}.",
            "detail": f"Worst single cycle {s.get('worst'):,.0f} and max drawdown "
                      f"{s.get('max_drawdown'):,.0f} — judge the total against those, not on "
                      f"its own."}


def _coverage(index: str) -> Optional[str]:
    try:
        s = MS.summary() or {}
        parts = []
        for kind, label in (("spot", "index minutes"), ("options", "options")):
            blk = ((s.get(kind) or {}).get("by_underlying") or {}).get(index.upper()) or {}
            a, b = blk.get("first_month"), blk.get("last_month")
            if a and b:
                parts.append(f"{label} {a} to {b}")
        return "; ".join(parts) or None
    except Exception as exc:
        logger.debug("coverage lookup failed: %s", exc)
    return None


def diagnose(index: str, start: str, end: str) -> dict:
    """What the stored chain can and cannot support, before anyone reads a P&L.

    This exists because the honest answer for this strategy is often "the data cannot price
    it", and that deserves to be stated precisely — how many expiries are listed per day and
    how far the premiums reach — rather than appearing as a mysteriously empty result.
    """
    o = _options(index, start, end)
    if o.empty:
        return {"ok": False, "index": index.upper(),
                "message": f"The store holds no {index.upper()} option minutes in that range.",
                "coverage": _coverage(index)}
    per_day = o.groupby("date")["expiry_date"].nunique()
    at = o[o["m"] == 9 * 60 + 30]
    reach = {}
    for typ in ("CE", "PE"):
        s = at[at["option_type"] == typ].groupby("date")["close"].max()
        reach[typ] = {"median_max_premium": round(float(s.median()), 0) if len(s) else None,
                      "days": int(len(s))}
    return {
        "ok": True, "index": index.upper(),
        "days": int(o["date"].nunique()),
        "expiries_per_day": {"min": int(per_day.min()), "median": float(per_day.median()),
                             "max": int(per_day.max())},
        "has_two_expiries_on": int((per_day >= 2).sum()),
        "strikes_per_day_median": int(o.groupby("date")["strike"].nunique().median()),
        "premium_reach_at_0930": reach,
        "coverage": _coverage(index),
        "verdict": ("The chain holds more than one expiry on at least some days, so a calendar "
                    "can be built." if int((per_day >= 2).sum()) > 0 else
                    "Only ONE expiry is stored on every day in this range, so the monthly leg "
                    "of this structure has nothing to price against. A calendar spread cannot "
                    "be backtested on it at all."),
    }


def run(cfg: Config) -> dict:
    p = cfg.params
    if not cfg.start or not cfg.end:
        return {"status": "bad_request", "message": "Give a start and an end date."}
    if pd.Timestamp(cfg.start) > pd.Timestamp(cfg.end):
        return {"status": "bad_request", "message": "The start date is after the end date."}

    opts = _options(p.index, cfg.start, cfg.end)
    if opts.empty:
        return {"status": "no_data",
                "message": (f"The store holds no {p.index.upper()} option minutes in that "
                            f"range."),
                "coverage": _coverage(p.index),
                "diagnosis": diagnose(p.index, cfg.start, cfg.end),
                "config": cfg.as_dict()}

    days = sorted(opts["date"].unique())
    results = [_run_one(opts, d, days, cfg) for d in days]
    results = [r for r in results if r]
    taken = [r for r in results if r.get("taken")]
    skipped = [r for r in results if not r.get("taken")]

    why_counts: dict[str, int] = {}
    for r in skipped:
        key = (r.get("why") or "unknown")[:160]
        why_counts[key] = why_counts.get(key, 0) + 1

    pnl = np.array([r["pnl_rs"] for r in taken], dtype=float)
    overall = _stats(pnl)
    span = {"start": str(days[0]), "end": str(days[-1]), "days": len(days)}

    # How far the long leg sits beyond the short one. It matters because an expired month's
    # own monthly contract cannot be fetched back from the broker, so cycles on past dates
    # tend to reach further out than the live structure would. That is a different trade, and
    # it deserves naming rather than disappearing into an average.
    gaps = np.array([g for g in (t.get("gap_days") for t in taken) if g], dtype=float)
    far_leg = None
    if len(gaps):
        med = float(np.median(gaps))
        far_leg = {
            "median_gap_days": round(med, 0),
            "min_gap_days": round(float(gaps.min()), 0),
            "max_gap_days": round(float(gaps.max()), 0),
            "matches_spec": bool(med <= 40),
            "note": (f"The long leg sits a median of {med:.0f} days past the short one — "
                     f"longer than this structure intends. On past dates that month's own "
                     f"monthly contract is delisted and cannot be fetched back, so the next "
                     f"listed monthly is used instead. Read these as a longer calendar than "
                     f"the one you would put on today." if med > 40 else
                     f"The long leg sits a median of {med:.0f} days past the short one, which "
                     f"is the spacing this structure is meant to have."),
        }

    return {
        "status": "ok" if taken else "no_trades",
        "span": span,
        "far_leg": far_leg,
        "index": p.index.upper(),
        "lot_size": cfg.lot_size,
        "rules": ST.describe(p),
        "exit": {"mode": p.exit_mode, "hold_days": p.hold_days,
                 "time": f"{p.exit_minute // 60:02d}:{p.exit_minute % 60:02d}",
                 "note": ("The source specifies an entry and no exit, so this is a choice "
                          "made here, not a rule from the strategy.")},
        "taken": len(taken), "skipped": len(skipped),
        "skipped_reasons": sorted(why_counts.items(), key=lambda kv: -kv[1])[:8],
        "overall": overall,
        "trades": taken[-400:],
        "trades_truncated": ({"shown": 400, "of": len(taken)} if len(taken) > 400 else None),
        "verdict": _verdict(overall, len(skipped), len(days)),
        "diagnosis": diagnose(p.index, cfg.start, cfg.end),
        "costs": {"slippage_pts_per_leg_per_side": cfg.slippage_pts,
                  "note": "Zerodha statutory charges on each of the twelve fills, plus "
                          "slippage against the position on every one."},
        "config": cfg.as_dict(),
    }
