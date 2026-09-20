"""
Data Ingestion Lab service.

Flow for every file:  stage → preview (auto-mapped) → validate (dry run) → commit.

  stage     the upload is streamed to disk under DATA_DIR/ingest_staging/<id>; nothing is
            read into memory, so files larger than RAM are fine
  preview   the first few thousand rows: columns, guessed mapping, kind (index or options),
            underlying, bar size, date range
  validate  the whole file through the same mapping + Market Store normaliser, in chunks,
            without writing: rows kept/dropped and why, contracts, sessions, how many rows are
            new versus already stored
  commit    the same transform, merged into the store through ``market_store.service
            .ingest_frame`` — so the database copy, catalog and audit log behave exactly as
            for every other upload — then the OI Lab study for that underlying is refreshed

Validate and commit run as background jobs (big files take minutes); the UI polls them.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from config import settings
from core.logger import get_logger
from research.data_ingestion import mapping as MP
from research.market_store import store as MS

logger = get_logger("research.data_ingestion")

STAGE_ROOT = Path(settings.DATA_DIR) / "ingest_staging"
STAGE_TTL_H = 48
CHUNK_ROWS = 400_000
PREVIEW_ROWS = 3000
SUPPORTED = (".csv", ".csv.gz", ".parquet", ".pq")


# ── staging ──────────────────────────────────────────────────────────
def _stage_dir(stage_id: str) -> Path:
    if not stage_id or any(c not in "0123456789abcdef" for c in stage_id):
        raise ValueError("invalid stage id")
    return STAGE_ROOT / stage_id


def _meta_path(stage_id: str) -> Path:
    return _stage_dir(stage_id) / "meta.json"


def _read_meta(stage_id: str) -> dict:
    p = _meta_path(stage_id)
    if not p.exists():
        raise FileNotFoundError("staged file not found — it may have expired; upload it again")
    return json.loads(p.read_text())


def _write_meta(stage_id: str, meta: dict) -> None:
    _meta_path(stage_id).write_text(json.dumps(meta, default=str))


def supported(filename: str) -> bool:
    n = (filename or "").lower()
    return n.endswith(SUPPORTED)


def new_stage(filename: str, user_id: int, source: str = "upload") -> tuple[str, Path]:
    if not supported(filename):
        raise ValueError("Upload a .csv, .csv.gz or .parquet file")
    cleanup_stale()
    sid = uuid.uuid4().hex
    d = STAGE_ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in Path(filename).name)
    _write_meta(sid, {"id": sid, "filename": filename, "file": safe, "user_id": user_id, "source": source,
                      "created": datetime.now().isoformat(timespec="seconds")})
    return sid, d / safe


def finish_stage(stage_id: str) -> dict:
    meta = _read_meta(stage_id)
    f = _stage_dir(stage_id) / meta["file"]
    meta["bytes"] = f.stat().st_size
    _write_meta(stage_id, meta)
    return preview(stage_id)


def stage_copy(src: Path, filename: str, user_id: int, source: str) -> dict:
    sid, dest = new_stage(filename, user_id, source)
    shutil.copyfile(src, dest)
    return finish_stage(sid)


def list_stages(user_id: int) -> list[dict]:
    out = []
    if not STAGE_ROOT.exists():
        return out
    for d in STAGE_ROOT.iterdir():
        try:
            m = json.loads((d / "meta.json").read_text())
        except Exception:
            continue
        if m.get("user_id") == user_id:
            out.append({k: m.get(k) for k in ("id", "filename", "bytes", "created", "source", "kind",
                                              "underlying", "committed", "last_job")})
    return sorted(out, key=lambda m: m.get("created") or "", reverse=True)


def drop_stage(stage_id: str) -> None:
    shutil.rmtree(_stage_dir(stage_id), ignore_errors=True)


def cleanup_stale() -> None:
    if not STAGE_ROOT.exists():
        return
    cutoff = time.time() - STAGE_TTL_H * 3600
    for d in STAGE_ROOT.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


# ── reading ──────────────────────────────────────────────────────────
def _file(stage_id: str) -> Path:
    return _stage_dir(stage_id) / _read_meta(stage_id)["file"]


def _is_parquet(path: Path) -> bool:
    return path.name.lower().endswith((".parquet", ".pq"))


def iter_chunks(path: Path, columns: Optional[list[str]] = None, rows: int = CHUNK_ROWS) -> Iterator[pd.DataFrame]:
    if _is_parquet(path):
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=rows, columns=columns):
            yield batch.to_pandas()
    else:
        for chunk in pd.read_csv(path, chunksize=rows, usecols=columns, low_memory=False):
            yield chunk


def head(path: Path, n: int = PREVIEW_ROWS) -> pd.DataFrame:
    if _is_parquet(path):
        pf = pq.ParquetFile(path)
        return next(pf.iter_batches(batch_size=n)).to_pandas() if pf.metadata.num_rows else pd.DataFrame()
    return pd.read_csv(path, nrows=n, low_memory=False)


def total_rows(path: Path) -> Optional[int]:
    if _is_parquet(path):
        return int(pq.ParquetFile(path).metadata.num_rows)
    if path.name.lower().endswith(".gz"):
        return None
    n = -1
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            n += block.count(b"\n")
    return max(n, 0)


# ── preview ──────────────────────────────────────────────────────────
def preview(stage_id: str) -> dict:
    meta = _read_meta(stage_id)
    path = _file(stage_id)
    df = head(path)
    if df.empty:
        return {"status": "error", "stage_id": stage_id, "message": "The file has no rows."}
    mp = MP.guess_mapping(list(df.columns))
    kind = MP.detect_kind(df, mp)
    und = MP.detect_underlying(df, mp, meta["filename"])
    framed, notes = MP.apply_mapping(df, kind, mp)
    ts = framed["timestamp"].dropna() if "timestamp" in framed else pd.Series(dtype="datetime64[ns]")
    minutes = MP.bar_minutes(ts) if len(ts) else None
    warnings = []
    if minutes and minutes >= 1440:
        warnings.append("These look like DAILY bars. The Market Store keeps intraday bars (09:15–15:30); "
                        "daily rows are dropped by the session filter.")
    elif minutes and minutes not in (1, 5):
        warnings.append(f"{minutes}-minute bars are stored, but the OI Lab study reads 1- or 5-minute bars.")
    if kind == "options" and "oi" not in mp:
        warnings.append("No open-interest column found — OI walls, PCR and every OI model need it.")
    if kind == "options" and "spot" not in mp:
        warnings.append("No spot column — also upload the index file for the same dates so the OI study has spot.")
    columns = [{"name": str(c), "dtype": str(df[c].dtype),
                "sample": [MP.jsonable(v) for v in df[c].dropna().head(3).tolist()]} for c in df.columns]
    meta.update(kind=kind, underlying=und, mapping=mp)
    _write_meta(stage_id, meta)
    return {
        "status": "ok", "stage_id": stage_id, "filename": meta["filename"], "bytes": meta.get("bytes"),
        "rows_estimate": total_rows(path), "columns": columns,
        "guess": {"kind": kind, "underlying": und, "mapping": mp},
        "fields": {"spot": MP.SPOT_FIELDS, "options": MP.OPTION_FIELDS},
        "required": MP.REQUIRED, "missing_required": MP.missing_required(kind, mp, framed),
        "bar_minutes": minutes,
        "first": str(ts.min()) if len(ts) else None, "last": str(ts.max()) if len(ts) else None,
        "sample": json.loads(df.head(20).to_json(orient="records", date_format="iso", default_handler=str)),
        "notes": notes, "warnings": warnings,
        "known_underlyings": MP.KNOWN_UNDERLYINGS,
    }


# ── transform (shared by validate and commit) ────────────────────────
def _symbol_last_days(path: Path, mp: dict) -> Optional[pd.Series]:
    """Last traded day per symbol across the WHOLE file (for monthly symbols without an expiry column)."""
    if "symbol" not in mp or mp.get("expiry_date"):
        return None
    cols = [mp["symbol"], mp["timestamp"]] + ([mp["time_part"]] if mp.get("time_part") else [])
    last = None
    for ch in iter_chunks(path, columns=list(dict.fromkeys(cols))):
        framed = pd.DataFrame({"symbol": ch[mp["symbol"]].astype(str), "timestamp": MP._timestamps(ch, mp)})
        s = framed.assign(d=framed["timestamp"].dt.date).groupby("symbol")["d"].max()
        last = s if last is None else pd.concat([last, s]).groupby(level=0).max()
    return last


def _transform_chunks(stage_id: str, kind: str, underlying: str, mp: dict,
                      progress: Callable[[str], None]) -> Iterator[tuple[pd.DataFrame, dict, list[str]]]:
    path = _file(stage_id)
    if kind not in ("spot", "options"):
        raise ValueError("kind must be 'spot' or 'options'")
    und = (underlying or "").upper().strip()
    if not und or not und.replace("_", "").isalnum():
        raise ValueError("Choose the underlying (e.g. NIFTY, SENSEX)")
    missing = [f for f in MP.REQUIRED[kind] if f not in mp and not (f in ("strike", "option_type", "expiry_date") and "symbol" in mp)]
    if missing:
        raise ValueError(f"Map these required fields first: {', '.join(missing)}")
    last_days = _symbol_last_days(path, mp) if kind == "options" else None
    total = total_rows(path)
    seen = 0
    for ch in iter_chunks(path):
        seen += len(ch)
        progress(f"{seen:,}" + (f" / {total:,}" if total else "") + " rows")
        framed, notes = MP.apply_mapping(ch, kind, mp, symbol_last_day=last_days)
        if kind == "options":
            norm, rep = MS.normalize_options(framed, und)
        else:
            norm, rep = MS.normalize_spot(framed, und)
        yield norm, rep, notes


def _existing_keys(kind: str, und: str, y: int, m: int) -> Optional[pd.Index]:
    base = MS._part_dir(kind, und, y, m)
    files = list(base.glob("*.parquet")) if base.exists() else []
    if not files:
        return None
    cols = ["timestamp", "contract"] if kind == "options" else ["timestamp"]
    t = pd.concat([pq.read_table(f, columns=cols).to_pandas() for f in files])
    return pd.MultiIndex.from_frame(t) if kind == "options" else pd.Index(t["timestamp"])


def validate(stage_id: str, kind: str, underlying: str, mp: dict, progress: Callable[[str], None]) -> dict:
    und = underlying.upper()
    agg = {"rows_in": 0, "rows_out": 0, "bad_ohlc": 0, "outside_session": 0, "duplicates_merged": 0,
           "conflicting_duplicates": 0}
    contracts, sessions, months = set(), set(), {}
    expiries, strikes = set(), [np.inf, -np.inf]
    first = last = None
    notes, interval = [], None
    oi_zero = oi_rows = iv_missing = spot_missing = 0
    new_rows = 0
    existing_cache: dict = {}
    sample = None
    for norm, rep, n in _transform_chunks(stage_id, kind, und, mp, progress):
        for k in agg:
            agg[k] += int(rep.get(k, 0))
        notes.extend(x for x in n if x not in notes)
        if norm.empty:
            continue
        if sample is None:
            sample = json.loads(norm.head(10).to_json(orient="records", date_format="iso", default_handler=str))
        if interval is None:
            one = norm[norm["contract"] == norm["contract"].iloc[0]] if kind == "options" else norm
            interval = MP.bar_minutes(one["timestamp"])
        ts = norm["timestamp"]
        first = ts.min() if first is None else min(first, ts.min())
        last = ts.max() if last is None else max(last, ts.max())
        sessions.update(ts.dt.date.unique())
        if kind == "options":
            contracts.update(norm["contract"].unique())
            expiries.update(pd.to_datetime(norm["expiry_date"]).dt.date.unique())
            strikes = [min(strikes[0], float(norm["strike"].min())), max(strikes[1], float(norm["strike"].max()))]
            oi_rows += len(norm)
            oi_zero += int((norm["oi"] <= 0).sum())
            iv_missing += int(norm["iv"].isna().sum())
            spot_missing += int(norm["spot"].isna().sum())
        for (y, m), part in norm.groupby(["year", "month"]):
            key = (int(y), int(m))
            months[key] = months.get(key, 0) + len(part)
            if key not in existing_cache:
                existing_cache[key] = _existing_keys(kind, und, *key)
            ex = existing_cache[key]
            if ex is None:
                new_rows += len(part)
            elif kind == "options":
                new_rows += int((~pd.MultiIndex.from_frame(part[["timestamp", "contract"]]).isin(ex)).sum())
            else:
                new_rows += int((~part["timestamp"].isin(ex)).sum())
    warnings = []
    if agg["rows_out"] == 0:
        warnings.append("Nothing would be stored — check the mapping, the timestamp format and that bars fall within 09:15–15:30.")
    if kind == "options" and oi_rows and oi_zero / oi_rows > 0.5:
        warnings.append(f"{oi_zero / oi_rows:.0%} of option bars have no OI — the OI Lab cannot use them.")
    if kind == "options" and oi_rows and spot_missing / oi_rows > 0.5:
        warnings.append("Most option bars carry no spot: upload the index file for the same dates too.")
    if kind == "options" and oi_rows and iv_missing / oi_rows > 0.5:
        notes.append("No IV in the file — the OI study computes it from premium, spot and expiry (Black-Scholes, r = 10%).")
    if interval and interval not in (1, 5):
        warnings.append(f"{interval}-minute bars: stored, but the OI Lab study needs 1- or 5-minute bars.")
    return {
        "status": "ok", "kind": kind, "underlying": und, "report": agg,
        "rows_new": int(new_rows), "rows_already_stored": int(agg["rows_out"] - new_rows),
        "contracts": len(contracts), "sessions": len(sessions), "bar_minutes": interval,
        "first": str(first) if first is not None else None, "last": str(last) if last is not None else None,
        "months": [{"month": f"{y}-{m:02d}", "rows": r} for (y, m), r in sorted(months.items())],
        "expiries": len(expiries), "nearest_expiries": [str(e) for e in sorted(expiries)[:6]],
        "strike_range": strikes if kind == "options" and np.isfinite(strikes[0]) else None,
        "sample": sample or [], "notes": notes, "warnings": warnings,
    }


def commit(stage_id: str, kind: str, underlying: str, mp: dict, user_id: int, progress: Callable[[str], None]) -> dict:
    from research.market_store import service as SV
    und = underlying.upper()
    meta = _read_meta(stage_id)
    totals = {"rows_in": 0, "rows_stored": 0, "rows_added": 0, "months": set(), "warnings": [], "saved_to_database": True}
    notes: list[str] = []
    for i, (norm, rep, n) in enumerate(_transform_chunks(stage_id, kind, und, mp, progress)):
        notes.extend(x for x in n if x not in notes)
        totals["rows_in"] += int(rep.get("rows_in", 0))
        if norm.empty:
            continue
        progress(f"merging chunk {i + 1} into the store")
        res = SV.ingest_frame(norm, filename=meta["filename"], kind=kind, underlying=und, user_id=user_id)
        if res.get("status") != "ok":
            raise RuntimeError(res.get("message") or "ingest failed")
        totals["rows_stored"] += len(norm)
        totals["rows_added"] += int(res.get("rows_added", 0))
        totals["months"].update(res.get("months", []))
        if res.get("warning") and res["warning"] not in totals["warnings"]:
            totals["warnings"].append(res["warning"])
        if res.get("saved_to_database") is False:
            totals["saved_to_database"] = False
    meta["committed"] = datetime.now().isoformat(timespec="seconds")
    _write_meta(stage_id, meta)
    _invalidate_coverage()
    study = _refresh_study(und)
    return {"status": "ok", "kind": kind, "underlying": und, "rows_in": totals["rows_in"],
            "rows_stored": totals["rows_stored"], "rows_added": totals["rows_added"],
            "months": sorted(totals["months"]), "saved_to_database": totals["saved_to_database"],
            "warnings": totals["warnings"], "notes": notes, "oi_study": study}


def _refresh_study(underlying: str) -> Optional[str]:
    try:
        from research.oi_lab import history as HS
        if underlying in HS.available_underlyings():
            HS.ensure_started(underlying, force=True)
            return f"OI Lab study for {underlying} is rebuilding with the new data."
    except Exception as exc:
        logger.debug("study refresh skipped: %s", exc)
    return None


# ── background jobs ──────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def start_job(action: str, stage_id: str, kind: str, underlying: str, mp: dict, user_id: int) -> dict:
    _read_meta(stage_id)                                  # fail fast on a bad id
    for j in _jobs.values():
        if j["stage_id"] == stage_id and j["status"] == "running":
            return j
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "action": action, "stage_id": stage_id, "user_id": user_id, "status": "running",
           "progress": "starting", "started": time.time(), "result": None, "error": None}
    with _jobs_lock:
        _jobs[jid] = job
        for old in [k for k, v in _jobs.items() if v["status"] != "running" and time.time() - v["started"] > 6 * 3600]:
            _jobs.pop(old, None)

    def run():
        def progress(msg):
            job["progress"] = msg
        try:
            if action == "validate":
                job["result"] = validate(stage_id, kind, underlying, mp, progress)
            else:
                job["result"] = commit(stage_id, kind, underlying, mp, user_id, progress)
            job["status"] = "done"
            meta = _read_meta(stage_id)
            meta.update(kind=kind, underlying=underlying.upper(), mapping=mp, last_job=action)
            _write_meta(stage_id, meta)
        except Exception as exc:
            logger.error("ingestion %s failed: %s | %s", action, exc, traceback.format_exc())
            job["status"] = "error"
            job["error"] = str(exc)[:500]
        job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=run, daemon=True, name=f"ingest-{action}-{jid}").start()
    return job


def get_job(job_id: str, user_id: int) -> Optional[dict]:
    j = _jobs.get(job_id)
    return j if j and j["user_id"] == user_id else None


# ── one-click pull from Zerodha ──────────────────────────────────────
PULL_STAGE = "zerodha-pull"


def pull_last() -> Optional[dict]:
    from research.data_ingestion import kite_pull as KP
    return KP.last_pull()


def pull_plan(broker, cfg: Optional[dict] = None) -> dict:
    from research.data_ingestion import kite_pull as KP
    return KP.plan(broker, cfg)


def start_pull_job(broker, cfg: Optional[dict], user_id: int) -> dict:
    """Fetch every listed series from Zerodha in the background (the UI polls ``get_job``)."""
    from research.data_ingestion import kite_pull as KP
    for j in _jobs.values():
        if j["stage_id"] == PULL_STAGE and j["status"] == "running":
            return j                                   # one pull at a time is plenty
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "action": "pull", "stage_id": PULL_STAGE, "user_id": user_id, "status": "running",
           "progress": "asking Zerodha what is listed", "started": time.time(), "result": None, "error": None}
    with _jobs_lock:
        _jobs[jid] = job

    def run():
        try:
            job["result"] = KP.run(broker, cfg, lambda m: job.__setitem__("progress", m), user_id)
            job["status"] = "done" if job["result"].get("status") == "ok" else "error"
            job["error"] = job["result"].get("message") if job["status"] == "error" else None
            _invalidate_coverage()
        except Exception as exc:
            logger.error("zerodha pull failed: %s | %s", exc, traceback.format_exc())
            job["status"] = "error"
            job["error"] = str(exc)[:500]
        job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=run, daemon=True, name=f"ingest-pull-{jid}").start()
    return job


# ── coverage ─────────────────────────────────────────────────────────
_coverage_cache: dict = {}


def _invalidate_coverage() -> None:
    _coverage_cache.clear()


def _disk_signature() -> str:
    names = sorted(str(p.relative_to(MS.ROOT)) for p in MS.ROOT.rglob("*.parquet")) if MS.ROOT.exists() else []
    return str(hash(tuple(names)))


def coverage() -> dict:
    """What is stored, per underlying and kind, month by month — with the weekdays that have no bars."""
    MS._sync(block=False)
    sig = _disk_signature()
    if _coverage_cache.get("sig") == sig:
        return _coverage_cache["data"]
    from research.market_store import durable
    seeded = set(durable._partitions_under(durable.SEED_ROOT).keys()) if durable.SEED_ROOT.exists() else set()
    series: dict = {}
    for kind in ("spot", "options"):
        base = MS.ROOT / f"kind={kind}"
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.parquet")):
            und = f.parent.parent.parent.name.split("=")[1]
            y = int(f.parent.parent.name.split("=")[1])
            mo = int(f.parent.name.split("=")[1])
            cols = ["timestamp", "contract"] if kind == "options" else ["timestamp"]
            t = pq.read_table(f, columns=cols).to_pandas()
            days = sorted(t["timestamp"].dt.date.unique())
            s = series.setdefault((und, kind), {"underlying": und, "kind": kind, "months": [], "days": set()})
            s["days"].update(days)
            s["months"].append({
                "year": y, "month": mo, "label": f"{y}-{mo:02d}", "rows": int(len(t)), "bytes": f.stat().st_size,
                "sessions": len(days), "contracts": int(t["contract"].nunique()) if kind == "options" else None,
                "first": str(t["timestamp"].min()), "last": str(t["timestamp"].max()),
                "bundled": (kind, und, y, mo) in seeded,
            })
    out = []
    for (und, kind), s in sorted(series.items()):
        days = sorted(s.pop("days"))
        s["sessions"] = len(days)
        s["first_day"], s["last_day"] = (str(days[0]), str(days[-1])) if days else (None, None)
        s["rows"] = sum(m["rows"] for m in s["months"])
        s["bytes"] = sum(m["bytes"] for m in s["months"])
        have = set(days)
        gaps = []
        if days:
            d = days[0]
            while d <= days[-1]:
                if d.weekday() < 5 and d not in have:
                    gaps.append(str(d))
                d += timedelta(days=1)
        s["weekday_gaps"] = gaps[-60:]
        s["weekday_gap_count"] = len(gaps)
        out.append(s)
    unds = sorted({s["underlying"] for s in out})
    readiness = []
    for u in unds:
        kinds = {s["kind"]: s for s in out if s["underlying"] == u}
        readiness.append({
            "underlying": u, "has_spot": "spot" in kinds, "has_options": "options" in kinds,
            "oi_lab_ready": "options" in kinds,
            "sessions_options": kinds.get("options", {}).get("sessions", 0),
            "sessions_spot": kinds.get("spot", {}).get("sessions", 0),
        })
    data = {"status": "ok", "series": out, "underlyings": readiness, "root": str(MS.ROOT)}
    _coverage_cache.update(sig=sig, data=data)
    return data


def delete_partition(kind: str, underlying: str, year: int, month: int) -> dict:
    """Remove one stored month everywhere: database copy, catalog and disk. Bundled history is protected."""
    from research.market_store import durable
    und = underlying.upper()
    if kind not in ("spot", "options"):
        raise ValueError("kind must be spot or options")
    if (kind, und, int(year), int(month)) in durable._partitions_under(durable.SEED_ROOT):
        raise ValueError("This month is part of the bundled history shipped with the app and would be restored "
                         "automatically — it cannot be removed here.")
    removed_db = False
    try:
        from core.database import get_db_session
        from core.models import MarketStoreBlob as B, MarketStorePartition as P
        db = get_db_session()
        try:
            for M in (B, P):
                n = db.query(M).filter(M.kind == kind, M.underlying == und, M.year == int(year),
                                       M.month == int(month)).delete()
                removed_db = removed_db or bool(n)
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        # the database copy must go first, or the next sync restores the month onto disk
        if durable.enabled():
            raise RuntimeError(f"could not remove the database copy, nothing was deleted: {exc}")
    pdir = MS._part_dir(kind, und, int(year), int(month))
    files = list(pdir.glob("*.parquet")) if pdir.exists() else []
    for f in files:
        f.unlink()
    if pdir.exists() and not any(pdir.iterdir()):
        pdir.rmdir()
    _invalidate_coverage()
    _refresh_study(und)
    return {"status": "ok", "removed_files": len(files), "removed_from_database": removed_db,
            "month": f"{int(year)}-{int(month):02d}", "kind": kind, "underlying": und}


# ── explorer ─────────────────────────────────────────────────────────
def _resample(df: pd.DataFrame, minutes: int, extra: dict | None = None) -> pd.DataFrame:
    if minutes <= 1 or df.empty:
        return df
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", **(extra or {})}
    agg = {k: v for k, v in agg.items() if k in df.columns}
    r = df.set_index("timestamp").groupby(pd.Grouper(freq=f"{minutes}min", origin="start_day", offset="15min")).agg(agg)
    return r.dropna(subset=["close"]).reset_index()


def _day_range(start: str, end: Optional[str], max_days: int = 10) -> tuple[str, str]:
    s = pd.Timestamp(start).normalize()
    e = pd.Timestamp(end).normalize() if end else s
    if e < s:
        s, e = e, s
    if (e - s).days > max_days:
        e = s + pd.Timedelta(days=max_days)
    return str(s.date()), str(e.date())


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", default_handler=str))


def explore_spot(underlying: str, start: str, end: Optional[str] = None, minutes: int = 1) -> dict:
    s, e = _day_range(start, end)
    df = MS.read("spot", underlying.upper(), start=s, end=e, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = _resample(df.sort_values("timestamp"), int(minutes))
    return {"status": "ok", "underlying": underlying.upper(), "start": s, "end": e, "minutes": int(minutes),
            "bars": _records(df)}


def explore_contracts(underlying: str, day: str) -> dict:
    s, _ = _day_range(day, None)
    df = MS.read("options", underlying.upper(), start=s, end=s,
                 columns=["timestamp", "contract", "expiry_date", "strike", "option_type", "oi", "spot"])
    if df.empty:
        return {"status": "ok", "underlying": underlying.upper(), "day": s, "expiries": [], "spot_range": None}
    g = (df.groupby(["expiry_date", "strike", "option_type"])
         .agg(bars=("timestamp", "size"), contract=("contract", "first"), oi=("oi", "last")).reset_index())
    exps = []
    for exp, part in g.groupby("expiry_date"):
        strikes = sorted(part["strike"].unique().tolist())
        exps.append({"expiry": str(exp), "strikes": strikes,
                     "contracts": _records(part.sort_values(["strike", "option_type"]))})
    sp = df["spot"].dropna()
    return {"status": "ok", "underlying": underlying.upper(), "day": s, "expiries": exps,
            "spot_range": [float(sp.min()), float(sp.max())] if len(sp) else None}


def explore_option(underlying: str, contract: str, start: str, end: Optional[str] = None, minutes: int = 1) -> dict:
    s, e = _day_range(start, end, max_days=31)
    df = MS.read("options", underlying.upper(), start=s, end=e,
                 columns=["timestamp", "open", "high", "low", "close", "volume", "oi", "iv", "spot"],
                 where=ds.field("contract") == contract)
    df = _resample(df.sort_values("timestamp"), int(minutes), {"oi": "last", "iv": "last", "spot": "last"})
    return {"status": "ok", "underlying": underlying.upper(), "contract": contract, "start": s, "end": e,
            "minutes": int(minutes), "bars": _records(df)}


def explore_chain(underlying: str, day: str, at: str, expiry: Optional[str] = None) -> dict:
    """Every stored contract's last bar at or before ``at`` (HH:MM) on ``day``."""
    s, _ = _day_range(day, None)
    df = MS.read("options", underlying.upper(), start=s, end=s,
                 columns=["timestamp", "expiry_date", "strike", "option_type", "close", "volume", "oi", "iv", "spot"])
    if df.empty:
        return {"status": "ok", "rows": [], "expiries": []}
    exps = sorted({str(x) for x in df["expiry_date"].unique()})
    exp = expiry or exps[0]
    df = df[df["expiry_date"].astype(str) == exp]
    hh, mm = (int(x) for x in at.split(":"))
    cut = pd.Timestamp(s) + pd.Timedelta(hours=hh, minutes=mm)
    df = df[df["timestamp"] <= cut].sort_values("timestamp")
    if df.empty:
        return {"status": "ok", "rows": [], "expiries": exps, "expiry": exp, "at": at}
    vol = df.groupby(["strike", "option_type"])["volume"].sum()
    last = df.groupby(["strike", "option_type"]).tail(1).set_index(["strike", "option_type"])
    rows = []
    for k in sorted(last.index.get_level_values(0).unique()):
        row = {"strike": float(k)}
        for typ in ("CE", "PE"):
            if (k, typ) in last.index:
                r = last.loc[(k, typ)]
                row[typ.lower()] = {"close": MP.jsonable(float(r["close"])), "oi": int(r["oi"]),
                                    "volume": int(vol.get((k, typ), 0)), "iv": MP.jsonable(float(r["iv"])) if pd.notna(r["iv"]) else None,
                                    "time": str(r["timestamp"])[11:16]}
        rows.append(row)
    spot = df["spot"].dropna()
    return {"status": "ok", "expiries": exps, "expiry": exp, "at": at, "day": s,
            "spot": float(spot.iloc[-1]) if len(spot) else None, "rows": rows}


