"""
The OI study behind the OI Lab — one per underlying, built from the Market Store.

For every session and every 5-minute checkpoint it records the chain features
(``features.py``) and what spot did afterwards. From that one table come:

  wall odds      how often the biggest call/put OI strike actually held until the close,
                 by distance (in straddles), time of day, days to expiry and dominance
  models         logistic regressions for "call wall holds", "put wall holds", "both hold"
                 (strong out of sample on NIFTY) and for direction (no edge — kept and
                 shown so nobody has to take that on trust), plus reach models for how
                 far spot still travels up/down; trained before the holdout date and
                 scored only on the sessions after it
  analogs        the past sessions whose chain looked most like now, and what came next
  expiry curves  how the ATM straddle decays and how far spot travels, by days to expiry

Any underlying with option bars in the Market Store gets its own study (NIFTY, SENSEX,
whatever is uploaded through the Data Ingestion Lab). What differs per underlying is
derived from the data, never hard-coded: the strike step (from listed strikes), the
holdout date (the standard one when enough sessions follow it, else the last 30% of
sessions), IV (computed when an upload has none) and spot (the options' own spot
column when no separate index file was uploaded).

Tables are cached on disk keyed by the store's file hashes and rebuilt in a background
thread when the data changes; the live X-ray works without them and omits the odds.
"""
from __future__ import annotations

import hashlib
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from config import settings
from core.logger import get_logger
from research.market_store import store as MS
from research.oi_lab import features as FT
from research.oi_lab import vol
from research.oi_lab.indices import REF_MIN, CHECK_EVERY, hhmm
from research.oi_lab.model import Logit, Reach, evaluate, verdict

logger = get_logger("research.oi_lab.history")

UNDERLYING = "NIFTY"                 # the default study (the store's primary underlying)
HOLDOUT_START = "2025-12-01"
MIN_SESSIONS_MODELS = 40             # below this, only descriptive tables are built
LAST_CP = 15 * 60 + 25
CACHE_DIR = Path(settings.DATA_DIR) / "oi_lab"
BUILD_VERSION = "v3"

ANALOG_COLS = ["pcr_log", "doi_bal", "vol_log", "d_ce_wall", "d_pe_wall",
               "ce_wall_share", "pe_wall_share", "ret_open", "decay", "skew"]
ANALOG_WEIGHTS = np.array([1.0, 1.5, 0.7, 1.0, 1.0, 0.6, 0.6, 1.5, 1.2, 0.5])


# ── buckets (shared by the tables and the live lookup) ───────────────
def dist_bucket(d: float) -> Optional[str]:
    if d is None or not np.isfinite(d):
        return None
    return "<0.25" if d < 0.25 else "0.25–0.5" if d < 0.5 else "0.5–1" if d < 1.0 else ">1"


DIST_ORDER = ["<0.25", "0.25–0.5", "0.5–1", ">1"]


def tod_bucket(cp: int) -> str:
    return "morning" if cp < 11 * 60 else "midday" if cp < 13 * 60 + 30 else "afternoon"


def dte_bucket(dte: float) -> str:
    return "expiry day" if dte <= 0 else "1–2 days" if dte <= 2 else "3+ days"


DTE_ORDER = ["expiry day", "1–2 days", "3+ days"]


def dom_bucket(share: float) -> str:
    # ~top quartile of wall share across 3 years of NIFTY (a flat window of 6 strikes is 0.17)
    return "dominant" if share is not None and np.isfinite(share) and share >= 0.25 else "spread"


def dte_fine(dte: float) -> str:
    return str(int(dte)) if dte <= 3 else "4+"


# ── build ────────────────────────────────────────────────────────────
def _und(underlying: Optional[str]) -> str:
    return (underlying or UNDERLYING).upper()


def _partitions(kind: str, underlying: str) -> list[Path]:
    base = MS.ROOT / f"kind={kind}" / f"underlying={underlying}"
    return sorted(base.rglob("*.parquet")) if base.exists() else []


def _months(underlying: str) -> list[tuple[int, int]]:
    out = set()
    for f in _partitions("options", underlying):
        out.add((int(f.parent.parent.name.split("=")[1]), int(f.parent.name.split("=")[1])))
    return sorted(out)


