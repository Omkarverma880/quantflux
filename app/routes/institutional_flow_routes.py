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
from research.institutional_flow import symbols as SYM
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
        "data_types": ["aggregate_cash_flow", "intraday_cash_flow", "stock_delivery",
                       "fii_shareholding_change",
                       "mf_shareholding_change", "dii_shareholding_change",
                       "stock_price", "stock_volume"],
        "sources": [
            {"name": "NSE — FII/DII trading activity (cash market)", "url": SRC.NSE_FII_DII,
             "provides": "aggregate_cash_flow (buy, sell, net)",
             "cadence": "once, after the close"},
            {"name": "Moneycontrol — institutional trading activity (provisional)",
             "url": SRC.MC_MARKETSTATS,
             "provides": "aggregate_cash_flow (buy, sell, net) — cross-check and fallback",
             "cadence": "once, after the close"},
            {"name": "Moneycontrol — FII/DII activity history", "url": SRC.MC_FII_DII,
             "provides": "aggregate_cash_flow (net only) for ~30 sessions, plus FII derivatives",
             "cadence": "daily"},
            {"name": "Economic Times — bought by FII", "url": SRC.ET_BOUGHT_BY_FII,
             "provides": "fii_shareholding_change (increase) — a percentage, never a rupee value",
             "cadence": "as shareholdings are filed, quarterly"},
            {"name": "Economic Times — sold by FII", "url": SRC.ET_SOLD_BY_FII,
             "provides": "fii_shareholding_change (decrease) — a percentage, never a rupee value",
             "cadence": "as shareholdings are filed, quarterly"},
            {"name": "Economic Times — bought by MF", "url": SRC.ET_BOUGHT_BY_MF,
             "provides": "mf_shareholding_change (increase) — a percentage, never a rupee value",
             "cadence": "as shareholdings are filed, quarterly"},
            {"name": "Economic Times — sold by MF", "url": SRC.ET_SOLD_BY_MF,
             "provides": "mf_shareholding_change (decrease) — a percentage, never a rupee value",
             "cadence": "as shareholdings are filed, quarterly"},
            {"name": "NSE — securities bhavcopy with delivery", "url": SRC.BHAV_URL % "DDMMYYYY",
             "provides": "stock_delivery, stock_price — per stock, never attributed to anyone",
             "cadence": "once, after the close"},
            {"name": "NSE — list of equities available for trading", "url": SYM.EQUITY_LIST_CSV,
             "provides": "company name to trading symbol", "cadence": "as revised"},
            {"name": "NSE — NIFTY 50 constituent list", "url": SRC.NIFTY50_CSV,
             "provides": "index membership", "cadence": "as revised"},
            {"name": "Zerodha (your connection)", "url": None,
             "provides": "stock_price, stock_volume — price and volume only, never institutional "
                         "attribution",
             "cadence": "live while the market is open"},
        ],
        "symbol_index": {"count": SYM.index().get("count"),
                         "source": SYM.index().get("source")},
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
def yesterday(limit: int = 15, user_id: int = Depends(login_required),
              db: Session = Depends(get_db)):
    """Yesterday's stored FII list, measured against today's price and volume.

    This reads the snapshot written for the previous session rather than re-deriving a list, so
    "yesterday" means the list as it stood yesterday — which is the only way the comparison says
    anything. When no snapshot exists yet, it says so instead of showing today's list twice.
    """
    return {"status": "ok", **_service(db, user_id).yesterday_tracker(db, limit=limit)}


@router.get("/fii-stocks")
@safe("fii_stocks")
def fii_stocks(refresh: int = 0, user_id: int = Depends(login_required),
               db: Session = Depends(get_db)):
    """The two published FII lists, resolved to NSE symbols and priced with today's tape.

    What these lists report is a change in FII *shareholding*, as a percentage of equity. The
    payload says so on every row, and carries no rupee figure, because none is published.
    """
    return {"status": "ok", **_service(db, user_id).fii_stock_lists(refresh=bool(refresh))}


@router.get("/dii-stocks")
@safe("dii_stocks")
def dii_stocks(refresh: int = 0, user_id: int = Depends(login_required),
               db: Session = Depends(get_db)):
    """Why there is no security-level DII list — and the mutual fund lists that do exist.

    The DII aggregate is published; a DII stock list is not. Mutual fund shareholding is, and
    funds are the largest part of DII, so it is offered under its own name with that stated
    rather than presented as the DII figure broken down.
    """
    return {"status": "ok", **_service(db, user_id).dii_stock_note(refresh=bool(refresh))}


@router.get("/derivatives")
@safe("derivatives")
def derivatives(days: int = 15, user_id: int = Depends(login_required),
                db: Session = Depends(get_db)):
    """FII's four derivative books per session, beside the index close."""
    return {"status": "ok", **_service(db, user_id).derivatives_history(days=days)}


@router.get("/live-movers")
@safe("live_movers")
def live_movers(source: str = "FII_BOUGHT", limit: int = 10,
                user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """The named stocks that are moving right now, with the order book behind each.

    Polled during the session, so it is kept deliberately cheap: one quote call for the list,
    and a per-minute volume rate measured between successive readings rather than recomputed
    from history.
    """
    return {"status": "ok", **_service(db, user_id).live_movers(
        db=db, source=source, limit=limit)}


@router.get("/delivery-screen")
@safe("delivery_screen")
def delivery_screen(lookback: int = 11, min_turnover_cr: float = 25.0, universe: str = "ALL",
                    rank: str = "surge", limit: int = 40,
                    user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Which stocks were taken to delivery at a rate unlike their own recent norm.

    The only daily per-stock figure NSE publishes that speaks to intent. It attributes nothing:
    the payload says in two places that this is not FII activity, because a delivery ranking
    sitting on an institutional-flow screen is exactly the thing a reader would assume it was.
    """
    return {"status": "ok", **_service(db, user_id).delivery_screen(
        lookback=lookback, min_turnover_cr=min_turnover_cr, universe=universe,
        rank=rank, limit=limit, db=db)}


@router.post("/backfill")
@safe("backfill")
def backfill(force: int = 0, user_id: int = Depends(login_required),
             db: Session = Depends(get_db)):
    """Fill the by-date table from the published multi-session series."""
    svc = _service(db, user_id)
    out = svc.backfill_history(db, force=bool(force))
    svc.invalidate()
    return {"status": "ok", **out}


@router.post("/fii-stocks/capture")
@safe("capture_fii_stocks")
def capture_fii_stocks(trading_date: str | None = None, institution: str = "FII",
                       user_id: int = Depends(login_required),
                       db: Session = Depends(get_db)):
    """Store today's reading of both lists, so tomorrow has something to compare against."""
    return {"status": "ok",
            **_service(db, user_id).sync_fii_stock_lists(
                db, trading_date, refresh=True, institution=institution)}


@router.post("/sync")
@safe("sync")
def sync(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Force a fresh read and write the session down. Used by the manual refresh."""
    svc = _service(db, user_id)
    svc.invalidate()
    agg = svc.fetch_fii_dii_daily(force=True)
    return {"status": "ok", "aggregate": agg,
            "persisted": svc.persist_session(db, agg),
            "backfilled": svc.backfill_history(db),
            "stock_lists": svc.sync_fii_stock_lists(db, refresh=True, institution="FII"),
            "mf_lists": svc.sync_fii_stock_lists(db, refresh=True, institution="MF")}
