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
    EXIT_TARGET, EXIT_STOP, EXIT_SQUAREOFF, EXIT_OPEN,
    STATE_FULL, STATE_HALF, STATE_OPEN, DIRECTION_LONG,
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


def _fmt(dt) -> str:
    """Date-aware exit label (a monthly hold can exit days after entry)."""
    return dt.strftime("%d-%b %H:%M")


def _exit_leg(entry: float, forward: list[tuple], target: float) -> dict:
    """Simulate one option leg forward. Exit when its CLOSE ≥ target, else at the
    last candle (square-off). ``forward`` = [(dt, close, high, low), …] after
    entry (2-tuples of (dt, close) are also accepted for back-compat)."""
    for row in forward:
        dt, prem = row[0], row[1]
        if prem is not None and prem >= target:
            return {"exit_time": _fmt(dt), "exit": round(prem, 2),
                    "reason": EXIT_TARGET, "exit_dt": dt}
    if forward:
        dt, prem = forward[-1][0], forward[-1][1]
        return {"exit_time": _fmt(dt),
                "exit": round(prem, 2) if prem is not None else round(entry, 2),
                "reason": EXIT_SQUAREOFF, "exit_dt": dt}
    return {"exit_time": None, "exit": round(entry, 2), "reason": EXIT_SQUAREOFF, "exit_dt": None}


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
    exit_dts = [d for d in (ce.get("exit_dt"), pe.get("exit_dt")) if d]

    return {
        "combined_entry": round(entry_ce + entry_pe, 2),
        "target_premium": target,
        "ce_exit": ce["exit"], "ce_exit_time": ce["exit_time"], "ce_exit_reason": ce["reason"],
        "pe_exit": pe["exit"], "pe_exit_time": pe["exit_time"], "pe_exit_reason": pe["reason"],
        "ce_mtm": ce_mtm, "pe_mtm": pe_mtm, "combined_mtm": round(ce_mtm + pe_mtm, 2),
        "targets_hit": targets_hit, "exit_dt": max(exit_dts) if exit_dts else None,
        "status": STATE_FULL,          # completed backtest → both legs closed
    }


def _hl(row):
    """Return (high, low) from a forward row that may be (dt, close, high, low)
    or a legacy (dt, close)."""
    if len(row) >= 4:
        return row[2], row[3]
    return row[1], row[1]


def leg_excursions(entry_ce, entry_pe, ce_forward, pe_forward, lot_size) -> dict:
    """Per-leg max profit / max loss after entry, using each leg's intraday
    HIGH for the max and LOW for the min (so a wick counts):

        Max Profit CE = (CE_highest_HIGH − CE_entry) × qty
        Max Loss   CE = (CE_lowest_LOW   − CE_entry) × qty
        Max Profit PE = (PE_highest_HIGH − PE_entry) × qty
        Max Loss   PE = (PE_lowest_LOW   − PE_entry) × qty
    """
    max_ce = min_ce = entry_ce
    max_pe = min_pe = entry_pe
    for row in ce_forward:
        h, l = _hl(row)
        if h is not None:
            max_ce = max(max_ce, h)
        if l is not None:
            min_ce = min(min_ce, l)
    for row in pe_forward:
        h, l = _hl(row)
        if h is not None:
            max_pe = max(max_pe, h)
        if l is not None:
            min_pe = min(min_pe, l)
    return {
        "max_profit_ce": round((max_ce - entry_ce) * lot_size, 2),
        "max_loss_ce": round((min_ce - entry_ce) * lot_size, 2),
        "max_profit_pe": round((max_pe - entry_pe) * lot_size, 2),
        "max_loss_pe": round((min_pe - entry_pe) * lot_size, 2),
    }


