"""
API for the NIFTY Futures Volume Breakout Lab.

Read-only against the market. The backtest runs in a background job because a three-year walk
takes minutes, and the four operating modes are a stored setting rather than a code path, so
moving from signals to paper to live is a deliberate act the user performs and can see.
"""
from __future__ import annotations

import math
import traceback
import uuid
from dataclasses import replace
from datetime import datetime
from functools import wraps
from threading import Thread

import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.logger import get_logger
from research.fvb import backtest as BT
from research.fvb import data as D
from research.fvb import execution as EX
from research.fvb import signals as SG
from research.fvb import structure as ST
from research.fvb.firewall import FirewallParams

router = APIRouter()
logger = get_logger("api.fvb")

_jobs: dict[str, dict] = {}

# the four modes the spec requires, defaulting to the two that cannot lose money
MODE_BACKTEST = "HISTORICAL_BACKTEST"
MODE_SIGNALS = "LIVE_SIGNALS_ONLY"
MODE_PAPER = "PAPER_TRADING"
MODE_LIVE = "LIVE_TRADING"
MODES = (MODE_BACKTEST, MODE_SIGNALS, MODE_PAPER, MODE_LIVE)


def json_safe(o):
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return [json_safe(v) for v in o.tolist()]
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items() if not str(k).startswith("_")}
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
                return {"status": "error",
                        "message": f"{name}: {type(exc).__name__} — {exc}"[:300]}
        return wrapper
    return deco


def _cfg_from(payload: dict) -> BT.Config:
    """Build a Config from whatever the page sent, ignoring keys it does not own."""
    def sub(cls, default, key):
        given = (payload.get(key) or {})
        fields = default.as_dict()
        return replace(default, **{k: v for k, v in given.items()
                                   if k in fields and v is not None})
    return BT.Config(
        start=payload.get("start") or "2023-09-18",
        end=payload.get("end") or "2026-09-11",
        timeframe=int(payload.get("timeframe") or 5),
        participation=payload.get("participation") or D.OPTIONS,
        firewall=bool(payload.get("firewall", True)),
        variant=payload.get("variant") or "B",
        params=sub(ST.Params, ST.P, "params"),
        entry=sub(SG.EntryParams, SG.EP, "entry"),
        execution=sub(EX.ExecParams, EX.XP, "execution"),
        firewall_params=sub(FirewallParams, FirewallParams(), "firewall_params"))


@router.get("/meta")
@safe("meta")
def meta(user_id: int = Depends(login_required)):
    """Everything the page needs to render its controls, plus what the data can support."""
    inv = D.inventory()
    return {
        "status": "ok",
        "inventory": inv,
        "futures_available": inv["futures"]["available"],
        "participation_sources": [
            {"key": D.FUTURES, "label": "NIFTY futures volume",
             "available": inv["futures"]["available"],
             "note": "The measure this strategy was designed around. Requires spot/NIFTYFUT, "
                     "which the Data Ingestion Lab can pull with a connected broker."},
            {"key": D.OPTIONS, "label": "Option volume near the money",
             "available": inv["options"]["available"],
             "note": "Real traded volume on real contracts. A different measure from futures "
                     "participation, reported under its own name and never as a substitute."},
            {"key": D.NONE, "label": "No participation filter", "available": True,
             "note": "Variant A. Breakout geometry alone."},
        ],
        "variants": [{"key": k, "label": v} for k, v in BT.VARIANTS.items()],
        "modes": list(MODES),
        "defaults": {
            "start": inv["spot"]["first"], "end": inv["spot"]["last"], "timeframe": 5,
            "participation": D.OPTIONS if not inv["futures"]["available"] else D.FUTURES,
            "variant": "B", "firewall": True,
            "params": ST.P.as_dict(), "entry": SG.EP.as_dict(),
            "execution": EX.XP.as_dict(), "firewall_params": FirewallParams().as_dict(),
        },
        "rvol_threshold_experiments": [1.0, 1.25, 1.5, 1.75, 2.0],
        "caveats": [
            "NIFTY 50 is an index. Its stored volume column is not futures participation and "
            "is never read.",
            "Only minute OHLC exists. Where a bar touched both the stop and the target, the "
            "stop is taken.",
        ],
    }


@router.get("/inventory")
@safe("inventory")
def inventory(user_id: int = Depends(login_required)):
    return {"status": "ok", **D.inventory()}


class RunReq(BaseModel):
    start: str | None = None
    end: str | None = None
    timeframe: int | None = 5
    participation: str | None = None
    firewall: bool | None = True
    variant: str | None = "B"
    params: dict | None = None
    entry: dict | None = None
    execution: dict | None = None
    firewall_params: dict | None = None
    compare_variants: bool | None = False


