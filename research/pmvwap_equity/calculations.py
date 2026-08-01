"""
Pure calculation logic for the Prev-Month-VWAP Equity-Holding Research.

Entry-signal detection (price meets Prev-Month VWAP while Prev-Week VWAP is
above it — the "green above purple" setup) and a multi-day holding simulation
with target / stop / max-hold exit. Side-effect free and unit-testable.
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Optional

from research.prev_period_vwap import crossed_up
from research.pmvwap_equity.constants import (
    ENTRY_CROSS_UP, EXIT_TARGET, EXIT_STOP, EXIT_MAXHOLD, EXIT_END,
    STATE_OPEN, STATE_CLOSED, DIRECTION_LONG,
)


def _parse_hhmm(s: str) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(0, 0)


def _touch(prev_close, low, high, level, buffer) -> bool:
    """True when the candle's range straddles the level (an intrabar touch)."""
    if level is None:
        return False
    lo = level - buffer
    hi = level + buffer
    return low <= hi and high >= lo


def find_holding_signals(candles: list[dict], vwaps: list[dict], *, entry_mode: str,
                         buffer: float, require_pw_above: bool, entry_start: str,
                         signal_cutoff: str, one_per_day: bool, day) -> list[dict]:
    """Bars on ``day`` where price meets Prev-Month VWAP with Prev-Week VWAP above.

    ``entry_mode`` = cross_up (prev close below, current crosses up) or touch
    (candle range straddles the level). ``require_pw_above`` enforces the green
    (Prev-Week) line being above the purple (Prev-Month) line.
    """
    start = _parse_hhmm(entry_start)
    cutoff = _parse_hhmm(signal_cutoff)
    signals: list[dict] = []
    for i in range(1, len(candles)):
        c = candles[i]
        dt = c.get("_dt")
        if dt is None or dt.date() != day or not (start <= dt.time() <= cutoff):
            continue
        pm = vwaps[i].get("prev_month_vwap")
        pw = vwaps[i].get("prev_week_vwap")
        if pm is None:
            continue
        if require_pw_above and (pw is None or pw <= pm):
            continue
        prev_close = float(candles[i - 1]["close"])
        hit = (crossed_up(prev_close, float(c["high"]), float(c["close"]), pm, buffer)
               if entry_mode == ENTRY_CROSS_UP
               else _touch(prev_close, float(c["low"]), float(c["high"]), pm, buffer))
        if hit:
            signals.append({
                "index": i, "dt": dt, "prev_month_vwap": pm, "prev_week_vwap": pw,
                "close": round(float(c["close"]), 2), "direction": DIRECTION_LONG,
            })
            if one_per_day:
                break
    return signals


def position_qty(capital_per_trade: int, fixed_qty: int, entry_price: float) -> int:
    if fixed_qty and fixed_qty > 0:
        return int(fixed_qty)
    if entry_price <= 0:
        return 0
    return int(capital_per_trade // entry_price)


def simulate_holding(entry_price: float, forward: list[dict], *, target_pct: float,
                     stop_pct: float, max_hold_days: int, exit_on: str, qty: int,
                     entry_day) -> dict:
    """Simulate a long equity holding forward candle-by-candle.

    ``forward`` = candles after entry, each {'_dt','high','low','close'}. Exits
    on target, stop (if enabled), max-hold days, else the last available candle.
    """
    target = round(entry_price * (1.0 + target_pct / 100.0), 2)
    stop = round(entry_price * (1.0 - stop_pct / 100.0), 2) if stop_pct and stop_pct > 0 else None

    exit_price = entry_price
    exit_dt = None
    reason = EXIT_END
    for c in forward:
        dt = c.get("_dt")
        if dt is None:
            continue
        hi, lo, cl = float(c["high"]), float(c["low"]), float(c["close"])
        held_days = (dt.date() - entry_day).days
        if exit_on == "high_low":
            if stop is not None and lo <= stop:
                exit_price, exit_dt, reason = stop, dt, EXIT_STOP
                break
            if hi >= target:
                exit_price, exit_dt, reason = target, dt, EXIT_TARGET
                break
        else:  # exit_on == close
            if stop is not None and cl <= stop:
                exit_price, exit_dt, reason = cl, dt, EXIT_STOP
                break
            if cl >= target:
                exit_price, exit_dt, reason = cl, dt, EXIT_TARGET
                break
        if held_days >= max_hold_days:
            exit_price, exit_dt, reason = cl, dt, EXIT_MAXHOLD
            break
        exit_price, exit_dt = cl, dt      # trail the latest close as the running exit

    mtm = round((exit_price - entry_price) * qty, 2)
    hold_days = (exit_dt.date() - entry_day).days if exit_dt else 0
    return {
        "target_price": target, "stop_price": stop,
        "exit_price": round(exit_price, 2),
        "exit_date": exit_dt.date().isoformat() if exit_dt else None,
        "exit_time": exit_dt.strftime("%H:%M") if exit_dt else None,
        "exit_reason": reason, "hold_days": hold_days,
        "qty": qty, "mtm": mtm,
        "return_pct": round((exit_price - entry_price) / entry_price * 100.0, 2) if entry_price else 0.0,
        "status": STATE_CLOSED if reason != EXIT_END else STATE_OPEN,
    }
