"""
FALSE_BREAKOUT_FIREWALL — the layer that sits between a signal and an order.

A breakout engine's job is to notice that a level broke. This module's job is to doubt it, and
the separation is the point: the same code that wants to trade should not be the code deciding
whether the trade is safe.

It distinguishes three things that are routinely confused:

    suspicious      something about this break is off — a rejection wick, no participation,
                    futures disagreeing. Worth a warning and worth blocking an entry on. It is
                    NOT a claim that the break will fail, and the alert says so.
    failed          price has closed back through the level within the failure window. This is
                    an observation about candles that have already printed, not a prediction.
    cooling off     a failure just happened here; new attempts at the same level are refused
                    for a configured number of bars.

A wick through a level is not a breakout. A single weak volume reading is not a failure. Both
of those mistakes produce a system that is confidently wrong at speed, so each state transition
below requires a completed candle and names the measurement that caused it.

Every transition is recorded with an event id, so the same breakout attempt cannot raise the
same alert twice and the whole sequence can be read back afterwards.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from research.fvb.structure import (VOL_CONFIRMED, VOL_UNAVAILABLE, VOL_WEAK, Params,
                                    candle_shape)

# ── states ───────────────────────────────────────────────────────────────────
IDLE = "IDLE"
BREAKOUT_CANDIDATE = "BREAKOUT_CANDIDATE"
WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
WAITING_FOR_RETEST = "WAITING_FOR_RETEST"
VALID_BREAKOUT = "VALID_BREAKOUT"
POTENTIAL_FALSE_BREAKOUT = "POTENTIAL_FALSE_BREAKOUT"
FAILED_BREAKOUT_CONFIRMED = "FAILED_BREAKOUT_CONFIRMED"
ENTRY_BLOCKED = "ENTRY_BLOCKED"
COOLDOWN = "COOLDOWN"

STATES = (IDLE, BREAKOUT_CANDIDATE, WAITING_FOR_CONFIRMATION, WAITING_FOR_RETEST,
          VALID_BREAKOUT, POTENTIAL_FALSE_BREAKOUT, FAILED_BREAKOUT_CONFIRMED,
          ENTRY_BLOCKED, COOLDOWN)

TERMINAL = (VALID_BREAKOUT, FAILED_BREAKOUT_CONFIRMED, ENTRY_BLOCKED)

# ── alert kinds ──────────────────────────────────────────────────────────────
ALERT_WARNING = "POTENTIAL FALSE BREAKOUT — NIFTY"
ALERT_FAILED = "FALSE BREAKOUT CONFIRMED — NIFTY"
ALERT_VALID = "BREAKOUT CONFIRMED — ENTRY ELIGIBLE"


@dataclass(frozen=True)
class FirewallParams:
    enabled: bool = True
    # a break is only a break on a CLOSE past the level plus its buffer; a wick is never enough
    require_close_beyond: bool = True
    # bars allowed for the break to prove itself before it is abandoned
    confirmation_bars: int = 2
    # bars after a confirmed break within which a close back through it means failure
    failure_window: int = 3
    # bars to refuse new attempts at the same level after a failure
    cooldown_bars: int = 15
    # which warnings are armed — each independently testable
    warn_on_wick_reject: bool = True
    warn_on_no_participation: bool = True
    warn_on_futures_disagree: bool = True
    warn_on_immediate_reclaim: bool = True
    warn_on_choppy: bool = False
    # a warning can either block the entry outright or merely annotate it
    block_on_warning: bool = True
    # how many warnings must fire before an entry is blocked, when block_on_warning is on
    warnings_to_block: int = 1

    def as_dict(self) -> dict:
        return asdict(self)


FP = FirewallParams()


def event_id(d: date, minute: int, direction: str, level: float) -> str:
    """A stable id for one breakout attempt, so the same attempt cannot alert twice."""
    raw = f"{d}|{minute}|{direction}|{level:.2f}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


@dataclass
class Attempt:
    """One breakout attempt, from the bar that broke the level to whatever became of it."""
    event_id: str
    session: date
    direction: str                 # BULL | BEAR
    level: float
    level_name: str
    buffer: float
    started_minute: int
    # begins IDLE and transitions INTO candidacy, so the audit log never contains a
    # self-transition that reads as if nothing happened
    state: str = IDLE
    confirmed_minute: Optional[int] = None
    resolved_minute: Optional[int] = None
    warnings: list = field(default_factory=list)
    transitions: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    measurements: dict = field(default_factory=dict)
    blocked: bool = False
    block_reason: Optional[str] = None

    def to(self, state: str, minute: int, why: str, **measured) -> None:
        """Move to a new state, recording what was measured at the moment it happened."""
        self.transitions.append({
            "from": self.state, "to": state, "minute": minute,
            "time": f"{minute // 60:02d}:{minute % 60:02d}", "why": why,
            "measured": {k: v for k, v in measured.items() if v is not None},
        })
        self.state = state

    def alert(self, kind: str, minute: int, body: dict) -> Optional[dict]:
        """Raise an alert once per kind per attempt. Repeats are dropped, not re-sent."""
        if any(a["kind"] == kind for a in self.alerts):
            return None
        a = {"kind": kind, "event_id": self.event_id, "session": str(self.session),
             "minute": minute, "time": f"{minute // 60:02d}:{minute % 60:02d}",
             "direction": self.direction, "level": round(self.level, 2),
             "level_name": self.level_name, "state": self.state, **body}
        self.alerts.append(a)
        return a

    def as_dict(self) -> dict:
        d = asdict(self)
        d["session"] = str(self.session)
        return d


class Firewall:
    """Per-session firewall. One instance watches one trading day."""

    def __init__(self, session: date, p: Params, fp: FirewallParams = FP):
        self.session = session
        self.p = p
        self.fp = fp
        self.attempts: list[Attempt] = []
        self.open: Optional[Attempt] = None
        self.cooldown_until: dict[str, int] = {}    # level key -> minute
        self.seen: set[str] = set()

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _key(direction: str, level: float) -> str:
        return f"{direction}:{level:.2f}"

    def in_cooldown(self, direction: str, level: float, minute: int) -> Optional[int]:
        until = self.cooldown_until.get(self._key(direction, level))
        return until if (until is not None and minute < until) else None

    # ── the break itself ─────────────────────────────────────────────────
    def observe_break(self, bar: pd.Series, minute: int, direction: str,
                      level: float, level_name: str, buffer: float,
                      vol: dict, futures: dict, chop: dict) -> Optional[Attempt]:
        """A completed candle has closed beyond a level. Open an attempt and judge the bar.

        Returns the attempt, or None when the bar does not qualify as a break at all.
        """
        bullish = direction == "BULL"
        close = float(bar["close"])
        beyond = (close > level + buffer) if bullish else (close < level - buffer)
        touched = (float(bar["high"]) > level) if bullish else (float(bar["low"]) < level)

        if self.fp.require_close_beyond and not beyond:
            # a wick through the level with a close back inside is the textbook warning, but it
            # is not a breakout and must never start one
            if touched:
                self._wick_only(bar, minute, direction, level, level_name, buffer, vol)
            return None
        if not beyond:
            return None

        cd = self.in_cooldown(direction, level, minute)
        if cd is not None:
            a = Attempt(event_id(self.session, minute, direction, level), self.session,
                        direction, level, level_name, buffer, minute)
            a.to(COOLDOWN, minute, f"this level failed recently; refused until "
                                   f"{cd // 60:02d}:{cd % 60:02d}")
            a.blocked, a.block_reason = True, "cooldown after a confirmed failure"
            self.attempts.append(a)
            return a

        eid = event_id(self.session, minute, direction, level)
        if eid in self.seen:
            return None
        self.seen.add(eid)

        a = Attempt(eid, self.session, direction, level, level_name, buffer, minute)
        shape = candle_shape(bar, bullish)
        a.measurements = {"close": round(close, 2), "shape": shape, "volume": vol,
                          "futures": futures, "chop": chop,
                          "distance": round(abs(close - level), 2)}
        a.to(BREAKOUT_CANDIDATE, minute,
             f"a completed candle closed {'above' if bullish else 'below'} "
             f"{level_name} ({level:.2f}) by more than the {buffer:.1f} point buffer",
             close=round(close, 2))

        self._collect_warnings(a, minute, shape, vol, futures, chop)
        if a.warnings and self.fp.block_on_warning and \
                len(a.warnings) >= self.fp.warnings_to_block:
            a.to(POTENTIAL_FALSE_BREAKOUT, minute,
                 f"{len(a.warnings)} warning condition(s) on the breakout bar")
            a.blocked = True
            a.block_reason = "; ".join(w["what"] for w in a.warnings)
            a.alert(ALERT_WARNING, minute, {
                "spot_close": round(close, 2),
                "relative_volume": vol.get("rvol"),
                "volume_state": vol.get("state"),
                "conditions": [w["what"] for w in a.warnings],
                "action": "entry blocked, awaiting a new validated setup",
                "note": "This says the breakout is suspicious, not that it will fail."})
        else:
            a.to(WAITING_FOR_CONFIRMATION, minute,
                 "the breakout bar passed every armed warning check")

        self.attempts.append(a)
        self.open = a
        return a

    def _wick_only(self, bar, minute, direction, level, level_name, buffer, vol) -> None:
        """Record a poke through the level that closed back inside, without starting an attempt."""
        eid = event_id(self.session, minute, direction, level)
        if eid in self.seen:
            return
        self.seen.add(eid)
        a = Attempt(eid, self.session, direction, level, level_name, buffer, minute)
        a.measurements = {"close": round(float(bar["close"]), 2), "volume": vol}
        a.warnings.append({"what": "price crossed the level intrabar but closed back inside",
                           "measured": {"high": float(bar["high"]), "low": float(bar["low"]),
                                        "close": float(bar["close"]), "level": level}})
        a.to(POTENTIAL_FALSE_BREAKOUT, minute,
             "a wick crossed the level and the candle closed back inside — not a breakout")
        a.blocked, a.block_reason = True, "wick through the level, close back inside"
        a.alert(ALERT_WARNING, minute, {
            "spot_close": round(float(bar["close"]), 2),
            "relative_volume": vol.get("rvol"), "volume_state": vol.get("state"),
            "conditions": [a.warnings[0]["what"]],
            "action": "no entry — a wick is not a break",
            "note": "This says the breakout is suspicious, not that it will fail."})
        self.attempts.append(a)

    def _collect_warnings(self, a: Attempt, minute: int, shape: dict,
                          vol: dict, futures: dict, chop: dict) -> None:
        f = self.fp
        if f.warn_on_wick_reject and shape.get("ok"):
            if shape["wick_pct"] > self.p.max_wick_pct:
                a.warnings.append({"what": f"rejection wick is {shape['wick_pct']:.0f}% of the "
                                           f"candle, over the {self.p.max_wick_pct:.0f}% limit",
                                   "measured": shape})
            elif shape["close_location"] < self.p.min_close_location:
                a.warnings.append({"what": f"closed at {shape['close_location']:.0%} of the "
                                           f"candle's range, a weak close",
                                   "measured": shape})
        if f.warn_on_no_participation:
            if vol.get("state") == VOL_UNAVAILABLE:
                a.warnings.append({"what": "participation could not be measured: "
                                           + str(vol.get("reason") or "no volume series"),
                                   "measured": vol})
            elif vol.get("state") == VOL_WEAK:
                a.warnings.append({"what": f"relative volume {vol.get('rvol')} is below the "
                                           f"{vol.get('threshold')} threshold",
                                   "measured": vol})
        if f.warn_on_futures_disagree and futures.get("checked"):
            if not futures.get("agrees", True):
                a.warnings.append({"what": "futures did not move with the index on this bar",
                                   "measured": futures})
        if f.warn_on_choppy and chop.get("checked") and chop.get("choppy"):
            a.warnings.append({"what": "the session is in a choppy regime by the configured "
                                       "measure", "measured": chop})

    # ── what became of it ────────────────────────────────────────────────
    def follow(self, a: Attempt, day: pd.DataFrame, minute: int) -> Attempt:
        """Walk the bars after the break and classify the outcome on completed candles only."""
        if a.state in TERMINAL or a.state == COOLDOWN:
            return a
        bullish = a.direction == "BULL"
        after = day[(day["minute"] > a.started_minute) &
                    (day["minute"] <= a.started_minute + self.fp.failure_window)]
        for row in after.itertuples():
            cl = float(row.close)
            back_inside = (cl < a.level) if bullish else (cl > a.level)
            if back_inside:
                a.resolved_minute = int(row.minute)
                a.to(FAILED_BREAKOUT_CONFIRMED, int(row.minute),
                     f"closed back {'below' if bullish else 'above'} {a.level_name} "
                     f"({a.level:.2f}) within {self.fp.failure_window} candles",
                     close=round(cl, 2))
                a.blocked = True
                a.block_reason = a.block_reason or "the breakout failed"
                bars_in = int(row.minute) - a.started_minute
                a.alert(ALERT_FAILED, int(row.minute), {
                    "failure_close": round(cl, 2),
                    "failure_time": f"{int(row.minute) // 60:02d}:{int(row.minute) % 60:02d}",
                    "candles_in_window": bars_in,
                    "window": self.fp.failure_window,
                    "evidence": f"close {cl:.2f} is back through {a.level:.2f}",
                    "action": "signal invalidated; any unfilled entry is eligible for "
                              "cancellation after its broker status is checked",
                    "cooldown_until": f"{(int(row.minute) + self.fp.cooldown_bars) // 60:02d}:"
                                      f"{(int(row.minute) + self.fp.cooldown_bars) % 60:02d}"})
                self.cooldown_until[self._key(a.direction, a.level)] = \
                    int(row.minute) + self.fp.cooldown_bars
                self.open = None
                return a
        # survived the window
        if not a.blocked:
            a.resolved_minute = a.started_minute + self.fp.failure_window
            a.confirmed_minute = a.resolved_minute
            a.to(VALID_BREAKOUT, a.resolved_minute,
                 f"held above {a.level_name} for {self.fp.failure_window} candles")
        else:
            a.to(ENTRY_BLOCKED, a.started_minute, a.block_reason or "blocked")
        self.open = None
        return a

    # ── reporting ────────────────────────────────────────────────────────
    def summary(self) -> dict:
        by_state: dict[str, int] = {}
        for a in self.attempts:
            by_state[a.state] = by_state.get(a.state, 0) + 1
        return {"session": str(self.session), "attempts": len(self.attempts),
                "by_state": by_state,
                "alerts": [al for a in self.attempts for al in a.alerts],
                "blocked": sum(1 for a in self.attempts if a.blocked)}
