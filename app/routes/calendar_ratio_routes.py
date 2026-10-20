"""
API routes for the Calendar Ratio Spread (Index Strategies → Calendar Ratio).

    /meta       which indices are configured, the default structure, what the store holds
    /live       today's six legs from the broker, for NIFTY or SENSEX
    /backtest   the same structure over stored history, on real option prints
    /diagnose   whether the stored chain can support a calendar at all, before anyone reads
                a P&L — this strategy needs two expiries and deep strikes, and saying so
                plainly is more useful than an empty result

No order path. The live endpoint reads instruments and quotes only.
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
from research.calendar_ratio import backtest as BT
from research.calendar_ratio import live as LV
from research.calendar_ratio import structure as ST
from research.oi_lab import indices as IX

router = APIRouter()
logger = get_logger("api.calendar_ratio")

SUPPORTED = ["NIFTY", "SENSEX", "BANKNIFTY", "BANKEX", "FINNIFTY", "MIDCPNIFTY"]


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


def _params(d: dict | None) -> ST.Params:
    d = dict(d or {})
    idx = str(d.get("index", "NIFTY")).upper()
    if idx not in IX.INDICES:
        raise ValueError(f"index must be one of {', '.join(SUPPORTED)}")
    d["index"] = idx
    p = ST.Params.from_dict(d)
    p.entry_minute = max(9 * 60 + 15, min(int(p.entry_minute), 15 * 60 + 20))
    p.exit_minute = max(9 * 60 + 16, min(int(p.exit_minute), 15 * 60 + 29))
    p.tolerance_pct = max(0.0, min(float(p.tolerance_pct), 100.0))
    p.tolerance_abs = max(0.0, min(float(p.tolerance_abs), 500.0))
    p.hold_days = max(1, min(int(p.hold_days), 40))
    if p.exit_mode not in ("intraday", "weekly_expiry", "fixed_days"):
        p.exit_mode = "weekly_expiry"
    if p.same_expiry_policy not in ("skip", "next_month"):
        p.same_expiry_policy = "skip"
    if not p.legs:
        raise ValueError("the structure needs at least one leg")
    if len(p.legs) > 12:
        raise ValueError("at most 12 legs")
    for l in p.legs:
        if l.side not in (ST.BUY, ST.SELL):
            raise ValueError("each leg must be BUY or SELL")
        if l.option_type not in (ST.CE, ST.PE):
            raise ValueError("each leg must be CE or PE")
        if l.expiry not in (ST.WEEKLY, ST.MONTHLY):
            raise ValueError("each leg must sit in the weekly or the monthly expiry")
        if not (0 < l.target_premium <= 10000):
            raise ValueError("a target premium must be above zero")
    return p


class LiveReq(BaseModel):
    params: dict | None = None
    window: int = LV.DEFAULT_WINDOW


class RunReq(BaseModel):
    config: dict = {}


class DiagReq(BaseModel):
    index: str = "NIFTY"
    start: str = ""
    end: str = ""


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    p = ST.Params()
    return {
        "status": "ok",
        "connected": _broker(db, user_id) is not None,
        "indices": [{"key": k, "label": IX.INDICES[k]["label"],
                     "exchange": IX.INDICES[k]["exch"], "step": IX.INDICES[k]["step"],
                     "lot_size": BT.LOT_SIZE.get(k)}
                    for k in SUPPORTED if k in IX.INDICES],
        "defaults": {"params": p.as_dict(), "window": LV.DEFAULT_WINDOW,
                     "slippage_pts": 0.5},
        "exit_modes": [
            {"key": "weekly_expiry", "label": "Hold to the weekly expiry"},
            {"key": "intraday", "label": "Square off the same day"},
            {"key": "fixed_days", "label": "Hold a fixed number of days"},
        ],
        "rules": ST.describe(p),
        "store": {k: BT._coverage(k) for k in ("NIFTY", "SENSEX")},
        "note": ("Live legs come from the broker and need Zerodha connected. The backtest "
                 "comes from the Market Store, which must hold BOTH a weekly and a monthly "
                 "expiry on the same day for this structure to be priceable at all."),
    }


@router.post("/live")
@safe("live")
def live(req: LiveReq, user_id: int = Depends(login_required),
         db: Session = Depends(get_db)):
    window = max(5, min(int(req.window or LV.DEFAULT_WINDOW), 80))
    return {"status": "ok",
            "live": LV.desk(_broker(db, user_id), _params(req.params), window)}


@router.post("/diagnose")
@safe("diagnose")
def diagnose(req: DiagReq, user_id: int = Depends(login_required)):
    idx = str(req.index or "NIFTY").upper()
    if idx not in IX.INDICES:
        raise ValueError(f"index must be one of {', '.join(SUPPORTED)}")
    start, end = str(req.start or ""), str(req.end or "")
    if start and end:
        date.fromisoformat(start), date.fromisoformat(end)
    return {"status": "ok", "diagnosis": BT.diagnose(idx, start, end)}


@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    c = dict(req.config or {})
    start, end = str(c.get("start", "")), str(c.get("end", ""))
    date.fromisoformat(start), date.fromisoformat(end)
    cfg = BT.Config(
        start=start, end=end,
        params=_params(c.get("params")),
        lot_size=max(0, min(int(c.get("lot_size", 0) or 0), 10000)),
        slippage_pts=max(0.0, min(float(c.get("slippage_pts", 0.5)), 20.0)),
        lots_multiplier=max(1, min(int(c.get("lots_multiplier", 1)), 50)),
    )
    return {"status": "ok", "result": BT.run(cfg)}
