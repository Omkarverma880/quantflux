"""
Hunter — daily bars for the whole universe.

Reuses the My Equity daily cache: one Parquet file per stock, and only the sessions after the last
cached bar are fetched. So the first scan is slow (one Zerodha call per stock) and every later one
is quick. Calls are spaced to stay inside Zerodha's historical-data rate limit.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Callable, Optional

import pandas as pd

from core.logger import get_logger
from research.my_equity import cache as CACHE

logger = get_logger("research.hunter.data")

CALL_SPACING_S = 0.34          # Zerodha allows ~3 historical calls a second
MIN_BARS = 220


def load_all(broker, stocks: list[dict], progress: Optional[Callable[[str], None]] = None,
             today: Optional[date] = None) -> tuple[dict, list[dict]]:
    """{symbol: daily bars} for every stock that has enough history, plus what was skipped."""
    say = progress or (lambda _m: None)
    bars, skipped = {}, []
    n = len(stocks)
    for i, s in enumerate(stocks, 1):
        if i % 25 == 0 or i == n:
            say(f"{i}/{n} stocks · {len(bars)} loaded")
        t0 = time.time()
        try:
            df = CACHE.daily(broker, s["symbol"], s["token"], s.get("exchange", "NSE"), today=today)
        except Exception as exc:
            skipped.append({"symbol": s["symbol"], "reason": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        if df is None or df.empty or len(df) < MIN_BARS:
            skipped.append({"symbol": s["symbol"], "reason": f"only {0 if df is None else len(df)} daily bars stored"})
            continue
        bars[s["symbol"]] = df
        wait = CALL_SPACING_S - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)
    return bars, skipped
