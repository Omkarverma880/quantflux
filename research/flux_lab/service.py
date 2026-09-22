"""
Flux Lab — background jobs and stored runs for the failed-breakout iron fly.

Uses the existing ``flux_lab_runs`` / ``flux_lab_trades`` tables unchanged. An iron fly is one row:
``side = "IF"``, ``option_entry`` = credit and ``option_exit`` = debit to close (premium points,
per unit), ``pnl`` = rupees after charges, and the four legs with their fills in ``indicators``.
Rows written by the previous Flux Lab engine are left in the tables but never shown.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
import uuid
from datetime import date
from typing import Callable, Optional

from core.logger import get_logger
from core.models import FluxLabRun, FluxLabTrade
from research.flux_lab import backtest as BT
from research.flux_lab import strategy as ST

logger = get_logger("research.flux_lab.service")


def clean(o):
    """NumPy scalars → plain Python, everywhere, before anything reaches the database.

    psycopg2 renders a NumPy scalar as ``np.float64(1.5)``, which PostgreSQL rejects, and
    ``json.dumps`` refuses them outright in a JSONB column. SQLite accepts them silently, so this
    has to be enforced here rather than discovered in production.
    """
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if hasattr(o, "item") and hasattr(o, "dtype"):      # any NumPy scalar
        return o.item()
    return o


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def start_job(kind: str, payload: dict, user_id: int, runner: Callable) -> dict:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "user_id": user_id, "status": "running",
           "progress": "starting", "started": time.time(), "result": None, "error": None}
    with _jobs_lock:
        _jobs[jid] = job
        for old in [k for k, v in _jobs.items()
                    if v["status"] != "running" and time.time() - v["started"] > 6 * 3600]:
            _jobs.pop(old, None)

    def work():
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

    threading.Thread(target=work, daemon=True, name=f"flux-{kind}-{jid}").start()
    return job


def get_job(job_id: str, user_id: int) -> Optional[dict]:
    j = _jobs.get(job_id)
    return j if j and j["user_id"] == user_id else None


# ── runs ─────────────────────────────────────────────────────────────
def run_backtest(db, user_id: int, start: str, end: str, lots: int, label: str = "",
                 progress: Optional[Callable[[str], None]] = None, store: bool = True) -> dict:
    t0 = time.time()
    res = BT.run(start, end, lots, progress)
    cfg = {"rule": ST.RULE.as_dict(), "start": start, "end": end, "lots": lots,
           "slippage_pts": BT.SLIPPAGE, "lot_size": BT.LOT}
    res["seconds"] = round(time.time() - t0, 1)
    if store and db is not None:
        res["run_id"] = save_run(db, user_id, cfg, res, label)
    return res


def save_run(db, user_id: int, cfg: dict, res: dict, label: str = "") -> int:
    s = clean(res["summary"])
    cfg = clean(cfg)
    run = FluxLabRun(
        user_id=user_id, label=label or None, kind="backtest", underlying="NIFTY",
        strategy_name=ST.STRATEGY_NAME, start_date=date.fromisoformat(res["start"]),
        end_date=date.fromisoformat(res["end"]), timeframe=1, config=cfg,
        config_hash=hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16],
        engine_version=ST.ENGINE_VERSION, status="ok", sessions=s.get("sessions", 0),
        signals_count=s.get("trades", 0) + s.get("skipped", 0), trades_count=s.get("trades", 0),
        seconds=res.get("seconds", 0), summary=s)
    db.add(run)
    db.flush()
    for n, t in enumerate(res["trades"], 1):
        db.add(trade_row(t, user_id, "BACKTEST", run_id=run.id, trade_no=n))
    db.commit()
    return run.id


def trade_row(t: dict, user_id: int, mode: str, run_id: Optional[int] = None,
              trade_no: Optional[int] = None, status: str = "CLOSED") -> FluxLabTrade:
    t = clean(t)
    d = date.fromisoformat(t["date"])
    return FluxLabTrade(
        run_id=run_id, user_id=user_id, mode=mode, trade_no=trade_no, trade_date=d,
        signal_time=t.get("signal_time"), entry_time=t.get("entry_time"), exit_time=t.get("exit_time"),
        side="IF", contract=f"NIFTY {t['atm']:g} iron fly ±{ST.RULE.wing_pts}", strike=t["atm"],
        expiry=date.fromisoformat(t["expiry"]) if t.get("expiry") else None, dte=t.get("dte"),
        lots=t.get("lots"), qty=t.get("qty"), option_entry=t.get("credit"), option_exit=t.get("debit"),
        spot_entry=t.get("spot_entry"), spot_exit=t.get("spot_exit"),
        spot_move_pts=(round(t["spot_exit"] - t["spot_entry"], 2)
                       if t.get("spot_exit") is not None and t.get("spot_entry") is not None else None),
        gross_pts=t.get("gross_pts"), charges=t.get("charges"), pnl=t.get("pnl"),
        exit_reason=t.get("exit_reason"), held_min=t.get("held_min"),
        dow=d.weekday(), month=t["date"][:7], year=d.year,
        reasons=[e["time"] + " " + e["text"] for e in t.get("events", [])],
        indicators={"legs": t.get("legs", []), "signal": t.get("signal", {})},
        regime={}, ladder={}, status=status)


def trade_dict(t: FluxLabTrade) -> dict:
    ind = t.indicators or {}
    f = lambda v: float(v) if v is not None else None
    return {
        "id": t.id, "run_id": t.run_id, "mode": t.mode, "trade_no": t.trade_no,
        "date": str(t.trade_date), "signal_time": t.signal_time, "entry_time": t.entry_time,
        "exit_time": t.exit_time, "contract": t.contract, "atm": f(t.strike),
        "expiry": str(t.expiry) if t.expiry else None, "dte": t.dte, "lots": t.lots, "qty": t.qty,
        "credit": f(t.option_entry), "debit": f(t.option_exit), "spot_entry": f(t.spot_entry),
        "spot_exit": f(t.spot_exit), "gross_pts": f(t.gross_pts), "charges": f(t.charges),
        "pnl": f(t.pnl), "exit_reason": t.exit_reason, "held_min": t.held_min, "status": t.status,
        "legs": ind.get("legs", []), "signal": ind.get("signal", {}), "events": t.reasons or [],
    }


def _ours(q):
    return q.filter(FluxLabRun.engine_version == ST.ENGINE_VERSION)


def list_runs(db, user_id: int, limit: int = 50) -> list[dict]:
    rows = (_ours(db.query(FluxLabRun).filter(FluxLabRun.user_id == user_id))
            .order_by(FluxLabRun.id.desc()).limit(limit).all())
    return [run_dict(r, brief=True) for r in rows]


def run_dict(r: FluxLabRun, brief: bool = False) -> dict:
    s = r.summary or {}
    out = {"id": r.id, "created": r.created_at.isoformat() if r.created_at else None, "label": r.label,
           "start": str(r.start_date), "end": str(r.end_date), "lots": (r.config or {}).get("lots"),
           "trades": r.trades_count, "net": s.get("net"), "win_rate": s.get("win_rate"),
           "green_months": s.get("green_months"), "months": s.get("months"), "seconds": r.seconds}
    if not brief:
        out.update(summary=s, config=r.config)
    return out


def get_run(db, user_id: int, run_id: int) -> Optional[dict]:
    r = _ours(db.query(FluxLabRun).filter(FluxLabRun.user_id == user_id, FluxLabRun.id == run_id)).first()
    return run_dict(r) if r else None


def trades_of(db, user_id: int, run_id: int, limit: int = 5000) -> list[dict]:
    rows = (db.query(FluxLabTrade)
              .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.run_id == run_id, FluxLabTrade.side == "IF")
              .order_by(FluxLabTrade.trade_no).limit(limit).all())
    return [trade_dict(t) for t in rows]


def delete_run(db, user_id: int, run_id: int) -> bool:
    r = _ours(db.query(FluxLabRun).filter(FluxLabRun.user_id == user_id, FluxLabRun.id == run_id)).first()
    if not r:
        return False
    db.query(FluxLabTrade).filter(FluxLabTrade.run_id == run_id).delete(synchronize_session=False)
    db.delete(r)
    db.commit()
    return True


def latest_run(db, user_id: int) -> Optional[dict]:
    r = (_ours(db.query(FluxLabRun).filter(FluxLabRun.user_id == user_id))
         .order_by(FluxLabRun.id.desc()).first())
    return run_dict(r) if r else None
