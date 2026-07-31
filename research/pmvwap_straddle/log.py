"""
Append-only research-log persistence for the Prev-Month-VWAP Straddle Research.

Thin data-access layer over ``PMVwapStraddleTrade``. Rows are only ever
appended (never overwritten); each backtest / live scan gets a ``run_id`` so
runs can be listed, fetched and compared side-by-side.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import func

from core.logger import get_logger
from core.models import PMVwapStraddleTrade

logger = get_logger("research.pmvwap_straddle.log")


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def persist_rows(db, user_id: int, run_id: str, mode: str, rows: list[dict]) -> int:
    """Append research-log rows. Returns the number stored."""
    n = 0
    for r in rows:
        try:
            db.add(PMVwapStraddleTrade(
                user_id=user_id, run_id=run_id, mode=mode,
                trade_date=date.fromisoformat(r["date"]),
                signal_time=r.get("time"), underlying=r.get("underlying"),
                atm_strike=r.get("atm_strike"), ce_symbol=r.get("ce_symbol"),
                pe_symbol=r.get("pe_symbol"), lot_size=r.get("lot_size"),
                combined_premium=r.get("combined_premium"),
                target_premium=r.get("target_premium"),
                combined_mtm=r.get("combined_mtm"), targets_hit=r.get("targets_hit", 0),
                status=r.get("status"), data=r,
            ))
            n += 1
        except Exception as exc:
            logger.warning("persist row failed (%s): %s", r.get("underlying"), exc)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("persist commit failed: %s", exc)
        return 0
    return n


def list_runs(db, user_id: int, limit: int = 50) -> list[dict]:
    q = (db.query(
            PMVwapStraddleTrade.run_id, PMVwapStraddleTrade.mode,
            func.count(PMVwapStraddleTrade.id).label("signals"),
            func.min(PMVwapStraddleTrade.trade_date).label("start"),
            func.max(PMVwapStraddleTrade.trade_date).label("end"),
            func.sum(PMVwapStraddleTrade.combined_mtm).label("total_mtm"),
            func.max(PMVwapStraddleTrade.created_at).label("created_at"),
         )
         .filter(PMVwapStraddleTrade.user_id == user_id)
         .group_by(PMVwapStraddleTrade.run_id, PMVwapStraddleTrade.mode)
         .order_by(func.max(PMVwapStraddleTrade.created_at).desc())
         .limit(limit))
    out = []
    for row in q.all():
        out.append({
            "run_id": row.run_id, "mode": row.mode, "signals": int(row.signals or 0),
            "start": row.start.isoformat() if row.start else None,
            "end": row.end.isoformat() if row.end else None,
            "total_mtm": float(row.total_mtm or 0),
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else None,
        })
    return out


def fetch_rows(db, user_id: int, run_id: str | None = None,
               trade_date: str | None = None) -> list[dict]:
    q = db.query(PMVwapStraddleTrade).filter(PMVwapStraddleTrade.user_id == user_id)
    if run_id:
        q = q.filter(PMVwapStraddleTrade.run_id == run_id)
    if trade_date:
        try:
            q = q.filter(PMVwapStraddleTrade.trade_date == date.fromisoformat(trade_date))
        except ValueError:
            pass
    q = q.order_by(PMVwapStraddleTrade.trade_date, PMVwapStraddleTrade.signal_time)
    return [r.data for r in q.all()]
