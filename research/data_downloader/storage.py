"""
Storage layer for the Data Downloader — the single seam between download and
storage (swap for S3/object storage later without touching the engine).

Datasets are written under the existing data directory as Parquet (default) or
CSV. Each chunk is persisted as an intermediate "part" so a failed/resumed
download never re-fetches completed chunks; on completion the parts are merged,
de-duplicated, sorted and written to the final file with a checksum.

Railway note: the container filesystem is ephemeral. Files persist for the
instance lifetime; metadata (path, checksum, rows) lives in PostgreSQL so the
UI/catalog survives restarts even if a file must later be re-downloaded or moved
to object storage.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from research.data_downloader.config import DATA_ROOT
from research.data_downloader.constants import SCHEMA

logger = get_logger("research.data_downloader.storage")

_BUCKET = {"index": "indices", "equity": "equity", "futures": "futures", "options": "options"}


def _safe(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in (name or "sym")).strip("_") or "sym"


def dataset_dir(instrument_type: str, symbol: str) -> Path:
    return DATA_ROOT / _BUCKET.get(instrument_type, "other") / _safe(symbol)


def _parts_dir(dataset_id: int) -> Path:
    return DATA_ROOT / ".parts" / str(dataset_id)


def parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except Exception:
        try:
            import fastparquet  # noqa: F401
            return True
        except Exception:
            return False


def _df(rows: list[dict]):
    import pandas as pd
    df = pd.DataFrame(rows, columns=SCHEMA)
    return df


def write_part(rows: list[dict], dataset_id: int, chunk_idx: int) -> int:
    """Persist one chunk's rows as an intermediate part. Returns row count."""
    if not rows:
        return 0
    pd_dir = _parts_dir(dataset_id)
    pd_dir.mkdir(parents=True, exist_ok=True)
    df = _df(rows)
    try:
        df.to_parquet(pd_dir / f"chunk_{chunk_idx:05d}.parquet", index=False)
    except Exception:                      # no parquet engine → CSV parts
        df.to_csv(pd_dir / f"chunk_{chunk_idx:05d}.csv", index=False)
    return len(df)


def has_part(dataset_id: int, chunk_idx: int) -> bool:
    d = _parts_dir(dataset_id)
    return (d / f"chunk_{chunk_idx:05d}.parquet").exists() or (d / f"chunk_{chunk_idx:05d}.csv").exists()


def _read_any(path: Path):
    import pandas as pd
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def finalize(dataset_id: int, instrument_type: str, symbol: str, interval: str,
             from_date: str, to_date: str, fmt: str) -> Optional[dict]:
    """Merge parts → final file. Returns {path, format, rows, checksum, size, records}."""
    import pandas as pd
    pd_dir = _parts_dir(dataset_id)
    parts = sorted(pd_dir.glob("chunk_*.*")) if pd_dir.exists() else []
    if not parts:
        return None
    frames = []
    for p in parts:
        try:
            frames.append(_read_any(p))
        except Exception as exc:
            logger.warning("part read failed %s: %s", p, exc)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    if "timestamp" in df.columns:
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Drop columns that never apply to this instrument type so files stay lean
    # (an index/equity has no expiry/strike/option; a future has no strike/option).
    drop = {
        "index": ["expiry", "strike", "option_type"],
        "equity": ["expiry", "strike", "option_type"],
        "futures": ["strike", "option_type"],
    }.get(instrument_type, [])
    if "oi" in df.columns and df["oi"].isnull().all():       # OI absent / not requested
        drop = drop + ["oi"]
    if drop:
        df = df.drop(columns=[c for c in drop if c in df.columns])

    out_dir = dataset_dir(instrument_type, symbol)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{_safe(symbol)}_{interval}_{from_date}_{to_date}"
    use_parquet = fmt == "parquet" and parquet_available()
    ext = "parquet" if use_parquet else "csv"
    path = out_dir / f"{base}.{ext}"
    if use_parquet:
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)

    checksum = _sha256(path)
    records = df.to_dict("records")
    # tidy up intermediate parts
    try:
        shutil.rmtree(pd_dir, ignore_errors=True)
    except Exception:
        pass
    return {"path": str(path), "format": ext, "rows": int(len(df)),
            "checksum": checksum, "size": path.stat().st_size, "records": records}


def read_sample(file_path: str, limit: int = 200) -> list[dict]:
    p = Path(file_path)
    if not p.exists():
        return []
    try:
        df = _read_any(p).head(limit)
        return df.where(df.notnull(), None).to_dict("records")
    except Exception as exc:
        logger.warning("sample read failed: %s", exc)
        return []


def to_csv_path(file_path: str) -> Optional[str]:
    """Return a CSV sibling of a dataset (create from parquet on demand)."""
    p = Path(file_path)
    if not p.exists():
        return None
    if p.suffix == ".csv":
        return str(p)
    csv_path = p.with_suffix(".csv")
    if not csv_path.exists():
        try:
            _read_any(p).to_csv(csv_path, index=False)
        except Exception as exc:
            logger.warning("csv export failed: %s", exc)
            return None
    return str(csv_path)


def delete_files(file_path: Optional[str], dataset_id: int) -> None:
    for cand in filter(None, [file_path]):
        p = Path(cand)
        for f in [p, p.with_suffix(".csv"), p.with_suffix(".parquet")]:
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass
    shutil.rmtree(_parts_dir(dataset_id), ignore_errors=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
