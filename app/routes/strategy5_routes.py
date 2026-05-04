"""
API routes for Strategy 5 — Dynamic Gann-Level Range Retest.

Identical logic to Strategy 4 except the active range is derived live
from gann_levels.csv (lower = largest level <= spot, upper = smallest
level > spot) and floats with the spot while IDLE.
"""
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from strategies.strategy5_gann_range import Strategy5GannRange

router = APIRouter()
logger = get_logger("api.strategy5")

_user_strategies: dict[int, Strategy5GannRange] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy5_gann_range.json"


class Strategy5Config(BaseModel):
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
    retest_only: bool = True
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


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy5GannRange:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker._kite is not None:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy5GannRange(broker, _load_config())
    if strat.restore_state():
        logger.info("Strategy 5 state restored for user %s: %s", user_id, strat.state.value)
    _user_strategies[user_id] = strat
    return strat


def _get_spot_price(broker: Broker, authenticated: bool) -> float:
    if not authenticated:
        return 0.0
    try:
        ltp = broker.get_ltp(["NSE:NIFTY 50"])
        return float(ltp.get("NSE:NIFTY 50", 0) or 0)
    except Exception as exc:
        logger.debug("S5 spot LTP fetch failed: %s", exc)
        return 0.0


# ── Endpoints ──────────────────────────────────────


@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    return _get_strategy(broker, user_id).get_status()


@router.get("/levels")
async def get_levels(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Force a refresh of the active Gann pair from current spot."""
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    return strat.fetch_levels(force=True)


@router.get("/intraday")
async def get_intraday(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Today's NIFTY 50 minute-candle close series (9:15 → now)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "series": []}
    strat = _get_strategy(broker, user_id)
    try:
        series = strat.get_intraday_series()
        return {"status": "ok", "series": series}
    except Exception as exc:
        logger.error("S5 intraday fetch failed: %s", exc)
        return {"status": "error", "series": [], "message": str(exc)}


@router.post("/start")
async def start_strategy(config: Strategy5Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    broker = get_user_broker(db, user_id)
    strat = Strategy5GannRange(broker, params)
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
        logger.error("S5 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.put("/config")
async def update_config(config: Strategy5Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    strat = _user_strategies.get(user_id)
    if strat is not None:
        strat.apply_config(params)
    return {"status": "updated", "config": params}


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required)):
    file = settings.DATA_DIR / "trade_history" / "strategy5_trades.json"
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
    """Single-day spot-proxy backtest with dynamic Gann range."""
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

    strat = Strategy5GannRange(broker, _load_config())
    try:
        return strat.backtest(target)
    except Exception as exc:
        logger.error("S5 backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/backtest-multi")
async def run_backtest_multi(
    payload: dict | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Aggregated multi-day backtest. Body: {"days": 30} (default 30, cap 60)."""
    days = 30
    if payload and payload.get("days"):
        try:
            days = max(1, min(int(payload["days"]), 60))
        except Exception:
            return {"status": "error", "message": "Invalid days value"}

    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — backtest needs historical data access"}

    strat = Strategy5GannRange(broker, _load_config())
    try:
        return strat.backtest_multi(days)
    except Exception as exc:
        logger.error("S5 multi backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}
