"""
API routes for the 4th-Candle Strategy (Equity Strategy #2).

Backtest + simulate are read-only research paths. Status/start/stop/positions
drive the paper/live strategy. Real orders only when paper_trade is off AND the
global trading gate is on. Mirrors the other equity-strategy route conventions.
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
from research.fourth_candle import FourthCandleResearch
from research.fourth_candle.config import load_config, save_config
from strategies.fourth_candle_strategy import FourthCandleStrategy

router = APIRouter()
logger = get_logger("api.fourth_candle")

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "fourth_candle_config.json"
_research: dict[int, FourthCandleResearch] = {}
_strategies: dict[int, FourthCandleStrategy] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_research(broker: Broker, user_id: int) -> FourthCandleResearch:
    eng = _research.get(user_id)
    if eng is None:
        eng = FourthCandleResearch(broker, user_id=user_id)
        _research[user_id] = eng
    else:
        eng.broker = broker
        eng.universe.broker = broker
    eng.user_id = user_id
    return eng


def _load_strategy_cfg() -> dict:
    cfg = dict(load_config())
    cfg["symbols"] = []
    try:
        if _STATE_FILE.exists():
            cfg.update(json.loads(_STATE_FILE.read_text()) or {})
    except Exception:
        pass
    return cfg


def _save_strategy_cfg(cfg: dict):
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(cfg, indent=2, default=str))
    except Exception as exc:
        logger.error("4th-candle strategy cfg save failed: %s", exc)


def _get_strategy(broker: Broker, user_id: int) -> FourthCandleStrategy:
    strat = _strategies.get(user_id)
    if strat is None:
        strat = FourthCandleStrategy(broker, _load_strategy_cfg(), user_id=user_id)
        _strategies[user_id] = strat
    else:
        strat.broker = broker
        strat.universe.broker = broker
    strat.user_id = user_id
    return strat


class BacktestReq(BaseModel):
    overrides: dict | None = None
    symbol: str | None = None
    symbols: list[str] | None = None
    start: str | None = None
    end: str | None = None
    include_non_trades: bool = False
    apply_caps: bool = True


class SimulateReq(BaseModel):
    symbol: str
    overrides: dict | None = None
    date: str | None = None


class StrategyCfg(BaseModel):
    config: dict | None = None


# ── research: backtest + simulate ──
@router.post("/backtest")
def backtest(payload: BacktestReq | None = None, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or BacktestReq()
    try:
        return _get_research(broker, user_id).backtest(p.overrides, symbol=p.symbol,
                                                       symbols=p.symbols, start=p.start, end=p.end,
                                                       include_non_trades=p.include_non_trades,
                                                       apply_caps=p.apply_caps)
    except Exception as exc:
        logger.error("4th-candle backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/simulate")
def simulate(payload: SimulateReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_research(broker, user_id).simulate(payload.symbol, payload.overrides, payload.date)
    except Exception as exc:
        logger.error("4th-candle simulate failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/config")
def get_config(user_id: int = Depends(login_required)):
    return {"status": "ok", "config": _load_strategy_cfg()}


@router.post("/config")
def post_config(payload: dict | None = None, user_id: int = Depends(login_required)):
    cfg = save_config(payload or {})
    merged = {**cfg, "symbols": (payload or {}).get("symbols", _load_strategy_cfg().get("symbols", []))}
    _save_strategy_cfg(merged)
    return {"status": "ok", "config": merged}


# ── strategy: status / start / stop / positions ──
@router.get("/status")
def status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", **_get_strategy(get_user_broker(db, user_id), user_id).get_status()}


@router.post("/start")
def start(payload: StrategyCfg | None = None, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — log in first"}
    strat = _get_strategy(broker, user_id)
    cfg = (payload.config if payload else None) or {}
    if not cfg.get("symbols") and not strat.cfg.get("symbols"):
        return {"status": "error", "message": "Add at least one stock before starting"}
    strat.start(cfg)
    _save_strategy_cfg(strat.config_dict())
    try:
        strat.check()
    except Exception as exc:
        logger.debug("initial check failed: %s", exc)
    return {"status": "ok", **strat.get_status()}


@router.post("/stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.stop()
    _save_strategy_cfg(strat.config_dict())
    return {"status": "ok", **strat.get_status()}


@router.put("/config")
def update_strategy_config(payload: StrategyCfg, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.apply_config(payload.config or {})
    cfg = strat.config_dict()
    cfg["symbols"] = (payload.config or {}).get("symbols", strat.cfg.get("symbols", []))
    strat.cfg["symbols"] = cfg["symbols"]
    _save_strategy_cfg(cfg)
    return {"status": "ok", "config": cfg}


@router.post("/check")
def check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    return {"status": "ok", **_get_strategy(broker, user_id).check()}


@router.get("/positions")
def positions(date: str | None = None, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    return {"status": "ok", "positions": strat.positions(date)}
