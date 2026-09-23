"""
API routes for the Hunter (Equity Strategies → Hunter).

Screener only: it reads market data and writes its own scan snapshots. There is no order path in
this router. Scans run as background jobs with the polling contract the other labs use.
"""
from __future__ import annotations

import math
import traceback
from functools import wraps

import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.broker import get_user_broker
from core.database import get_db
from core.logger import get_logger
from research.hunter import patterns as PT
from research.hunter import scan as SCAN
from research.hunter import service as SV
from research.hunter import universe as UNIV

router = APIRouter()
logger = get_logger("api.hunter")


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
    if isinstance(o, np.ndarray):
        return [json_safe(v) for v in o.tolist()]
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


class ScanReq(BaseModel):
    min_rs: float | None = None
    refresh_universe: bool = False


class ConfigReq(BaseModel):
    auto_scan: bool | None = None
    min_rs: int | None = None


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    u = UNIV.load()
    return {"status": "ok", "stages": SCAN.STAGES, "screens": PT.SCREENS,
            "stage_labels": SCAN.STAGE_LABEL, "stage_blurbs": SCAN.STAGE_BLURB,
            "params": PT.P.as_dict(), "config": SV.load_config(db, user_id),
            "universe": {"source": u.get("source"), "fetched": u.get("fetched"), "count": u.get("count"),
                         "note": u.get("note")},
            "connected": _broker(db, user_id) is not None, "scanning": SV.scanning(),
            "how": ["Strong stocks only: above a rising 200-day average, near the 52-week high, "
                    "outperforming most of the NIFTY 500.",
                    "A base is a tight range under a ceiling where volatility contracts and trading dries up.",
                    "A breakout is a close above that ceiling on at least 1.3× the usual volume.",
                    "Stages follow the trade: forming → fresh breakout → climbing → played out (stop lost).",
                    "Screener only: it finds setups, it does not place orders."]}


@router.get("/board")
@safe("board")
def board(stage: str = "", industry: str = "", q: str = "", sort: str = "rs_rating",
          limit: int = 400, scan_date: str = "", screens: str = "", combine: str = "any",
          user_id: int = Depends(login_required)):
    picked = [s.strip() for s in (screens or "").split(",") if s.strip()]
    return SV.latest(stage or None, industry or None, q or None, sort, limit, scan_date or None, picked, combine)


@router.get("/stock/{symbol}")
@safe("stock")
def stock(symbol: str, user_id: int = Depends(login_required)):
    r = SV.one(symbol)
    return {"status": "ok", "stock": r} if r else {"status": "error", "message": "not in the last scan"}


@router.get("/chart/{symbol}")
@safe("chart")
def chart(symbol: str, bars: int = 140, user_id: int = Depends(login_required)):
    c = SV.chart(symbol, bars)
    return {"status": "ok", "chart": c} if c else {"status": "error", "message": "no cached candles for that stock"}


@router.post("/scan")
@safe("scan")
def start(req: ScanReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    if broker is None:
        return {"status": "error", "code": "not_connected",
                "message": "Connect Zerodha — the scan needs daily candles for 500 stocks."}
    if SV.scanning():
        return {"status": "error", "message": "a scan is already running"}
    cfg = SV.load_config(db, user_id)
    job = SV.start_scan(broker, user_id, float(req.min_rs if req.min_rs is not None else cfg.get("min_rs", 70)),
                        req.refresh_universe)
    return {"status": "ok", "job": {k: v for k, v in job.items() if k != "user_id"}}


@router.get("/evidence")
@safe("evidence")
def evidence(user_id: int = Depends(login_required)):
    from research.hunter import evidence as EVID
    return EVID.load()


@router.post("/evidence")
@safe("build_evidence")
def build_evidence(years: int = 5, user_id: int = Depends(login_required)):
    if SV.scanning():
        return {"status": "error", "message": "a scan is running — try again once it finishes"}
    job = SV.start_evidence(user_id, max(1, min(10, years)))
    return {"status": "ok", "job": {k: v for k, v in job.items() if k != "user_id"}}


@router.get("/job/{job_id}")
@safe("job")
def job(job_id: str, user_id: int = Depends(login_required)):
    j = SV.get_job(job_id, user_id)
    if j is None:
        return {"status": "error", "message": "job not found"}
    return {"status": "ok", "job": {k: v for k, v in j.items() if k != "user_id"}}


@router.post("/config")
@safe("config")
def config(req: ConfigReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "config": SV.save_config(db, user_id, req.model_dump(exclude_none=True))}
