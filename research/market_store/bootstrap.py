"""
One-time load of existing history into the Market Store.

    python -m research.market_store.bootstrap --spot  "D:/.../NIFTY_spot_1m.csv" \
                                              --options "D:/.../Options Data"

Reads one file at a time so memory stays flat, merges each into the store (the
rolling ATM±k files overlap heavily; the store keeps one row per real contract per
minute), then rebuilds the database catalog. Safe to re-run: it is idempotent.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

from research.market_store import service as SV  # noqa: E402
from research.market_store import store as MS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot", default="")
    ap.add_argument("--options", default="", help="folder searched recursively for .parquet/.csv")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--no-catalog", action="store_true", help="skip database writes")
    a = ap.parse_args()
    t0 = time.time()

    if a.spot:
        df = pd.read_parquet(a.spot) if a.spot.lower().endswith(".parquet") else pd.read_csv(a.spot)
        r = SV.ingest_frame(df, filename=Path(a.spot).name, kind="spot",
                            underlying=a.underlying, catalog=False)
        print(f"spot   {Path(a.spot).name}: {r.get('rows_added')} rows added "
              f"({r.get('report', {}).get('sessions')} sessions)", flush=True)

    if a.options:
        files = sorted(p for p in Path(a.options).rglob("*") if p.suffix.lower() in (".parquet", ".csv"))
        for i, f in enumerate(files, 1):
            df = pd.read_parquet(f) if f.suffix.lower() == ".parquet" else pd.read_csv(f)
            r = SV.ingest_frame(df, filename=f.name, kind="options",
                                underlying=a.underlying, catalog=False)
            rep = r.get("report", {})
            print(f"[{i}/{len(files)}] {f.name}: +{r.get('rows_added')} new rows "
                  f"(in {rep.get('rows_in')}, bad {rep.get('bad_ohlc')}, "
                  f"outside-session {rep.get('outside_session')}) {round(time.time()-t0)}s", flush=True)

    s = MS.summary()
    print("\nSTORE:", s, flush=True)
    if not a.no_catalog:
        try:
            n = SV.rebuild_catalog()
            print(f"catalog rebuilt: {n} partitions", flush=True)
        except Exception as exc:
            print(f"catalog skipped (database unreachable here): {exc}", flush=True)
    print(f"done in {round(time.time()-t0)}s", flush=True)


if __name__ == "__main__":
    main()
