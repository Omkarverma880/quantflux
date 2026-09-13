"""
API routes for the Index Straddle Engine.

Backtest (CSV upload or a broker pull) is read-only. Status/start/stop/positions
drive the paper/live engine. Real orders only when paper_trade is off AND the
global trading gate is on.
"""
from __future__ import annotations

import math
import shutil
import traceback
from functools import wraps
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from research.index_straddle import service as S
from research.index_straddle.config import (
    Config, RESULTS_DIR, load_config, save_config,
)
from research.index_straddle import options as O
from strategies.index_straddle_strategy import IndexStraddleStrategy

router = APIRouter()
logger = get_logger("api.index_straddle")


def json_safe(o):
    """Strip anything JSON cannot carry.

    Starlette serialises with ``allow_nan=False`` AFTER the handler returns, so a
    NaN produced anywhere in pandas becomes an un-catchable HTTP 500. Scrub to null.
    """
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    return o


def safe(name: str):
    """Never surface a bare HTTP 500 — the page should be able to explain it."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            try:
                out = fn(*a, **k)
                return json_safe(out) if isinstance(out, (dict, list)) else out
            except Exception as exc:
                logger.error("%s failed: %s | %s", name, exc, traceback.format_exc())
                return {"status": "error",
                        "message": f"{name}: {type(exc).__name__} — {exc}"[:400]}
        return wrapper
    return deco


UPLOAD_DIR = settings.DATA_DIR / "uploads" / "index_straddle"
_strategies: dict[int, IndexStraddleStrategy] = {}
_last_run: dict[int, dict] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_strategy(broker: Broker, user_id: int) -> IndexStraddleStrategy:
    strat = _strategies.get(user_id)
    if strat is None:
        strat = IndexStraddleStrategy(broker, load_config(), user_id=user_id)
        _strategies[user_id] = strat
    else:
        strat.broker = broker
        strat.universe.broker = broker
    strat.user_id = user_id
    return strat


def _results_dir(user_id: int) -> Path:
    return Path(RESULTS_DIR) / f"user_{user_id}"


class BacktestReq(BaseModel):
    config: dict | None = None
    write: bool = True


class StrategyCfg(BaseModel):
    config: dict | None = None


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required)):
    """Defaults, the selectable modes, the research reference and uploaded data."""
    files = []
    d = UPLOAD_DIR / str(user_id)
    if d.exists():
        files = sorted(
            ({"name": p.name, "path": str(p), "size": p.stat().st_size,
              "modified": p.stat().st_mtime} for p in d.glob("*.csv")),
            key=lambda x: -x["modified"])
    return {
        "status": "ok",
        "config": load_config().to_dict(),
        "defaults": Config().to_dict(),
        "datasets": files,
        "research": S.RESEARCH_REFERENCE,
        "directions": [
            {"key": "short", "name": "SHORT straddle (sell premium)",
             "note": "The configuration the research supports: +16.3% of credit per "
                     "trade, 72.9% win rate, t = 10.0 over 421 trades. Collects theta; "
                     "loses fast on a trend day, so the stop is the whole risk control."},
            {"key": "long", "name": "LONG straddle (buy premium)",
             "note": "Tested and rejected: −7.35% per trade pooled, t = −8.95. Included "
                     "so the claim can be re-checked, not because it is recommended."}],
        "structures": [
            {"key": "straddle", "name": "Straddle — one strike, CE + PE"},
            {"key": "strangle", "name": "Strangle — two wings either side of ATM"}],
        "strike_modes": [
            {"key": "atm_offset", "name": "Distance from ATM (points)"},
            {"key": "premium", "name": "Target premium (pick the strike that prices nearest)"}],
        "strike_offsets": [
            {"value": v, "label": O.moneyness_label(v)}
            for v in (-300, -200, -150, -100, -50, 0, 50, 100, 150, 200, 300)],
        "premium_sources": [
            {"key": "model", "name": "Calibrated research model",
             "note": "Reproduces the documented result and works over the whole index "
                     "history. Validated at r = 0.988 against a real chain, but the "
                     "0-DTE premium level is an assumption — see the ⓘ tab."},
            {"key": "broker", "name": "Real option candles (Zerodha)",
             "note": "The contract's own traded premium — the honest test, but limited "
                     "to whatever option history Zerodha serves, usually months not years. "
                     "Days with no contract history are skipped, never modelled."}],
        "results": sorted(p.name for p in _results_dir(user_id).glob("*"))
                   if _results_dir(user_id).exists() else [],
    }


@router.post("/upload")
@safe("upload")
async def upload(file: UploadFile = File(...), user_id: int = Depends(login_required)):
    """Store a 1-minute OHLC CSV for this user and return its path."""
    if not (file.filename or "").lower().endswith(".csv"):
        return {"status": "error", "message": "Please upload a .csv file"}
    d = UPLOAD_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / Path(file.filename).name
    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as exc:
        logger.error("index_straddle upload failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    finally:
        await file.close()
    return {"status": "ok", "path": str(dest), "name": dest.name,
            "size": dest.stat().st_size}


@router.post("/backtest")
@safe("backtest")
def backtest(payload: BacktestReq | None = None, user_id: int = Depends(login_required),
             db: Session = Depends(get_db)):
    p = payload or BacktestReq()
    cfg = Config.from_dict({**load_config().to_dict(), **(p.config or {})})
    broker = universe = token = None
    if not cfg.csv_path:
        if not _is_authed(db, user_id):
            return {"status": "error",
                    "message": "Upload a 1-minute CSV, or connect Zerodha to pull the history"}
        broker = get_user_broker(db, user_id)
        strat = _get_strategy(broker, user_id)
        token = strat._resolve_index()
        universe = strat.universe
        if not token:
            return {"status": "error", "message": "Could not resolve NIFTY 50"}
    elif cfg.premium_source == "broker":
        if not _is_authed(db, user_id):
            return {"status": "error",
                    "message": "Real-premium mode needs a connected Zerodha session"}
        broker = get_user_broker(db, user_id)
        universe = _get_strategy(broker, user_id).universe

    try:
        res = S.run(cfg, broker=broker, token=token, universe=universe,
                    write=p.write, out_dir=_results_dir(user_id))
    except Exception as exc:
        logger.error("index_straddle backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    if res.get("status") != "ok":
        return res

    trades = res["trades"]
    slim = {
        "status": "ok",
        "summary": res["summary"], "checks": res["checks"],
        "monthly": res["monthly"], "yearly": res["yearly"], "by_dte": res["by_dte"],
        "coverage": res["coverage"], "config": res["config"],
        "describe": res["describe"], "source": res["source"],
        "premium_source": res["premium_source"], "bars": res["bars"],
        "first_day": res["first_day"], "last_day": res["last_day"],
        "rows_dropped": res["rows_dropped"],
        "duplicates_removed": res["duplicates_removed"],
        "partial_sessions_dropped": res["partial_sessions_dropped"],
        "trades": trades[-1000:], "trade_count": len(trades),
        "equity_curve": res["equity_curve"],
        "contract_example": res.get("contract_example"),
        "files": {k: Path(v).name for k, v in (res.get("files") or {}).items()},
        "parity": S.parity(res["summary"]),
    }
    _last_run[user_id] = slim
    return slim


@router.get("/last")
@safe("last")
def last(user_id: int = Depends(login_required)):
    return _last_run.get(user_id) or {"status": "empty"}


@router.get("/file/{name}")
@safe("result_file")
def result_file(name: str, user_id: int = Depends(login_required)):
    """Serve one artefact from this user's results folder."""
    fname = Path(name).name
    path = _results_dir(user_id) / fname
    if not path.exists():
        return {"status": "error", "message": f"{fname} not found — run a backtest first"}
    return FileResponse(path, filename=fname)


