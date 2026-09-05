"""
API routes for the Chart Simulation workspace.

Chart data, the indicator catalogue, the F&O chain pickers, and CRUD for the
horizontal levels a user saves per instrument. Read-only with respect to
trading — nothing here places an order or touches strategy state.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from core.models import ChartLevel
from research.simulation import SimulationService
from research.simulation.config import (
    INDICATORS, TF_DEFAULT_DAYS, TIMEFRAMES, load_config, save_config,
)
from research.simulation.service import INDEX_SPOT

router = APIRouter()
logger = get_logger("api.simulation")

_services: dict[int, SimulationService] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_service(broker: Broker, user_id: int) -> SimulationService:
    svc = _services.get(user_id)
    if svc is None:
        svc = SimulationService(broker, user_id=user_id)
        _services[user_id] = svc
    else:
        svc.broker = broker
        svc.universe.broker = broker
    svc.user_id = user_id
    return svc


class ChartReq(BaseModel):
    kind: str = "equity"
    symbol: str | None = None
    index: str | None = None
    expiry: str | None = None
    strike: float | None = None
    opt_type: str = "CE"
    tradingsymbol: str | None = None
    overrides: dict | None = None


class LevelReq(BaseModel):
    instrument_key: str
    symbol: str | None = None
    kind: str = "equity"
    price: float
    label: str | None = None
    color: str | None = None
    note: str | None = None
    type: str = "line"          # line | text
    anchor: str | None = None   # candle timestamp a text note pins to


def _level_dict(r: ChartLevel) -> dict:
    return {"id": r.id, "instrument_key": r.instrument_key, "symbol": r.symbol, "kind": r.kind,
            "price": float(r.price), "label": r.label, "color": r.color or "#f59e0b",
            "note": r.note, "type": r.type or "line", "anchor": r.anchor,
            "created_at": r.created_at.strftime("%d-%b-%Y") if r.created_at else None}


def _levels_for(db, user_id: int, instrument_key: str) -> list[dict]:
    rows = (db.query(ChartLevel)
              .filter(ChartLevel.user_id == user_id,
                      ChartLevel.instrument_key == instrument_key,
                      ChartLevel.active.is_(True))
              .order_by(ChartLevel.price.desc()).all())
    return [_level_dict(r) for r in rows]


@router.get("/meta")
def meta(user_id: int = Depends(login_required)):
    return {"status": "ok", "indicators": INDICATORS, "timeframes": TIMEFRAMES,
            "indices": list(INDEX_SPOT.keys()), "history_days": TF_DEFAULT_DAYS,
            "config": load_config()}


@router.post("/chart")
def chart(payload: ChartReq, user_id: int = Depends(login_required),
          db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    svc = _get_service(broker, user_id)
    sel = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    overrides = sel.pop("overrides", None)
    try:
        inst = svc.resolve(kind=sel.get("kind") or "equity", symbol=sel.get("symbol") or "",
                           index=sel.get("index") or "", expiry=sel.get("expiry") or "",
                           strike=sel.get("strike"), opt_type=sel.get("opt_type") or "CE",
                           tradingsymbol=sel.get("tradingsymbol") or "")
        levels = _levels_for(db, user_id, inst["quote_key"]) if inst.get("status") == "ok" else []
        res = svc.chart(sel, overrides, levels=levels)
    except Exception as exc:
        logger.error("simulation chart failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    return res


@router.get("/chain")
def chain(name: str, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_service(broker, user_id).chain(name)
    except Exception as exc:
        logger.error("simulation chain failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ── saved levels ─────────────────────────────────────────────────────
@router.get("/levels")
def get_levels(instrument_key: str, user_id: int = Depends(login_required),
               db: Session = Depends(get_db)):
    return {"status": "ok", "levels": _levels_for(db, user_id, instrument_key)}


@router.get("/levels/all")
def all_levels(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    rows = (db.query(ChartLevel)
              .filter(ChartLevel.user_id == user_id, ChartLevel.active.is_(True))
              .order_by(ChartLevel.instrument_key, ChartLevel.price.desc()).all())
    return {"status": "ok", "levels": [_level_dict(r) for r in rows]}


@router.post("/levels")
def add_level(payload: LevelReq, user_id: int = Depends(login_required),
              db: Session = Depends(get_db)):
    try:
        row = ChartLevel(user_id=user_id, instrument_key=payload.instrument_key.strip().upper(),
                         symbol=(payload.symbol or "").strip().upper(), kind=payload.kind,
                         price=round(float(payload.price), 2), label=payload.label,
                         color=payload.color or "#f59e0b", note=payload.note,
                         type=(payload.type if payload.type in ("line", "text") else "line"),
                         anchor=payload.anchor, active=True)
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"status": "ok", "level": _level_dict(row)}
    except Exception as exc:
        db.rollback()
        logger.error("simulation add level failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.put("/levels/{level_id}")
def update_level(level_id: int, payload: dict | None = None,
                 user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    row = db.query(ChartLevel).filter(ChartLevel.id == level_id,
                                      ChartLevel.user_id == user_id).first()
    if not row:
        return {"status": "error", "message": "Level not found"}
    p = payload or {}
    try:
        if p.get("price") is not None:
            row.price = round(float(p["price"]), 2)
        for f in ("label", "color", "note", "anchor"):
            if p.get(f) is not None:
                setattr(row, f, p[f])
        if p.get("type") in ("line", "text"):
            row.type = p["type"]
        db.commit()
        db.refresh(row)
        return {"status": "ok", "level": _level_dict(row)}
    except Exception as exc:
        db.rollback()
        return {"status": "error", "message": str(exc)}


@router.delete("/levels/{level_id}")
def delete_level(level_id: int, user_id: int = Depends(login_required),
                 db: Session = Depends(get_db)):
    row = db.query(ChartLevel).filter(ChartLevel.id == level_id,
                                      ChartLevel.user_id == user_id).first()
    if not row:
        return {"status": "error", "message": "Level not found"}
    db.delete(row)
    db.commit()
    return {"status": "ok", "deleted": level_id}


@router.get("/config")
def get_config(user_id: int = Depends(login_required)):
    return {"status": "ok", "config": load_config()}


@router.post("/config")
def post_config(payload: dict | None = None, user_id: int = Depends(login_required)):
    return {"status": "ok", "config": save_config(payload or {})}
