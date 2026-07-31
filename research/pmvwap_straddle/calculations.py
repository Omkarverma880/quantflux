"""
Pure calculation logic for the Prev-Month-VWAP Straddle Research.

Side-effect free and unit-testable: entry-signal detection (crossing the
Previous-Month VWAP from below) and the virtual straddle simulation with the
independent second-leg target exit. No broker calls, no I/O.
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Optional

from research.prev_period_vwap import crossed_up
from research.pmvwap_straddle.constants import (
    EXIT_TARGET, EXIT_SQUAREOFF, STATE_FULL, STATE_HALF, STATE_OPEN, DIRECTION_LONG,
)


def _parse_hhmm(s: str) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(0, 0)


def find_entry_signals(candles: list[dict], vwaps: list[dict], *, buffer: float,
                       entry_start: str, signal_cutoff: str, one_per_day: bool,
                       day) -> list[dict]:
    """Detect bars on ``day`` that cross Prev-Month VWAP from below.

    A signal fires when the previous candle closed below Prev-Month VWAP and the
    current candle touches/crosses it (crossed_up). ``one_per_day`` keeps only
    the first crossing per session. Returns dicts with the candle index + level.
    """
    start = _parse_hhmm(entry_start)
    cutoff = _parse_hhmm(signal_cutoff)
    signals: list[dict] = []
    seen_day = False
    for i in range(1, len(candles)):
        c = candles[i]
        dt = c.get("_dt")
        if dt is None or dt.date() != day:
            continue
        if not (start <= dt.time() <= cutoff):
            continue
        level = vwaps[i].get("prev_month_vwap")
        prev_close = float(candles[i - 1]["close"])
        if crossed_up(prev_close, float(c["high"]), float(c["close"]), level, buffer):
            signals.append({
                "index": i, "dt": dt, "level": level,
                "close": round(float(c["close"]), 2), "direction": DIRECTION_LONG,
            })
            if one_per_day:
                break
        seen_day = True
    return signals


def combined_target(entry_ce: float, entry_pe: float, target_pct: float) -> float:
    """Target premium = combined entry × (1 + target%). Rounded to 2dp."""
    return round((entry_ce + entry_pe) * (1.0 + target_pct / 100.0), 2)


def _exit_leg(entry: float, forward: list[tuple], target: float) -> dict:
    """Simulate one option leg forward. Exit when its premium ≥ target, else at
    the last candle (square-off). ``forward`` = [(dt, premium), …] after entry."""
    for dt, prem in forward:
        if prem is not None and prem >= target:
            return {"exit_time": dt.strftime("%H:%M"), "exit": round(prem, 2),
                    "reason": EXIT_TARGET}
    if forward:
        dt, prem = forward[-1]
        return {"exit_time": dt.strftime("%H:%M"),
                "exit": round(prem, 2) if prem is not None else round(entry, 2),
                "reason": EXIT_SQUAREOFF}
    return {"exit_time": None, "exit": round(entry, 2), "reason": EXIT_SQUAREOFF}


def simulate_straddle(entry_ce: float, entry_pe: float, ce_forward: list[tuple],
                      pe_forward: list[tuple], *, target_pct: float,
                      lot_size: int) -> dict:
    """Simulate the CE+PE straddle with the independent second-leg target exit.

    Each leg exits when ITS OWN premium reaches the combined target; the other
    leg keeps running until its own target (or square-off). Returns per-leg
    exits, MTMs (× lot size) and the final status.
    """
    target = combined_target(entry_ce, entry_pe, target_pct)
    ce = _exit_leg(entry_ce, ce_forward, target)
    pe = _exit_leg(entry_pe, pe_forward, target)

    ce_mtm = round((ce["exit"] - entry_ce) * lot_size, 2)
    pe_mtm = round((pe["exit"] - entry_pe) * lot_size, 2)
    targets_hit = int(ce["reason"] == EXIT_TARGET) + int(pe["reason"] == EXIT_TARGET)

    return {
        "combined_entry": round(entry_ce + entry_pe, 2),
        "target_premium": target,
        "ce_exit": ce["exit"], "ce_exit_time": ce["exit_time"], "ce_exit_reason": ce["reason"],
        "pe_exit": pe["exit"], "pe_exit_time": pe["exit_time"], "pe_exit_reason": pe["reason"],
        "ce_mtm": ce_mtm, "pe_mtm": pe_mtm, "combined_mtm": round(ce_mtm + pe_mtm, 2),
        "targets_hit": targets_hit,
        "status": STATE_FULL,          # completed backtest → both legs closed
    }


def live_status(ce_open: bool, pe_open: bool) -> str:
    """Live trade state from which legs are still running."""
    if ce_open and pe_open:
        return STATE_OPEN
    if ce_open or pe_open:
        return STATE_HALF
    return STATE_FULL
