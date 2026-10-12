"""
NIFTY Futures Volume Breakout — the data layer, and what it refuses to pretend.

Every series this strategy wants is checked before it is used, because the single most damaging
thing a breakout system can do is produce a confident signal from a series that was stale,
misaligned, or not what it claimed to be.

What the store actually holds, verified rather than assumed:

    spot/NIFTY        739 sessions of one-minute OHLCV. It carries a ``volume`` column with
                      large non-zero values — but NIFTY 50 is an index calculation, not a
                      traded contract, and India VIX (also an index, same ingester) correctly
                      reports zero. Whatever that number is, it is NOT futures participation,
                      so it is quarantined: ``spot_volume_is_not_futures`` and nothing in the
                      volume engine may read it.
    spot/NIFTYFUT     ABSENT. The ingester supports it (``include_futures`` writes it under this
                      key) but it has never been pulled. Until it is, futures confirmation
                      resolves to UNAVAILABLE and every signal that required it is skipped and
                      counted, never silently waved through.
    options/NIFTY     739 sessions with real traded volume, OI and IV per contract. This is
                      genuine exchange volume, and it is offered as its OWN participation
                      source under its own name — never as a stand-in for futures.
    spot/INDIAVIX     916 sessions, for the optional volatility-regime filter.

The participation source is therefore an explicit choice with three honest outcomes, and the
backtest reports which one it actually used.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS

logger = get_logger("research.fvb.data")

IST = "Asia/Kolkata"
SESSION_OPEN = 9 * 60 + 15
SESSION_CLOSE = 15 * 60 + 30
LOT = 65
STRIKE_STEP = 50

# participation sources, in the order of what they actually measure
FUTURES = "futures"          # real futures volume — the thing the strategy was designed around
OPTIONS = "options"          # real option volume at the traded strikes — a different measure
NONE = "none"                # no participation filter at all (Variant A)

UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Quality:
    """What a session's data is fit for, decided before any signal is computed."""
    session: date
    spot_bars: int
    gaps: int
    ohlc_violations: int
    duplicates: int
    first_minute: Optional[int]
    last_minute: Optional[int]
    futures_bars: int
    futures_available: bool
    options_contracts: int
    vix_bars: int
    usable: bool
    reasons: tuple

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__annotations__}
        d["session"] = str(self.session)
        d["reasons"] = list(self.reasons)
        return d


def _minutes(ts: pd.Series) -> pd.Series:
    return ts.dt.hour * 60 + ts.dt.minute


def months(start: str, end: str) -> list[tuple[str, str]]:
    out, cur = [], pd.Timestamp(start).normalize().replace(day=1)
    stop = pd.Timestamp(end).normalize()
    while cur <= stop:
        nxt = cur + pd.offsets.MonthBegin(1)
        out.append((str(max(cur, pd.Timestamp(start)).date()),
                    str(min(nxt - pd.Timedelta(days=1), stop).date())))
        cur = nxt
    return out


def load_spot(start: str, end: str) -> pd.DataFrame:
    """NIFTY index one-minute bars. The volume column is dropped on the way in.

    Dropping it is deliberate and not merely cautious: leaving a plausible-looking ``volume``
    on the index frame is an invitation for some later line of code to divide by it and call
    the result participation.
    """
    d = MS.read("spot", "NIFTY", start=start, end=end,
                columns=["timestamp", "open", "high", "low", "close", "volume"])
    if d is None or not len(d):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "minute", "date"])
    d = d.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values("timestamp").drop_duplicates("timestamp")
    d["index_volume_unusable"] = d.pop("volume")     # kept, renamed so it cannot be mistaken
    d["minute"] = _minutes(d["timestamp"])
    d["date"] = d["timestamp"].dt.date
    return d.reset_index(drop=True)


