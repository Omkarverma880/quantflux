"""
Hunter — what each screen has actually returned.

Every signal the screen would have fired on the stored history is traded by one fixed rule, so the
numbers on the Evidence tab come from the same definitions the live scan uses:

  entry   the next session's open after the signal
  stop    8% below the entry (intraday: if the low touches it, you are out there)
  trail   a close below the 50-day average ends the trade at the next open
  time    60 sessions maximum
  costs   0.15% of the trade value each way (brokerage, STT, exchange, stamp, GST)

Two honest limits, shown on the tab:
  * the universe is today's NIFTY 500, so stocks that fell out of the index over the years are not
    in it — real results would be somewhat worse (survivorship);
  * signals are found with the same functions the scan uses, on data up to that day only, but the
    universe membership itself is current, not historical.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.hunter import patterns as PT
from research.hunter import store as STORE

logger = get_logger("research.hunter.evidence")

COST_PCT = 0.15                 # each way
STOP_PCT = 8.0
MAX_HOLD = 60
YEARS = 5


def _trend_mask(d: pd.DataFrame, p: PT.Params = PT.P) -> np.ndarray:
    c = d["close"].to_numpy(float)
    return ((c > d["sma50"].to_numpy(float))
            & (d["sma50"].to_numpy(float) > d["sma150"].to_numpy(float))
            & (d["sma150"].to_numpy(float) > d["sma200"].to_numpy(float))
            & d["sma200_up"].fillna(False).to_numpy(bool)
            & (c >= d["high_52w"].to_numpy(float) * (1 - p.near_high_pct / 100))
            & (c >= d["low_52w"].to_numpy(float) * (1 + p.above_low_pct / 100)))


def signals(d: pd.DataFrame, screen: str, p: PT.Params = PT.P) -> np.ndarray:
    """Indices where this screen fires, using only that day's close and earlier."""
    c = d["close"].to_numpy(float)
    v = d["volume"].to_numpy(float)
    av = d["vol50"].to_numpy(float)
    trend = _trend_mask(d, p)
    if screen == "base_break":
        pv = d["pivot_hist"].to_numpy(float)
        prev_c = np.r_[np.nan, c[:-1]]
        prev_pv = np.r_[np.nan, pv[:-1]]
        hit = (c > pv) & (prev_c <= prev_pv) & (v >= p.breakout_volume * av) & trend
    elif screen == "high_52w":
        hit = (c >= d["high_52w"].to_numpy(float) * 0.999) & (v >= 1.3 * av) & trend
    elif screen == "pullback_50":
        near = np.abs(c / d["sma50"].to_numpy(float) - 1) * 100
        state = (near <= 3.0) & (c >= d["sma50"].to_numpy(float)) & trend
        hit = state & ~np.r_[False, state[:-1]]                   # the day it becomes true
    elif screen == "squeeze":
        atr = d["atr_pct"].to_numpy(float)
        floor = d["atr_pct"].rolling(126).min().to_numpy(float)
        state = (atr <= floor * 1.05) & trend
        hit = state & ~np.r_[False, state[:-1]]
    elif screen == "pocket_pivot":
        down = np.where(c < np.r_[np.nan, c[:-1]], v, 0.0)
        big_down = pd.Series(down).rolling(10).max().shift(1).to_numpy(float)
        hit = (c > np.r_[np.nan, c[:-1]]) & (c > d["sma50"].to_numpy(float)) & (v > big_down) & trend
    else:
        raise ValueError(f"unknown screen {screen}")
    hit = np.nan_to_num(hit, nan=False).astype(bool)
    hit[:PT.MIN_BARS] = False
    return np.flatnonzero(hit)


def trade(d: pd.DataFrame, i: int) -> Optional[dict]:
    """One signal → one trade under the fixed rule."""
    o = d["open"].to_numpy(float)
    h = d["high"].to_numpy(float)
    low = d["low"].to_numpy(float)
    c = d["close"].to_numpy(float)
    s50 = d["sma50"].to_numpy(float)
    n = len(c)
    if i + 2 >= n:
        return None
    entry = o[i + 1]
    if not np.isfinite(entry) or entry <= 0:
        return None
    stop = entry * (1 - STOP_PCT / 100)
    end = min(i + 1 + MAX_HOLD, n - 1)
    exit_px, why, j = c[end], "TIME", end
    for k in range(i + 1, end + 1):
        if low[k] <= stop:
            exit_px, why, j = stop, "STOP", k
            break
        if c[k] < s50[k]:
            exit_px, why, j = (o[k + 1] if k + 1 <= end else c[k]), "TRAIL", min(k + 1, end)
            break
    gross = (exit_px / entry - 1) * 100
    return {"date": str(pd.Timestamp(d["date"].iloc[i]).date()), "entry": round(float(entry), 2),
            "exit": round(float(exit_px), 2), "why": why, "held": int(j - i),
            "ret_pct": round(float(gross - 2 * COST_PCT), 2),
            "mfe_pct": round(float((np.nanmax(h[i + 1:j + 1]) / entry - 1) * 100), 2) if j > i else 0.0,
            "year": str(pd.Timestamp(d["date"].iloc[i]).year)}


