"""
API routes for the VWAP Options Engine (NIFTY index options).

Chart / ladder / backtest are read-only research paths. Status / start / stop /
positions drive the paper (default) or live strategy. Real orders require
paper_trade off AND the global trading gate on.
"""
from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.broker import Broker, get_user_broker
from core.database import get_db
from core.logger import get_logger
from research.vwap_options import VwapOptionsService
from research.vwap_options import config as vo_config
from strategies.vwap_options_strategy import VwapOptionsStrategy

router = APIRouter()
logger = get_logger("api.vwap_options")

_services: dict[int, VwapOptionsService] = {}
_strategies: dict[int, VwapOptionsStrategy] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_service(broker: Broker, user_id: int) -> VwapOptionsService:
    eng = _services.get(user_id)
    if eng is None:
        eng = VwapOptionsService(broker, user_id=user_id)
        _services[user_id] = eng
    else:
        eng.broker = broker
        eng.chain.broker = broker
        eng.chain.chain.broker = broker
        eng.pricing.broker = broker
    eng.user_id = user_id
    return eng


def _get_strategy(broker: Broker, user_id: int, db=None) -> VwapOptionsStrategy:
    strat = _strategies.get(user_id)
    if strat is None:
        cfg = vo_config.load_config(db) if db is not None else {}
        strat = VwapOptionsStrategy(broker, cfg, user_id=user_id)
        _strategies[user_id] = strat
    else:
        strat.broker = broker
        strat.chain.broker = broker
        strat.chain.chain.broker = broker
    strat.user_id = user_id
    return strat


def _cfg(db, overrides):
    return vo_config.sanitize({**vo_config.load_config(db), **(overrides or {})})


class ChartReq(BaseModel):
    overrides: dict | None = None
    date: str | None = None


class BacktestReq(BaseModel):
    overrides: dict | None = None
    start: str | None = None
    end: str | None = None
    label: str | None = None
    save: bool = True


class StrategyCfg(BaseModel):
    config: dict | None = None


# ── research: chart / ladder / backtest ──
@router.post("/chart")
def chart(payload: ChartReq | None = None, user_id: int = Depends(login_required),
          db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or ChartReq()
    try:
        return _get_service(broker, user_id).chart(_cfg(db, p.overrides), day=p.date)
    except Exception as exc:
        logger.error("vwap-options chart failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/ladder")
def ladder(payload: ChartReq | None = None, user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or ChartReq()
    try:
        return _get_service(broker, user_id).ladder(_cfg(db, p.overrides))
    except Exception as exc:
        logger.error("vwap-options ladder failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/backtest")
def backtest(payload: BacktestReq | None = None, user_id: int = Depends(login_required),
             db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    p = payload or BacktestReq()
    try:
        cfg = _cfg(db, p.overrides)
        res = _get_service(broker, user_id).backtest(cfg, start=p.start, end=p.end)
        if res.get("status") == "ok" and p.save:
            try:
                from core.models import VWAPOptionsBacktestRun
                run = VWAPOptionsBacktestRun(
                    user_id=user_id, label=p.label or f"{res['start']}→{res['end']}",
                    start_date=_date.fromisoformat(res["start"]),
                    end_date=_date.fromisoformat(res["end"]),
                    engine_version=cfg.get("engine_version"), config=cfg,
                    stats=res["stats"], by_source=res["by_source"],
                    trades=res["trades"], skipped=res["skipped"])
                db.add(run)
                db.commit()
                db.refresh(run)
                res["run_id"] = run.id
            except Exception as exc:
                logger.debug("vwap-options backtest save failed: %s", exc)
        return res
    except Exception as exc:
        logger.error("vwap-options backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/backtests")
def backtests(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        from core.models import VWAPOptionsBacktestRun
        rows = (db.query(VWAPOptionsBacktestRun)
                .filter(VWAPOptionsBacktestRun.user_id == user_id)
                .order_by(VWAPOptionsBacktestRun.created_at.desc()).limit(50).all())
        return {"status": "ok", "runs": [
            {"id": r.id, "label": r.label, "start": r.start_date.isoformat(),
             "end": r.end_date.isoformat(), "version": r.engine_version,
             "stats": r.stats, "by_source": r.by_source,
             "created_at": r.created_at.isoformat()} for r in rows]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/backtest/{run_id}")
def backtest_get(run_id: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from core.models import VWAPOptionsBacktestRun
    r = (db.query(VWAPOptionsBacktestRun)
         .filter(VWAPOptionsBacktestRun.id == run_id,
                 VWAPOptionsBacktestRun.user_id == user_id).first())
    if not r:
        return {"status": "error", "message": "Run not found"}
    return {"status": "ok", "id": r.id, "label": r.label, "config": r.config,
            "stats": r.stats, "by_source": r.by_source, "trades": r.trades, "skipped": r.skipped}


# ── config / meta ──
@router.get("/config")
def get_config(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    _cfg = vo_config.load_config(db)
    return {"status": "ok", "config": _cfg,
            # band labels follow the saved percentages
            "lines": vo_config.lines_for(_cfg), "events": vo_config.EVENTS,
            "actions": vo_config.ACTIONS, "timeframes": vo_config.TIMEFRAMES}


@router.post("/config")
def save_config(payload: dict | None = None, user_id: int = Depends(login_required),
                db: Session = Depends(get_db)):
    try:
        cfg = vo_config.save_config(db, payload or {})
        strat = _strategies.get(user_id)
        if strat is not None:
            strat.apply_config(cfg)
        return {"status": "ok", "config": cfg}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ── strategy: status / start / stop / positions ──
@router.get("/status")
def status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id, db)
    return {"status": "ok", **strat.get_status()}


@router.post("/start")
def start(payload: StrategyCfg | None = None, user_id: int = Depends(login_required),
          db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — log in first"}
    strat = _get_strategy(broker, user_id, db)
    cfg = (payload.config if payload else None) or {}
    if cfg:
        vo_config.save_config(db, cfg)
    strat.start(cfg)
    try:
        strat.check()
    except Exception as exc:
        logger.debug("vwap-options initial check failed: %s", exc)
    return {"status": "ok", **strat.get_status()}


@router.post("/stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id, db)
    strat.stop()
    return {"status": "ok", **strat.get_status()}


@router.post("/check")
def check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    return {"status": "ok", **_get_strategy(broker, user_id, db).check()}


@router.get("/positions")
def positions(date: str | None = None, user_id: int = Depends(login_required),
              db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id, db)
    return {"status": "ok", "positions": strat.positions(date)}