def load_futures(start: str, end: str) -> tuple[pd.DataFrame, Optional[str]]:
    """NIFTY futures one-minute bars with real traded volume, if they have ever been pulled.

    Returns an empty frame and a reason rather than raising, so a backtest can run the variants
    that do not need futures and report the rest as blocked.
    """
    try:
        d = MS.read("spot", "NIFTYFUT", start=start, end=end,
                    columns=["timestamp", "open", "high", "low", "close", "volume"])
    except Exception as exc:
        return pd.DataFrame(), f"futures series could not be read: {str(exc)[:120]}"
    if d is None or not len(d):
        return (pd.DataFrame(),
                "NIFTY futures one-minute data has never been ingested (store key "
                "spot/NIFTYFUT is empty). The downloader supports it — enable futures in the "
                "Data Ingestion Lab with a connected broker — until then futures participation "
                "cannot be evaluated and is reported as UNAVAILABLE.")
    d = d.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values("timestamp").drop_duplicates("timestamp")
    d["minute"] = _minutes(d["timestamp"])
    d["date"] = d["timestamp"].dt.date
    return d.reset_index(drop=True), None


def load_options(start: str, end: str) -> pd.DataFrame:
    d = MS.read("options", "NIFTY", start=start, end=end,
                columns=["timestamp", "strike", "option_type", "expiry_date",
                         "open", "high", "low", "close", "volume", "oi"])
    if d is None or not len(d):
        return pd.DataFrame()
    d = d.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["minute"] = _minutes(d["timestamp"])
    d["date"] = d["timestamp"].dt.date
    return d


def load_vix(start: str, end: str) -> pd.DataFrame:
    d = MS.read("spot", "INDIAVIX", start=start, end=end,
                columns=["timestamp", "open", "high", "low", "close"])
    if d is None or not len(d):
        return pd.DataFrame()
    d = d.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["minute"] = _minutes(d["timestamp"])
    d["date"] = d["timestamp"].dt.date
    return d


def inventory() -> dict:
    """What exists, over what span — the answer to 'can this strategy be tested at all'."""
    out = {}
    for key, (kind, und) in {
        "spot": ("spot", "NIFTY"),
        "futures": ("spot", "NIFTYFUT"),
        "options": ("options", "NIFTY"),
        "vix": ("spot", "INDIAVIX"),
    }.items():
        try:
            d = MS.read(kind, und, start="2015-01-01", end="2035-12-31", columns=["timestamp"])
            if d is None or not len(d):
                out[key] = {"available": False, "sessions": 0, "rows": 0,
                            "first": None, "last": None}
                continue
            t = pd.to_datetime(d["timestamp"])
            out[key] = {"available": True, "sessions": int(t.dt.date.nunique()),
                        "rows": int(len(d)), "first": str(t.min().date()),
                        "last": str(t.max().date())}
        except Exception as exc:
            out[key] = {"available": False, "sessions": 0, "rows": 0,
                        "first": None, "last": None, "error": str(exc)[:120]}
    out["notes"] = [
        "NIFTY 50 is an index, not a traded contract. Its stored volume column is not futures "
        "participation and is never read by the volume engine.",
        "Futures participation requires spot/NIFTYFUT, which the Data Ingestion Lab can pull "
        "with a connected broker." if not out["futures"]["available"] else
        "Futures participation is available.",
        "Option volume is real traded volume and is offered as its own participation source, "
        "reported under its own name.",
    ]
    return out


