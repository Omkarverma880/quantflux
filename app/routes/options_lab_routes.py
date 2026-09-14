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


PRESETS = {
    "iron_fly_research": {
        "label": "Iron fly, 200-pt wings (sell) — NOT validated",
        "rule": {"structure": "iron_fly", "entry_minute": 585, "squareoff": 915, "wing_steps": 4,
                 "stop_x_credit": 0.0, "dte_min": 2, "dte_max": 30, "margin_per_lot": 40000.0},
        "note": "Looked strong in research (holdout t = 2.35) until stress-tested: 48% of trades had a wing "
                "with no real price at exit. Priced adversely it loses (t = -9). Kept so the result can be "
                "re-checked; stale-leg adverse pricing is ON by default.",
    },
    "short_straddle_expiry": {
        "label": "Short straddle, expiry day (for comparison)",
        "rule": {"structure": "short_straddle", "entry_minute": 600, "squareoff": 915,
                 "stop_x_credit": 0.35, "dte_min": 0, "dte_max": 0, "margin_per_lot": 190000.0},
        "note": "The old modelled strategy on real prices. Not significant; needs ~₹1.9L margin per lot.",
    },
    "long_atm_call": {
        "label": "Long ATM call (research baseline — loses)",
        "rule": {"structure": "long_call", "entry_minute": 600, "squareoff": 915, "stop_pct": 15.0,
                 "target_pct": 100.0, "dte_min": 0, "dte_max": 30},
        "note": "Random-entry option buying on real premiums loses in every slice tested.",
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
    eq = r.cumsum()
    wins, losses = r[r > 0], r[r < 0]
    return {
        "trades": n, "win_rate": round(float((r > 0).mean() * 100), 1),
        "pnl_total": round(float(r.sum())), "pnl_per_trade": round(float(r.mean())),
        "pnl_per_year": round(float(r.sum() / yrs)),
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
            "presets": PRESETS, "holdout_start": HOLDOUT_START, "store": MS.summary()}


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
    sessions = int(pd.to_datetime(O.timestamp).dt.date.nunique())
    trades = ST.run(O, rule, lot_size=req.lot_size, costs=CostModel(slippage_pts=req.slippage_pts),
                    underlying=req.underlying)
    T = pd.DataFrame([t.as_dict() for t in trades])
    skips = [{"reason": k, "label": ST.SKIP_LABELS.get(k, k), "sessions": v}
             for k, v in sorted(getattr(ST.run, "last_skips", {}).items(), key=lambda kv: -kv[1])]
    out = {"status": "ok", "rule": asdict(rule), "sessions": sessions, "summary": _stats(T),
           "trades": [], "yearly": [], "monthly": [], "equity": [], "split": {},
           "funnel": {"sessions_in_range": sessions, "trades": len(T), "skips": skips}}
    if T.empty:
        top = skips[0]["label"] if skips else "no eligible sessions"
        out["summary"]["note"] = f"No trades. Main reason: {top}."
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
    out["split"] = {"before_holdout": _stats(T[dt < ho]), "holdout": _stats(T[dt >= ho])}
    out["funnel"] = {"sessions_in_range": sessions, "trades": len(T), "skips": skips}
    return out
