"""
Gann × OI entry signals — ONE set of rules for the backtest and the live signal desk.

Every rule reads a ``Ctx``: the 5-minute spot bar that just closed plus the OI state at that
moment (walls, fresh-writing balance, ATM straddle, and the tested wall-hold probabilities).
The backtest builds ``Ctx`` from the Market Store, the live desk from Kite — so a signal on
screen is exactly the signal whose history is shown next to it.

Setups (index options are always BOUGHT, at the money by default):

  GANN_BREAKOUT     a 5-min close through a Gann level from below, closing strong, with room
                    to the next level (or the call wall, if it sits in between)   → CALL
  GANN_BREAKDOWN    the mirror image through a level from above                   → PUT
  GANN_REJECTION    the bar trades up into a Gann level and closes back below it, with call
                    writers sitting at that level (or calls being written)        → PUT
  GANN_RECLAIM      the bar dips through a Gann level and closes back above it, with put
                    writers sitting at that level (or puts being written)         → CALL

What 3 years of NIFTY said (signal_backtest, costs included): trading Gann levels alone lost
2-3x more than with the OI gates below, so the gates earn their place; but no variant tested was
profitable before the holdout date, and the unseen period was near breakeven. Treat signals as
candidates to paper-trade, not as a proven edge.

OI gates use only what was validated out of sample: wall-hold probabilities (AUC ≈ 0.8) and
where writers are adding. Direction models are not used — they showed no edge.

Every signal carries two execution plans:
  SWING   exit at the spot target (next Gann level / wall), on a 5-min close back through the
          level, on an exit alert, or on the premium catastrophe stop; square-off 15:15
  SCALP   premium Gann grid: target the next premium level, stop under the level below,
          at most 45 minutes
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

import numpy as np

from research.oi_lab import gann
from research.oi_lab.indices import hhmm

RULES_VERSION = "g2"
TRADE_UNDERLYINGS = ("NIFTY", "SENSEX")          # the signal desk and paper engine trade these

SETUPS = {
    "GANN_BREAKOUT": {"side": "CE", "label": "Gann breakout", "kind": "breakout"},
    "GANN_BREAKDOWN": {"side": "PE", "label": "Gann breakdown", "kind": "breakout"},
    "GANN_REJECTION": {"side": "PE", "label": "Rejection at Gann level", "kind": "rejection"},
    "GANN_RECLAIM": {"side": "CE", "label": "Reclaim of Gann level", "kind": "rejection"},
}
MODES = ("SWING", "SCALP")

ENTRY_START = 9 * 60 + 35
ENTRY_END = 14 * 60 + 40
EXPIRY_DAY_LAST_ENTRY = 13 * 60 + 30             # premium melts fastest after this (History tab)
SQUAREOFF = 15 * 60 + 15
SCALP_MAX_HOLD = 45
MIN_ROOM_STRADDLES = 0.25
WALL_NEAR_STRADDLES = 0.35

# Rule parameters. DEFAULTS are what the desk runs; the backtest can evaluate variants, and a
# variant is only ever chosen on sessions BEFORE the holdout date (see signal_backtest.research).
DEFAULTS = {
    "oi_gates": True,                 # use fresh-writing balance and wall-hold odds as filters
    "doi_against": 0.25,              # block a breakout when writers on the other side out-write by this
    "p_wall_block": 0.65,             # block a breakout when the wall in its path holds with this probability
    "p_wall_confirm": 0.55,           # rejection/reclaim needs the defending wall to hold at least this likely
    "close_strength": 0.6,            # breakout bar must close in the top 40% of its range (mirror for PE)
    "strike": "ATM",                  # ATM | ITM1 — ATM lost least on training sessions (both modes)
    "alerts": ["INVALIDATED", "WRITERS_FLIP", "WALL_HARDENING", "MOMENTUM_FADE"],
    "setups": list(SETUPS),
}


def params(overrides: Optional[dict] = None) -> dict:
    return {**DEFAULTS, **{k: v for k, v in (overrides or {}).items() if k in DEFAULTS and v is not None}}


@dataclass
class Bar:
    end: int            # minute of day the bar closed
    open: float
    high: float
    low: float
    close: float


@dataclass
class Ctx:
    underlying: str
    day: date
    cp: int                                  # decision minute (= bar.end)
    bar: Bar
    prev_close: float
    straddle: float
    step: float
    dte: int
    ce_wall: Optional[float] = None
    pe_wall: Optional[float] = None
    doi_bal: Optional[float] = None
    p_ce_held: Optional[float] = None
    p_pe_held: Optional[float] = None


def _ok(x) -> bool:
    return x is not None and np.isfinite(x)


def itm_strike(side: str, spot: float, step: float, choice: str = "ITM1") -> float:
    """One strike in the money (never a strike within a quarter step of spot), or the ATM strike."""
    if choice == "ATM":
        return float(round(spot / step) * step)
    if side == "CE":
        k = np.floor(spot / step) * step
        return float(k - step if spot - k < 0.25 * step else k)
    k = np.ceil(spot / step) * step
    return float(k + step if k - spot < 0.25 * step else k)


def _signal(c: Ctx, setup: str, level: float, target: float, invalidation: float, reasons: list[str],
            p: Optional[dict] = None) -> dict:
    p = p or DEFAULTS
    meta = SETUPS[setup]
    side = meta["side"]
    return {
        "setup": setup, "label": meta["label"], "kind": meta["kind"], "side": side,
        "underlying": c.underlying, "day": c.day.isoformat(), "cp": c.cp, "time": hhmm(c.cp),
        "entry_time": hhmm(c.cp + 1), "spot": round(c.bar.close, 2), "level": level,
        "spot_target": round(target, 2), "invalidation": round(invalidation, 2),
        "room_pts": round(abs(target - c.bar.close), 1), "risk_pts": round(abs(c.bar.close - invalidation), 1),
        "strike": itm_strike(side, c.bar.close, c.step, p["strike"]), "reasons": reasons,
        "context": {"straddle": round(c.straddle, 2), "ce_wall": c.ce_wall, "pe_wall": c.pe_wall,
                    "doi_bal": round(c.doi_bal, 3) if _ok(c.doi_bal) else None,
                    "p_ce_held": c.p_ce_held, "p_pe_held": c.p_pe_held, "dte": c.dte},
        "rules_version": RULES_VERSION,
    }


def evaluate(c: Ctx, p: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
    """Signals firing on this bar, and the setups that looked close but were blocked (with why)."""
    p = p or DEFAULTS
    gates = p["oi_gates"]
    fired, blocked = [], []
    if not _ok(c.straddle) or c.straddle <= 0 or not _ok(c.prev_close):
        return fired, blocked
    if not (ENTRY_START <= c.cp <= ENTRY_END):
        return fired, blocked
    if c.dte <= 0 and c.cp > EXPIRY_DAY_LAST_ENTRY:
        return fired, [{"setup": "ALL", "why": "Expiry day after 13:30 — option buying fights the fastest decay."}]
    s = c.straddle
    b, c0, c1 = c.bar, c.bar.close, c.prev_close
    buf = max(0.04 * s, 0.0003 * c0)
    rng = max(b.high - b.low, 1e-9)
    pos = (c0 - b.low) / rng                       # where the bar closed inside its range
    doi = c.doi_bal if _ok(c.doi_bal) else 0.0

    def block(setup, level, why):
        blocked.append({"setup": setup, "level": level, "why": why})

    # ── breakouts: a close through a level ──
    for G in gann.crossed(c1, c0):
        if c0 > c1 and c1 <= G <= c0 - buf:
            nxt = gann.ceil(G)
            wall_in_path = _ok(c.ce_wall) and c0 < c.ce_wall < nxt
            target = c.ce_wall if wall_in_path else nxt
            if pos < p["close_strength"]:
                block("GANN_BREAKOUT", G, "closed weak inside its bar")
            elif target - c0 < MIN_ROOM_STRADDLES * s:
                block("GANN_BREAKOUT", G, f"no room: {'call wall' if wall_in_path else 'next Gann level'} {target:g} too close")
            elif gates and doi < -p["doi_against"]:
                block("GANN_BREAKOUT", G, "call writers are out-writing put writers today")
            elif gates and wall_in_path and _ok(c.p_ce_held) and c.p_ce_held > p["p_wall_block"]:
                block("GANN_BREAKOUT", G, f"call wall {c.ce_wall:g} likely holds ({c.p_ce_held:.0%})")
            else:
                reasons = [f"5-min close {c0:,.1f} through Gann {G:g} (from {c1:,.1f})",
                           f"target {'call wall' if wall_in_path else 'next Gann'} {target:g} is {target - c0:.0f} pts away"]
                if _ok(c.p_ce_held) and wall_in_path:
                    reasons.append(f"call wall hold odds only {c.p_ce_held:.0%}")
                if doi > 0.15:
                    reasons.append("put writers adding faster than call writers")
                fired.append(_signal(c, "GANN_BREAKOUT", G, target, G - buf, reasons, p))
        if c0 < c1 and c0 + buf <= G <= c1:
            nxt = gann.below(G)
            wall_in_path = _ok(c.pe_wall) and nxt < c.pe_wall < c0
            target = c.pe_wall if wall_in_path else nxt
            if pos > 1 - p["close_strength"]:
                block("GANN_BREAKDOWN", G, "closed weak inside its bar")
            elif c0 - target < MIN_ROOM_STRADDLES * s:
                block("GANN_BREAKDOWN", G, f"no room: {'put wall' if wall_in_path else 'next Gann level'} {target:g} too close")
            elif gates and doi > p["doi_against"]:
                block("GANN_BREAKDOWN", G, "put writers are out-writing call writers today")
            elif gates and wall_in_path and _ok(c.p_pe_held) and c.p_pe_held > p["p_wall_block"]:
                block("GANN_BREAKDOWN", G, f"put wall {c.pe_wall:g} likely holds ({c.p_pe_held:.0%})")
            else:
                reasons = [f"5-min close {c0:,.1f} through Gann {G:g} (from {c1:,.1f})",
                           f"target {'put wall' if wall_in_path else 'next Gann'} {target:g} is {c0 - target:.0f} pts away"]
                if _ok(c.p_pe_held) and wall_in_path:
                    reasons.append(f"put wall hold odds only {c.p_pe_held:.0%}")
                if doi < -0.15:
                    reasons.append("call writers adding faster than put writers")
                fired.append(_signal(c, "GANN_BREAKDOWN", G, target, G + buf, reasons, p))

    # ── rejection at the level above ──
    G = gann.ceil(max(c0, c1))
    if b.high >= G and c0 <= G - buf:
        wall_near = _ok(c.ce_wall) and abs(c.ce_wall - G) <= WALL_NEAR_STRADDLES * s
        fl = gann.floor(c0)
        target = fl if c0 - fl >= MIN_ROOM_STRADDLES * s else gann.below(fl)
        if _ok(c.pe_wall) and target < c.pe_wall < c0:
            target = c.pe_wall
        if pos > 0.45:
            block("GANN_REJECTION", G, "no rejection wick — closed near the high")
        elif gates and not (wall_near or doi <= -0.2):
            block("GANN_REJECTION", G, "no call writers at this level")
        elif gates and _ok(c.p_ce_held) and c.p_ce_held < p["p_wall_confirm"]:
            block("GANN_REJECTION", G, f"call wall hold odds only {c.p_ce_held:.0%}")
        elif c0 - target < MIN_ROOM_STRADDLES * s:
            block("GANN_REJECTION", G, "no room below")
        else:
            reasons = [f"tagged Gann {G:g} (high {b.high:,.1f}) and closed back at {c0:,.1f}"]
            if wall_near:
                reasons.append(f"call writers defend {c.ce_wall:g}" + (f" (hold odds {c.p_ce_held:.0%})" if _ok(c.p_ce_held) else ""))
            if doi <= -0.2:
                reasons.append("call writers adding faster than put writers")
            fired.append(_signal(c, "GANN_REJECTION", G, target, G + buf, reasons, p))

    # ── reclaim of the level below ──
    G = gann.floor(min(c0, c1))
    if b.low <= G and c0 >= G + buf:
        wall_near = _ok(c.pe_wall) and abs(c.pe_wall - G) <= WALL_NEAR_STRADDLES * s
        cl = gann.ceil(c0)
        target = cl if cl - c0 >= MIN_ROOM_STRADDLES * s else gann.ceil(cl)
        if _ok(c.ce_wall) and c0 < c.ce_wall < target:
            target = c.ce_wall
        if pos < 0.55:
            block("GANN_RECLAIM", G, "no reclaim wick — closed near the low")
        elif gates and not (wall_near or doi >= 0.2):
            block("GANN_RECLAIM", G, "no put writers at this level")
        elif gates and _ok(c.p_pe_held) and c.p_pe_held < p["p_wall_confirm"]:
            block("GANN_RECLAIM", G, f"put wall hold odds only {c.p_pe_held:.0%}")
        elif target - c0 < MIN_ROOM_STRADDLES * s:
            block("GANN_RECLAIM", G, "no room above")
        else:
            reasons = [f"dipped to Gann {G:g} (low {b.low:,.1f}) and closed back at {c0:,.1f}"]
            if wall_near:
                reasons.append(f"put writers defend {c.pe_wall:g}" + (f" (hold odds {c.p_pe_held:.0%})" if _ok(c.p_pe_held) else ""))
            if doi >= 0.2:
                reasons.append("put writers adding faster than call writers")
            fired.append(_signal(c, "GANN_RECLAIM", G, target, G - buf, reasons, p))
    fired = [f for f in fired if f["setup"] in p["setups"]]
    return fired, blocked


def watch(c: Ctx) -> list[dict]:
    """What would trigger next from here — the levels to watch, and whether OI currently allows each."""
    if not _ok(c.straddle) or c.straddle <= 0:
        return []
    s, c0 = c.straddle, c.bar.close
    up, dn = gann.ceil(c0), gann.floor(c0)
    buf = max(0.04 * s, 0.0003 * c0)
    doi = c.doi_bal if _ok(c.doi_bal) else 0.0
    out = []
    ce_ok = doi >= -0.25 and not (_ok(c.ce_wall) and c0 < c.ce_wall < gann.ceil(up) and _ok(c.p_ce_held) and c.p_ce_held > 0.65)
    out.append({"setup": "GANN_BREAKOUT", "side": "CE", "level": up, "trigger": f"5-min close above {up + buf:,.1f}",
                "distance_pts": round(up + buf - c0, 1), "oi_allows": ce_ok,
                "note": "OI allows it" if ce_ok else "blocked now: call writers dominate or call wall likely holds"})
    pe_ok = doi <= 0.25 and not (_ok(c.pe_wall) and gann.below(dn) < c.pe_wall < c0 and _ok(c.p_pe_held) and c.p_pe_held > 0.65)
    out.append({"setup": "GANN_BREAKDOWN", "side": "PE", "level": dn, "trigger": f"5-min close below {dn - buf:,.1f}",
                "distance_pts": round(c0 - (dn - buf), 1), "oi_allows": pe_ok,
                "note": "OI allows it" if pe_ok else "blocked now: put writers dominate or put wall likely holds"})
    rej_ok = (_ok(c.ce_wall) and abs(c.ce_wall - up) <= WALL_NEAR_STRADDLES * s) or doi <= -0.2
    out.append({"setup": "GANN_REJECTION", "side": "PE", "level": up, "trigger": f"tag {up:g}, close below {up - buf:,.1f}",
                "distance_pts": round(up - c0, 1), "oi_allows": rej_ok,
                "note": "call writers at the level" if rej_ok else "no call writers at this level yet"})
    rec_ok = (_ok(c.pe_wall) and abs(c.pe_wall - dn) <= WALL_NEAR_STRADDLES * s) or doi >= 0.2
    out.append({"setup": "GANN_RECLAIM", "side": "CE", "level": dn, "trigger": f"dip to {dn:g}, close above {dn + buf:,.1f}",
                "distance_pts": round(c0 - dn, 1), "oi_allows": rec_ok,
                "note": "put writers at the level" if rec_ok else "no put writers at this level yet"})
    return out


# ── open-position exit alerts (the "next bad move" warnings) ─────────
ALERTS = {
    "INVALIDATED": "5-min close back through the entry level — the move failed",
    "WRITERS_FLIP": "writers flipped against the trade (fresh-writing balance swung hard)",
    "WALL_HARDENING": "the wall in the target's path became very likely to hold",
    "MOMENTUM_FADE": "two adverse 5-min closes back past the entry price",
}


def exit_alerts(pos: dict, c: Ctx, enabled: Optional[list[str]] = None) -> tuple[list[dict], int]:
    """Alerts for an open swing position at this bar, and the updated adverse-close streak.

    ``pos`` needs: side, invalidation, spot_entry, spot_target, entry_doi_bal, adverse_streak.
    ``enabled`` limits which alert codes can fire (all by default)."""
    long_call = pos["side"] == "CE"
    c0, c1 = c.bar.close, c.prev_close
    out = []
    if (long_call and c0 < pos["invalidation"]) or (not long_call and c0 > pos["invalidation"]):
        out.append({"code": "INVALIDATED", "text": ALERTS["INVALIDATED"], "spot": c0})
    e = pos.get("entry_doi_bal")
    if _ok(e) and _ok(c.doi_bal) and ((long_call and c.doi_bal <= e - 0.4) or (not long_call and c.doi_bal >= e + 0.4)):
        out.append({"code": "WRITERS_FLIP", "text": ALERTS["WRITERS_FLIP"], "doi_bal": round(c.doi_bal, 3)})
    tgt = pos.get("spot_target")
    if long_call and _ok(c.ce_wall) and _ok(c.p_ce_held) and c0 < c.ce_wall <= (tgt or np.inf) and c.p_ce_held >= 0.8:
        out.append({"code": "WALL_HARDENING", "text": ALERTS["WALL_HARDENING"], "wall": c.ce_wall})
    if not long_call and _ok(c.pe_wall) and _ok(c.p_pe_held) and c0 > c.pe_wall >= (tgt or -np.inf) and c.p_pe_held >= 0.8:
        out.append({"code": "WALL_HARDENING", "text": ALERTS["WALL_HARDENING"], "wall": c.pe_wall})
    adverse = (long_call and c0 < c1) or (not long_call and c0 > c1)
    streak = (pos.get("adverse_streak") or 0) + 1 if adverse else 0
    behind = (long_call and c0 < pos["spot_entry"]) or (not long_call and c0 > pos["spot_entry"])
    if streak >= 2 and behind:
        out.append({"code": "MOMENTUM_FADE", "text": ALERTS["MOMENTUM_FADE"], "spot": c0})
    if enabled is not None:
        out = [a for a in out if a["code"] in enabled]
    return out, streak


def swing_premium_stop(entry: float) -> float:
    """Catastrophe stop for swing trades: two premium Gann levels under the entry's floor."""
    return gann.below(gann.floor(entry))


def plans(signal: dict, entry_premium: float) -> dict:
    scalp = gann.premium_plan(entry_premium)
    return {
        "SWING": {"spot_target": signal["spot_target"], "invalidation": signal["invalidation"],
                  "premium_stop": swing_premium_stop(entry_premium), "trail_after": scalp["target1"],
                  "squareoff": hhmm(SQUAREOFF)},
        "SCALP": {**scalp, "max_hold_min": SCALP_MAX_HOLD, "squareoff": hhmm(SQUAREOFF)},
    }


def ctx_dict(c: Ctx) -> dict:
    d = asdict(c)
    d["day"] = c.day.isoformat()
    return d
