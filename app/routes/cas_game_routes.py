"""
API routes for CAS Game Play (Index Strategies → CAS Game Play).

The desk can place real orders only when the strategy's own mode is "live" AND the app is not in
paper mode AND the risk fence is down — the check lives in ``research.cas_game.live._order``.
Everything else here reads data or edits settings.
"""
from __future__ import annotations

import math
import traceback
from datetime import date
from functools import wraps

import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.broker import get_user_broker
from core.database import get_db
from core.logger import get_logger
from research.cas_game import live as LIVE
from research.cas_game import service as SV
from research.cas_game import strategy as ST

router = APIRouter()
logger = get_logger("api.cas_game")


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


def _broker(db: Session, user_id: int):
    from core.auth import UserZerodhaAuth
    try:
        if not UserZerodhaAuth.is_authenticated(db, user_id):
            return None
    except Exception:
        return None
    return get_user_broker(db, user_id)


class ConfigReq(BaseModel):
    running: bool | None = None
    mode: str | None = None          # paper | live
    params: dict | None = None


class RunReq(BaseModel):
    config: dict = {}                # {start, end, params: {...}, label}


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from config import settings
    cfg = LIVE.load_config(db, user_id)
    return {
        "status": "ok", "defaults": ST.P.as_dict(), "config": cfg,
        "rules": ST.describe(LIVE.params_of(cfg)),
        "indices": list(ST.LOT_SIZE),
        "app_paper_mode": bool(settings.PAPER_TRADE or not settings.TRADING_ENABLED),
        "connected": _broker(db, user_id) is not None,
        "schedule": {
            "cash_auction": "15:15–15:35 — the closing auction that sets the cash close",
            "derivatives_close": "15:40 — index options trade ten minutes past the cash market",
            "why": "Those ten minutes are when option premiums re-price against the auction's close, "
                   "which is the dislocation this strategy is betting on.",
        },
    }


@router.post("/config")
@safe("config")
def config(req: ConfigReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    updates = req.model_dump(exclude_none=True)
    if updates.get("mode") not in (None, "paper", "live"):
        raise ValueError("mode must be paper or live")
    return {"status": "ok", "config": LIVE.save_config(db, user_id, updates)}


@router.post("/start")
@safe("start")
def start(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    if broker is None:
        return {"status": "error", "code": "not_connected",
                "message": "Connect Zerodha — the desk needs live option quotes."}
    cfg = LIVE.save_config(db, user_id, {"running": True, "started_at": str(date.today())})
    return {"status": "ok", "config": cfg, "result": LIVE.ENGINE.check(db, user_id, broker)}


@router.post("/stop")
@safe("stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "config": LIVE.save_config(db, user_id, {"running": False})}


@router.get("/desk")
@safe("desk")
def desk(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return LIVE.dashboard(db, user_id, _broker(db, user_id))


@router.post("/scan")
@safe("scan")
def scan_now(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Look at the chain right now without touching anything — the morning watch."""
    broker = _broker(db, user_id)
    if broker is None:
        return {"status": "error", "code": "not_connected", "message": "Connect Zerodha to scan the chain."}
    cfg = LIVE.load_config(db, user_id)
    return {"status": "ok", "scan": LIVE.scan(broker, LIVE.params_of(cfg))}


@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    c = req.config or {}
    date.fromisoformat(str(c.get("start"))), date.fromisoformat(str(c.get("end")))

    def runner(payload, say):
        return SV.run_backtest(payload, say)
    job = SV.start_job(c, user_id, runner)
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
def runs(user_id: int = Depends(login_required)):
    return {"status": "ok", "runs": SV.list_runs()}


@router.get("/runs/{run_id}")
@safe("run")
def run_detail(run_id: str, user_id: int = Depends(login_required)):
    r = SV.load_run(run_id)
    if not r:
        return {"status": "error", "message": "run not found"}
    return {"status": "ok", "run": {**r, "trades": r.get("trades", [])[:1000]}}


@router.delete("/runs/{run_id}")
@safe("delete_run")
def delete_run(run_id: str, user_id: int = Depends(login_required)):
    return {"status": "ok" if SV.delete_run(run_id) else "error"}