def _start_job(fn) -> str:
    job = uuid.uuid4().hex[:12]
    _jobs[job] = {"state": "running", "progress": "starting", "started": datetime.utcnow()
                  .isoformat(timespec="seconds"), "result": None, "error": None}

    def work():
        try:
            _jobs[job]["result"] = fn(lambda m: _jobs[job].update(progress=m))
            _jobs[job]["state"] = "done"
        except Exception as exc:
            logger.error("fvb job failed: %s | %s", exc, traceback.format_exc())
            _jobs[job].update(state="error", error=f"{type(exc).__name__}: {exc}"[:300])
    Thread(target=work, daemon=True).start()
    return job


@router.post("/backtest")
@safe("backtest")
def backtest(req: RunReq, user_id: int = Depends(login_required)):
    """Start a run. Returns a job id — a three-year walk is not an HTTP round trip."""
    cfg = _cfg_from(req.model_dump())
    if req.compare_variants:
        job = _start_job(lambda say: BT.compare(cfg, progress=say))
    else:
        job = _start_job(lambda say: BT.run(cfg, progress=say))
    return {"status": "ok", "job_id": job, "comparing": bool(req.compare_variants)}


@router.get("/job/{job_id}")
@safe("job")
def job(job_id: str, user_id: int = Depends(login_required)):
    j = _jobs.get(job_id)
    if not j:
        return {"status": "error", "message": "no such job"}
    out = {"status": "ok", "state": j["state"], "progress": j["progress"],
           "error": j["error"], "started": j["started"]}
    if j["state"] == "done":
        out["result"] = j["result"]
    return out


@router.post("/explain")
@safe("explain")
def explain(req: RunReq, user_id: int = Depends(login_required)):
    """What this configuration will and will not do, before anyone waits for a run."""
    cfg = _cfg_from(req.model_dump())
    p, ep = BT.variant_params(cfg.variant, cfg.params, cfg.entry, cfg.participation)
    inv = D.inventory()
    blocked = []
    if p.participation == D.FUTURES and not inv["futures"]["available"]:
        blocked.append("Futures participation is selected but spot/NIFTYFUT has never been "
                       "ingested. Every signal will be declined with the participation filter "
                       "unevaluated — which is the honest outcome, not a bug.")
    return {
        "status": "ok", "variant": cfg.variant, "label": BT.VARIANTS.get(cfg.variant),
        "entry_mode": ep.mode,
        "participation": p.participation,
        "rvol": f"{p.rvol_lookback} candle lookback, {p.rvol_threshold}x threshold"
                if p.participation != D.NONE else "no participation filter",
        "buffer": {"atr": f"{p.buffer_atr_mult} x ATR({p.atr_length})",
                   "points": f"{p.buffer_points} points",
                   "percent": f"{p.buffer_percent}% of price"}[p.buffer_mode],
        "firewall": ("on — a wick never starts a breakout, a close back through the level "
                     f"within {cfg.firewall_params.failure_window} candles is a confirmed "
                     f"failure, and the level then cools off for "
                     f"{cfg.firewall_params.cooldown_bars} bars")
                    if cfg.firewall else "off — breakouts are taken as they come",
        "stops": ("the underlying's invalidation and the option's own "
                  f"{cfg.execution.premium_stop_pct}% premium budget, checked separately"),
        "blocked": blocked,
    }


