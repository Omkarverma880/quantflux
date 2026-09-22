"""
Flux Lab — the one strategy: sell a hedged iron fly after a failed opening-range breakout.

This file is the ONLY place the rule lives. The backtest (``backtest.py``) and the live paper
engine (``live.py``) both call these functions, so what was measured is what paper-trades.

The rule, frozen from the research (OR · 15-pt break · 15-min window · 200-pt wings · 60/60):

  1. Opening range = highest and lowest NIFTY 1-minute close from 09:15 to 09:29.
  2. From 09:30 to 14:00, watch every completed 1-minute close.
     A close more than 15 pts above the range high (or below the range low) is a breakout.
  3. If NIFTY closes back inside the range within 15 minutes of that breakout, the breakout
     FAILED — the day is more likely to stay in a range. That minute is the signal.
     (A breakout that holds longer than 15 minutes is a real breakout: no trade, keep watching.)
  4. On the next minute, sell the ATM call and ATM put, and buy the call and put 200 pts away
     (an iron fly — the loss is capped by the bought wings). Nearest weekly expiry.
  5. Exit when 60% of the credit is captured (TARGET), when the position is losing 60% of the
     credit (STOP), or at 15:15 (EOD). One trade per day at most.

Every function here uses only information available at the minute it is evaluated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Optional

STRATEGY_NAME = "Failed opening-range breakout → iron fly"
ENGINE_VERSION = "fly-1"


@dataclass(frozen=True)
class Rule:
    or_start: int = 9 * 60 + 15        # opening range: closes from 09:15 …
    or_end: int = 9 * 60 + 29          # … to 09:29
    first_check: int = 9 * 60 + 30     # watching starts once the range is complete
    last_signal: int = 14 * 60         # no new signal after 14:00
    break_pts: float = 15.0            # a close this far beyond the range = breakout
    fail_window: int = 15              # back inside within this many minutes = failed
    wing_pts: int = 200                # hedge distance from the sold ATM strikes
    target_pct: float = 60.0           # % of credit captured → exit
    stop_pct: float = 60.0             # % of credit lost → exit
    squareoff: int = 15 * 60 + 15      # 15:15
    step: int = 50                     # NIFTY strike step

    def as_dict(self) -> dict:
        return asdict(self)


RULE = Rule()


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def opening_range(closes: Mapping[int, float], rule: Rule = RULE) -> Optional[tuple[float, float]]:
    """(high, low) of the 09:15–09:29 closes, or None until at least 10 of them exist."""
    vals = [closes[m] for m in range(rule.or_start, rule.or_end + 1)
            if m in closes and closes[m] == closes[m]]
    if len(vals) < 10:
        return None
    return max(vals), min(vals)


def scan(closes: Mapping[int, float], rule: Rule = RULE, upto: Optional[int] = None) -> dict:
    """Walk the day's completed closes and report where the rule stands.

    Returns ``{"state": ..., "signal": {...} | None, "or": (hi, lo) | None, "events": [...]}``.
    ``upto`` is the last COMPLETED minute; nothing after it is read.
    """
    rng = opening_range(closes, rule)
    if rng is None:
        return {"state": "waiting for the opening range (complete at 09:30)", "signal": None,
                "or": None, "events": []}
    hi, lo = rng
    last = min(rule.last_signal, upto if upto is not None else rule.last_signal)
    up = dn = None                       # [break minute, extreme close]
    events: list[dict] = []
    for m in range(rule.first_check, last + 1):
        s = closes.get(m)
        if s is None or s != s:
            continue
        if up is None and s > hi + rule.break_pts:
            up = [m, s]
            events.append({"time": hhmm(m), "text": f"broke above {hi:.2f} (close {s:.2f})"})
        elif up is not None:
            up[1] = max(up[1], s)
            if s < hi:
                if m - up[0] <= rule.fail_window:
                    sig = {"minute": m, "time": hhmm(m), "direction": "failed up-break",
                           "or_high": hi, "or_low": lo, "break_time": hhmm(up[0]),
                           "extreme": up[1], "spot": s}
                    events.append({"time": hhmm(m), "text": f"back below {hi:.2f} within "
                                   f"{m - up[0]} min — breakout FAILED → signal"})
                    return {"state": "signal", "signal": sig, "or": rng, "events": events}
                events.append({"time": hhmm(m), "text": f"came back only after {m - up[0]} min — "
                               "the breakout held too long, watching again"})
                up = None
        if dn is None and s < lo - rule.break_pts:
            dn = [m, s]
            events.append({"time": hhmm(m), "text": f"broke below {lo:.2f} (close {s:.2f})"})
        elif dn is not None:
            dn[1] = min(dn[1], s)
            if s > lo:
                if m - dn[0] <= rule.fail_window:
                    sig = {"minute": m, "time": hhmm(m), "direction": "failed down-break",
                           "or_high": hi, "or_low": lo, "break_time": hhmm(dn[0]),
                           "extreme": dn[1], "spot": s}
                    events.append({"time": hhmm(m), "text": f"back above {lo:.2f} within "
                                   f"{m - dn[0]} min — breakout FAILED → signal"})
                    return {"state": "signal", "signal": sig, "or": rng, "events": events}
                events.append({"time": hhmm(m), "text": f"came back only after {m - dn[0]} min — "
                               "the breakout held too long, watching again"})
                dn = None
    if upto is not None and upto >= rule.last_signal:
        state = "no failed breakout by 14:00 — no trade today"
    elif up is not None:
        state = (f"above the range since {hhmm(up[0])} — a close back below {hi:.2f} by "
                 f"{hhmm(up[0] + rule.fail_window)} would be the signal")
    elif dn is not None:
        state = (f"below the range since {hhmm(dn[0])} — a close back above {lo:.2f} by "
                 f"{hhmm(dn[0] + rule.fail_window)} would be the signal")
    else:
        state = f"watching — range {lo:.2f} – {hi:.2f}, breakout needs a close beyond ±{rule.break_pts:g} pts"
    return {"state": state, "signal": None, "or": rng, "events": events}


def legs(spot: float, rule: Rule = RULE) -> list[dict]:
    """The four legs, as quantity signs: −1 sold, +1 bought."""
    atm = round(spot / rule.step) * rule.step
    return [{"type": "CE", "strike": float(atm), "q": -1},
            {"type": "PE", "strike": float(atm), "q": -1},
            {"type": "CE", "strike": float(atm + rule.wing_pts), "q": 1},
            {"type": "PE", "strike": float(atm - rule.wing_pts), "q": 1}]


def exit_reason(pnl_per_unit: float, credit: float, minute: int, rule: Rule = RULE) -> Optional[str]:
    """TARGET / STOP on the position's per-unit P&L, EOD at square-off, else None."""
    if credit > 0 and pnl_per_unit >= rule.target_pct / 100 * credit:
        return "TARGET"
    if credit > 0 and pnl_per_unit <= -rule.stop_pct / 100 * credit:
        return "STOP"
    if minute >= rule.squareoff:
        return "EOD"
    return None


def describe(rule: Rule = RULE) -> list[str]:
    return [
        f"Opening range = highest and lowest NIFTY 1-minute close from {hhmm(rule.or_start)} to {hhmm(rule.or_end)}.",
        f"From {hhmm(rule.first_check)} to {hhmm(rule.last_signal)}, a close more than {rule.break_pts:g} pts beyond the range is a breakout.",
        f"If NIFTY closes back inside the range within {rule.fail_window} minutes, the breakout failed — that is the signal.",
        f"Next minute: sell the ATM call and put, buy the call and put {rule.wing_pts} pts away (iron fly, loss capped). Nearest weekly expiry.",
        f"Exit at {rule.target_pct:g}% of the credit captured, at a loss of {rule.stop_pct:g}% of the credit, or at {hhmm(rule.squareoff)}. One trade a day.",
    ]
