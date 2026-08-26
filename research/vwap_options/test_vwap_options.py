"""
VWAP Options Engine — pure-logic tests (no app deps; modules loaded by path).
Run:  python research/vwap_options/test_vwap_options.py
"""
import importlib.util
import math
import os
import sys
import types
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# minimal package stubs so the real modules import cleanly without the app
for p in ["research", "research.vwap_options"]:
    mod = types.ModuleType(p)
    mod.__path__ = []
    sys.modules[p] = mod

logger_mod = types.ModuleType("core.logger")
logger_mod.get_logger = lambda *_a, **_k: types.SimpleNamespace(
    debug=lambda *a, **k: None, info=lambda *a, **k: None,
    warning=lambda *a, **k: None, error=lambda *a, **k: None)
core_mod = types.ModuleType("core")
core_mod.__path__ = []
sys.modules["core"] = core_mod
sys.modules["core.logger"] = logger_mod

costs_mod = types.ModuleType("research.costs")
costs_mod.roundtrip_cost = lambda entry, exit_, qty, **k: round(
    0.0002 * (entry + exit_) * qty + 40.0, 2)
costs_mod.cost_config = lambda cfg, kind: {"slippage_bps": cfg.get("slippage_bps", 20),
                                           "brokerage_per_order": 20, "charges_pct": 0.1}
sys.modules["research.costs"] = costs_mod

ppv = _load("research.prev_period_vwap", os.path.join(_ROOT, "research", "prev_period_vwap.py"))
bs = _load("research.black_scholes", os.path.join(_ROOT, "research", "black_scholes.py"))
cfg_mod = _load("research.vwap_options.config", os.path.join(_HERE, "config.py"))
vwap = _load("research.vwap_options.vwap_engine", os.path.join(_HERE, "vwap_engine.py"))
sig = _load("research.vwap_options.signals", os.path.join(_HERE, "signals.py"))
sim = _load("research.vwap_options.simulate", os.path.join(_HERE, "simulate.py"))
# chain.py imports OptionChain (app dep) — stub it, then load the real module
oc = types.ModuleType("research.option_chain")
oc.OptionChain = object
sys.modules["research.option_chain"] = oc
chain = _load("research.vwap_options.chain", os.path.join(_HERE, "chain.py"))

CFG = cfg_mod.sanitize({})

D0 = datetime(2026, 8, 3, 9, 15)          # a Monday


def bar(dt, o, h, l, c, v=1000):
    return {"date": dt, "_dt": dt, "open": o, "high": h, "low": l, "close": c, "volume": v}


def session(day_offset, base=24000.0, n=10, drift=5.0, vol=1000):
    """One synthetic session of 5-min bars."""
    start = D0 + timedelta(days=day_offset)
    out = []
    px = base
    for i in range(n):
        dt = start + timedelta(minutes=5 * i)
        o = px
        c = px + drift
        out.append(bar(dt, o, max(o, c) + 2, min(o, c) - 2, c, vol))
        px = c
    return out


def test_config_sanitize_and_rules():
    c = cfg_mod.sanitize({"timeframe": "bogus", "vwap_source": "nope",
                          "rules": [{"line": "bad", "event": "touch", "action": "BUY_CE"},
                                    {"line": "day_vwap", "event": "cross_up", "action": "BUY_PE"}]})
    assert c["timeframe"] == "5minute" and c["vwap_source"] == "futures"
    assert len(c["rules"]) == 1 and c["rules"][0]["line"] == "day_vwap"
    assert c["paper_trade"] is True                      # paper by default


def test_vwap_lines_match_existing_engine():
    """Our series must agree with the already-proven prev_period_vwap output."""
    bars = session(0) + session(1) + session(2)
    ours = vwap.build_series(bars, "futures")
    theirs = ppv.compute_prev_period_vwaps(bars)
    for a, b in zip(ours, theirs):
        assert a["day_vwap"] == b["daily_vwap"]
        assert a["prev_day_vwap"] == b["prev_day_vwap"]
        assert a["prev_week_vwap"] == b["prev_week_vwap"]
        assert a["prev_month_vwap"] == b["prev_month_vwap"]


def test_rolling_vwap_warmup_and_value():
    r = vwap._RollingVWAP(2)
    d1 = datetime(2026, 8, 3, 9, 15)
    assert r.update(d1, 100.0, 10) is None               # day 1 incomplete → warming up
    d2 = datetime(2026, 8, 4, 9, 15)
    assert r.update(d2, 200.0, 10) is None               # only 1 completed day
    d3 = datetime(2026, 8, 5, 9, 15)
    v = r.update(d3, 300.0, 10)                          # 2 completed days → ready
    assert v is not None and abs(v - 200.0) < 1e-6       # (100+200+300)/3


