"""
VWAP series builder + trigger detectors — PURE and look-ahead safe.

Reuses the Pine-faithful accumulators already in ``research/prev_period_vwap``
(session / previous-day / previous-week / previous-month) and adds:

  • ``_RollingVWAP(n_days)`` — trailing N-day VWAP (15-day, 90-day, any N)
  • ``crossed_down`` / ``touched`` / ``crossed_any`` detectors

Every value at bar *i* is derived only from bars ≤ *i*, so the identical series
is correct for a backtest cutoff, a replay clock and a live tick.

Volume note: the NIFTY **index** carries no traded volume. In ``index`` mode a
weight of 1.0 per bar is used, which mathematically reduces the result to an
HLC3 *average price* — the UI must label it as such. In ``futures`` mode real
contract volume is used and the result is a true VWAP.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from research.prev_period_vwap import (      # reuse — do not re-implement
    _PeriodVWAP, _SessionVWAP, _candle_dt, _day_key, _hlc3, _month_key, _week_key,
    crossed_up,
)
from research.vwap_options.config import ROLLING_DAYS, VWAP_LINES

__all__ = ["build_series", "crossed_up", "crossed_down", "touched", "event_fired",
           "_RollingVWAP"]


class _RollingVWAP:
    """Trailing N-day VWAP: the last N completed sessions plus the running day.

    Drifts intraday (like an anchored VWAP), which is the intended behaviour for
    a 'last 15 / 90 day VWAP' reference line.
    """

    __slots__ = ("n", "cur_key", "cur_pv", "cur_vol", "hist", "sum_pv", "sum_vol")

    def __init__(self, n_days: int):
        self.n = max(1, int(n_days))
        self.cur_key = None
        self.cur_pv = 0.0
        self.cur_vol = 0.0
        self.hist: deque = deque()
        self.sum_pv = 0.0
        self.sum_vol = 0.0

    def seed(self, pairs) -> None:
        """Prefill with completed sessions as (price*vol, vol) pairs, oldest first.

        Lets a long window (15/90-day) be primed from cheap DAILY candles instead
        of fetching months of intraday bars just to warm the line up.
        """
        for pv, vol in list(pairs)[-self.n:]:
            self.hist.append((float(pv), float(vol)))
            self.sum_pv += float(pv)
            self.sum_vol += float(vol)
        while len(self.hist) > self.n:
            p, v = self.hist.popleft()
            self.sum_pv -= p
            self.sum_vol -= v

    def update(self, dt, hlc3: float, vol: float) -> Optional[float]:
        key = _day_key(dt)
        if key != self.cur_key:
            if self.cur_key is not None:                  # roll the completed day in
                self.hist.append((self.cur_pv, self.cur_vol))
                self.sum_pv += self.cur_pv
                self.sum_vol += self.cur_vol
                while len(self.hist) > self.n:            # drop days beyond the window
                    p, v = self.hist.popleft()
                    self.sum_pv -= p
                    self.sum_vol -= v
            self.cur_key = key
            self.cur_pv = 0.0
            self.cur_vol = 0.0
        self.cur_pv += hlc3 * vol
        self.cur_vol += vol
        if len(self.hist) < self.n:       # not enough completed sessions yet —
            return None                   # report nothing rather than a short-window value
        pv, v = self.sum_pv + self.cur_pv, self.sum_vol + self.cur_vol
        return (pv / v) if v > 0 else None


def _r(v: Optional[float]) -> Optional[float]:
    return round(v, 2) if v is not None else None


def build_series(candles: list[dict], vwap_source: str = "futures",
                 seed_days=None) -> list[dict]:
    """Per-bar dict of every selectable VWAP line, aligned 1:1 with ``candles``.

    ``candles`` must be chronological and carry high/low/close/volume + a datetime.
    ``seed_days`` primes the rolling lines with completed sessions BEFORE the first
    candle, as (price*vol, vol) pairs oldest-first — must not overlap ``candles``.
    """
    session = _SessionVWAP()
    day = _PeriodVWAP(_day_key)
    week = _PeriodVWAP(_week_key)
    month = _PeriodVWAP(_month_key)
    rollers = {k: _RollingVWAP(n) for k, n in ROLLING_DAYS.items()}
    if seed_days:
        for roller in rollers.values():
            roller.seed(seed_days)
    use_volume = vwap_source == "futures"

    out: list[dict] = []
    for c in candles:
        dt = _candle_dt(c)
        if dt is None:
            out.append({k: None for k in VWAP_LINES})
            continue
        hlc3 = _hlc3(c)
        vol = float(c.get("volume", 0) or 0) if use_volume else 1.0
        if use_volume and vol <= 0:
            vol = 1.0                       # degenerate bar — keep the series continuous
        row = {
            "day_vwap": _r(session.update(dt, hlc3, vol)),
            "prev_day_vwap": _r(day.update(dt, hlc3, vol)),
            "prev_week_vwap": _r(week.update(dt, hlc3, vol)),
            "prev_month_vwap": _r(month.update(dt, hlc3, vol)),
        }
        for k, roller in rollers.items():
            row[k] = _r(roller.update(dt, hlc3, vol))
        out.append(row)
    return out


# ── trigger detectors (Pine crossover/crossunder semantics) ──────────────────
def crossed_down(prev_close: Optional[float], cur_low: float,
                 level: Optional[float], buffer: float = 0.0) -> bool:
    """True when a bar reaches ``level`` from ABOVE: previous bar closed above the
    level and this bar's low touches it (mirror of ``crossed_up``)."""
    if level is None or prev_close is None:
        return False
    lvl = level + buffer
    return prev_close > lvl >= cur_low


def touched(cur_high: float, cur_low: float, level: Optional[float],
            buffer: float = 0.0) -> bool:
    """True when the bar's range comes within ``buffer`` points of the level —
    direction-agnostic, captures an intrabar tag exactly as a live tick would."""
    if level is None:
        return False
    return (cur_low - buffer) <= level <= (cur_high + buffer)


def event_fired(event: str, prev_close: Optional[float], bar: dict,
                level: Optional[float], buffer: float = 0.0) -> bool:
    """Dispatch one configured event against one VWAP level for one bar."""
    if level is None:
        return False
    hi, lo = float(bar["high"]), float(bar["low"])
    if event == "touch":
        return touched(hi, lo, level, buffer)
    if event == "cross_up":
        return crossed_up(prev_close, hi, float(bar["close"]), level, buffer)
    if event == "cross_down":
        return crossed_down(prev_close, lo, level, buffer)
    if event == "cross_any":
        return (crossed_up(prev_close, hi, float(bar["close"]), level, buffer)
                or crossed_down(prev_close, lo, level, buffer))
    return False
