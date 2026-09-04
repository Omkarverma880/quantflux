"""
API routes for the Equity Strategy Workspace (Equity → Workspace).

Strictly read-only: scan, config and an optional Telegram digest. No orders, no
strategy state, no interaction with any running engine.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from research.equity_workspace import EquityWorkspaceService
from research.equity_workspace import signals as sig
from research.equity_workspace.config import TIMEFRAMES, load_config, save_config
from research.equity_workspace.service import format_scan_message

router = APIRouter()
logger = get_logger("api.equity_workspace")

_services: dict[int, EquityWorkspaceService] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_service(broker: Broker, user_id: int) -> EquityWorkspaceService:
    svc = _services.get(user_id)
    if svc is None:
        svc = EquityWorkspaceService(broker, user_id=user_id)
        _services[user_id] = svc
    else:
        svc.broker = broker
        svc.universe.broker = broker
    svc.user_id = user_id
    return svc


class ScanReq(BaseModel):
    symbols: list[str] | None = None
    symbol: str | None = None
    overrides: dict | None = None
    telegram: bool = False


@router.get("/meta")
def meta(user_id: int = Depends(login_required)):
    """The strategy catalogue + timeframes — powers the legend and the pickers."""
    return {"status": "ok", "strategies": sig.STRATEGIES, "timeframes": TIMEFRAMES,
            "config": load_config()}


@router.post("/scan")
def scan(payload: ScanReq | None = None, user_id: int = Depends(login_required),
         db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or ScanReq()
    cfg = {**load_config(), **(p.overrides or {})}
    syms = ([p.symbol] if p.symbol else None) or p.symbols or cfg.get("symbols") or []
    if not syms:
        return {"status": "error", "message": "Pick a watchlist or enter a stock first"}
    try:
        result = _get_service(broker, user_id).scan(syms, p.overrides)
    except Exception as exc:
        logger.error("workspace scan failed: %s", exc)
        return {"status": "error", "message": str(exc)}

    if p.telegram or cfg.get("telegram_alerts"):
        bot = cfg.get("telegram_bot", "a")
        try:
            from core import notify
            if notify.enabled(bot):
                notify.send(format_scan_message(result, result.get("config", cfg)), bot=bot)
                result["telegram_sent"] = True
            else:
                result["telegram_error"] = f"Telegram bot {bot.upper()} is not configured"
        except Exception as exc:
            logger.error("workspace telegram failed: %s", exc)
            result["telegram_error"] = str(exc)
    return result


@router.get("/config")
def get_config(user_id: int = Depends(login_required)):
    return {"status": "ok", "config": load_config()}


@router.post("/config")
def post_config(payload: dict | None = None, user_id: int = Depends(login_required)):
    return {"status": "ok", "config": save_config(payload or {})}
