"""
Portfolio-capital simulation for the Equity-Holding research.

Instead of assuming fresh capital for every signal (the naive "sum of trades"
view), this simulates a realistic **fixed pool** of capital with a cap on
concurrent positions. Capital is deployed on entry, locked while a holding is
open, and recycled (with its realised P&L) when the holding exits — so a signal
is only taken if there's both a free slot and enough free capital.

Produces a true portfolio ROI, equity curve, max drawdown and CAGR. Pure and
unit-testable — it operates on the already-generated per-trade rows.
"""
from __future__ import annotations

from datetime import date, datetime


def _entry_dt(r: dict) -> datetime:
    try:
        t = r.get("time") or "09:15"
        return datetime.fromisoformat(f"{r['date']}T{t}:00")
    except Exception:
        return datetime.fromisoformat(f"{r['date']}T09:15:00")


def _exit_dt(r: dict) -> datetime:
    d = r.get("exit_date") or r.get("date")
    t = r.get("exit_time") or "15:30"
    try:
        return datetime.fromisoformat(f"{d}T{t}:00")
    except Exception:
        return datetime.fromisoformat(f"{d}T15:30:00")


def simulate_portfolio(rows: list[dict], *, pool: float, max_concurrent: int) -> dict:
    """Walk signals in entry order over a shared capital pool.

    Allocation per trade = pool / max_concurrent (fixed sizing). A trade is
    skipped if no slot is free or free capital can't buy ≥ 1 share. Returns
    headline portfolio metrics + an equity curve of realised P&L.
    """
    max_concurrent = max(1, int(max_concurrent))
    alloc = pool / max_concurrent
    ordered = sorted(rows, key=_entry_dt)

    available = float(pool)
    open_pos: list[tuple[datetime, float, float]] = []   # (exit_dt, capital, pnl)
    taken = skipped = 0
    realised: list[tuple[datetime, float]] = []          # (exit_dt, pnl)

    for r in ordered:
        edt = _entry_dt(r)
        # free capital + slots for anything that has already exited
        still: list[tuple] = []
        for xdt, cap, pnl in open_pos:
            if xdt <= edt:
                available += cap + pnl
            else:
                still.append((xdt, cap, pnl))
        open_pos = still

        if len(open_pos) >= max_concurrent:
            skipped += 1
            continue
        budget = min(alloc, available)
        entry_price = float(r.get("entry_price") or 0)
        if entry_price <= 0 or budget < entry_price:
            skipped += 1
            continue
        qty = int(budget // entry_price)
        cap_used = qty * entry_price
        pnl = round(float(r.get("return_pct") or 0) / 100.0 * cap_used, 2)
        available -= cap_used
        xdt = _exit_dt(r)
        open_pos.append((xdt, cap_used, pnl))
        realised.append((xdt, pnl))
        taken += 1

    # close out any still-open positions (mark realised at their exit)
    total_pnl = round(sum(p for _x, p in realised), 2)

    # equity curve of realised P&L over close dates
    realised.sort(key=lambda x: x[0])
    cum = peak = maxdd = 0.0
    curve: list[dict] = []
    for xdt, pnl in realised:
        cum += pnl
        curve.append({"t": xdt.date().isoformat(), "pnl": round(cum, 2)})
        peak = max(peak, cum)
        maxdd = max(maxdd, peak - cum)
    if len(curve) > 250:
        step = len(curve) // 250 + 1
        curve = curve[::step] + [curve[-1]]

    roi = round(total_pnl / pool * 100.0, 2) if pool else 0.0
    cagr = None
    period_days = 0
    if realised:
        period_days = max((realised[-1][0].date() - realised[0][0].date()).days, 0)
        if pool > 0 and period_days >= 1:
            final = pool + total_pnl
            if final > 0:
                cagr = round(((final / pool) ** (365.0 / period_days) - 1.0) * 100.0, 2)

    return {
        "pool": round(pool, 2), "max_concurrent": max_concurrent,
        "alloc_per_trade": round(alloc, 2),
        "trades_taken": taken, "trades_skipped": skipped,
        "total_pnl": total_pnl, "final_equity": round(pool + total_pnl, 2),
        "roi_pct": roi, "cagr_pct": cagr,
        "max_drawdown": round(maxdd, 2),
        "max_drawdown_pct": round(maxdd / pool * 100.0, 2) if pool else 0.0,
        "period_days": period_days,
        "equity_curve": curve,
    }
