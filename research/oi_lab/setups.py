"""
Entry-zone engine: turns the live walls into concrete, priced trade setups.

Four structural setups, each defined only from the chain:

  SUPPORT_BOUNCE     buy a call as spot pulls back into the put-writer wall
  RESISTANCE_REJECT  buy a put as spot rallies into the call-writer wall
  BREAKOUT           buy a call once spot clears the call wall
  BREAKDOWN          buy a put once spot loses the put wall
  RANGE_WRITE        (for option sellers) hedged short strangle outside both walls

How they are scored — deliberately NOT on a direction forecast, because the 3-year
study shows OI does not forecast direction to the close. They are scored on odds that
WERE validated out of sample: how likely spot is to travel far enough to hit the
target, versus far enough to hit the stop, given the current chain
(``HistoryStudy.reach_odds``). Without history loaded the same odds come from the
option market's own implied volatility (reflection principle). The positioning bias
only nudges the score.

What the walk-forward check showed (``VALIDATION``, reproduced by replaying the full live
pipeline on unseen Market Store sessions with models fitted only on earlier data): the
odds are close to reality, but the score does NOT rank outcomes. So setups are a level
plan with honest odds and costs — no letter grades, and "best" means best-placed.
"""
from __future__ import annotations

from math import erf, sqrt, tanh
from typing import Callable, Optional


MIN_BEST = 50.0          # below this the expected value is negative — never recommended as "best"

VALIDATION = {
    "sample": "70 random NIFTY sessions, Dec 2025 – Sep 2026, checked at 09:50, 10:45, 11:45, 13:00 and 14:10; "
              "models fitted only on sessions before Dec 2025",
    "setups_in_zone": 247,
    "odds": "Predicted 20% target / 24% stop; actually 24% reached the target first, 29% the stop first, "
            "47% neither by the close.",
    "score": "The edge score did not rank outcomes (correlation −0.06). Positive-edge setups averaged +3.6 spot pts "
             "(29 trades), the rest +0.5 — both ≈ breakeven before option costs.",
    "range": "Range-day odds held up: predicted 14% → 17% of sessions stayed inside both walls, 29% → 29%, "
             "50% → 42%, 69% → 46% (a little optimistic at the top).",
    "takeaway": "Use the walls, stops and odds to plan and size. No OI-based buy setup showed an edge by itself "
                "on unseen data.",
}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def implied_reach_odds(sigma_pts: Optional[float]) -> Callable[[str, float], Optional[float]]:
    """P(max excursion ≥ x before the close) for a driftless walk with the remaining 1σ."""
    def f(direction: str, pts: float) -> Optional[float]:
        if not sigma_pts or sigma_pts <= 0:
            return None
        return round(min(1.0, 2 * (1 - _norm_cdf(max(pts, 0) / sigma_pts))), 3)
    return f


def _premium_at(cell: dict, spot: float, at: float) -> Optional[float]:
    """Option price if spot moved to ``at`` right now (delta–gamma, time held still)."""
    if not cell or not cell.get("ltp"):
        return None
    d, g = float(cell.get("delta") or 0), float(cell.get("gamma") or 0)
    dx = at - spot
    return round(max(0.05, float(cell["ltp"]) + d * dx + 0.5 * g * dx * dx), 2)


def _contract(chain: dict, strike: float, typ: str) -> Optional[dict]:
    row = chain.get(strike)
    return (row or {}).get(typ.lower())


def _nearest(strikes: list[float], x: float) -> float:
    return min(strikes, key=lambda s: abs(s - x))


