"""
API routes for the Data Ingestion Lab — upload, check and explore index/option history.

Everything lands in the shared Market Store through its existing ingest path, so every
consumer (OI Lab, Options Lab, backtests) sees the new data. Read-only for the market.
"""
from __future__ import annotations

import inspect
import math
import traceback
from functools import wraps

import numpy as np
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core.auth import login_required
from core.logger import get_logger
from research.data_ingestion import service as SV

router = APIRouter()
logger = get_logger("api.data_ingestion")

MAX_UPLOAD_MB = 1024
_CHUNK = 1 << 20


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


def _fail(name: str, exc: Exception) -> dict:
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return {"status": "error", "message": str(exc)[:400]}
    logger.error("%s failed: %s | %s", name, exc, traceback.format_exc())
    return {"status": "error", "message": f"{name}: {type(exc).__name__} — {exc}"[:400]}


def safe(name: str):
    """Error envelope. Heavy handlers are plain ``def`` so FastAPI runs them in its thread pool
    instead of blocking the event loop; only the streaming upload is ``async``."""
    def deco(fn):
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def awrapper(*a, **k):
                try:
                    out = await fn(*a, **k)
                    return json_safe(out) if isinstance(out, (dict, list)) else out
                except Exception as exc:
                    return _fail(name, exc)
            return awrapper

        @wraps(fn)
        def wrapper(*a, **k):
            try:
                out = fn(*a, **k)
                return json_safe(out) if isinstance(out, (dict, list)) else out
            except Exception as exc:
                return _fail(name, exc)
        return wrapper
    return deco


class JobReq(BaseModel):
    stage_id: str
    kind: str
    underlying: str
    mapping: dict


class DeleteReq(BaseModel):
    kind: str
    underlying: str
    year: int
    month: int
    confirm: str


@router.post("/stage")
@safe("stage")
async def stage(files: list[UploadFile] = File(...), user_id: int = Depends(login_required)):
    """Stream each file to disk (no size held in memory), then preview it."""
    out = []
    for f in files:
        try:
            sid, dest = SV.new_stage(f.filename, user_id)
        except ValueError as exc:
            out.append({"status": "error", "filename": f.filename, "message": str(exc)})
            continue
        size = 0
        too_big = False
        with open(dest, "wb") as fh:
            while True:
                block = await f.read(_CHUNK)
                if not block:
                    break
                size += len(block)
                if size > MAX_UPLOAD_MB * _CHUNK:
                    too_big = True
                    break
                fh.write(block)
        await f.close()
        if too_big:
            SV.drop_stage(sid)
            out.append({"status": "error", "filename": f.filename,
                        "message": f"file exceeds {MAX_UPLOAD_MB} MB — split it by month"})
            continue
        try:
            out.append(await run_in_threadpool(SV.finish_stage, sid))
        except Exception as exc:
            SV.drop_stage(sid)
            out.append({"status": "error", "filename": f.filename, "message": f"could not read the file: {exc}"[:400]})
    return {"status": "ok", "files": out}


@router.get("/stages")
@safe("stages")
def stages(user_id: int = Depends(login_required)):
    return {"status": "ok", "stages": SV.list_stages(user_id)}


@router.get("/stage/{stage_id}")
@safe("preview")
def preview(stage_id: str, user_id: int = Depends(login_required)):
    return SV.preview(stage_id)


@router.delete("/stage/{stage_id}")
@safe("drop_stage")
def drop(stage_id: str, user_id: int = Depends(login_required)):
    SV._read_meta(stage_id)
    SV.drop_stage(stage_id)
    return {"status": "ok"}


@router.post("/validate")
@safe("validate")
def validate(req: JobReq, user_id: int = Depends(login_required)):
    return {"status": "ok", "job": SV.start_job("validate", req.stage_id, req.kind, req.underlying, req.mapping, user_id)}


@router.post("/commit")
@safe("commit")
def commit(req: JobReq, user_id: int = Depends(login_required)):
    return {"status": "ok", "job": SV.start_job("commit", req.stage_id, req.kind, req.underlying, req.mapping, user_id)}


@router.get("/job/{job_id}")
@safe("job")
def job(job_id: str, user_id: int = Depends(login_required)):
    j = SV.get_job(job_id, user_id)
    if j is None:
        return {"status": "error", "message": "job not found"}
    return {"status": "ok", "job": {k: v for k, v in j.items() if k != "user_id"}}


@router.get("/coverage")
@safe("coverage")
def coverage(user_id: int = Depends(login_required)):
    return SV.coverage()


@router.post("/partition/delete")
@safe("delete_partition")
def delete_partition(req: DeleteReq, user_id: int = Depends(login_required)):
    want = f"{req.underlying.upper()} {req.year}-{req.month:02d}"
    if req.confirm.strip().upper() != want:
        return {"status": "error", "message": f'Type "{want}" to confirm.'}
    return SV.delete_partition(req.kind, req.underlying, req.year, req.month)


@router.get("/explore/spot")
@safe("explore_spot")
def explore_spot(underlying: str, start: str, end: str | None = None, minutes: int = 1,
                       user_id: int = Depends(login_required)):
    return SV.explore_spot(underlying, start, end, minutes)


@router.get("/explore/contracts")
@safe("explore_contracts")
def explore_contracts(underlying: str, day: str, user_id: int = Depends(login_required)):
    return SV.explore_contracts(underlying, day)


@router.get("/explore/option")
@safe("explore_option")
def explore_option(underlying: str, contract: str, start: str, end: str | None = None, minutes: int = 1,
                         user_id: int = Depends(login_required)):
    return SV.explore_option(underlying, contract, start, end, minutes)


@router.get("/explore/chain")
@safe("explore_chain")
def explore_chain(underlying: str, day: str, at: str = "10:00", expiry: str | None = None,
                        user_id: int = Depends(login_required)):
    return SV.explore_chain(underlying, day, at, expiry)


@router.get("/downloader")
@safe("downloader")
def downloader(user_id: int = Depends(login_required)):
    return {"status": "ok", "datasets": SV.downloader_datasets(user_id)}


@router.post("/downloader/{dataset_id}/stage")
@safe("downloader_stage")
def downloader_stage(dataset_id: int, user_id: int = Depends(login_required)):
    return {"status": "ok", "files": [SV.stage_from_downloader(dataset_id, user_id)]}
