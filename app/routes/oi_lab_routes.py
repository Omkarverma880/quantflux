"""
API routes for the OI Lab — live option-chain X-ray, entry zones and the 3-year OI study.

Read-only research. Never places orders.
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
from research.oi_lab import history as HS
from research.oi_lab import indices as IX
from research.oi_lab import live as LV

router = APIRouter()
logger = get_logger("api.oi_lab")


def json_safe(o):
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
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
            except Exception as exc:
                logger.error("%s failed: %s | %s", name, exc, traceback.format_exc())
                msg = str(exc)
                if "token" in msg.lower() and ("invalid" in msg.lower() or "expired" in msg.lower()):
                    msg = "Zerodha session expired — log in to Zerodha again."
                return {"status": "error", "message": f"{name}: {msg}"[:400]}
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


NOT_CONNECTED = {"status": "error", "code": "not_connected",
                 "message": "Connect Zerodha to load live option chains. The History tab works without it."}


class SnapshotReq(BaseModel):
    index: str = "NIFTY"
    expiry: str | None = None
    window: int = 7


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required)):
    HS.ensure_started()
    return {"status": "ok", "indices": [{"key": k, **v} for k, v in IX.INDICES.items()],
            "history": HS.status()}


@router.get("/expiries")
@safe("expiries")
def expiries(index: str = "NIFTY", user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    if broker is None:
        return NOT_CONNECTED
    return {"status": "ok", "index": index.upper(), "expiries": LV.SERVICE.expiries(broker, index)}


@router.post("/snapshot")
@safe("snapshot")
def snapshot(req: SnapshotReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    if broker is None:
        return NOT_CONNECTED
    return LV.SERVICE.snapshot(broker, req.index, req.expiry or None, req.window)


@router.get("/overview")
@safe("overview")
def overview(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    if broker is None:
        return NOT_CONNECTED
    return LV.overview(LV.SERVICE, broker)


@router.get("/contract-series")
@safe("contract_series")
def contract_series(token: int, user_id: int = Depends(login_required)):
    return LV.contract_series(LV.SERVICE, token)


@router.get("/history")
@safe("history")
def history(user_id: int = Depends(login_required)):
    study = HS.get()
    if study is None:
        return {"status": "pending", "history": HS.status()}
    return {"status": "ok", "history": HS.status(), "report": study.report()}


@router.post("/history/rebuild")
@safe("history_rebuild")
def history_rebuild(user_id: int = Depends(login_required)):
    HS.ensure_started(force=True)
    return {"status": "ok", "history": HS.status()}
