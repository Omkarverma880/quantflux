"""
API routes for Strategy 12 — 200 EMA Pull-Back (intraday NIFTY options).

Same per-user pattern as the other strategy routes: one instance per user,
state restored from disk on first access, driven by the background loop.
Reuses broker integration, hidden SL/Target engine, RiskController,
order history and logging — no existing strategy is touched.
"""
import json
from datetime import date as _date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from strategies.strategy12_ema_pullback import Strategy12EmaPullback

router = APIRouter()
logger = get_logger("api.strategy12")

_user_strategies: dict[int, Strategy12EmaPullback] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy12_ema_pullback.json"


class Strategy12Config(BaseModel):
    fast_ema: int = 20
    slow_ema: int = 200
    timeframe: str = "1minute"
    option_selection: str = "ITM_100"
    lot_size: int = 65
    lots: int = 1
    atr_period: int = 14
    sl_mult: float = 3
    target_mode: str = "atr"          # atr | points | none
    tgt_mult: float = 9
    target_points: float = 30
    atr_update_minutes: int = 1
    exit_proximity: float = 5
    touch_buffer: float = 2
    enable_reentry: bool = False
    strike_interval: int = 50
    index_name: str = "NIFTY"
    start_time: str = "09:20"
    end_time: str = "15:10"
    max_trades_per_day: int = 5
    max_daily_loss: float = 0


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text()).get("params", {})
        except json.JSONDecodeError:
            pass
    return {}


def _save_config(params: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"params": params}, indent=2))


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy12EmaPullback:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker.is_kite_connected:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy12EmaPullback(broker, _load_config())
    if strat.restore_state():
        logger.info("S12 state restored for user %s: %s", user_id, strat.state.value)
    _user_strategies[user_id] = strat
    return strat


# ── Endpoints ─────────────────────────────────────────

@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    # Always-live index feed — refresh even before Start.
    try:
        if _is_authed(db, user_id):
            strat.refresh_spot()
            strat._refresh_emas()
    except Exception:
        pass
    return strat.get_status()


@router.get("/index-series")
async def index_series(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Today's index candles with 20/200 EMA overlays for the chart."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "series": [], "markers": []}
    strat = _get_strategy(broker, user_id)
    try:
        return {"status": "ok", "series": strat.index_series(), "markers": strat.markers[-40:]}
    except Exception as exc:
        logger.error("S12 index-series failed: %s", exc)
        return {"status": "error", "series": [], "markers": [], "message": str(exc)}


@router.post("/start")
async def start_strategy(config: Strategy12Config, user_id: int = Depends(login_required),
                         db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    strat.start(params)
    return strat.get_status()


@router.post("/stop")
async def stop_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    _get_strategy(broker, user_id).stop()
    return _get_strategy(broker, user_id).get_status()


@router.put("/config")
async def update_config(config: Strategy12Config, user_id: int = Depends(login_required),
                        db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    strat = _user_strategies.get(user_id)
    if strat is not None:
        strat.apply_config(params)
    return {"status": "updated", "config": params}


@router.post("/check")
async def check_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    if not strat.is_active and getattr(strat.state, "value", str(strat.state)) != "POSITION_OPEN":
        return strat.get_status()
    try:
        return strat.check()
    except Exception as exc:
        logger.error("S12 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required)):
    file = settings.DATA_DIR / "trade_history" / "strategy12_trades.json"
    if not file.exists():
        return {"trades": []}
    try:
        return {"trades": list(reversed(json.loads(file.read_text())))}
    except Exception:
        return {"trades": []}


# ── Backtest ──────────────────────────────────────────

class BacktestRequest(BaseModel):
    mode: str = "single"              # single | multi
    trade_date: str | None = None     # for single
    days: int = 30                    # for multi
    # optional config overrides (else the saved config is used)
    config: Strategy12Config | None = None


@router.post("/backtest")
async def run_backtest(payload: BacktestRequest, user_id: int = Depends(login_required),
                       db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error",
                "message": "Zerodha not authenticated — backtest needs historical data access"}
    # Build a throwaway strategy instance seeded with the requested config so a
    # backtest never disturbs the live per-user instance / state.
    cfg = payload.config.model_dump() if payload.config else _load_config()
    strat = Strategy12EmaPullback(broker, cfg)
    try:
        if payload.mode == "multi":
            return strat.backtest_multi(payload.days)
        try:
            td = _date.fromisoformat(payload.trade_date) if payload.trade_date else _date.today()
        except Exception:
            return {"status": "error", "message": "invalid trade_date (YYYY-MM-DD)"}
        return strat.backtest(td)
    except Exception as exc:
        logger.error("S12 backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ── Risk / re-entry control (reused RiskController) ────

class RiskConfigPayload(BaseModel):
    allow_reentry_after_target: bool | None = None
    allow_reentry_after_sl: bool | None = None
    max_reentries_per_day: int | None = None
    max_consecutive_losses: int | None = None
    entry_cooldown_seconds: int | None = None


@router.get("/risk")
async def get_risk_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    return {"status": "ok", "risk": _get_strategy(broker, user_id).risk.status_payload()}


@router.post("/risk/reset")
async def reset_risk_counters(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    strat.risk.reset_counters()
    try:
        strat._save_state()
    except Exception:
        pass
    return {"status": "ok", "risk": strat.risk.status_payload()}
