"""
Flux Lab — backtest of the combined hammer strategy on stored NIFTY data.

The signal is found on the index candle; the trade is taken in the option chain, because that is
what you would actually trade. Both numbers are reported and never mixed: index points measure
the move, rupees measure the trade.

The same signal can be expressed two ways. BUY takes the option the signal points at. SELL takes
the opposite option instead and collects its premium — a bullish hammer sells puts rather than
buying calls — which wins when the move merely fails to go against you rather than needing it to
arrive. With a hedge offset the short leg is paired with a further-OTM long, turning it into a
credit spread whose loss stops at the width of the strikes.

Honesty rules, the same ones the rest of the lab follows:
  * a bar is stamped with its START, so its close is only known on its LAST minute. The decision is
    placed there and the fill comes from the NEXT minute's real option open — never earlier.
  * every leg must have actually traded just before the fill, otherwise the signal is recorded as
    skipped and never priced from nothing
  * exits are decided on one-minute closes and filled at the next minute's open
  * Zerodha's own charges through the shared CostModel, plus slippage on every fill
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.flux_lab import patterns as PT
from research.market_store import store as MS
from research.options_lab.execution import CostModel

logger = get_logger("research.flux_lab.engine")

LOT = 65
STEP = 50
SESSION_OPEN = 9 * 60 + 15
COST = CostModel(slippage_pts=0.0)


class Execution:
    """How a signal becomes a trade."""

    def __init__(self, target_pct: float = 25.0, stop_pct: float = 12.0, max_hold_min: int = 60,
                 squareoff: int = 15 * 60 + 15, first_entry: int = 9 * 60 + 20,
                 last_entry: int = 14 * 60 + 45, slippage_pts: float = 0.5,
                 max_trades_per_day: int = 3, moneyness: int = 0, lots: int = 1,
                 action: str = "BUY", hedge_offset: int = 0,
                 min_dte: int = 0, max_dte: int = 0):
        self.target_pct, self.stop_pct, self.max_hold_min = target_pct, stop_pct, max_hold_min
        self.squareoff, self.first_entry, self.last_entry = squareoff, first_entry, last_entry
        self.slippage_pts, self.max_trades_per_day = slippage_pts, max_trades_per_day
        self.moneyness, self.lots = moneyness, lots
        # BUY  — the signal's own direction, bought outright
        # SELL — the opposite option sold, with hedge_offset strikes of protection bought
        #        alongside it (0 = naked, which has no defined loss)
        self.action = "SELL" if str(action).upper().startswith("S") else "BUY"
        self.hedge_offset = max(0, int(hedge_offset))
        # Days to expiry the trade is allowed to sit in. Expiry-day options decay fastest, but
        # they also move fastest against a seller, and the two do not cancel out — so this is a
        # filter rather than an assumption either way. 0 on max_dte means no upper bound.
        self.min_dte, self.max_dte = max(0, int(min_dte)), max(0, int(max_dte))

    @property
    def is_short(self) -> bool:
        return self.action == "SELL"

    def as_dict(self) -> dict:
        return dict(target_pct=self.target_pct, stop_pct=self.stop_pct, max_hold_min=self.max_hold_min,
                    squareoff=self.squareoff, first_entry=self.first_entry, last_entry=self.last_entry,
                    slippage_pts=self.slippage_pts, max_trades_per_day=self.max_trades_per_day,
                    moneyness=self.moneyness, lots=self.lots,
                    action=self.action, hedge_offset=self.hedge_offset,
                    min_dte=self.min_dte, max_dte=self.max_dte)


def hhmm(m: int) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def resample(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """One-minute bars folded into `minutes` candles, each stamped with the minute it starts."""
    if minutes <= 1:
        d = bars.copy()
        d["minute"] = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
        return d.reset_index(drop=True)
    out = []
    for day, g in bars.groupby(bars["timestamp"].dt.date, sort=True):
        g = g.sort_values("timestamp")
        mins = g["timestamp"].dt.hour * 60 + g["timestamp"].dt.minute
        bucket = SESSION_OPEN + ((mins - SESSION_OPEN) // minutes) * minutes
        agg = g.assign(bucket=bucket).groupby("bucket").agg(
            timestamp=("timestamp", "first"), open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).reset_index()
        agg["minute"] = agg["bucket"].astype(int)
        agg["date"] = day
        out.append(agg.drop(columns=["bucket"]))
    return pd.concat(out, ignore_index=True) if out else bars.iloc[0:0]


def _months(start: str, end: str) -> list[tuple[str, str]]:
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    out, cur = [], pd.Timestamp(a.year, a.month, 1)
    while cur <= b:
        nxt = cur + pd.offsets.MonthBegin(1)
        out.append((str(max(cur, a).date()), str(min(nxt - pd.Timedelta(days=1), b).date())))
        cur = nxt
    return out


def _load_month(start: str, end: str) -> tuple[pd.DataFrame, dict]:
    """Index bars for the month, and per-session option price grids for the nearest expiry."""
    bars = MS.read("spot", "NIFTY", start=start, end=end,
                   columns=["timestamp", "open", "high", "low", "close", "volume"])
    opts = MS.read("options", "NIFTY", start=start, end=end,
                   columns=["timestamp", "expiry_date", "strike", "option_type", "open", "close"])
    if bars.empty or opts.empty:
        return pd.DataFrame(), {}
    bars["timestamp"] = pd.to_datetime(bars["timestamp"])
    bars = bars.sort_values("timestamp").reset_index(drop=True)
    ts = pd.to_datetime(opts["timestamp"])
    opts = opts.assign(date=ts.dt.date, m=(ts.dt.hour * 60 + ts.dt.minute).astype(int),
                       expiry_date=pd.to_datetime(opts["expiry_date"]).dt.date)
    opts = opts[opts["expiry_date"] >= opts["date"]]
    opts = opts[opts["expiry_date"] == opts.groupby("date")["expiry_date"].transform("min")]
    grid = np.arange(SESSION_OPEN, 15 * 60 + 30)
    days = {}
    for d, g in opts.groupby("date"):
        op = g.pivot_table(index="m", columns=["option_type", "strike"], values="open", aggfunc="last").reindex(grid)
        cl = g.pivot_table(index="m", columns=["option_type", "strike"], values="close", aggfunc="last").reindex(grid)
        printed = cl.notna()
        cl = cl.ffill()
        op = op.where(printed).fillna(cl)
        days[d] = {"op": op, "cl": cl, "printed": printed, "expiry": g["expiry_date"].iloc[0]}
    return bars, days


def _col(frame: pd.DataFrame, typ: str, strike: float):
    for k in ((typ, float(strike)), (typ, int(strike)), (typ, strike)):
        if k in frame.columns:
            return k
    return None


def trade_option(day: dict, d: date, sig: dict, decision_min: int, ex: Execution) -> dict:
    """Buy the option the signal implies, then manage it to target, stop, time or the close."""
    typ = "CE" if sig["side"] == PT.BULLISH else "PE"
    spot = sig["close"]
    atm = round(spot / STEP) * STEP
    strike = atm + (1 if typ == "CE" else -1) * ex.moneyness * STEP
    col = _col(day["cl"], typ, strike)
    if col is None:
        return {"skipped": f"{typ} {strike:g} is not in the stored chain"}
    fill = decision_min + 1
    pr, op, cl = day["printed"], day["op"], day["cl"]
    if fill not in pr.index or not pr[col].loc[max(SESSION_OPEN, fill - 6):fill].any():
        return {"skipped": "that option had not traded in the minutes before the fill"}
    entry = float(op.at[fill, col])
    if not np.isfinite(entry) or entry <= 0:
        return {"skipped": "no usable option price at the fill minute"}
    entry += ex.slippage_pts

    last = min(ex.squareoff, int(pr.index[-1]))
    end = min(last, fill + ex.max_hold_min) if ex.max_hold_min > 0 else last
    target = entry * (1 + ex.target_pct / 100) if ex.target_pct else None
    stop = entry * (1 - ex.stop_pct / 100) if ex.stop_pct else None
    reason, exit_min, exit_px = "TIME", end, None
    for m in range(fill, end):
        px = float(cl.at[m, col])
        if not np.isfinite(px):
            continue
        if stop and px <= stop:
            reason, exit_min = "STOP", m + 1
            break
        if target and px >= target:
            reason, exit_min = "TARGET", m + 1
            break
    if exit_min >= min(ex.squareoff, end) and reason == "TIME":
        reason = "EOD" if end >= ex.squareoff else "TIME"
    exit_px = float(op.at[exit_min, col]) if exit_min in op.index and np.isfinite(op.at[exit_min, col]) \
        else float(cl.at[min(exit_min, end), col])
    exit_px -= ex.slippage_pts
    qty = LOT * ex.lots
    gross = (exit_px - entry) * qty
    charges = COST.round_trip(entry, exit_px, qty)
    return {
        "action": "BUY",
        "type": typ, "strike": float(strike), "expiry": str(day["expiry"]),
        "dte": (day["expiry"] - d).days, "qty": qty, "lots": ex.lots,
        "entry_time": hhmm(fill), "exit_time": hhmm(exit_min), "exit_reason": reason,
        "option_entry": round(entry, 2), "option_exit": round(exit_px, 2),
        "held_min": int(exit_min - fill), "gross": round(gross, 2), "charges": round(charges, 2),
        "pnl": round(gross - charges, 2),
    }


def trade_short(day: dict, d: date, sig: dict, decision_min: int, ex: Execution) -> dict:
    """Sell the option the signal argues against, optionally hedged, and manage the credit.

    A buyer of options needs the move to arrive, and arrive quickly. A seller needs it merely not
    to arrive, which is a different bet on the same read — so a bullish hammer sells puts rather
    than buying calls, and a bearish star sells calls.

    The direction of every rule flips with the position:
      entry   the premium is RECEIVED, so slippage reduces it
      target  the credit DECAYING to a fraction of itself is the profit
      stop    the credit EXPANDING past a multiple of itself is the loss
      exit    buying it back COSTS, so slippage increases it

    With ``hedge_offset`` set, a further-OTM option of the same type is bought alongside. That
    makes it a credit spread: the premium is smaller, but the loss stops at the width of the
    strikes instead of running with the index. Target and stop are then measured on the NET
    credit, because the spread is one position and managing the short leg alone would unwind
    the protection at exactly the wrong moment.
    """
    # the signal says which way the index should NOT go, and that is the side we sell
    typ = "PE" if sig["side"] == PT.BULLISH else "CE"
    spot = sig["close"]
    atm = round(spot / STEP) * STEP
    away = 1 if typ == "CE" else -1          # OTM is above for a call, below for a put
    short_strike = atm + away * ex.moneyness * STEP
    hedge_strike = short_strike + away * ex.hedge_offset * STEP if ex.hedge_offset else None

    short_col = _col(day["cl"], typ, short_strike)
    if short_col is None:
        return {"skipped": f"{typ} {short_strike:g} is not in the stored chain"}
    hedge_col = None
    if hedge_strike is not None:
        hedge_col = _col(day["cl"], typ, hedge_strike)
        if hedge_col is None:
            return {"skipped": f"hedge {typ} {hedge_strike:g} is not in the stored chain"}

    fill = decision_min + 1
    pr, op, cl = day["printed"], day["op"], day["cl"]
    if fill not in pr.index:
        return {"skipped": "no minute to fill against"}
    for c, what in ((short_col, "the option sold"), (hedge_col, "the hedge")):
        if c is None:
            continue
        if not pr[c].loc[max(SESSION_OPEN, fill - 6):fill].any():
            return {"skipped": f"{what} had not traded in the minutes before the fill"}

    short_in = float(op.at[fill, short_col])
    if not np.isfinite(short_in) or short_in <= 0:
        return {"skipped": "no usable option price at the fill minute"}
    # premium received, so slippage takes some of it away
    short_in -= ex.slippage_pts
    hedge_in = 0.0
    if hedge_col is not None:
        hedge_in = float(op.at[fill, hedge_col])
        if not np.isfinite(hedge_in) or hedge_in <= 0:
            return {"skipped": "no usable hedge price at the fill minute"}
        hedge_in += ex.slippage_pts       # the hedge is bought, so slippage adds to it
    credit = short_in - hedge_in
    if credit <= 0:
        return {"skipped": "the hedge costs more than the option sold — no credit to collect"}

    last = min(ex.squareoff, int(pr.index[-1]))
    end = min(last, fill + ex.max_hold_min) if ex.max_hold_min > 0 else last
    # the credit shrinking is the win; the credit growing is the loss
    target = credit * (1 - ex.target_pct / 100) if ex.target_pct else None
    stop = credit * (1 + ex.stop_pct / 100) if ex.stop_pct else None

    def value_at(m: int) -> Optional[float]:
        s_ = float(cl.at[m, short_col])
        if not np.isfinite(s_):
            return None
        if hedge_col is None:
            return s_
        h_ = float(cl.at[m, hedge_col])
        return s_ - (h_ if np.isfinite(h_) else 0.0)

    reason, exit_min = "TIME", end
    for m in range(fill, end):
        v = value_at(m)
        if v is None:
            continue
        # adverse first: a bar that touched both the stop and the target is a loss, because the
        # stop is what a live position would have hit without asking
        if stop is not None and v >= stop:
            reason, exit_min = "STOP", m + 1
            break
        if target is not None and v <= target:
            reason, exit_min = "TARGET", m + 1
            break
    if exit_min >= min(ex.squareoff, end) and reason == "TIME":
        reason = "EOD" if end >= ex.squareoff else "TIME"

    def px_out(c) -> float:
        v = float(op.at[exit_min, c]) if exit_min in op.index and np.isfinite(op.at[exit_min, c]) \
            else float(cl.at[min(exit_min, end), c])
        return v

    short_out = px_out(short_col) + ex.slippage_pts      # bought back, so slippage costs
    hedge_out = 0.0
    if hedge_col is not None:
        hedge_out = max(0.0, px_out(hedge_col) - ex.slippage_pts)   # sold back

    qty = LOT * ex.lots
    # short leg: sold at short_in, bought back at short_out
    gross = (short_in - short_out) * qty
    charges = COST.round_trip(buy_px=short_out, sell_px=short_in, qty=qty)
    if hedge_col is not None:
        gross += (hedge_out - hedge_in) * qty
        charges += COST.round_trip(buy_px=hedge_in, sell_px=hedge_out, qty=qty)

    width = abs(float(hedge_strike) - float(short_strike)) if hedge_strike is not None else None
    out = {
        "action": "SELL", "type": typ, "strike": float(short_strike),
        "expiry": str(day["expiry"]), "dte": (day["expiry"] - d).days,
        "qty": qty, "lots": ex.lots,
        "entry_time": hhmm(fill), "exit_time": hhmm(exit_min), "exit_reason": reason,
        "option_entry": round(short_in, 2), "option_exit": round(short_out, 2),
        "credit": round(credit, 2), "credit_rs": round(credit * qty, 2),
        "held_min": int(exit_min - fill),
        "gross": round(gross, 2), "charges": round(charges, 2),
        "pnl": round(gross - charges, 2),
    }
    if hedge_strike is not None:
        out.update({
            "hedge_strike": float(hedge_strike), "hedge_entry": round(hedge_in, 2),
            "hedge_exit": round(hedge_out, 2), "spread_width": width,
            # what the position can lose if the index runs straight through both strikes
            "max_loss": round(width * qty - credit * qty, 2),
            "margin": round(width * qty - credit * qty, 2),
        })
    return out


def run(start: str, end: str, timeframe: int = 5, params: PT.Params = PT.P,
        ex: Optional[Execution] = None, progress: Optional[Callable[[str], None]] = None) -> dict:
    """Walk the range month by month and trade every signal the rules produce."""
    say = progress or (lambda _m: None)
    ex = ex or Execution()
    trades, skipped, signals = [], [], 0
    sessions = 0
    for a, b in _months(start, end):
        say(f"{a[:7]} · {len(trades)} trades so far")
        bars, days = _load_month(a, b)
        if bars.empty:
            continue
        frame = PT.prepare(resample(bars, timeframe), params)
        frame["date"] = pd.to_datetime(frame["timestamp"]).dt.date
        for d, g in frame.groupby("date", sort=True):
            day = days.get(d)
            sessions += 1
            if day is None:
                continue
            dte = (day["expiry"] - d).days
            if dte < ex.min_dte or (ex.max_dte and dte > ex.max_dte):
                continue
            taken, busy_until = 0, -1
            for row in g.itertuples():
                # the candle's close is only known on its last minute
                decision = int(row.minute) + timeframe - 1
                if decision < ex.first_entry or decision > ex.last_entry:
                    continue
                sig = PT.signal_on(pd.Series(row._asdict()), params)
                if not sig:
                    continue
                signals += 1
                if taken >= ex.max_trades_per_day:
                    skipped.append({"date": str(d), "time": hhmm(decision), "why": "daily trade limit"})
                    continue
                if decision <= busy_until:
                    skipped.append({"date": str(d), "time": hhmm(decision), "why": "a position was still open"})
                    continue
                res = (trade_short if ex.is_short else trade_option)(day, d, sig, decision, ex)
                if "skipped" in res:
                    skipped.append({"date": str(d), "time": hhmm(decision), "why": res["skipped"]})
                    continue
                spot_exit = float(g[g["minute"] <= int(res["exit_time"][:2]) * 60 + int(res["exit_time"][3:])]
                                  ["close"].iloc[-1]) if len(g) else sig["close"]
                move = spot_exit - sig["close"]
                trades.append({
                    "date": str(d), "month": str(d)[:7], "signal_time": hhmm(decision),
                    "pattern": sig["pattern"], "side": sig["side"],
                    "rejection_ok": sig["rejection_ok"], "shape_ok": sig["shape_ok"],
                    "checks": sig["checks"], "candle": {k: sig[k] for k in ("open", "high", "low", "close")},
                    "spot_entry": sig["close"], "spot_exit": round(spot_exit, 2),
                    "spot_move_pts": round(move if sig["side"] == PT.BULLISH else -move, 2),
                    **res})
                taken += 1
                busy_until = int(res["exit_time"][:2]) * 60 + int(res["exit_time"][3:])
    say(f"done · {len(trades)} trades from {signals} signals")
    return {"status": "ok", "trades": trades, "skipped": skipped, "signals": signals,
            "sessions": sessions, "summary": summarise(trades, signals, skipped, sessions),
            "params": params.as_dict(), "execution": ex.as_dict(), "timeframe": timeframe,
            "start": start, "end": end}


def summarise(trades: list[dict], signals: int, skipped: list[dict], sessions: int) -> dict:
    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades": 0, "signals": signals, "sessions": sessions,
                "message": "no trades — the rules produced no tradable signal in this range"}
    w, l = t.pnl[t.pnl > 0], t.pnl[t.pnl <= 0]
    months = t.groupby("month").agg(trades=("pnl", "size"), net=("pnl", "sum"),
                                    wins=("pnl", lambda s: int((s > 0).sum())))
    by_side = t.groupby("pattern").agg(trades=("pnl", "size"), net=("pnl", "sum"),
                                       win_rate=("pnl", lambda s: round(float((s > 0).mean() * 100), 1)))
    eq = t.pnl.cumsum()
    return {
        "trades": int(len(t)), "signals": signals, "sessions": sessions,
        "skipped": len(skipped),
        "win_rate": round(float((t.pnl > 0).mean() * 100), 1),
        "avg_win": round(float(w.mean()), 2) if len(w) else 0.0,
        "avg_loss": round(float(l.mean()), 2) if len(l) else 0.0,
        "profit_factor": round(float(w.sum() / -l.sum()), 2) if l.sum() < 0 else None,
        "net": round(float(t.pnl.sum()), 2), "charges": round(float(t.charges.sum()), 2),
        "avg_trade": round(float(t.pnl.mean()), 2), "best": round(float(t.pnl.max()), 2),
        "worst": round(float(t.pnl.min()), 2),
        "max_drawdown": round(float((eq - eq.cummax()).min()), 2),
        "avg_hold_min": int(t.held_min.mean()),
        "avg_index_move": round(float(t.spot_move_pts.mean()), 2),
        "exits": {k: int(v) for k, v in t.exit_reason.value_counts().items()},
        "months": len(months), "green_months": int((months.net > 0).sum()),
        "avg_month": round(float(months.net.mean()), 2),
        "worst_month": round(float(months.net.min()), 2), "best_month": round(float(months.net.max()), 2),
        "monthly": [{"month": m, "trades": int(r.trades), "wins": int(r.wins), "net": round(float(r.net), 2)}
                    for m, r in months.iterrows()],
        "by_pattern": [{"pattern": p, "trades": int(r.trades), "net": round(float(r.net), 2),
                        "win_rate": float(r.win_rate)} for p, r in by_side.iterrows()],
        "skip_reasons": pd.Series([s["why"] for s in skipped]).value_counts().head(6).to_dict() if skipped else {},
    }
