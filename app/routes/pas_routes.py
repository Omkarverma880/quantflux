"""
API routes for Price Action Signals (Index Strategies → Price Action Signals).

Three things, and nothing else:

    /live       the levels right now, which signals have fired on CLOSED candles today, and
                the option each one implies with its live premium
    /backtest   the same rules over stored history, priced on real option candles
    /meta       what the store covers, and whether the broker is connected

Nothing here places an order. The live endpoint reads candles and quotes; it has no order
path at all, which is deliberate — the signal is the product, the position is yours.
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
from research.pas import backtest as BT
from research.pas import live as LIVE
from research.pas import strategy as ST

router = APIRouter()
logger = get_logger("api.pas")


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


def _params(d: dict) -> ST.Params:
    """The indicator's own inputs, clamped to ranges where the maths still means something."""
    d = d or {}
    return ST.Params(
        ema_len=max(2, min(200, int(d.get("ema_len", 20)))),
        swing_len=max(2, min(100, int(d.get("swing_len", 10)))),
        use_vwap=bool(d.get("use_vwap", True)),
        use_retest=bool(d.get("use_retest", False)),
        session_vwap=bool(d.get("session_vwap", True)),
        allow_bull=bool(d.get("allow_bull", True)),
        allow_bear=bool(d.get("allow_bear", True)),
    )


TIMEFRAMES = (1, 3, 5, 15, 30, 60)


def _timeframe(v) -> int:
    try:
        tf = int(v)
    except Exception:
        return 5
    return tf if tf in TIMEFRAMES else 5


class LiveReq(BaseModel):
    params: dict | None = None
    timeframe: int = 5
    strike_offset: int = 0


class RunReq(BaseModel):
    config: dict = {}


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {
        "status": "ok",
        "connected": _broker(db, user_id) is not None,
        "defaults": {"params": ST.Params().as_dict(), "timeframe": 5,
                     "strike_offset": 0,
                     "backtest": {k: v for k, v in BT.Config().as_dict().items()
                                  if k not in ("start", "end", "params")}},
        "timeframes": list(TIMEFRAMES),
        "coverage": BT._coverage(),
        "rules": ST.describe(ST.Params()),
        "source": "Pine v6 'Quantflux Price Action Signals', translated line for line.",
        "note": ("Live signals come from Zerodha candles and need the broker connected. The "
                 "backtest comes from the Market Store and does not."),
    }


@router.post("/live")
@safe("live")
def live(req: LiveReq, user_id: int = Depends(login_required),
         db: Session = Depends(get_db)):
    out = LIVE.signals_now(_broker(db, user_id), _params(req.params),
                           _timeframe(req.timeframe),
                           strike_offset=max(-5, min(5, int(req.strike_offset or 0))))
    return {"status": "ok", "live": out}


@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    c = req.config or {}
    start, end = str(c.get("start", "")), str(c.get("end", ""))
    # fail on a malformed date here rather than deep in a store read
    date.fromisoformat(start), date.fromisoformat(end)
    cfg = BT.Config(
        start=start, end=end,
        timeframe=_timeframe(c.get("timeframe", 5)),
        params=_params(c.get("params")),
        moneyness=max(-3, min(3, int(c.get("moneyness", 0)))),
        lots=max(1, min(20, int(c.get("lots", 1)))),
        target_pct=max(0.0, min(500.0, float(c.get("target_pct", 60.0)))),
        stop_pct=max(0.0, min(100.0, float(c.get("stop_pct", 30.0)))),
        max_hold_min=max(0, min(375, int(c.get("max_hold_min", 60)))),
        slippage_pts=max(0.0, min(10.0, float(c.get("slippage_pts", 0.5)))),
        one_at_a_time=bool(c.get("one_at_a_time", True)),
        day_reset=bool(c.get("day_reset", False)),
    )
    r = BT.run(cfg)
    # the trade list is the biggest part of the payload and the table only shows a page of it
    rows = r.get("trades") or []
    if len(rows) > 500:
        r["trades"] = rows[-500:]
        r["trades_truncated"] = {"shown": 500, "of": len(rows),
                                 "note": "the most recent 500 trades are listed; every "
                                         "number above is computed on all of them"}
    return {"status": "ok", "result": r}