def resample(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """One-minute bars folded into larger candles, each stamped with the minute it STARTS.

    The stamp matters downstream: a candle labelled 10:15 on a five-minute chart is only
    complete at 10:19, and every decision in this module is taken on that later minute.
    """
    if minutes <= 1 or bars.empty:
        return bars.copy()
    out = []
    for day, g in bars.groupby("date", sort=True):
        g = g.sort_values("timestamp")
        bucket = SESSION_OPEN + ((g["minute"] - SESSION_OPEN) // minutes) * minutes
        agg = (g.assign(bucket=bucket).groupby("bucket")
                 .agg(timestamp=("timestamp", "first"), open=("open", "first"),
                      high=("high", "max"), low=("low", "min"), close=("close", "last"),
                      **({"volume": ("volume", "sum")} if "volume" in g.columns else {}))
                 .reset_index())
        agg["minute"] = agg["bucket"].astype(int)
        agg["date"] = day
        out.append(agg.drop(columns=["bucket"]))
    return pd.concat(out, ignore_index=True) if out else bars.iloc[0:0].copy()


def session_quality(d: date, spot: pd.DataFrame, fut: pd.DataFrame,
                    opt: pd.DataFrame, vix: pd.DataFrame,
                    min_bars: int = 300) -> Quality:
    """Decide what one session's data supports, before any signal is computed."""
    s = spot[spot["date"] == d]
    reasons: list[str] = []
    if s.empty:
        return Quality(d, 0, 0, 0, 0, None, None, 0, False, 0, 0, False, ("no spot bars",))

    mins = s["minute"].to_numpy()
    expected = np.arange(mins.min(), mins.max() + 1)
    gaps = int(len(set(expected.tolist()) - set(mins.tolist())))
    dupes = int(len(s) - s["timestamp"].nunique())
    bad_ohlc = int(((s["high"] < s["low"]) | (s["high"] < s["open"]) | (s["high"] < s["close"])
                    | (s["low"] > s["open"]) | (s["low"] > s["close"])).sum())

    if len(s) < min_bars:
        reasons.append(f"only {len(s)} spot bars")
    if bad_ohlc:
        reasons.append(f"{bad_ohlc} bars where OHLC is inconsistent")
    if dupes:
        reasons.append(f"{dupes} duplicate timestamps")
    if int(mins.min()) > SESSION_OPEN + 5:
        reasons.append(f"session starts late at {int(mins.min())//60:02d}:{int(mins.min())%60:02d}")

    f = fut[fut["date"] == d] if not fut.empty else fut
    o = opt[opt["date"] == d] if not opt.empty else opt
    v = vix[vix["date"] == d] if not vix.empty else vix
    return Quality(
        session=d, spot_bars=len(s), gaps=gaps, ohlc_violations=bad_ohlc, duplicates=dupes,
        first_minute=int(mins.min()), last_minute=int(mins.max()),
        futures_bars=len(f), futures_available=bool(len(f)),
        options_contracts=int(o["strike"].nunique()) if len(o) else 0,
        vix_bars=len(v), usable=not reasons, reasons=tuple(reasons))


def align(spot_day: pd.DataFrame, fut_day: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Join spot and futures on the minute, and describe the basis rather than ignoring it.

    Futures trade at a premium or discount to the index that drifts with time to expiry and
    carry. Comparing a futures price to a spot level directly would make every breakout look
    early or late by that offset, so the two are compared on their own MOVES, and the basis is
    measured and reported so a sudden change in it can be treated as the warning it is.
    """
    if fut_day.empty:
        return spot_day.assign(fut_close=np.nan, basis=np.nan), {
            "available": False, "reason": "no futures bars for this session"}
    j = spot_day.merge(fut_day[["minute", "open", "high", "low", "close", "volume"]],
                       on="minute", how="left", suffixes=("", "_fut"))
    j = j.rename(columns={"close_fut": "fut_close", "volume": "fut_volume",
                          "open_fut": "fut_open", "high_fut": "fut_high", "low_fut": "fut_low"})
    j["basis"] = j["fut_close"] - j["close"]
    matched = int(j["fut_close"].notna().sum())
    b = j["basis"].dropna()
    return j, {"available": matched > 0, "matched_minutes": matched,
               "coverage_pct": round(matched / max(1, len(j)) * 100, 1),
               "basis_mean": round(float(b.mean()), 2) if len(b) else None,
               "basis_std": round(float(b.std()), 2) if len(b) else None,
               "basis_range": [round(float(b.min()), 2), round(float(b.max()), 2)] if len(b) else None}
