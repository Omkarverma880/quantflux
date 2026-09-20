"""
Flux Lab — the market picture at a point in time.

Every column here is **causal**: the value on bar *i* is computed only from bars up to and
including *i*. Nothing looks forward. The engine then makes its decision on bar *i* and fills on
bar *i+1* (or later, depending on the execution model), so a signal can never be informed by the
candle that pays for it.

Two rules are enforced rather than assumed:

* rolling windows are left-anchored (`rolling(n)` over past bars only, never `center=True`);
* anything that describes a completed period — the previous day's high, the opening range — is
  only populated on the bars that come *after* that period has finished.

The frame is built once per run for the whole date range and sliced by the engine, which keeps a
three-year backtest to a handful of vectorised passes instead of a Python loop per indicator.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30

TIME_BUCKETS = [
    ("09:15-09:30", 555, 570), ("09:30-10:00", 570, 600), ("10:00-11:00", 600, 660),
    ("11:00-12:00", 660, 720), ("12:00-13:00", 720, 780), ("13:00-14:00", 780, 840),
    ("14:00-15:00", 840, 900), ("15:00-15:30", 900, 930),
]


# ── primitives ───────────────────────────────────────────────────────
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.astype(float).diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    out[loss == 0] = 100.0
    out[(loss == 0) & (gain == 0)] = 50.0
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                      (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin-Ashi candles. HA-open depends on the previous HA candle only — never the next."""
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = np.empty(len(df))
    if len(df):
        ha_open[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
        c = ha_close.to_numpy()
        for i in range(1, len(df)):
            ha_open[i] = (ha_open[i - 1] + c[i - 1]) / 2
    ha_open = pd.Series(ha_open, index=df.index)
    return pd.DataFrame({
        "ha_open": ha_open, "ha_close": ha_close,
        "ha_high": pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1),
        "ha_low": pd.concat([df["low"], ha_open, ha_close], axis=1).min(axis=1),
    })


