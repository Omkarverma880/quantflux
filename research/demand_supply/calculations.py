"""
Pure demand/supply calculations — side-effect free and unit-tested.

The scanner combines several INDEPENDENT normalised signals into a 0-100 Demand
Score (never a raw bid/ask ratio):

    buy/sell ratio · 5-level depth imbalance · price momentum · relative volume ·
    VWAP position · buy-qty trend · sell-qty trend        → weighted score
                                                          → persistence damping
                                                          → signal + confidence

Everything reads its thresholds/anchors from the config dict so behaviour is
tunable without code changes.
"""
from __future__ import annotations

from typing import Optional


# ── primitives ──────────────────────────────────────────────────────────
def safe_ratio(buy: float, sell: float) -> Optional[float]:
    """Buy/Sell quantity ratio. None when it cannot be defined (no sell side)."""
    buy = float(buy or 0)
    sell = float(sell or 0)
    if sell <= 0:
        return None if buy <= 0 else float("inf")
    return round(buy / sell, 4)


def depth_totals(levels: list[dict]) -> float:
    """Sum of the quantity across up to 5 order-book levels."""
    return float(sum(float(l.get("quantity", 0) or 0) for l in (levels or [])[:5]))


def depth_imbalance(buy_depth: float, sell_depth: float) -> Optional[float]:
    """(buy - sell)/(buy + sell) in [-1, +1]. None if both sides empty."""
    b, s = float(buy_depth or 0), float(sell_depth or 0)
    tot = b + s
    if tot <= 0:
        return None
    return round((b - s) / tot, 4)


def pct_change(now: float, prev: float) -> Optional[float]:
    prev = float(prev or 0)
    if prev == 0:
        return None
    return round((float(now or 0) - prev) / prev * 100.0, 2)


def interp(anchors: list, x: Optional[float]) -> float:
    """Piecewise-linear interpolation of x over [[metric, score], …] (sorted by
    metric). Clamps to the end scores outside the range."""
    if x is None:
        return 50.0
    pts = sorted(anchors, key=lambda p: p[0])
    if x <= pts[0][0]:
        return float(pts[0][1])
    if x >= pts[-1][0]:
        return float(pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y1)
            return round(y0 + (y1 - y0) * (x - x0) / (x1 - x0), 2)
    return 50.0


# ── normalised component scores (each 0-100) ────────────────────────────
def ratio_score(ratio: Optional[float], cfg: dict) -> float:
    if ratio is None:
        return 50.0
    if ratio == float("inf"):
        return 100.0
    return interp(cfg["ratio_anchors"], ratio)


def imbalance_score(imb: Optional[float]) -> float:
    """[-1,+1] → [0,100] linearly (0 imbalance = 50)."""
    if imb is None:
        return 50.0
    return round((max(-1.0, min(1.0, imb)) + 1.0) / 2.0 * 100.0, 2)


def momentum_score(change_pct: Optional[float], cfg: dict) -> float:
    return interp(cfg["momentum_anchors"], change_pct)


def volume_score(rvol: Optional[float], cfg: dict) -> float:
    return interp(cfg["rvol_anchors"], rvol)


def vwap_score(ltp: Optional[float], vwap: Optional[float], cfg: dict) -> float:
    if not ltp or not vwap:
        return 50.0
    dist = (ltp - vwap) / vwap * 100.0
    return interp(cfg["vwap_dist_anchors"], dist)


def buy_trend_score(buy_change_pct: Optional[float], cfg: dict) -> float:
    return interp(cfg["buy_trend_anchors"], buy_change_pct)


def sell_trend_score(sell_change_pct: Optional[float], cfg: dict) -> float:
    return interp(cfg["sell_trend_anchors"], sell_change_pct)


# ── interpretation bands ────────────────────────────────────────────────
def _band_label(bands: list, value: Optional[float], default: str = "N/A") -> str:
    if value is None:
        return default
    for lb, *rest in bands:
        if value >= lb:
            return rest[0]
    return bands[-1][1] if bands else default


def classify_ratio(ratio: Optional[float], cfg: dict) -> str:
    if ratio == float("inf"):
        return cfg["ratio_bands"][0][1]
    return _band_label(cfg["ratio_bands"], ratio)


def classify_imbalance_pct(imb_pct: Optional[float], cfg: dict) -> str:
    return _band_label(cfg["imbalance_bands"], imb_pct)


def classify_rvol(rvol: Optional[float], cfg: dict) -> str:
    return _band_label(cfg["rvol_bands"], rvol)


def interpret_score(score: Optional[float], cfg: dict) -> tuple[str, str]:
    if score is None:
        return ("N/A", "")
    for lb, label, emoji in cfg["score_bands"]:
        if score >= lb:
            return (label, emoji)
    last = cfg["score_bands"][-1]
    return (last[1], last[2])


