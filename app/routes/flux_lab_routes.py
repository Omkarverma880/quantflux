"""
API routes for the Flux Strategy Test Lab — the combined hammer / inverted-hammer backtest.

Backtest only: it reads stored market data and writes its own runs. There is no order path in
this router. Long runs go through the background-job contract the frontend already polls.
"""
from __future__ import annotations

import csv
import io
import math
import traceback
from datetime import date
from functools import wraps

import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.logger import get_logger
from research.flux_lab import engine as EN
from research.flux_lab import patterns as PT
from research.flux_lab import service as SV

router = APIRouter()
logger = get_logger("api.flux_lab")


def json_safe(o):
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return [json_safe(v) for v in o.tolist()]
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [json_safe(v) for v in o]
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


def safe(name: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            try:
                out = fn(*a, **k)
                return json_safe(out) if isinstance(out, (dict, list)) else out
            except ValueError as exc:
                return {"status": "error", "message": str(exc)[:300]}
            except Exception as exc:
                logger.error("%s failed: %s | %s", name, exc, traceback.format_exc())
                return {"status": "error", "message": f"{name}: {type(exc).__name__} — {exc}"[:300]}
        return wrapper
    return deco


class RunReq(BaseModel):
    config: dict = {}        # {start, end, timeframe, params: {...}, execution: {...}}
    label: str | None = ""
    store: bool = True


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required)):
    from research.flux_lab.coverage import coverage
    return {
        "status": "ok", "strategy": SV.STRATEGY_NAME, "engine_version": SV.ENGINE_VERSION,
        "defaults": {"params": PT.P.as_dict(), "execution": EN.Execution().as_dict(), "timeframe": 5},
        "modes": PT.MODES, "explain": PT.describe(PT.P), "coverage": coverage(),
        "lot_size": EN.LOT,
        "sources": ["Hammer and inverted Hammer — long tails that break a recent extreme",
                    "Hammers & Stars — the body parked at the far end of a big enough candle"],
    }


@router.post("/explain")
@safe("explain")
def explain(req: RunReq, user_id: int = Depends(login_required)):
    """What the current settings mean, in words, without running anything."""
    p = PT.Params(**{k: v for k, v in (req.config.get("params") or {}).items()
                     if k in PT.P.as_dict() and v is not None})
    return {"status": "ok", "explain": PT.describe(p), "params": p.as_dict()}


@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    c = req.config or {}
    start, end = str(c.get("start") or ""), str(c.get("end") or "")
    date.fromisoformat(start), date.fromisoformat(end)
    if start > end:
        raise ValueError("start must be on or before end")

    def runner(payload, say, db, uid):
        return SV.run_backtest(db, uid, c, payload.get("label") or "", say, payload.get("store", True))
    job = SV.start_job({"label": req.label, "store": req.store}, user_id, runner)
    return {"status": "ok", "job": {k: v for k, v in job.items() if k != "user_id"}}


@router.get("/job/{job_id}")
@safe("job")
def job(job_id: str, user_id: int = Depends(login_required)):
    j = SV.get_job(job_id, user_id)
    if j is None:
        return {"status": "error", "message": "job not found"}
    out = {k: v for k, v in j.items() if k not in ("user_id", "result")}
    r = j.get("result") or {}
    out["result"] = {k: r.get(k) for k in ("run_id", "summary", "status")} if r else None
    return {"status": "ok", "job": out}


@router.get("/runs")
@safe("runs")
def runs(limit: int = 50, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "runs": SV.list_runs(db, user_id, limit)}


@router.get("/runs/{run_id}")
@safe("run")
def run_detail(run_id: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    r = SV.get_run(db, user_id, run_id)
    return {"status": "ok", "run": r} if r else {"status": "error", "message": "run not found"}


@router.get("/runs/{run_id}/trades")
@safe("run_trades")
def run_trades(run_id: int, limit: int = 5000, user_id: int = Depends(login_required),
               db: Session = Depends(get_db)):
    return {"status": "ok", "trades": SV.trades_of(db, user_id, run_id, limit)}


@router.delete("/runs/{run_id}")
@safe("delete_run")
def delete_run(run_id: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok" if SV.delete_run(db, user_id, run_id) else "error"}


@router.get("/runs/{run_id}/export.csv")
def export(run_id: int, what: str = "trades", user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    if what == "monthly":
        r = SV.get_run(db, user_id, run_id) or {}
        w.writerow(["month", "trades", "wins", "net"])
        for m in (r.get("summary") or {}).get("monthly", []):
            w.writerow([m["month"], m["trades"], m["wins"], m["net"]])
    else:
        cols = ["trade_no", "date", "pattern", "signal_time", "entry_time", "exit_time", "exit_reason",
                "type", "strike", "expiry", "dte", "lots", "qty", "option_entry", "option_exit",
                "spot_entry", "spot_exit", "spot_move_pts", "charges", "pnl"]
        w.writerow(cols)
        for t in SV.trades_of(db, user_id, run_id, 100000):
            w.writerow([t.get(c) for c in cols])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")
