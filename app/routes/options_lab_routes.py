"""
API routes for the Options Lab — backtests on REAL option prices from the Market Store.

Read-only research. Every trade is executed on the actual traded contract with next-bar
fills, adverse stale-leg pricing, real statutory costs and margin-based sizing.
"""
from __future__ import annotations

import math
import traceback
from dataclasses import asdict, fields
from functools import wraps

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.auth import login_required
from core.logger import get_logger
from research.market_store import store as MS
from research.options_lab.execution import CostModel
from research.options_lab import structures as ST

router = APIRouter()
logger = get_logger("api.options_lab")
HOLDOUT_START = "2025-12-01"          # the sealed holdout used during research


def json_safe(o):
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    return o


def safe(name: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            try:
                out = fn(*a, **k)
                return json_safe(out) if isinstance(out, (dict, list)) else out
            except Exception as exc:
                logger.error("%s failed: %s | %s", name, exc, traceback.format_exc())
                return {"status": "error", "message": f"{name}: {type(exc).__name__} — {exc}"[:400]}
        return wrapper
    return deco


DEFAULT_PRESET = "short_straddle_expiry"
# Notes quote this exact code run on the full store (Sep 2023 – Sep 2026, 739 sessions), so a
# user testing a few days can compare with the long-run result before trusting it.
PRESETS = {
    "short_straddle_expiry": {
        "label": "Short straddle, expiry day, 10:00, stop 0.35× — most consistent (needs ₹2L)",
        "rule": {"structure": "short_straddle", "entry_minute": 600, "squareoff": 915,
                 "stop_x_credit": 0.35, "target_pct": 0.0, "dte_min": 0, "dte_max": 0,
                 "margin_per_lot": 190000.0, "capital": 200000.0, "max_lots": 1},
        "note": "3 years, 1 lot: 154 trades, win 47.4%, +₹80,028 (≈ ₹27k/year), max drawdown −₹24,138, "
                "profit every year (2023 +₹6k, 2024 +₹16k, 2025 +₹31k, 2026 +₹27k) and after 2025-12-01 "
                "(+₹39k). t = 1.66 — consistent but not yet proven (> 2). Needs ~₹1.9L margin: with ₹1L "
                "capital it cannot place a lot.",
    },
    "iron_fly_expiry_100": {
        "label": "Iron fly, 100-pt wings, expiry day, 10:00 (fits ₹1L) — breakeven",
        "rule": {"structure": "iron_fly", "entry_minute": 600, "squareoff": 915, "wing_steps": 2,
                 "stop_x_credit": 0.0, "dte_min": 0, "dte_max": 0, "margin_per_lot": 30000.0,
                 "capital": 100000.0, "max_lots": 3},
        "note": "3 years: 154 trades, win 46.8%, −₹4,644 in total (2024 −₹37k, 2025 +₹31k). No edge.",
    },
    "long_atm_call": {
        "label": "Long ATM call, 10:00, stop 15% / target 100% — loses over 3 years",
        "rule": {"structure": "long_call", "entry_minute": 600, "squareoff": 915, "stop_pct": 15.0,
                 "target_pct": 100.0, "dte_min": 0, "dte_max": 30, "max_lots": 1},
        "note": "3 years, 1 lot: 737 trades, win 22.5%, −₹63,446. A week or two can show a big profit "
                "from one or two target hits; over years the stop-losses outweigh them. Long ATM put: −₹2.31L.",
    },
    "iron_fly_research": {
        "label": "Iron fly, 200-pt wings, DTE 2–30 — worst result, do not use",
        "rule": {"structure": "iron_fly", "entry_minute": 585, "squareoff": 915, "wing_steps": 4,
                 "stop_x_credit": 0.0, "dte_min": 2, "dte_max": 30, "margin_per_lot": 40000.0},
        "note": "3 years: 443 trades, win 20.3%, −₹9,61,460, lost every year. It only looked good in early "
                "research because 48% of trades had a wing with no real price at exit.",
    },
}


class LabReq(BaseModel):
    rule: dict | None = None
    start_date: str | None = None
    end_date: str | None = None
    lot_size: int = 65
    slippage_pts: float = 0.5
    underlying: str = "NIFTY"


def _rule(d: dict | None) -> ST.StructureRule:
    base = asdict(ST.StructureRule())
    names = {f.name for f in fields(ST.StructureRule)}
    base.update({k: v for k, v in (d or {}).items() if k in names and v is not None})
    r = ST.StructureRule(**base)
    if r.structure not in ST.STRUCTURES:
        raise ValueError(f"structure must be one of {ST.STRUCTURES}")
    return r


def _stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0}
    r = df.pnl_rs
    n = len(r)
    d0, d1 = pd.to_datetime(df.date).min(), pd.to_datetime(df.date).max()
    yrs = max((d1 - d0).days / 365.25, 1 / 12)
    # projecting a few days to a year is meaningless — only annualise 3+ months
    per_year = round(float(r.sum() / yrs)) if (d1 - d0).days >= 90 else None
    eq = r.cumsum()
    wins, losses = r[r > 0], r[r < 0]
    return {
        "trades": n, "win_rate": round(float((r > 0).mean() * 100), 1),
        "pnl_total": round(float(r.sum())), "pnl_per_trade": round(float(r.mean())),
        "pnl_per_year": per_year,
        "t_stat": round(float(r.mean() / (r.std() / np.sqrt(n))), 2) if n > 2 and r.std() > 0 else None,
        "max_drawdown": round(float((eq - eq.cummax()).min())),
        "worst_trade": round(float(r.min())), "best_trade": round(float(r.max())),
        "avg_win": round(float(wins.mean())) if len(wins) else 0,
        "avg_loss": round(float(losses.mean())) if len(losses) else 0,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) and losses.sum() else None,
        "stop_exits": int((df.exit_reason == "SL").sum()),
        "stale_leg_trades": int((df.stale_legs_at_exit > 0).sum()),
        "avg_capital_used": round(float(df.capital_used.mean())),
        "avg_return_on_capital_pct": round(float(df.ret_on_capital_pct.mean()), 3),
        "first": str(d0.date()), "last": str(d1.date()),
    }


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required)):
    return {"status": "ok", "structures": list(ST.STRUCTURES), "defaults": asdict(ST.StructureRule()),
            "presets": PRESETS, "default_preset": DEFAULT_PRESET,
            "holdout_start": HOLDOUT_START, "store": MS.summary()}


