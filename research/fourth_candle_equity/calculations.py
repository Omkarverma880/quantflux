"""
Pure calculations for the 4th-Candle CASH-EQUITY Strategy.

Reuses the shared 4th-candle signal logic (candle colours, 4th-candle levels,
breakout) and adds direction-aware equity trade simulation (LONG or SHORT the
stock, target/SL on the stock price, direction-aware max profit / max loss).
"""
from __future__ import annotations

from research.fourth_candle.calculations import (   # shared, already unit-tested
    candle_color, day_candles, analyze_day, find_breakout, _parse_hhmm,
)

__all__ = ["candle_color", "day_candles", "analyze_day", "find_breakout", "_parse_hhmm",
           "direction_for", "resolve_target_sl", "position_qty", "simulate_equity"]


def direction_for(bias: str) -> str:
    """CALL bias → LONG (buy the stock); PUT bias → SHORT (sell the stock)."""
    return "long" if bias == "call" else "short"


def resolve_target_sl(entry: float, direction: str, cfg: dict) -> tuple[float, float]:
    """Target/SL on the stock price. LONG → target above, stop below; SHORT →
    target below, stop above. Percent or points."""
    tv, sv = float(cfg["target_value"]), float(cfg["sl_value"])
    t_pts = cfg.get("target_mode") == "points"
    s_pts = cfg.get("sl_mode") == "points"
    if direction == "long":
        target = entry + tv if t_pts else entry * (1.0 + tv / 100.0)
        stop = entry - sv if s_pts else entry * (1.0 - sv / 100.0)
    else:                                   # short
        target = entry - tv if t_pts else entry * (1.0 - tv / 100.0)
        stop = entry + sv if s_pts else entry * (1.0 + sv / 100.0)
    return round(target, 2), round(max(stop, 0.0), 2)


def position_qty(cfg: dict, entry: float) -> int:
    if cfg.get("qty_mode") == "fixed":
        return max(0, int(cfg.get("fixed_qty", 1)))
    cap = float(cfg.get("capital_per_trade", 0) or 0)
    return int(cap // entry) if entry > 0 else 0


def simulate_equity(entry: float, forward: list[tuple], *, direction: str, target: float,
                    stop: float, qty: int, square_off_reached: bool = True) -> dict:
    """Directional stock trade. ``forward`` = [(dt, close, high, low), …] after entry.

    LONG:  exit when HIGH ≥ target (TARGET) or LOW ≤ stop (STOP).
    SHORT: exit when LOW ≤ target (TARGET) or HIGH ≥ stop (STOP).
    Else square off at the last candle (if the horizon has passed) or stay OPEN.
    P&L, max profit and max loss are all direction-aware."""
    qty = int(qty or 0)
    long = direction == "long"
    sign = 1 if long else -1
    mx = mn = entry
    exit_dt = exit_px = reason = None
    last = None
    for row in forward:
        dt, c = row[0], row[1]
        h = row[2] if len(row) > 2 else c
        l = row[3] if len(row) > 3 else c
        if c is None:
            continue
        mx, mn = max(mx, h), min(mn, l)
        last = (dt, c)
        if long:
            if target and h >= target:
                exit_dt, exit_px, reason = dt, target, "TARGET"; break
            if stop and l <= stop:
                exit_dt, exit_px, reason = dt, stop, "STOP"; break
        else:
            if target and l <= target:
                exit_dt, exit_px, reason = dt, target, "TARGET"; break
            if stop and h >= stop:
                exit_dt, exit_px, reason = dt, stop, "STOP"; break
    if reason is None and square_off_reached and last:
        exit_dt, exit_px, reason = last[0], last[1], "SQUAREOFF"

    if long:
        max_profit = round((mx - entry) * qty, 2)
        max_loss = round((mn - entry) * qty, 2)
    else:
        max_profit = round((entry - mn) * qty, 2)
        max_loss = round((entry - mx) * qty, 2)

    if reason is not None:
        return {"exit": round(exit_px, 2), "exit_dt": exit_dt, "exit_reason": reason,
                "mtm": round((exit_px - entry) * qty * sign, 2), "max_profit": max_profit,
                "max_loss": max_loss, "open": False}
    cur = last[1] if last else entry
    return {"exit": None, "exit_dt": None, "exit_reason": "OPEN",
            "mtm": round((cur - entry) * qty * sign, 2), "max_profit": max_profit,
            "max_loss": max_loss, "open": True}
