"""
API routes for the Market Store — upload and inspect historical spot/option data.

Uploads are merged, never overwritten: re-uploading overlapping files adds only
the rows that were not already stored.
"""
from __future__ import annotations

import math
import traceback
from functools import wraps

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.logger import get_logger
from research.market_store import service as SV
from research.market_store import store as MS

router = APIRouter()
logger = get_logger("api.market_store")

MAX_UPLOAD_MB = 250


def json_safe(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    return o


def safe(name: str):
    def deco(fn):
        @wraps(fn)
        async def wrapper(*a, **k):
            try:
                out = await fn(*a, **k)
                return json_safe(out) if isinstance(out, (dict, list)) else out
            except Exception as exc:
                logger.error("%s failed: %s | %s", name, exc, traceback.format_exc())
                return {"status": "error", "message": f"{name}: {type(exc).__name__} — {exc}"[:400]}
        return wrapper
    return deco


@router.get("/summary")
@safe("summary")
async def summary(user_id: int = Depends(login_required)):
    return {"status": "ok", "store": MS.summary(), "root": str(MS.ROOT)}


@router.get("/partitions")
@safe("partitions")
async def partitions(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from core.models import MarketStorePartition as P
    rows = db.query(P).order_by(P.kind, P.underlying, P.year, P.month).all()
    return {"status": "ok", "partitions": [
        {"kind": r.kind, "underlying": r.underlying, "month": f"{r.year}-{r.month:02d}",
         "rows": r.rows, "bytes": r.bytes, "sessions": r.sessions, "contracts": r.contracts,
         "first": r.first_ts.isoformat() if r.first_ts else None,
         "last": r.last_ts.isoformat() if r.last_ts else None} for r in rows]}


@router.get("/ingests")
@safe("ingests")
async def ingests(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    from core.models import MarketStoreIngest as I
    rows = db.query(I).filter(I.user_id == user_id).order_by(I.id.desc()).limit(100).all()
    return {"status": "ok", "ingests": [
        {"id": r.id, "at": r.created_at.isoformat() if r.created_at else None,
         "filename": r.filename, "kind": r.kind, "status": r.status,
         "rows_in": r.rows_in, "rows_added": r.rows_added, "report": r.report,
         "error": r.error} for r in rows]}


@router.post("/upload")
@safe("upload")
async def upload(files: list[UploadFile] = File(...), underlying: str = "NIFTY",
                 user_id: int = Depends(login_required)):
    """Merge one or more spot/option files. Kind is detected from the columns."""
    results = []
    for f in files:
        raw = await f.read()
        await f.close()
        if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
            results.append({"status": "error", "filename": f.filename,
                            "message": f"file exceeds {MAX_UPLOAD_MB} MB — split it by month"})
            continue
        try:
            df = SV.read_upload(f.filename, raw)
        except Exception as exc:
            results.append({"status": "error", "filename": f.filename, "message": str(exc)})
            continue
        del raw
        results.append(SV.ingest_frame(df, filename=f.filename, underlying=underlying,
                                       user_id=user_id))
    return {"status": "ok", "results": results, "store": MS.summary()}


@router.post("/rebuild-catalog")
@safe("rebuild_catalog")
async def rebuild(user_id: int = Depends(login_required)):
    return {"status": "ok", "partitions": SV.rebuild_catalog()}
