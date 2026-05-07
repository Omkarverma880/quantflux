"""
API routes for Strategy 7 — CE/PE Strike Line Touch Entry.

Adds /strikes endpoint to enumerate ATM±N strikes for both CE and PE,
and /set-strikes to select the two monitored strikes. Mirrors S6 for
everything else (status, intraday, lines, start, stop, check, config,
history).
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
from strategies.strategy7_strike_lines import Strategy7StrikeLines

router = APIRouter()
logger = get_logger("api.strategy7")

_user_strategies: dict[int, Strategy7StrikeLines] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy7_strike_lines.json"


class Strategy7Config(BaseModel):
    sl_points: float = 30
    target_points: float = 60
    lot_size: int = 65
    lots: int = 1
    strike_interval: int = 50
    sl_proximity: float = 5
    target_proximity: float = 5
    max_trades_per_day: int = 3
    max_entry_slippage: float = 8
    index_name: str = "NIFTY"
    call_line: float = 0
    put_line: float = 0
    ce_strike: int = 0
    pe_strike: int = 0
    ce_symbol: str = ""
    pe_symbol: str = ""
    ce_token: int = 0
    pe_token: int = 0


class LinesUpdate(BaseModel):
    call_line: float | None = None
    put_line: float | None = None


class StrikeSelection(BaseModel):
    strike: int
    tradingsymbol: str | None = None
    token: int | None = None


class StrikesUpdate(BaseModel):
    ce: StrikeSelection | None = None
    pe: StrikeSelection | None = None


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


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy7StrikeLines:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker._kite is not None:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy7StrikeLines(broker, _load_config())
    if strat.restore_state():
        logger.info("Strategy 7 state restored for user %s: %s", user_id, strat.state.value)
    _user_strategies[user_id] = strat
    return strat


def _get_spot_price(broker: Broker, authenticated: bool) -> float:
    if not authenticated:
        return 0.0
    try:
        ltp = broker.get_ltp(["NSE:NIFTY 50"])
        return float(ltp.get("NSE:NIFTY 50", 0) or 0)
    except Exception as exc:
        logger.debug("S7 spot LTP fetch failed: %s", exc)
        return 0.0


# ── Endpoints ──────────────────────────────────────


@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    # Always-live LTP feed — fetch even before Start
    try:
        if _is_authed(db, user_id):
            strat.fetch_ltps()
    except Exception:
        pass
    return strat.get_status()


@router.get("/strikes")
async def list_strikes(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Return 5 strikes above and below ATM for both CE and PE."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "atm": 0, "strikes": [], "spot": 0}
    strat = _get_strategy(broker, user_id)
    spot = _get_spot_price(broker, True)
    if spot > 0:
        strat.spot_price = spot
    try:
        data = strat.list_strikes(spot, count=5)
        return {"status": "ok", "spot": spot, **data}
    except Exception as exc:
        logger.error("S7 strikes fetch failed: %s", exc)
        return {"status": "error", "atm": 0, "strikes": [], "spot": spot, "message": str(exc)}


@router.post("/set-strikes")
async def set_strikes(payload: StrikesUpdate, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    ce_dict = payload.ce.model_dump() if payload.ce else None
    pe_dict = payload.pe.model_dump() if payload.pe else None
    result = strat.set_strikes(ce_dict, pe_dict)

    cfg = _load_config()
    cfg.update({
        "ce_strike": result["ce_strike"], "ce_symbol": result["ce_symbol"],
        "ce_token":  strat.ce_token,
        "pe_strike": result["pe_strike"], "pe_symbol": result["pe_symbol"],
        "pe_token":  strat.pe_token,
    })
    _save_config(cfg)
    return {"status": "ok", **result}


@router.get("/intraday")
async def get_intraday(side: str = "CE", user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Today's minute-candle close series for the chosen strike (CE or PE)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "series": []}
    strat = _get_strategy(broker, user_id)
    try:
        side_u = (side or "CE").upper()
        if side_u not in ("CE", "PE"):
            side_u = "CE"
        series = strat.get_intraday_series(side_u)
        return {"status": "ok", "side": side_u, "series": series}
    except Exception as exc:
        logger.error("S7 intraday fetch failed: %s", exc)
        return {"status": "error", "series": [], "message": str(exc)}


@router.post("/start")
async def start_strategy(config: Strategy7Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    broker = get_user_broker(db, user_id)
    existing = _user_strategies.get(user_id)
    # Preserve in-memory selections if start payload omits them
    if existing is not None:
        if not params.get("call_line") and existing.call_line:
            params["call_line"] = existing.call_line
        if not params.get("put_line") and existing.put_line:
            params["put_line"] = existing.put_line
        for k in ("ce_strike", "ce_symbol", "ce_token", "pe_strike", "pe_symbol", "pe_token"):
            if not params.get(k) and getattr(existing, k, None):
                params[k] = getattr(existing, k)
    strat = Strategy7StrikeLines(broker, params)
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
    strat = _get_strategy(broker, user_id)
    if not strat.is_active and getattr(strat.state, "value", str(strat.state)) != "POSITION_OPEN":
        return strat.get_status()
    try:
        return strat.check()
    except Exception as exc:
        logger.error("S7 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.put("/config")
async def update_config(config: Strategy7Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    strat = _user_strategies.get(user_id)
    if strat is not None:
        strat.apply_config(params)
    return {"status": "updated", "config": params}


@router.post("/lines")
async def update_lines(payload: LinesUpdate, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    result = strat.set_lines(call_line=payload.call_line, put_line=payload.put_line)
    cfg = _load_config()
    cfg["call_line"] = result["call_line"]
    cfg["put_line"] = result["put_line"]
    _save_config(cfg)
    return {"status": "ok", **result}


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required)):
    file = settings.DATA_DIR / "trade_history" / "strategy7_trades.json"
    if not file.exists():
        return {"trades": []}
    try:
        return {"trades": json.loads(file.read_text())}
    except Exception:
        return {"trades": []}