@router.get("/modes")
@safe("modes")
def modes(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """The operating mode, and exactly what stands between it and a real order."""
    from config import settings
    return {
        "status": "ok",
        "modes": [
            {"key": MODE_BACKTEST, "label": "Historical backtest",
             "places_orders": False, "description": "Stored data only."},
            {"key": MODE_SIGNALS, "label": "Live signals only", "places_orders": False,
             "description": "Watches the live session and raises alerts. No positions."},
            {"key": MODE_PAPER, "label": "Paper trading", "places_orders": False,
             "description": "Full position lifecycle, recorded, with no broker order."},
            {"key": MODE_LIVE, "label": "Live trading", "places_orders": True,
             "description": "Real orders. Requires the app out of paper mode and the risk "
                            "fence down, both checked at the moment of every order."},
        ],
        "default": MODE_SIGNALS,
        "app_paper_mode": bool(getattr(settings, "PAPER_TRADE", True)),
        "app_trading_enabled": bool(getattr(settings, "TRADING_ENABLED", False)),
        "note": "A mode of LIVE_TRADING is necessary but not sufficient. The app's own paper "
                "flag and the risk fence are checked again inside the order path.",
    }

# ── the live desk ────────────────────────────────────────────────────────────
class DeskReq(BaseModel):
    running: bool | None = None
    mode: str | None = None
    variant: str | None = None
    participation: str | None = None
    firewall: bool | None = None
    params: dict | None = None
    entry: dict | None = None
    execution: dict | None = None
    firewall_params: dict | None = None


@router.get("/desk")
@safe("desk")
def desk(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from research.fvb import live as LV
    return {"status": "ok", **LV.dashboard(db, user_id)}


@router.post("/desk")
@safe("desk_update")
def desk_update(req: DeskReq, user_id: int = Depends(login_required),
                db: Session = Depends(get_db)):
    """Change the desk. Switching to LIVE_TRADING is allowed here and still not sufficient —
    the app's paper flag and the risk fence are checked again at every order."""
    from research.fvb import live as LV
    row = LV.load_config(db, user_id)
    given = {k: v for k, v in req.model_dump().items() if v is not None}
    if "mode" in given and given["mode"] not in LV.MODES:
        raise ValueError(f"mode must be one of {', '.join(LV.MODES)}")
    if given:
        LV._save(db, row, **given)
        if "running" in given:
            LV._log(db, row, "desk started" if given["running"] else "desk stopped")
        if "mode" in given:
            LV._log(db, row, f"mode set to {given['mode']}")
    return {"status": "ok", **LV.dashboard(db, user_id)}


@router.post("/desk/flatten")
@safe("desk_flatten")
def desk_flatten(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Emergency kill switch: stop the desk and forget the tracked position.

    This does NOT close a real broker position — it cannot, because the desk may not be the
    only thing holding one. It stops new entries and says so plainly, so nobody reads a
    stopped desk as a flat account.
    """
    from research.fvb import live as LV
    row = LV.load_config(db, user_id)
    LV._save(db, row, running=False)
    st = LV.ENGINE._state.get(user_id) or {}
    had = bool(st.get("position"))
    st["position"] = None
    LV._log(db, row, "KILL SWITCH — desk stopped" +
            (" with a position tracked; verify the broker yourself" if had else ""))
    return {"status": "ok", "stopped": True, "had_tracked_position": had,
            "warning": ("A tracked position was dropped. This does not close anything at the "
                        "broker — check your positions.") if had else None}

# ── the futures gap, and the one button that closes it ───────────────────────
@router.post("/ingest-futures")
@safe("ingest_futures")
def ingest_futures(days: int = 5, user_id: int = Depends(login_required),
                   db: Session = Depends(get_db)):
    """Pull NIFTY futures one-minute candles into the store.

    This is the missing half of the strategy. Until spot/NIFTYFUT exists, futures participation
    reports UNAVAILABLE and Phase 3's question — does futures volume add value — cannot be
    answered at all.

    Two things worth knowing before pressing it. Kite serves a limited window of minute data per
    request, so a single run backfills only the recent past; building years of history means
    running it repeatedly as the window rolls. And futures expire monthly, so a long history is
    a chain of contracts with their own basis, not one continuous series — the engine compares
    MOVES rather than levels precisely so that stitching does not corrupt the signal.
    """
    from core.auth import UserZerodhaAuth
    from core.broker import get_user_broker
    from research.data_ingestion import service as ING
    if not UserZerodhaAuth.is_authenticated(db, user_id):
        return {"status": "error",
                "message": "Connect Zerodha first — futures candles come from the broker."}
    broker = get_user_broker(db, user_id)
    before = D.inventory()["futures"]
    job = ING.start_pull_job(broker, {
        "indices": ["NIFTY"], "days": max(1, min(int(days), 60)),
        "include_index": False, "include_vix": False, "include_futures": True,
        "expiries": 1, "strikes": 1,
    }, user_id)
    return {"status": "ok", "job": {k: job.get(k) for k in
                                    ("id", "status", "progress", "action")},
            "futures_before": before,
            "note": "Poll the Data Ingestion Lab job, then reload this page — the "
                    "participation source switches to futures once the series exists."}


@router.get("/ingest-futures/{job_id}")
@safe("ingest_futures_job")
def ingest_futures_job(job_id: str, user_id: int = Depends(login_required)):
    from research.data_ingestion import service as ING
    j = ING.get_job(job_id, user_id)
    if not j:
        return {"status": "error", "message": "no such job"}
    return {"status": "ok", "job": {k: j.get(k) for k in
                                    ("id", "status", "progress", "error", "seconds")},
            "futures": D.inventory()["futures"]}

@router.get("/today")
@safe("today")
def today_levels(variant: str = "B", participation: str | None = None,
                 user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Today's levels and triggers, read from the broker rather than the stored history.

    The store is a snapshot and today is never in it, which is why a backtest dated today
    returns nothing. This asks the broker for the live session's own candles and runs the same
    levels, buffers, participation and firewall over them.
    """
    from core.auth import UserZerodhaAuth
    from core.broker import get_user_broker
    from research.fvb import today as TD
    if not UserZerodhaAuth.is_authenticated(db, user_id):
        return {"status": "ok", "available": False,
                "message": "Connect Zerodha — today's candles come from the broker, not from "
                           "the stored history, which ends at the last ingest."}
    broker = get_user_broker(db, user_id)
    p, ep = BT.variant_params(variant or "B", ST.P, SG.EP,
                              participation or D.OPTIONS)
    return {"status": "ok", **TD.levels_now(broker, p=p, ep=ep)}
