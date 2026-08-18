"""
Config + thresholds for the Demand-Supply Equity Scanner (Research #12).

Every threshold, scoring weight and score-band is data-driven here so nothing is
hard-coded across the engine. Durable file-backed JSON, mirroring the other
research modules (qmie.json etc.).
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger

logger = get_logger("research.demand_supply.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "demand_supply.json"

# ── scoring-function anchor tables: (metric_value, score 0-100), linearly
#    interpolated. Editing these tunes the normalisation without touching code. ──
RATIO_ANCHORS = [[0.50, 0], [0.80, 10], [1.00, 20], [1.25, 35],
                 [1.50, 50], [2.00, 70], [3.00, 85], [4.00, 100]]
MOMENTUM_ANCHORS = [[-2.0, 0], [-0.5, 20], [0.0, 40], [0.5, 60], [1.5, 85], [3.0, 100]]
RVOL_ANCHORS = [[0.5, 10], [0.75, 30], [1.0, 45], [1.25, 60], [1.5, 75], [2.0, 90], [3.0, 100]]
VWAP_DIST_ANCHORS = [[-1.0, 0], [-0.2, 30], [0.0, 50], [0.2, 70], [1.0, 100]]
BUY_TREND_ANCHORS = [[-20, 0], [-5, 30], [0, 50], [5, 70], [20, 100]]
SELL_TREND_ANCHORS = [[20, 0], [5, 30], [0, 50], [-5, 70], [-20, 100]]  # sell falling → high

DEFAULT_CONFIG: dict = {
    # ── scoring weights (must conceptually total 100) ──
    "weights": {
        "ratio": 20, "imbalance": 20, "momentum": 15, "volume": 15,
        "vwap": 10, "buy_trend": 10, "sell_trend": 10,
    },
    # ── buy/sell ratio interpretation bands (lower-bound → label) ──
    "ratio_bands": [
        [3.00, "Very Strong Demand"], [2.00, "Strong Demand"], [1.50, "Moderate Demand"],
        [1.00, "Slight Demand"], [0.80, "Slight Supply"], [0.50, "Strong Supply"],
        [0.00, "Very Strong Supply"],
    ],
    # ── depth-imbalance interpretation bands (lower-bound %, → label) ──
    "imbalance_bands": [
        [50, "Extreme Buy Pressure"], [30, "Strong Buy Pressure"], [15, "Moderate Buy Pressure"],
        [-15, "Balanced"], [-30, "Moderate Sell Pressure"], [-50, "Strong Sell Pressure"],
        [-100, "Extreme Sell Pressure"],
    ],
    # ── demand-score interpretation bands (lower-bound → [label, emoji]) ──
    "score_bands": [
        [90, "EXTREME DEMAND", "🔥"], [80, "VERY STRONG DEMAND", "🚀"],
        [70, "STRONG DEMAND", "🟢"], [60, "MODERATE DEMAND", "🟢"],
        [45, "NEUTRAL", "⚪"], [30, "MODERATE SUPPLY", "🟠"],
        [20, "STRONG SUPPLY", "🔴"], [0, "EXTREME SUPPLY", "🔻"],
    ],
    # ── relative-volume interpretation (lower-bound → label) ──
    "rvol_bands": [
        [2.00, "Very Strong"], [1.50, "Strong"], [1.25, "Elevated"],
        [0.75, "Normal"], [0.00, "Low participation"],
    ],
    # ── persistence / trend ──
    "persistence_lookback": 5,        # number of snapshots forming the window
    "persistence_weight": 0.10,       # how much low persistence damps the final score (0-1)
    "trend_min_history": 3,           # snapshots needed before building/weakening is decided
    # ── relative volume ──
    "rvol_lookback": 20,              # trading days for the average-volume baseline
    "rvol_fetch_cap": 40,             # max avg-vol historical lookups per scan (rate-limit safety)
    # ── universe / display ──
    "max_stocks": 0,                  # 0 = all in the chosen universe
    "top_n": 10,                      # size of Top Demand / Top Supply lists
    "min_price": 0.0,                 # ignore stocks below this LTP
    "min_volume": 0,                  # ignore stocks below this day volume
    "require_vwap_above": False,      # if true, only keep LTP > VWAP
    "update_interval": 15,            # frontend live-poll seconds (advisory)
    "history_cap": 60,                # rolling snapshots kept per symbol
}

_NUM_INT = {"persistence_lookback", "trend_min_history", "rvol_lookback", "rvol_fetch_cap",
            "max_stocks", "top_n", "min_volume", "update_interval", "history_cap"}
_NUM_FLOAT = {"persistence_weight", "min_price"}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    # weights
    w = dict(DEFAULT_CONFIG["weights"])
    w.update({k: v for k, v in (out.get("weights") or {}).items() if k in w and v is not None})
    for k in w:
        try:
            w[k] = max(0, float(w[k]))
        except (TypeError, ValueError):
            w[k] = DEFAULT_CONFIG["weights"][k]
    out["weights"] = w
    for k in _NUM_INT:
        try:
            out[k] = int(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    for k in _NUM_FLOAT:
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    out["persistence_lookback"] = max(1, min(30, out["persistence_lookback"]))
    out["persistence_weight"] = max(0.0, min(1.0, out["persistence_weight"]))
    out["trend_min_history"] = max(2, min(30, out["trend_min_history"]))
    out["rvol_lookback"] = max(2, min(120, out["rvol_lookback"]))
    out["rvol_fetch_cap"] = max(0, min(200, out["rvol_fetch_cap"]))
    out["top_n"] = max(1, min(100, out["top_n"]))
    out["update_interval"] = max(3, min(120, out["update_interval"]))
    out["history_cap"] = max(5, min(500, out["history_cap"]))
    out["require_vwap_above"] = bool(out["require_vwap_above"])
    # keep the anchor tables available on the config so the engine reads one source
    for k, default in (("ratio_anchors", RATIO_ANCHORS), ("momentum_anchors", MOMENTUM_ANCHORS),
                       ("rvol_anchors", RVOL_ANCHORS), ("vwap_dist_anchors", VWAP_DIST_ANCHORS),
                       ("buy_trend_anchors", BUY_TREND_ANCHORS), ("sell_trend_anchors", SELL_TREND_ANCHORS)):
        anchors = out.get(k) or default
        try:
            out[k] = [[float(a), float(b)] for a, b in anchors]
        except Exception:
            out[k] = [[float(a), float(b)] for a, b in default]
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("demand_supply config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("demand_supply config save failed: %s", exc)
    return cfg
