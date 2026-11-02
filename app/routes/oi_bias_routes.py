"""
API routes for the OI Lab's Bias Tape (OI Lab → Bias Tape).

    /sessions   which dates have a recording, for the date picker
    /series     one session at 1 / 5 / 15 / 30 / 60 minutes, with the index beside the score
    /record     capture the current minute now, for when you want a reading without waiting
    /purge      delete recordings, only when explicitly asked

Read-only with respect to the OI Lab itself: the score is taken from its existing snapshot
and stored. No OI Lab behaviour is changed by anything here, and there is no order path.
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
from research.oi_lab import bias_tape as TAPE
from research.oi_lab import indices as IX

router = APIRouter()
logger = get_logger("api.oi_bias")


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
                return {"status": "error",
                        "message": f"{name}: {type(exc).__name__} — {exc}"[:300]}
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


def _index(v: str | None) -> str:
    k = (v or "NIFTY").upper()
    if k not in IX.INDICES:
        raise ValueError(f"index must be one of {', '.join(IX.INDICES)}")
    return k


class PurgeReq(BaseModel):
    index: str | None = None
    before: str | None = None


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {
        "status": "ok",
        "connected": _broker(db, user_id) is not None,
        "timeframes": list(TAPE.TIMEFRAMES),
        "recorded_indices": list(TAPE.DEFAULT_INDICES),
        "indices": [{"key": k, "label": IX.INDICES[k]["label"]} for k in IX.INDICES],
        "note": ("The tape records while the market is open and Zerodha is connected. It "
                 "cannot be backfilled: the score is computed from the live option chain, "
                 "and that chain is gone once the minute has passed."),
    }


@router.get("/sessions")
@safe("sessions")
def sessions(index: str = "NIFTY", limit: int = 60,
             user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", **TAPE.sessions(db, _index(index), limit)}


@router.get("/series")
@safe("series")
def series(index: str = "NIFTY", day: str | None = None, timeframe: int = 1,
           user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    if day:
        date.fromisoformat(day)
    return {"status": "ok", **TAPE.series(db, _index(index), day, timeframe)}


@router.post("/record")
@safe("record")
def record(index: str = "NIFTY", user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    """Capture this minute now, rather than waiting for the background loop."""
    broker = _broker(db, user_id)
    if broker is None:
        return {"status": "ok",
                "result": {"written": [], "skipped": ["no broker"], "errors": [],
                           "message": "Connect Zerodha — the score is read from the live chain."}}
    from research.oi_lab.live import SERVICE
    return {"status": "ok",
            "result": TAPE.record(db, broker, SERVICE, indices=[_index(index)])}


@router.post("/purge")
@safe("purge")
def purge(req: PurgeReq, user_id: int = Depends(login_required),
          db: Session = Depends(get_db)):
    idx = _index(req.index) if req.index else None
    if req.before:
        date.fromisoformat(req.before)
    return {"status": "ok", **TAPE.purge(db, idx, req.before)}
