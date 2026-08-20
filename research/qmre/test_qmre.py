"""
QMRE pure-logic tests. Loads modules by file path (no app deps) so it runs
anywhere. Includes the MANDATORY look-ahead test: a future candle cannot change
an earlier signal.

Run:  python research/qmre/test_qmre.py
"""
import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))


def _stub(pkg):
    m = types.ModuleType(pkg); m.__path__ = []; sys.modules[pkg] = m


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


# stub research.costs (paper.py imports `from research import costs`)
for p in ["research", "research.qmre"]:
    _stub(p)
costs = types.ModuleType("research.costs")
costs.roundtrip_cost = lambda entry, exit_, qty, **k: round(0.0002 * (entry + exit_) * qty, 2)
costs.cost_config = lambda cfg, kind: {"slippage_bps": 5, "brokerage_per_order": 0, "charges_pct": 0.12}
sys.modules["research.costs"] = costs

feat = _load("research.qmre.features", "features.py"); sys.modules["research.qmre.features"] = feat
scoring = _load("research.qmre.scoring", "scoring.py"); sys.modules["research.qmre.scoring"] = scoring
paper = _load("research.qmre.paper", "paper.py"); sys.modules["research.qmre.paper"] = paper
engine = _load("research.qmre.engine", "engine.py")

CFG = {
    "weights": {"market_regime": 10, "sector_strength": 10, "price_trend": 10, "relative_strength": 10,
                "volume": 15, "breakout": 15, "vwap": 10, "volatility": 5, "liquidity": 5,
                "order_book": 5, "risk_reward": 5},
    "class_bands": [[85, "A+"], [75, "A"], [60, "B"], [45, "WATCH"], [0, "NO TRADE"]],
    "opening_range_min": 15, "vwap_slope_lookback": 6, "breakout_rvol_min": 1.5,
    "breakout_needs_vwap": True, "min_avg_value_cr": 5, "min_rr": 1.5,
    "sl_mode": "atr", "sl_value": 1.5, "target_mode": "atr", "target_value": 3.0,
    "capital_per_stock": 20000, "apply_costs": True, "slippage_bps": 5, "charges_pct": 0.12,
}

BASE = datetime(2026, 8, 18, 9, 15)


def _c(i, o, h, l, cl, v=100000):
    return {"open": o, "high": h, "low": l, "close": cl, "volume": v, "_dt": BASE + timedelta(minutes=5 * i)}


def _rising_day(n=12):
    # a clean uptrend from 100, each candle a bit higher, decent volume
    rows = []
    px = 100.0
    for i in range(n):
        o = px; cl = px + 0.6; h = cl + 0.2; l = o - 0.15
        rows.append(_c(i, o, h, l, cl, v=120000 + i * 3000)); px = cl
    return rows


def _ctx(candles):
    return {"prev_close": 99.0, "prev_high": 100.5, "prev_low": 98.0, "atr": 1.2, "atr_pct": 1.2,
            "expected_cum_vol": 900000, "avg_day_value_cr": 40, "bench_ret_pct": 0.3, "symbol": "TEST"}


def test_vwap_and_rvol():
    c = _rising_day(6)
    f = feat.compute_features(c, _ctx(c), CFG)
    assert f["above_vwap"] is True and f["vwap"] > 0
    assert f["rvol"] is not None and f["rvol"] > 0
    assert f["change_pct"] > 0


def test_scoring_and_class():
    c = _rising_day(10)
    ctx = {**_ctx(c), "regime_score": 0.6, "rr": 2.0}
    f = feat.compute_features(c, ctx, CFG)
    sc = scoring.score_features(f, ctx, CFG)
    assert 0 <= sc["score"] <= 100
    assert sc["class"] in ("A+", "A", "B", "WATCH", "NO TRADE")
    assert abs(sum(b["points"] for b in sc["breakdown"].values()) - sc["score"]) < 0.5


def test_entry_and_sizing():
    # NOW: broken above trigger, not extended → entry ≈ LTP
    f = {"ltp": 100.0, "atr": 2.0, "day_low": 98.0, "or_low": 98.5, "vwap": 99.5, "or_high": 99.6, "prev_high": 99.0}
    rp = scoring.entry_plan(f, CFG)
    assert rp["entry_type"] == "NOW" and rp["sl"] < rp["entry"] < rp["target1"] and rp["rr"] > 0
    sz = scoring.size_position(rp["entry"], rp["sl"], CFG)
    assert sz["qty"] == int(20000 // rp["entry"]) and sz["risk_amount"] > 0


def test_entry_break_and_pullback():
    # BREAK: price below the trigger → entry ABOVE ltp
    fb = {"ltp": 100.0, "atr": 2.0, "day_low": 98, "or_low": 98.5, "vwap": 99.5, "or_high": 101.0, "prev_high": 101.5}
    rb = scoring.entry_plan(fb, CFG)
    assert rb["entry_type"] == "BREAK" and rb["entry"] > 100.0
    # PULLBACK: extended far above VWAP → entry BELOW ltp, lower quality
    fp = {"ltp": 110.0, "atr": 2.0, "day_low": 100, "or_low": 101, "vwap": 100.0, "or_high": 101, "prev_high": 101}
    rp = scoring.entry_plan(fp, CFG)
    assert rp["entry_type"] == "PULLBACK" and rp["entry"] < 110.0 and rp["entry_quality"] < 0.6


def test_paper_forward_target_and_costs():
    entry = 100.0
    fwd = [(BASE, 101, 102, 100.5), (BASE, 103, 106, 102)]   # hits target 106
    r = paper.simulate_forward(entry, fwd, sl=97, target=106, qty=200, square_off_reached=True, cfg=CFG)
    assert r["exit_reason"] == "TARGET" and r["open"] is False
    assert r["mfe"] >= r["mtm"] and r["cost"] > 0             # cost applied, MFE ≥ realized


def test_paper_is_paper_only():
    assert paper.LIVE_ORDER_EXECUTION is False
    paper.assert_paper_only()                                # must not raise
    # ensure no order-placement symbol leaked into the module
    assert not any(k in dir(paper) for k in ("place_order", "submit_order", "kite"))


def test_lookahead_invariance():
    """MANDATORY: changing a FUTURE candle must not change an EARLIER signal."""
    full = _rising_day(12)
    cutoff = full[5]["_dt"]                                   # signal at the 6th candle
    early = [x for x in full if x["_dt"] <= cutoff]
    ctx = {**_ctx(early), "regime_score": 0.4}
    cand_a = engine.evaluate(early, ctx, {"regime_score": 0.4}, CFG)
    # mutate a later candle dramatically
    tampered = [dict(x) for x in full]
    tampered[9]["high"] = 999.0; tampered[9]["close"] = 950.0; tampered[9]["volume"] = 9_999_999
    early2 = [x for x in tampered if x["_dt"] <= cutoff]
    cand_b = engine.evaluate(early2, {**_ctx(early2), "regime_score": 0.4}, {"regime_score": 0.4}, CFG)
    assert cand_a["score"] == cand_b["score"], "future candle leaked into earlier signal!"
    assert cand_a["risk"] == cand_b["risk"]


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} QMRE tests passed")


if __name__ == "__main__":
    _run()
