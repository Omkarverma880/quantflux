"""
Gann level grid — the same ``gann_levels.csv`` Strategy 1 and Strategy 5 trade from.

The grid is a square-of-nine style series (odd squares and even squares + 1: 5, 9, 17,
25, 37, 49 …). Spacing grows with price, so one grid serves both jobs:

  on SPOT     levels ~300 pts apart near NIFTY 23,000 and ~550 near SENSEX 75,000 —
              swing ranges: a close through a level is a breakout, a failed test a rejection
  on PREMIUM  levels ~20 apart near ₹120 — scalping steps: target the next level up,
              stop under the level below (Strategy 1's floor/ceil logic)
"""
from __future__ import annotations

import bisect
from functools import lru_cache
from pathlib import Path

GANN_CSV = Path(__file__).resolve().parents[2] / "gann_levels.csv"


@lru_cache(maxsize=1)
def levels() -> tuple[float, ...]:
    vals = []
    if GANN_CSV.exists():
        for line in GANN_CSV.read_text().split():
            try:
                vals.append(float(line.strip().split(",")[0]))
            except ValueError:
                continue
    return tuple(sorted(set(v for v in vals if v > 0)))


def floor(x: float) -> float:
    """Largest level ≤ x (the first level when x is below the grid)."""
    L = levels()
    i = bisect.bisect_right(L, x) - 1
    return L[max(i, 0)] if L else x


def ceil(x: float) -> float:
    """Smallest level strictly above x."""
    L = levels()
    i = bisect.bisect_right(L, x)
    return L[i] if L and i < len(L) else (L[-1] if L else x)


def below(x: float) -> float:
    """Largest level strictly below x."""
    L = levels()
    i = bisect.bisect_left(L, x) - 1
    return L[max(i, 0)] if L else x


def crossed(prev: float, now: float) -> list[float]:
    """Levels crossed moving from ``prev`` to ``now`` (ascending), exclusive of prev, inclusive of now."""
    L = levels()
    lo, hi = sorted((prev, now))
    i, j = bisect.bisect_right(L, lo), bisect.bisect_right(L, hi)
    return list(L[i:j])


def ladder(x: float, n: int = 4) -> list[float]:
    """n levels below and n above x."""
    L = levels()
    i = bisect.bisect_right(L, x)
    return list(L[max(0, i - n): i + n])


def premium_plan(entry: float, min_gap_pct: float = 3.0) -> dict:
    """Scalp targets and stop on the premium grid (Strategy 1 style).

    Target 1 = the next level up, target 2 = the one after; stop = the level under the entry's
    floor. Levels closer than ``min_gap_pct`` to the entry are skipped (a 1-tick target is noise)."""
    t1 = ceil(entry)
    if (t1 - entry) / entry * 100 < min_gap_pct:
        t1 = ceil(t1)
    t2 = ceil(t1)
    sl = floor(entry)
    if (entry - sl) / entry * 100 < min_gap_pct:
        sl = below(sl)
    return {"entry": round(entry, 2), "target1": t1, "target2": t2, "stop": sl}
