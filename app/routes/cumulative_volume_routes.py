"""
API route for Strategy 1 — Cumulative Volume Analysis.
Exposes endpoints that return computed table data.
Works even without broker authentication (falls back to demo data).
When broker IS authenticated, always shows real market data.
"""
import json
from datetime import datetime
from fastapi import APIRouter, Depends
from typing import Optional
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from strategies.base_strategy import StrategyConfig
from strategies.cumulative_volume import CumulativeVolumeStrategy

router = APIRouter()
logger = get_logger("api.cumulative_volume")

# Cached strategy instance
_strategy_instance: Optional[CumulativeVolumeStrategy] = None


def _is_broker_authenticated_for_user(db, user_id: int) -> bool:
    """Check if this user has a valid Zerodha session today."""
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_strategy(authenticated: bool, broker: Broker = None) -> CumulativeVolumeStrategy:
    """
    Load or return the cached strategy instance.
    When broker becomes authenticated, recreate with the real broker.
    """
    global _strategy_instance

    # If we have a cached instance, check if broker state changed
    if _strategy_instance is not None:
        # If now authenticated but strategy was using a bare broker, reset
        if authenticated and _strategy_instance.futures_token == 0:
            _strategy_instance = None
        else:
            if broker and broker._kite is not None:
                _strategy_instance.broker = broker
            return _strategy_instance

    config_file = settings.DATA_DIR / "strategy_configs" / "cumulative_volume.json"
    params = {}
    if config_file.exists():
        try:
            params = json.loads(config_file.read_text()).get("params", {})
        except json.JSONDecodeError:
            pass

    from strategies.cumulative_volume import CumulativeVolumeStrategy as CVS
    default_fut = CVS._current_month_futures()

    cfg = StrategyConfig(
        name="cumulative_volume",
        instruments=[params.get("futures_instrument", default_fut)],
        capital=0,
        max_positions=0,
        params=params,
    )

    if broker is None:
        broker = Broker()

    _strategy_instance = CumulativeVolumeStrategy(cfg, broker)
    return _strategy_instance


@router.get("/data")
async def get_cumulative_volume_data(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """
    Returns the full cumulative-volume analysis table.
    - Authenticated: real market data (latest trading day)
    - Not authenticated: demo data
    Auto-called every 60s by the frontend.
    """
    try:
        broker = get_user_broker(db, user_id)
        authenticated = _is_broker_authenticated_for_user(db, user_id)
        strategy = _get_strategy(authenticated, broker)
        result = strategy.compute(broker_authenticated=authenticated)
        return result
    except Exception as e:
        logger.error(f"Cumulative volume compute failed: {e}")
        now = datetime.now()
        return {
            "error": str(e),
            "symbol": "",
            "spot_instrument": "",
            "spot_price": 0,
            "trend_bias": "Neutral",
            "threshold": 50000,
            "last_cumulative_volume": 0,
            "candle_count": 0,
            "is_demo": True,
            "data_date": now.strftime("%Y-%m-%d"),
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
            "rows": [],
        }


@router.get("/config")
async def get_config():
    """Return the current strategy config."""
    config_file = settings.DATA_DIR / "strategy_configs" / "cumulative_volume.json"
    if config_file.exists():
        return json.loads(config_file.read_text())
    return {}


@router.put("/config")
async def update_config(body: dict):
    """Update strategy config and reset the cached instance."""
    global _strategy_instance
    config_file = settings.DATA_DIR / "strategy_configs" / "cumulative_volume.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(body, indent=2))
    _strategy_instance = None  # force reload on next request
    logger.info("Cumulative volume config updated, instance reset.")
    return {"status": "updated"}
