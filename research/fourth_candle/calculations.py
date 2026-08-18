"""
Pure calculation logic for the 4th-Candle Strategy — side-effect free, testable.

  1. classify the first 3 candles (all red → CALL bias, all green → PUT bias, mixed → none)
  2. take the 4th candle's high & low as reference lines
  3. scan later candles for a break above the high (CALL) or below the low (PUT)
  4. buy the ATM option and simulate it to target/SL (on the option premium) or expiry,
     tracking max profit / max loss (intraday high/low) and current MTM.
"""
from __future__ import annotations

from datetime import time as dtime


def _parse_hhmm(s: str) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(15, 20)


def candle_color(c) -> str:
    o, cl = float(c["open"]), float(c["close"])
    return "green" if cl > o else "red" if cl < o else "doji"


def day_candles(candles: list[dict], day) -> list[dict]:
    out = [c for c in candles if c.get("_dt") and c["_dt"].date() == day]
    out.sort(key=lambda c: c["_dt"])
    return out


def analyze_day(candles_day: list[dict], reverse: bool = False) -> dict | None:
    """First-3-candle colours + 4th-candle high/low + the resulting bias.

    Default: 3 red → CALL (break the 4th HIGH), 3 green → PUT (break the 4th LOW).
    When ``reverse`` is set the bias is flipped: 3 red → PUT (break the 4th LOW),
    3 green → CALL (break the 4th HIGH). Everything downstream follows the bias.
    """
    if len(candles_day) < 5:
        return None
    pre = candles_day[:3]
    fourth = candles_day[3]
    colors = [candle_color(c) for c in pre]
    if all(x == "red" for x in colors):
        bias = "call"
    elif all(x == "green" for x in colors):
        bias = "put"
    else:
        bias = None         # mixed → no trade
    if reverse and bias:
        bias = "put" if bias == "call" else "call"
    return {"colors": colors, "fourth_high": round(float(fourth["high"]), 2),
            "fourth_low": round(float(fourth["low"]), 2), "fourth_dt": fourth["_dt"],
            "bias": bias, "reversed": bool(reverse)}


def find_breakout(candles_day: list[dict], analysis: dict, *, entry_cutoff: str) -> dict | None:
    """First candle (from the 5th onward) that breaks the 4th-candle reference."""
    bias = analysis["bias"]
    if not bias:
        return None
    cutoff = _parse_hhmm(entry_cutoff)
    for c in candles_day[4:]:
        dt = c["_dt"]
        if dt.time() > cutoff:
            break
        if bias == "call" and float(c["high"]) >= analysis["fourth_high"]:
            return {"dt": dt, "opt_type": "CE", "trigger": analysis["fourth_high"],
                    "underlying": round(float(c["close"]), 2)}
        if bias == "put" and float(c["low"]) <= analysis["fourth_low"]:
            return {"dt": dt, "opt_type": "PE", "trigger": analysis["fourth_low"],
                    "underlying": round(float(c["close"]), 2)}
    return None


def resolve_target_sl(entry: float, cfg: dict) -> tuple[float, float]:
    """Target/SL prices on the option premium (long option): target above entry,
    stop below entry. Percent or points."""
    if cfg.get("target_mode") == "points":
        target = entry + float(cfg["target_value"])
    else:
        target = entry * (1.0 + float(cfg["target_value"]) / 100.0)
    if cfg.get("sl_mode") == "points":
        stop = entry - float(cfg["sl_value"])
    else:
        stop = entry * (1.0 - float(cfg["sl_value"]) / 100.0)
    return round(target, 2), round(max(stop, 0.0), 2)


def simulate_option(entry: float, forward: list[tuple], *, target: float, stop: float,
                    qty: int, square_off_reached: bool = True) -> dict:
    """Long single option. ``forward`` = [(dt, close, high, low), …] after entry.
    Exit when the premium HIGH ≥ target (TARGET) or LOW ≤ stop (STOP); else square
    off at the last candle (if the expiry square-off has passed) or stay OPEN.
    Max profit uses the highest high, max loss the lowest low."""
    qty = int(qty or 0)
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
        if target and h >= target:
            exit_dt, exit_px, reason = dt, target, "TARGET"
            break
        if stop and l <= stop:
            exit_dt, exit_px, reason = dt, stop, "STOP"
            break
    if reason is None and square_off_reached and last:
        exit_dt, exit_px, reason = last[0], last[1], "SQUAREOFF"

    max_profit = round((mx - entry) * qty, 2)
    max_loss = round((mn - entry) * qty, 2)
    if reason is not None:               # closed
        return {"exit": round(exit_px, 2), "exit_dt": exit_dt, "exit_reason": reason,
                "mtm": round((exit_px - entry) * qty, 2), "max_profit": max_profit,
                "max_loss": max_loss, "open": False}
    cur = last[1] if last else entry     # still OPEN (live, before square-off)
    return {"exit": None, "exit_dt": None, "exit_reason": "OPEN",
            "mtm": round((cur - entry) * qty, 2), "max_profit": max_profit,
            "max_loss": max_loss, "open": True}