# ── bar building ─────────────────────────────────────────────────────
def resample(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1-minute bars into the working timeframe, session by session.

    Bars are labelled with the START of the interval and only completed intervals survive, so a
    5-minute bar stamped 09:15 covers 09:15–09:19 and is decided on at 09:20 at the earliest.
    """
    if minutes <= 1 or bars.empty:
        return bars.reset_index(drop=True)
    b = bars.copy()
    b["timestamp"] = pd.to_datetime(b["timestamp"])
    out = []
    for day, g in b.groupby(b["timestamp"].dt.date, sort=True):
        g = g.set_index("timestamp").sort_index()
        agg = g.resample(f"{minutes}min", origin=g.index[0].normalize() + pd.Timedelta(minutes=SESSION_OPEN_MIN),
                         label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        agg = agg.dropna(subset=["close"])
        agg["date"] = day
        out.append(agg.reset_index())
    return pd.concat(out, ignore_index=True) if out else bars.iloc[0:0]


# ── the feature frame ────────────────────────────────────────────────
DEFAULT_PARAMS = {
    "ema_fast": 9, "ema_slow": 21, "ema_trend": 50,
    "rsi_period": 14, "atr_period": 14,
    "bb_period": 20, "bb_std": 2.0,
    "opening_range_min": 15,
    "vol_avg_period": 20,
}


def build(bars: pd.DataFrame, params: Optional[dict] = None,
          vix: Optional[pd.DataFrame] = None, futures: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Every research variable, aligned to the bar it can legitimately be known on."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    d = bars.copy().reset_index(drop=True)
    if d.empty:
        return d
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["date"] = d["timestamp"].dt.date
    d["minute"] = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d["dow"] = d["timestamp"].dt.dayofweek
    d["month"] = d["timestamp"].dt.to_period("M").astype(str)
    d["year"] = d["timestamp"].dt.year
    d["bucket"] = pd.cut(d["minute"], bins=[b[1] for b in TIME_BUCKETS] + [TIME_BUCKETS[-1][2]],
                         labels=[b[0] for b in TIME_BUCKETS], right=False).astype(str)

    g = d.groupby("date", sort=False)

    # trend
    d["ema_fast"] = ema(d["close"], p["ema_fast"])
    d["ema_slow"] = ema(d["close"], p["ema_slow"])
    d["ema_trend"] = ema(d["close"], p["ema_trend"])
    d["ema_stack_up"] = (d["ema_fast"] > d["ema_slow"]) & (d["ema_slow"] > d["ema_trend"])
    d["ema_stack_down"] = (d["ema_fast"] < d["ema_slow"]) & (d["ema_slow"] < d["ema_trend"])
    d["ema_cross_up"] = (d["ema_fast"] > d["ema_slow"]) & (d["ema_fast"].shift(1) <= d["ema_slow"].shift(1))
    d["ema_cross_down"] = (d["ema_fast"] < d["ema_slow"]) & (d["ema_fast"].shift(1) >= d["ema_slow"].shift(1))

    # session VWAP — cumulative within the day, so it is causal by construction
    tp = (d["high"] + d["low"] + d["close"]) / 3
    pv = (tp * d["volume"].fillna(0)).groupby(d["date"]).cumsum()
    vv = d["volume"].fillna(0).groupby(d["date"]).cumsum().replace(0, np.nan)
    d["vwap"] = (pv / vv).fillna(d["close"])
    d["vs_vwap"] = d["close"] - d["vwap"]
    d["above_vwap"] = d["close"] > d["vwap"]
    dev = (d["close"] - d["vwap"]) ** 2
    d["vwap_sd"] = np.sqrt(dev.groupby(d["date"]).expanding().mean().reset_index(level=0, drop=True))
    d["vwap_upper"] = d["vwap"] + d["vwap_sd"]
    d["vwap_lower"] = d["vwap"] - d["vwap_sd"]

    # momentum and volatility
    d["rsi"] = rsi(d["close"], p["rsi_period"])
    d["atr"] = atr(d, p["atr_period"])
    d["atr_pct"] = d["atr"] / d["close"] * 100
    mid = d["close"].rolling(p["bb_period"]).mean()
    sd = d["close"].rolling(p["bb_period"]).std(ddof=0)
    d["bb_mid"], d["bb_upper"], d["bb_lower"] = mid, mid + p["bb_std"] * sd, mid - p["bb_std"] * sd
    d["bb_width"] = (d["bb_upper"] - d["bb_lower"]) / mid.replace(0, np.nan) * 100
    d["bb_width_avg"] = d["bb_width"].rolling(p["bb_period"]).mean()
    d["vol_expanding"] = d["bb_width"] > d["bb_width_avg"]
    d["atr_avg"] = d["atr"].rolling(p["vol_avg_period"]).mean()

    # structure
    d["hh"] = d["high"] > d["high"].shift(1)
    d["ll"] = d["low"] < d["low"].shift(1)
    d["higher_highs"] = d["hh"].rolling(3).sum() >= 2
    d["lower_lows"] = d["ll"].rolling(3).sum() >= 2
    d["body"] = (d["close"] - d["open"]).abs()
    d["range"] = (d["high"] - d["low"]).replace(0, np.nan)
    d["body_pct"] = d["body"] / d["range"] * 100
    d["bull"] = d["close"] > d["open"]
    d["bear"] = d["close"] < d["open"]
    d["bull_streak"] = _streak(d["bull"])
    d["bear_streak"] = _streak(d["bear"])
    d["engulf_up"] = d["bull"] & (d["close"] > d["high"].shift(1)) & (d["open"] <= d["close"].shift(1))
    d["engulf_down"] = d["bear"] & (d["close"] < d["low"].shift(1)) & (d["open"] >= d["close"].shift(1))
    ha = heikin_ashi(d)
    d["ha_bull"] = ha["ha_close"] > ha["ha_open"]
    d["ha_bear"] = ha["ha_close"] < ha["ha_open"]

    # volume
    d["vol_avg"] = d["volume"].rolling(p["vol_avg_period"]).mean()
    d["vol_ratio"] = d["volume"] / d["vol_avg"].replace(0, np.nan)

    # intraday reference levels — each only known once its period has closed
    d["day_open"] = g["open"].transform("first")
    d["day_high_so_far"] = g["high"].cummax()
    d["day_low_so_far"] = g["low"].cummin()
    d["day_range_so_far"] = d["day_high_so_far"] - d["day_low_so_far"]
    orm = p["opening_range_min"]
    in_or = d["minute"] < SESSION_OPEN_MIN + orm
    d["or_high"] = d["high"].where(in_or).groupby(d["date"]).transform("max")
    d["or_low"] = d["low"].where(in_or).groupby(d["date"]).transform("min")
    d.loc[in_or, ["or_high", "or_low"]] = np.nan          # not knowable until the range closes
    d["or_break_up"] = d["close"] > d["or_high"]
    d["or_break_down"] = d["close"] < d["or_low"]

    # previous day — shifted by one session, so today can use yesterday and nothing newer
    daily = d.groupby("date").agg(high=("high", "max"), low=("low", "min"),
                                  close=("close", "last"), open=("open", "first")).sort_index()
    prev = daily.shift(1)
    prev.columns = [f"prev_{c}" for c in prev.columns]
    d = d.merge(prev, left_on="date", right_index=True, how="left")
    d["prev_range"] = d["prev_high"] - d["prev_low"]
    d["gap_pts"] = d["day_open"] - d["prev_close"]
    d["gap_pct"] = d["gap_pts"] / d["prev_close"].replace(0, np.nan) * 100
    d["above_prev_high"] = d["close"] > d["prev_high"]
    d["below_prev_low"] = d["close"] < d["prev_low"]

    # market context from the other stored series
    d = _attach_daily_context(d, vix, "vix")
    if futures is not None and not futures.empty:
        f = futures.copy()
        f["timestamp"] = pd.to_datetime(f["timestamp"])
        f = f[["timestamp", "close"]].rename(columns={"close": "fut_close"})
        d = d.merge(f, on="timestamp", how="left")
        d["fut_close"] = d["fut_close"].ffill()
        d["basis"] = d["fut_close"] - d["close"]
    return d


def _streak(flag: pd.Series) -> pd.Series:
    """How many bars in a row the flag has been true, including this one."""
    grp = (~flag).cumsum()
    return flag.groupby(grp).cumsum().astype(int)


def _attach_daily_context(d: pd.DataFrame, series: Optional[pd.DataFrame], name: str) -> pd.DataFrame:
    """Join another series (India VIX) on the same minute, forward-filled, never back-filled."""
    if series is None or series.empty:
        d[name] = np.nan
        d[f"{name}_open"] = np.nan
        return d
    s = series.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"])
    s = s[["timestamp", "close"]].rename(columns={"close": name}).sort_values("timestamp")
    d = pd.merge_asof(d.sort_values("timestamp"), s, on="timestamp", direction="backward")
    d[f"{name}_open"] = d.groupby("date")[name].transform("first")
    return d


def regime(row: pd.Series, vix_split: float = 14.0, gap_pts: float = 40.0) -> dict:
    """Label the day/bar's conditions from data available at that moment."""
    gap = float(row.get("gap_pts") or 0)
    vix = row.get("vix")
    trend = "sideways"
    if bool(row.get("ema_stack_up")):
        trend = "trending up"
    elif bool(row.get("ema_stack_down")):
        trend = "trending down"
    day_range = float(row.get("day_range_so_far") or 0)
    prev_range = float(row.get("prev_range") or 0) or np.nan
    wide = day_range > prev_range if np.isfinite(prev_range) else None
    return {
        "trend": trend,
        "volatility": "expanding" if bool(row.get("vol_expanding")) else "contracting",
        "vix": None if vix is None or not np.isfinite(float(vix or np.nan))
        else ("high vix" if float(vix) >= vix_split else "low vix"),
        "gap": "gap up" if gap >= gap_pts else "gap down" if gap <= -gap_pts
        else "small gap" if abs(gap) > 5 else "flat open",
        "range_day": None if wide is None else ("wide range" if wide else "narrow range"),
    }
