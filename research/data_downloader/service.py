"""
Data Downloader service — validation, job orchestration, dataset catalog.

Reuses the existing Broker (read-only), settings/DATA_DIR, PostgreSQL session
and logging. Never places orders. Long downloads run in a background thread via
``jobs.manager`` and are polled by the frontend.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from core.models import DataDataset
from research.data_downloader import storage
from research.data_downloader.chunker import build_chunks
from research.data_downloader.config import load_config, save_config
from research.data_downloader.instruments import InstrumentIndex
from research.data_downloader.jobs import manager
from research.data_downloader.constants import (
    INTERVALS, INSTRUMENT_TYPES, FORMATS, STATUS_QUEUED,
    STATUS_RUNNING, STATUS_PARTIAL, STATUS_FAILED,
)

logger = get_logger("research.data_downloader")


def _to_dict(ds: DataDataset, sample=None) -> dict:
    d = {
        "id": ds.id, "symbol": ds.symbol, "exchange": ds.exchange, "segment": ds.segment,
        "instrument_type": ds.instrument_type, "instrument_token": ds.instrument_token,
        "expiry": ds.expiry, "strike": float(ds.strike) if ds.strike is not None else None,
        "option_type": ds.option_type, "interval": ds.interval,
        "from_date": ds.from_date.isoformat() if ds.from_date else None,
        "to_date": ds.to_date.isoformat() if ds.to_date else None,
        "timezone": ds.timezone, "include_oi": ds.include_oi, "fmt": ds.fmt,
        "status": ds.status, "progress": ds.progress or 0, "rows": ds.rows or 0,
        "chunks_total": ds.chunks_total or 0, "chunks_completed": ds.chunks_completed or 0,
        "chunks": ds.chunks or [], "error": ds.error,
        "file_format": ds.file_format, "checksum": ds.checksum, "size_bytes": ds.size_bytes,
        "quality": ds.quality or {}, "has_file": bool(ds.file_path),
        "created_at": ds.created_at.isoformat() if ds.created_at else None,
        "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
    }
    if sample is not None:
        d["sample"] = sample
    return d


class DataDownloader:
    def __init__(self, broker: Broker, user_id: int):
        self.broker = broker
        self.user_id = user_id
        self.index = InstrumentIndex(broker)

    # ── config ──
    def load_config(self) -> dict:
        return load_config()

    def save_config(self, partial: dict) -> dict:
        return save_config(partial)

    # ── instrument selectors (read-only) ──
    def search(self, q, itype=None, exchange=None, limit=25):
        return self.index.search(q, itype, exchange, limit)

    def expiries(self, name, kind="all"):
        return self.index.expiries(name, kind)

    def futures(self, name):
        return self.index.futures(name)

    def strikes(self, name, expiry, option_type=None):
        return self.index.strikes(name, expiry, option_type)

    # ── download ──
    def start_download(self, db, spec: dict) -> dict:
        interval = spec.get("interval")
        if interval not in INTERVALS:
            return {"status": "error", "message": f"Unsupported interval: {interval}"}
        itype = spec.get("instrument_type")
        if itype not in INSTRUMENT_TYPES:
            return {"status": "error", "message": f"Unsupported instrument type: {itype}"}
        try:
            token = int(spec["instrument_token"])
        except (KeyError, TypeError, ValueError):
            return {"status": "error", "message": "Missing/invalid instrument token"}
        try:
            frm = date.fromisoformat(spec["from_date"])
            to = date.fromisoformat(spec["to_date"])
        except Exception:
            return {"status": "error", "message": "Invalid date range (use YYYY-MM-DD)"}
        if to < frm:
            return {"status": "error", "message": "To date is before From date"}
        if to > date.today():
            to = date.today()
        fmt = spec.get("fmt") if spec.get("fmt") in FORMATS else self.load_config()["default_format"]

        chunks = build_chunks(frm, to, INTERVALS[interval])
        if not chunks:
            return {"status": "error", "message": "Empty date range"}

        ds = DataDataset(
            user_id=self.user_id, symbol=spec.get("symbol"), exchange=spec.get("exchange"),
            segment=spec.get("segment"), instrument_type=itype, instrument_token=token,
            expiry=spec.get("expiry"), strike=spec.get("strike"), option_type=spec.get("option_type"),
            interval=interval, from_date=frm, to_date=to,
            timezone=spec.get("timezone") or "Asia/Kolkata",
            include_oi=bool(spec.get("include_oi", True)), fmt=fmt,
            normalize=bool(spec.get("normalize", True)),
            status=STATUS_QUEUED, progress=0, rows=0,
            chunks_total=len(chunks), chunks_completed=0, chunks=chunks,
        )
        db.add(ds)
        db.commit()
        db.refresh(ds)
        cfg = self.load_config()
        manager.start(ds.id, self.broker, cfg)
        return {"status": "ok", "job_id": ds.id, "dataset": _to_dict(ds)}

    def job_status(self, db, ds_id: int) -> dict:
        ds = self._get(db, ds_id)
        if not ds:
            return {"status": "error", "message": "Job not found"}
        d = _to_dict(ds)
        d["running"] = manager.is_running(ds_id)
        return {"status": "ok", **d}

    def resume(self, db, ds_id: int) -> dict:
        ds = self._get(db, ds_id)
        if not ds:
            return {"status": "error", "message": "Dataset not found"}
        if manager.is_running(ds_id):
            return {"status": "error", "message": "Download already running"}
        if ds.status not in (STATUS_PARTIAL, STATUS_FAILED):
            return {"status": "error", "message": f"Nothing to resume (status: {ds.status})"}
        chunks = list(ds.chunks or [])
        for c in chunks:                       # re-queue failed/pending chunks
            if c.get("status") != "completed":
                c["status"] = "pending"
                c.pop("error", None)
        ds.chunks = chunks
        ds.error = None
        ds.status = STATUS_RUNNING
        db.commit()
        manager.start(ds_id, self.broker, self.load_config())
        return {"status": "ok", "job_id": ds_id}

    def cancel(self, db, ds_id: int) -> dict:
        manager.cancel(ds_id)
        return {"status": "ok"}

    # ── catalog ──
    def list_datasets(self, db) -> list[dict]:
        q = (db.query(DataDataset).filter(DataDataset.user_id == self.user_id)
               .order_by(DataDataset.created_at.desc()).limit(200))
        return [_to_dict(d) for d in q.all()]

    def get_dataset(self, db, ds_id: int, with_sample=True) -> dict:
        ds = self._get(db, ds_id)
        if not ds:
            return {"status": "error", "message": "Dataset not found"}
        sample = None
        if with_sample and ds.file_path:
            sample = storage.read_sample(ds.file_path, self.load_config()["max_rows_view"])
        return {"status": "ok", **_to_dict(ds, sample=sample)}

    def delete_dataset(self, db, ds_id: int) -> dict:
        ds = self._get(db, ds_id)
        if not ds:
            return {"status": "error", "message": "Dataset not found"}
        manager.cancel(ds_id)
        storage.delete_files(ds.file_path, ds_id)
        db.delete(ds)
        db.commit()
        return {"status": "ok"}

    def file_path(self, db, ds_id: int, fmt: str = "native") -> Optional[str]:
        ds = self._get(db, ds_id)
        if not ds or not ds.file_path:
            return None
        return storage.to_csv_path(ds.file_path) if fmt == "csv" else ds.file_path

    def _get(self, db, ds_id: int) -> Optional[DataDataset]:
        return (db.query(DataDataset)
                  .filter(DataDataset.id == ds_id, DataDataset.user_id == self.user_id).first())
