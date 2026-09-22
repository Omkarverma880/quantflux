"""
API routes for the Flux Strategy Test Lab — the failed opening-range breakout iron fly.

Backtest on the stored history and PAPER trading on live data. There is no order path in this
router, and ``research.flux_lab.live`` has none either: paper trades are database rows only.

Long backtests run as a background job with the polling contract the frontend already uses.
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
from core.broker import get_user_broker
from core.database import get_db
from core.logger import get_logger
from research.flux_lab import backtest as BT
from research.flux_lab import live as LIVE
from research.flux_lab import service as SV
from research.flux_lab import strategy as ST

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


def _broker(db: Session, user_id: int):
    from core.auth import UserZerodhaAuth
    try:
        if not UserZerodhaAuth.is_authenticated(db, user_id):
            return None
    except Exception:
        return None
    return get_user_broker(db, user_id)


class RunReq(BaseModel):
    config: dict = {}          # {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "lots": 1}
    label: str | None = ""
    store: bool = True


class PaperReq(BaseModel):
    enabled: bool | None = None
    lots: int | None = None


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required)):
    return {"status": "ok", "strategy": ST.STRATEGY_NAME, "engine_version": ST.ENGINE_VERSION,
            "rules": ST.describe(), "rule": ST.RULE.as_dict(), "coverage": BT.coverage(),
            "lot_size": BT.LOT, "slippage_pts": BT.SLIPPAGE}


@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    c = req.config or {}
    start, end = str(c.get("start") or ""), str(c.get("end") or "")
    date.fromisoformat(start), date.fromisoformat(end)          # ValueError → clean message
    if start > end:
        raise ValueError("start must be on or before end")
    lots = max(1, min(50, int(c.get("lots") or 1)))

    def runner(payload, say, db, uid):
        return SV.run_backtest(db, uid, start, end, lots, payload.get("label") or "", say, payload.get("store", True))
    job = SV.start_job("backtest", {"label": req.label, "store": req.store}, user_id, runner)
    return {"status": "ok", "job": {k: v for k, v in job.items() if k != "user_id"}}


@router.get("/job/{job_id}")
@safe("job")
def job(job_id: str, user_id: int = Depends(login_required)):
    j = SV.get_job(job_id, user_id)
    if j is None:
        return {"status": "error", "message": "job not found"}
    return {"status": "ok", "job": {k: v for k, v in j.items() if k != "user_id"}}


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
        cols = ["trade_no", "date", "signal_time", "entry_time", "exit_time", "exit_reason", "atm", "expiry",
                "dte", "lots", "qty", "credit", "debit", "gross_pts", "charges", "pnl", "spot_entry", "spot_exit"]
        w.writerow(cols + ["legs"])
        for t in SV.trades_of(db, user_id, run_id, 100000):
            w.writerow([t.get(c) for c in cols] + [" | ".join(
                f"{'SELL' if l['q'] < 0 else 'BUY'} {l['type']} {l['strike']:g} @{l.get('entry')}→{l.get('exit')}"
                for l in t.get("legs", []))])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


# ── paper trading (never an order) ───────────────────────────────────
@router.get("/paper")
@safe("paper")
def paper(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return LIVE.dashboard(db, user_id, _broker(db, user_id))


@router.post("/paper/config")
@safe("paper_config")
def paper_config(req: PaperReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "config": LIVE.save_config(db, user_id, req.model_dump(exclude_none=True))}


@router.post("/paper/check")
@safe("paper_check")
def paper_check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Run one paper tick now — the same call the background loop makes every 20 seconds."""
    broker = _broker(db, user_id)
    if broker is None:
        return {"status": "error", "code": "not_connected",
                "message": "Connect Zerodha to run paper trading on live data."}
    LIVE._last_check.pop(user_id, None)
    return {"status": "ok", "result": LIVE.ENGINE.check(db, user_id, broker)}


@router.get("/paper/signals")
@safe("paper_signals")
def paper_signals(limit: int = 100, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from core.models import FluxLabSignal
    rows = (db.query(FluxLabSignal)
              .filter(FluxLabSignal.user_id == user_id, FluxLabSignal.side == "IF")
              .order_by(FluxLabSignal.id.desc()).limit(limit).all())
    return {"status": "ok", "signals": [{
        "id": s.id, "at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else None,
        "date": str(s.trade_date), "time": s.bar_time, "spot": float(s.spot) if s.spot is not None else None,
        "acted": s.acted, "skip_reason": s.skip_reason, "direction": (s.reasons or [None])[0],
        "trade_id": s.trade_id} for s in rows]}
