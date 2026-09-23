"""
Hunter — scan snapshots on disk.

One Parquet per scan date under ``data/hunter``, so yesterday's board is still there to compare
against and the "what changed since last close" feed is a real diff, not a guess.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.hunter.store")

ROOT = Path(MS.ROOT).parent / "hunter"
KEEP = 60                       # scans to keep on disk
JSON_COLS = ("trend_checks", "base", "breakout", "day", "first_breakout", "screens")


def _path(d: str) -> Path:
    return ROOT / f"scan_{d}.parquet"


def save(rows: list[dict], meta: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    d = meta.get("scan_date") or date.today().isoformat()
    df = pd.DataFrame(rows)
    for col in JSON_COLS:
        if col in df.columns:
            df[col] = df[col].map(lambda v: json.dumps(v, default=str))
    df.to_parquet(_path(d), index=False)
    (ROOT / f"scan_{d}.json").write_text(json.dumps(meta, default=str))
    for old in sorted(ROOT.glob("scan_*.parquet"))[:-KEEP]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)


def dates() -> list[str]:
    return sorted(p.stem.replace("scan_", "") for p in ROOT.glob("scan_*.parquet"))


def load(d: Optional[str] = None) -> tuple[list[dict], dict]:
    """A scan by date, or the latest one. Returns ([], {}) when nothing is stored."""
    ds = dates()
    if not ds:
        return [], {}
    d = d or ds[-1]
    if d not in ds:
        return [], {}
    df = pd.read_parquet(_path(d))
    for col in JSON_COLS:
        if col in df.columns:
            df[col] = df[col].map(lambda v: json.loads(v) if isinstance(v, str) else v)
    for col in df.columns:                      # parquet hands list columns back as numpy arrays
        if df[col].dtype == object:
            df[col] = df[col].map(lambda v: list(v) if isinstance(v, np.ndarray) else v)
    try:
        meta = json.loads((ROOT / f"scan_{d}.json").read_text())
    except Exception:
        meta = {"scan_date": d}
    return df.to_dict("records"), meta


def previous(before: str) -> tuple[list[dict], dict]:
    earlier = [d for d in dates() if d < before]
    return load(earlier[-1]) if earlier else ([], {})
