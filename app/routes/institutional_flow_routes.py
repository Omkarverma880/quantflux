"""
API routes for the FII_DII_Equity Activity Watcher (Equity Strategies).

Read-only against the market: NSE's published aggregate, quotes through the broker connection
the app already holds, and the app's own daily cache for averages. No orders, no strategy state.

Every payload carries its provenance — source, source URL, the date the data belongs to and the
moment it was read — because a number on this screen without those four things is a number a
trader cannot act on.
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
from research.institutional_flow import session as SESS
from research.institutional_flow import sources as SRC
from research.institutional_flow.service import InstitutionalFlowService

router = APIRouter()
logger = get_logger("api.institutional_flow")

_services: dict[int, InstitutionalFlowService] = {}


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
        return {str(k): json_safe(v) for k, v in o.items() if not str(k).startswith("_")}
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


def _service(db: Session, user_id: int) -> InstitutionalFlowService:
    svc = _services.get(user_id)
    broker = None
    try:
        from core.auth import UserZerodhaAuth
        if UserZerodhaAuth.is_authenticated(db, user_id):
            broker = get_user_broker(db, user_id)
    except Exception:
        broker = None
    if svc is None:
        svc = _services[user_id] = InstitutionalFlowService(broker=broker, user_id=user_id)
    else:
        svc.broker = broker
    return svc


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    svc = _service(db, user_id)
    return {
        "status": "ok",
        "market": SESS.market_status(),
        "connected": svc.broker is not None,
        "universes": [
            {"key": "NIFTY50", "label": "NIFTY 50"},
            {"key": "FII_ACTIVITY", "label": "FII activity"},
            {"key": "DII_ACTIVITY", "label": "DII activity"},
            {"key": "FII_DII", "label": "FII + DII"},
            {"key": "ALL", "label": "All available equities"},
            {"key": "WATCHLIST", "label": "Watchlist"},
        ],
        "data_types": ["aggregate_cash_flow", "intraday_cash_flow", "fii_shareholding_change",
                       "dii_shareholding_change", "stock_price", "stock_volume"],
        "sources": [
            {"name": "NSE — FII/DII trading activity (cash market)", "url": SRC.NSE_FII_DII,
             "provides": "aggregate_cash_flow", "cadence": "once, after the close"},
            {"name": "NSE — NIFTY 50 constituent list", "url": SRC.NIFTY50_CSV,
             "provides": "index membership", "cadence": "as revised"},
            {"name": "Zerodha (your connection)", "url": None,
             "provides": "stock_price, stock_volume", "cadence": "live while the market is open"},
        ],
    }


@router.get("/snapshot")
@safe("snapshot")
def snapshot(universe: str = "NIFTY50", limit: int = 50, refresh: int = 1,
             user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Everything the page needs for the session on screen, in one call."""
    svc = _service(db, user_id)
    snap = svc.build_daily_snapshot(db=db, universe=universe,
                                   limit=max(1, min(int(limit), 250)), refresh=bool(refresh))
    snap["conflict"] = svc.conflict(snap.get("aggregate"))
    # keep the session on record as soon as we have a real reading for it
    if (snap.get("aggregate") or {}).get("available"):
        snap["persisted"] = svc.persist_session(db, snap["aggregate"])
    return snap


@router.get("/aggregate")
@safe("aggregate")
def aggregate(force: int = 0, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "aggregate": _service(db, user_id).fetch_fii_dii_daily(force=bool(force))}


class ImportReq(BaseModel):
    trading_date: str
    institution: str = "FII"
    increased: list[str] = []
    decreased: list[str] = []
    source: str


@router.get("/flow-table")
@safe("flow_table")
def flow_table(days: int = 10, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """FII/DII by date, with sessions NSE has not published yet marked pending."""
    return {"status": "ok", **_service(db, user_id).flow_table(db, days=days)}


@router.get("/stock-activity")
@safe("stock_activity")
def stock_activity(institution: str = "FII", trading_date: str | None = None,
                   user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok",
            **_service(db, user_id).stock_activity(db, trading_date, institution)}


@router.post("/stock-activity/import")
@safe("import_stock_activity")
def import_stock_activity(req: ImportReq, user_id: int = Depends(login_required),
                          db: Session = Depends(get_db)):
    """Record a named stock-level list. The source is required — an unattributed list is not
    evidence, and this screen will not display one as though it were."""
    return {"status": "ok", **_service(db, user_id).import_stock_activity(
        db, req.trading_date, req.institution, req.increased, req.decreased, req.source)}


@router.get("/history")
@safe("history")
def history(days: int = 10, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", **_service(db, user_id).history(db, days=days)}


@router.get("/yesterday")
@safe("yesterday")
def yesterday(universe: str = "NIFTY50", limit: int = 50,
              user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """What was recorded yesterday, and how those names behaved today.

    Stock-level institutional attribution is not published daily, so the "yesterday activity"
    column is populated only where a stored row actually carries it. Rows without it say so
    rather than borrowing a reason from somewhere else.
    """
    from core.models import InstitutionalStockActivity
    svc = _service(db, user_id)
    status = SESS.market_status()
    today = date.fromisoformat(status["session_date"])
    prev = SESS.previous_trading_day(today)

    rows = (db.query(InstitutionalStockActivity)
              .filter(InstitutionalStockActivity.trading_date == prev)
              .filter(InstitutionalStockActivity.institution.in_(["FII", "DII"]))
              .all())
    if not rows:
        return {"status": "ok", "available": False, "previous_session": prev.isoformat(),
                "rows": [],
                "message": ("No stock-level institutional activity was recorded for "
                            f"{prev.strftime('%d %b %Y')}. Daily per-stock FII/DII attribution "
                            "is not published by the current source, so there is nothing to "
                            "carry forward.")}

    snap = svc.build_daily_snapshot(db=db, universe=universe, limit=limit)
    by_sym = {s["symbol"]: s for s in snap.get("stocks", [])}
    out = []
    for r in rows:
        t = by_sym.get(r.symbol) or {}
        from research.institutional_flow.service import reaction
        out.append({
            "symbol": r.symbol, "institution": r.institution,
            "yesterday_activity": r.activity_type, "yesterday_date": prev.isoformat(),
            "price": t.get("price"), "price_change_pct": t.get("price_change_pct"),
            "volume": t.get("volume"), "relative_volume": t.get("relative_volume"),
            "reaction": reaction(t.get("price_change_pct"), t.get("relative_volume"),
                                 r.activity_type),
        })
    return {"status": "ok", "available": True, "previous_session": prev.isoformat(), "rows": out}


@router.post("/sync")
@safe("sync")
def sync(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Force a fresh read and write the session down. Used by the manual refresh."""
    svc = _service(db, user_id)
    svc.invalidate()
    agg = svc.fetch_fii_dii_daily(force=True)
    return {"status": "ok", "aggregate": agg, "persisted": svc.persist_session(db, agg)}