def summarise(trades: list[dict], label: str) -> dict:
    if not trades:
        return {"screen": label, "trades": 0, "message": "no signals in the stored history"}
    t = pd.DataFrame(trades)
    w, l = t.ret_pct[t.ret_pct > 0], t.ret_pct[t.ret_pct <= 0]
    by_year = t.groupby("year").ret_pct.agg(["size", "mean", "median"]).round(2)
    return {
        "screen": label, "trades": int(len(t)),
        "win_rate": round(len(w) / len(t) * 100, 1),
        "avg_win": round(float(w.mean()), 2) if len(w) else 0.0,
        "avg_loss": round(float(l.mean()), 2) if len(l) else 0.0,
        "expectancy": round(float(t.ret_pct.mean()), 2),
        "median": round(float(t.ret_pct.median()), 2),
        "payoff": round(float(w.mean() / abs(l.mean())), 2) if len(w) and len(l) and l.mean() != 0 else None,
        "avg_hold": int(t.held.mean()), "avg_mfe": round(float(t.mfe_pct.mean()), 2),
        "best": round(float(t.ret_pct.max()), 2), "worst": round(float(t.ret_pct.min()), 2),
        "exits": {k: int(v) for k, v in t.why.value_counts().items()},
        "by_year": [{"year": y, "trades": int(r["size"]), "avg_pct": float(r["mean"]), "median_pct": float(r["median"])}
                    for y, r in by_year.iterrows()],
        "stocks": int(t.symbol.nunique()) if "symbol" in t.columns else None,
    }


def run(progress: Optional[Callable[[str], None]] = None, screens: Optional[list[str]] = None,
        years: int = YEARS) -> dict:
    """Backtest every screen over the cached daily history of the last scan's universe."""
    from research.my_equity import cache as CACHE
    say = progress or (lambda _m: None)
    rows, meta = STORE.load()
    if not rows:
        return {"status": "error", "message": "run a scan first — the Evidence tab uses its universe"}
    keys = screens or list(PT.SCREENS)
    cutoff = pd.Timestamp(date.today()) - pd.DateOffset(years=years)
    found: dict[str, list[dict]] = {k: [] for k in keys}
    n = len(rows)
    for i, r in enumerate(rows, 1):
        if i % 25 == 0 or i == n:
            say(f"{i}/{n} stocks · {sum(len(v) for v in found.values())} trades")
        df = CACHE.daily(None, r["symbol"], r.get("token") or 0, r.get("exchange", "NSE"), refresh=False)
        if df is None or len(df) < PT.MIN_BARS + 30:
            continue
        d = PT.indicators(df)
        in_range = pd.to_datetime(d["date"]) >= cutoff
        for k in keys:
            try:
                idx = signals(d, k)
            except Exception as exc:
                logger.debug("%s signals failed for %s: %s", k, r["symbol"], exc)
                continue
            for j in idx:
                if not bool(in_range.iloc[j]):
                    continue
                tr = trade(d, int(j))
                if tr:
                    found[k].append({**tr, "symbol": r["symbol"]})
    out = {k: summarise(v, PT.SCREENS[k]["name"]) for k, v in found.items()}
    payload = {"status": "ok", "built": pd.Timestamp.now().isoformat(timespec="seconds"),
               "years": years, "universe": meta.get("universe", {}), "scan_date": meta.get("scan_date"),
               "rule": {"entry": "next open after the signal", "stop_pct": STOP_PCT,
                        "trail": "a close below the 50-day average", "max_hold": MAX_HOLD,
                        "cost_pct_each_way": COST_PCT},
               "caveats": ["The universe is today's NIFTY 500 — names that dropped out of the index are "
                           "missing, which flatters these numbers (survivorship).",
                           "Signals use only data up to their own day, but index membership is current.",
                           "Results are per trade, before position sizing and before slippage beyond the "
                           f"{COST_PCT}% each way charged here."],
               "screens": out}
    path = Path(STORE.ROOT) / "evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str))
    return payload


def load() -> dict:
    try:
        return json.loads((Path(STORE.ROOT) / "evidence.json").read_text())
    except Exception:
        return {"status": "empty", "message": "no evidence built yet"}