# ── persistence + trend (need a rolling history of snapshots) ───────────
def persistence(history: list[dict], lookback: int) -> dict:
    """Fraction of the last `lookback` snapshots that were bullish
    (ratio>1 AND imbalance>0). Returns count/n and the fraction."""
    window = [h for h in history[-lookback:]]
    n = len(window)
    if n == 0:
        return {"bullish": 0, "n": 0, "fraction": 0.0}
    bullish = sum(1 for h in window
                  if (h.get("ratio") is not None and h["ratio"] > 1.0)
                  and (h.get("imbalance") is not None and h["imbalance"] > 0))
    return {"bullish": bullish, "n": n, "fraction": round(bullish / n, 3)}


def demand_trend(history: list[dict], min_history: int) -> str:
    """Compare the recent half of the score history to the earlier half."""
    scores = [h.get("score") for h in history if h.get("score") is not None]
    if len(scores) < min_history:
        return "STABLE"
    half = max(1, len(scores) // 2)
    early = sum(scores[:half]) / half
    late = sum(scores[-half:]) / half
    diff = late - early
    if diff >= 4:
        return "DEMAND BUILDING"
    if diff <= -4:
        return "DEMAND WEAKENING"
    return "STABLE"


def confirmation_status(history: list[dict], score: Optional[float], min_history: int) -> str:
    """Six-state confirmation, distinct from the raw score."""
    trend = demand_trend(history, min_history)
    strong = score is not None and score >= 60
    weak = score is not None and score <= 40
    if trend == "DEMAND BUILDING":
        return "Demand Confirmed" if strong else "Demand Building"
    if trend == "DEMAND WEAKENING":
        return "Supply Confirmed" if weak else "Demand Weakening"
    if strong:
        return "Demand Confirmed"
    if weak:
        return "Supply Confirmed"
    return "Balanced"


def confidence(flags: dict) -> int:
    """Signal confidence (NOT statistical certainty) from data availability +
    agreement. flags: depth, volume, vwap, price_confirm, depth_confirm,
    persistence_ok (all bool)."""
    score = 40
    score += 15 if flags.get("depth") else 0
    score += 15 if flags.get("volume") else 0
    score += 10 if flags.get("vwap") else 0
    score += 8 if flags.get("price_confirm") else 0
    score += 7 if flags.get("depth_confirm") else 0
    score += 5 if flags.get("persistence_ok") else 0
    return int(max(0, min(100, score)))


# ── the score engine ────────────────────────────────────────────────────
def compose_score(metrics: dict, cfg: dict) -> dict:
    """Combine normalised components into a 0-100 score with a full breakdown.

    metrics keys (any may be None → neutral component):
        ratio, imbalance, change_pct, rvol, ltp, vwap,
        buy_change_pct, sell_change_pct, history (list), availability flags.
    Returns: {score, breakdown, interpretation, signal, confidence,
              persistence, trend, status}.
    """
    w = cfg["weights"]
    comp = {
        "ratio": (ratio_score(metrics.get("ratio"), cfg), w["ratio"]),
        "imbalance": (imbalance_score(metrics.get("imbalance")), w["imbalance"]),
        "momentum": (momentum_score(metrics.get("change_pct"), cfg), w["momentum"]),
        "volume": (volume_score(metrics.get("rvol"), cfg), w["volume"]),
        "vwap": (vwap_score(metrics.get("ltp"), metrics.get("vwap"), cfg), w["vwap"]),
        "buy_trend": (buy_trend_score(metrics.get("buy_change_pct"), cfg), w["buy_trend"]),
        "sell_trend": (sell_trend_score(metrics.get("sell_change_pct"), cfg), w["sell_trend"]),
    }
    total_weight = sum(weight for _, weight in comp.values()) or 1.0
    breakdown = {}
    raw = 0.0
    for name, (cscore, weight) in comp.items():
        pts = cscore / 100.0 * weight
        raw += pts
        breakdown[name] = {"points": round(pts, 1), "max": round(weight, 1),
                           "component_score": round(cscore, 1)}
    # normalise to 0-100 in case weights don't total exactly 100
    base = raw / total_weight * 100.0

    history = metrics.get("history") or []
    pers = persistence(history, cfg["persistence_lookback"])
    # persistence damping: a single bullish snapshot can't earn full marks
    pw = float(cfg["persistence_weight"])
    factor = (1.0 - pw) + pw * pers["fraction"] if history else (1.0 - pw)
    score = round(base * factor, 1)

    signal, emoji = interpret_score(score, cfg)
    trend = demand_trend(history, cfg["trend_min_history"])
    status = confirmation_status(history, score, cfg["trend_min_history"])

    change_pct = metrics.get("change_pct")
    imb = metrics.get("imbalance")
    flags = {
        "depth": bool(metrics.get("availability", {}).get("depth")),
        "volume": metrics.get("rvol") is not None,
        "vwap": bool(metrics.get("vwap")),
        "price_confirm": (change_pct is not None and imb is not None
                          and ((change_pct > 0) == (imb > 0))),
        "depth_confirm": imb is not None and abs(imb) > 0.15,
        "persistence_ok": pers["n"] >= cfg["persistence_lookback"] and pers["fraction"] >= 0.6,
    }
    conf = confidence(flags)

    return {
        "score": score, "breakdown": breakdown,
        "interpretation": signal, "emoji": emoji, "signal": signal,
        "confidence": conf, "persistence": pers, "trend": trend, "status": status,
    }