def available_underlyings() -> list[str]:
    """Underlyings with option bars on disk — each can have a study."""
    base = MS.ROOT / "kind=options"
    if not base.exists():
        return []
    return sorted(p.name.split("=", 1)[1] for p in base.iterdir()
                  if p.is_dir() and p.name.startswith("underlying=") and any(p.rglob("*.parquet")))


def signature(underlying: Optional[str] = None) -> str:
    u = _und(underlying)
    names = [f.name for f in _partitions("options", u)] + [f.name for f in _partitions("spot", u)]
    return hashlib.sha1(("|".join(sorted(names)) + BUILD_VERSION + u).encode()).hexdigest()[:12]


def _month_filter(underlying: str, y: int, m: int):
    return (ds.field("underlying") == underlying) & (ds.field("year") == y) & (ds.field("month") == m)


def infer_step(strikes: np.ndarray, spot: float) -> float:
    """Most common gap between listed strikes near spot (NIFTY 50, SENSEX 100, …)."""
    s = np.unique(np.asarray(strikes, float))
    s = s[np.abs(s - spot) <= max(spot * 0.04, 1.0)] if np.isfinite(spot) and spot > 0 else s
    d = np.diff(s)
    d = np.round(d[d > 0], 2)
    if not len(d):
        return 50.0
    vals, counts = np.unique(d, return_counts=True)
    return float(vals[np.argmax(counts)])


SUPPORTED_INTERVALS = (1, 5)


def bar_interval(ts: pd.Series) -> int:
    """Bar size in minutes of one series (most common gap between consecutive bars)."""
    t = pd.Series(pd.to_datetime(ts).unique()).sort_values()
    d = t.diff().dt.total_seconds().div(60)
    d = d[(d > 0) & (d <= 30)].round()
    return int(d.mode().iloc[0]) if len(d) else 1


