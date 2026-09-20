"""
API routes for My Equity Workspace (Equity Strategies → My Equity Workspace).

Your own research list: stocks, the levels you are waiting for, the daily technical picture
and a per-stock X-ray. Strictly read-only against the market — quotes and candles only, no
orders, no strategy state.
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
from research.my_equity import cache as CACHE
from research.my_equity import fundamentals as FN
from research.my_equity import news as NEWS
from research.my_equity import store as ST
from research.my_equity.service import MyEquityService

router = APIRouter()
logger = get_logger("api.my_equity")

_services: dict[int, MyEquityService] = {}


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
    """Error envelope. Handlers are plain ``def`` so FastAPI runs them in its thread pool —
    a broker fetch must never block the event loop."""
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
                msg = str(exc)
                if "token" in msg.lower() and ("invalid" in msg.lower() or "expired" in msg.lower()):
                    return {"status": "error", "code": "not_connected",
                            "message": "Your Zerodha session has expired — log in again."}
                return {"status": "error", "message": f"{name}: {type(exc).__name__} — {exc}"[:300]}
        return wrapper
    return deco


def _service(db: Session, user_id: int) -> MyEquityService:
    """One service per user, with the broker attached only when Zerodha is connected.

    Without a broker the workspace still loads: the list, the saved levels and everything the
    daily cache already holds. Only live prices go missing.
    """
    from core.auth import UserZerodhaAuth
    broker = None
    try:
        if UserZerodhaAuth.is_authenticated(db, user_id):
            broker = get_user_broker(db, user_id)
    except Exception:
        broker = None
    svc = _services.get(user_id)
    if svc is None:
        svc = MyEquityService(broker, user_id=user_id)
        _services[user_id] = svc
    svc.broker = broker
    return svc


class AddReq(BaseModel):
    symbol: str
    exchange: str | None = None
    levels: list | str | None = None
    note: str | None = ""
    added_on: str | None = None          # the research date, not the day it was typed in
    touch_pct: float | None = 0.25
    category: str | None = "SWING"       # INVESTMENT | SWING


class UpdateReq(BaseModel):
    levels: list | str | None = None
    note: str | None = None
    added_on: str | None = None
    touch_pct: float | None = None
    archived: bool | None = None
    category: str | None = None
    sector: str | None = None
    industry: str | None = None


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    svc = _service(db, user_id)
    return {"status": "ok", "connected": svc.broker is not None,
            "max_stocks": ST.MAX_STOCKS, "max_levels": ST.MAX_LEVELS,
            "categories": list(ST.CATEGORIES)}


@router.get("/search")
@safe("search")
def search(q: str, limit: int = 15, user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    svc = _service(db, user_id)
    if svc.broker is None:
        return {"status": "error", "code": "not_connected",
                "message": "Connect Zerodha to search the stock list."}
    return {"status": "ok", "results": svc.search(q, limit)}


@router.get("/stocks")
@safe("stocks")
def stocks(refresh: int = 1, force: int = 0, user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    """The workspace table. ``refresh=0`` skips every broker call and serves what is cached."""
    svc = _service(db, user_id)
    return svc.rows(db, user_id, refresh=bool(refresh), force=bool(force))


@router.post("/stocks")
@safe("add_stock")
def add_stock(req: AddReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    svc = _service(db, user_id)
    symbol = ST.clean_symbol(req.symbol)
    hit = svc.resolve(symbol, req.exchange) if svc.broker else None
    if svc.broker and hit is None:
        return {"status": "error",
                "message": f"{symbol} is not listed as a cash equity on NSE or BSE"}
    exchange = (hit or {}).get("exchange") or (req.exchange or "NSE")
    sector, industry = FN.sector_of(symbol, exchange)          # cached; failure just leaves it blank
    row = ST.add(db, user_id, symbol=symbol, exchange=exchange,
                 token=(hit or {}).get("token"), company=(hit or {}).get("company"),
                 levels=req.levels, note=req.note or "", added_on=req.added_on,
                 touch_pct=req.touch_pct if req.touch_pct is not None else 0.25,
                 category=req.category or "SWING", sector=sector, industry=industry)
    svc._rows_cache.pop(user_id, None)
    return {"status": "ok", "stock": ST.to_dict(row)}


@router.patch("/stocks/{stock_id}")
@safe("update_stock")
def update_stock(stock_id: int, req: UpdateReq, user_id: int = Depends(login_required),
                 db: Session = Depends(get_db)):
    row = ST.update(db, user_id, stock_id, **req.model_dump(exclude_none=True))
    _service(db, user_id)._rows_cache.pop(user_id, None)       # the table must show the edit at once
    return {"status": "ok", "stock": ST.to_dict(row)}


@router.delete("/stocks/{stock_id}")
@safe("remove_stock")
def remove_stock(stock_id: int, user_id: int = Depends(login_required),
                 db: Session = Depends(get_db)):
    if not ST.remove(db, user_id, stock_id):
        return {"status": "error", "message": "that stock is not in your workspace"}
    return {"status": "ok"}


@router.get("/xray/{stock_id}")
@safe("xray")
def xray(stock_id: int, news: int = 1, fundamentals: int = 1, refresh: int = 1,
         user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    svc = _service(db, user_id)
    return svc.xray(db, user_id, stock_id, with_news=bool(news),
                    with_fundamentals=bool(fundamentals), refresh=bool(refresh))


@router.post("/fundamentals/{stock_id}/refresh")
@safe("refresh_fundamentals")
def refresh_fundamentals(stock_id: int, user_id: int = Depends(login_required),
                         db: Session = Depends(get_db)):
    """Re-fetch the company data, and adopt the sector unless you set it by hand."""
    row = ST.get(db, user_id, stock_id)
    if row is None:
        return {"status": "error", "message": "that stock is not in your workspace"}
    data = FN.fetch(row.symbol, row.exchange, force=True)
    if data.get("sector") and row.sector_source != "manual":
        ST.update(db, user_id, stock_id, sector=data["sector"],
                  industry=data.get("industry"), sector_source="auto")
    return {"status": "ok", "fundamentals": data}


@router.get("/news/{stock_id}")
@safe("news")
def news(stock_id: int, force: int = 0, user_id: int = Depends(login_required),
         db: Session = Depends(get_db)):
    row = ST.get(db, user_id, stock_id)
    if row is None:
        return {"status": "error", "message": "that stock is not in your workspace"}
    return NEWS.headlines(row.symbol, row.company, force=bool(force))


@router.post("/cache/{stock_id}/rebuild")
@safe("rebuild_cache")
def rebuild_cache(stock_id: int, user_id: int = Depends(login_required),
                  db: Session = Depends(get_db)):
    """Throw away this stock's cached daily history and pull it again from scratch."""
    row = ST.get(db, user_id, stock_id)
    if row is None:
        return {"status": "error", "message": "that stock is not in your workspace"}
    svc = _service(db, user_id)
    if svc.broker is None:
        return {"status": "error", "code": "not_connected",
                "message": "Connect Zerodha to rebuild the history."}
    CACHE.drop(row.symbol, row.exchange)
    svc._rows_cache.pop(user_id, None)
    df = svc._daily(row.symbol, svc.token_for(row), row.exchange, True)
    return {"status": "ok", "sessions": int(len(df)),
            "cache": CACHE.status(row.symbol, row.exchange)}
