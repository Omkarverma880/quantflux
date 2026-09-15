"""
Market Store — the one place historical index and option candles live.

Follows the platform's existing storage rule (see ``DataDataset``): candles on
disk, PostgreSQL holds the catalog. Millions of option minute-bars as Postgres
rows would put exactly the load on the database this is meant to avoid; as
zstd-compressed columnar Parquet they are ~10x smaller and scan in milliseconds
with predicate push-down.

Layout (Hive partitions, so a date-range read touches only the months it needs):

    DATA_DIR/market_store/
        kind=options/underlying=NIFTY/year=2025/month=09/part-<hash>.parquet
        kind=spot/underlying=NIFTY/year=2025/month=09/part-<hash>.parquet

Invariants the store guarantees, because every backtest depends on them:

  * one row per (timestamp, contract) for options and per timestamp for spot —
    re-uploading overlapping data never double-counts
  * a contract is identified by its real name, never by a rolling "ATM" label;
    rolling series are exploded back into the contracts they were built from
  * appends rewrite only the months they touch
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from config import settings
from core.logger import get_logger

logger = get_logger("research.market_store")

# On Railway the container disk is wiped on every redeploy. Point MARKET_STORE_DIR
# at a mounted volume (e.g. /data/market_store) so uploaded history survives.
ROOT = Path(os.environ.get("MARKET_STORE_DIR") or (Path(settings.DATA_DIR) / "market_store"))
COMPRESSION = "zstd"
SESSION_START_MIN, SESSION_END_MIN = 9 * 60 + 15, 15 * 60 + 30

OPTION_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("s")),
    ("contract", pa.string()),
    ("underlying", pa.string()),
    ("expiry_date", pa.date32()),
    ("strike", pa.float32()),
    ("option_type", pa.string()),          # CE | PE
    ("open", pa.float32()), ("high", pa.float32()), ("low", pa.float32()),
    ("close", pa.float32()),
    ("volume", pa.int64()), ("oi", pa.int64()),
    ("iv", pa.float32()),
    ("spot", pa.float32()),
    ("is_monthly_expiry", pa.bool_()),
    ("year", pa.int16()), ("month", pa.int8()),
])

SPOT_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("s")),
    ("underlying", pa.string()),
    ("open", pa.float32()), ("high", pa.float32()), ("low", pa.float32()),
    ("close", pa.float32()), ("volume", pa.int64()),
    ("year", pa.int16()), ("month", pa.int8()),
])


# ── normalisation ────────────────────────────────────────────────────
def _ts(series: pd.Series) -> pd.Series:
    t = pd.to_datetime(series, errors="coerce")
    if getattr(t.dt, "tz", None) is not None:
        t = t.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return t.dt.floor("s")


def _opt_type(v: pd.Series) -> pd.Series:
    s = v.astype(str).str.upper().str.strip()
    return s.replace({"CALL": "CE", "C": "CE", "PUT": "PE", "P": "PE"})


def normalize_options(df: pd.DataFrame, underlying: str = "NIFTY") -> tuple[pd.DataFrame, dict]:
    """Bring any supported option file to the store schema and report what changed."""
    d = df.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    need = ["timestamp", "strike", "option_type", "open", "high", "low", "close"]
    miss = [c for c in need if c not in d.columns]
    if miss:
        raise ValueError(f"option file is missing column(s): {', '.join(miss)}")
    n0 = len(d)
    d["timestamp"] = _ts(d["timestamp"])
    d["option_type"] = _opt_type(d["option_type"])
    d["underlying"] = (d["underlying"].astype(str).str.upper()
                       if "underlying" in d.columns else underlying.upper())
    if "expiry_date" not in d.columns:
        if "expiry" in d.columns:
            d["expiry_date"] = d["expiry"]
        else:
            raise ValueError("option file needs an expiry_date (or expiry) column")
    d["expiry_date"] = pd.to_datetime(d["expiry_date"], errors="coerce").dt.date
    if "contract" not in d.columns:
        d["contract"] = (d["underlying"] + " " + pd.to_datetime(d["expiry_date"]).dt.strftime("%d-%b-%Y")
                         + " " + d["strike"].astype(float).round(2).map(lambda x: f"{x:g}")
                         + " " + d["option_type"])
    for c in ("volume", "oi"):
        d[c] = pd.to_numeric(d.get(c, 0), errors="coerce").fillna(0).astype("int64")
    for c in ("iv", "spot"):
        d[c] = pd.to_numeric(d.get(c, np.nan), errors="coerce")
    d["is_monthly_expiry"] = d.get("is_monthly_expiry", False)
    d["is_monthly_expiry"] = d["is_monthly_expiry"].fillna(False).astype(bool)

    for c in ("strike", "open", "high", "low", "close"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["timestamp", "strike", "open", "high", "low", "close", "expiry_date"])
    d = d[d.option_type.isin(["CE", "PE"])]
    bad_ohlc = ((d.high < d.low) | (d.close > d.high + 1e-6) | (d.close < d.low - 1e-6)
                | (d.open > d.high + 1e-6) | (d.open < d.low - 1e-6) | (d.close <= 0))
    n_bad = int(bad_ohlc.sum())
    d = d[~bad_ohlc]
    m = d.timestamp.dt.hour * 60 + d.timestamp.dt.minute
    outside = int(((m < SESSION_START_MIN) | (m >= SESSION_END_MIN)).sum())
    d = d[(m >= SESSION_START_MIN) & (m < SESSION_END_MIN)]
    # rolling ATM series: the same (timestamp, contract) appears in several files
    dups = int(d.duplicated(["timestamp", "contract"]).sum())
    conflicts = 0
    if dups:
        g = d.groupby(["timestamp", "contract"])["close"].nunique()
        conflicts = int((g > 1).sum())
    d = d.sort_values(["timestamp", "contract"]).drop_duplicates(["timestamp", "contract"], keep="last")
    d["year"] = d.timestamp.dt.year.astype("int16")
    d["month"] = d.timestamp.dt.month.astype("int8")
    report = {"rows_in": int(n0), "rows_out": int(len(d)), "bad_ohlc": n_bad,
              "outside_session": outside, "duplicates_merged": dups,
              "conflicting_duplicates": conflicts,
              "contracts": int(d.contract.nunique()),
              "first": str(d.timestamp.min()) if len(d) else None,
              "last": str(d.timestamp.max()) if len(d) else None}
    cols = [f.name for f in OPTION_SCHEMA]
    return d[cols], report


def normalize_spot(df: pd.DataFrame, underlying: str = "NIFTY") -> tuple[pd.DataFrame, dict]:
    d = df.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    ts = next((c for c in ("timestamp", "datetime", "date", "time") if c in d.columns), None)
    if ts is None or any(c not in d.columns for c in ("open", "high", "low", "close")):
        raise ValueError("spot file needs timestamp, open, high, low, close")
    n0 = len(d)
    d["timestamp"] = _ts(d[ts])
    d["underlying"] = underlying.upper()
    d["volume"] = pd.to_numeric(d.get("volume", 0), errors="coerce").fillna(0).astype("int64")
    for c in ("open", "high", "low", "close"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["timestamp", "open", "high", "low", "close"])
    bad = (d.high < d.low) | (d.close > d.high + 1e-6) | (d.close < d.low - 1e-6)
    n_bad = int(bad.sum())
    d = d[~bad]
    m = d.timestamp.dt.hour * 60 + d.timestamp.dt.minute
    outside = int(((m < SESSION_START_MIN) | (m >= SESSION_END_MIN)).sum())
    d = d[(m >= SESSION_START_MIN) & (m < SESSION_END_MIN)]
    dups = int(d.duplicated("timestamp").sum())
    d = d.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    d["year"] = d.timestamp.dt.year.astype("int16")
    d["month"] = d.timestamp.dt.month.astype("int8")
    report = {"rows_in": int(n0), "rows_out": int(len(d)), "bad_ohlc": n_bad,
              "outside_session": outside, "duplicates_merged": dups,
              "sessions": int(d.timestamp.dt.date.nunique()),
              "first": str(d.timestamp.min()) if len(d) else None,
              "last": str(d.timestamp.max()) if len(d) else None}
    return d[[f.name for f in SPOT_SCHEMA]], report


# ── write / append ───────────────────────────────────────────────────
def _part_dir(kind: str, underlying: str, year: int, month: int) -> Path:
    return ROOT / f"kind={kind}" / f"underlying={underlying.upper()}" / f"year={year}" / f"month={month:02d}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def append(kind: str, df: pd.DataFrame, underlying: str = "NIFTY") -> list[dict]:
    """Merge ``df`` into the store. Rewrites only the months it touches.

    Idempotent: appending the same data twice leaves the store unchanged.
    Returns one catalog record per partition written.
    """
    if df.empty:
        return []
    key = ["timestamp", "contract"] if kind == "options" else ["timestamp"]
    schema = OPTION_SCHEMA if kind == "options" else SPOT_SCHEMA
    out = []
    for (y, mo), part in df.groupby(["year", "month"]):
        pdir = _part_dir(kind, underlying, int(y), int(mo))
        existing = []
        if pdir.exists():
            for f in pdir.glob("*.parquet"):
                existing.append(pq.read_table(f).to_pandas())
        merged = pd.concat(existing + [part], ignore_index=True) if existing else part
        before = sum(len(e) for e in existing)
        merged = merged.sort_values(key).drop_duplicates(key, keep="last")
        # underlying/year/month live in the directory path; writing them into the
        # file as well makes Arrow see two conflicting definitions of each field.
        file_schema = pa.schema([f for f in schema if f.name not in PARTITION_COLS])
        table = pa.Table.from_pandas(merged[[f.name for f in file_schema]],
                                     schema=file_schema, preserve_index=False)
        tmp = Path(tempfile.mkdtemp(prefix="mstore_"))
        tmp_file = tmp / "part.parquet"
        pq.write_table(table, tmp_file, compression=COMPRESSION, row_group_size=200_000,
                       use_dictionary=["contract", "option_type"] if kind == "options" else False)
        digest = _sha256(tmp_file)
        pdir.mkdir(parents=True, exist_ok=True)
        final = pdir / f"part-{digest[:16]}.parquet"
        shutil.move(str(tmp_file), final)                  # write new first …
        for f in pdir.glob("*.parquet"):                  # … then drop the old
            if f != final:
                f.unlink()
        shutil.rmtree(tmp, ignore_errors=True)
        ts = merged["timestamp"]
        out.append({
            "kind": kind, "underlying": underlying.upper(), "year": int(y), "month": int(mo),
            "path": str(final), "rows": int(len(merged)), "rows_added": int(len(merged) - before),
            "bytes": final.stat().st_size, "checksum": digest,
            "first_ts": ts.min().to_pydatetime(), "last_ts": ts.max().to_pydatetime(),
            "sessions": int(ts.dt.date.nunique()),
            "contracts": int(merged["contract"].nunique()) if kind == "options" else None,
        })
    return out


# ── read ─────────────────────────────────────────────────────────────
PARTITION_COLS = ("underlying", "year", "month")
_PARTITIONING = ds.partitioning(
    pa.schema([("underlying", pa.string()), ("year", pa.int16()), ("month", pa.int8())]),
    flavor="hive")


def _dataset(kind: str) -> Optional[ds.Dataset]:
    base = ROOT / f"kind={kind}"
    if not base.exists() or not any(base.rglob("*.parquet")):
        return None
    return ds.dataset(str(base), format="parquet", partitioning=_PARTITIONING)


def read(kind: str, underlying: str = "NIFTY", start: Optional[str] = None,
         end: Optional[str] = None, columns: Optional[Iterable[str]] = None,
         where: Optional[ds.Expression] = None) -> pd.DataFrame:
    """Predicate-pushdown read. Only the partitions in range are opened."""
    d = _dataset(kind)
    if d is None:
        return pd.DataFrame()
    f = ds.field("underlying") == underlying.upper()
    if start:
        s = pd.Timestamp(start)
        f = f & (ds.field("timestamp") >= pa.scalar(s.to_pydatetime(), pa.timestamp("s")))
        f = f & ((ds.field("year") > s.year) | ((ds.field("year") == s.year) & (ds.field("month") >= s.month)))
    if end:
        e = pd.Timestamp(end) + pd.Timedelta(days=1)
        f = f & (ds.field("timestamp") < pa.scalar(e.to_pydatetime(), pa.timestamp("s")))
        f = f & ((ds.field("year") < e.year) | ((ds.field("year") == e.year) & (ds.field("month") <= e.month)))
    if where is not None:
        f = f & where
    cols = list(columns) if columns else None
    return d.to_table(filter=f, columns=cols).to_pandas()


def summary() -> dict:
    """What is in the store, without opening a single data page."""
    out = {}
    for kind in ("spot", "options"):
        base = ROOT / f"kind={kind}"
        files = list(base.rglob("*.parquet")) if base.exists() else []
        rows = 0; size = 0; first = last = None
        for f in files:
            md = pq.ParquetFile(f).metadata
            rows += md.num_rows; size += f.stat().st_size
        parts = sorted({(int(p.parent.parent.name.split("=")[1]), int(p.parent.name.split("=")[1]))
                        for p in files})
        if parts:
            first, last = f"{parts[0][0]}-{parts[0][1]:02d}", f"{parts[-1][0]}-{parts[-1][1]:02d}"
        out[kind] = {"files": len(files), "rows": rows, "bytes": size,
                     "first_month": first, "last_month": last, "months": len(parts)}
    return out
