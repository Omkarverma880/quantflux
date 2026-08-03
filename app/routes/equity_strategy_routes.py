"""
API routes for the Equity Strategies section.

Currently hosts the Equity Previous-Month-VWAP Holding strategy (live port of
Research #8). Follows the same auth + per-user + durable-config pattern as the
other strategy routes. Real orders are only placed when ``paper_trade`` is off.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from strategies.equity_pmvwap_holding import EquityPMVwapHolding, DEFAULT_CONFIG

router = APIRouter()
logger = get_logger("api.equity_strategy")

_CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "equity_pmvwap_holding.json"
_user_strategies: dict[int, EquityPMVwapHolding] = {}


def _load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if _CONFIG_FILE.exists():
            cfg.update(json.loads(_CONFIG_FILE.read_text()) or {})
    except Exception as exc:
        logger.debug("equity strategy config read failed: %s", exc)
    return cfg


def _save_config(params: dict):
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(params, indent=2, default=str))
    except Exception as exc:
        logger.error("equity strategy config save failed: %s", exc)


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_strategy(broker: Broker = None, user_id: int = 0) -> EquityPMVwapHolding:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker.is_kite_connected:
            strat.broker = broker
            strat.universe.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = EquityPMVwapHolding(broker, _load_config(), user_id=user_id)
    try:
        if strat.restore_state():
            logger.info("Equity holding state restored for user %s", user_id)
    except Exception as exc:
        logger.error("restore failed: %s", exc)
    _user_strategies[user_id] = strat
    return strat


class StrategyConfig(BaseModel):
    config: dict | None = None


@router.get("/status")
def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    return strat.get_status()


@router.post("/start")
def start(payload: StrategyConfig | None = None, user_id: int = Depends(login_required),
                db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — log in first"}
    strat = _get_strategy(broker, user_id)
    cfg = (payload.config if payload else None) or {}
    if not cfg.get("symbols") and not strat.cfg.get("symbols"):
        return {"status": "error", "message": "Add at least one stock (pick a watchlist) before starting"}
    strat.start(cfg)
    _save_config(strat.config_dict())
    try:
        strat.check()          # immediate first evaluation
    except Exception as exc:
        logger.debug("initial check failed: %s", exc)
    return {"status": "ok", **strat.get_status()}


@router.post("/stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.stop()
    _save_config(strat.config_dict())
    return {"status": "ok", **strat.get_status()}


@router.put("/config")
def update_config(payload: StrategyConfig, user_id: int = Depends(login_required),
                        db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.apply_config(payload.config or {})
    _save_config(strat.config_dict())
    return {"status": "ok", "config": strat.config_dict()}


@router.post("/check")
def check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    strat = _get_strategy(broker, user_id)
    return {"status": "ok", **strat.check()}


@router.post("/reset")
def reset(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.reset()
    return {"status": "ok", **strat.get_status()}
