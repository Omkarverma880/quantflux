"""
API routes for the 20 EMA Single Candle Breakout (Index Strategies → 20 EMA Breakout).

    /meta       indices, timeframes, defaults, what the store covers
    /desk       the live/paper desk: setup state, tickets, events, the three gates
    /config     change settings, start and stop
    /backtest   the same rules over stored history, priced on real option candles

A real order needs this strategy in live mode AND the app out of paper mode AND the risk
fence down. That check lives in ``research.ema_breakout.live._order`` and nowhere else;
nothing here can bypass it.
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
from research.ema_breakout import backtest as BT
from research.ema_breakout import live as LIVE
from research.ema_breakout import strategy as ST

router = APIRouter()
logger = get_logger("api.ema_breakout")

SUPPORTED = ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "BANKEX"]


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


def _clean_params(d: dict | None) -> dict:
    """Clamp everything a user can send. The engine must never see a nonsense setting."""
    d = dict(d or {})
    out: dict = {}
    if "index" in d:
        idx = str(d["index"]).upper()
        if idx not in SUPPORTED:
            raise ValueError(f"index must be one of {', '.join(SUPPORTED)}")
        out["index"] = idx
    if "ema_length" in d:
        out["ema_length"] = max(2, min(int(d["ema_length"]), 200))
    if "timeframe" in d:
        tf = int(d["timeframe"])
        if tf not in ST.TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {ST.TIMEFRAMES}")
        out["timeframe"] = tf
    if "option_action" in d:
        act = str(d["option_action"]).upper()
        if act not in (ST.BUY, ST.SELL):
            raise ValueError("option_action must be BUY or SELL")
        out["option_action"] = act
    if "strike_offset" in d:
        out["strike_offset"] = max(-10, min(int(d["strike_offset"]), 10))
    if "lots" in d:
        out["lots"] = max(1, min(int(d["lots"]), 100))
    for k in ("allow_long", "allow_short"):
        if k in d:
            out[k] = bool(d[k])
    for k in ("entry_from", "entry_until", "squareoff"):
        if k in d:
            out[k] = max(9 * 60 + 15, min(int(d[k]), 15 * 60 + 29))
    return out


class ConfigReq(BaseModel):
    running: bool | None = None
    mode: str | None = None
    params: dict | None = None


class RunReq(BaseModel):
    config: dict = {}


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from config import settings
    cfg = LIVE.load_config(db, user_id)
    return {
        "status": "ok",
        "connected": _broker(db, user_id) is not None,
        "config": cfg,
        "defaults": ST.P.as_dict(),
        "indices": [{"key": k, "step": ST.STRIKE_STEP.get(k),
                     "exchange": "BFO" if k in ("SENSEX", "BANKEX") else "NFO",
                     "backtest_lot": BT.LOT_SIZE.get(k)} for k in SUPPORTED],
        "timeframes": list(ST.TIMEFRAMES),
        "actions": [{"key": ST.BUY, "label": "Buy options",
                     "detail": "A long signal buys a CALL, a short signal buys a PUT."},
                    {"key": ST.SELL, "label": "Sell options",
                     "detail": "A long signal SELLS a PUT, a short signal SELLS a CALL. "
                               "Premium is capped and the loss is not — size accordingly."}],
        "rules": ST.describe(ST.P),
        "store": {k: BT._coverage(k) for k in ("NIFTY", "SENSEX", "BANKNIFTY")},
        "app_paper_mode": bool(getattr(settings, "PAPER_TRADE", True)
                               or not getattr(settings, "TRADING_ENABLED", False)),
        "note": ("Live signals need Zerodha connected; lot size comes from the broker's "
                 "instrument master. The backtest reads the Market Store and does not need "
                 "a broker."),
    }


@router.get("/desk")
@safe("desk")
def desk(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return LIVE.dashboard(db, user_id, _broker(db, user_id))


@router.post("/config")
@safe("config")
def config(req: ConfigReq, user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    updates: dict = {}
    if req.running is not None:
        updates["running"] = bool(req.running)
    if req.mode is not None:
        m = str(req.mode).lower()
        if m not in ("paper", "live"):
            raise ValueError("mode must be paper or live")
        updates["mode"] = m
    if req.params is not None:
        updates["params"] = _clean_params(req.params)
    return {"status": "ok", "config": LIVE.save_config(db, user_id, updates)}


@router.post("/start")
@safe("start")
def start(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = _broker(db, user_id)
    cfg = LIVE.save_config(db, user_id, {"running": True})
    if broker is None:
        return {"status": "ok", "config": cfg,
                "result": {"state": "started, but Zerodha is not connected — nothing will be "
                                    "read until it is"}}
    return {"status": "ok", "config": cfg,
            "result": LIVE.ENGINE.check(db, user_id, broker)}


@router.post("/stop")
@safe("stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "config": LIVE.save_config(db, user_id, {"running": False}),
            "note": "Stopped. Any position already open is left as it is — closing it is a "
                    "decision, not a side effect of pressing stop."}


@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    c = dict(req.config or {})
    start_s, end_s = str(c.get("start", "")), str(c.get("end", ""))
    date.fromisoformat(start_s), date.fromisoformat(end_s)
    params = ST.Params(**{**ST.P.as_dict(), **_clean_params(c.get("params"))})
    cfg = BT.Config(
        start=start_s, end=end_s, params=params,
        lot_size=max(0, min(int(c.get("lot_size", 0) or 0), 10000)),
        slippage_pts=max(0.0, min(float(c.get("slippage_pts", 0.5)), 20.0)),
        strike_tolerance=max(0, min(int(c.get("strike_tolerance", 2)), 10)),
    )
    return {"status": "ok", "result": BT.run(cfg)}
