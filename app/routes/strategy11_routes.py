"""
API routes for Strategy 11 — VWAP vs Previous-Day VWAP (positional options).

Same per-user pattern as the other strategy routes: one instance per user,
state restored from disk on first access, driven by the background loop.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from strategies.strategy11_vwap_pvwap import Strategy11VwapPvwap

router = APIRouter()
logger = get_logger("api.strategy11")

_user_strategies: dict[int, Strategy11VwapPvwap] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy11_vwap_pvwap.json"
DOC_FILE = Path(__file__).resolve().parents[2] / "documentation_review.json"


def _doc_password() -> str:
    try:
        return str(json.loads(DOC_FILE.read_text()).get("strategy11", {}).get("password", "3569"))
    except Exception:
        return "3569"


class Strategy11Config(BaseModel):
    paper_trade: bool = True
    expiry_type: str = "monthly"        # monthly | weekly
    strike_mode: str = "ITM"            # ITM | OTM | ATM
    target_mode: str = "points"         # points | percent | double
    target_points: float = 300
    target_percent: float = 150
    lots: int = 3
    manage_second_leg: bool = True
    leg2_exit_mode: str = "fraction"    # points | percent | fraction
    leg2_exit_value: float = 2          # fraction: 2/3/4 · points/percent: buffer
    max_open_pairs: int = 5


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


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy11VwapPvwap:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker.is_kite_connected:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy11VwapPvwap(broker, _load_config(), user_id=user_id)
    if strat.restore_state():
        logger.info("S11 state restored for user %s", user_id)
    _user_strategies[user_id] = strat
    return strat


def _row_to_trade(r) -> dict:
    held = 0
    if r.exit_date and r.trade_date:
        held = (r.exit_date - r.trade_date).days
    return {
        "date": r.trade_date.isoformat() if r.trade_date else None,
        "entry_time": r.entry_time,
        "exit_date": r.exit_date.isoformat() if r.exit_date else None,
        "exit_time": r.exit_time, "held_days": held,
        "direction": "CALL" if r.option_type == "CE" else "PUT",
        "signal": r.signal, "expiry_type": r.expiry_type,
        "expiry": r.expiry.isoformat() if r.expiry else None,
        "strike": r.strike, "symbol": r.symbol,
        "premium_buy": float(r.entry_price) if r.entry_price is not None else None,
        "target_premium": float(r.target_price) if r.target_price is not None else None,
        "premium_sell": float(r.exit_price) if r.exit_price is not None else None,
        "qty": r.qty, "pnl": float(r.pnl) if r.pnl is not None else None,
        "exit_reason": r.exit_reason, "paper": bool(r.paper),
    }


# ── Endpoints ─────────────────────────────────────────

@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    return _get_strategy(broker, user_id).get_status()


@router.post("/start")
async def start_strategy(config: Strategy11Config, user_id: int = Depends(login_required),
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
async def update_config(config: Strategy11Config, user_id: int = Depends(login_required)):
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
    if not strat.is_active and not strat.has_open_positions:
        return strat.get_status()
    try:
        return strat.check()
    except Exception as exc:
        logger.error("S11 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.post("/doc-unlock")
async def doc_unlock(payload: dict | None = None, user_id: int = Depends(login_required)):
    """Verify the documentation password (stored in documentation_review.json)."""
    pwd = str((payload or {}).get("password", ""))
    return {"ok": pwd == _doc_password()}


@router.post("/simulate-entry")
async def simulate_entry(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Paper-only: force a crossover entry now to watch the full cycle."""
    broker = get_user_broker(db, user_id)
    return _get_strategy(broker, user_id).simulate_entry()


@router.post("/reset")
async def reset_positions(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Paper-only: clear all tracked positions + DB rows (wipe test data)."""
    broker = get_user_broker(db, user_id)
    return _get_strategy(broker, user_id).reset_positions()


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    # DB first (Railway-safe, pgAdmin-visible); fall back to the JSON file.
    try:
        from core.models import Strategy11Leg
        rows = (db.query(Strategy11Leg)
                .filter(Strategy11Leg.user_id == user_id, Strategy11Leg.state != "OPEN")
                .order_by(Strategy11Leg.id.desc()).limit(500).all())
        if rows:
            return {"trades": [_row_to_trade(r) for r in rows]}
    except Exception as exc:
        logger.debug("S11 history DB read failed: %s", exc)
    file = settings.DATA_DIR / "trade_history" / "strategy11_trades.json"
    if not file.exists():
        return {"trades": []}
    try:
        return {"trades": list(reversed(json.loads(file.read_text())))}
    except Exception:
        return {"trades": []}