@router.post("/preview")
@safe("preview")
def preview(payload: StrategyCfg | None = None, user_id: int = Depends(login_required),
            db: Session = Depends(get_db)):
    """What would this config trade right now? Strike, expiry, quantity, stop."""
    cfg = Config.from_dict({**load_config().to_dict(),
                            **((payload.config if payload else None) or {})})
    spot = None
    if _is_authed(db, user_id):
        spot = _get_strategy(get_user_broker(db, user_id), user_id)._index_ltp()
    if not spot:
        return {"status": "ok", "spot": None,
                "message": "Connect Zerodha to preview live strikes"}
    from datetime import date as _d
    legs = O.legs_for(cfg, spot)
    exp = O.expiry_for(cfg, _d.today())
    return {"status": "ok", "spot": spot, "legs": legs,
            "expiry": str(exp), "dte": (exp - _d.today()).days,
            "describe": cfg.describe()}


# ── live / paper ─────────────────────────────────────────────────────
@router.get("/status")
@safe("status")
def status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok",
            **_get_strategy(get_user_broker(db, user_id), user_id).get_status()}


@router.post("/start")
@safe("start")
def start(payload: StrategyCfg | None = None, user_id: int = Depends(login_required),
          db: Session = Depends(get_db)):
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — log in first"}
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.start((payload.config if payload else None) or {})
    save_config(strat.config_dict())
    try:
        strat.check()
    except Exception as exc:
        logger.debug("index_straddle initial check failed: %s", exc)
    return {"status": "ok", **strat.get_status()}


@router.post("/stop")
@safe("stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.stop()
    save_config(strat.config_dict())
    return {"status": "ok", **strat.get_status()}


@router.post("/check")
@safe("check")
def check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    return {"status": "ok",
            **_get_strategy(get_user_broker(db, user_id), user_id).check()}


@router.post("/squareoff")
@safe("squareoff")
def squareoff(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.square_off_all("MANUAL")
    return {"status": "ok", **strat.get_status()}


@router.get("/positions")
@safe("positions")
def positions(date: str | None = None, user_id: int = Depends(login_required),
              db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    return {"status": "ok", "positions": strat.positions(date)}


@router.get("/config")
@safe("get_config")
def get_config(user_id: int = Depends(login_required)):
    return {"status": "ok", "config": load_config().to_dict()}


@router.post("/config")
@safe("post_config")
def post_config(payload: dict | None = None, user_id: int = Depends(login_required)):
    cfg = save_config(payload or {})
    for strat in _strategies.values():
        strat.apply_config(cfg.to_dict())
    return {"status": "ok", "config": cfg.to_dict()}
