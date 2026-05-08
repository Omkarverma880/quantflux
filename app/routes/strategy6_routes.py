"""
API routes for Strategy 6 — Manual CALL / PUT Line Touch Entry.

Mirrors the Strategy 4 / 5 pattern. Adds a /lines endpoint so the
frontend can update either or both of the user-drawn horizontal lines
via drag, double-click edit, or numeric input.
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
from strategies.strategy6_call_put_lines import Strategy6CallPutLines

router = APIRouter()
logger = get_logger("api.strategy6")

_user_strategies: dict[int, Strategy6CallPutLines] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy6_call_put_lines.json"


class Strategy6Config(BaseModel):
    sl_points: float = 30
    target_points: float = 60
    lot_size: int = 65
    lots: int = 1
    strike_interval: int = 50
    sl_proximity: float = 5
    target_proximity: float = 5
    max_trades_per_day: int = 3
    itm_offset: int = 100
    max_entry_slippage: float = 8
    index_name: str = "NIFTY"
    call_line: float = 0
    put_line: float = 0


class LinesUpdate(BaseModel):
    call_line: float | None = None
    put_line: float | None = None


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


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy6CallPutLines:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker._kite is not None:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy6CallPutLines(broker, _load_config())
    if strat.restore_state():
        logger.info("Strategy 6 state restored for user %s: %s", user_id, strat.state.value)
    _user_strategies[user_id] = strat
    return strat


def _get_spot_price(broker: Broker, authenticated: bool) -> float:
    if not authenticated:
        return 0.0
    try:
        ltp = broker.get_ltp(["NSE:NIFTY 50"])
        return float(ltp.get("NSE:NIFTY 50", 0) or 0)
    except Exception as exc:
        logger.debug("S6 spot LTP fetch failed: %s", exc)
        return 0.0


# ── Endpoints ──────────────────────────────────────


@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    try:
        spot = _get_spot_price(broker, _is_authed(db, user_id))
        if spot > 0:
            strat.spot_price = spot
    except Exception:
        pass
    return strat.get_status()


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
        logger.error("S6 intraday fetch failed: %s", exc)
        return {"status": "error", "series": [], "message": str(exc)}


@router.post("/start")
async def start_strategy(config: Strategy6Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    broker = get_user_broker(db, user_id)
    # Preserve any lines already set in-memory if the start payload sent zeros
    existing = _user_strategies.get(user_id)
    if existing is not None:
        if not params.get("call_line") and existing.call_line:
            params["call_line"] = existing.call_line
        if not params.get("put_line") and existing.put_line:
            params["put_line"] = existing.put_line
    strat = Strategy6CallPutLines(broker, params)
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
    if not strat.is_active and getattr(strat.state, "value", str(strat.state)) != "POSITION_OPEN":
        return strat.get_status()
    try:
        spot = _get_spot_price(broker, authed)
        result = strat.check(spot)
        result["spot_price"] = spot
        return result
    except Exception as exc:
        logger.error("S6 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.put("/config")
async def update_config(config: Strategy6Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    strat = _user_strategies.get(user_id)
    if strat is not None:
        strat.apply_config(params)
    return {"status": "updated", "config": params}


@router.post("/lines")
async def update_lines(payload: LinesUpdate, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Update CALL and/or PUT line price.

    Supports:
      - numeric input edits ({"call_line": 24500, "put_line": 24300})
      - dragging a single line ({"call_line": 24512})
      - clearing a line ({"call_line": 0})
    """
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    result = strat.set_lines(call_line=payload.call_line, put_line=payload.put_line)

    # Persist new lines to the on-disk config so they survive restarts
    # even if the strategy was never explicitly started.
    cfg = _load_config()
    cfg["call_line"] = result["call_line"]
    cfg["put_line"] = result["put_line"]
    _save_config(cfg)
    return {"status": "ok", **result}


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required)):
    file = settings.DATA_DIR / "trade_history" / "strategy6_trades.json"
    if not file.exists():
        return {"trades": []}
    try:
        return {"trades": json.loads(file.read_text())}
    except Exception:
        return {"trades": []}



# ── Risk / re-entry control ─────────────────────────

class RiskConfigPayload(BaseModel):
    allow_reentry_after_target: bool | None = None
    allow_reentry_after_sl: bool | None = None
    require_manual_confirmation_after_sl: bool | None = None
    auto_pause_after_sl: bool | None = None
    max_reentries_per_day: int | None = None
    max_sl_hits_per_day: int | None = None
    max_consecutive_losses: int | None = None
    entry_cooldown_seconds: int | None = None
    require_fresh_crossover: bool | None = None
    fresh_crossover_distance: float | None = None


@router.get("/risk")
async def get_risk_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    return {"status": "ok", "risk": strat.risk.status_payload()}


@router.post("/risk/config")
async def update_risk_config(payload: RiskConfigPayload, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    strat.risk.update_config(**payload.model_dump(exclude_none=True))
    try: strat._save_state()
    except Exception: pass
    return {"status": "ok", "risk": strat.risk.status_payload()}


@router.post("/risk/resume")
async def resume_after_sl(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    strat.risk.confirm_resume()
    try: strat._save_state()
    except Exception: pass
    return {"status": "ok", "risk": strat.risk.status_payload()}


@router.post("/risk/pause")
async def pause_strategy_risk(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    strat.risk.pause()
    try: strat._save_state()
    except Exception: pass
    return {"status": "ok", "risk": strat.risk.status_payload()}


@router.post("/risk/reset")
async def reset_risk_counters(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    strat.risk.reset_counters()
    try: strat._save_state()
    except Exception: pass
    return {"status": "ok", "risk": strat.risk.status_payload()}
