"""
API routes for Strategy 8 — CE/PE Reverse Line Touch Entry.

Mirrors Strategy 7 endpoints with extra reverse-mode controls:
  • POST /reverse-mode      — toggle AUTO / MANUAL
  • POST /set-reverse-strikes — pick manual reverse PE / CE strikes
  • POST /backtest          — replay historical candles (also exposed on S7)
"""
from datetime import date as _date
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from strategies.strategy8_reverse import Strategy8Reverse
from strategies.backtest_engine import run_backtest

router = APIRouter()
logger = get_logger("api.strategy8")

_user_strategies: dict[int, Strategy8Reverse] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy8_reverse.json"


class Strategy8Config(BaseModel):
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
    reverse_mode: str = "AUTO"
    reverse_offset: int = 200
    manual_pe_strike: int = 0
    manual_pe_symbol: str = ""
    manual_pe_token: int = 0
    manual_ce_strike: int = 0
    manual_ce_symbol: str = ""
    manual_ce_token: int = 0


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


class ReverseStrikesUpdate(BaseModel):
    manual_pe: StrikeSelection | None = None  # used on CALL trigger
    manual_ce: StrikeSelection | None = None  # used on PUT trigger


class ReverseModeUpdate(BaseModel):
    mode: str  # "AUTO" | "MANUAL"


class BacktestRequest(BaseModel):
    trade_date: str            # "YYYY-MM-DD"
    ce_token: int
    pe_token: int
    ce_strike: int
    pe_strike: int
    call_line: float
    put_line: float
    sl_points: float = 30
    target_points: float = 60
    lot_size: int = 65
    lots: int = 1
    max_trades: int = 3
    reverse_offset: int = 200
    manual_pe_strike: int = 0
    manual_ce_strike: int = 0
    reverse_ce_token: int = 0
    reverse_pe_token: int = 0


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


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy8Reverse:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker._kite is not None:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy8Reverse(broker, _load_config())
    if strat.restore_state():
        logger.info("Strategy 8 state restored for user %s: %s", user_id, strat.state.value)
    _user_strategies[user_id] = strat
    return strat


def _get_spot_price(broker: Broker, authenticated: bool) -> float:
    if not authenticated:
        return 0.0
    try:
        ltp = broker.get_ltp(["NSE:NIFTY 50"])
        return float(ltp.get("NSE:NIFTY 50", 0) or 0)
    except Exception as exc:
        logger.debug("S8 spot LTP fetch failed: %s", exc)
        return 0.0


# ── Endpoints ──────────────────────────────────────


@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    try:
        if _is_authed(db, user_id):
            strat.fetch_ltps()
    except Exception:
        pass
    return strat.get_status()


@router.get("/strikes")
async def list_strikes(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
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
        logger.error("S8 strikes fetch failed: %s", exc)
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
        "ce_strike": result["ce_strike"], "ce_symbol": result["ce_symbol"], "ce_token": strat.ce_token,
        "pe_strike": result["pe_strike"], "pe_symbol": result["pe_symbol"], "pe_token": strat.pe_token,
    })
    _save_config(cfg)
    return {"status": "ok", **result}


@router.post("/set-reverse-strikes")
async def set_reverse_strikes(payload: ReverseStrikesUpdate, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    pe_dict = payload.manual_pe.model_dump() if payload.manual_pe else None
    ce_dict = payload.manual_ce.model_dump() if payload.manual_ce else None
    result = strat.set_reverse_strikes(pe_dict, ce_dict)
    cfg = _load_config()
    cfg.update({
        "manual_pe_strike": result["manual_pe_strike"], "manual_pe_symbol": result["manual_pe_symbol"], "manual_pe_token": strat.manual_pe_token,
        "manual_ce_strike": result["manual_ce_strike"], "manual_ce_symbol": result["manual_ce_symbol"], "manual_ce_token": strat.manual_ce_token,
    })
    _save_config(cfg)
    return {"status": "ok", **result}


@router.post("/reverse-mode")
async def set_reverse_mode(payload: ReverseModeUpdate, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    mode = strat.set_reverse_mode(payload.mode)
    cfg = _load_config()
    cfg["reverse_mode"] = mode
    _save_config(cfg)
    return {"status": "ok", "reverse_mode": mode}


@router.get("/intraday")
async def get_intraday(side: str = "CE", user_id: int = Depends(login_required), db: Session = Depends(get_db)):
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
        logger.error("S8 intraday fetch failed: %s", exc)
        return {"status": "error", "series": [], "message": str(exc)}


@router.post("/start")
async def start_strategy(config: Strategy8Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    params = config.model_dump()
    _save_config(params)
    broker = get_user_broker(db, user_id)
    existing = _user_strategies.get(user_id)
    if existing is not None:
        if not params.get("call_line") and existing.call_line:
            params["call_line"] = existing.call_line
        if not params.get("put_line") and existing.put_line:
            params["put_line"] = existing.put_line
        for k in ("ce_strike", "ce_symbol", "ce_token", "pe_strike", "pe_symbol", "pe_token",
                  "manual_pe_strike", "manual_pe_symbol", "manual_pe_token",
                  "manual_ce_strike", "manual_ce_symbol", "manual_ce_token", "reverse_mode"):
            if not params.get(k) and getattr(existing, k, None):
                params[k] = getattr(existing, k)
    strat = Strategy8Reverse(broker, params)
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
        logger.error("S8 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.put("/config")
async def update_config(config: Strategy8Config, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
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
    file = settings.DATA_DIR / "trade_history" / "strategy8_trades.json"
    if not file.exists():
        return {"trades": []}
    try:
        return {"trades": json.loads(file.read_text())}
    except Exception:
        return {"trades": []}


@router.post("/backtest")
async def run_strategy_backtest(payload: BacktestRequest, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha session not authenticated"}
    try:
        td = _date.fromisoformat(payload.trade_date)
    except Exception:
        return {"status": "error", "message": "invalid trade_date (YYYY-MM-DD)"}
    try:
        result = run_backtest(
            broker, strategy="S8", trade_date=td,
            ce_token=payload.ce_token, pe_token=payload.pe_token,
            ce_strike=payload.ce_strike, pe_strike=payload.pe_strike,
            call_line=payload.call_line, put_line=payload.put_line,
            sl_points=payload.sl_points, target_points=payload.target_points,
            lot_size=payload.lot_size, lots=payload.lots,
            max_trades=payload.max_trades, reverse_offset=payload.reverse_offset,
            manual_pe_strike=payload.manual_pe_strike,
            manual_ce_strike=payload.manual_ce_strike,
            reverse_ce_token=payload.reverse_ce_token,
            reverse_pe_token=payload.reverse_pe_token,
        )
        return result
    except Exception as exc:
        logger.error("S8 backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}



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
