"""
API routes for Strategy 4 — Previous-Day First-Hour High/Low Retest.

Endpoints follow the same pattern as Strategy 1 / 3 — each user has their
own strategy instance keyed by user_id; state is restored from disk on
first access.
"""
import json
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from strategies.strategy4_high_low_retest import Strategy4HighLowRetest

router = APIRouter()
logger = get_logger("api.strategy4")

_user_strategies: dict[int, Strategy4HighLowRetest] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy4_high_low_retest.json"


class Strategy4Config(BaseModel):
    sl_points: float = 30
    target_points: float = 60
    lot_size: int = 65
    lots: int = 1
    strike_interval: int = 50
    sl_proximity: float = 5
    target_proximity: float = 5
    retest_buffer: float = 8
    max_breakout_extension: float = 60
    max_trades_per_day: int = 1
    allow_reentry: bool = False
    itm_offset: int = 100
    gann_target: bool = False
    gann_count: int = 1
    max_entry_slippage: float = 8
    index_name: str = "NIFTY"


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


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy4HighLowRetest:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker._kite is not None:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy4HighLowRetest(broker, _load_config())
    if strat.restore_state():
        logger.info("Strategy 4 state restored for user %s: %s", user_id, strat.state.value)
    _user_strategies[user_id] = strat
    return strat


def _get_spot_price(broker: Broker, authenticated: bool) -> float:
    """Return live NIFTY 50 spot LTP, or 0.0 if not available."""
    if not authenticated:
        return 0.0
    try:
        ltp = broker.get_ltp(["NSE:NIFTY 50"])
        return float(ltp.get("NSE:NIFTY 50", 0) or 0)
    except Exception as exc:
        logger.debug("S4 spot LTP fetch failed: %s", exc)
        return 0.0


# ── Endpoints ──────────────────────────────────────


@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    return _get_strategy(broker, user_id).get_status()


@router.get("/levels")
async def get_levels(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Force-fetch (or return cached) previous-day 9:15-10:15 levels."""
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    return strat.fetch_levels()


@router.post("/start")
async def start_strategy(config: Strategy4Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    broker = get_user_broker(db, user_id)
    strat = Strategy4HighLowRetest(broker, params)
    strat.start(params)
    _user_strategies[user_id] = strat
    return strat.get_status()


@router.post("/stop")
async def stop_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    _get_strategy(broker, user_id).stop()
    return _get_strategy(broker, user_id).get_status()


@router.post("/check")
async def check_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    authed = _is_authed(db, user_id)
    strat = _get_strategy(broker, user_id)
    if not strat.is_active:
        return strat.get_status()
    try:
        spot = _get_spot_price(broker, authed)
        result = strat.check(spot)
        result["spot_price"] = spot
        return result
    except Exception as exc:
        logger.error("S4 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.put("/config")
async def update_config(config: Strategy4Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    strat = _user_strategies.get(user_id)
    if strat is not None:
        strat.apply_config(params)
    return {"status": "updated", "config": params}


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required)):
    file = settings.DATA_DIR / "trade_history" / "strategy4_trades.json"
    if not file.exists():
        return {"trades": []}
    try:
        return {"trades": json.loads(file.read_text())}
    except Exception:
        return {"trades": []}


@router.post("/backtest")
async def run_backtest(
    payload: dict | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Backtest on the latest available trading day (or a given date).

    Body (optional): {"date": "YYYY-MM-DD"}
    """
    from datetime import date as _date
    target = None
    if payload and payload.get("date"):
        try:
            target = _date.fromisoformat(payload["date"])
        except Exception:
            return {"status": "error", "message": "Invalid date format (use YYYY-MM-DD)"}

    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — backtest needs historical data access"}

    # Use a fresh in-memory instance so it doesn't disturb the live one
    strat = Strategy4HighLowRetest(broker, _load_config())
    try:
        return strat.backtest(target)
    except Exception as exc:
        logger.error("S4 backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/backtest-multi")
async def run_backtest_multi(
    payload: dict | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Aggregated multi-day backtest.

    Body: {"days": 30}  (default 30, hard cap 60)
    """
    days = 30
    if payload and payload.get("days"):
        try:
            days = max(1, min(int(payload["days"]), 60))
        except Exception:
            return {"status": "error", "message": "Invalid days value"}

    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — backtest needs historical data access"}

    strat = Strategy4HighLowRetest(broker, _load_config())
    try:
        return strat.backtest_multi(days)
    except Exception as exc:
        logger.error("S4 multi backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}
