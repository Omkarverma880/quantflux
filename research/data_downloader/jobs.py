"""
Background download-job manager for the Data Downloader.

In-process daemon threads (same pattern the trading engine already uses) — no
new task infrastructure. Each job:
  • fetches chunk-by-chunk via the EXISTING read-only Broker (never orders),
  • rate-limits (< Kite's 3 req/s) and retries each chunk with exponential
    backoff — a single failed chunk does NOT restart the whole download,
  • persists each completed chunk as a part file so a resume skips finished work,
  • updates the DataDataset row (status / progress / per-chunk state) as it goes,
  • supports graceful cancellation.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from core.database import get_db_session
from core.logger import get_logger
from core.models import DataDataset
from research.data_downloader import normalize, quality, storage
from research.data_downloader.constants import (
    INTERVALS, STATUS_RUNNING, STATUS_COMPLETED, STATUS_PARTIAL,
    STATUS_FAILED, STATUS_CANCELLED,
)

logger = get_logger("research.data_downloader.jobs")


class DownloadJobManager:
    def __init__(self):
        self._threads: dict[int, threading.Thread] = {}
        self._cancel: dict[int, threading.Event] = {}
        self._lock = threading.Lock()

    def is_running(self, ds_id: int) -> bool:
        t = self._threads.get(ds_id)
        return bool(t and t.is_alive())

    def start(self, ds_id: int, broker, cfg: dict) -> bool:
        with self._lock:
            if self.is_running(ds_id):
                return False
            ev = threading.Event()
            self._cancel[ds_id] = ev
            t = threading.Thread(target=self._run, args=(ds_id, broker, cfg, ev),
                                 name=f"dl-{ds_id}", daemon=True)
            self._threads[ds_id] = t
            t.start()
            return True

    def cancel(self, ds_id: int) -> bool:
        ev = self._cancel.get(ds_id)
        if ev:
            ev.set()
            return True
        return False

    # ── worker ──
    def _run(self, ds_id: int, broker, cfg: dict, ev: threading.Event):
        db = get_db_session()
        ds = None
        try:
            ds = db.query(DataDataset).filter(DataDataset.id == ds_id).first()
            if not ds:
                return
            kite_interval = INTERVALS.get(ds.interval, "day")
            meta = {"symbol": ds.symbol, "exchange": ds.exchange,
                    "instrument_token": int(ds.instrument_token), "instrument_type": ds.instrument_type,
                    "expiry": ds.expiry, "strike": float(ds.strike) if ds.strike is not None else None,
                    "option_type": ds.option_type, "interval_label": ds.interval}
            chunks = list(ds.chunks or [])
            ds.status = STATUS_RUNNING
            ds.error = None
            db.commit()

            for idx, ch in enumerate(chunks):
                if ev.is_set():
                    ds.status = STATUS_CANCELLED
                    db.commit()
                    return
                if ch.get("status") == "completed" and storage.has_part(ds_id, idx):
                    continue
                ok, payload = self._fetch_chunk(broker, meta, kite_interval, ch, bool(ds.include_oi), cfg, ev)
                if ev.is_set():
                    ds.status = STATUS_CANCELLED
                    db.commit()
                    return
                if ok:
                    n = storage.write_part(payload, ds_id, idx)
                    ch["status"] = "completed"
                    ch["rows"] = n
                    ch.pop("error", None)
                else:
                    ch["status"] = "failed"
                    ch["error"] = str(payload)[:200]
                comp = sum(1 for c in chunks if c.get("status") == "completed")
                ds.chunks = list(chunks)             # reassign so JSONB change is tracked
                ds.chunks_completed = comp
                ds.rows = sum(c.get("rows", 0) for c in chunks)
                ds.progress = int(comp / max(1, len(chunks)) * 100)
                db.commit()

            failed = [c for c in chunks if c.get("status") != "completed"]
            if failed:
                ds.status = STATUS_PARTIAL
                ds.error = f"{len(failed)} of {len(chunks)} chunk(s) failed — Resume to retry them"
                db.commit()
                return

            info = storage.finalize(ds_id, ds.instrument_type, ds.symbol, ds.interval,
                                    ds.from_date.isoformat(), ds.to_date.isoformat(), ds.fmt)
            if not info:
                ds.status = STATUS_FAILED
                ds.error = "No data returned for this instrument / range"
                db.commit()
                return
            ds.file_path = info["path"]
            ds.file_format = info["format"]
            ds.rows = info["rows"]
            ds.checksum = info["checksum"]
            ds.size_bytes = info["size"]
            if cfg.get("enable_quality_checks", True):
                ds.quality = quality.quality_report(info["records"], ds.interval)
            ds.status = STATUS_COMPLETED
            ds.progress = 100
            db.commit()
            logger.info("dataset %s completed: %d rows → %s", ds_id, info["rows"], info["format"])
        except Exception as exc:
            logger.error("download job %s failed: %s", ds_id, exc)
            try:
                if ds:
                    ds.status = STATUS_FAILED
                    ds.error = str(exc)[:300]
                    db.commit()
            except Exception:
                db.rollback()
        finally:
            db.close()
            with self._lock:
                self._threads.pop(ds_id, None)
                self._cancel.pop(ds_id, None)

    def _fetch_chunk(self, broker, meta, kite_interval, ch, include_oi, cfg, ev):
        start = datetime.fromisoformat(ch["start"])
        end = datetime.fromisoformat(ch["end"]).replace(hour=23, minute=59, second=59)
        attempts = int(cfg.get("max_retries", 3))
        base_delay = float(cfg.get("retry_delay", 2.0))
        for a in range(1, attempts + 1):
            if ev.is_set():
                return False, "cancelled"
            try:
                time.sleep(float(cfg.get("request_gap", 0.4)))      # rate limit
                candles = broker.get_historical_data(meta["instrument_token"], start, end,
                                                     kite_interval, oi=include_oi) or []
                return True, normalize.normalize_candles(candles, meta, meta["interval_label"])
            except Exception as exc:
                if a >= attempts:
                    return False, exc
                wait = min(base_delay * (2 ** (a - 1)), 30.0)       # exponential backoff
                if ev.wait(wait):                                    # interruptible
                    return False, "cancelled"
        return False, "max retries exceeded"


# module-level singleton (mirrors the trading engine's single-process model)
manager = DownloadJobManager()
