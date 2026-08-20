"""
QMRE PaperBroker — SIMULATION ONLY. There is no code path from here to a real
broker order. The safety guard below makes that explicit and fails closed.

Also provides realistic forward fill simulation (entry/exit slippage + round-trip
transaction costs + MFE/MAE) shared by Backtest, Replay and Single-Stock forensics.
"""
from __future__ import annotations

from research import costs

# Hard, module-level guard. Research #13 must NEVER place a real order.
LIVE_ORDER_EXECUTION = False


def assert_paper_only():
    if LIVE_ORDER_EXECUTION:                       # pragma: no cover - safety
        raise RuntimeError("QMRE is paper-only: live order execution is disabled by design.")


def apply_slippage(price: float, side: str, cfg: dict) -> float:
    """Buy fills a touch higher, sell a touch lower — by slippage_bps."""
    bps = float(cfg.get("slippage_bps", 0) or 0) / 1e4
    return round(price * (1 + bps) if side == "buy" else price * (1 - bps), 2)


def simulate_forward(entry: float, forward: list[tuple], *, sl: float, target: float,
                     qty: int, square_off_reached: bool, cfg: dict) -> dict:
    """Long paper trade. forward = [(dt, close, high, low), …] strictly after entry.
    Exit on HIGH ≥ target (TARGET) or LOW ≤ sl (STOP); else square off at the last
    candle if the horizon has passed, else OPEN. Net of slippage + round-trip cost."""
    assert_paper_only()
    qty = int(qty or 0)
    fill_entry = apply_slippage(entry, "buy", cfg)
    mx = mn = fill_entry
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
            exit_dt, exit_px, reason = dt, target, "TARGET"; break
        if sl and l <= sl:
            exit_dt, exit_px, reason = dt, sl, "STOP"; break
    if reason is None and square_off_reached and last:
        exit_dt, exit_px, reason = last[0], last[1], "SQUAREOFF"

    mfe = round((mx - fill_entry) * qty, 2)
    mae = round((mn - fill_entry) * qty, 2)
    if reason is None:
        cur = last[1] if last else fill_entry
        return {"open": True, "exit": None, "exit_dt": None, "exit_reason": "OPEN",
                "fill_entry": fill_entry, "mtm": round((cur - fill_entry) * qty, 2),
                "mfe": mfe, "mae": mae, "cost": 0.0}
    fill_exit = apply_slippage(exit_px, "sell", cfg)
    cost = 0.0
    if cfg.get("apply_costs"):
        cost = round(costs.roundtrip_cost(fill_entry, fill_exit, qty, **costs.cost_config(cfg, "equity")), 2)
    gross = (fill_exit - fill_entry) * qty
    return {"open": False, "exit": round(fill_exit, 2), "exit_dt": exit_dt, "exit_reason": reason,
            "fill_entry": fill_entry, "mtm": round(gross - cost, 2), "gross_mtm": round(gross, 2),
            "mfe": mfe, "mae": mae, "cost": cost}
