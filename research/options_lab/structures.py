"""
Options Lab — multi-leg structures on REAL option prices.

Single legs, straddles, strangles and iron flies are all "a set of legs entered at
one decision bar and exited together". Built on the same honesty rules as
``execution.py``, plus the ones multi-leg positions need:

  SAME-INSTANT MARKING   The position is valued on every leg's CLOSE in the same minute.
                         Adding each leg's own worst extreme overstates adverse moves,
                         because a call and a put do not peak in the same minute.
  STALE LEGS ADVERSE     If a leg has no print at exit (it drifted beyond the downloaded
                         strike window) its carried price is replaced adversely: a long
                         leg at intrinsic value, a short leg at carried price +25%.
  LOSS CAPPED BY WINGS   A defined-risk structure can never lose more than its width.
  REAL MARGIN            Lots are sized from a configurable margin per lot, not from
                         premium (which is wrong for anything short).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from research.options_lab.execution import CostModel, SESSION_OPEN, LAST_BAR, STEP, _hhmm

STRUCTURES = ("long_call", "long_put", "short_straddle", "short_strangle", "iron_fly",
              "long_straddle")


@dataclass
class StructureRule:
    structure: str = "iron_fly"
    entry_minute: int = 585              # decision bar (09:45); fill at next bar open
    squareoff: int = 915                 # 15:15
    wing_steps: int = 4                  # iron fly / strangle: strikes from ATM
    moneyness: int = 0                   # single legs: -1 ITM, 0 ATM, +1 OTM
    stop_x_credit: float = 0.0           # short structures: exit when loss >= x * credit (0 = none)
    stop_pct: float = 0.0                # long structures: exit when down this % of debit
    target_pct: float = 0.0              # long: up this %; short: this % of credit captured
    dte_min: int = 2
    dte_max: int = 30
    lots: int = 0                        # 0 = size from capital / margin
    margin_per_lot: float = 40_000.0     # short structures
    capital: float = 100_000.0
    max_lots: int = 10
    stale_adverse: bool = True


@dataclass
class StructureTrade:
    date: str
    structure: str
    dte: int
    decision_time: str
    entry_time: str
    exit_time: str
    spot_entry: float
    spot_exit: float
    legs: str                            # human-readable leg list
    net_entry: float                     # + credit received / - debit paid, per unit
    net_exit: float                      # per unit, same sign convention
    max_loss_unit: float
    lots: int
    qty: int
    capital_used: float
    costs_rs: float
    pnl_rs: float
    ret_on_capital_pct: float
    exit_reason: str
    stale_legs_at_exit: int
    gap_min: int
    # human-readable view of the same trade (prices per unit, always positive)
    premium_in: float = 0.0
    premium_out: float = 0.0
    entry_action: str = ""               # "Paid" (bought / debit) | "Received" (sold / credit)
    exit_action: str = ""
    leg_prices: str = ""                 # "BUY 23250 CE 110.20→242.05"
    plain: str = ""                      # one sentence a person can check against the chart

    def as_dict(self) -> dict:
        return asdict(self)


def _legs_for(rule: StructureRule, atm: float, step: int) -> list[tuple[str, float, int]]:
    """[(type, strike, sign)] — sign +1 bought, -1 sold."""
    w = rule.wing_steps * step
    s = rule.structure
    if s == "long_call":
        return [("CE", atm + rule.moneyness * step, +1)]
    if s == "long_put":
        return [("PE", atm - rule.moneyness * step, +1)]
    if s == "long_straddle":
        return [("CE", atm, +1), ("PE", atm, +1)]
    if s == "short_straddle":
        return [("CE", atm, -1), ("PE", atm, -1)]
    if s == "short_strangle":
        return [("CE", atm + w, -1), ("PE", atm - w, -1)]
    if s == "iron_fly":
        return [("CE", atm, -1), ("PE", atm, -1), ("CE", atm + w, +1), ("PE", atm - w, +1)]
    raise ValueError(f"unknown structure {s}")


def _book(day: pd.DataFrame) -> tuple[np.ndarray, dict]:
    grid = np.arange(SESSION_OPEN, LAST_BAR + 1)
    spot = day.drop_duplicates("m").set_index("m")["spot"].reindex(grid).ffill().bfill().values
    by = {}
    for (typ, k), g in day.groupby(["option_type", "strike"]):
        s = g.set_index("m")[["open", "close"]].reindex(grid)
        miss = s["close"].isna().values
        cl = s["close"].ffill().values
        op = np.where(np.isnan(s["open"].values), cl, s["open"].values)
        t = str(typ).upper().replace("CALL", "CE").replace("PUT", "PE")
        by[(t, float(k))] = {"op": op, "cl": cl, "miss": miss, "con": g["contract"].iloc[0]}
    return spot, by


SKIP_LABELS = {
    "outside_dte_window": "outside the days-to-expiry window",
    "entry_outside_session": "entry time outside the session",
    "leg_not_traded_at_entry": "a required leg had no trade at the entry minute",
    "no_usable_credit": "structure offered no usable credit",
    "insufficient_capital_for_one_lot": "capital below the margin for one lot",
}


class _Skip:
    def __init__(self, reason):
        self.reason = reason


def _skip(reason):
    return _Skip(reason)


def run_day(day: pd.DataFrame, rule: StructureRule, lot_size: int, costs: CostModel,
            underlying: str = "NIFTY"):
    d = pd.Timestamp(day["timestamp"].iloc[0]).date()
    exp = pd.Timestamp(day["expiry_date"].iloc[0]).date()
    dte = (exp - d).days
    if not (rule.dte_min <= dte <= rule.dte_max):
        return _skip("outside_dte_window")
    step = STEP.get(underlying, 50)
    spot, by = _book(day)
    t = rule.entry_minute - SESSION_OPEN
    i0, last = t + 1, min(rule.squareoff - SESSION_OPEN, len(spot) - 1)
    if t < 0 or i0 >= last:
        return _skip("entry_outside_session")
    atm = round(float(spot[t]) / step) * step
    spec = _legs_for(rule, atm, step)
    legs = []
    for typ, k, sign in spec:
        b = by.get((typ, float(k)))
        if b is None or b["miss"][i0] or not np.isfinite(b["op"][i0]):
            return _skip("leg_not_traded_at_entry")       # every leg must genuinely print at entry
        legs.append((typ, k, sign, b))
    slip = costs.slippage_pts
    # per-unit fill prices: buy at +slip, sell at -slip
    ein = [b["op"][i0] + slip if sign > 0 else b["op"][i0] - slip for _, _, sign, b in legs]
    net_entry = sum(-p if sign > 0 else p for p, (_, _, sign, _) in zip(ein, legs))   # + = credit
    short = net_entry > 0
    width = rule.wing_steps * step if rule.structure == "iron_fly" else None
    max_loss = (width - net_entry) if width else float("inf")
    if rule.structure == "iron_fly" and (net_entry <= 0.5 or max_loss <= 0):
        return _skip("no_usable_credit")

    # value to CLOSE per unit on same-instant closes: what we pay (short) / receive (long)
    val = np.zeros(len(spot))
    for _, _, sign, b in legs:
        val += (-sign) * b["cl"]            # short leg (+) costs to buy back, long leg (-) returns cash
    exit_i, reason = last, "EOD"
    for j in range(i0, last + 1):
        if short:
            loss = val[j] - net_entry
            if rule.stop_x_credit > 0 and loss >= rule.stop_x_credit * net_entry:
                exit_i, reason = j, "SL"; break
            if rule.target_pct > 0 and (net_entry - val[j]) >= rule.target_pct / 100 * net_entry:
                exit_i, reason = j, "TARGET"; break
        else:
            debit = -net_entry
            worth = -val[j]
            if rule.stop_pct > 0 and worth <= debit * (1 - rule.stop_pct / 100):
                exit_i, reason = j, "SL"; break
            if rule.target_pct > 0 and worth >= debit * (1 + rule.target_pct / 100):
                exit_i, reason = j, "TARGET"; break

    spx = float(spot[exit_i])
    exits, stale = [], 0
    for typ, k, sign, b in legs:
        px = b["cl"][exit_i]
        if b["miss"][exit_i]:
            stale += 1
            if rule.stale_adverse:
                intrinsic = max(0.0, spx - k) if typ == "CE" else max(0.0, k - spx)
                px = max(intrinsic, 0.05) if sign > 0 else px * 1.25
        exits.append(px - slip if sign > 0 else px + slip)          # sell longs, buy back shorts
    net_exit = sum(p if sign > 0 else -p for p, (_, _, sign, _) in zip(exits, legs))   # + = cash in
    pnl_unit = net_entry + net_exit
    if width:
        pnl_unit = max(pnl_unit, -max_loss)                          # wings cap the loss

    if rule.lots > 0:
        lots = rule.lots
    elif short:
        lots = int(min(rule.max_lots, rule.capital // max(rule.margin_per_lot, 1)))
    else:
        lots = int(min(rule.max_lots, rule.capital // max(-net_entry * lot_size, 1)))
    if lots <= 0:
        return _skip("insufficient_capital_for_one_lot")
    qty = lots * lot_size
    c = 0.0
    for p_in, p_out, (_, _, sign, _) in zip(ein, exits, legs):
        buy_px, sell_px = (p_in, p_out) if sign > 0 else (p_out, p_in)
        c += costs.round_trip(buy_px, sell_px, qty)
    pnl = pnl_unit * qty - c
    cap = rule.margin_per_lot * lots if short else -net_entry * qty
    leg_txt = " + ".join(f"{'BUY' if s > 0 else 'SELL'} {int(k)} {t}" for t, k, s, _ in legs)
    leg_px = " + ".join(f"{'BUY' if s > 0 else 'SELL'} {int(k)} {t} {pi:.2f}→{po:.2f}"
                        for (t, k, s, _), pi, po in zip(legs, ein, exits))
    t_in, t_out = _hhmm(SESSION_OPEN + i0), _hhmm(SESSION_OPEN + exit_i)
    why = {"SL": "stop-loss hit", "TARGET": "target hit",
           "EOD": f"squared off at {_hhmm(rule.squareoff)}"}.get(reason, reason)
    lot_txt = f"{lots} lot{'s' if lots != 1 else ''} ({qty} qty)"
    if len(legs) == 1:
        typ0, k0, _, _ = legs[0]
        plain = (f"Bought {lot_txt} of {int(k0)} {typ0} at ₹{ein[0]:.2f} at {t_in}, sold at ₹{exits[0]:.2f} "
                 f"at {t_out} — {why}. NIFTY {spot[i0]:.0f} → {spx:.0f}.")
    else:
        opened = ", ".join(f"{'bought' if s > 0 else 'sold'} {int(k)} {t} at ₹{p:.2f}"
                           for (t, k, s, _), p in zip(legs, ein))
        closed = ", ".join(f"{'sold' if s > 0 else 'bought back'} {int(k)} {t} at ₹{p:.2f}"
                           for (t, k, s, _), p in zip(legs, exits))
        plain = (f"{lot_txt} at {t_in}: {opened} (net ₹{abs(net_entry):.2f} per unit "
                 f"{'received' if net_entry > 0 else 'paid'}). At {t_out}: {closed} — {why}. "
                 f"NIFTY {spot[i0]:.0f} → {spx:.0f}.")
    return StructureTrade(
        premium_in=round(abs(float(net_entry)), 2), premium_out=round(abs(float(net_exit)), 2),
        entry_action="Received" if net_entry > 0 else "Paid",
        exit_action="Received" if net_exit > 0 else "Paid",
        leg_prices=leg_px, plain=plain,
        date=str(d), structure=rule.structure, dte=dte,
        decision_time=_hhmm(rule.entry_minute), entry_time=_hhmm(SESSION_OPEN + i0),
        exit_time=_hhmm(SESSION_OPEN + exit_i), spot_entry=round(float(spot[i0]), 2),
        spot_exit=round(spx, 2), legs=leg_txt, net_entry=round(float(net_entry), 2),
        net_exit=round(float(net_exit), 2),
        max_loss_unit=round(float(max_loss), 2) if np.isfinite(max_loss) else -1.0,
        lots=lots, qty=qty, capital_used=round(float(cap), 2), costs_rs=round(float(c), 2),
        pnl_rs=round(float(pnl), 2), ret_on_capital_pct=round(float(pnl / cap * 100), 3) if cap else 0.0,
        exit_reason=reason, stale_legs_at_exit=stale,
        gap_min=int(sum(b["miss"][i0:exit_i + 1].sum() for _, _, _, b in legs)))


def run(options: pd.DataFrame, rule: StructureRule, lot_size: int = 65,
        costs: Optional[CostModel] = None, underlying: str = "NIFTY") -> list[StructureTrade]:
    costs = costs or CostModel()
    o = options.copy()
    ts = pd.to_datetime(o["timestamp"])
    o["date"] = ts.dt.date
    o["m"] = ts.dt.hour * 60 + ts.dt.minute
    out = []
    run.last_skips = {}
    for _, day in o.groupby("date", sort=True):
        tr = run_day(day, rule, lot_size, costs, underlying)
        if isinstance(tr, _Skip):
            run.last_skips[tr.reason] = run.last_skips.get(tr.reason, 0) + 1
        elif tr is not None:
            out.append(tr)
    return out