def simulate_straddle_combined(entry_ce: float, entry_pe: float, ce_forward: list[tuple],
                               pe_forward: list[tuple], *, target_amount: float,
                               sl_amount: float, lot_size: int,
                               square_off_reached: bool = True) -> dict:
    """Combined-P&L straddle exit: BOTH legs exit together the moment the combined
    MTM (× lot) reaches ``+target_amount`` or ``-sl_amount``. Until then (live,
    before square-off) the position stays OPEN and per-leg exits are blank.

    Also returns the max profit (MFE) and max loss (MAE) reached after entry, and
    the combined-premium levels that correspond to the target/SL rupee amounts.
    """
    combined_entry = round(entry_ce + entry_pe, 2)
    # pe_map[dt] = (close, high, low)
    pe_map = {row[0]: (row[1], _hl(row)[0], _hl(row)[1]) for row in pe_forward}
    # per-leg extremes after entry — HIGH for max, LOW for min (wicks count);
    # exit/MTM use the CLOSE.
    max_ce = min_ce = entry_ce
    max_pe = min_pe = entry_pe
    exit_dt = exit_ce = exit_pe = reason = None
    last = None
    for row in ce_forward:
        dt, ce_prem = row[0], row[1]
        ce_h, ce_l = _hl(row)
        rec = pe_map.get(dt)
        if ce_prem is None or rec is None:
            continue
        pe_prem, pe_h, pe_l = rec
        if pe_prem is None:
            continue
        max_ce = max(max_ce, ce_h); min_ce = min(min_ce, ce_l)
        max_pe = max(max_pe, pe_h); min_pe = min(min_pe, pe_l)
        mtm = (ce_prem + pe_prem - combined_entry) * lot_size
        last = (dt, ce_prem, pe_prem)
        if mtm >= target_amount:
            exit_dt, exit_ce, exit_pe, reason = dt, ce_prem, pe_prem, EXIT_TARGET
            break
        if sl_amount > 0 and mtm <= -sl_amount:
            exit_dt, exit_ce, exit_pe, reason = dt, ce_prem, pe_prem, EXIT_STOP
            break

    if reason is None and square_off_reached and last:
        exit_dt, exit_ce, exit_pe, reason = last[0], last[1], last[2], EXIT_SQUAREOFF

    lot = int(lot_size or 0)
    tgt_prem = round(combined_entry + (target_amount / lot if lot else 0), 2)
    sl_prem = round(combined_entry - (sl_amount / lot if lot else 0), 2)
    base = {"combined_entry": combined_entry, "target_premium": tgt_prem, "sl_premium": sl_prem,
            "max_profit_ce": round((max_ce - entry_ce) * lot, 2),
            "max_loss_ce": round((min_ce - entry_ce) * lot, 2),
            "max_profit_pe": round((max_pe - entry_pe) * lot, 2),
            "max_loss_pe": round((min_pe - entry_pe) * lot, 2),
            "target_amount": target_amount, "sl_amount": sl_amount}

    if reason is not None:          # closed (target / stop / square-off)
        lbl = _fmt(exit_dt)
        return {**base,
                "ce_exit": round(exit_ce, 2), "pe_exit": round(exit_pe, 2),
                "ce_exit_time": lbl, "pe_exit_time": lbl, "exit_dt": exit_dt,
                "ce_exit_reason": reason, "pe_exit_reason": reason,
                "ce_mtm": round((exit_ce - entry_ce) * lot, 2),
                "pe_mtm": round((exit_pe - entry_pe) * lot, 2),
                "combined_mtm": round((exit_ce + exit_pe - combined_entry) * lot, 2),
                "targets_hit": 1 if reason == EXIT_TARGET else 0,
                "status": STATE_FULL, "open": False}

    # still running (live, before expiry square-off) → exits blank, show unrealized
    cur_ce = last[1] if last else entry_ce
    cur_pe = last[2] if last else entry_pe
    return {**base,
            "ce_exit": None, "pe_exit": None, "ce_exit_time": None, "pe_exit_time": None,
            "exit_dt": None, "ce_exit_reason": EXIT_OPEN, "pe_exit_reason": EXIT_OPEN,
            "ce_mtm": round((cur_ce - entry_ce) * lot, 2),
            "pe_mtm": round((cur_pe - entry_pe) * lot, 2),
            "combined_mtm": round((cur_ce + cur_pe - combined_entry) * lot, 2),
            "targets_hit": 0, "status": STATE_OPEN, "open": True}


def live_status(ce_open: bool, pe_open: bool) -> str:
    """Live trade state from which legs are still running."""
    if ce_open and pe_open:
        return STATE_OPEN
    if ce_open or pe_open:
        return STATE_HALF
    return STATE_FULL
