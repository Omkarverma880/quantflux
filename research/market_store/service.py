"""
Ingest orchestration: file in → normalised → merged into the store → catalogued.

Kept separate from ``store.py`` so the storage layer has no database dependency
and can be used from scripts and notebooks as well as the API.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.market_store.service")


def detect_kind(df: pd.DataFrame) -> str:
    cols = {str(c).strip().lower() for c in df.columns}
    return "options" if {"strike", "option_type"} <= cols else "spot"


def read_upload(name: str, raw: bytes) -> pd.DataFrame:
    n = (name or "").lower()
    if n.endswith(".parquet"):
        return pd.read_parquet(io.BytesIO(raw))
    if n.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw))
    raise ValueError("Upload a .parquet or .csv file")


def _catalog(records: list[dict]) -> None:
    """Upsert partition rows. Best-effort: a catalog failure never loses data,
    because the files themselves are the source of truth and the catalog can be
    rebuilt from them with ``rebuild_catalog``."""
    if not records:
        return
    try:
        from core.database import get_db_session
        from core.models import MarketStorePartition as P
    except Exception as exc:
        logger.warning("market store catalog unavailable: %s", exc)
        return
    db = get_db_session()
    try:
        for r in records:
            row = (db.query(P).filter(P.kind == r["kind"], P.underlying == r["underlying"],
                                      P.year == r["year"], P.month == r["month"]).first())
            if row is None:
                row = P(kind=r["kind"], underlying=r["underlying"], year=r["year"], month=r["month"])
                db.add(row)
            row.path = r["path"]; row.rows = r["rows"]; row.bytes = r["bytes"]
            row.checksum = r["checksum"]; row.first_ts = r["first_ts"]; row.last_ts = r["last_ts"]
            row.sessions = r["sessions"]; row.contracts = r["contracts"]
            row.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("market store catalog upsert failed: %s", exc)
    finally:
        db.close()


def _log(user_id: int, filename: str, kind: str, underlying: str, report: dict,
         rows_added: int, status: str = "completed", error: str = "") -> None:
    try:
        from core.database import get_db_session
        from core.models import MarketStoreIngest
        db = get_db_session()
        try:
            db.add(MarketStoreIngest(user_id=user_id, filename=filename, kind=kind,
                                     underlying=underlying, status=status,
                                     rows_in=int(report.get("rows_in", 0)),
                                     rows_added=int(rows_added), report=report, error=error))
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.debug("market store ingest log failed: %s", exc)


def ingest_frame(df: pd.DataFrame, *, filename: str = "", kind: Optional[str] = None,
                 underlying: str = "NIFTY", user_id: int = 0, catalog: bool = True) -> dict:
    kind = kind or detect_kind(df)
    try:
        if kind == "options":
            norm, report = MS.normalize_options(df, underlying)
        else:
            norm, report = MS.normalize_spot(df, underlying)
        records = MS.append(kind, norm, underlying)
        added = sum(r["rows_added"] for r in records)
        durable_result = None
        if catalog:
            from research.market_store import durable
            durable_result = durable.save(records)
            _catalog(records)
            _log(user_id, filename, kind, underlying, report, added)
        out = {"status": "ok", "kind": kind, "filename": filename, "report": report,
               "rows_added": added, "partitions": len(records),
               "months": [f"{r['year']}-{r['month']:02d}" for r in records]}
        if durable_result is not None:
            out["saved_to_database"] = durable_result.get("ok", False)
            if not durable_result.get("ok", False):
                out["warning"] = ("Stored on this server's disk only — saving to the database failed, "
                                  "so this upload will be lost on the next deploy: "
                                  + durable_result.get("error", ""))
        return out
    except Exception as exc:
        logger.error("market store ingest failed (%s): %s", filename, exc)
        if catalog:
            _log(user_id, filename, kind or "?", underlying, {}, 0, "failed", str(exc)[:500])
        return {"status": "error", "filename": filename, "message": str(exc)[:400]}


def rebuild_catalog() -> int:
    """Re-derive every catalog row from the files on disk."""
    import pyarrow.parquet as pq
    records = []
    for kind in ("spot", "options"):
        base = MS.ROOT / f"kind={kind}"
        if not base.exists():
            continue
        for f in base.rglob("*.parquet"):
            und = f.parent.parent.parent.name.split("=")[1]
            y = int(f.parent.parent.name.split("=")[1]); mo = int(f.parent.name.split("=")[1])
            cols = ["timestamp", "contract"] if kind == "options" else ["timestamp"]
            t = pq.read_table(f, columns=cols).to_pandas()
            records.append({"kind": kind, "underlying": und, "year": y, "month": mo,
                            "path": str(f), "rows": len(t), "rows_added": 0,
                            "bytes": f.stat().st_size, "checksum": f.stem.replace("part-", ""),
                            "first_ts": t.timestamp.min().to_pydatetime(),
                            "last_ts": t.timestamp.max().to_pydatetime(),
                            "sessions": int(t.timestamp.dt.date.nunique()),
                            "contracts": int(t.contract.nunique()) if kind == "options" else None})
    _catalog(records)
    return len(records)
