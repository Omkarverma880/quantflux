"""
API routes for the Hammer-at-3/6-Month-Low Breakout Strategy (Equity Strategy #4).

Backtest / simulate / scan are read-only. Status/start/stop/positions drive the
paper/live strategy (buys the stock as CNC delivery or MIS). Real orders only
when paper_trade is off AND the global trading gate is on. Mirrors the other
equity strategy route conventions.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from research.hammer_breakout import HammerBreakoutResearch
from research.hammer_breakout.config import load_config, save_config
from research.hammer_breakout.service import format_today_message
from strategies.hammer_breakout_strategy import HammerBreakoutStrategy

router = APIRouter()
logger = get_logger("api.hammer_breakout")

_research: dict[int, HammerBreakoutResearch] = {}
_strategies: dict[int, HammerBreakoutStrategy] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_research(broker: Broker, user_id: int) -> HammerBreakoutResearch:
    eng = _research.get(user_id)
    if eng is None:
        eng = HammerBreakoutResearch(broker, user_id=user_id)
        _research[user_id] = eng
    else:
        eng.broker = broker
        eng.universe.broker = broker
    eng.user_id = user_id
    return eng


def _get_strategy(broker: Broker, user_id: int) -> HammerBreakoutStrategy:
    strat = _strategies.get(user_id)
    if strat is None:
        strat = HammerBreakoutStrategy(broker, load_config(), user_id=user_id)
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


class ScanReq(BaseModel):
    symbols: list[str] | None = None
    overrides: dict | None = None


class StrategyCfg(BaseModel):
    config: dict | None = None


# ── research: backtest / simulate / scan ──
@router.post("/backtest")
def backtest(payload: BacktestReq | None = None, user_id: int = Depends(login_required),
             db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or BacktestReq()
    try:
        return _get_research(broker, user_id).backtest(
            p.overrides, symbol=p.symbol, symbols=p.symbols, start=p.start, end=p.end,
            include_non_trades=p.include_non_trades, apply_caps=p.apply_caps)
    except Exception as exc:
        logger.error("hammer backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/simulate")
def simulate(payload: SimulateReq, user_id: int = Depends(login_required),
             db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_research(broker, user_id).simulate(payload.symbol, payload.overrides, payload.date)
    except Exception as exc:
        logger.error("hammer simulate failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/scan")
def scan(payload: ScanReq | None = None, user_id: int = Depends(login_required),
         db: Session = Depends(get_db)):
    """Hammers on the last completed daily candle — the setups armed for today."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or ScanReq()
    syms = p.symbols if p.symbols is not None else load_config().get("symbols", [])
    if not syms:
        return {"status": "ok", "setups": [], "message": "No stocks in the watchlist"}
    try:
        return {"status": "ok", **_get_research(broker, user_id).scan(syms, p.overrides)}
    except Exception as exc:
        logger.error("hammer scan failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/today/telegram")
def today_telegram(payload: ScanReq | None = None, user_id: int = Depends(login_required),
                   db: Session = Depends(get_db)):
    """Push today's qualifying stocks to Telegram on demand."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or ScanReq()
    cfg = {**load_config(), **(p.overrides or {})}
    syms = p.symbols if p.symbols is not None else cfg.get("symbols", [])
    if not syms:
        return {"status": "error", "message": "No stocks in the watchlist"}
    bot = cfg.get("telegram_bot", "a")
    try:
        from core import notify
        if not notify.enabled(bot):
            return {"status": "error",
                    "message": f"Telegram bot {bot.upper()} is not configured — set it up in Settings"}
        result = _get_research(broker, user_id).scan(syms, p.overrides)
        notify.send(format_today_message(result, result.get("config", cfg)), bot=bot)
        return {"status": "ok", "sent": len(result.get("setups") or []), **result}
    except Exception as exc:
        logger.error("hammer today telegram failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/config")
def get_config(user_id: int = Depends(login_required)):
    return {"status": "ok", "config": load_config()}


@router.post("/config")
def post_config(payload: dict | None = None, user_id: int = Depends(login_required)):
    return {"status": "ok", "config": save_config(payload or {})}


# ── strategy: status / start / stop / positions ──
@router.get("/status")
def status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", **_get_strategy(get_user_broker(db, user_id), user_id).get_status()}


@router.post("/start")
def start(payload: StrategyCfg | None = None, user_id: int = Depends(login_required),
          db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — log in first"}
    strat = _get_strategy(broker, user_id)
    cfg = (payload.config if payload else None) or {}
    if not cfg.get("symbols") and not strat.cfg.get("symbols"):
        return {"status": "error", "message": "Add at least one stock before starting"}
    strat.start(cfg)
    save_config(strat.config_dict())
    try:
        strat.check()
    except Exception as exc:
        logger.debug("hammer initial check failed: %s", exc)
    return {"status": "ok", **strat.get_status()}


@router.post("/stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.stop()
    save_config(strat.config_dict())
    return {"status": "ok", **strat.get_status()}


@router.put("/config")
def update_strategy_config(payload: StrategyCfg, user_id: int = Depends(login_required),
                           db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.apply_config(payload.config or {})
    cfg = save_config(strat.config_dict())
    return {"status": "ok", "config": cfg}


@router.post("/check")
def check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    return {"status": "ok", **_get_strategy(broker, user_id).check()}


@router.get("/positions")
def positions(date: str | None = None, user_id: int = Depends(login_required),
              db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    return {"status": "ok", "positions": strat.positions(date)}
