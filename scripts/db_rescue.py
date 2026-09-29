"""
Emergency room for a PostgreSQL volume that has filled up.

Run it from anywhere that can reach the database — your own machine works, using the public
connection string Railway shows under the Postgres service:

    DATABASE_URL="postgresql://user:pass@host:port/railway" python scripts/db_rescue.py
    DATABASE_URL="..." python scripts/db_rescue.py --free-market-store
    DATABASE_URL="..." python scripts/db_rescue.py --vacuum-full market_store_blobs

Nothing is deleted unless you ask for it: with no flags it only reports.

What is safe to delete, and why
  market_store_blobs   a byte-for-byte copy of the monthly Parquet files, kept so a wiped
                       container can rebuild its disk cache. Your own machine still has those
                       files (data/market_store) and the repo bundles the older months, so this
                       table can be emptied and re-pushed later with:
                           python -m research.market_store.durable push
  flux_lab_trades      stored backtest results; deleting a run's rows only loses that report.
  flux_lab_signals     the paper-trading audit log.

TRUNCATE returns the space to the volume immediately. DELETE does not — it leaves dead rows that
only VACUUM FULL reclaims, and VACUUM FULL needs room for a second copy of the table while it
runs, which a full volume may not have. On a completely full volume: raise the volume size in
Railway first (a restart is enough), then come back here.
"""
from __future__ import annotations

import argparse
import os
import sys

TABLES_SAFE_TO_EMPTY = {
    "market_store_blobs": "market data mirror — rebuildable from your local files",
    "flux_lab_signals": "paper-trading audit log",
}


def _engine():
    from sqlalchemy import create_engine
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set. Copy the public connection string from Railway → Postgres → Connect.")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):          # name the driver: 2.1 would otherwise pick psycopg 3
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url, pool_pre_ping=True)


def report(conn) -> None:
    from sqlalchemy import text
    total = conn.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar()
    print(f"\ndatabase total: {total}\n")
    rows = conn.execute(text("""
        SELECT c.relname AS name,
               pg_total_relation_size(c.oid) AS bytes,
               pg_size_pretty(pg_total_relation_size(c.oid)) AS pretty,
               COALESCE(s.n_live_tup, 0) AS live, COALESCE(s.n_dead_tup, 0) AS dead,
               COALESCE(s.last_autovacuum, s.last_vacuum) AS vacuumed
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 15""")).mappings().all()
    print(f"{'table':28s} {'size':>10s} {'live rows':>10s} {'dead rows':>10s}  last vacuum")
    for r in rows:
        note = "  ← safe to empty" if r["name"] in TABLES_SAFE_TO_EMPTY else ""
        print(f"{r['name']:28s} {r['pretty']:>10s} {r['live']:>10d} {r['dead']:>10d}  "
              f"{str(r['vacuumed'])[:19] if r['vacuumed'] else 'never'}{note}")
    print("\ndead rows are space already used but not yet returned — VACUUM reclaims it.")


def free_market_store(conn) -> None:
    from sqlalchemy import text
    before = conn.execute(text("SELECT pg_size_pretty(pg_total_relation_size('market_store_blobs'))")).scalar()
    kept = conn.execute(text("SELECT count(*) FROM market_store_blobs")).scalar()
    conn.execute(text("TRUNCATE TABLE market_store_blobs"))
    after = conn.execute(text("SELECT pg_size_pretty(pg_total_relation_size('market_store_blobs'))")).scalar()
    print(f"\nemptied market_store_blobs: {kept} month copies, {before} → {after}")
    print("re-upload them when the service is healthy:  python -m research.market_store.durable push")


def delete_before(conn, year: int) -> None:
    from sqlalchemy import text
    n = conn.execute(text("DELETE FROM market_store_blobs WHERE year < :y"), {"y": year}).rowcount
    print(f"\ndeleted {n} month copies from before {year}. Run --vacuum-full market_store_blobs to "
          "return that space to the volume.")


def vacuum(conn, table: str, full: bool) -> None:
    from sqlalchemy import text
    sql = f"VACUUM {'FULL ' if full else ''}ANALYZE {table}"
    print(f"\nrunning {sql} … (this can take minutes on a large table)")
    conn.execute(text(sql))
    size = conn.execute(text(f"SELECT pg_size_pretty(pg_total_relation_size('{table}'))")).scalar()
    print(f"done · {table} is now {size}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Report on, and reclaim, PostgreSQL volume space.")
    ap.add_argument("--free-market-store", action="store_true",
                    help="empty market_store_blobs (rebuildable from your local Parquet files)")
    ap.add_argument("--delete-before", type=int, metavar="YEAR",
                    help="delete market data copies from before this year")
    ap.add_argument("--vacuum", metavar="TABLE", help="reclaim dead space in a table for reuse")
    ap.add_argument("--vacuum-full", metavar="TABLE", help="return a table's dead space to the volume")
    args = ap.parse_args()

    eng = _engine()
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        report(conn)
        if args.delete_before:
            delete_before(conn, args.delete_before)
        if args.free_market_store:
            free_market_store(conn)
        if args.vacuum:
            vacuum(conn, args.vacuum, full=False)
        if args.vacuum_full:
            vacuum(conn, args.vacuum_full, full=True)
        if any([args.delete_before, args.free_market_store, args.vacuum, args.vacuum_full]):
            print("\nafter the change:")
            report(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
