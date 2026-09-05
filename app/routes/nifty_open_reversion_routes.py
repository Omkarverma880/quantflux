"""
API routes for the NIFTY opening-price mean-reversion strategy.

Backtest (CSV upload or a broker pull) is read-only. Status/start/stop/positions
drive the paper/live engine. Real orders only when paper_trade is off AND the
global trading gate is on.
"""
from __future__ import annotations

import shutil
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
from research.nifty_open_reversion import service as S
from research.nifty_open_reversion.config import (
    Config, RESULTS_DIR, load_config, save_config,
)
from strategies.nifty_open_reversion_strategy import NiftyOpenReversionStrategy

router = APIRouter()
logger = get_logger("api.nifty_open_reversion")

UPLOAD_DIR = settings.DATA_DIR / "uploads" / "nifty_open_reversion"
_strategies: dict[int, NiftyOpenReversionStrategy] = {}
_last_run: dict[int, dict] = {}


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_strategy(broker: Broker, user_id: int) -> NiftyOpenReversionStrategy:
    strat = _strategies.get(user_id)
    if strat is None:
        strat = NiftyOpenReversionStrategy(broker, load_config(), user_id=user_id)
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
def meta(user_id: int = Depends(login_required)):
    """Defaults, the option modes and the uploaded datasets."""
    files = []
    d = UPLOAD_DIR / str(user_id)
    if d.exists():
        files = sorted(
            ({"name": p.name, "path": str(p), "size": p.stat().st_size,
              "modified": p.stat().st_mtime} for p in d.glob("*.csv")),
            key=lambda x: -x["modified"])
    return {"status": "ok", "config": load_config().to_dict(), "datasets": files,
            "instrument_modes": [
                {"key": "spot", "name": "Index points (spot)",
                 "note": "NIFTY points × quantity. The honest baseline — no option premium is modelled."},
                {"key": "option_buy", "name": "Option buying",
                 "note": "BUY signal → buy CALL · SELL signal → buy PUT."},
                {"key": "option_sell", "name": "Option selling",
                 "note": "BUY signal → sell PUT · SELL signal → sell CALL."}],
            "results": sorted(p.name for p in _results_dir(user_id).glob("*")) if _results_dir(user_id).exists() else []}


@router.post("/upload")
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
        logger.error("open-reversion upload failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    finally:
        await file.close()
    return {"status": "ok", "path": str(dest), "name": dest.name,
            "size": dest.stat().st_size}


@router.post("/backtest")
def backtest(payload: BacktestReq | None = None, user_id: int = Depends(login_required),
             db: Session = Depends(get_db)):
    p = payload or BacktestReq()
    cfg = Config.from_dict({**load_config().to_dict(), **(p.config or {})})
    broker = universe = token = None
    if not cfg.csv_path:
        if not _is_authed(db, user_id):
            return {"status": "error",
                    "message": "Upload a CSV, or connect Zerodha to pull the history"}
        broker = get_user_broker(db, user_id)
        strat = _get_strategy(broker, user_id)
        token = strat._resolve_index()
        universe = strat.universe
        if not token:
            return {"status": "error", "message": "Could not resolve NIFTY 50"}
    elif cfg.instrument_mode != "spot" and _is_authed(db, user_id):
        broker = get_user_broker(db, user_id)
        universe = _get_strategy(broker, user_id).universe
    try:
        res = S.run(cfg, broker=broker, token=token, universe=universe,
                    write=p.write, out_dir=_results_dir(user_id))
    except Exception as exc:
        logger.error("open-reversion backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    if res.get("status") != "ok":
        return res

    trades = res["trades"]
    slim = {
        "status": "ok", "summary": res["summary"], "checks": res["checks"],
        "scaling": res["scaling"], "monthly": res["monthly"], "yearly": res["yearly"],
        "report": res.get("report", ""), "config": res["config"],
        "source": res["source"], "bars": res["bars"],
        "first_day": res["first_day"], "last_day": res["last_day"],
        "rows_dropped": res["rows_dropped"], "duplicates_removed": res["duplicates_removed"],
        "trades": trades[-500:], "trade_count": len(trades),
        "equity_curve": [{"date": t["date"], "equity": t["equity"],
                          "drawdown": t["drawdown"], "lots": t["lots"],
                          "return_pct": t["return_pct"]} for t in trades],
        "files": {k: Path(v).name for k, v in (res.get("files") or {}).items()},
        "option_summary": res.get("option_summary"),
        "option_error": res.get("option_error"),
        "option_skipped": res.get("option_skipped"),
    }
    _last_run[user_id] = slim
    return slim


@router.get("/last")
def last(user_id: int = Depends(login_required)):
    return _last_run.get(user_id) or {"status": "empty"}


@router.get("/file/{name}")
def result_file(name: str, user_id: int = Depends(login_required)):
    """Serve one artefact (CSV or PNG) from this user's results folder."""
    safe = Path(name).name
    path = _results_dir(user_id) / safe
    if not path.exists():
        return {"status": "error", "message": f"{safe} not found — run a backtest first"}
    return FileResponse(path, filename=safe)


# ── live / paper ─────────────────────────────────────────────────────
@router.get("/status")
def status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", **_get_strategy(get_user_broker(db, user_id), user_id).get_status()}


@router.post("/start")
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
        logger.debug("open-reversion initial check failed: %s", exc)
    return {"status": "ok", **strat.get_status()}


@router.post("/stop")
def stop(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    strat.stop()
    save_config(strat.config_dict())
    return {"status": "ok", **strat.get_status()}


@router.post("/check")
def check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    return {"status": "ok", **_get_strategy(get_user_broker(db, user_id), user_id).check()}


@router.get("/positions")
def positions(date: str | None = None, user_id: int = Depends(login_required),
              db: Session = Depends(get_db)):
    strat = _get_strategy(get_user_broker(db, user_id), user_id)
    return {"status": "ok", "positions": strat.positions(date)}


@router.get("/config")
def get_config(user_id: int = Depends(login_required)):
    return {"status": "ok", "config": load_config().to_dict()}


@router.post("/config")
def post_config(payload: dict | None = None, user_id: int = Depends(login_required)):
    cfg = save_config(payload or {})
    for strat in _strategies.values():
        strat.apply_config(cfg.to_dict())
    return {"status": "ok", "config": cfg.to_dict()}
