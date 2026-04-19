"""
API routes for Strategy 2 — Gann + Cumulative Volume Option Selling.
Endpoints: start, stop, check (trigger), status, config, backtest, history.
"""
import json
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from strategies.strategy2_option_sell import Strategy2OptionSell

router = APIRouter()
logger = get_logger("api.strategy2")

# Per-user strategy instances
_user_strategies: dict[int, Strategy2OptionSell] = {}
_last_backtest: Optional[dict] = None


class Strategy2Config(BaseModel):
    sl_points: float = 45
    target_points: float = 55
    lot_size: int = 65
    cv_threshold: int = 150_000
    strike_interval: int = 50
    sl_proximity: float = 5
    target_proximity: float = 5
    gann_target: bool = False


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy2OptionSell:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker._kite is not None:
            strat.broker = broker
        return strat

    config = _load_config()
    if broker is None:
        broker = Broker()

    strat = Strategy2OptionSell(broker, config)
    if strat.restore_state():
        logger.info(f"Strategy 2 state restored for user {user_id}: {strat.state.value}")

    _user_strategies[user_id] = strat
    return strat


def _load_config() -> dict:
    config_file = settings.DATA_DIR / "strategy_configs" / "strategy2_option_sell.json"
    if config_file.exists():
        try:
            return json.loads(config_file.read_text()).get("params", {})
        except json.JSONDecodeError:
            pass
    return {}


def _save_config(params: dict):
    config_file = settings.DATA_DIR / "strategy_configs" / "strategy2_option_sell.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = {"params": params}
    config_file.write_text(json.dumps(data, indent=2))


def _is_broker_authenticated_for_user(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_cv_data(broker: Broker, authenticated: bool) -> dict:
    """Get cumulative volume data from the CV strategy."""
    from app.routes.cumulative_volume_routes import (
        _get_strategy as get_cv_strategy,
    )
    cv_strategy = get_cv_strategy(authenticated, broker)
    return cv_strategy.compute(broker_authenticated=authenticated)


# ── Endpoints ──────────────────────────────────────


@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Current strategy state (no recompute)."""
    broker = get_user_broker(db, user_id)
    return _get_strategy(broker, user_id).get_status()


@router.post("/start")
async def start_strategy(config: Strategy2Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Start the strategy with given config."""
    params = config.model_dump()
    _save_config(params)

    broker = get_user_broker(db, user_id)
    strat = Strategy2OptionSell(broker, params)
    strat.start(params)
    _user_strategies[user_id] = strat
    return strat.get_status()


@router.post("/stop")
async def stop_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Stop the strategy."""
    broker = get_user_broker(db, user_id)
    _get_strategy(broker, user_id).stop()
    return _get_strategy(broker, user_id).get_status()


@router.post("/check")
async def check_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """
    Trigger a strategy check cycle.
    Fetches fresh CV data, runs entry/fill/exit logic.
    """
    broker = get_user_broker(db, user_id)
    authenticated = _is_broker_authenticated_for_user(db, user_id)
    strategy = _get_strategy(broker, user_id)
    if not strategy.is_active:
        return strategy.get_status()

    try:
        cv_data = _get_cv_data(broker, authenticated)
        spot_price = cv_data.get("spot_price", 0)
        result = strategy.check(cv_data, spot_price)
        result["cv_value"] = cv_data.get("last_cumulative_volume", 0)
        result["spot_price"] = spot_price
        return result
    except Exception as e:
        logger.error(f"Strategy 2 check failed: {e}")
        status = strategy.get_status()
        status["error"] = str(e)
        return status


@router.put("/config")
async def update_config(config: Strategy2Config, user_id: int = Depends(login_required)):
    """Update saved config (takes effect on next start)."""
    params = config.model_dump()
    _save_config(params)
    _user_strategies.pop(user_id, None)  # force reload
    return {"status": "updated", "config": params}


TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy2_trades.json"
ORDER_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "order_history.json"


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Return historical orders grouped by date."""
    history = _load_order_history()

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_already = any(d.get("date") == today_str for d in history)

    if not today_already:
        try:
            broker = get_user_broker(db, user_id)
            raw_orders = broker.get_orders()
            if raw_orders:
                today_orders = []
                for o in raw_orders:
                    today_orders.append({
                        "time": o.get("order_timestamp", o.get("exchange_timestamp", "")),
                        "tradingsymbol": o.get("tradingsymbol", ""),
                        "transaction_type": o.get("transaction_type", ""),
                        "quantity": o.get("quantity", 0),
                        "average_price": o.get("average_price", 0),
                        "price": o.get("price", 0),
                        "status": o.get("status", ""),
                        "order_id": str(o.get("order_id", "")),
                        "tag": o.get("tag", ""),
                    })
                if today_orders:
                    history.append({"date": today_str, "orders": today_orders})
        except Exception:
            pass

    history.sort(key=lambda d: d.get("date", ""), reverse=True)
    return history


@router.post("/history/snapshot")
async def save_order_snapshot(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Save today's orders from Zerodha to history."""
    try:
        broker = get_user_broker(db, user_id)
        raw_orders = broker.get_orders()
    except Exception as e:
        return {"status": "error", "message": str(e)}

    if not raw_orders:
        return {"status": "no_orders", "message": "No orders to save"}

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_orders = []
    for o in raw_orders:
        today_orders.append({
            "time": str(o.get("order_timestamp", o.get("exchange_timestamp", ""))),
            "tradingsymbol": o.get("tradingsymbol", ""),
            "transaction_type": o.get("transaction_type", ""),
            "quantity": o.get("quantity", 0),
            "average_price": o.get("average_price", 0),
            "price": o.get("price", 0),
            "status": o.get("status", ""),
            "order_id": str(o.get("order_id", "")),
            "tag": o.get("tag", ""),
        })

    _save_order_snapshot_to_file(today_str, today_orders)
    return {"status": "saved", "date": today_str, "order_count": len(today_orders)}


def _load_order_history() -> list:
    if ORDER_HISTORY_FILE.exists():
        try:
            return json.loads(ORDER_HISTORY_FILE.read_text())
        except (json.JSONDecodeError, Exception):
            pass
    return []


def _save_order_snapshot_to_file(date_str: str, orders: list):
    history = _load_order_history()
    history = [d for d in history if d.get("date") != date_str]
    history.append({"date": date_str, "orders": orders})
    history.sort(key=lambda d: d.get("date", ""), reverse=True)
    ORDER_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        ORDER_HISTORY_FILE.write_text(json.dumps(history, indent=2, default=str))
    except Exception as e:
        logger.error(f"Failed to save order history: {e}")
