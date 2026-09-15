# Market Store

Historical NIFTY spot and option minute bars, shared by every Options Lab backtest.

- **Data** lives on disk as zstd-compressed Parquet, one file per month:
  `kind=options/underlying=NIFTY/year=2025/month=09/part-<hash>.parquet`
- **Catalog** lives in PostgreSQL: `market_store_partitions` (what exists) and
  `market_store_ingests` (who uploaded what). Both tables are created on server start.

## Invariants

- One row per `(timestamp, contract)` for options, one per `timestamp` for spot.
  Re-uploading overlapping files adds only the missing rows.
- Contracts are identified by their real name. The rolling `ATM±k` files are
  exploded back into the contracts they were built from.
- An append rewrites only the months it touches.

## Where the data lives

| Copy | Location | Role |
|---|---|---|
| Disk | `data/market_store/` (or `MARKET_STORE_DIR`) | what backtests scan; git- and docker-ignored |
| Database | `market_store_blobs` (one row per month, the same Parquet bytes) | survives redeploys |

Railway wipes the container disk on every deploy; the Postgres volume survives. So
every month written by an upload is also saved to `market_store_blobs`
(`durable.py`). On a fresh server the first Data-tab visit or backtest restores the
missing months from the database onto disk. The Data tab shows "Restoring…" meanwhile.
Months found on disk but missing from the database are pushed up automatically.
No Railway volume is required. Set `MARKET_STORE_DB_SYNC=0` to turn the database copy off.

## Bundled history (no upload needed)

`seed/market_store/` in the repo holds the 3-year NIFTY spot, NIFTY options and India VIX
store (same layout, ~120 MB). A fresh server copies every month it lacks from there on the
first Data-tab visit or backtest, then saves those months to `market_store_blobs` in the
background. Months already on disk are never overwritten by the seed, and a newer copy of a
month in the database (e.g. after an upload merged new rows) replaces the seed copy.
Override the folder with `MARKET_STORE_SEED_DIR`.

To refresh the bundle after adding data locally, copy `data/market_store` over
`seed/market_store` and commit.

## Loading 3 years of history into Railway (once)

Uploading the 22 rolling option files through the UI works, but each one touches
every month, so the database copy is rewritten 22 times. Push an already-built local
store instead:

```bash
# 1. build the local store (skip if data/market_store already has it)
python -m research.market_store.bootstrap --spot "NIFTY_spot_1m.csv" --options "Options Data" --no-catalog

# 2. push it — Railway → Postgres → Connect → "Public Network" connection URL
DATABASE_URL="postgresql://..." python -m research.market_store.durable push
```

`push` is idempotent: months already in the database with the same checksum are skipped.
`python -m research.market_store.durable status` shows what the database holds.

## Loading history locally

```bash
python -m research.market_store.bootstrap \
  --spot "path/to/NIFTY_spot_1m.csv" \
  --options "path/to/Options Data"
```

Idempotent: safe to re-run. It rebuilds the database catalog at the end when the
database is reachable.

## Known limit of the current dataset

Only strikes within ±5 of the money at each minute are present. When NIFTY moves,
far legs drift out of that window and stop printing. Backtests price those
unobserved legs adversely by default; downloading ±10 or more strikes removes the
problem for wide structures.
