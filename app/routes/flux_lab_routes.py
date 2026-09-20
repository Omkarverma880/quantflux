"""
API routes for the Flux Strategy Test Lab (Index Strategies → Flux Strategy Test Lab).

Research and paper trading only. The lab can read market data and write its own rows; there is
no order path in this router, and paper mode is enforced in ``research.flux_lab.live``.

Long work (backtests, sweeps, walk-forward) runs as a background job with the same polling
contract the Data Ingestion Lab uses, so the browser never holds a request open for minutes.
"""
from __future__ import annotations

import math
import traceback
from functools import wraps

import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.broker import get_user_broker
from core.database import get_db
from core.logger import get_logger
from research.flux_lab import data as DATA
from research.flux_lab import engine as EN
from research.flux_lab import live as LIVE
from research.flux_lab import metrics as MX
from research.flux_lab import research as RS
from research.flux_lab import rules as RU
from research.flux_lab import service as SV

router = APIRouter()
logger = get_logger("api.flux_lab")


def json_safe(o):
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [json_safe(v) for v in o]
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


def safe(name: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            try:
                out = fn(*a, **k)
                return json_safe(out) if isinstance(out, (dict, list)) else out
            except ValueError as exc:
                return {"status": "error", "message": str(exc)[:300]}
            except Exception as exc:
                logger.error("%s failed: %s | %s", name, exc, traceback.format_exc())
                return {"status": "error", "message": f"{name}: {type(exc).__name__} — {exc}"[:300]}
        return wrapper
    return deco


def _broker(db: Session, user_id: int):
    from core.auth import UserZerodhaAuth
    try:
        if not UserZerodhaAuth.is_authenticated(db, user_id):
            return None
    except Exception:
        return None
    return get_user_broker(db, user_id)


class RunReq(BaseModel):
    config: dict
    label: str | None = ""
    store: bool = True


class ResearchReq(BaseModel):
    config: dict = {}
    grid: dict | None = None
    train: float | None = 0.6
    validation: float | None = 0.2
    train_days: int | None = 30
    test_days: int | None = 10
    run_id: int | None = None
    runs: int | None = 500
    seed: int | None = 7


class PaperReq(BaseModel):
    enabled: bool | None = None
    config: dict | None = None
    max_trades_per_day: int | None = None
    lots: int | None = None


# ── what the lab can do, and with what data ──────────────────────────
@router.get("/meta")
@safe("meta")
def meta(underlying: str = "NIFTY", user_id: int = Depends(login_required)):
    return {
        "status": "ok",
        "coverage": DATA.coverage(underlying.upper()),
        "presets": [{"key": k, **{kk: vv for kk, vv in v.items() if kk != "params"},
                     "params": v.get("params", {})} for k, v in RU.PRESETS.items()],
        "conditions": RU.catalogue(),
        "entry_models": list(EN.ENTRY_MODELS),
        "expiry_rules": list(EN.EXPIRY_RULES),
        "defaults": {"execution": EN.Execution().__dict__, "risk": EN.Risk().__dict__,
                     "selection": EN.Selection().__dict__, "timeframe": 5,
                     "capital": 200000.0},
        "point_ladder": EN.POINT_LADDER,
        "engine_version": RU.ENGINE_VERSION,
        "robustness_variants": [v[0] for v in RS.VARIANTS],
        "max_sweep_combinations": RS.MAX_COMBINATIONS,
    }


@router.post("/data-check")
@safe("data_check")
def data_check(req: RunReq, user_id: int = Depends(login_required)):
    """Inspect the data a run would use, before spending minutes on it."""
    cfg = EN.config_from(req.config)
    cov = DATA.coverage(cfg.underlying)
    span = cov.get("options") or cov.get("spot") or {}
    start = cfg.start or span.get("first", "")
    end = cfg.end or span.get("last", "")
    if not start:
        return {"status": "error", "message": "no stored data for this underlying"}
    bundle = DATA.load(cfg.underlying, start, end)
    return {"status": "ok", "range": {"start": start, "end": end},
            "quality": DATA.check(bundle, cfg.underlying), "coverage": cov}


# ── backtest ─────────────────────────────────────────────────────────
@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    def runner(payload, say, db, uid):
        return SV.backtest(payload["config"], progress=say,
                           persist=db if payload.get("store") else None,
                           user_id=uid, label=payload.get("label") or "")
    job = SV.start_job("backtest", {"config": req.config, "label": req.label, "store": req.store},
                       user_id, runner)
    return {"status": "ok", "job": {k: v for k, v in job.items() if k != "user_id"}}


@router.get("/job/{job_id}")
@safe("job")
def job(job_id: str, user_id: int = Depends(login_required)):
    j = SV.get_job(job_id, user_id)
    if j is None:
        return {"status": "error", "message": "job not found"}
    return {"status": "ok", "job": {k: v for k, v in j.items() if k != "user_id"}}


# ── research modes ───────────────────────────────────────────────────
@router.post("/research/{mode}")
@safe("research")
def research(mode: str, req: ResearchReq, user_id: int = Depends(login_required)):
    runners = {"sweep": RS.sweep, "splits": RS.splits, "walkforward": RS.walk_forward,
               "robustness": RS.robustness, "montecarlo": RS.monte_carlo}
    fn = runners.get(mode)
    if fn is None:
        return {"status": "error", "message": f"unknown research mode '{mode}'"}
    payload = req.model_dump(exclude_none=True)
    job = SV.start_job(mode, payload, user_id, lambda p, say, db, uid: fn(p, say, db, uid))
    return {"status": "ok", "job": {k: v for k, v in job.items() if k != "user_id"}}


# ── stored runs ──────────────────────────────────────────────────────
@router.get("/runs")
@safe("runs")
def runs(limit: int = 50, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "runs": SV.list_runs(db, user_id, limit)}


@router.get("/runs/{run_id}")
@safe("run")
def run_detail(run_id: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    r = SV.get_run(db, user_id, run_id)
    if r is None:
        return {"status": "error", "message": "no such run"}
    return {"status": "ok", "run": r}


@router.get("/runs/{run_id}/trades")
@safe("run_trades")
def run_trades(run_id: int, limit: int = 2000, user_id: int = Depends(login_required),
               db: Session = Depends(get_db)):
    return {"status": "ok", "trades": SV.trades_of(db, user_id, run_id, limit)}


@router.delete("/runs/{run_id}")
@safe("delete_run")
def delete_run(run_id: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok" if SV.delete_run(db, user_id, run_id) else "error"}


@router.get("/runs/{run_id}/export.csv")
@safe("export")
def export(run_id: int, what: str = "trades", user_id: int = Depends(login_required),
           db: Session = Depends(get_db)):
    body = SV.export_csv(db, user_id, run_id, what)
    return PlainTextResponse(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="flux-run-{run_id}-{what}.csv"'})


@router.post("/runs/{run_id}/montecarlo")
@safe("montecarlo")
def montecarlo(run_id: int, runs_n: int = 500, seed: int = 7,
               user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    trades = SV.trades_of(db, user_id, run_id, limit=100000)
    if not trades:
        return {"status": "error", "message": "that run has no stored trades"}
    return {"status": "ok", **MX.monte_carlo(trades, runs=runs_n, seed=seed)}


# ── replay ───────────────────────────────────────────────────────────
@router.post("/replay")
@safe("replay")
def replay(req: RunReq, user_id: int = Depends(login_required)):
    """One session, bar by bar, through the same engine the backtest uses."""
    cfg = EN.config_from(req.config)
    if not cfg.start:
        return {"status": "error", "message": "pick a date to replay"}
    cfg.end = cfg.start
    bundle = DATA.load(cfg.underlying, cfg.start, cfg.start)
    if bundle["bars"].empty:
        return {"status": "error", "message": f"no index bars stored for {cfg.start}"}
    return LIVE.replay_trace(bundle["bars"], bundle["options"], cfg,
                             vix=bundle.get("vix"), futures=bundle.get("futures"))


# ── paper trading ────────────────────────────────────────────────────
@router.get("/paper")
@safe("paper")
def paper(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return LIVE.dashboard(db, user_id, _broker(db, user_id))


@router.post("/paper/config")
@safe("paper_config")
def paper_config(req: PaperReq, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    cfg = LIVE.save_config(db, user_id, req.model_dump(exclude_none=True))
    return {"status": "ok", "config": cfg}


@router.post("/paper/check")
@safe("paper_check")
def paper_check(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Run one paper tick now — the same call the background loop makes."""
    broker = _broker(db, user_id)
    if broker is None:
        return {"status": "error", "code": "not_connected",
                "message": "Connect Zerodha to run paper trading on live data."}
    LIVE._last_check.pop(user_id, None)              # this is a deliberate manual tick
    return {"status": "ok", "result": LIVE.ENGINE.check(db, user_id, broker)}


@router.get("/paper/signals")
@safe("paper_signals")
def paper_signals(limit: int = 100, user_id: int = Depends(login_required),
                  db: Session = Depends(get_db)):
    from core.models import FluxLabSignal
    rows = (db.query(FluxLabSignal).filter(FluxLabSignal.user_id == user_id)
              .order_by(FluxLabSignal.id.desc()).limit(limit).all())
    return {"status": "ok", "signals": [{
        "id": s.id, "at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else None,
        "date": str(s.trade_date), "bar": s.bar_time, "strategy": s.strategy_name,
        "side": s.side, "spot": float(s.spot) if s.spot is not None else None,
        "fired": s.fired, "acted": s.acted, "skip_reason": s.skip_reason,
        "reasons": s.reasons, "indicators": s.indicators, "regime": s.regime,
        "trade_id": s.trade_id} for s in rows]}


@router.get("/paper/compare/{run_id}")
@safe("compare")
def compare(run_id: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Paper results beside the backtest that predicted them, in the same trade format."""
    from core.models import FluxLabTrade
    back = SV.trades_of(db, user_id, run_id, limit=100000)
    rows = (db.query(FluxLabTrade)
              .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.mode == "PAPER",
                      FluxLabTrade.status == "CLOSED").all())
    paper_trades = [SV._trade_dict(t) for t in rows]
    if not paper_trades:
        return {"status": "ok", "paper_trades": 0,
                "message": "No completed paper trades yet — the comparison fills in as they close."}

    def block(rows_):
        import pandas as pd
        df = pd.DataFrame(rows_)
        return MX._block(df) if not df.empty else {"trades": 0}

    return {"status": "ok", "backtest": block(back), "paper": block(paper_trades),
            "paper_list": paper_trades[:100],
            "note": "Different periods and sample sizes — this compares behaviour, not totals. "
                    "A paper win rate far below the backtest usually means fills, not logic."}
