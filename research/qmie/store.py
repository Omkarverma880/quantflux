"""
QMIE snapshot persistence (§9, §A.7) — append-only, reproducible.

Stores each ranked scan as an immutable row so a published result can be
retrieved unchanged later (with its as-of time and config/ruleset versions).
Never mutates a prior snapshot. Research only — no orders.
"""
from __future__ import annotations

from core.logger import get_logger
from core.models import QMIESnapshot

logger = get_logger("research.qmie.store")


def save_snapshot(db, user_id: int, snap: dict) -> str | None:
    """Persist a scan snapshot; returns its snapshot_id (or None on failure)."""
    if not snap or snap.get("status") != "ok":
        return None
    try:
        row = QMIESnapshot(
            user_id=user_id, snapshot_id=snap.get("snapshot_id"),
            as_of=snap.get("as_of"), horizon=snap.get("horizon"),
            config_version=(snap.get("config") or {}).get("config_version"),
            ruleset_version=snap.get("ruleset_version"), benchmark=snap.get("benchmark"),
            counts=snap.get("counts") or {}, config=snap.get("config") or {},
            results=snap.get("results") or [], restricted=snap.get("restricted") or [],
            market_context=snap.get("market_context") or {})
        db.add(row)
        db.commit()
        return snap.get("snapshot_id")
    except Exception as exc:
        db.rollback()
        logger.error("qmie snapshot save failed: %s", exc)
        return None


def list_snapshots(db, user_id: int, limit: int = 50) -> list[dict]:
    q = (db.query(QMIESnapshot)
           .filter(QMIESnapshot.user_id == user_id)
           .order_by(QMIESnapshot.created_at.desc()).limit(limit))
    out = []
    for r in q.all():
        out.append({
            "snapshot_id": r.snapshot_id, "as_of": r.as_of, "horizon": r.horizon,
            "config_version": r.config_version, "ruleset_version": r.ruleset_version,
            "benchmark": r.benchmark, "counts": r.counts or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out


def get_snapshot(db, user_id: int, snapshot_id: str) -> dict | None:
    r = (db.query(QMIESnapshot)
           .filter(QMIESnapshot.user_id == user_id,
                   QMIESnapshot.snapshot_id == snapshot_id)
           .order_by(QMIESnapshot.created_at.desc()).first())
    if not r:
        return None
    return {
        "status": "ok", "snapshot_id": r.snapshot_id, "as_of": r.as_of,
        "as_of_display": (r.as_of or "").replace("T", " ")[:19],
        "horizon": r.horizon, "config": r.config or {}, "counts": r.counts or {},
        "ruleset_version": r.ruleset_version, "benchmark": r.benchmark,
        "results": r.results or [], "restricted": r.restricted or [],
        "market_context": r.market_context or {}, "stored": True,
        "disclaimer": "QMIE research only — no order is created, transmitted, or executed.",
    }
