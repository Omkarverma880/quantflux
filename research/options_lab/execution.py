"""
Options Lab — execution on REAL option prices.

Turns signals into trades using the actual traded contract, from the Market Store.
Every rule below exists to stop a specific way a backtest lies:

  NO ROLLING SERIES   A signal names a side and a moneyness; the strike is resolved
                      from spot at the decision bar and the contract is fixed BY NAME
                      for the life of the trade. It never switches because "ATM moved".
  NEXT-BAR FILL       Decision on the close of minute t, fill at the OPEN of t+1.
  NO FAKE EXTREMES    If the held contract has no print in a minute (it drifted out of
                      the downloaded strike window) the last traded price is carried and
                      the minute is counted in ``gap_min`` — never interpolated.
  ADVERSE FIRST       Inside one bar the stop is tested before the target.
  GAPS THROUGH STOPS  If a bar OPENS beyond the stop, the fill is that open, not the stop.
  REAL COSTS          Zerodha statutory charges for the actual side, plus slippage.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Iterable, Optional

import numpy as np
import pandas as pd

STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
SESSION_OPEN = 555            # 09:15 in minutes
LAST_BAR = 929                # 15:29


@dataclass
class CostModel:
    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.10          # % of SELL-side premium (0.0625 before Oct-2024)
    exchange_pct: float = 0.03503
    sebi_pct: float = 0.0001
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    slippage_pts: float = 0.5           # per side, premium points

    def round_trip(self, buy_px: float, sell_px: float, qty: int) -> float:
        bv, sv = buy_px * qty, sell_px * qty
        brok = 2 * self.brokerage_per_order
        exch = self.exchange_pct / 100 * (bv + sv)
        sebi = self.sebi_pct / 100 * (bv + sv)
        return (brok + self.stt_sell_pct / 100 * sv + exch + sebi
                + self.stamp_buy_pct / 100 * bv + self.gst_pct / 100 * (brok + exch + sebi))


@dataclass
class ExitRule:
    target_pct: float = 100.0           # % move in the position's favour; 0 = none
    stop_pct: float = 10.0              # % move against; 0 = none
    max_hold_min: int = 30              # 0 = until square-off
    squareoff: int = 915                # 15:15
    trail_after_pct: float = 0.0        # arm a trailing stop once up this much (0 = off)
    trail_giveback_pct: float = 0.0     # … and exit if it gives back this much of the peak


@dataclass
class Signal:
    date: date
    minute: int                          # decision bar, minutes from midnight
    side: str                            # CE | PE
    action: str = "BUY"                  # BUY | SELL
    moneyness: int = 0                   # strike steps: -1 ITM, 0 ATM, +1 OTM
    lots: int = 1
    tag: str = ""
    score: float = float("nan")


@dataclass
class Trade:
    date: str
    tag: str
    side: str
    action: str
    contract: str
    strike: float
    expiry: str
    dte: int
    decision_time: str
    entry_time: str
    exit_time: str
    spot_entry: float
    spot_exit: float
    entry: float
    exit: float
    lots: int
    qty: int
    gross_pts: float
    costs_rs: float
    pnl_rs: float
    ret_pct: float
    mfe_pct: float
    mae_pct: float
    held_min: int
    exit_reason: str
    gap_min: int
    score: float

    def as_dict(self) -> dict:
        return asdict(self)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


class DayBook:
    """All contracts for one session, reindexed to a full minute grid."""

    def __init__(self, day_options: pd.DataFrame, day_spot: Optional[pd.DataFrame] = None):
        o = day_options
        self.grid = np.arange(SESSION_OPEN, LAST_BAR + 1)
        self.spot = None
        if day_spot is not None and len(day_spot):
            s = day_spot.set_index("m")["close"]
            self.spot = s.reindex(self.grid).ffill().bfill().values
        elif "spot" in o.columns:
            s = o.drop_duplicates("m").set_index("m")["spot"]
            self.spot = s.reindex(self.grid).ffill().bfill().values
        self.by_key: dict = {}
        self.by_key_expiry: dict = {}          # (type, strike, expiry) -> contract
        self.expiries: list = []
        self.meta: dict = {}
        has_vol = "volume" in o.columns
        has_oi = "oi" in o.columns
        for con, g in o.groupby("contract"):
            s = g.set_index("m")[["open", "high", "low", "close"]].reindex(self.grid)
            miss = s["close"].isna().values
            cl = s["close"].ffill().values
            op, hi, lo = s["open"].values.copy(), s["high"].values.copy(), s["low"].values.copy()
            for a in (op, hi, lo):
                a[np.isnan(a)] = cl[np.isnan(a)]
            r = g.iloc[0]
            typ = str(r["option_type"]).upper().replace("CALL", "CE").replace("PUT", "PE")
            expiry = str(r["expiry_date"])[:10]
            key = (typ, float(r["strike"]))
            self.by_key[key] = con
            self.by_key_expiry[(typ, float(r["strike"]), expiry)] = con
            meta = {"op": op, "hi": hi, "lo": lo, "cl": cl, "miss": miss,
                    "strike": float(r["strike"]), "expiry": expiry, "type": typ}
            # liquidity, when the stored rows carry it — used by the Flux Lab's filters
            if has_vol:
                meta["vol"] = g.set_index("m")["volume"].reindex(self.grid).fillna(0).values
            if has_oi:
                meta["oi"] = g.set_index("m")["oi"].reindex(self.grid).ffill().fillna(0).values
            self.meta[con] = meta
        self.expiries = sorted({m["expiry"] for m in self.meta.values()})

    def idx(self, minute: int) -> int:
        return int(minute - SESSION_OPEN)


def resolve_contract(book: DayBook, sig: Signal, underlying: str = "NIFTY",
                     expiry: Optional[str] = None, at_minute: Optional[int] = None) -> Optional[str]:
    """The contract this signal buys.

    ``expiry`` pins the series (the Flux Lab chooses it per its expiry rule); without it the
    behaviour is unchanged — whichever expiry the day's data holds for that strike.
    ``at_minute`` lets the caller price the ATM off the fill bar rather than the decision bar.
    """
    step = STEP.get(underlying, 50)
    t = book.idx(at_minute if at_minute is not None else sig.minute)
    if book.spot is None or t < 0 or t >= len(book.grid):
        return None
    atm = round(float(book.spot[t]) / step) * step
    sgn = 1 if sig.side == "CE" else -1
    strike = atm + sgn * sig.moneyness * step
    if expiry:
        return book.by_key_expiry.get((sig.side, float(strike), str(expiry)[:10]))
    return book.by_key.get((sig.side, float(strike)))


def execute(book: DayBook, sig: Signal, rule: ExitRule, lot_size: int,
            costs: CostModel, underlying: str = "NIFTY") -> Optional[Trade]:
    con = resolve_contract(book, sig, underlying)
    if con is None:
        return None
    M = book.meta[con]
    i0 = book.idx(sig.minute) + 1                   # fill on the next bar
    last = min(book.idx(rule.squareoff), len(book.grid) - 1)
    if i0 > last or M["miss"][i0] or not np.isfinite(M["op"][i0]) or M["op"][i0] <= 0.05:
        return None
    op, hi, lo, cl = M["op"], M["hi"], M["lo"], M["cl"]
    long = sig.action == "BUY"
    slip = costs.slippage_pts
    entry = op[i0] + slip if long else op[i0] - slip
    if entry <= 0:
        return None
    end = last if rule.max_hold_min <= 0 else min(i0 + rule.max_hold_min, last)
    tgt = rule.target_pct / 100.0
    stp = rule.stop_pct / 100.0

    def favour(px):                                 # signed % move for the position
        return (px / entry - 1.0) if long else (1.0 - px / entry)

    exit_px, exit_i, reason = None, end, "TIME" if rule.max_hold_min > 0 else "EOD"
    mfe = mae = 0.0
    peak = 0.0
    # Levels are compared in PRICE with a small tolerance: a bar that prints exactly
    # at the stop has touched it. Comparing float ratios silently misses that tie.
    eps = 1e-6
    stop_px_lvl = entry * (1 - stp) if long else entry * (1 + stp)
    tgt_px_lvl = entry * (1 + tgt) if long else entry * (1 - tgt)
    for j in range(i0, end + 1):
        worst = lo[j] if long else hi[j]
        best = hi[j] if long else lo[j]
        mae = min(mae, favour(worst)); mfe = max(mfe, favour(best))
        if stp > 0 and ((long and worst <= stop_px_lvl + eps) or (not long and worst >= stop_px_lvl - eps)):
            stop_px = stop_px_lvl
            if j > i0 and ((long and op[j] < stop_px_lvl) or (not long and op[j] > stop_px_lvl)):
                stop_px = op[j]                     # opened through the stop
            exit_px, exit_i, reason = stop_px, j, "SL"
            break
        if tgt > 0 and ((long and best >= tgt_px_lvl - eps) or (not long and best <= tgt_px_lvl + eps)):
            tp_px = tgt_px_lvl
            if j > i0 and ((long and op[j] > tgt_px_lvl) or (not long and op[j] < tgt_px_lvl)):
                tp_px = op[j]                       # opened through the target
            exit_px, exit_i, reason = tp_px, j, "TARGET"
            break
        if rule.trail_after_pct > 0:
            peak = max(peak, favour(best))
            if peak >= rule.trail_after_pct / 100 and favour(cl[j]) <= peak - rule.trail_giveback_pct / 100:
                exit_px, exit_i, reason = cl[j], j, "TRAIL"
                break
    if exit_px is None:
        exit_px = cl[exit_i]
    exit_px = exit_px - slip if long else exit_px + slip
    exit_px = max(exit_px, 0.05)
    qty = sig.lots * lot_size
    gross_pts = (exit_px - entry) if long else (entry - exit_px)
    buy_px, sell_px = (entry, exit_px) if long else (exit_px, entry)
    c = costs.round_trip(buy_px, sell_px, qty)
    pnl = gross_pts * qty - c
    d = pd.Timestamp(sig.date).date()
    exp = pd.Timestamp(M["expiry"]).date()
    return Trade(
        date=str(d), tag=sig.tag, side=sig.side, action=sig.action, contract=con,
        strike=M["strike"], expiry=str(exp), dte=(exp - d).days,
        decision_time=_hhmm(sig.minute), entry_time=_hhmm(SESSION_OPEN + i0),
        exit_time=_hhmm(SESSION_OPEN + exit_i),
        spot_entry=float(book.spot[i0]) if book.spot is not None else float("nan"),
        spot_exit=float(book.spot[exit_i]) if book.spot is not None else float("nan"),
        entry=round(float(entry), 2), exit=round(float(exit_px), 2), lots=sig.lots, qty=qty,
        gross_pts=round(float(gross_pts), 2), costs_rs=round(float(c), 2),
        pnl_rs=round(float(pnl), 2), ret_pct=round(float(pnl / (entry * qty) * 100), 3),
        mfe_pct=round(mfe * 100, 2), mae_pct=round(mae * 100, 2),
        held_min=int(exit_i - i0), exit_reason=reason,
        gap_min=int(M["miss"][i0:exit_i + 1].sum()), score=float(sig.score))


def run_signals(options: pd.DataFrame, spot: Optional[pd.DataFrame], signals: Iterable[Signal],
                rule: ExitRule, lot_size: int = 65, costs: Optional[CostModel] = None,
                underlying: str = "NIFTY", one_position_at_a_time: bool = True) -> list[Trade]:
    """Execute signals day by day. With ``one_position_at_a_time`` a signal that
    arrives while a trade is still open is skipped — no stacked exposure."""
    costs = costs or CostModel()
    sigs = sorted(signals, key=lambda s: (pd.Timestamp(s.date), s.minute))
    if not sigs:
        return []
    o = options.copy()
    o["date"] = pd.to_datetime(o["timestamp"]).dt.date
    o["m"] = (pd.to_datetime(o["timestamp"]).dt.hour * 60 + pd.to_datetime(o["timestamp"]).dt.minute)
    s = None
    if spot is not None and len(spot):
        s = spot.copy()
        s["date"] = pd.to_datetime(s["timestamp"]).dt.date
        s["m"] = pd.to_datetime(s["timestamp"]).dt.hour * 60 + pd.to_datetime(s["timestamp"]).dt.minute
    by_day_o = dict(tuple(o.groupby("date")))
    by_day_s = dict(tuple(s.groupby("date"))) if s is not None else {}
    trades: list[Trade] = []
    cur_day, book, busy_until = None, None, -1
    for sg in sigs:
        d = pd.Timestamp(sg.date).date()
        if d != cur_day:
            cur_day = d
            busy_until = -1
            if d not in by_day_o:
                book = None
                continue
            book = DayBook(by_day_o[d], by_day_s.get(d))
        if book is None:
            continue
        if one_position_at_a_time and sg.minute <= busy_until:
            continue
        t = execute(book, sg, rule, lot_size, costs, underlying)
        if t is not None:
            trades.append(t)
            h, mm = map(int, t.exit_time.split(":"))
            busy_until = h * 60 + mm
    return trades