def _spot_frame(underlying: str, y: int, m: int, options: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Spot minute bars for the month: the index file when uploaded, else the options' spot column."""
    d = MS._dataset("spot")
    s = pd.DataFrame()
    if d is not None:
        s = d.to_table(filter=_month_filter(underlying, y, m), columns=["timestamp", "high", "low", "close"]).to_pandas()
    source = "index file"
    if s.empty:
        sp = options.dropna(subset=["spot"])
        sp = sp[sp["spot"] > 0]
        if sp.empty:
            return pd.DataFrame(), "none"
        s = (sp.groupby("timestamp", as_index=False)["spot"].median()
             .rename(columns={"spot": "close"}))
        s["high"] = s["close"]
        s["low"] = s["close"]
        source = "options spot column"
    return s, source


def _spot_targets(s: pd.DataFrame) -> pd.DataFrame:
    """Per spot bar: what happens after it. Keyed by the bar's END minute (``end``), which is
    how option checkpoints are keyed too — so 1-minute and 5-minute files line up."""
    s = s.copy()
    s["date"] = s["timestamp"].dt.normalize()
    step = bar_interval(s.loc[s["date"] == s["date"].iloc[0], "timestamp"]) if len(s) else 1
    s["end"] = s["timestamp"].dt.hour * 60 + s["timestamp"].dt.minute + step
    s = s.sort_values(["date", "end"]).reset_index(drop=True)
    g = s.groupby("date")
    rev_hi = s.iloc[::-1].groupby("date")["high"].cummax().iloc[::-1]
    rev_lo = s.iloc[::-1].groupby("date")["low"].cummin().iloc[::-1]
    s["hi_after"] = rev_hi.groupby(s["date"]).shift(-1)       # strictly after this bar
    s["lo_after"] = rev_lo.groupby(s["date"]).shift(-1)
    s["day_close"] = g["close"].transform("last")
    s["day_high"] = g["high"].transform("max")
    s["day_low"] = g["low"].transform("min")
    fut = s[["date", "end", "close"]].rename(columns={"close": "close60"})
    fut["end"] = fut["end"] - 60
    return s.merge(fut, on=["date", "end"], how="left")


def _build_month(underlying: str, y: int, m: int, notes: Optional[dict] = None) -> pd.DataFrame:
    d = MS._dataset("options")
    o = d.to_table(filter=_month_filter(underlying, y, m),
                   columns=["timestamp", "expiry_date", "strike", "option_type", "close",
                            "volume", "oi", "iv", "spot"]).to_pandas()
    if o.empty:
        return pd.DataFrame()
    o["date"] = o["timestamp"].dt.normalize()
    o["expiry_date"] = pd.to_datetime(o["expiry_date"])
    o = o[o["expiry_date"] >= o["date"]]
    o = o[o["expiry_date"] == o.groupby("date")["expiry_date"].transform("min")]   # nearest expiry only
    keys = ["date", "strike", "option_type"]
    busiest = o.groupby(keys).size().idxmax()
    interval = bar_interval(o.loc[(o["date"] == busiest[0]) & (o["strike"] == busiest[1])
                                  & (o["option_type"] == busiest[2]), "timestamp"])
    if interval not in SUPPORTED_INTERVALS:
        if notes is not None:
            notes.setdefault("skipped_months", []).append(
                f"{y}-{m:02d}: {interval}-minute option bars (the study needs 1- or 5-minute bars)")
        return pd.DataFrame()
    o["bar"] = o["timestamp"].dt.hour * 60 + o["timestamp"].dt.minute
    o["cp"] = o["bar"] + interval                 # state AFTER the bar closes
    o = o.sort_values(keys + ["bar"])
    o["vol_cum"] = o.groupby(keys)["volume"].cumsum()
    o["oi_first"] = o.groupby(keys)["oi"].transform("first")
    ref = o.loc[o["cp"] == REF_MIN, keys + ["oi"]].rename(columns={"oi": "oi_ref"})

    spot_bars, spot_source = _spot_frame(underlying, y, m, o)
    if spot_bars.empty:
        if notes is not None:
            notes.setdefault("skipped_months", []).append(f"{y}-{m:02d}: no spot (upload the index file or include a spot column)")
        return pd.DataFrame()
    s = _spot_targets(spot_bars)

    o = o[(o["cp"] % CHECK_EVERY == 0) & (o["cp"] >= REF_MIN) & (o["cp"] <= LAST_CP)]
    o = o.merge(ref, on=keys, how="left")
    o["oi_open"] = o["oi_ref"].fillna(o["oi_first"])
    o = o.merge(s[["date", "end", "close"]].rename(columns={"end": "cp", "close": "spot_px"}), on=["date", "cp"], how="left")
    o["spot"] = o["spot_px"].fillna(o["spot"])
    o = o[o["spot"] > 0]
    if o.empty:
        return pd.DataFrame()

    step = infer_step(o["strike"].to_numpy(), float(o["spot"].median()))
    iv = o["iv"].astype(float)
    iv_missing = (iv.isna() | (iv <= 0.5)).mean() > 0.5
    if iv_missing:
        # only the strikes the features read: ATM and two steps out
        k = ((o["strike"] - (o["spot"] / step).round() * step) / step).round().abs()
        need = k.isin([0, 2]).to_numpy()
        T = ((o["expiry_date"] + pd.Timedelta(minutes=15 * 60 + 30)) - (o["date"] + pd.to_timedelta(o["cp"], unit="min"))
             ).dt.total_seconds().to_numpy() / vol.YEAR_S
        calc = np.full(len(o), np.nan)
        calc[need] = vol.implied_vol(o["close"].to_numpy(float)[need], o["spot"].to_numpy(float)[need],
                                     o["strike"].to_numpy(float)[need], T[need],
                                     (o["option_type"] == "CE").to_numpy()[need])
        o["iv"] = calc

    if notes is not None:
        notes.setdefault("steps", set()).add(step)
        notes.setdefault("intervals", set()).add(f"{interval}-minute options")
        notes.setdefault("spot_sources", set()).add(spot_source)
        if iv_missing:
            notes.setdefault("iv_computed_months", []).append(f"{y}-{m:02d}")

    day_num = o["date"].values.astype("datetime64[D]").astype(np.int64)
    c = pd.DataFrame({
        "key": day_num * 10000 + o["cp"].values, "cp": o["cp"].values,
        "strike": o["strike"].astype(float).values, "typ": o["option_type"].values,
        "close": o["close"].astype(float).values, "oi": o["oi"].astype(float).values,
        "oi_open": o["oi_open"].astype(float).values, "vol_cum": o["vol_cum"].astype(float).values,
        "iv": o["iv"].astype(float).values, "spot": o["spot"].astype(float).values,
        "dte": (o["expiry_date"] - o["date"]).dt.days.values,
    })
    st = FT.chain_state(c, step)
    if st.empty:
        return pd.DataFrame()
    session = pd.Series(st.index // 10000, index=st.index)
    spot_o, strad_o = FT.opening_refs(st, session)
    f = FT.features_from_state(st, spot_o, strad_o)
    out = st[["cp", "spot", "atm", "dte", "straddle", "atm_iv", "ce_wall", "pe_wall", "ce_oi", "pe_oi"]].join(f.drop(columns=["dte"]))
    out["straddle_open"] = strad_o
    out["date"] = pd.to_datetime(session.values, unit="D")
    out = out.reset_index(drop=True).merge(
        s[["date", "end", "hi_after", "lo_after", "day_close", "day_high", "day_low", "close60"]]
        .rename(columns={"end": "cp"}), on=["date", "cp"], how="left")
    strad = out["straddle"].where(out["straddle"] > 0)
    out["fwd_close_s"] = (out["day_close"] - out["spot"]) / strad
    out["fwd60_s"] = (out["close60"] - out["spot"]) / strad
    out["max_up_s"] = (out["hi_after"] - out["spot"]) / strad
    out["max_dn_s"] = (out["spot"] - out["lo_after"]) / strad
    out["ce_held"] = (out["hi_after"] < out["ce_wall"]).astype(float).where(out["hi_after"].notna())
    out["pe_held"] = (out["lo_after"] > out["pe_wall"]).astype(float).where(out["lo_after"].notna())
    out["up_close"] = np.where(out["day_close"] > out["spot"], 1.0,
                               np.where(out["day_close"] < out["spot"], 0.0, np.nan))
    out["up60"] = np.where(out["close60"] > out["spot"], 1.0,
                           np.where(out["close60"] < out["spot"], 0.0, np.nan))
    out["step"] = step
    float_cols = out.select_dtypes("float64").columns
    out[float_cols] = out[float_cols].astype("float32")
    return out


def build_table(underlying: Optional[str] = None, progress=None, notes: Optional[dict] = None) -> pd.DataFrame:
    u = _und(underlying)
    MS._sync(block=True)
    months = _months(u)
    parts = []
    for i, (y, m) in enumerate(months):
        try:
            part = _build_month(u, y, m, notes)
            if not part.empty:
                parts.append(part)
        except Exception as exc:
            logger.warning("OI Lab history %s: %d-%02d skipped: %s", u, y, m, exc)
            if notes is not None:
                notes.setdefault("skipped_months", []).append(f"{y}-{m:02d}: {type(exc).__name__}: {exc}"[:160])
        if progress:
            progress(i + 1, len(months))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def holdout_for(dates: pd.Series) -> str:
    """The standard holdout date when it leaves a fair test set, else the last 30% of sessions."""
    days = np.sort(pd.to_datetime(dates).unique())
    n = len(days)
    std = np.datetime64(HOLDOUT_START)
    after = int((days >= std).sum())
    if after >= max(20, int(0.2 * n)) and n - after >= MIN_SESSIONS_MODELS:
        return HOLDOUT_START
    return str(pd.Timestamp(days[int(n * 0.7)]).date()) if n else HOLDOUT_START


# ── the fitted study ─────────────────────────────────────────────────
class HistoryStudy:
    def __init__(self, H: pd.DataFrame, sig: str, underlying: str = UNDERLYING, notes: Optional[dict] = None):
        self.H = H
        self.sig = sig
        self.underlying = underlying
        self.notes = notes or {}
        self.sessions = int(H["date"].nunique())
        self.first = str(H["date"].min().date())
        self.last = str(H["date"].max().date())
        self.step = float(H["step"].mode().iloc[0]) if "step" in H and H["step"].notna().any() else None
        self.holdout = holdout_for(H["date"])
        self.has_models = self.sessions >= MIN_SESSIONS_MODELS
        self._fit_models()
        self._wall_tables()
        self._expiry_curves()
        self._analog_prep()

    # models ------------------------------------------------------------
    # Probability models. The three wall models are where OI has real predictive power;
    # the two direction models are kept, scored and shown so nobody has to take that on trust.
    CLASSIFIERS = {
        "ce_held": ("ce_held", "Call wall holds — spot does not trade at or above it before the close", "wall"),
        "pe_held": ("pe_held", "Put wall holds — spot does not trade at or below it before the close", "wall"),
        "inside": ("inside", "Range day — both walls hold until the close", "wall"),
        "close": ("up_close", "Direction — spot closes the day above the current level", "direction"),
        "60m": ("up60", "Direction — spot is higher 60 minutes from now", "direction"),
    }
    REACH = {
        "up": ("max_up_s", "How far spot still rises before the close"),
        "down": ("max_dn_s", "How far spot still falls before the close"),
    }

    def _rows(self, target: str) -> pd.DataFrame:
        H = self.H
        last = 14 * 60 + 25 if target == "up60" else 14 * 60 + 30
        return H[(H["cp"] <= last) & ((H["cp"] - REF_MIN) % 15 == 0) & H[target].notna()]

    def _fit_models(self):
        ho = pd.Timestamp(self.holdout)
        H = self.H
        H["inside"] = ((H["ce_held"] == 1) & (H["pe_held"] == 1)).astype("float32").where(
            H["ce_held"].notna() & H["pe_held"].notna())
        self.models, self.model_report = {}, {}
        self.reach, self.reach_report = {}, {}
        if not self.has_models:
            return
        for name, (target, text, kind) in self.CLASSIFIERS.items():
            rows = self._rows(target)
            X, y = rows[FT.FEATURES].to_numpy(float), rows[target].to_numpy(float)
            if len(np.unique(y)) < 2:
                continue
            tr, te = (rows["date"] < ho).to_numpy(), (rows["date"] >= ho).to_numpy()
            rep = {"target": text, "kind": kind, "train_sessions": int(rows.loc[tr, "date"].nunique()),
                   "test_sessions": int(rows.loc[te, "date"].nunique()), "holdout_start": self.holdout}
            if tr.sum() > 200 and te.sum() > 60 and len(np.unique(y[tr])) == 2:
                oos = Logit().fit(X[tr], y[tr])
                rep["out_of_sample"] = evaluate(y[te], oos.predict(X[te]))
                rep["in_sample"] = evaluate(y[tr], oos.predict(X[tr]))
            else:
                rep["out_of_sample"] = {"n": int(te.sum())}
            rep["verdict"] = verdict(rep["out_of_sample"].get("auc"))
            final = Logit().fit(X, y)                      # live uses all data; the OOS score above is the honest one
            rep["drivers"] = [{**d, "label": FT.LABELS.get(d["feature"], d["feature"])}
                              for d in final.coefficients(FT.FEATURES)]
            self.models[name] = final
            self.model_report[name] = rep
        for name, (target, text) in self.REACH.items():
            rows = self._rows(target)
            if len(rows) < 100:
                continue
            X, y = rows[FT.FEATURES].to_numpy(float), rows[target].to_numpy(float)
            tr, te = (rows["date"] < ho).to_numpy(), (rows["date"] >= ho).to_numpy()
            rep = {"target": text, "train_sessions": int(rows.loc[tr, "date"].nunique()),
                   "test_sessions": int(rows.loc[te, "date"].nunique())}
            if tr.sum() > 200 and te.sum() > 60:
                rep["out_of_sample"] = Reach().fit(X[tr], y[tr]).evaluate(X[te], y[te])
            self.reach[name] = Reach().fit(X, y)
            self.reach_report[name] = rep

    # wall odds ---------------------------------------------------------
    def _wall_tables(self):
        H = self.H
        self.wall = {}
        grids = {}
        for side in ("ce", "pe"):
            d = pd.DataFrame({
                "dist": H[f"d_{side}_wall"].map(dist_bucket), "tod": H["cp"].map(tod_bucket),
                "dte": H["dte"].map(dte_bucket), "dom": H[f"{side}_wall_share"].map(dom_bucket),
                "held": H[f"{side}_held"], "date": H["date"],
            }).dropna(subset=["dist", "held"])
            levels = {4: ["dist", "tod", "dte", "dom"], 3: ["dist", "tod", "dte"], 2: ["dist", "dte"], 1: ["dist"]}
            tables = {}
            for lvl, cols in levels.items():
                g = d.groupby(cols).agg(rate=("held", "mean"), sessions=("date", "nunique"))
                tables[lvl] = {k if isinstance(k, tuple) else (k,): (float(r.rate), int(r.sessions))
                               for k, r in g.iterrows()}
            self.wall[side] = (levels, tables)
            g2 = d.groupby(["dte", "dist"]).agg(rate=("held", "mean"), sessions=("date", "nunique")).reset_index()
            grids[side] = [{"dte": r.dte, "dist": r.dist, "held_pct": round(r.rate * 100, 1), "sessions": int(r.sessions)}
                           for r in g2.itertuples()]
        self.wall_grid = grids

    def wall_odds(self, side: str, dist: float, cp: int, dte: float, share: float, min_sessions: int = 40) -> Optional[dict]:
        b = dist_bucket(dist)
        if b is None:
            return None
        levels, tables = self.wall[side]
        vals = {"dist": b, "tod": tod_bucket(cp), "dte": dte_bucket(dte), "dom": dom_bucket(share)}
        for lvl in (4, 3, 2, 1):
            key = tuple(vals[c] for c in levels[lvl])
            hit = tables[lvl].get(key)
            if hit and hit[1] >= min_sessions:
                return {"held_pct": round(hit[0] * 100, 1), "sessions": hit[1],
                        "basis": " · ".join(key)}
        return None

    # expiry behaviour --------------------------------------------------
    def _expiry_curves(self):
        H = self.H.assign(dte_f=self.H["dte"].map(dte_fine), abs_move=self.H["ret_open"].abs())
        curves = {}
        for k, g in H.groupby("dte_f"):
            q = g.groupby("cp").agg(decay_med=("decay", "median"),
                                    decay_p25=("decay", lambda s: s.quantile(0.25)),
                                    decay_p75=("decay", lambda s: s.quantile(0.75)),
                                    move_med=("abs_move", "median"), sessions=("date", "nunique"))
            curves[k] = [{"cp": int(cp), "time": hhmm(cp), **{c: (round(float(v), 4) if pd.notna(v) else None)
                                                              if c != "sessions" else int(v)
                                                              for c, v in r.items()}}
                         for cp, r in q.iterrows()]
        self.curves = curves
        E = self.H[self.H["dte"] == 0]
        rows = []
        for day, g in E.groupby("date"):
            at10 = g[g["cp"] == 600]
            end = g[g["cp"] == LAST_CP]
            if at10.empty or end.empty:
                continue
            a, e = at10.iloc[0], end.iloc[0]
            if not all(np.isfinite([a.spot, a.ce_wall, a.pe_wall, a.straddle, a.day_close, e.straddle])):
                continue
            rows.append({
                "date": str(day.date()), "spot_1000": round(float(a.spot), 1),
                "ce_wall_1000": float(a.ce_wall), "pe_wall_1000": float(a.pe_wall),
                "straddle_1000": round(float(a.straddle), 1), "close": round(float(a.day_close), 1),
                "day_high": round(float(a.day_high), 1), "day_low": round(float(a.day_low), 1),
                "straddle_1525": round(float(e.straddle), 1),
                "ce_held": bool(a.ce_held == 1), "pe_held": bool(a.pe_held == 1),
                "close_inside": bool(a.pe_wall < a.day_close < a.ce_wall),
                "move_from_1000": round(float(a.day_close - a.spot), 1),
            })
        self.expiries = rows
        n = len(rows)
        self.expiry_summary = {
            "expiry_days": n,
            "both_walls_held_pct": round(100 * sum(r["ce_held"] and r["pe_held"] for r in rows) / n, 1) if n else None,
            "close_inside_pct": round(100 * sum(r["close_inside"] for r in rows) / n, 1) if n else None,
            "median_abs_move_from_1000": round(float(np.median([abs(r["move_from_1000"]) for r in rows])), 1) if n else None,
            "median_straddle_decay_pct": round(float(np.median([(r["straddle_1525"] / r["straddle_1000"] - 1) * 100
                                                                for r in rows if r["straddle_1000"]])), 1) if n else None,
        }

    # analogs -----------------------------------------------------------
    def _analog_prep(self):
        X = self.H[ANALOG_COLS].to_numpy(float)
        with np.errstate(all="ignore"):
            self._amean = np.nan_to_num(np.nanmean(X, axis=0))
            sd = np.nan_to_num(np.nanstd(X, axis=0))
        self._astd = np.where(sd > 1e-9, sd, 1.0)
        self._dte_f = self.H["dte"].map(dte_fine).to_numpy()

    def analogs(self, feat: dict, cp: int, dte: float, straddle_now: float, k: int = 30,
                exclude=None) -> Optional[dict]:
        """The ``k`` past sessions whose chain at this time of day looked most like ``feat``.

        Direction among analogs was tested out of sample and carries no edge (AUC ≈ 0.50);
        their ranges and wall outcomes are the useful part."""
        H = self.H
        if cp > LAST_CP:
            return None
        mask = (np.abs(H["cp"].to_numpy() - cp) <= 10) & (self._dte_f == dte_fine(dte)) & H["fwd_close_s"].notna().to_numpy()
        if exclude is not None:
            mask &= (H["date"] != pd.Timestamp(exclude)).to_numpy()
        cand = H[mask]
        if cand.empty:
            return None
        cand = cand.assign(dcp=(cand["cp"] - cp).abs()).sort_values(["date", "dcp"]).drop_duplicates("date")
        Z = np.nan_to_num((cand[ANALOG_COLS].to_numpy(float) - self._amean) / self._astd)
        q = np.nan_to_num((np.array([feat.get(c, np.nan) for c in ANALOG_COLS], float) - self._amean) / self._astd)
        dist = np.sqrt((((Z - q) ** 2) * ANALOG_WEIGHTS).sum(axis=1))
        order = np.argsort(dist)[:k]
        top = cand.iloc[order].assign(adist=dist[order])
        s = straddle_now if straddle_now and straddle_now > 0 else None
        pts = (lambda v: round(float(v) * s, 1) if pd.notna(v) else None) if s else (lambda v: None)
        fwd = top["fwd_close_s"].to_numpy(float)
        items = [{
            "date": str(r.date.date()), "time": hhmm(int(r.cp)), "similarity": round(float(1 / (1 + r.adist)) * 100, 1),
            "close_move_pts": pts(r.fwd_close_s), "max_up_pts": pts(r.max_up_s), "max_down_pts": pts(r.max_dn_s),
            "ce_held": bool(r.ce_held == 1), "pe_held": bool(r.pe_held == 1),
        } for r in top.itertuples()]
        return {
            "candidates": int(len(cand)), "used": int(len(top)),
            "pct_up": round(float((fwd > 0).mean() * 100), 1),
            "median_move_pts": pts(np.median(fwd)), "p25_move_pts": pts(np.percentile(fwd, 25)),
            "p75_move_pts": pts(np.percentile(fwd, 75)),
            "median_max_up_pts": pts(top["max_up_s"].median()), "median_max_down_pts": pts(top["max_dn_s"].median()),
            "ce_wall_held_pct": round(float(top["ce_held"].mean() * 100), 1),
            "pe_wall_held_pct": round(float(top["pe_held"].mean() * 100), 1),
            "days": items[:15],
        }

    # live read ---------------------------------------------------------
    @staticmethod
    def _x(feat: dict) -> np.ndarray:
        return np.array([[feat.get(c, np.nan) for c in FT.FEATURES]], float)

    def predict(self, feat: dict, straddle_now: float) -> dict:
        x = self._x(feat)
        probs = {}
        for name, mdl in self.models.items():
            rep = self.model_report[name]
            probs[name] = {"p": round(float(mdl.predict(x)[0]), 3), "label": rep["target"], "kind": rep["kind"],
                           "oos_auc": rep["out_of_sample"].get("auc"), "grade": rep["verdict"]["grade"]}
        reach = {}
        s = straddle_now if straddle_now and straddle_now > 0 else None
        for name, mdl in self.reach.items():
            q = mdl.quantiles(x)[0]
            reach[name] = {
                "p20_s": round(float(q[0]), 3), "median_s": round(float(q[1]), 3), "p80_s": round(float(q[2]), 3),
                "p20_pts": round(float(q[0]) * s, 1) if s else None,
                "median_pts": round(float(q[1]) * s, 1) if s else None,
                "p80_pts": round(float(q[2]) * s, 1) if s else None,
                "oos_corr": (self.reach_report[name].get("out_of_sample") or {}).get("corr"),
            }
        return {"probabilities": probs, "reach": reach}

    def reach_odds(self, feat: dict, direction: str, pts: float, straddle_now: float) -> Optional[float]:
        """Historical odds that spot travels at least ``pts`` in ``direction`` before the close."""
        if not straddle_now or straddle_now <= 0 or pts is None or direction not in self.reach:
            return None
        return round(self.reach[direction].prob_at_least(self._x(feat), pts / straddle_now), 3)

    def report(self) -> dict:
        notes = {k: (sorted(v) if isinstance(v, set) else v) for k, v in self.notes.items()}
        return {
            "underlying": self.underlying, "sessions": self.sessions, "first": self.first, "last": self.last,
            "rows": int(len(self.H)), "holdout_start": self.holdout, "step": self.step,
            "has_models": self.has_models, "min_sessions_models": MIN_SESSIONS_MODELS,
            "models": self.model_report, "reach_models": self.reach_report, "wall_grid": self.wall_grid,
            "dist_order": DIST_ORDER, "dte_order": DTE_ORDER,
            "expiry_curves": self.curves, "recent_expiries": self.expiries[-20:][::-1],
            "expiry_summary": self.expiry_summary,
            "feature_labels": FT.LABELS, "notes": notes,
        }


# ── lifecycle: one cached study per underlying, built in the background ─────
_SIG_CHECK_S = 600
_studies: dict[str, HistoryStudy] = {}
_states: dict[str, dict] = {}
_last_sig_check: dict[str, float] = {}
_lock = threading.Lock()


def _state(u: str) -> dict:
    return _states.setdefault(u, {"status": "idle", "done": 0, "total": 0, "error": None,
                                  "built_at": None, "seconds": None, "error_sig": None})


def status(underlying: Optional[str] = None) -> dict:
    u = _und(underlying)
    s = {k: v for k, v in _state(u).items() if k != "error_sig"}
    s["underlying"] = u
    study = _studies.get(u)
    if study is not None:
        s.update(sessions=study.sessions, first=study.first, last=study.last, has_models=study.has_models)
    return s


def all_status() -> list[dict]:
    return [status(u) for u in sorted(set(available_underlyings()) | set(_studies) | {UNDERLYING})]


def get(underlying: Optional[str] = None) -> Optional[HistoryStudy]:
    """The fitted study for ``underlying`` (None until its first build finishes).

    Every few minutes the store's file hashes are compared with the ones the study was built
    from; after an upload the study rebuilds in the background while the old one keeps serving."""
    u = _und(underlying)
    ensure_started(u)
    study = _studies.get(u)
    now = time.monotonic()
    if study is not None and now - _last_sig_check.get(u, 0.0) > _SIG_CHECK_S:
        _last_sig_check[u] = now
        try:
            if signature(u) != study.sig:
                logger.info("OI Lab history %s: Market Store changed — rebuilding", u)
                ensure_started(u, force=True)
        except Exception as exc:
            logger.debug("history signature check failed: %s", exc)
    return study


def ensure_started(underlying: Optional[str] = None, force: bool = False) -> None:
    u = _und(underlying)
    with _lock:
        st = _state(u)
        if st["status"] == "building" or (u in _studies and not force):
            return
        if not force and st["status"] == "error":
            # don't retry a failed build until the data for it changes
            try:
                if signature(u) == st.get("error_sig"):
                    return
            except Exception:
                return
        st.update(status="building", done=0, total=0, error=None)
    threading.Thread(target=_build, args=(u, force), daemon=True, name=f"oi-lab-history-{u}").start()


def _build(u: str, force: bool) -> None:
    t0 = time.monotonic()
    st = _state(u)
    sig = None
    try:
        MS._sync(block=True)
        sig = signature(u)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"history_{u}_{sig}.parquet"
        notes: dict = {}
        if path.exists() and not force:
            H = pd.read_parquet(path)
        else:
            def prog(i, n):
                st.update(done=i, total=n)
            H = build_table(u, prog, notes)
            if H.empty:
                reason = "; ".join(notes.get("skipped_months", [])[:3])
                raise RuntimeError(f"no usable {u} option history in the Market Store"
                                   + (f" ({reason})" if reason else " — upload it in the Data Ingestion Lab"))
            H.to_parquet(path, compression="zstd", index=False)
            for old in CACHE_DIR.glob("history_*.parquet"):
                # older builds of this underlying, and the pre-v2 single-underlying cache (history_<sig>)
                if old != path and (old.name.startswith(f"history_{u}_") or old.stem.count("_") == 1):
                    old.unlink(missing_ok=True)
        study = HistoryStudy(H, sig, u, notes)
        _studies[u] = study
        _last_sig_check[u] = time.monotonic()
        st.update(status="ready", built_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                  seconds=round(time.monotonic() - t0, 1), error=None, error_sig=None)
        logger.info("OI Lab history %s ready: %d sessions, %d rows (%.1fs)", u, study.sessions, len(H), time.monotonic() - t0)
    except Exception as exc:
        logger.error("OI Lab history %s build failed: %s | %s", u, exc, traceback.format_exc())
        st.update(status="error", error=f"{exc}"[:300], error_sig=sig)
