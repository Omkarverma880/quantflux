"""
Pure calculations for the Hammer-at-3/6-Month-Low Breakout Strategy.

A direct port of the Pine study: every measurement is taken on the PREVIOUS
daily candle (the "signal candle") and every percentage is expressed against
that candle's LOW — `lowerWick / sigLow * 100`, exactly as the indicator does.
Long only. Position sizing and the trade simulator are the shared, already
unit-tested equity helpers.
"""
from __future__ import annotations

from typing import Optional

from research.fourth_candle_equity.calculations import (   # shared equity helpers
    position_qty, simulate_equity, _parse_hhmm,
)

__all__ = ["position_qty", "simulate_equity", "_parse_hhmm",
           "candle_metrics", "hammer_at", "breakout_entry", "resolve_target_sl"]


def _f(c: dict, key: str) -> float:
    return float(c[key])


def candle_metrics(c: dict) -> dict:
    """Wick/body geometry of one candle, as percentages of its LOW."""
    o, h, l, cl = _f(c, "open"), _f(c, "high"), _f(c, "low"), _f(c, "close")
    lower_wick = min(o, cl) - l
    upper_wick = h - max(o, cl)
    body = abs(cl - o)
    pct = (lambda v: round(v / l * 100.0, 3)) if l else (lambda v: 0.0)
    return {"open": o, "high": h, "low": l, "close": cl,
            "lower_wick": round(lower_wick, 2), "upper_wick": round(upper_wick, 2),
            "body": round(body, 2), "lower_wick_pct": pct(lower_wick),
            "upper_wick_pct": pct(upper_wick), "body_pct": pct(body),
            "red": cl < o}


def hammer_at(candles: list[dict], i: int, cfg: dict) -> Optional[dict]:
    """Evaluate the 5 setup conditions on ``candles[i]`` (the signal candle).

    ``candles`` must be chronological daily candles. Returns a diagnostic dict
    (every condition individually, plus ``ok``) or ``None`` when there is not
    enough history behind ``i`` to judge the setup.
    """
    lookback = int(cfg["low_lookback"])
    red_n = int(cfg["red_before"])
    if i < 0 or i >= len(candles):
        return None
    if i - lookback + 1 < 0 or i - red_n < 0:
        return None                              # not enough history — undecidable

    m = candle_metrics(candles[i])
    lower_ok = m["lower_wick_pct"] >= float(cfg["lower_wick_min"])
    body_ok = m["body_pct"] < float(cfg["body_max"])
    upper_ok = m["upper_wick_pct"] < float(cfg["upper_wick_max"])

    window = candles[i - lookback + 1: i + 1]    # `lookback` bars ending AT the signal
    lowest = min(_f(c, "low") for c in window)
    low_ok = m["low"] <= lowest

    reds = [candle_metrics(candles[i - k])["red"] for k in range(1, red_n + 1)]
    red_ok = all(reds) if red_n else True

    return {**m, "lowest_low": round(lowest, 2), "lookback": lookback,
            "reds_before": reds, "lower_wick_ok": lower_ok, "body_ok": body_ok,
            "upper_wick_ok": upper_ok, "low_at_extreme": low_ok, "red_ok": red_ok,
            "ok": bool(lower_ok and body_ok and upper_ok and low_ok and red_ok)}


def breakout_entry(day: dict, sig_high: float) -> Optional[float]:
    """Trigger price for the breakout day, or ``None`` when the signal high was
    never taken out. A gap-up opens above the level, so the fill is the open."""
    if float(day["high"]) <= sig_high:
        return None
    return round(max(float(day["open"]), sig_high), 2)


def resolve_target_sl(entry: float, cfg: dict, signal_low: Optional[float] = None) -> tuple[float, float]:
    """Long-only target/stop on the stock price. ``signal_low`` mode parks the
    stop just under the hammer's low — the level the setup is built on."""
    tv, sv = float(cfg["target_value"]), float(cfg["sl_value"])
    target = entry + tv if cfg.get("target_mode") == "points" else entry * (1.0 + tv / 100.0)
    mode = cfg.get("sl_mode")
    if mode == "signal_low" and signal_low:
        stop = float(signal_low)
    elif mode == "points":
        stop = entry - sv
    else:
        stop = entry * (1.0 - sv / 100.0)
    return round(target, 2), round(max(stop, 0.0), 2)
