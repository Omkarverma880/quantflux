"""
Append-only research-log persistence for the Prev-Month-VWAP Equity Research.

Thin data-access over ``PMVwapEquityTrade``. Rows are only appended; each run
gets a ``run_id``. ``persist_new`` de-dupes so a live-day scan can be re-run
repeatedly and only genuinely new signals are added.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func

from core.logger import get_logger
from core.models import PMVwapEquityTrade

logger = get_logger("research.pmvwap_equity.log")


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def _mk(user_id, run_id, mode, r):
    return PMVwapEquityTrade(
        user_id=user_id, run_id=run_id, mode=mode,
        trade_date=date.fromisoformat(r["date"]), signal_time=r.get("time"),
        underlying=r.get("underlying"), entry_price=r.get("entry_price"),
        exit_price=r.get("exit_price"), mtm=r.get("mtm"), return_pct=r.get("return_pct"),
        status=r.get("status"), data=r,
    )


def persist_rows(db, user_id: int, run_id: str, mode: str, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        try:
            db.add(_mk(user_id, run_id, mode, r)); n += 1
        except Exception as exc:
            logger.warning("persist row failed (%s): %s", r.get("underlying"), exc)
    try:
        db.commit()
    except Exception as exc:
        db.rollback(); logger.error("persist commit failed: %s", exc); return 0
    return n


def persist_new(db, user_id: int, run_id: str, mode: str, rows: list[dict]) -> int:
    """Append only rows not already stored for this run (dedupe key = date +
    underlying + signal_time). Used by the live-day scan."""
    existing = {
        (t.trade_date.isoformat(), t.underlying, t.signal_time)
        for t in db.query(PMVwapEquityTrade.trade_date, PMVwapEquityTrade.underlying,
                          PMVwapEquityTrade.signal_time)
                   .filter(PMVwapEquityTrade.user_id == user_id,
                           PMVwapEquityTrade.run_id == run_id).all()
    }
    fresh = [r for r in rows if (r["date"], r["underlying"], r.get("time")) not in existing]
    persist_rows(db, user_id, run_id, mode, fresh)
    return fresh                    # return the newly-added rows (for alerts)


def list_runs(db, user_id: int, limit: int = 50) -> list[dict]:
    q = (db.query(
            PMVwapEquityTrade.run_id, PMVwapEquityTrade.mode,
            func.count(PMVwapEquityTrade.id).label("signals"),
            func.min(PMVwapEquityTrade.trade_date).label("start"),
            func.max(PMVwapEquityTrade.trade_date).label("end"),
            func.sum(PMVwapEquityTrade.mtm).label("total_mtm"),
            func.max(PMVwapEquityTrade.created_at).label("created_at"),
         )
         .filter(PMVwapEquityTrade.user_id == user_id)
         .group_by(PMVwapEquityTrade.run_id, PMVwapEquityTrade.mode)
         .order_by(func.max(PMVwapEquityTrade.created_at).desc()).limit(limit))
    return [{
        "run_id": r.run_id, "mode": r.mode, "signals": int(r.signals or 0),
        "start": r.start.isoformat() if r.start else None,
        "end": r.end.isoformat() if r.end else None,
        "total_mtm": float(r.total_mtm or 0),
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None,
    } for r in q.all()]


def fetch_rows(db, user_id: int, run_id: str | None = None, trade_date: str | None = None) -> list[dict]:
    q = db.query(PMVwapEquityTrade).filter(PMVwapEquityTrade.user_id == user_id)
    if run_id:
        q = q.filter(PMVwapEquityTrade.run_id == run_id)
    if trade_date:
        try:
            q = q.filter(PMVwapEquityTrade.trade_date == date.fromisoformat(trade_date))
        except ValueError:
            pass
    q = q.order_by(PMVwapEquityTrade.trade_date, PMVwapEquityTrade.signal_time)
    return [r.data for r in q.all()]