@router.post("/backtest")
@safe("backtest")
def backtest(req: LabReq, user_id: int = Depends(login_required)):
    rule = _rule(req.rule)
    O = MS.read("options", underlying=req.underlying, start=req.start_date or None,
                end=req.end_date or None,
                columns=["timestamp", "contract", "expiry_date", "strike", "option_type",
                         "open", "close", "spot"])
    if O.empty:
        return {"status": "error", "message": "No option data in the Market Store for that range — upload it in the Data tab"}
    days = pd.to_datetime(O.timestamp).dt.date
    sessions = int(days.nunique())
    first_day, last_day = pd.Timestamp(days.min()), pd.Timestamp(days.max())
    trades = ST.run(O, rule, lot_size=req.lot_size, costs=CostModel(slippage_pts=req.slippage_pts),
                    underlying=req.underlying)
    T = pd.DataFrame([t.as_dict() for t in trades])
    skips = [{"reason": k, "label": ST.SKIP_LABELS.get(k, k), "sessions": v}
             for k, v in sorted(getattr(ST.run, "last_skips", {}).items(), key=lambda kv: -kv[1])]
    out = {"status": "ok", "rule": asdict(rule), "sessions": sessions, "summary": _stats(T),
           "trades": [], "yearly": [], "monthly": [], "equity": [], "split": {},
           "funnel": {"sessions_in_range": sessions, "trades": len(T), "skips": skips}}
    if T.empty:
        # Days outside the DTE window are expected skips, not the problem. Name the
        # reason that stopped the days that WERE eligible, or it points at the wrong setting.
        outside = next((k["sessions"] for k in skips if k["reason"] == "outside_dte_window"), 0)
        eligible = [k for k in skips if k["reason"] != "outside_dte_window"]
        if eligible:
            n_elig = sum(k["sessions"] for k in eligible)
            out["summary"]["note"] = (f"No trades. {outside} session(s) were outside the days-to-expiry window; "
                                      f"of the {n_elig} inside it, the main reason was: {eligible[0]['label']}.")
        elif outside:
            out["summary"]["note"] = ("No trades. Every session was outside the days-to-expiry window — "
                                      "widen DTE from / DTE to.")
        else:
            out["summary"]["note"] = "No trades. No eligible sessions in the selected range."
        return out
    T["pnl_cum"] = T.pnl_rs.cumsum()
    out["trades"] = T.to_dict("records")
    out["equity"] = [{"date": a, "equity": b} for a, b in zip(T.date, T.pnl_cum)]
    dt = pd.to_datetime(T.date)
    for key, fmt in (("yearly", "%Y"), ("monthly", "%Y-%m")):
        rows = []
        for p, g in T.groupby(dt.dt.strftime(fmt)):
            s = _stats(g); s["period"] = p; rows.append(s)
        out[key] = rows
    ho = pd.Timestamp(HOLDOUT_START)
    before, after = _stats(T[dt < ho]), _stats(T[dt >= ho])
    if first_day >= ho:
        before["outside_range"] = True            # the chosen dates never reach this period
    if last_day < ho:
        after["outside_range"] = True
    out["split"] = {"before_holdout": before, "holdout": after}
    out["funnel"] = {"sessions_in_range": sessions, "trades": len(T), "skips": skips}
    return out
