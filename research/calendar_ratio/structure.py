"""
The six legs, and how each one is chosen.

The structure, as specified:

    SELL  current week   CALL   premium near 150    1 lot
    SELL  current week   CALL   premium near 450    1 lot
    BUY   current month  CALL   premium near 200    3 lots
    SELL  current week   PUT    premium near 150    1 lot
    SELL  current week   PUT    premium near 450    1 lot
    BUY   current month  PUT    premium near 200    3 lots

Two things about it are worth saying out loud, because they drive every design choice below.

FIRST, legs are chosen by PREMIUM, not by strike or moneyness. That inverts the usual
resolution: instead of "which contract is 200 points out", it is "which listed contract is
trading nearest to 150". The strike therefore moves with volatility — on a quiet day the
150-premium call sits close to spot, on a wild one it sits far away. ``pick_leg`` searches the
chain and reports how far off the match landed, because "nearest available" is only honest if
you can see the gap. A leg whose closest match is 300 away from a 450 target is NOT that leg,
and ``tolerance`` is what stops the structure quietly becoming a different trade.

SECOND, the premium maths is near-neutral by construction: 150 + 450 collected against
3 x 200 paid is 600 against 600. The position is not a credit trade — it is a bet that the
near-dated options you are short decay faster than the far-dated ones you are long. That is
the entire edge being claimed, and it only exists while the two expiries are DIFFERENT.

Which is the trap this module exists to catch. In the last week of a month the current weekly
expiry IS the monthly expiry. The "calendar" collapses: you would be selling 2 lots and buying
3 lots of the same expiry, which is a ratio backspread with no time differential at all — a
completely different risk. ``resolve_expiries`` detects that and ``same_expiry_policy`` decides
what to do about it, rather than letting the structure silently change shape one week in four.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

SELL, BUY = "SELL", "BUY"
CE, PE = "CE", "PE"
WEEKLY, MONTHLY = "weekly", "monthly"


@dataclass(frozen=True)
class LegSpec:
    """One leg: what to do, in which expiry, at roughly what premium, in what size."""
    side: str                  # SELL | BUY
    option_type: str           # CE | PE
    expiry: str                # weekly | monthly
    target_premium: float
    lots: int

    @property
    def signed_lots(self) -> int:
        return self.lots if self.side == BUY else -self.lots

    def label(self) -> str:
        return (f"{self.side} {self.lots} lot {self.expiry} {self.option_type} "
                f"@ ~{self.target_premium:g}")


def default_legs() -> list[LegSpec]:
    return [
        LegSpec(SELL, CE, WEEKLY, 150.0, 1),
        LegSpec(SELL, CE, WEEKLY, 450.0, 1),
        LegSpec(BUY, CE, MONTHLY, 200.0, 3),
        LegSpec(SELL, PE, WEEKLY, 150.0, 1),
        LegSpec(SELL, PE, WEEKLY, 450.0, 1),
        LegSpec(BUY, PE, MONTHLY, 200.0, 3),
    ]


@dataclass
class Params:
    index: str = "NIFTY"               # NIFTY | SENSEX | any index in the registry
    entry_minute: int = 9 * 60 + 30    # 09:30, as specified
    legs: list[LegSpec] = field(default_factory=default_legs)

    # How close a listed premium must be to count as the leg that was asked for. Both are
    # applied; the larger of the two is the allowance, so a 450 leg gets more room in points
    # than a 150 leg does without either becoming meaningless.
    tolerance_pct: float = 25.0
    tolerance_abs: float = 20.0

    # What to do in the week where the weekly expiry and the monthly expiry are the same
    # contract. "skip" takes no trade; "next_month" buys the following month instead, which
    # keeps a real calendar but is a different trade from the one specified.
    same_expiry_policy: str = "skip"   # skip | next_month

    # Exit. The specification gives an entry and no exit, so this is a choice, not a rule from
    # the source, and it is surfaced rather than buried.
    exit_mode: str = "weekly_expiry"   # intraday | weekly_expiry | fixed_days
    exit_minute: int = 15 * 60 + 15    # for intraday, and the square-off on the exit day
    hold_days: int = 1                 # for fixed_days
    one_at_a_time: bool = True         # no stacking a new structure over an open one

    def as_dict(self) -> dict:
        d = asdict(self)
        d["legs"] = [asdict(l) for l in self.legs]
        return d

    @staticmethod
    def from_dict(d: Optional[dict]) -> "Params":
        d = dict(d or {})
        legs = d.pop("legs", None)
        p = Params(**{k: v for k, v in d.items() if k in Params.__dataclass_fields__})
        if legs:
            p.legs = [LegSpec(side=str(l["side"]).upper(),
                              option_type=str(l["option_type"]).upper(),
                              expiry=str(l["expiry"]).lower(),
                              target_premium=float(l["target_premium"]),
                              lots=max(1, int(l["lots"]))) for l in legs]
        return p


def tolerance_for(p: Params, target: float) -> float:
    return max(p.tolerance_abs, target * p.tolerance_pct / 100.0)


# ── expiries ────────────────────────────────────────────────────────────────
def resolve_expiries(available: list[date], today: date,
                     policy: str = "skip") -> dict:
    """Pick the current-week and current-month expiry out of what is listed.

    "Current month" is the LAST expiry falling in this calendar month — which is what the
    monthly contract is — and it rolls to next month's once this month's has passed. When that
    turns out to be the very same contract as the weekly, the calendar has collapsed and the
    result says so instead of pretending the structure is intact.
    """
    ex = sorted({e for e in available if e >= today})
    if not ex:
        return {"ok": False, "reason": "no expiry on or after this date is listed",
                "weekly": None, "monthly": None, "collapsed": False}

    weekly = ex[0]
    this_month = [e for e in ex if (e.year, e.month) == (today.year, today.month)]
    if this_month:
        monthly = this_month[-1]
    else:
        monthly = ex[-1]

    collapsed = (monthly == weekly)
    if collapsed and policy == "next_month":
        later = [e for e in ex if (e.year, e.month) != (weekly.year, weekly.month)]
        if later:
            nxt = [e for e in later if (e.year, e.month) == (later[0].year, later[0].month)]
            monthly = nxt[-1] if nxt else later[-1]
            collapsed = (monthly == weekly)

    out = {"ok": True, "weekly": weekly, "monthly": monthly, "collapsed": collapsed,
           "listed": [str(e) for e in ex[:8]],
           "weekly_dte": (weekly - today).days, "monthly_dte": (monthly - today).days}
    if collapsed:
        out.update({
            "ok": policy != "skip",
            "reason": ("this week's expiry IS this month's expiry, so both sides of the "
                       "calendar would be the same contract — there is no time differential "
                       "left to decay"),
        })
    return out


# ── leg selection ───────────────────────────────────────────────────────────
def pick_leg(chain: pd.DataFrame, leg: LegSpec, expiry: date, p: Params) -> dict:
    """The listed contract trading nearest this leg's target premium.

    ``chain`` needs ``strike``, ``option_type``, ``expiry_date`` and ``premium``. The result
    always reports the gap between what was asked for and what was found, and marks the leg
    out of tolerance rather than dropping it — a leg that could not be filled is information,
    not an error to swallow.
    """
    want = chain[(chain["option_type"] == leg.option_type)
                 & (chain["expiry_date"] == expiry)
                 & (chain["premium"] > 0)]
    base = {"spec": leg.label(), "side": leg.side, "option_type": leg.option_type,
            "expiry_kind": leg.expiry, "expiry": str(expiry), "lots": leg.lots,
            "target_premium": leg.target_premium}
    if want.empty:
        return {**base, "ok": False, "strike": None, "premium": None, "gap": None,
                "why": f"no {leg.option_type} listed for {expiry} in the data"}

    gaps = (want["premium"] - leg.target_premium).abs()
    row = want.loc[gaps.idxmin()]
    gap = float(abs(float(row["premium"]) - leg.target_premium))
    tol = tolerance_for(p, leg.target_premium)
    ok = gap <= tol
    return {
        **base, "ok": ok,
        "strike": float(row["strike"]),
        "premium": round(float(row["premium"]), 2),
        "gap": round(gap, 2), "tolerance": round(tol, 2),
        "tradingsymbol": row.get("contract") or row.get("tradingsymbol"),
        "why": None if ok else (
            f"the closest {leg.option_type} to a {leg.target_premium:g} premium is "
            f"{float(row['premium']):.2f} at strike {float(row['strike']):.0f} — off by "
            f"{gap:.0f}, more than the {tol:.0f} allowed. The chain in the data does not "
            f"reach that premium."),
    }


def build(chain: pd.DataFrame, today: date, p: Params) -> dict:
    """The whole structure for one day: expiries, six legs, and whether it is tradable."""
    avail = sorted({pd.Timestamp(e).date() for e in chain["expiry_date"].dropna().unique()})
    exp = resolve_expiries(avail, today, p.same_expiry_policy)
    out = {"date": str(today), "index": p.index, "expiries": exp, "legs": [],
           "tradable": False, "blockers": []}
    if not exp["weekly"]:
        out["blockers"].append(exp.get("reason") or "no expiry available")
        return out
    if exp["collapsed"] and p.same_expiry_policy == "skip":
        out["blockers"].append(exp["reason"])
        return out

    by_kind = {WEEKLY: exp["weekly"], MONTHLY: exp["monthly"]}
    legs = [pick_leg(chain, l, by_kind[l.expiry], p) for l in p.legs]
    out["legs"] = legs

    bad = [l for l in legs if not l["ok"]]
    for l in bad:
        out["blockers"].append(l["why"])
    out["tradable"] = not bad
    if out["tradable"]:
        out.update(summarize(legs))
    return out


def summarize(legs: list[dict], lot_size: int = 1) -> dict:
    """Net premium and size. A SELL brings premium in, a BUY pays it out."""
    credit = sum(l["premium"] * l["lots"] for l in legs if l["side"] == SELL and l["premium"])
    debit = sum(l["premium"] * l["lots"] for l in legs if l["side"] == BUY and l["premium"])
    short_lots = sum(l["lots"] for l in legs if l["side"] == SELL)
    long_lots = sum(l["lots"] for l in legs if l["side"] == BUY)
    net = credit - debit
    return {
        "premium_collected": round(credit, 2),
        "premium_paid": round(debit, 2),
        "net_premium": round(net, 2),
        "net_is": "credit" if net > 0 else "debit" if net < 0 else "flat",
        "short_lots": short_lots, "long_lots": long_lots,
        "net_premium_rs": round(net * lot_size, 0) if lot_size > 1 else None,
        "note": ("Premium in and premium out are close by design — the position is not a "
                 "credit trade. What it is betting on is that the near-dated options you are "
                 "short lose value faster than the far-dated ones you are long."),
    }


def describe(p: Params) -> str:
    return (f"At {p.entry_minute // 60:02d}:{p.entry_minute % 60:02d} on {p.index}: "
            + "; ".join(l.label() for l in p.legs)
            + f". Legs are chosen by premium, within {p.tolerance_pct:g}% or "
              f"{p.tolerance_abs:g} points of target, whichever is larger.")
