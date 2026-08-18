"""
Unit tests for the Demand-Supply Scanner pure calculations.
Run:  python research/demand_supply/test_demand_supply.py

Loads calculations.py directly (no app imports) so it runs anywhere, and builds
the config from the real DEFAULT_CONFIG anchors without pulling in core/*.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(mod_name, filename):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


calc = _load("ds_calc", "calculations.py")

# Config mirrored from config.py DEFAULT_CONFIG (kept in sync); avoids importing
# config.py which pulls in core.logger / settings.
CFG = {
    "weights": {"ratio": 20, "imbalance": 20, "momentum": 15, "volume": 15,
                "vwap": 10, "buy_trend": 10, "sell_trend": 10},
    "ratio_bands": [[3.00, "Very Strong Demand"], [2.00, "Strong Demand"], [1.50, "Moderate Demand"],
                    [1.00, "Slight Demand"], [0.80, "Slight Supply"], [0.50, "Strong Supply"],
                    [0.00, "Very Strong Supply"]],
    "imbalance_bands": [[50, "Extreme Buy Pressure"], [30, "Strong Buy Pressure"], [15, "Moderate Buy Pressure"],
                        [-15, "Balanced"], [-30, "Moderate Sell Pressure"], [-50, "Strong Sell Pressure"],
                        [-100, "Extreme Sell Pressure"]],
    "score_bands": [[90, "EXTREME DEMAND", "🔥"], [80, "VERY STRONG DEMAND", "🚀"],
                    [70, "STRONG DEMAND", "🟢"], [60, "MODERATE DEMAND", "🟢"],
                    [45, "NEUTRAL", "⚪"], [30, "MODERATE SUPPLY", "🟠"],
                    [20, "STRONG SUPPLY", "🔴"], [0, "EXTREME SUPPLY", "🔻"]],
    "rvol_bands": [[2.00, "Very Strong"], [1.50, "Strong"], [1.25, "Elevated"],
                   [0.75, "Normal"], [0.00, "Low participation"]],
    "persistence_lookback": 5, "persistence_weight": 0.10, "trend_min_history": 3,
    "ratio_anchors": [[0.50, 0], [0.80, 10], [1.00, 20], [1.25, 35], [1.50, 50], [2.00, 70], [3.00, 85], [4.00, 100]],
    "momentum_anchors": [[-2.0, 0], [-0.5, 20], [0.0, 40], [0.5, 60], [1.5, 85], [3.0, 100]],
    "rvol_anchors": [[0.5, 10], [0.75, 30], [1.0, 45], [1.25, 60], [1.5, 75], [2.0, 90], [3.0, 100]],
    "vwap_dist_anchors": [[-1.0, 0], [-0.2, 30], [0.0, 50], [0.2, 70], [1.0, 100]],
    "buy_trend_anchors": [[-20, 0], [-5, 30], [0, 50], [5, 70], [20, 100]],
    "sell_trend_anchors": [[20, 0], [5, 30], [0, 50], [-5, 70], [-20, 100]],
}


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def test_ratio():
    assert calc.safe_ratio(800000, 250000) == 3.2
    assert calc.safe_ratio(100, 0) == float("inf")     # no sell side
    assert calc.safe_ratio(0, 0) is None               # nothing to define
    assert calc.safe_ratio(0, 100) == 0.0


def test_depth_imbalance():
    assert approx(calc.depth_imbalance(850000, 250000), 0.5454)   # +54.5%
    assert calc.depth_imbalance(0, 0) is None
    assert calc.depth_imbalance(100, 100) == 0.0
    assert calc.depth_imbalance(0, 100) == -1.0


def test_interp_normalisation():
    # exact anchor points
    assert calc.ratio_score(1.0, CFG) == 20
    assert calc.ratio_score(3.0, CFG) == 85
    assert calc.ratio_score(4.0, CFG) == 100
    assert calc.ratio_score(0.4, CFG) == 0          # below range clamps
    # interpolation between 2.0(70) and 3.0(85) at 2.5 → 77.5
    assert approx(calc.ratio_score(2.5, CFG), 77.5)
    assert calc.ratio_score(None, CFG) == 50.0
    assert calc.ratio_score(float("inf"), CFG) == 100.0


def test_imbalance_score():
    assert calc.imbalance_score(1.0) == 100
    assert calc.imbalance_score(-1.0) == 0
    assert calc.imbalance_score(0.0) == 50
    assert calc.imbalance_score(None) == 50


def test_pct_change():
    assert calc.pct_change(800000, 650000) == 23.08
    assert calc.pct_change(100, 0) is None
    assert calc.pct_change(90, 100) == -10.0


def test_classifications():
    assert calc.classify_ratio(3.4, CFG) == "Very Strong Demand"
    assert calc.classify_ratio(0.6, CFG) == "Strong Supply"
    assert calc.classify_imbalance_pct(54.5, CFG) == "Extreme Buy Pressure"
    assert calc.classify_imbalance_pct(-40, CFG) == "Strong Sell Pressure"
    assert calc.interpret_score(94, CFG)[0] == "EXTREME DEMAND"
    assert calc.interpret_score(50, CFG)[0] == "NEUTRAL"
    assert calc.interpret_score(10, CFG)[0] == "EXTREME SUPPLY"


def test_persistence_and_trend():
    hist = [{"ratio": 2.0, "imbalance": 0.4, "score": 60},
            {"ratio": 1.8, "imbalance": 0.3, "score": 64},
            {"ratio": 2.1, "imbalance": 0.5, "score": 70},
            {"ratio": 2.3, "imbalance": 0.6, "score": 78},
            {"ratio": 2.5, "imbalance": 0.7, "score": 85}]
    p = calc.persistence(hist, 5)
    assert p == {"bullish": 5, "n": 5, "fraction": 1.0}
    assert calc.demand_trend(hist, 3) == "DEMAND BUILDING"
    falling = [{"score": 90}, {"score": 86}, {"score": 78}, {"score": 71}]
    assert calc.demand_trend(falling, 3) == "DEMAND WEAKENING"
    assert calc.persistence([], 5)["n"] == 0            # no history safe


def test_compose_full_demand():
    metrics = {
        "ratio": 3.4, "imbalance": 0.545, "change_pct": 1.82, "rvol": 2.3,
        "ltp": 105, "vwap": 100, "buy_change_pct": 20, "sell_change_pct": -20,
        "history": [{"ratio": 3.0, "imbalance": 0.5, "score": 80},
                    {"ratio": 3.2, "imbalance": 0.52, "score": 84},
                    {"ratio": 3.4, "imbalance": 0.545, "score": None}],
        "availability": {"depth": True},
    }
    r = calc.compose_score(metrics, CFG)
    assert r["score"] >= 80, r["score"]
    assert r["signal"] in ("VERY STRONG DEMAND", "EXTREME DEMAND")
    assert abs(sum(b["points"] for b in r["breakdown"].values()) - r["score"] / (
        (1 - CFG["persistence_weight"]) + CFG["persistence_weight"] * r["persistence"]["fraction"])) < 0.5
    assert r["confidence"] >= 70


def test_compose_supply_side():
    metrics = {"ratio": 0.4, "imbalance": -0.6, "change_pct": -1.5, "rvol": 0.6,
               "ltp": 95, "vwap": 100, "buy_change_pct": -20, "sell_change_pct": 20,
               "history": [], "availability": {"depth": True}}
    r = calc.compose_score(metrics, CFG)
    assert r["score"] < 30, r["score"]
    assert "SUPPLY" in r["signal"]


def test_missing_data_neutral():
    r = calc.compose_score({"availability": {"depth": False}}, CFG)   # everything None
    assert 40 <= r["score"] <= 60                # neutral-ish
    assert r["confidence"] < 60                  # low data → low confidence


def test_zero_and_edge_cases():
    assert calc.depth_totals([]) == 0
    assert calc.vwap_score(100, 0, CFG) == 50.0          # missing vwap
    assert calc.volume_score(None, CFG) == 50.0          # missing rvol


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"  ok  {t.__name__}")
    print(f"\n{passed}/{len(tests)} demand-supply calculation tests passed")


if __name__ == "__main__":
    _run()
