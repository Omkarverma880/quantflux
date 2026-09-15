"""
Durable copy of the Market Store in PostgreSQL.

Parquet files on local disk are what backtests scan, but on Railway the container disk
is wiped on every deploy while the Postgres volume survives. So every monthly partition
file is also stored, byte for byte, in ``market_store_blobs``:

  * after an upload, each rewritten month is saved to the database (``save``)
  * before a read, months that exist in the database but not on disk are restored
    (``hydrate``) — a fresh container rebuilds its disk copy on first use
  * months on disk that the database lacks are pushed up, so a store populated before
    this module existed heals itself the first time a server with a database sees it

Command line (run once from a machine that has the data):

    DATABASE_URL=<railway public postgres url> python -m research.market_store.durable push
    python -m research.market_store.durable status
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.market_store.durable")

_lock = threading.Lock()
_seed_lock = threading.Lock()
_state = {"checked_at": 0.0, "fail_until": 0.0, "thread": None, "syncing": False, "seeded": False}

# History bundled with the code (repo folder seed/market_store, same layout as the store).
# A fresh server copies any month it lacks from here, so the app has data with no upload.
SEED_ROOT = Path(os.environ.get("MARKET_STORE_SEED_DIR")
                 or (Path(__file__).resolve().parents[2] / "seed" / "market_store"))
RECHECK_SECONDS = 60
FAIL_BACKOFF_SECONDS = 300


def enabled() -> bool:
    return os.environ.get("MARKET_STORE_DB_SYNC", "1") != "0"


def _session():
    from core.database import get_db_session
    return get_db_session()


def _local_partitions() -> dict:
    return _partitions_under(MS.ROOT)


def seed_disk() -> int:
    """Copy bundled seed months that are missing on disk. Runs once per process, needs no DB.

    Months already on disk are never touched; a newer copy of a month kept in the database
    replaces the seed copy on the next ``hydrate``."""
    if _state["seeded"]:
        return 0
    with _seed_lock:
        if _state["seeded"]:
            return 0
        n = 0
        try:
            if SEED_ROOT.exists() and SEED_ROOT.resolve() != MS.ROOT.resolve():
                local = _local_partitions()
                for key, f in sorted(_partitions_under(SEED_ROOT).items()):
                    if key in local:
                        continue
                    pdir = MS._part_dir(*key)
                    pdir.mkdir(parents=True, exist_ok=True)
                    tmp = pdir / (f.name + ".tmp")
                    shutil.copy2(f, tmp)
                    os.replace(tmp, pdir / f.name)
                    n += 1
            if n:
                logger.info("market store: loaded %d month file(s) from the bundled seed %s", n, SEED_ROOT)
        except Exception as exc:
            logger.error("market store: loading the bundled seed failed: %s", exc)
        _state["seeded"] = True
        return n


def _partitions_under(root: Path) -> dict:
    out = {}
    for kind in ("spot", "options"):
        base = root / f"kind={kind}"
        if not base.exists():
            continue
        for f in base.rglob("*.parquet"):
            und = f.parent.parent.parent.name.split("=")[1]
            y = int(f.parent.parent.name.split("=")[1]); mo = int(f.parent.name.split("=")[1])
            out[(kind, und, y, mo)] = f
    return out


def _db_partitions(db) -> dict:
    from core.models import MarketStoreBlob as B
    rows = db.query(B.kind, B.underlying, B.year, B.month, B.checksum, B.rows, B.bytes).all()
    return {(r.kind, r.underlying, r.year, r.month): r for r in rows}


def _upsert(db, kind, underlying, year, month, path: Path) -> bool:
    """Store one partition file. Returns False when the database already has it."""
    import pyarrow.parquet as pq
    from core.models import MarketStoreBlob as B
    checksum = path.stem.replace("part-", "")
    row = (db.query(B).filter(B.kind == kind, B.underlying == underlying,
                              B.year == year, B.month == month).first())
    if row is not None and row.checksum == checksum:
        return False
    data = path.read_bytes()
    if row is None:
        row = B(kind=kind, underlying=underlying, year=year, month=month)
        db.add(row)
    row.checksum = checksum
    row.rows = int(pq.ParquetFile(path).metadata.num_rows)
    row.bytes = len(data)
    row.data = data
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return True


def save(records: list[dict]) -> dict:
    """Persist the partitions an append just wrote. Never raises."""
    if not records or not enabled():
        return {"saved": 0, "ok": True}
    db = None
    try:
        db = _session()
        n = 0
        for r in records:
            n += _upsert(db, r["kind"], r["underlying"], r["year"], r["month"], Path(r["path"]))
        return {"saved": n, "ok": True}
    except Exception as exc:
        if db is not None:
            db.rollback()
        logger.error("market store: saving partitions to the database failed: %s", exc)
        return {"saved": 0, "ok": False, "error": str(exc)[:300]}
    finally:
        if db is not None:
            db.close()


def _restore(db, key, want_checksum) -> None:
    from core.models import MarketStoreBlob as B
    kind, und, y, mo = key
    data = (db.query(B.data).filter(B.kind == kind, B.underlying == und,
                                    B.year == y, B.month == mo).scalar())
    if not data:
        return
    pdir = MS._part_dir(kind, und, y, mo)
    pdir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="mstore_restore_")) / "part.parquet"
    tmp.write_bytes(bytes(data))
    final = pdir / f"part-{want_checksum[:16]}.parquet"
    shutil.move(str(tmp), final)
    shutil.rmtree(tmp.parent, ignore_errors=True)
    for f in pdir.glob("*.parquet"):
        if f != final:
            f.unlink()


def hydrate(force: bool = False, push: bool = True) -> dict:
    """Make disk and database agree. Cheap when they already do (metadata only).

    ``push=False`` (used inside a backtest request) only restores newer months from the
    database; uploading disk-only months is left to the background run."""
    seed_disk()
    if not enabled():
        return {"skipped": "disabled"}
    now = time.time()
    if not force and (now < _state["fail_until"] or now - _state["checked_at"] < RECHECK_SECONDS):
        return {"skipped": "recent"}
    with _lock:
        if not force and time.time() - _state["checked_at"] < RECHECK_SECONDS:
            return {"skipped": "recent"}
        db = None
        _state["syncing"] = True
        try:
            db = _session()
            remote = _db_partitions(db)
            local = _local_partitions()
            restored = pushed = 0
            for key, r in remote.items():
                f = local.get(key)
                if f is None or f.stem.replace("part-", "") != r.checksum[:16]:
                    _restore(db, key, r.checksum)
                    restored += 1
            if not push:
                return {"restored": restored, "pushed": 0, "db_months": len(remote)}
            for key, f in local.items():
                if key not in remote:
                    pushed += _upsert(db, *key, f)
            _state["checked_at"] = time.time()
            if restored or pushed:
                logger.info("market store sync: restored %d month(s) from DB, pushed %d to DB", restored, pushed)
                try:
                    from research.market_store import service as SV
                    SV.rebuild_catalog()
                except Exception as exc:
                    logger.warning("market store catalog rebuild after sync failed: %s", exc)
            return {"restored": restored, "pushed": pushed, "db_months": len(remote)}
        except Exception as exc:
            if db is not None:
                db.rollback()
            _state["fail_until"] = time.time() + FAIL_BACKOFF_SECONDS
            logger.warning("market store sync unavailable (retry in %ds): %s", FAIL_BACKOFF_SECONDS, exc)
            return {"error": str(exc)[:300]}
        finally:
            _state["syncing"] = False
            if db is not None:
                db.close()


def hydrate_in_background() -> None:
    t = _state["thread"]
    if t is not None and t.is_alive():
        return
    t = threading.Thread(target=hydrate, name="market-store-hydrate", daemon=True)
    _state["thread"] = t
    t.start()


def is_syncing() -> bool:
    t = _state["thread"]
    return bool(_state["syncing"] or (t is not None and t.is_alive()))


def db_summary() -> dict | None:
    """Store summary from database metadata alone (used while disk is being restored)."""
    if not enabled() or time.time() < _state["fail_until"]:
        return None
    db = None
    try:
        db = _session()
        remote = _db_partitions(db)
    except Exception:
        return None
    finally:
        if db is not None:
            db.close()
    return MS.summarize_entries([(k[0], k[1], k[2], k[3], r.rows or 0, r.bytes or 0)
                                 for k, r in remote.items()])


def _cli(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    from core.database import Base, engine
    from core.models import MarketStoreBlob, MarketStorePartition  # noqa: F401
    Base.metadata.create_all(bind=engine, tables=[MarketStoreBlob.__table__, MarketStorePartition.__table__])
    if cmd == "push":
        local = _local_partitions()
        print(f"local store {MS.ROOT}: {len(local)} month file(s)")
        db = _session()
        try:
            for i, (key, f) in enumerate(sorted(local.items())):
                changed = _upsert(db, *key, f)
                print(f"  [{i+1}/{len(local)}] {key[0]:7s} {key[1]} {key[2]}-{key[3]:02d} "
                      f"{f.stat().st_size/1e6:6.1f} MB  {'uploaded' if changed else 'already in DB'}", flush=True)
        finally:
            db.close()
        from research.market_store import service as SV
        print(f"catalog rows rebuilt: {SV.rebuild_catalog()}")
    elif cmd == "pull":
        print(hydrate(force=True))
    s = db_summary() or {}
    for kind, v in s.items():
        for und, u in v.get("by_underlying", {}).items():
            print(f"database has {kind} {und}: {u['months']} months, {u['rows']:,} rows, {u['bytes']/1e6:.1f} MB "
                  f"({u['first_month']} .. {u['last_month']})")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
