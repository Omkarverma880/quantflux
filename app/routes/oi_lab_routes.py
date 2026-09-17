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
from research.oi_lab import paper as PAPER
from research.oi_lab import signal_backtest as SB
from research.oi_lab import signals as SG
from research.oi_lab.signal_hub import HUB

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
    for u in set(HS.available_underlyings()) | {HS.UNDERLYING}:
        HS.ensure_started(u)
    return {"status": "ok", "indices": [{"key": k, **v} for k, v in IX.INDICES.items()],
            "history": HS.status(), "histories": HS.all_status(),
            "trade_underlyings": list(SG.TRADE_UNDERLYINGS)}


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
def history(underlying: str = "NIFTY", user_id: int = Depends(login_required)):
    u = underlying.upper()
    study = HS.get(u)
    base = {"underlying": u, "history": HS.status(u), "available": HS.all_status()}
    if study is None:
        return {"status": "pending", **base}
    return {"status": "ok", **base, "report": study.report()}


@router.post("/history/rebuild")
@safe("history_rebuild")
def history_rebuild(underlying: str = "NIFTY", user_id: int = Depends(login_required)):
    HS.ensure_started(underlying, force=True)
    return {"status": "ok", "history": HS.status(underlying)}


# ── signal desk (NIFTY / SENSEX) ─────────────────────────────────────
@router.get("/desk")
@safe("desk")
def desk(index: str = "NIFTY", user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    if broker is None:
        return NOT_CONNECTED
    return HUB.desk(broker, index)


@router.get("/signal-backtest")
@safe("signal_backtest")
def signal_backtest(index: str = "NIFTY", user_id: int = Depends(login_required)):
    u = index.upper()
    summary = SB.get(u)
    out = {"status": "ok" if summary else "pending", "underlying": u, "backtest_status": SB.status(u),
           "history": HS.status(u), "rules": {"version": SG.RULES_VERSION, "params": SG.DEFAULTS}}
    if summary:
        out["summary"] = {k: v for k, v in summary.items() if not k.startswith("_")}
    return out


@router.post("/signal-backtest/run")
@safe("signal_backtest_run")
def signal_backtest_run(index: str = "NIFTY", user_id: int = Depends(login_required)):
    SB.ensure_started(index.upper(), force=True)
    return {"status": "ok", "backtest_status": SB.status(index)}


# ── paper trading (never places orders) ──────────────────────────────
class PaperConfigReq(BaseModel):
    config: dict


class PaperOpenReq(BaseModel):
    signal_id: int
    mode: str = "SWING"
    lots: int = 1


@router.get("/paper/config")
@safe("paper_config")
def paper_config(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "config": PAPER.load_config(db, user_id), "defaults": PAPER.DEFAULT_CONFIG,
            "setups": SG.SETUPS, "modes": list(SG.MODES), "indices": list(SG.TRADE_UNDERLYINGS)}


@router.post("/paper/config")
@safe("paper_config_save")
def paper_config_save(req: PaperConfigReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "config": PAPER.save_config(db, user_id, req.config)}


@router.get("/paper/positions")
@safe("paper_positions")
def paper_positions(days: int = 30, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return PAPER.journal(db, user_id, days)


@router.post("/paper/open")
@safe("paper_open")
def paper_open(req: PaperOpenReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from core.models import OILabSignal
    broker = _broker(db, user_id)
    if broker is None:
        return NOT_CONNECTED
    sig = db.query(OILabSignal).filter(OILabSignal.id == req.signal_id).first()
    if sig is None:
        return {"status": "error", "message": "signal not found"}
    try:
        row = PAPER.open_from_signal(db, user_id, broker, sig, req.mode.upper(), req.lots, "MANUAL")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "position": PAPER.serialize(row)}


@router.post("/paper/{position_id}/close")
@safe("paper_close")
def paper_close(position_id: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    try:
        row = PAPER.close_manual(db, user_id, broker, position_id) if broker is not None else None
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if row is None:
        return NOT_CONNECTED
    return {"status": "ok", "position": PAPER.serialize(row)}