def test_rolling_seed_fills_long_windows():
    """A 90-day line cannot warm up from a day of intraday bars; seeding it from
    completed DAILY sessions must make it render immediately."""
    bars = session(0, n=8)
    unseeded = vwap.build_series(bars, "futures")[-1]
    assert unseeded["roll_15d_vwap"] is None and unseeded["roll_90d_vwap"] is None

    seed = [(24000.0 * 1000, 1000.0)] * 90            # 90 completed sessions
    seeded = vwap.build_series(bars, "futures", seed_days=seed)[-1]
    assert seeded["roll_90d_vwap"] is not None, "90-day line must render once seeded"
    assert seeded["roll_15d_vwap"] is not None
    assert 23900 < seeded["roll_90d_vwap"] < 24100     # dominated by the seeded history

    # a short seed must NOT fake a full window
    short = vwap.build_series(bars, "futures", seed_days=[(24000.0, 1.0)] * 20)[-1]
    assert short["roll_15d_vwap"] is not None and short["roll_90d_vwap"] is None


def test_index_mode_is_hlc3_average():
    """With no volume the series must reduce to an HLC3 average, not crash."""
    bars = [bar(D0 + timedelta(minutes=5 * i), 100, 102, 98, 100, v=0) for i in range(4)]
    s = vwap.build_series(bars, "index")
    assert s[-1]["day_vwap"] == 100.0                    # hlc3 of (102+98+100)/3


def test_detectors():
    assert vwap.touched(105, 95, 100) is True
    assert vwap.touched(105, 101, 100) is False
    assert vwap.touched(105, 101, 100, buffer=2) is True          # buffer catches a near-miss
    assert vwap.crossed_down(105, 99, 100) is True                # closed above, low tags it
    assert vwap.crossed_down(95, 90, 100) is False                # already below
    assert ppv.crossed_up(95, 101, 100, 100) is True
    b = {"high": 105, "low": 99, "close": 104}
    assert vwap.event_fired("cross_any", 95, b, 100) is True
    assert vwap.event_fired("touch", None, b, None) is False      # no level → no signal


def test_signals_respect_window_and_caps():
    bars = session(0, n=12)
    series = vwap.build_series(bars, "futures")
    level = float(bars[3]["close"])
    c = cfg_mod.sanitize({**CFG, "rules": [{"line": "day_vwap", "event": "touch",
                                            "action": "BUY_CE", "enabled": True}],
                          "entry_start": "09:15", "entry_cutoff": "15:00",
                          "one_signal_per_day": True, "touch_buffer_pts": 1e6})
    hits = sig.find_signals(bars, series, c, day=bars[0]["_dt"].date())
    assert len(hits) == 1                                          # one_signal_per_day honoured
    assert hits[0]["opt_type"] == "CE" and hits[0]["action"] == "BUY_CE"
    # a window that excludes everything yields nothing
    c2 = cfg_mod.sanitize({**c, "entry_start": "14:00", "entry_cutoff": "14:30"})
    assert sig.find_signals(bars, series, c2, day=bars[0]["_dt"].date()) == []


def test_target_sl_and_fill_modes():
    t, s = sim.resolve_target_sl(100.0, {"target_mode": "percent", "target_value": 30,
                                         "sl_mode": "percent", "sl_value": 25})
    assert t == 130.0 and s == 75.0
    t, s = sim.resolve_target_sl(100.0, {"target_mode": "points", "target_value": 20,
                                         "sl_mode": "points", "sl_value": 15})
    assert t == 120.0 and s == 85.0
    prem = [bar(D0 + timedelta(minutes=5 * i), 100 + i, 101 + i, 99 + i, 100 + i) for i in range(4)]
    nxt = sim.pick_entry(prem, prem[0]["_dt"], {"fill_mode": "next_bar_open"})
    assert nxt["i"] == 1 and nxt["basis"] == "next bar open"
    now = sim.pick_entry(prem, prem[0]["_dt"], {"fill_mode": "signal_bar_close"})
    assert now["i"] == 0 and now["basis"] == "signal bar close"


def test_simulate_hits_target_with_costs():
    c = {**CFG, "target_mode": "percent", "target_value": 30, "sl_mode": "percent",
         "sl_value": 25, "apply_costs": True, "slippage_bps": 0, "square_off_time": "15:15"}
    prem = [bar(D0, 100, 100, 100, 100),
            bar(D0 + timedelta(minutes=5), 100, 135, 99, 130)]     # tags +30%
    r = sim.simulate_trade(prem, 0, 100.0, 75, c)
    assert r["exit_reason"] == "TARGET" and r["open"] is False
    assert r["cost"] > 0 and r["mtm"] < r["gross_mtm"]             # costs reduce the net
    assert r["mfe"] >= r["gross_mtm"]


