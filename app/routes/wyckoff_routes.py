"""
API routes for the Wyckoff Method desk (equity · NIFTY options · equity F&O).

Strictly read-only analysis. No orders, no strategy state, no interaction with
any running engine.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from research.wyckoff import WyckoffService
from research.wyckoff import core as wyckoff_core
from research.wyckoff.config import TIMEFRAMES, load_config, save_config
from research.wyckoff.service import INDEX_SPOT, format_message

router = APIRouter()
logger = get_logger("api.wyckoff")

_services: dict[int, WyckoffService] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_service(broker: Broker, user_id: int) -> WyckoffService:
    svc = _services.get(user_id)
    if svc is None:
        svc = WyckoffService(broker, user_id=user_id)
        _services[user_id] = svc
    else:
        svc.broker = broker
        svc.universe.broker = broker
    svc.user_id = user_id
    return svc


class EquityReq(BaseModel):
    symbol: str
    overrides: dict | None = None
    telegram: bool = False


class OptionsReq(BaseModel):
    index: str | None = None
    mode: str | None = None            # index | premium
    strike: float | None = None
    opt_type: str = "CE"
    expiry_type: str = "weekly"
    overrides: dict | None = None
    telegram: bool = False


class ScanReq(BaseModel):
    symbols: list[str] | None = None
    overrides: dict | None = None


def _maybe_telegram(payload: dict, cfg: dict, send: bool) -> dict:
    if not send:
        return payload
    bot = (cfg or {}).get("telegram_bot", "a")
    try:
        from core import notify
        if not notify.enabled(bot):
            payload["telegram_error"] = f"Telegram bot {bot.upper()} is not configured"
        else:
            notify.send(format_message(payload), bot=bot)
            payload["telegram_sent"] = True
    except Exception as exc:
        logger.error("wyckoff telegram failed: %s", exc)
        payload["telegram_error"] = str(exc)
    return payload


@router.get("/meta")
def meta(user_id: int = Depends(login_required)):
    """Events, phases and the nine tests — the teaching legend for the UI."""
    return {"status": "ok", "events": wyckoff_core.EVENTS, "phases": wyckoff_core.PHASES,
            "buy_tests": [{"key": k, "label": v} for k, v in wyckoff_core.BUY_TESTS],
            "sell_tests": [{"key": k, "label": v} for k, v in wyckoff_core.SELL_TESTS],
            "timeframes": TIMEFRAMES, "indices": list(INDEX_SPOT.keys()),
            "config": load_config()}


@router.post("/equity")
def equity(payload: EquityReq, user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        res = _get_service(broker, user_id).analyze_equity(payload.symbol, payload.overrides)
    except Exception as exc:
        logger.error("wyckoff equity failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    if res.get("status") == "ok":
        _maybe_telegram(res, res.get("config"), payload.telegram)
    return res


@router.post("/options")
def options(payload: OptionsReq | None = None, user_id: int = Depends(login_required),
            db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or OptionsReq()
    try:
        res = _get_service(broker, user_id).analyze_options(
            p.overrides, index=p.index, mode=p.mode, strike=p.strike,
            opt_type=p.opt_type, expiry_type=p.expiry_type)
    except Exception as exc:
        logger.error("wyckoff options failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    if res.get("status") == "ok":
        _maybe_telegram(res, res.get("config"), p.telegram)
    return res


@router.post("/fno")
def fno(payload: EquityReq, user_id: int = Depends(login_required),
        db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        res = _get_service(broker, user_id).analyze_fno(payload.symbol, payload.overrides)
    except Exception as exc:
        logger.error("wyckoff fno failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    if res.get("status") == "ok":
        _maybe_telegram(res, res.get("config"), payload.telegram)
    return res


@router.post("/scan")
def scan(payload: ScanReq | None = None, user_id: int = Depends(login_required),
         db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or ScanReq()
    syms = p.symbols or load_config().get("symbols") or []
    if not syms:
        return {"status": "error", "message": "Pick a watchlist or a stock first"}
    try:
        return _get_service(broker, user_id).scan(syms, p.overrides)
    except Exception as exc:
        logger.error("wyckoff scan failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/config")
def get_config(user_id: int = Depends(login_required)):
    return {"status": "ok", "config": load_config()}


@router.post("/config")
def post_config(payload: dict | None = None, user_id: int = Depends(login_required)):
    return {"status": "ok", "config": save_config(payload or {})}
