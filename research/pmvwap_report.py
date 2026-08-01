"""
Shared Research Summary-Report engine for the Prev-Month-VWAP modules.

Pure and unit-testable: it turns a flat list of research-log rows into an
analytics report — win-rate, P&L distribution, stock / day-of-week /
time-of-day / gap / sector breakdowns, a weekday×hour heatmap — plus a
side-by-side comparison of two runs. No I/O; works for both the straddle
(``mtm_key="combined_mtm"``) and equity (``mtm_key="mtm"``) modules.
"""
from __future__ import annotations

from datetime import date

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _agg(rows: list[dict], mtm_key: str) -> dict:
    """Headline metrics for a set of rows."""
    n = len(rows)
    if not n:
        return {"signals": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "total_mtm": 0.0, "avg_mtm": 0.0, "best": 0.0, "worst": 0.0,
                "profit_factor": 0.0, "expectancy": 0.0}
    mtms = [float(r.get(mtm_key) or 0) for r in rows]
    wins = [m for m in mtms if m > 0]
    losses = [m for m in mtms if m < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total_mtm = sum(mtms)
    # Capital deployed (equity holdings carry `capital` = entry × qty). Absent
    # for the options straddle, where it stays 0 and the ROI card is hidden.
    total_capital = sum(float(r.get("capital") or 0) for r in rows)
    return {
        "signals": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100.0, 1),
        "total_mtm": round(total_mtm, 2), "avg_mtm": round(total_mtm / n, 2),
        "best": round(max(mtms), 2), "worst": round(min(mtms), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (gross_win and 999.0 or 0.0),
        "expectancy": round(total_mtm / n, 2),
        "total_capital": round(total_capital, 2),
        "avg_capital": round(total_capital / n, 2) if total_capital else 0.0,
        "roi_pct": round(total_mtm / total_capital * 100.0, 2) if total_capital else 0.0,
    }


def _group(rows: list[dict], key_fn, mtm_key: str) -> dict:
    buckets: dict = {}
    for r in rows:
        k = key_fn(r)
        buckets.setdefault(k, []).append(r)
    return {k: _agg(v, mtm_key) for k, v in buckets.items()}


def _pnl_distribution(rows: list[dict], mtm_key: str, bins: int = 9) -> list[dict]:
    mtms = [float(r.get(mtm_key) or 0) for r in rows]
    if not mtms:
        return []
    lo, hi = min(mtms), max(mtms)
    if lo == hi:
        return [{"label": f"{lo:,.0f}", "count": len(mtms), "lo": lo, "hi": hi}]
    width = (hi - lo) / bins
    out = [{"lo": lo + i * width, "hi": lo + (i + 1) * width, "count": 0} for i in range(bins)]
    for m in mtms:
        idx = min(int((m - lo) / width), bins - 1)
        out[idx]["count"] += 1
    for b in out:
        b["label"] = f"{b['lo']:,.0f} … {b['hi']:,.0f}"
        b["lo"] = round(b["lo"], 2)
        b["hi"] = round(b["hi"], 2)
    return out


def _gap_bucket(gap, flat_thr: float = 0.3) -> str:
    if gap is None:
        return "Unknown"
    if gap > flat_thr:
        return "Gap-up"
    if gap < -flat_thr:
        return "Gap-down"
    return "Flat"


def _heatmap(rows: list[dict], mtm_key: str) -> dict:
    """weekday × entry-hour grid of total MTM + signal count."""
    grid: dict = {}
    hours: set = set()
    for r in rows:
        try:
            wd = date.fromisoformat(r["date"]).weekday()
            hr = int(str(r.get("time", "0:0")).split(":")[0])
        except Exception:
            continue
        hours.add(hr)
        cell = grid.setdefault((wd, hr), {"total_mtm": 0.0, "count": 0})
        cell["total_mtm"] += float(r.get(mtm_key) or 0)
        cell["count"] += 1
    hours = sorted(hours)
    matrix = []
    for wd in range(5):                       # Mon–Fri
        row = {"weekday": _WEEKDAYS[wd], "cells": []}
        for hr in hours:
            c = grid.get((wd, hr))
            row["cells"].append({"hour": hr,
                                 "total_mtm": round(c["total_mtm"], 2) if c else 0.0,
                                 "count": c["count"] if c else 0})
        matrix.append(row)
    return {"hours": hours, "rows": matrix}


def _close_key(r: dict) -> tuple:
    return (r.get("exit_date") or r.get("date") or "", r.get("time") or "")


def _equity_curve(rows: list[dict], mtm_key: str, base_capital: float) -> dict:
    """Realised cumulative-P&L curve ordered by each trade's close, plus the
    peak-to-trough max drawdown (₹ and %) and CAGR when capital is known."""
    ordered = sorted(rows, key=_close_key)
    cum = peak = maxdd = 0.0
    curve: list[dict] = []
    for r in ordered:
        cum += float(r.get(mtm_key) or 0)
        curve.append({"t": r.get("exit_date") or r.get("date"), "pnl": round(cum, 2)})
        peak = max(peak, cum)
        maxdd = max(maxdd, peak - cum)
    # down-sample so the payload/chart stays light
    if len(curve) > 250:
        step = len(curve) // 250 + 1
        curve = curve[::step] + [curve[-1]]
    denom = (base_capital + peak) if base_capital else (peak or 1.0)
    max_dd_pct = round(maxdd / denom * 100.0, 2) if denom else 0.0

    cagr = None
    period_days = 0
    if ordered:
        try:
            d0 = date.fromisoformat(_close_key(ordered[0])[0])
            d1 = date.fromisoformat(_close_key(ordered[-1])[0])
            period_days = max((d1 - d0).days, 0)
        except Exception:
            period_days = 0
        if base_capital > 0 and period_days >= 1:
            final = base_capital + cum
            if final > 0:
                yrs = period_days / 365.0
                cagr = round(((final / base_capital) ** (1.0 / yrs) - 1.0) * 100.0, 2)
    return {"equity_curve": curve, "max_drawdown": round(maxdd, 2),
            "max_drawdown_pct": max_dd_pct, "cagr_pct": cagr, "period_days": period_days}


def build_report(rows: list[dict], *, mtm_key: str, sector_map: dict | None = None) -> dict:
    """Full summary report for a run's research-log rows."""
    sector_map = sector_map or {}

    def _rank(group: dict, order: list | None = None) -> list[dict]:
        items = [{"key": k, **v} for k, v in group.items()]
        if order:
            items.sort(key=lambda x: order.index(x["key"]) if x["key"] in order else 999)
        else:
            items.sort(key=lambda x: x["total_mtm"], reverse=True)
        return items

    overall = _agg(rows, mtm_key)
    curve = _equity_curve(rows, mtm_key, overall.get("total_capital", 0.0))
    return {
        "overall": overall,
        **curve,
        "pnl_distribution": _pnl_distribution(rows, mtm_key),
        "stock_ranking": _rank(_group(rows, lambda r: r.get("underlying", "?"), mtm_key)),
        "day_of_week": _rank(_group(rows, lambda r: _WEEKDAYS[date.fromisoformat(r["date"]).weekday()], mtm_key), _WEEKDAYS),
        "time_of_day": _rank(_group(rows, lambda r: f"{int(str(r.get('time','0:0')).split(':')[0]):02d}:00", mtm_key),
                             sorted({f"{int(str(r.get('time','0:0')).split(':')[0]):02d}:00" for r in rows})),
        "gap_performance": _rank(_group(rows, lambda r: _gap_bucket(r.get("gap_pct")), mtm_key),
                                 ["Gap-up", "Flat", "Gap-down", "Unknown"]),
        "sector_performance": _rank(_group(rows, lambda r: sector_map.get(r.get("underlying"), "Unknown"), mtm_key)),
        "heatmap": _heatmap(rows, mtm_key),
    }


def compare(rows_a: list[dict], rows_b: list[dict], *, mtm_key: str,
            label_a: str = "A", label_b: str = "B") -> dict:
    """Side-by-side headline comparison of two runs + per-metric delta."""
    a, b = _agg(rows_a, mtm_key), _agg(rows_b, mtm_key)
    keys = ["signals", "win_rate", "total_mtm", "avg_mtm", "best", "worst", "profit_factor"]
    delta = {k: round((b.get(k, 0) or 0) - (a.get(k, 0) or 0), 2) for k in keys}
    return {"label_a": label_a, "label_b": label_b, "a": a, "b": b, "delta": delta,
            "stocks_a": _rank_stocks(rows_a, mtm_key), "stocks_b": _rank_stocks(rows_b, mtm_key)}


def _rank_stocks(rows: list[dict], mtm_key: str, top: int = 10) -> list[dict]:
    g = _group(rows, lambda r: r.get("underlying", "?"), mtm_key)
    items = [{"key": k, **v} for k, v in g.items()]
    items.sort(key=lambda x: x["total_mtm"], reverse=True)
    return items[:top]
