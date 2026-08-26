"""
Trade simulation — PURE. Shared by the backtest, the single-day simulate and the
live/paper engine so an entry can never behave differently between them.

Long option only (BUY CE / BUY PE). Exits on target, stop, square-off time or
expiry, tracking MFE/MAE and applying slippage + round-trip transaction costs.
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Optional

from research import costs


def _hhmm(s: str, default=(15, 15)) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(*default)


def resolve_target_sl(entry: float, cfg: dict) -> tuple[float, float]:
    """Target above / stop below the entry premium (percent or points)."""
    tv, sv = float(cfg["target_value"]), float(cfg["sl_value"])
    target = entry + tv if cfg.get("target_mode") == "points" else entry * (1 + tv / 100.0)
    stop = entry - sv if cfg.get("sl_mode") == "points" else entry * (1 - sv / 100.0)
    return round(target, 2), round(max(0.05, stop), 2)


def apply_slippage(price: float, side: str, cfg: dict) -> float:
    bps = float(cfg.get("slippage_bps", 0) or 0) / 1e4
    return round(price * (1 + bps) if side == "buy" else price * (1 - bps), 2)


def pick_entry(prem_bars: list[dict], signal_index_dt, cfg: dict) -> Optional[dict]:
    """Locate the fill bar for a signal.

    ``next_bar_open`` (default) fills on the OPEN of the bar after the signal —
    the realistic assumption, since the signal is only known once its bar closes.
    ``signal_bar_close`` fills at the signal bar's close (optimistic).
    """
    idx = next((i for i, b in enumerate(prem_bars) if b.get("_dt") == signal_index_dt), None)
    if idx is None:                       # premium series may not align exactly
        idx = next((i for i, b in enumerate(prem_bars)
                    if b.get("_dt") and b["_dt"] >= signal_index_dt), None)
        if idx is None:
            return None
    if cfg.get("fill_mode", "next_bar_open") == "signal_bar_close":
        b = prem_bars[idx]
        return {"i": idx, "price": float(b["close"]), "dt": b["_dt"], "basis": "signal bar close"}
    if idx + 1 >= len(prem_bars):
        return None                       # signal on the last bar — nothing to fill on
    b = prem_bars[idx + 1]
    price = float(b.get("open") or b["close"])
    return {"i": idx + 1, "price": price, "dt": b["_dt"], "basis": "next bar open"}


def simulate_trade(prem_bars: list[dict], entry_i: int, entry_px: float, qty: int,
                   cfg: dict, *, expiry_reached: bool = True,
                   index_bars: Optional[list[dict]] = None,
                   index_entry: Optional[float] = None) -> dict:
    """Walk the premium bars after entry and resolve the exit.

    ``exit_on='index_points'`` triggers on the INDEX moving target/SL points
    while P&L is still realised on the premium — the honest way to model an
    index-level exit rule.
    """
    fill = apply_slippage(entry_px, "buy", cfg)
    target, stop = resolve_target_sl(fill, cfg)
    sq = _hhmm(cfg.get("square_off_time", "15:15"))
    use_index = cfg.get("exit_on") == "index_points" and index_bars and index_entry

    if use_index:
        tv, sv = float(cfg["target_value"]), float(cfg["sl_value"])
        up = index_entry + (tv if cfg.get("target_mode") == "points" else index_entry * tv / 100.0)
        dn = index_entry - (sv if cfg.get("sl_mode") == "points" else index_entry * sv / 100.0)
        idx_by_dt = {b["_dt"]: b for b in index_bars if b.get("_dt")}

    mx = mn = fill
    exit_px = exit_dt = reason = None
    last = None
    for j in range(entry_i + 1, len(prem_bars)):
        b = prem_bars[j]
        dt = b.get("_dt")
        if dt is None:
            continue
        hi, lo, cl = float(b["high"]), float(b["low"]), float(b["close"])
        mx, mn = max(mx, hi), min(mn, lo)
        last = (dt, cl)
        if use_index:
            ib = idx_by_dt.get(dt)
            if ib:
                is_call = True   # long-option P&L is on the premium either way
                if float(ib["high"]) >= up:
                    exit_px, exit_dt, reason = cl, dt, "TARGET"; break
                if float(ib["low"]) <= dn:
                    exit_px, exit_dt, reason = cl, dt, "STOP"; break
        else:
            if hi >= target:
                exit_px, exit_dt, reason = target, dt, "TARGET"; break
            if lo <= stop:
                exit_px, exit_dt, reason = stop, dt, "STOP"; break
        if not cfg.get("hold_to_expiry") and dt.time() >= sq:
            exit_px, exit_dt, reason = cl, dt, "SQUAREOFF"; break

    if reason is None and last and expiry_reached:
        exit_px, exit_dt, reason = last[1], last[0], "EXPIRY" if cfg.get("hold_to_expiry") else "SQUAREOFF"

    mfe = round((mx - fill) * qty, 2)
    mae = round((mn - fill) * qty, 2)
    if reason is None:
        cur = last[1] if last else fill
        return {"open": True, "entry": fill, "target": target, "sl": stop, "exit": None,
                "exit_dt": None, "exit_reason": "OPEN", "mtm": round((cur - fill) * qty, 2),
                "gross_mtm": round((cur - fill) * qty, 2), "cost": 0.0, "mfe": mfe, "mae": mae}

    fill_exit = apply_slippage(float(exit_px), "sell", cfg)
    cost = 0.0
    if cfg.get("apply_costs"):
        cost = round(costs.roundtrip_cost(fill, fill_exit, qty, **costs.cost_config(cfg, "option")), 2)
    gross = (fill_exit - fill) * qty
    return {"open": False, "entry": fill, "target": target, "sl": stop,
            "exit": round(fill_exit, 2), "exit_dt": exit_dt, "exit_reason": reason,
            "gross_mtm": round(gross, 2), "mtm": round(gross - cost, 2),
            "cost": cost, "mfe": mfe, "mae": mae}
