"""
Shared Prev-Period VWAP engine — an exact port of the TradingView Pine script
"Daily + Prev Day/Week/Month VWAP".

Two research modules build on this:
  • Previous-Month-VWAP Straddle Research (options)
  • Previous-Month-VWAP Equity-Holding Research (cash)

It is a **pure** library — no broker calls, no I/O, no global state — so it is
trivially unit-testable and produces the same numbers as TradingView bar-for-bar.

Pine reference (accumulate hlc3·volume, reset on each new period, and expose the
*previous* completed period's VWAP as a flat step until the next boundary):

    newX = ta.change(time("X"))
    if newX:
        prevXVWAP := xPV / xVol      # the period that just finished
        xPV := hlc3*volume           # start the new period on this bar
        xVol := volume
    else:
        xPV += hlc3*volume
        xVol += volume

The "current daily VWAP" (``ta.vwap(hlc3)``) is session-anchored: it resets each
new day and accumulates within the day.

Boundary keys (match TradingView's exchange calendar for NSE):
  • Day   → calendar date
  • Week  → ISO (year, week)   [weeks start Monday]
  • Month → (year, month)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def _candle_dt(c: dict) -> Optional[datetime]:
    """Accept Kite candles ('date' key, tz-aware) or pre-parsed datetimes."""
    dt = c.get("date") if isinstance(c, dict) else None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None)
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day)
    return None


def _hlc3(c: dict) -> float:
    return (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0


def _day_key(dt: datetime):
    return dt.date()


def _week_key(dt: datetime):
    iso = dt.isocalendar()
    return (iso[0], iso[1])          # (ISO year, ISO week) — Monday-anchored


def _month_key(dt: datetime):
    return (dt.year, dt.month)


class _PeriodVWAP:
    """One period accumulator replicating the Pine ``var`` + reset pattern."""

    __slots__ = ("_key_fn", "cur", "pv", "vol", "prev")

    def __init__(self, key_fn):
        self._key_fn = key_fn
        self.cur = None
        self.pv = 0.0
        self.vol = 0.0
        self.prev: Optional[float] = None      # previous completed period's VWAP

    def update(self, dt: datetime, hlc3: float, vol: float) -> Optional[float]:
        key = self._key_fn(dt)
        if self.cur is None:                   # first bar → begin accumulating
            self.cur = key
        if key != self.cur:                    # new period boundary (ta.change)
            self.prev = (self.pv / self.vol) if self.vol > 0 else None
            self.pv = hlc3 * vol
            self.vol = vol
            self.cur = key
        else:
            self.pv += hlc3 * vol
            self.vol += vol
        return self.prev


class _SessionVWAP:
    """Current-day session-anchored VWAP (``ta.vwap`` — resets each new day)."""

    __slots__ = ("cur", "pv", "vol")

    def __init__(self):
        self.cur = None
        self.pv = 0.0
        self.vol = 0.0

    def update(self, dt: datetime, hlc3: float, vol: float) -> Optional[float]:
        key = _day_key(dt)
        if key != self.cur:
            self.cur = key
            self.pv = 0.0
            self.vol = 0.0
        self.pv += hlc3 * vol
        self.vol += vol
        return (self.pv / self.vol) if self.vol > 0 else None


def compute_prev_period_vwaps(candles: list[dict]) -> list[dict]:
    """Return, for each candle (chronological order), the four VWAP series:

        {"daily_vwap", "prev_day_vwap", "prev_week_vwap", "prev_month_vwap"}

    Values are ``None`` until the corresponding previous period has completed
    (mirrors Pine's ``na`` before the first boundary crossing). Input candles
    must be sorted oldest → newest and carry high/low/close/volume + a datetime.
    """
    session = _SessionVWAP()
    day = _PeriodVWAP(_day_key)
    week = _PeriodVWAP(_week_key)
    month = _PeriodVWAP(_month_key)

    out: list[dict] = []
    for c in candles:
        dt = _candle_dt(c)
        if dt is None:
            out.append({"daily_vwap": None, "prev_day_vwap": None,
                        "prev_week_vwap": None, "prev_month_vwap": None})
            continue
        hlc3 = _hlc3(c)
        vol = float(c.get("volume", 0) or 0)
        out.append({
            "daily_vwap": _round(session.update(dt, hlc3, vol)),
            "prev_day_vwap": _round(day.update(dt, hlc3, vol)),
            "prev_week_vwap": _round(week.update(dt, hlc3, vol)),
            "prev_month_vwap": _round(month.update(dt, hlc3, vol)),
        })
    return out


def _round(v: Optional[float]) -> Optional[float]:
    return round(v, 2) if v is not None else None


# ── Crossing detectors (Pine crossover/crossunder semantics) ──────────────

def daily_gap_map(candles: list[dict]) -> dict:
    """Per-day open-gap % = (day open − previous day's close) / prev close × 100.

    Used by the research Summary Report for gap-up vs gap-down analysis. Keyed
    by ``date``; the first session in the set has no prior close → omitted.
    """
    first_open: dict = {}
    last_close: dict = {}
    order: list = []
    for c in candles:
        dt = _candle_dt(c)
        if dt is None:
            continue
        d = dt.date()
        if d not in first_open:
            first_open[d] = float(c["open"])
            order.append(d)
        last_close[d] = float(c["close"])
    gaps: dict = {}
    for i in range(1, len(order)):
        prev_c = last_close.get(order[i - 1])
        op = first_open.get(order[i])
        if prev_c:
            gaps[order[i]] = round((op - prev_c) / prev_c * 100.0, 2)
    return gaps


def crossed_up(prev_close: Optional[float], cur_high: float, cur_close: float,
               level: Optional[float], buffer: float = 0.0) -> bool:
    """True when a bar touches/crosses ``level`` from BELOW.

    Matches the spec's entry: previous candle closed below the level and the
    current candle reaches it (high ≥ level within an optional buffer). Using
    high (not just close) captures an intrabar touch, like a live tick would.
    """
    if level is None or prev_close is None:
        return False
    lvl = level - buffer
    return prev_close < lvl <= cur_high