def build(*, spot: float, step: float, zones: dict, bias: dict, expected_move: dict, rows: list[dict],
          mins_left: float, dte: int, reach_odds: Optional[Callable[[str, float], Optional[float]]] = None,
          probs: Optional[dict] = None, lot_size: Optional[int] = None) -> dict:
    sigma = expected_move.get("intraday_sigma_pts")
    if not sigma or sigma <= 0 or not rows:
        return {"setups": [], "best": None, "warnings": ["Not enough live data to price setups (IV unavailable)."],
                "odds_source": None}
    odds_source = "history" if reach_odds else "implied volatility"
    odds = reach_odds or implied_reach_odds(sigma)
    chain = {r["strike"]: r for r in rows}
    strikes = sorted(chain)
    sup = (zones.get("support") or [None])[0]
    res = (zones.get("resistance") or [None])[0]
    b = float(bias.get("score") or 0) / 100
    warnings = []
    if mins_left < 20:
        warnings.append("Less than 20 minutes left — new intraday entries have little time to work.")
    atm = chain.get(_nearest(strikes, spot)) or {}
    for side in ("ce", "pe"):
        c = atm.get(side) or {}
        if c.get("spread_pct") and c["spread_pct"] > 2.0:
            warnings.append(f"ATM {side.upper()} bid-ask spread is {c['spread_pct']:.1f}% of premium — fills will be costly.")
    if sup and res and res["anchor"] - sup["anchor"] < 0.6 * sigma:
        warnings.append("Walls are closer together than a normal intraday swing — expect whipsaws; size down.")

    def price(direction: str, entry: float, stop: float, t1: float, t2: float, strike_bias: int = 0) -> dict:
        typ = "CE" if direction == "long" else "PE"
        k = _nearest(strikes, entry)
        idx = strikes.index(k)
        idx = max(0, min(len(strikes) - 1, idx + strike_bias))
        k = strikes[idx]
        cell = _contract(chain, k, typ) or {}
        return {
            "contract": {"strike": k, "type": typ, "symbol": cell.get("symbol"), "ltp": cell.get("ltp"),
                         "delta": cell.get("delta"), "theta": cell.get("theta"), "iv": cell.get("iv"),
                         "time_value": cell.get("time_value"), "lot_size": cell.get("lot_size") or lot_size},
            "premium": {"entry": _premium_at(cell, spot, entry), "stop": _premium_at(cell, spot, stop),
                        "t1": _premium_at(cell, spot, t1), "t2": _premium_at(cell, spot, t2)},
        }

    def decay_cost_pts(contract: dict) -> float:
        """Spot points an option buyer gives up if price goes nowhere until the close."""
        tv = float(contract.get("time_value") or 0)
        theta = abs(float(contract.get("theta") or 0))
        delta = max(abs(float(contract.get("delta") or 0)), 0.1)
        prem = tv if dte <= 0 else min(tv, theta * min(mins_left, 375) / 375)
        return prem / delta

    def score(kind: str, direction: str, entry_lo: float, entry_hi: float, stop: float, t1: float, t2: float,
              bias_sign: int, base_why: list[str], contract: dict) -> dict:
        """``kind`` is "fade" (buy at a wall) or "break" (buy through a wall).

        Target and stop odds are measured from the middle of the entry zone, because that
        is where the trade starts. Reaching the zone at all is a separate number (p_fill).
        The score is an expected value in spot points: target × its odds, minus stop × its
        odds, minus time decay in the likely case that neither is reached — which is what
        sinks option buyers late in the day and on expiry."""
        mid = (entry_lo + entry_hi) / 2
        up = direction == "long"
        reward = (t1 - mid) if up else (mid - t1)
        risk = (mid - stop) if up else (stop - mid)
        rr = reward / risk if risk > 0 else None
        p_t = odds("up" if up else "down", abs(t1 - mid)) if reward > 0 else None
        p_s = odds("down" if up else "up", abs(mid - stop)) if risk > 0 else None
        why = list(base_why)
        conf = 50.0
        ev = None
        if p_t is not None and p_s is not None and risk > 0:
            cost = decay_cost_pts(contract)
            p_none = max(0.0, 1.0 - p_t - p_s)
            ev = p_t * reward - p_s * risk - p_none * cost
            conf += 45 * tanh(1.5 * ev / risk)
            why.append(f"From the entry zone: {p_t:.0%} odds spot travels to target {t1:,.0f}, "
                       f"{p_s:.0%} odds it travels to the stop {stop:,.0f} ({odds_source}).")
            if p_none >= 0.3 and cost >= 0.15 * risk:
                why.append(f"{p_none:.0%} odds neither is reached; time decay would then cost ≈ {cost:.0f} spot-pts "
                           f"({cost * max(abs(float(contract.get('delta') or 0)), 0.1):.1f} of premium) by the close.")
        conf += 8 * b * bias_sign
        if abs(b) >= 0.12:
            why.append(f"Positioning bias {bias['label'].lower()} ({bias['score']:+.0f}) "
                       f"{'supports' if b * bias_sign > 0 else 'works against'} this side.")
        if mins_left < 20:
            conf -= 20
        beyond_stop = (up and spot < stop) or (not up and spot > stop)
        past_zone = (up and spot > entry_hi) or (not up and spot < entry_lo)
        if entry_lo <= spot <= entry_hi:
            status, dist, p_fill = "ACTIVE", 0.0, 1.0
        elif kind == "fade" and beyond_stop:
            status, dist, p_fill = "INVALID", None, None
            why.append("The wall this trade leans on has already given way.")
        elif kind == "break" and past_zone:
            status, dist, p_fill = "EXTENDED", round(min(abs(spot - entry_lo), abs(spot - entry_hi)), 1), None
            why.append("Price already ran past the entry zone — chasing worsens the reward/risk.")
        else:
            status = "WAIT"
            dist = round(min(abs(spot - entry_lo), abs(spot - entry_hi)), 1)
            toward = "up" if mid > spot else "down"
            p_fill = odds(toward, dist)
            if p_fill is not None:
                why.append(f"Odds price reaches the entry zone ({dist:.0f} pts away) before the close: {p_fill:.0%}.")
        conf = max(3.0, min(95.0, conf))
        return {"entry_zone": [round(entry_lo, 1), round(entry_hi, 1)], "stop": round(stop, 1),
                "targets": [round(t1, 1), round(t2, 1)], "reward_pts": round(reward, 1), "risk_pts": round(risk, 1),
                "rr": round(rr, 2) if rr else None, "p_target": p_t, "p_stop": p_s, "p_fill": p_fill,
                "ev_pts": round(ev, 1) if ev is not None else None,
                "confidence": round(conf, 1), "status": status, "distance_pts": dist, "why": why}

    setups = []
    if sup and res and res["anchor"] > sup["anchor"]:
        S, R = sup["anchor"], res["anchor"]
        mid_sr = (S + R) / 2
        # 1. buy a call at support
        lo, hi = S + 0.05 * sigma, S + 0.30 * sigma
        stop = sup["low"] - 0.25 * sigma
        t1 = max(hi + 0.4 * sigma, min(mid_sr, R - 0.15 * sigma))
        t2 = max(t1 + 0.2 * sigma, R - 0.10 * sigma)
        why = [f"Put writers defend {S:g} ({sup['label'].lower()}, strength {sup['strength']:.0f})."]
        if probs and probs.get("pe_held"):
            why.append(f"Tested model: put wall holds until the close with {probs['pe_held']['p']:.0%} probability.")
        px = price("long", (lo + hi) / 2, stop, t1, t2)
        s = score("fade", "long", lo, hi, stop, t1, t2, +1, why, px["contract"])
        setups.append({"id": "SUPPORT_BOUNCE", "title": f"Buy Call — bounce off {S:g} support", "direction": "long",
                       "trigger": f"Spot dips into {lo:,.0f}–{hi:,.0f} and holds above {S:g}", **s, **px})
        # 2. buy a put at resistance
        lo, hi = R - 0.30 * sigma, R - 0.05 * sigma
        stop = res["high"] + 0.25 * sigma
        t1 = min(lo - 0.4 * sigma, max(mid_sr, S + 0.15 * sigma))
        t2 = min(t1 - 0.2 * sigma, S + 0.10 * sigma)
        why = [f"Call writers defend {R:g} ({res['label'].lower()}, strength {res['strength']:.0f})."]
        if probs and probs.get("ce_held"):
            why.append(f"Tested model: call wall holds until the close with {probs['ce_held']['p']:.0%} probability.")
        px = price("short", (lo + hi) / 2, stop, t1, t2)
        s = score("fade", "short", lo, hi, stop, t1, t2, -1, why, px["contract"])
        setups.append({"id": "RESISTANCE_REJECT", "title": f"Buy Put — rejection at {R:g} resistance", "direction": "short",
                       "trigger": f"Spot rallies into {lo:,.0f}–{hi:,.0f} and fails below {R:g}", **s, **px})
        # 3. breakout above the call wall
        above = [r for r in rows if r["strike"] > res["high"] and r.get("ce")]
        nxt = max(above, key=lambda r: r["ce"].get("oi") or 0)["strike"] if above else None
        lo, hi = res["high"] + 0.10 * sigma, res["high"] + 0.35 * sigma
        stop = R - 0.20 * sigma
        t1 = max(hi + 0.5 * sigma, (nxt - 0.1 * sigma) if nxt else hi + 0.7 * sigma)
        t2 = t1 + 0.5 * sigma
        why = [f"Needs call writers at {R:g} to cover — their buying fuels the move."]
        if probs and probs.get("ce_held"):
            why.append(f"Tested model: call wall breaks before the close with {1 - probs['ce_held']['p']:.0%} probability.")
        if res.get("reasons") and any("weakening" in x for x in res["reasons"]):
            why.append("Call writers are already covering from today's OI peak.")
        px = price("long", (lo + hi) / 2, stop, t1, t2, strike_bias=-1)
        s = score("break", "long", lo, hi, stop, t1, t2, +1, why, px["contract"])
        setups.append({"id": "BREAKOUT", "title": f"Buy Call — breakout above {R:g}", "direction": "long",
                       "trigger": f"Spot sustains above {lo:,.0f} (5-min close)", **s, **px})
        # 4. breakdown below the put wall
        below = [r for r in rows if r["strike"] < sup["low"] and r.get("pe")]
        nxt = max(below, key=lambda r: r["pe"].get("oi") or 0)["strike"] if below else None
        lo, hi = sup["low"] - 0.35 * sigma, sup["low"] - 0.10 * sigma
        stop = S + 0.20 * sigma
        t1 = min(lo - 0.5 * sigma, (nxt + 0.1 * sigma) if nxt else lo - 0.7 * sigma)
        t2 = t1 - 0.5 * sigma
        why = [f"Needs put writers at {S:g} to cover — their selling fuels the move."]
        if probs and probs.get("pe_held"):
            why.append(f"Tested model: put wall breaks before the close with {1 - probs['pe_held']['p']:.0%} probability.")
        if sup.get("reasons") and any("weakening" in x for x in sup["reasons"]):
            why.append("Put writers are already covering from today's OI peak.")
        px = price("short", (lo + hi) / 2, stop, t1, t2, strike_bias=+1)
        s = score("break", "short", lo, hi, stop, t1, t2, -1, why, px["contract"])
        setups.append({"id": "BREAKDOWN", "title": f"Buy Put — breakdown below {S:g}", "direction": "short",
                       "trigger": f"Spot sustains below {hi:,.0f} (5-min close)", **s, **px})
        # 5. range write for sellers
        ce_cell, pe_cell = _contract(chain, res["high"], "CE"), _contract(chain, sup["low"], "PE")
        if ce_cell and pe_cell and ce_cell.get("ltp") and pe_cell.get("ltp"):
            p_in = (probs or {}).get("inside", {}).get("p")
            credit = round(float(ce_cell["ltp"]) + float(pe_cell["ltp"]), 2)
            conf = round(100 * p_in, 1) if p_in is not None else None
            setups.append({
                "id": "RANGE_WRITE", "title": f"Range write {sup['low']:g} PE + {res['high']:g} CE (sellers, hedged)",
                "direction": "neutral", "trigger": "Only while both walls stay firm; hedge with further-OTM wings",
                "entry_zone": [sup["low"], res["high"]], "stop": None, "targets": [], "credit": credit,
                "p_target": p_in, "p_stop": None, "p_fill": None, "ev_pts": None, "rr": None, "reward_pts": None, "risk_pts": None,
                "confidence": conf if conf is not None else 50.0,
                "status": "ACTIVE" if sup["anchor"] < spot < res["anchor"] else "INVALID", "distance_pts": 0.0,
                "why": ([f"Tested model: both walls hold until the close with {p_in:.0%} probability."] if p_in is not None
                        else ["History not loaded — no tested odds for both walls holding."])
                       + [f"Collect ≈ {credit:.1f} per unit; loses if spot closes beyond either strike ± credit."],
                "contract": {"legs": [ce_cell.get("symbol"), pe_cell.get("symbol")]}, "premium": {},
            })
    # Best entry: an active setup if one is in its zone; otherwise the waiting setup with the best
    # quality weighted by the odds that price actually comes to it today.
    actionable = [s for s in setups if s["status"] == "ACTIVE" and s["id"] != "RANGE_WRITE" and s["confidence"] >= MIN_BEST]
    waiting = [s for s in setups if s["status"] == "WAIT" and s["id"] != "RANGE_WRITE" and s["confidence"] >= MIN_BEST]
    best = (max(actionable, key=lambda s: s["confidence"]) if actionable
            else max(waiting, key=lambda s: s["confidence"] * (0.4 + 0.6 * (s["p_fill"] if s["p_fill"] is not None else 0.5)))
            if waiting else None)
    setups.sort(key=lambda s: -s["confidence"])
    stand_aside = None
    if best is None and setups:
        stand_aside = ("No option-buying setup has a positive expected value right now — standing aside is a position. "
                       "Wait for price to reach a wall, or for more time/volatility to be left in the session.")
    return {"setups": setups, "best": best["id"] if best else None, "stand_aside": stand_aside,
            "warnings": warnings, "odds_source": odds_source, "sigma_pts": sigma, "min_best": MIN_BEST,
            "validation": VALIDATION}