# ── Data Downloader bridge ───────────────────────────────────────────
def downloader_datasets(user_id: int) -> list[dict]:
    from core.database import get_db_session
    from core.models import DataDataset
    db = get_db_session()
    try:
        rows = (db.query(DataDataset).filter(DataDataset.user_id == user_id, DataDataset.file_path.isnot(None))
                .order_by(DataDataset.id.desc()).limit(200).all())
        out = []
        for r in rows:
            p = Path(r.file_path)
            out.append({
                "id": r.id, "symbol": r.symbol, "exchange": r.exchange, "instrument_type": r.instrument_type,
                "expiry": r.expiry, "strike": float(r.strike) if r.strike is not None else None,
                "option_type": r.option_type, "interval": r.interval,
                "from_date": r.from_date.isoformat() if r.from_date else None,
                "to_date": r.to_date.isoformat() if r.to_date else None,
                "rows": r.rows, "status": r.status, "file_exists": p.exists(),
                "suggested_kind": "options" if (r.option_type or "").upper() in ("CE", "PE") else "spot",
                "suggested_underlying": MP.underlying_from_text(r.symbol or ""),
            })
        return out
    finally:
        db.close()


def stage_from_downloader(dataset_id: int, user_id: int) -> dict:
    from core.database import get_db_session
    from core.models import DataDataset
    db = get_db_session()
    try:
        r = db.query(DataDataset).filter(DataDataset.id == int(dataset_id), DataDataset.user_id == user_id).first()
        if r is None or not r.file_path:
            raise FileNotFoundError("dataset not found")
        src = Path(r.file_path)
        if not src.exists():
            raise FileNotFoundError("the dataset file is no longer on this server — download it again")
        name = f"downloader_{r.id}_{src.name}"
    finally:
        db.close()
    return stage_copy(src, name, user_id, source=f"data-downloader #{dataset_id}")
