"""
Flux Lab — orchestration.

Loads data through the Market Store, checks it, runs the engine, computes the statistics and
stores the run so it can be reproduced later. Long runs happen on a background thread with the
same job contract the Data Ingestion Lab uses, so the UI polls instead of blocking.

Reproducibility is a property of the stored record, not a promise: the configuration is hashed
(canonical JSON, sorted keys) and saved with the engine version. Re-running the same hash over
the same data must produce the same trades — there is no randomness anywhere in the engine, and
the one place randomness exists (Monte Carlo) takes an explicit seed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import time
import traceback
import uuid
from datetime import datetime
from typing import Callable, Optional

import pandas as pd

from core.logger import get_logger
from core.models import FluxLabRun, FluxLabTrade
from research.flux_lab import data as DATA
from research.flux_lab import engine as EN
from research.flux_lab import metrics as MX
from research.flux_lab import rules as RU

logger = get_logger("research.flux_lab.service")

MAX_SKIP_SAMPLES = 400
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def config_hash(cfg: dict) -> str:
    """A stable fingerprint of a run configuration — same inputs, same id."""
    blob = json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


# ── one backtest ─────────────────────────────────────────────────────
def backtest(payload: dict, progress: Optional[Callable[[str], None]] = None,
             persist=None, user_id: int = 0, label: str = "", mode: str = "BACKTEST") -> dict:
    """Run one configuration end to end and (optionally) store it."""
    say = progress or (lambda _m: None)
    started = time.time()
    cfg = EN.config_from(payload)
    if not cfg.start or not cfg.end:
        cov = DATA.coverage(cfg.underlying)
        span = cov.get("options") or cov.get("spot") or {}
        cfg.start = cfg.start or span.get("first", "")
        cfg.end = cfg.end or span.get("last", "")
    if not cfg.start or not cfg.end:
        return {"status": "error", "message": "no stored data for this underlying yet"}

    say("loading data")
    bundle = DATA.load(cfg.underlying, cfg.start, cfg.end)
    quality = DATA.check(bundle, cfg.underlying)
    if not quality["ok"]:
        return {"status": "error", "message": quality["problems"][0]["text"],
                "quality": quality, "config": cfg.as_dict()}

    say("running the engine")
    result = EN.run(bundle["bars"], bundle["options"], cfg,
                    vix=bundle.get("vix"), futures=bundle.get("futures"), progress=say)
    if result.get("status") != "ok":
        return {**result, "quality": quality, "config": cfg.as_dict()}

    say("measuring")
    stats = MX.summarise(result, cfg)
    # "quality" already holds the trade classification from summarise(); the data report is its
    # own key so one never silently replaces the other
    stats["data_quality"] = quality
    stats["config"] = cfg.as_dict()
    stats["config_hash"] = config_hash(cfg.as_dict())
    stats["engine_version"] = RU.ENGINE_VERSION
    stats["seconds"] = round(time.time() - started, 1)
    stats["mode"] = mode
    # a capped sample of blocked signals, so "why did nothing trade?" is answerable
    skipped = [s for s in result["signals"] if s.get("outcome") == "skipped"]
    stats["skipped_sample"] = skipped[:MAX_SKIP_SAMPLES]
    stats["skipped_total"] = len(skipped)
    stats["trades"] = result["trades"]

    if persist is not None:
        try:
            stats["run_id"] = save_run(persist, user_id, cfg, stats, result, label, mode)
        except Exception as exc:
            logger.error("could not store run: %s", exc)
            stats["store_error"] = str(exc)[:200]
    return stats


# ── persistence ──────────────────────────────────────────────────────
def save_run(db, user_id: int, cfg: EN.RunConfig, stats: dict, result: dict,
             label: str = "", mode: str = "BACKTEST") -> int:
    head = stats.get("headline") or {}
    strat = RU.from_config(cfg.strategy)
    run = FluxLabRun(
        user_id=user_id, label=label or strat.name, kind=mode.lower(),
        underlying=cfg.underlying, strategy_name=strat.name,
        start_date=pd.Timestamp(result.get("first_session") or cfg.start).date(),
        end_date=pd.Timestamp(result.get("last_session") or cfg.end).date(),
        timeframe=cfg.timeframe, config=cfg.as_dict(), config_hash=stats["config_hash"],
        engine_version=RU.ENGINE_VERSION, status="ok",
        sessions=int(result.get("sessions") or 0),
        signals_count=int(len(result.get("signals") or [])),
        trades_count=int(len(result.get("trades") or [])),
        seconds=float(stats.get("seconds") or 0),
        summary={k: v for k, v in stats.items() if k not in ("trades", "skipped_sample")},
    )
    db.add(run)
    db.flush()
    for t in result.get("trades") or []:
        db.add(_trade_row(run.id, user_id, t, mode))
    db.commit()
    logger.info("flux lab: stored run %s (%s trades)", run.id, run.trades_count)
    return run.id


def _trade_row(run_id: Optional[int], user_id: int, t: dict, mode: str) -> FluxLabTrade:
    return FluxLabTrade(
        run_id=run_id, user_id=user_id, mode=mode, trade_no=t.get("trade_id"),
        trade_date=pd.Timestamp(t["date"]).date(), signal_time=t.get("time"),
        entry_time=t.get("entry_time"), exit_time=t.get("exit_time"), side=t.get("side"),
        contract=t.get("contract"), strike=t.get("strike"),
        expiry=pd.Timestamp(t["expiry"]).date() if t.get("expiry") else None, dte=t.get("dte"),
        lots=t.get("lots"), qty=t.get("qty"),
        option_entry=t.get("option_entry"), option_exit=t.get("option_exit"),
        spot_entry=t.get("spot_entry"), spot_exit=t.get("spot_exit"),
        spot_move_pts=t.get("spot_move_pts"), spot_mfe_pts=t.get("spot_mfe_pts"),
        spot_mae_pts=t.get("spot_mae_pts"), option_mfe_pct=t.get("option_mfe_pct"),
        option_mae_pct=t.get("option_mae_pct"), gross_pts=t.get("gross_pts"),
        charges=t.get("charges"), pnl=t.get("pnl"), exit_reason=t.get("exit_reason"),
        held_min=t.get("held_min"), bucket=t.get("bucket"), dow=t.get("dow"),
        month=t.get("month"), year=t.get("year"), regime=t.get("regime") or {},
        reasons=t.get("reasons") or [], indicators=t.get("indicators") or {},
        ladder=t.get("ladder_time") or {}, status="CLOSED",
    )


def list_runs(db, user_id: int, limit: int = 50) -> list[dict]:
    rows = (db.query(FluxLabRun).filter(FluxLabRun.user_id == user_id)
              .order_by(FluxLabRun.created_at.desc()).limit(limit).all())
    return [{
        "id": r.id, "label": r.label, "kind": r.kind, "strategy": r.strategy_name,
        "underlying": r.underlying, "start": str(r.start_date), "end": str(r.end_date),
        "timeframe": r.timeframe, "sessions": r.sessions, "signals": r.signals_count,
        "trades": r.trades_count, "seconds": r.seconds, "hash": r.config_hash,
        "engine": r.engine_version, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
        "headline": (r.summary or {}).get("headline", {}),
    } for r in rows]


def get_run(db, user_id: int, run_id: int) -> Optional[dict]:
    r = db.query(FluxLabRun).filter(FluxLabRun.user_id == user_id, FluxLabRun.id == run_id).first()
    if r is None:
        return None
    out = dict(r.summary or {})
    out.update({"run_id": r.id, "label": r.label, "config": r.config, "config_hash": r.config_hash,
                "engine_version": r.engine_version, "created_at": r.created_at.isoformat(),
                "trades_count": r.trades_count, "signals_count": r.signals_count})
    return out


def trades_of(db, user_id: int, run_id: int, limit: int = 2000) -> list[dict]:
    rows = (db.query(FluxLabTrade)
              .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.run_id == run_id)
              .order_by(FluxLabTrade.trade_no).limit(limit).all())
    return [_trade_dict(t) for t in rows]


def _trade_dict(t: FluxLabTrade) -> dict:
    f = lambda v: None if v is None else float(v)   # noqa: E731
    return {
        "id": t.id, "trade_no": t.trade_no, "date": str(t.trade_date), "time": t.signal_time,
        "entry_time": t.entry_time, "exit_time": t.exit_time, "side": t.side,
        "contract": t.contract, "strike": f(t.strike), "expiry": str(t.expiry) if t.expiry else None,
        "dte": t.dte, "lots": t.lots, "qty": t.qty,
        "option_entry": f(t.option_entry), "option_exit": f(t.option_exit),
        "spot_entry": f(t.spot_entry), "spot_exit": f(t.spot_exit),
        "spot_move_pts": f(t.spot_move_pts), "spot_mfe_pts": f(t.spot_mfe_pts),
        "spot_mae_pts": f(t.spot_mae_pts), "option_mfe_pct": f(t.option_mfe_pct),
        "option_mae_pct": f(t.option_mae_pct), "gross_pts": f(t.gross_pts),
        "charges": f(t.charges), "pnl": f(t.pnl), "exit_reason": t.exit_reason,
        "held_min": t.held_min, "bucket": t.bucket, "dow": t.dow, "month": t.month,
        "year": t.year, "regime": t.regime, "reasons": t.reasons, "indicators": t.indicators,
        "ladder": t.ladder, "mode": t.mode, "status": t.status,
    }


def delete_run(db, user_id: int, run_id: int) -> bool:
    r = db.query(FluxLabRun).filter(FluxLabRun.user_id == user_id, FluxLabRun.id == run_id).first()
    if r is None:
        return False
    db.query(FluxLabTrade).filter(FluxLabTrade.run_id == run_id).delete()
    db.delete(r)
    db.commit()
    return True


# ── export ───────────────────────────────────────────────────────────
def export_csv(db, user_id: int, run_id: int, what: str = "trades") -> str:
    run = get_run(db, user_id, run_id)
    if run is None:
        return ""
    if what == "daily":
        rows = run.get("daily") or []
    elif what == "monthly":
        rows = run.get("by_month") or []
    elif what == "mfe":
        rows = [{"mfe_pts": s["mfe"], "mae_pts": s["mae"], "pnl": s["pnl"], "exit": s["reason"]}
                for s in ((run.get("mfe_mae") or {}).get("scatter") or [])]
    else:
        rows = [{k: v for k, v in t.items() if k not in ("reasons", "indicators", "regime", "ladder")}
                for t in trades_of(db, user_id, run_id, limit=100000)]
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


# ── background jobs ──────────────────────────────────────────────────
def start_job(kind: str, payload: dict, user_id: int, runner: Callable) -> dict:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "user_id": user_id, "status": "running",
           "progress": "starting", "started": time.time(), "result": None, "error": None}
    with _jobs_lock:
        _jobs[jid] = job
        for old in [k for k, v in _jobs.items()
                    if v["status"] != "running" and time.time() - v["started"] > 6 * 3600]:
            _jobs.pop(old, None)

    def run():
        from core.database import get_db_session
        db = get_db_session()
        try:
            job["result"] = runner(payload, lambda m: job.__setitem__("progress", m), db, user_id)
            job["status"] = "done"
        except Exception as exc:
            logger.error("flux lab %s failed: %s | %s", kind, exc, traceback.format_exc())
            job["status"] = "error"
            job["error"] = str(exc)[:400]
        finally:
            try:
                db.close()
            except Exception:
                pass
            job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=run, daemon=True, name=f"flux-{kind}-{jid}").start()
    return job


def get_job(job_id: str, user_id: int) -> Optional[dict]:
    j = _jobs.get(job_id)
    return j if j and j["user_id"] == user_id else None
