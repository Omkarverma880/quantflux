"""Market Store — compressed, partitioned history of index spot and option candles.

Shared by every backtest and research module. Candles on disk (zstd Parquet,
Hive-partitioned by month); the catalog in PostgreSQL. Append-only, idempotent.
"""
from research.market_store.store import (  # noqa: F401
    ROOT, append, normalize_options, normalize_spot, read, summary,
)
