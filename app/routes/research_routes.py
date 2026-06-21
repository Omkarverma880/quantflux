"""
API routes for the Research modules (read-only backtest / analytics).

These endpoints never place orders or mutate strategy state — they only read
historical data via the existing per-user Broker. Auth + broker resolution
follow the same pattern as the strategy routes.
"""
from datetime import date as _date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from research.vwap_pvwap import VwapPvwapResearch

router = APIRouter()
logger = get_logger("api.research")

# Per-user engine instances (reused so the daily instrument/spot caches persist)
_engines: dict[int, VwapPvwapResearch] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_engine(broker: Broker, user_id: int) -> VwapPvwapResearch:
    eng = _engines.get(user_id)
    if eng is None:
        eng = VwapPvwapResearch(broker)
        _engines[user_id] = eng
    else:
        eng.broker = broker  # keep the freshest authenticated broker
    return eng


class RunRequest(BaseModel):
    days: int = 30
    variants: list[str] | None = None
    date: str | None = None  # if set, backtest only this single day (YYYY-MM-DD)


class SignalsRequest(BaseModel):
    date: str | None = None


@router.post("/vwap-pvwap/run")
async def run_vwap_pvwap(
    payload: RunRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Run the VWAP / previous-day-VWAP backtest across the 4 variants."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error",
                "message": "Zerodha not authenticated — research needs historical data access"}
    payload = payload or RunRequest()
    target = None
    if payload.date:
        try:
            target = _date.fromisoformat(payload.date)
        except Exception:
            return {"status": "error", "message": "Invalid date (use YYYY-MM-DD)"}
    eng = _get_engine(broker, user_id)
    try:
        return eng.run(days=payload.days, variant_keys=payload.variants, target_date=target)
    except Exception as exc:
        logger.error("VWAP/PVWAP research run failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/vwap-pvwap/signals")
async def vwap_pvwap_signals(
    payload: SignalsRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Single-day signal overlay (NIFTY close, running VWAP, prev VWAP, markers)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    target = None
    if payload and payload.date:
        try:
            target = _date.fromisoformat(payload.date)
        except Exception:
            return {"status": "error", "message": "Invalid date (use YYYY-MM-DD)"}
    eng = _get_engine(broker, user_id)
    try:
        return eng.signals(target)
    except Exception as exc:
        logger.error("VWAP/PVWAP signals failed: %s", exc)
        return {"status": "error", "message": str(exc)}
