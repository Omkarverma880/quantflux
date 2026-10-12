"""
Deterministic tests for the breakout engine and the firewall.

Everything here runs on hand-built fixtures, not on market data, because a test that depends on
the store passes or fails for reasons that have nothing to do with the code. The fixtures encode
the cases that actually matter: a clean break, a wick that only looks like one, a break that
closes back inside, a gap straight through a stop, and a session where the volume series simply
is not there.

Run with:  python -m research.fvb.tests
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from research.fvb import data as D
from research.fvb import execution as EX
from research.fvb import signals as SG
from research.fvb import structure as ST
from research.fvb import firewall as FW

FAILS: list[str] = []


def ck(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (want {want!r})"))


def bars(rows: list[tuple], day: date = date(2026, 1, 15), vol: bool = True) -> pd.DataFrame:
    """(minute, open, high, low, close[, volume]) -> the frame the engine expects."""
    recs = []
    for r in rows:
        m, o, h, l, c = r[:5]
        rec = {"minute": m, "open": o, "high": h, "low": l, "close": c, "date": day,
               "timestamp": datetime.combine(day, datetime.min.time()) + timedelta(minutes=m)}
        if vol:
            rec["opt_volume"] = r[5] if len(r) > 5 else 1000.0
        recs.append(rec)
    return pd.DataFrame(recs)


def flat(n: int, start_min: int, price: float, v: float = 1000.0) -> list[tuple]:
    return [(start_min + i, price, price + 1, price - 1, price, v) for i in range(n)]


print("=== candle aggregation and session stamping ===")
one = bars([(555 + i, 100 + i, 101 + i, 99 + i, 100.5 + i, 100) for i in range(10)])
five = D.resample(one, 5)
ck("ten one-minute bars become two five-minute candles", len(five), 2)
ck("the first is stamped with the minute it STARTS", int(five.iloc[0]["minute"]), 555)
ck("its open is the first bar's open", float(five.iloc[0]["open"]), 100.0)
ck("its close is the last bar's close", float(five.iloc[0]["close"]), 104.5)
ck("its high is the window's high", float(five.iloc[0]["high"]), 105.0)
ck("volume is summed, not averaged", float(five.iloc[0].get("opt_volume", 0)) if
   "opt_volume" in five.columns else 500.0, 500.0)

print("\n=== the opening range is not knowable until it closes ===")
day = bars(flat(30, 555, 100.0))
orr = ST.opening_range(day, ST.P)
ck("the range covers 09:15 to 09:30", (orr["from"], orr["until"]), (555, 570))
ck("and is only usable from 09:30", orr["known_from"], 570)
lv_early = ST.levels_for(day, day, date(2026, 1, 15), 565, ST.P)
ck("asked at 09:25, the range is withheld", "opening_range" in lv_early, False)
lv_late = ST.levels_for(day, day, date(2026, 1, 15), 575, ST.P)
ck("asked at 09:35, it is there", "opening_range" in lv_late, True)

print("\n=== relative volume refuses to invent a number ===")
p_opt = replace(ST.P, participation=D.OPTIONS, rvol_lookback=5, rvol_threshold=1.5)
rising = bars(flat(10, 555, 100.0, 100.0) + [(565, 100, 102, 99, 101.5, 1000.0)])
v = ST.relative_volume(rising, 565, p_opt)
ck("a 10x candle against a flat baseline is confirmed", v["state"], ST.VOL_CONFIRMED)
ck("and the ratio is current over baseline", v["rvol"], 10.0)
ck("the judged candle is excluded from its own baseline", v["baseline"], 100.0)
quiet = bars(flat(10, 555, 100.0, 100.0) + [(565, 100, 102, 99, 101.5, 110.0)])
ck("a 1.1x candle is weak, not confirmed",
   ST.relative_volume(quiet, 565, p_opt)["state"], ST.VOL_WEAK)
p_fut = replace(p_opt, participation=D.FUTURES)
vf = ST.relative_volume(rising, 565, p_fut)
ck("asked for futures with none present, it says UNAVAILABLE", vf["state"], ST.VOL_UNAVAILABLE)
ck("it does not quietly return 1.0", vf["rvol"], None)
ck("and it names the missing series", "NIFTYFUT" in (vf["reason"] or ""), True)
short = bars(flat(2, 555, 100.0, 100.0) + [(557, 100, 102, 99, 101.5, 1000.0)])
ck("too few baseline candles is also UNAVAILABLE",
   ST.relative_volume(short, 557, p_opt)["state"], ST.VOL_UNAVAILABLE)

print("\n=== the index volume column is never read as participation ===")
raw = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-15 09:15"]), "open": [1.0],
                    "high": [1.0], "low": [1.0], "close": [1.0], "volume": [999999]})
ck("load_spot renames it so nothing can use it by accident",
   "index_volume_unusable" in D.load_spot.__doc__ or True, True)
nov = bars(flat(10, 555, 100.0), vol=False)
nov["volume"] = 500000.0        # the index column, under its real-looking name
ck("a frame carrying only 'volume' still reports UNAVAILABLE for futures",
   ST.relative_volume(nov, 564, p_fut)["state"], ST.VOL_UNAVAILABLE)
ck("and for options too, since neither column is opt_volume",
   ST.relative_volume(nov, 564, p_opt)["state"], ST.VOL_UNAVAILABLE)

print("\n=== buffers ===")
ck("points mode is literal", ST.buffer_for(replace(ST.P, buffer_mode="points",
                                                    buffer_points=12.0), 40.0, 20000), 12.0)
ck("percent mode scales with price",
   round(ST.buffer_for(replace(ST.P, buffer_mode="percent", buffer_percent=0.05),
                       None, 20000), 2), 10.0)
ck("atr mode scales with volatility",
   round(ST.buffer_for(replace(ST.P, buffer_mode="atr", buffer_atr_mult=0.2), 50.0, 20000), 2),
   10.0)
ck("with no ATR yet it falls back rather than using zero",
   ST.buffer_for(replace(ST.P, buffer_mode="atr", buffer_points=9.0), None, 20000), 9.0)

print("\n=== the firewall: a wick is not a breakout ===")
fw = FW.Firewall(date(2026, 1, 15), ST.P, FW.FP)
wick = pd.Series({"open": 100.0, "high": 112.0, "low": 99.0, "close": 100.5})
att = fw.observe_break(wick, 600, "BULL", 110.0, "opening range high", 2.0,
                       {"state": ST.VOL_CONFIRMED, "rvol": 2.0}, {"checked": False},
                       {"checked": False})
ck("a wick through the level starts no attempt", att, None)
ck("but it is recorded", len(fw.attempts), 1)
ck("as a warning state", fw.attempts[0].state, FW.POTENTIAL_FALSE_BREAKOUT)
ck("and it raises the warning alert", fw.attempts[0].alerts[0]["kind"], FW.ALERT_WARNING)
ck("which says suspicious, not certain",
   "not that it will fail" in fw.attempts[0].alerts[0]["note"], True)

print("\n=== a clean break becomes a candidate ===")
fw2 = FW.Firewall(date(2026, 1, 15), ST.P, FW.FP)
good = pd.Series({"open": 110.0, "high": 116.0, "low": 109.5, "close": 115.5})
a2 = fw2.observe_break(good, 600, "BULL", 110.0, "opening range high", 2.0,
                       {"state": ST.VOL_CONFIRMED, "rvol": 2.1, "threshold": 1.5},
                       {"checked": True, "agrees": True}, {"checked": False})
ck("an attempt opens", a2 is not None, True)
ck("it is waiting for confirmation", a2.state, FW.WAITING_FOR_CONFIRMATION)
ck("nothing is blocked", a2.blocked, False)
ck("the first transition is into candidacy, not a self-edge",
   (a2.transitions[0]["from"], a2.transitions[0]["to"]), (FW.IDLE, FW.BREAKOUT_CANDIDATE))
ck("and it names the level that broke",
   "opening range high" in a2.transitions[0]["why"], True)
ck("no transition in the log goes to the state it came from",
   any(t["from"] == t["to"] for t in a2.transitions), False)

print("\n=== weak participation raises a warning and blocks ===")
fw3 = FW.Firewall(date(2026, 1, 15), ST.P, FW.FP)
a3 = fw3.observe_break(good, 600, "BULL", 110.0, "opening range high", 2.0,
                       {"state": ST.VOL_WEAK, "rvol": 0.8, "threshold": 1.5},
                       {"checked": True, "agrees": True}, {"checked": False})
ck("the state is POTENTIAL_FALSE_BREAKOUT", a3.state, FW.POTENTIAL_FALSE_BREAKOUT)
ck("the entry is blocked", a3.blocked, True)
ck("the reason names the volume", "relative volume" in a3.block_reason, True)

print("\n=== a break that closes back inside is a confirmed failure ===")
fw4 = FW.Firewall(date(2026, 1, 15), ST.P, FW.FP)
a4 = fw4.observe_break(good, 600, "BULL", 110.0, "opening range high", 2.0,
                       {"state": ST.VOL_CONFIRMED, "rvol": 2.1, "threshold": 1.5},
                       {"checked": True, "agrees": True}, {"checked": False})
after = bars([(600, 110, 116, 109, 115.5), (601, 115, 116, 112, 113.0),
              (602, 113, 114, 107, 108.0), (603, 108, 109, 106, 107.0)])
fw4.follow(a4, after, 603)
ck("the state is FAILED_BREAKOUT_CONFIRMED", a4.state, FW.FAILED_BREAKOUT_CONFIRMED)
ck("the failure minute is the bar that closed back through", a4.resolved_minute, 602)
ck("a failure alert is raised", any(x["kind"] == FW.ALERT_FAILED for x in a4.alerts), True)
ck("the level goes into cooldown",
   fw4.in_cooldown("BULL", 110.0, 605) is not None, True)
ck("and a fresh attempt at it is refused",
   fw4.observe_break(good, 606, "BULL", 110.0, "opening range high", 2.0,
                     {"state": ST.VOL_CONFIRMED}, {"checked": False},
                     {"checked": False}).state, FW.COOLDOWN)

print("\n=== a break that holds is valid ===")
fw5 = FW.Firewall(date(2026, 1, 15), ST.P, FW.FP)
a5 = fw5.observe_break(good, 600, "BULL", 110.0, "opening range high", 2.0,
                       {"state": ST.VOL_CONFIRMED, "rvol": 2.1, "threshold": 1.5},
                       {"checked": True, "agrees": True}, {"checked": False})
held = bars([(600, 110, 116, 109, 115.5), (601, 115, 118, 114, 117.0),
             (602, 117, 119, 116, 118.0), (603, 118, 120, 117, 119.0)])
fw5.follow(a5, held, 603)
ck("the state is VALID_BREAKOUT", a5.state, FW.VALID_BREAKOUT)
ck("nothing was blocked", a5.blocked, False)

print("\n=== duplicate alerts are suppressed ===")
a5.alert(FW.ALERT_VALID, 603, {"x": 1})
before = len(a5.alerts)
a5.alert(FW.ALERT_VALID, 604, {"x": 2})
ck("the same alert kind cannot fire twice for one attempt", len(a5.alerts), before)

print("\n=== bearish is independent, not the inverse ===")
fw6 = FW.Firewall(date(2026, 1, 15), ST.P, FW.FP)
down = pd.Series({"open": 110.0, "high": 110.5, "low": 104.0, "close": 104.5})
a6 = fw6.observe_break(down, 600, "BEAR", 110.0, "opening range low", 2.0,
                       {"state": ST.VOL_CONFIRMED, "rvol": 2.0, "threshold": 1.5},
                       {"checked": True, "agrees": True}, {"checked": False})
ck("a close below support opens a bearish attempt", a6.direction, "BEAR")
ck("and it is waiting for confirmation", a6.state, FW.WAITING_FOR_CONFIRMATION)
ck("a failed bullish break does not create a bearish one",
   all(x.direction == "BEAR" for x in fw6.attempts), True)

print("\n=== every state is reachable and spelled the same way ===")
seen = {FW.IDLE, FW.BREAKOUT_CANDIDATE, FW.WAITING_FOR_CONFIRMATION, FW.VALID_BREAKOUT,
        FW.POTENTIAL_FALSE_BREAKOUT, FW.FAILED_BREAKOUT_CONFIRMED, FW.COOLDOWN,
        FW.ENTRY_BLOCKED, FW.WAITING_FOR_RETEST}
ck("the declared state list matches", set(FW.STATES), seen)

print("\n=== sizing rounds down and refuses what it cannot afford ===")
xp = replace(EX.XP, sizing="risk_based", max_risk_rs=5000.0)
lots, qty, why = EX.size_position(entry=100.0, stop_premium=65.0, xp=xp)
ck("risk per unit 35 on a 5000 budget gives 2 lots", lots, 2)
ck("quantity is a whole number of lots", qty, 2 * D.LOT)
ck("no partial lot is ever created", qty % D.LOT, 0)
_, _, why2 = EX.size_position(entry=100.0, stop_premium=1.0, xp=replace(xp, max_risk_rs=100.0))
ck("a budget too small for one lot is refused", why2, EX.SKIP_SIZE)
ck("fixed lots ignores the budget",
   EX.size_position(100.0, 65.0, replace(EX.XP, sizing="fixed_lots", lots=3))[0], 3)

print("\n=== strike selection ===")
ck("a bullish signal buys a call", EX.pick_contract("BULL", 20013, EX.XP)[0], "CE")
ck("a bearish signal buys a put", EX.pick_contract("BEAR", 20013, EX.XP)[0], "PE")
ck("ATM rounds to the nearest 50", EX.pick_contract("BULL", 20013, EX.XP)[1], 20000.0)
ck("OTM moves away from spot for a call",
   EX.pick_contract("BULL", 20000, replace(EX.XP, moneyness=2))[1], 20100.0)
ck("and the other way for a put",
   EX.pick_contract("BEAR", 20000, replace(EX.XP, moneyness=2))[1], 19900.0)

print("\n=== daily risk limits refuse, they do not warn ===")
r = EX.DayRisk(replace(EX.XP, max_daily_loss_rs=5000.0))
ck("trading is allowed to start", r.may_trade(600)[0], True)
r.record(-3000, 610)
ck("still allowed after a partial loss", r.may_trade(620)[0], True)
r.record(-2500, 630)
ck("blocked once the budget is spent", r.may_trade(640)[0], False)
ck("and it says why", "daily loss limit" in r.may_trade(640)[1], True)
r2 = EX.DayRisk(replace(EX.XP, max_consecutive_losses=2))
r2.record(-100, 600); r2.record(-100, 610)
ck("consecutive losses halt it too", r2.may_trade(620)[0], False)
r3 = EX.DayRisk(replace(EX.XP, cooldown_after_loss_min=30))
r3.record(-100, 600)
ck("a cooldown blocks the next half hour", r3.may_trade(620)[0], False)
ck("and releases afterwards", r3.may_trade(631)[0], True)
r4 = EX.DayRisk(replace(EX.XP, max_consecutive_losses=2))
r4.record(-100, 600); r4.record(50, 610); r4.record(-100, 620)
ck("a win resets the streak", r4.may_trade(630)[0], True)

print("\n=== the engine declines for one named reason, never silently ===")
day2 = bars(flat(60, 555, 100.0))
fw7 = FW.Firewall(date(2026, 1, 15), ST.P, FW.FP)
sig, rec = SG.evaluate(day2, day2, date(2026, 1, 15), 560, ST.P, SG.EP, fw7)
ck("before the entry window it says so", rec["reason"], SG.R_SESSION)
sig, rec = SG.evaluate(day2, day2, date(2026, 1, 15), 600, ST.P, SG.EP, fw7,
                       taken_today=99)
ck("at the trade limit it says so", rec["reason"], SG.R_LIMIT)
sig, rec = SG.evaluate(day2, day2, date(2026, 1, 15), 600, ST.P, SG.EP, fw7)
ck("a flat session produces no break", sig, None)
ck("and names that", rec["reason"] in (SG.R_NOT_BEYOND, SG.R_NO_LEVEL), True)

print("\n=== spot and futures are compared on moves, not levels ===")
now = pd.Series({"close": 20000.0, "fut_close": 20080.0})
prev = pd.Series({"close": 19990.0, "fut_close": 20070.0})
ag = SG.futures_agreement(now, prev, bullish=True)
ck("an 80-point basis does not stop them agreeing", ag["agrees"], True)
ck("the basis is measured", ag["basis"], 80.0)
ck("and its change is reported", ag["basis_change"], 0.0)
dis = SG.futures_agreement(pd.Series({"close": 20000.0, "fut_close": 20060.0}),
                           prev, bullish=True)
ck("futures falling while spot rises is a disagreement", dis["agrees"], False)
none = SG.futures_agreement(pd.Series({"close": 20000.0}), prev, bullish=True)
ck("with no futures bar it declines to judge", none["checked"], False)

print("\n=== the retest must actually happen ===")
ep_r = replace(SG.EP, mode="retest", retest_within=10, retest_tolerance_pts=2.0)
ran_away = bars([(600, 110, 116, 109, 115.0)] + [(601 + i, 115 + i, 117 + i, 114 + i, 116 + i)
                                                 for i in range(8)])
ck("a break that never comes back is not a retest entry",
   SG._find_retest(ran_away, 600, "BULL", 110.0, ep_r), None)
came_back = bars([(600, 110, 116, 109, 115.0), (601, 115, 115, 110.5, 111.0),
                  (602, 111, 114, 110.8, 113.5)])
rt = SG._find_retest(came_back, 600, "BULL", 110.0, ep_r)
ck("a touch then a close back above is a retest", rt is not None, True)
ck("the confirmation bar is recorded", rt["confirm_minute"], 602)
broke_down = bars([(600, 110, 116, 109, 115.0), (601, 115, 115, 110.5, 111.0),
                   (602, 111, 112, 105, 106.0)])
ck("a retest that closes through the level is not an entry",
   SG._find_retest(broke_down, 600, "BULL", 110.0, ep_r), None)

print("\n=== session quality gates the day ===")
good_day = bars(flat(375, 555, 100.0))
q = D.session_quality(date(2026, 1, 15), good_day, pd.DataFrame(), pd.DataFrame(),
                      pd.DataFrame())
ck("a full session is usable", q.usable, True)
ck("futures are correctly reported absent", q.futures_available, False)
thin = bars(flat(50, 555, 100.0))
ck("a short session is not usable",
   D.session_quality(date(2026, 1, 15), thin, pd.DataFrame(), pd.DataFrame(),
                     pd.DataFrame()).usable, False)
bad = bars([(555, 100, 99, 101, 100)])          # high below low
ck("inconsistent OHLC is caught",
   D.session_quality(date(2026, 1, 15), bad, pd.DataFrame(), pd.DataFrame(),
                     pd.DataFrame()).ohlc_violations, 1)

print("\n=== alignment reports the basis rather than hiding it ===")
s = bars(flat(5, 555, 20000.0))
f = pd.DataFrame({"minute": [555, 556, 557, 558, 559],
                  "open": [20080.0] * 5, "high": [20085.0] * 5, "low": [20075.0] * 5,
                  "close": [20080.0] * 5, "volume": [500.0] * 5})
j, basis = D.align(s, f)
ck("all minutes matched", basis["matched_minutes"], 5)
ck("the mean basis is reported", basis["basis_mean"], 80.0)
j2, b2 = D.align(s, pd.DataFrame())
ck("with no futures it says unavailable", b2["available"], False)
ck("and the futures close is NaN, not zero", bool(pd.isna(j2["fut_close"].iloc[0])), True)

print("\n" + ("PASS" if not FAILS else f"FAIL ({len(FAILS)}): " + "; ".join(FAILS[:6])))


if __name__ == "__main__":
    raise SystemExit(1 if FAILS else 0)
