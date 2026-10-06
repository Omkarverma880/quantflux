"""
API routes for the 5 & 60 Minute Range lab (Index Strategies → 5 & 60 Range).

Real orders are possible only when this strategy's own mode is "live" AND the app is out of
paper mode AND the risk fence is down — that check lives in ``research.range_lab.live._order``.
Everything here reads data or edits settings.
"""
from __future__ import annotations

import csv
import io as _io
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
from research.range_lab import live as LIVE
from research.range_lab import presets as PRE
from research.range_lab import service as SV
from research.range_lab import strategy as ST

router = APIRouter()
logger = get_logger("api.range_lab")


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
    mode: str | None = None
    params: dict | None = None


class RunReq(BaseModel):
    config: dict = {}


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from config import settings
    cfg = LIVE.load_config(db, user_id)
    return {
        "status": "ok", "defaults": ST.P.as_dict(), "config": cfg,
        "rules": ST.describe(LIVE.params_of(cfg)),
        "indices": list(ST.LOT_SIZE),
        "lot_sizes": ST.LOT_SIZE, "strike_steps": ST.STRIKE_STEP,
        "modes": ["reversal", "breakout"],
        "moneyness": ["ITM", "ATM", "OTM"],
        "app_paper_mode": bool(settings.PAPER_TRADE or not settings.TRADING_ENABLED),
        "connected": _broker(db, user_id) is not None,
    }


@router.get("/guide")
@safe("guide")
def guide(user_id: int = Depends(login_required)):
    """What each filter does, and the configurations whose numbers are quoted in the app."""
    return {"status": "ok", "window": PRE.WINDOW, "filters": PRE.FILTERS,
            "presets": PRE.PRESETS, "caveats": PRE.CAVEATS, "common": PRE.COMMON}


@router.post("/config")
@safe("config")
def config(req: ConfigReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    updates = req.model_dump(exclude_none=True)
    if updates.get("mode") not in (None, "paper", "live"):
        raise ValueError("mode must be paper or live")
    p = (updates.get("params") or {})
    if p.get("mode") not in (None, "reversal", "breakout"):
        raise ValueError("params.mode must be reversal or breakout")
    if str(p.get("moneyness", "ITM")).upper() not in ("ITM", "ATM", "OTM"):
        raise ValueError("moneyness must be ITM, ATM or OTM")
    return {"status": "ok", "config": LIVE.save_config(db, user_id, updates)}


@router.post("/start")
@safe("start")
def start(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    if broker is None:
        return {"status": "error", "code": "not_connected",
                "message": "Connect Zerodha — the desk needs live index and option quotes."}
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


@router.get("/runs/{run_id}/export.csv")
def export(run_id: str, what: str = "trades", user_id: int = Depends(login_required)):
    """The whole run as CSV — every trade, not the 500 the page shows."""
    r = SV.load_run(run_id) or {}
    buf = _io.StringIO()
    w = csv.writer(buf)
    if what == "monthly":
        w.writerow(["month", "net"])
        for m, v in ((r.get("summary") or {}).get("monthly") or {}).items():
            w.writerow([m, v])
    elif what == "summary":
        s = r.get("summary") or {}
        w.writerow(["setting", "value"])
        for k, v in (r.get("params") or {}).items():
            w.writerow([k, v])
        w.writerow([]); w.writerow(["result", "value"])
        for k, v in s.items():
            if not isinstance(v, (dict, list)):
                w.writerow([k, v])
    else:
        cols = ["date", "time", "fill_time", "range", "level", "level_price", "why", "side",
                "strike", "moneyness", "spot_at_signal", "entry", "exit", "exit_time",
                "exit_reason", "held_min", "lots", "qty", "gross", "charges", "pnl"]
        w.writerow(cols)
        for t in r.get("trades") or []:
            w.writerow([t.get(c) for c in cols])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


@router.delete("/runs/{run_id}")
@safe("delete_run")
def delete_run(run_id: str, user_id: int = Depends(login_required)):
    return {"status": "ok" if SV.delete_run(run_id) else "error"}
