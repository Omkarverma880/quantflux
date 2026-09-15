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

| Environment | Location |
|---|---|
| Local | `data/market_store/` (git-ignored and docker-ignored) |
| Railway | set `MARKET_STORE_DIR` to a mounted volume, e.g. `/data/market_store` |

**Railway's container disk is wiped on every redeploy.** Without a volume the store
is empty after each deploy and history has to be uploaded again.

1. Railway → service → **Volumes** → add a volume mounted at `/data`.
2. Railway → **Variables** → `MARKET_STORE_DIR=/data/market_store`.
3. Redeploy, open **Index Strategies → Options Lab → Data**, upload the spot CSV and
   the option parquet files. Uploads are merged, so they can be sent in batches.

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
