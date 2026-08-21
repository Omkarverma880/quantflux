"""
MIH pure-logic tests (no app deps — loads modules by path).
Run:  python research/mih/test_mih.py
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


for p in ["research", "research.mih"]:
    mod = types.ModuleType(p)
    mod.__path__ = []
    sys.modules[p] = mod

sectors = _load("research.mih.sectors", "sectors.py")
scanners = _load("research.mih.scanners", "scanners.py")
scoring = _load("research.mih.scoring", "scoring.py")
ideas = _load("research.mih.ideas", "ideas.py")

CFG = {
    "open_eq_tol_pct": 0.15, "gap_pct": 1.0, "vol_shocker_rvol": 3.0,
    "breakout_rvol": 2.0, "breakout_change_pct": 2.0, "near_52w_pct": 1.0,
    "score_weights": {"trend": 25, "momentum": 25, "volume": 20, "vwap": 15, "range": 15},
    "idea_min_score": 6.5, "idea_sl_atr": 1.5, "idea_target_atr": 3.0,
}


def row(**kw):
    base = {"symbol": "TEST", "sector": "Other", "ltp": 100.0, "open": 99.0, "high": 101.0,
            "low": 98.5, "prev_close": 98.0, "change_pct": 2.04, "volume": 500000,
            "rvol": 2.5, "vwap": 99.5, "high_52w": 105.0, "low_52w": 70.0,
            "high_20d": 99.8, "low_20d": 92.0, "atr": 2.0}
    base.update(kw)
    return base


def test_sector_map():
    assert sectors.sector_of("RELIANCE") == "Energy"
    assert sectors.sector_of("HDFCBANK") == "Banking"
    assert sectors.sector_of("NOTALISTED") == "Other"       # safe fallback, never invented


def test_open_equal_to_low():
    hit = row(open=98.6, low=98.5, change_pct=1.5)          # open ≈ low, up
    miss = row(open=101.0, low=98.5)
    r = scanners.run_scanner("open_eq_low", [hit, miss], CFG)
    assert r["count"] == 1 and r["rows"][0] is hit and r["direction"] == "bullish"


def test_open_equal_to_high_is_bearish():
    hit = row(open=100.9, high=101.0, change_pct=-1.2)
    r = scanners.run_scanner("open_eq_high", [hit], CFG)
    assert r["count"] == 1 and r["direction"] == "bearish"


def test_gap_and_volume_screens():
    gap = row(open=100.0, prev_close=98.0)                  # +2.04% gap
    assert scanners.run_scanner("gap_up", [gap], CFG)["count"] == 1
    assert scanners.run_scanner("vol_shocker", [row(rvol=3.4)], CFG)["count"] == 1
    assert scanners.run_scanner("vol_shocker", [row(rvol=1.2)], CFG)["count"] == 0


def test_price_volume_breakout_needs_all_conditions():
    good = row(change_pct=3.0, rvol=2.6, ltp=100.0, high_20d=99.0)
    weak_vol = row(change_pct=3.0, rvol=1.1, ltp=100.0, high_20d=99.0)
    below = row(change_pct=3.0, rvol=2.6, ltp=98.0, high_20d=99.0)
    r = scanners.run_scanner("price_vol_breakout", [good, weak_vol, below], CFG)
    assert r["count"] == 1 and r["rows"][0]["rvol"] == 2.6


def test_missing_data_is_reported_not_guessed():
    """A screen needing enrichment must exclude rows lacking it and say so."""
    have = row()
    missing = row(symbol="NOENRICH", high_52w=None, rvol=None)
    r = scanners.run_scanner("high_52w", [have, missing], CFG)
    assert r["coverage"]["eligible"] == 1 and r["coverage"]["total"] == 2
    assert r["data_note"] and "1/2" in r["data_note"]


def test_score_bounds_and_breakdown():
    strong = scoring.score_stock(row(ltp=104.9, change_pct=5.0, rvol=3.0), CFG)
    weak = scoring.score_stock(row(ltp=71.0, change_pct=-3.5, rvol=0.4, vwap=75.0,
                                   high=76.0, low=70.5), CFG)
    assert 0 <= weak["score"] < strong["score"] <= 10
    assert strong["grade"] in ("Strong", "Very Strong")
    assert strong["fundamentals_available"] is False       # honest: no fundamentals in feed
    assert set(strong["breakdown"]) == {"trend", "momentum", "volume", "vwap", "range"}


def test_idea_gating_and_levels():
    r = row(ltp=104.0, change_pct=4.0, rvol=3.0, atr=2.0)
    sc = scoring.score_stock(r, CFG)
    idea = ideas.build_idea(r, sc, CFG)
    if sc["score"] >= CFG["idea_min_score"]:
        assert idea and idea["sl"] < idea["entry"] < idea["target"]
        assert idea["rr"] > 0 and 0 <= idea["progress"] <= 100
    # low score never produces an idea
    weak = row(ltp=71.0, change_pct=-3.0, rvol=0.3, vwap=75.0, high=76.0, low=70.5)
    assert ideas.build_idea(weak, scoring.score_stock(weak, CFG), CFG) is None


def test_idea_awaits_entry_when_extended():
    """Far above VWAP → entry must be BELOW price (no chasing)."""
    r = row(ltp=110.0, vwap=100.0, atr=2.0, change_pct=6.0, rvol=3.5,
            high=110.5, low=100.0, high_52w=112.0, low_52w=70.0)
    idea = ideas.build_idea(r, scoring.score_stock(r, CFG), CFG)
    assert idea is not None and idea["entry"] < r["ltp"]
    assert idea["status"] in ("AWAITING ENTRY", "ACTIVE", "TARGET HIT")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} MIH tests passed")


if __name__ == "__main__":
    _run()
