"""
Constants for the Research → Data Downloader module.

A read-only historical-data downloader built on the EXISTING Zerodha Broker.
It never places orders. Interval limits mirror the documented Kite historical
API maximums (per single request) so the chunker never asks for more than Kite
allows.
"""
from __future__ import annotations

MODULE_ID = "data_downloader"

# ── Zerodha-supported historical intervals (UI label → Kite interval) ─────────
# Only intervals Kite serves directly are exposed — no silent derivation.
INTERVALS: dict[str, str] = {
    "1minute": "minute",
    "3minute": "3minute",
    "5minute": "5minute",
    "10minute": "10minute",
    "15minute": "15minute",
    "30minute": "30minute",
    "60minute": "60minute",
    "day": "day",
}
INTERVAL_LABELS: dict[str, str] = {
    "1minute": "1 Minute", "3minute": "3 Minute", "5minute": "5 Minute",
    "10minute": "10 Minute", "15minute": "15 Minute", "30minute": "30 Minute",
    "60minute": "60 Minute", "day": "Daily",
}

# Max span (days) Kite serves per single historical request, per interval.
# The chunker splits any wider range into <= these windows.
CHUNK_DAYS: dict[str, int] = {
    "minute": 60, "3minute": 100, "5minute": 100, "10minute": 100,
    "15minute": 200, "30minute": 200, "60minute": 400, "day": 2000,
}

# Instrument-type filter values exposed in the UI.
INSTRUMENT_TYPES = ["index", "equity", "futures", "options"]

# Exchanges / segments whose instrument dumps we index (all read-only).
EXCHANGES = ["NSE", "BSE", "NFO", "BFO"]

# Normalized dataset schema (order matters for file columns).
SCHEMA = [
    "timestamp", "symbol", "exchange", "instrument_token", "instrument_type",
    "expiry", "strike", "option_type", "interval",
    "open", "high", "low", "close", "volume", "oi",
]

# Job / dataset status vocabulary.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

FORMATS = ["parquet", "csv"]