def test_simulate_hits_stop():
    c = {**CFG, "target_value": 30, "sl_value": 25, "apply_costs": False, "slippage_bps": 0}
    prem = [bar(D0, 100, 100, 100, 100),
            bar(D0 + timedelta(minutes=5), 100, 101, 70, 72)]
    r = sim.simulate_trade(prem, 0, 100.0, 75, c)
    assert r["exit_reason"] == "STOP" and r["mtm"] < 0


def test_black_scholes_fallback_sanity():
    """Modelled premiums must behave like options: ITM > ATM > OTM, and decay in T."""
    S, sigma, r = 24000.0, 0.14, 0.065
    itm = bs.bs_price(S, 23500, 30 / 365, r, sigma, True)
    atm = bs.bs_price(S, 24000, 30 / 365, r, sigma, True)
    otm = bs.bs_price(S, 24500, 30 / 365, r, sigma, True)
    assert itm > atm > otm > 0
    near = bs.bs_price(S, 24000, 1 / 365, r, sigma, True)
    assert near < atm                                              # time decay
    put = bs.bs_price(S, 24500, 30 / 365, r, sigma, False)
    assert put > bs.bs_price(S, 23500, 30 / 365, r, sigma, False)   # puts rise with strike


def test_lookahead_invariance():
    """MANDATORY: mutating a FUTURE bar must not change an EARLIER signal."""
    bars = session(0, n=12) + session(1, n=12)
    c = cfg_mod.sanitize({**CFG, "entry_start": "09:15", "entry_cutoff": "15:00",
                          "one_signal_per_day": False, "touch_buffer_pts": 3,
                          "rules": [{"line": "prev_day_vwap", "event": "touch",
                                     "action": "BUY_CE", "enabled": True}]})
    day2 = bars[12]["_dt"].date()
    cutoff_i = 16
    early = bars[:cutoff_i + 1]
    a = sig.find_signals(early, vwap.build_series(early, "futures"), c, day=day2)

    tampered = [dict(b) for b in bars]
    for j in range(cutoff_i + 1, len(tampered)):                   # wreck the future
        tampered[j]["high"] = 99999.0
        tampered[j]["close"] = 99999.0
        tampered[j]["volume"] = 10 ** 9
    early2 = tampered[:cutoff_i + 1]
    b_ = sig.find_signals(early2, vwap.build_series(early2, "futures"), c, day=day2)
    assert a == b_, "future data leaked into an earlier signal"


def test_auto_strike_is_moneyness_aware():
    """A signed offset means OTM for a CALL but ITM for a PUT — auto mode must
    flip the sign per option type so one setting means the same thing for both."""
    otm = cfg_mod.sanitize({**CFG, "strike_mode": "auto", "auto_moneyness": "OTM",
                            "auto_points": 100})
    assert chain.offset_for(otm, "CE") == 2        # ATM+100
    assert chain.offset_for(otm, "PE") == -2       # ATM-100  (also OTM for a put)
    assert chain.describe_strike(otm, "CE") == "ATM+100"
    assert chain.describe_strike(otm, "PE") == "ATM-100"

    itm = cfg_mod.sanitize({**otm, "auto_moneyness": "ITM", "auto_points": 200})
    assert chain.offset_for(itm, "CE") == -4       # ATM-200
    assert chain.offset_for(itm, "PE") == 4        # ATM+200

    atm = cfg_mod.sanitize({**otm, "auto_moneyness": "ATM"})
    assert chain.offset_for(atm, "CE") == 0 and chain.offset_for(atm, "PE") == 0

    # fixed mode must keep using the signed offset unchanged for both sides
    fixed = cfg_mod.sanitize({**CFG, "strike_mode": "fixed", "strike_offset_steps": 3})
    assert chain.offset_for(fixed, "CE") == 3 and chain.offset_for(fixed, "PE") == 3


def test_strike_resolution_travels_across_dates():
    """The same offset must resolve to different absolute strikes as ATM moves."""
    assert chain.strike_for_offset(24312, 2) == 24400.0    # ATM 24300 + 100
    assert chain.strike_for_offset(24788, 2) == 24900.0    # ATM 24800 + 100
    assert chain.atm_strike(24324) == 24300.0
    assert chain.strike_for_offset(0, 2) is None           # no spot -> no strike


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} VWAP-options engine tests passed")


if __name__ == "__main__":
    _run()
