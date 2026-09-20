"""
Indicator maths for My Equity Workspace.

Pure functions over a daily (or intraday) OHLCV frame — no I/O, no broker, no state, so the
same code serves the table, the X-ray and the entry-zone backtest and can be unit-tested on
a fixed frame. Wilder's smoothing is used for RSI, ATR and ADX, matching what charting
platforms show.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS_YEAR = 252


# ── building blocks ──────────────────────────────────────────────────
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).ewm(span=n, adjust=False).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing — an EMA with alpha = 1/n."""
    return s.astype(float).ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.astype(float).diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder((-d).clip(lower=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[loss == 0] = 100.0                      # only gains in the window
    out[(loss == 0) & (gain == 0)] = 50.0       # a flat line is neither
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                      (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder's ADX with both directional indicators."""
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = wilder(true_range(df), n).replace(0, np.nan)
    pdi = 100 * wilder(pd.Series(plus_dm, index=df.index), n) / tr
    mdi = 100 * wilder(pd.Series(minus_dm, index=df.index), n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pd.DataFrame({"adx": wilder(dx.fillna(0), n), "plus_di": pdi, "minus_di": mdi})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid.replace(0, np.nan) * 100
    pctb = (close - lower) / (upper - lower).replace(0, np.nan) * 100
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
                         "bb_width": width, "bb_pctb": pctb})


def vwap_bands(df: pd.DataFrame, n: int = 20, k: float = 1.5) -> pd.DataFrame:
    """Rolling volume-weighted average price with ±k σ bands.

    On a daily frame this is the "where has the money actually traded" line that a 20-day
    simple average misses when one session carries most of the volume.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float).replace(0, np.nan)
    pv = (tp * vol).rolling(n).sum()
    vv = vol.rolling(n).sum()
    vwap = pv / vv
    var = ((tp - vwap) ** 2 * vol).rolling(n).sum() / vv
    sd = np.sqrt(var.clip(lower=0))
    return pd.DataFrame({"vwap": vwap, "vwap_upper": vwap + k * sd, "vwap_lower": vwap - k * sd,
                         "vwap_sd": sd})


def session_vwap_bands(df: pd.DataFrame, k: float = 1.5) -> pd.DataFrame:
    """Anchored-at-open VWAP with ±k σ bands, for an intraday frame of one session."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float)
    cpv, cv = (tp * vol).cumsum(), vol.cumsum().replace(0, np.nan)
    vwap = cpv / cv
    var = ((tp - vwap) ** 2 * vol).cumsum() / cv
    sd = np.sqrt(var.clip(lower=0))
    return pd.DataFrame({"vwap": vwap, "vwap_upper": vwap + k * sd, "vwap_lower": vwap - k * sd})


# ── one frame, every indicator ───────────────────────────────────────
def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Return the frame with every indicator column the workspace uses."""
    d = df.copy().reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["ema20"] = ema(d["close"], 20)
    d["ema50"] = ema(d["close"], 50)
    d["ema200"] = ema(d["close"], 200)
    d["rsi"] = rsi(d["close"], 14)
    d["atr"] = atr(d, 14)
    d = pd.concat([d, adx(d, 14), bollinger(d["close"], 20, 2.0), vwap_bands(d, 20, 1.5)], axis=1)
    d["vol_avg20"] = d["volume"].rolling(20).mean()
    d["vol_avg5"] = d["volume"].rolling(5).mean()
    return d


def _f(v) -> Optional[float]:
    """Plain float or None — JSON has no NaN."""
    try:
        f = float(v)
        return round(f, 4) if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _pct(a, b) -> Optional[float]:
    a, b = _f(a), _f(b)
    if a is None or not b:
        return None
    return round((a - b) / b * 100.0, 2)


# ── summaries ────────────────────────────────────────────────────────
def extremes(d: pd.DataFrame, ltp: Optional[float] = None) -> dict:
    """52-week and lifetime high/low, and how far price sits from each."""
    if d.empty:
        return {}
    px = _f(ltp) or _f(d["close"].iloc[-1])
    yr = d.tail(TRADING_DAYS_YEAR)
    out = {
        "high_52w": _f(yr["high"].max()), "low_52w": _f(yr["low"].min()),
        "high_life": _f(d["high"].max()), "low_life": _f(d["low"].min()),
        "high_life_on": str(d.loc[d["high"].idxmax(), "date"]),
        "low_life_on": str(d.loc[d["low"].idxmin(), "date"]),
        "sessions": int(len(d)), "first_session": str(d["date"].iloc[0]),
    }
    out["from_52w_high"] = _pct(px, out["high_52w"])
    out["from_52w_low"] = _pct(px, out["low_52w"])
    out["from_life_high"] = _pct(px, out["high_life"])
    if out["high_52w"] is not None and out["low_52w"] is not None and out["high_52w"] > out["low_52w"]:
        out["range_pos_52w"] = round((px - out["low_52w"]) / (out["high_52w"] - out["low_52w"]) * 100, 1)
    return out


def volume_flow(d: pd.DataFrame, today_volume: Optional[float] = None, bars: int = 12) -> dict:
    """Latest volume next to the recent record: the bars to draw, and how today compares.

    ``today_volume`` (from the live quote) replaces the last cached bar while the session is
    still running, so the column is honest intraday.
    """
    if d.empty:
        return {}
    vols = d["volume"].tail(bars).tolist()
    dates = [str(x) for x in d["date"].tail(bars).tolist()]
    last = _f(today_volume) if today_volume else _f(d["volume"].iloc[-1])
    if today_volume and vols:
        vols[-1] = float(today_volume)
    avg5, avg20 = _f(d["volume"].tail(6).head(5).mean()), _f(d["volume"].tail(21).head(20).mean())
    up = d.tail(20)
    up_vol = float(up.loc[up["close"] >= up["open"], "volume"].sum())
    tot_vol = float(up["volume"].sum()) or 1.0
    return {
        "latest": last, "avg5": avg5, "avg20": avg20,
        "vs_avg20": round(last / avg20, 2) if last and avg20 else None,
        "vs_avg5": round(last / avg5, 2) if last and avg5 else None,
        "buy_share_20d": round(up_vol / tot_vol * 100, 1),
        "bars": [{"date": dt, "volume": _f(v)} for dt, v in zip(dates, vols)],
    }


def _clip(v: Optional[float], lo: float = -1.0, hi: float = 1.0) -> float:
    if v is None or not np.isfinite(v):
        return 0.0
    return float(max(lo, min(hi, v)))


def sensitivity(d: pd.DataFrame, ltp: Optional[float] = None, ext: Optional[dict] = None,
                flow: Optional[dict] = None) -> dict:
    """How bullish or bearish the stock reads right now: −100 … +100, with its workings.

    Six independent readings, each scored −1 … +1 and weighted. Nothing here is a forecast —
    it is a summary of trend, momentum, strength, flow and position in the yearly range.
    """
    if d.empty:
        return {"score": 0, "label": "No data", "parts": []}
    r = d.iloc[-1]
    px = _f(ltp) or _f(r["close"])
    ext = ext or extremes(d, px)
    flow = flow or volume_flow(d)
    parts = [
        {"key": "trend", "label": "Price vs 200 EMA", "weight": 25,
         "value": _pct(px, r["ema200"]), "unit": "%",
         "score": _clip((_pct(px, r["ema200"]) or 0) / 12.0)},
        {"key": "stack", "label": "20 EMA vs 50 EMA", "weight": 15,
         "value": _pct(r["ema20"], r["ema50"]), "unit": "%",
         "score": _clip((_pct(r["ema20"], r["ema50"]) or 0) / 5.0)},
        {"key": "rsi", "label": "RSI (14)", "weight": 20,
         "value": _f(r["rsi"]), "unit": "",
         "score": _clip(((_f(r["rsi"]) or 50) - 50) / 25.0)},
        {"key": "adx", "label": "Directional strength (ADX)", "weight": 15,
         "value": _f(r["adx"]), "unit": "",
         "score": _clip(((_f(r["plus_di"]) or 0) - (_f(r["minus_di"]) or 0)) / 25.0)
         * min(1.0, (_f(r["adx"]) or 0) / 25.0)},
        {"key": "flow", "label": "Volume on up days (20d)", "weight": 15,
         "value": flow.get("buy_share_20d"), "unit": "%",
         "score": _clip(((flow.get("buy_share_20d") or 50) - 50) / 20.0)},
        {"key": "range", "label": "Position in 52-week range", "weight": 10,
         "value": ext.get("range_pos_52w"), "unit": "%",
         "score": _clip(((ext.get("range_pos_52w") or 50) - 50) / 40.0)},
    ]
    score = round(sum(p["score"] * p["weight"] for p in parts), 1)
    for p in parts:
        p["score"] = round(p["score"], 2)
        p["points"] = round(p["score"] * p["weight"], 1)
    if score >= 55:
        label, tone = "Strongly bullish", "bull"
    elif score >= 20:
        label, tone = "Bullish", "bull"
    elif score > -20:
        label, tone = "Neutral", "flat"
    elif score > -55:
        label, tone = "Bearish", "bear"
    else:
        label, tone = "Strongly bearish", "bear"
    return {"score": score, "label": label, "tone": tone, "parts": parts}


def _squeeze(d: pd.DataFrame) -> bool:
    """Bands in the tightest fifth of the last 120 sessions — the classic pre-move coil."""
    w = _f(d["bb_width"].iloc[-1])
    ref = _f(d["bb_width"].tail(120).quantile(0.2))
    return bool(w is not None and ref is not None and w <= ref)


def snapshot(d: pd.DataFrame, ltp: Optional[float] = None) -> dict:
    """The indicator values the table and the X-ray header show."""
    if d.empty:
        return {}
    r = d.iloc[-1]
    px = _f(ltp) or _f(r["close"])
    prev = _f(d["close"].iloc[-2]) if len(d) > 1 else None
    return {
        "close": _f(r["close"]), "prev_close": prev, "change_pct": _pct(px, prev),
        "rsi": _f(r["rsi"]), "adx": _f(r["adx"]), "plus_di": _f(r["plus_di"]),
        "minus_di": _f(r["minus_di"]), "atr": _f(r["atr"]),
        "ema20": _f(r["ema20"]), "ema50": _f(r["ema50"]), "ema200": _f(r["ema200"]),
        "vs_ema200": _pct(px, r["ema200"]), "vs_ema50": _pct(px, r["ema50"]),
        "bb_upper": _f(r["bb_upper"]), "bb_lower": _f(r["bb_lower"]), "bb_mid": _f(r["bb_mid"]),
        "bb_pctb": _f(r["bb_pctb"]), "bb_width": _f(r["bb_width"]),
        "bb_squeeze": _squeeze(d),
        "vwap": _f(r["vwap"]), "vwap_upper": _f(r["vwap_upper"]), "vwap_lower": _f(r["vwap_lower"]),
        "vs_vwap": _pct(px, r["vwap"]),
        "last_session": str(r["date"]),
    }


# ── the deeper read: performance, risk, seasonality, pivots ──────────
WINDOWS = [("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252), ("3Y", 756), ("5Y", 1260)]


def performance(d: pd.DataFrame, ltp: Optional[float] = None) -> dict:
    """Return over the standard windows, plus this calendar year, measured to the last price."""
    if d.empty:
        return {}
    px = _f(ltp) or _f(d["close"].iloc[-1])
    closes = d["close"].to_numpy(float)
    out = {"windows": []}
    for label, n in WINDOWS:
        if len(closes) > n:
            out["windows"].append({"label": label, "sessions": n, "pct": _pct(px, closes[-1 - n])})
    yr = d[pd.to_datetime(d["date"]).dt.year == pd.Timestamp(d["date"].iloc[-1]).year]
    if len(yr) > 1:
        out["ytd"] = _pct(px, yr["close"].iloc[0])
    out["since_listing"] = _pct(px, closes[0])
    return out


def risk_stats(d: pd.DataFrame, ltp: Optional[float] = None) -> dict:
    """How wild the stock is: volatility, drawdown from the lifetime high, best and worst days."""
    if len(d) < 30:
        return {}
    px = _f(ltp) or _f(d["close"].iloc[-1])
    rets = d["close"].pct_change().dropna() * 100
    last_year = rets.tail(TRADING_DAYS_YEAR)
    peak = d["close"].cummax()
    dd = (d["close"] / peak - 1) * 100
    worst_i, best_i = rets.tail(TRADING_DAYS_YEAR).idxmin(), rets.tail(TRADING_DAYS_YEAR).idxmax()
    atr_pct = _pct(_f(d["atr"].iloc[-1]) + px, px) if "atr" in d.columns else None
    return {
        "volatility_annual": _f(last_year.std(ddof=0) * np.sqrt(TRADING_DAYS_YEAR)),
        "avg_daily_move": _f(last_year.abs().mean()),
        "atr_pct": atr_pct,
        "max_drawdown": _f(dd.min()),
        "drawdown_now": _f(dd.iloc[-1]),
        "up_days_pct": _f((last_year > 0).mean() * 100),
        "best_day": {"date": str(d.loc[best_i, "date"]), "pct": _f(rets.loc[best_i])},
        "worst_day": {"date": str(d.loc[worst_i, "date"]), "pct": _f(rets.loc[worst_i])},
        "gap_ups": int((d["open"].tail(TRADING_DAYS_YEAR) > d["high"].shift(1).tail(TRADING_DAYS_YEAR)).sum()),
        "gap_downs": int((d["open"].tail(TRADING_DAYS_YEAR) < d["low"].shift(1).tail(TRADING_DAYS_YEAR)).sum()),
    }


def relative_strength(d: pd.DataFrame, index_df: Optional[pd.DataFrame], windows=(21, 63, 252)) -> list[dict]:
    """The stock's return against the index over the same window — who is carrying whom."""
    if d.empty or index_df is None or index_df.empty:
        return []
    idx = index_df.rename(columns={"timestamp": "date"}).copy()
    idx["date"] = pd.to_datetime(idx["date"]).dt.date
    idx = idx.groupby("date", as_index=False)["close"].last()
    merged = pd.merge(d[["date", "close"]], idx, on="date", how="inner", suffixes=("", "_idx"))
    out = []
    for n in windows:
        if len(merged) > n:
            s = _pct(merged["close"].iloc[-1], merged["close"].iloc[-1 - n])
            i = _pct(merged["close_idx"].iloc[-1], merged["close_idx"].iloc[-1 - n])
            if s is not None and i is not None:
                out.append({"label": f"{n}d", "stock": s, "index": i, "excess": round(s - i, 2)})
    return out


def seasonality(d: pd.DataFrame, years: int = 10) -> list[dict]:
    """Average return by calendar month over the recent years — a habit, not a promise."""
    if len(d) < 400:
        return []
    f = d.copy()
    f["ts"] = pd.to_datetime(f["date"])
    f = f[f["ts"] >= f["ts"].max() - pd.DateOffset(years=years)]
    g = f.groupby([f["ts"].dt.year.rename("y"), f["ts"].dt.month.rename("m")])["close"]
    monthly = pd.DataFrame({"first": g.first(), "last": g.last()}).reset_index()
    monthly["ret"] = (monthly["last"] / monthly["first"] - 1) * 100
    return [{"month": int(m), "avg": _f(grp["ret"].mean()),
             "positive_pct": _f((grp["ret"] > 0).mean() * 100), "samples": int(len(grp))}
            for m, grp in monthly.groupby("m")]


def pivots(d: pd.DataFrame) -> dict:
    """Classic floor-trader pivots from the last completed session."""
    if d.empty:
        return {}
    r = d.iloc[-1]
    h, l, c = float(r["high"]), float(r["low"]), float(r["close"])
    p = (h + l + c) / 3
    return {"date": str(r["date"]), "pivot": round(p, 2),
            "r1": round(2 * p - l, 2), "r2": round(p + (h - l), 2), "r3": round(h + 2 * (p - l), 2),
            "s1": round(2 * p - h, 2), "s2": round(p - (h - l), 2), "s3": round(l - 2 * (h - p), 2)}


def order_book(quote: dict) -> dict:
    """The live order book from the quote: five levels a side, the spread and the day's limits."""
    depth = (quote or {}).get("depth") or {}
    buy, sell = depth.get("buy") or [], depth.get("sell") or []
    if not buy and not sell:
        return {"available": False}
    bid = _f(buy[0]["price"]) if buy else None
    ask = _f(sell[0]["price"]) if sell else None
    tot_buy = float(quote.get("buy_quantity") or sum(b.get("quantity", 0) for b in buy))
    tot_sell = float(quote.get("sell_quantity") or sum(s.get("quantity", 0) for s in sell))
    return {
        "available": True, "bid": bid, "ask": ask,
        "spread": round(ask - bid, 2) if bid and ask else None,
        "spread_pct": _pct(ask, bid) if bid and ask else None,
        "buy_quantity": tot_buy, "sell_quantity": tot_sell,
        "pressure": round(tot_buy / (tot_buy + tot_sell) * 100, 1) if (tot_buy + tot_sell) else None,
        "buy": [{"price": _f(b.get("price")), "quantity": b.get("quantity"), "orders": b.get("orders")} for b in buy[:5]],
        "sell": [{"price": _f(s.get("price")), "quantity": s.get("quantity"), "orders": s.get("orders")} for s in sell[:5]],
        "upper_circuit": _f(quote.get("upper_circuit_limit")),
        "lower_circuit": _f(quote.get("lower_circuit_limit")),
        "last_trade_time": str(quote.get("last_trade_time") or "") or None,
        "average_price": _f(quote.get("average_price")),
    }
