"""
Backtest of the Gann × OI signals (``signals.py``) on REAL option premiums from the Market Store.

For every stored session of an underlying it rebuilds, at each 5-minute close, the same ``Ctx``
the live desk builds from Kite, fires the same rules, and trades the actual contract:

  NEXT-BAR FILL     decision on the 5-min close at t, fill at the OPEN of the bar starting at t
  ADVERSE FIRST     inside a bar the premium stop is tested before any target
  REAL CONTRACT     the strike the rules pick (ATM by default), fixed by name for the trade's life
  STALE LEGS        if the held contract stops printing (it left the stored strike window) the
                    trade exits at its last traded price and is counted as ``stale``
  REAL COSTS        Zerodha statutory charges on the actual premiums + slippage per side
  HONEST ODDS       wall-hold probabilities come from models fitted ONLY on sessions before the
                    holdout date; results are reported separately before and after it

Two books run independently per session — SWING and SCALP — each with at most one open
position, three trades a day and a 15-minute cooldown after an exit.
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from core.logger import get_logger
from research.market_store import store as MS
from research.oi_lab import features as FT
from research.oi_lab import history as HS
from research.oi_lab import signals as SG
from research.oi_lab.indices import REF_MIN, hhmm
from research.oi_lab.model import Logit
from research.options_lab.execution import CostModel

logger = get_logger("research.oi_lab.signal_backtest")

LOT = {"NIFTY": 65, "SENSEX": 20, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120, "BANKEX": 30}
SLIPPAGE = {"NIFTY": 0.5, "SENSEX": 1.0}
MAX_TRADES_DAY = 3
COOLDOWN_MIN = 15
STALE_GAP_MIN = 10


def lot_for(u: str) -> int:
    return LOT.get(u.upper(), 50)


def _probabilities(study: "HS.HistoryStudy") -> pd.DataFrame:
    """Wall-hold probabilities for every checkpoint from models fitted before the holdout only."""
    H = study.H
    ho = pd.Timestamp(study.holdout)
    out = H[["date", "cp"]].copy()
    X = H[FT.FEATURES].to_numpy(float)
    for target, col in (("ce_held", "p_ce_held"), ("pe_held", "p_pe_held")):
        train = H[(H["date"] < ho) & H[target].notna() & ((H["cp"] - REF_MIN) % 15 == 0)]
        if len(train) < 200 or train[target].nunique() < 2:
            out[col] = np.nan
            continue
        mdl = Logit().fit(train[FT.FEATURES].to_numpy(float), train[target].to_numpy(float))
        out[col] = mdl.predict(X)
    return out


class _Contract:
    __slots__ = ("start", "open", "high", "low", "close")

    def __init__(self, g: pd.DataFrame):
        g = g.sort_values("start")
        self.start = g["start"].to_numpy(int)
        self.open = g["open"].to_numpy(float)
        self.high = g["high"].to_numpy(float)
        self.low = g["low"].to_numpy(float)
        self.close = g["close"].to_numpy(float)

    def first_from(self, minute: int) -> int:
        return int(np.searchsorted(self.start, minute, side="left"))


def _simulate_day(u: str, day, mode: str, hday: pd.DataFrame, spot: dict, contracts: dict, step: float,
                  costs: CostModel, slip: float, lot: int, holdout: pd.Timestamp, skips: dict,
                  prm: Optional[dict] = None) -> list[dict]:
    prm = prm or SG.DEFAULTS
    trades = []
    pos: Optional[dict] = None
    n_today, cooldown_until = 0, 0
    s_end, s_high, s_low, s_close, s_open = spot["end"], spot["high"], spot["low"], spot["close"], spot["open"]

    def bar_at(cp: int) -> Optional[SG.Bar]:
        i0, i1 = np.searchsorted(s_end, cp - 5, side="right"), np.searchsorted(s_end, cp, side="right")
        if i1 <= i0 or s_end[i1 - 1] != cp:
            return None
        return SG.Bar(cp, float(s_open[i0]), float(s_high[i0:i1].max()), float(s_low[i0:i1].min()), float(s_close[i1 - 1]))

    def close_pos(p: dict, minute: int, price: float, reason: str, stale: bool = False):
        nonlocal pos, cooldown_until
        exit_px = max(price - slip, 0.05)
        pts = exit_px - p["entry"]
        rupees = pts * lot - costs.round_trip(p["entry"], exit_px, lot)
        trades.append({
            "date": str(day.date()), "underlying": u, "mode": mode, "setup": p["setup"], "side": p["side"],
            "contract": p["contract"], "strike": p["strike"], "level": p["level"], "dte": p["dte"],
            "entry_time": hhmm(p["entry_min"]), "exit_time": hhmm(minute), "hold_min": minute - p["entry_min"],
            "spot_entry": p["spot_entry"], "entry": round(p["entry"], 2), "exit": round(exit_px, 2),
            "pts": round(pts, 2), "rupees": round(rupees, 2), "exit_reason": reason, "stale": stale,
            "mfe": round(p["mfe"], 2), "mae": round(p["mae"], 2), "holdout": day >= holdout,
        })
        cooldown_until = minute + COOLDOWN_MIN
        pos = None

    def walk(p: dict, t0: int, t1: int) -> bool:
        """Advance an open position through option bars starting in [t0, t1). True if it closed."""
        k: _Contract = p["bars"]
        i = k.first_from(t0)
        last_seen = p.get("last_bar_min", p["entry_min"])
        while i < len(k.start) and k.start[i] < t1:
            m = int(k.start[i])
            if m - last_seen > STALE_GAP_MIN:
                close_pos(p, last_seen, p["last_close"], "STALE", stale=True)
                return True
            if m >= SG.SQUAREOFF:
                close_pos(p, m, k.open[i], "SQUAREOFF")
                return True
            if mode == "SCALP" and m >= p["entry_min"] + SG.SCALP_MAX_HOLD:
                close_pos(p, m, k.open[i], "TIME")
                return True
            o, h, lo, c = k.open[i], k.high[i], k.low[i], k.close[i]
            p["mfe"] = max(p["mfe"], h - p["entry"])
            p["mae"] = min(p["mae"], lo - p["entry"])
            if lo <= p["stop"]:                                  # adverse first
                close_pos(p, m, min(o, p["stop"]), "STOP" if p["stop"] < p["entry"] else "BREAKEVEN")
                return True
            if mode == "SCALP" and h >= p["target"]:
                close_pos(p, m, max(o, p["target"]), "TARGET")
                return True
            if mode == "SWING":
                j0, j1 = np.searchsorted(s_end, m, side="right"), np.searchsorted(s_end, m + p["opt_interval"], side="right")
                if j1 > j0:
                    hit = (s_high[j0:j1].max() >= p["spot_target"]) if p["side"] == "CE" else (s_low[j0:j1].min() <= p["spot_target"])
                    if hit:
                        close_pos(p, m, c, "TARGET")
                        return True
                if h >= p["trail_after"]:
                    p["stop"] = max(p["stop"], p["entry"])            # applies from the next bar
            p["last_bar_min"], p["last_close"] = m, c
            last_seen = m
            i += 1
        return False

    prev_close = None
    for r in hday.itertuples():
        cp = int(r.cp)
        bar = bar_at(cp)
        if bar is None:
            continue
        if pos is not None and walk(pos, pos.get("cursor", pos["entry_min"]), cp):
            pass
        elif pos is not None:
            pos["cursor"] = cp
        c = SG.Ctx(underlying=u, day=day.date(), cp=cp, bar=bar,
                   prev_close=prev_close if prev_close is not None else np.nan,
                   straddle=float(r.straddle) if pd.notna(r.straddle) else np.nan, step=step, dte=int(r.dte),
                   ce_wall=float(r.ce_wall) if pd.notna(r.ce_wall) else None,
                   pe_wall=float(r.pe_wall) if pd.notna(r.pe_wall) else None,
                   doi_bal=float(r.doi_bal) if pd.notna(r.doi_bal) else None,
                   p_ce_held=round(float(r.p_ce_held), 3) if pd.notna(r.p_ce_held) else None,
                   p_pe_held=round(float(r.p_pe_held), 3) if pd.notna(r.p_pe_held) else None)
        prev_close = bar.close
        if pos is not None and mode == "SWING":
            alerts, pos["adverse_streak"] = SG.exit_alerts(pos, c, prm["alerts"])
            if alerts:
                k: _Contract = pos["bars"]
                i = k.first_from(cp)
                if i < len(k.start) and k.start[i] - cp <= 3:
                    close_pos(pos, int(k.start[i]), k.open[i], f"ALERT:{alerts[0]['code']}")
                    continue
        if pos is not None or n_today >= MAX_TRADES_DAY or cp < cooldown_until:
            continue
        fired, _ = SG.evaluate(c, prm)
        if not fired:
            continue
        sig = fired[0]
        key = (sig["strike"], sig["side"])
        name = contracts["by_key"].get(key)
        if name is None:
            skips["strike_not_stored"] = skips.get("strike_not_stored", 0) + 1
            continue
        k: _Contract = contracts["bars"][name]
        i = k.first_from(cp)
        if i >= len(k.start) or k.start[i] - cp > 3:
            skips["no_fill_bar"] = skips.get("no_fill_bar", 0) + 1
            continue
        entry = float(k.open[i]) + slip
        plan = SG.plans(sig, entry)
        n_today += 1
        pos = {
            "setup": sig["setup"], "side": sig["side"], "contract": name, "strike": sig["strike"], "level": sig["level"],
            "dte": int(r.dte), "entry": entry, "entry_min": int(k.start[i]), "spot_entry": bar.close,
            "spot_target": sig["spot_target"], "invalidation": sig["invalidation"],
            "entry_doi_bal": c.doi_bal, "adverse_streak": 0, "bars": k, "opt_interval": contracts["interval"],
            "stop": plan["SCALP"]["stop"] if mode == "SCALP" else plan["SWING"]["premium_stop"],
            "target": plan["SCALP"]["target1"], "trail_after": plan["SWING"]["trail_after"],
            "mfe": 0.0, "mae": 0.0, "last_close": entry, "cursor": int(k.start[i]),
        }
    if pos is not None:
        if not walk(pos, pos.get("cursor", pos["entry_min"]), 24 * 60) and pos is not None:
            close_pos(pos, pos.get("last_bar_min", pos["entry_min"]), pos["last_close"], "END_OF_DATA", stale=True)
    return trades


def run(underlying: str, progress=None, prm: Optional[dict] = None,
        until: Optional[str] = None) -> tuple[pd.DataFrame, dict]:
    """Replay the rules over every stored session (or only the sessions before ``until``)."""
    u = underlying.upper()
    prm = SG.params(prm)
    study = HS._studies.get(u)
    if study is None:
        raise RuntimeError(f"the {u} OI study is not built yet")
    probs = _probabilities(study)
    H = study.H.drop(columns=[c for c in ("p_ce_held", "p_pe_held") if c in study.H]).join(probs[["p_ce_held", "p_pe_held"]])
    H = H.sort_values(["date", "cp"])
    holdout = pd.Timestamp(study.holdout)
    costs = CostModel(slippage_pts=0.0)
    slip = SLIPPAGE.get(u, 0.75)
    lot = lot_for(u)
    step = study.step or 50.0
    skips: dict = {}
    trades: list[dict] = []
    months = HS._months(u)
    if until:
        cut = pd.Timestamp(until)
        months = [(y, m) for y, m in months if pd.Timestamp(year=y, month=m, day=1) < cut]
        H = H[H["date"] < cut]
    d = MS._dataset("options")
    for mi, (y, m) in enumerate(months):
        try:
            o = d.to_table(filter=HS._month_filter(u, y, m),
                           columns=["timestamp", "contract", "expiry_date", "strike", "option_type",
                                    "open", "high", "low", "close", "spot"]).to_pandas()
            if o.empty:
                continue
            o["date"] = o["timestamp"].dt.normalize()
            o["expiry_date"] = pd.to_datetime(o["expiry_date"])
            o = o[o["expiry_date"] >= o["date"]]
            o = o[o["expiry_date"] == o.groupby("date")["expiry_date"].transform("min")]
            o["start"] = o["timestamp"].dt.hour * 60 + o["timestamp"].dt.minute
            sp, _src = HS._spot_frame(u, y, m, o)
            if sp.empty:
                continue
            sp = sp.copy()
            if "open" not in sp:
                sp["open"] = sp["close"]
            sp["date"] = sp["timestamp"].dt.normalize()
            s_int = HS.bar_interval(sp.loc[sp["date"] == sp["date"].iloc[0], "timestamp"])
            sp["end"] = sp["timestamp"].dt.hour * 60 + sp["timestamp"].dt.minute + s_int
            Hm = H[(H["date"].dt.year == y) & (H["date"].dt.month == m)]
            for day, og in o.groupby("date"):
                hday = Hm[Hm["date"] == day]
                sday = sp[sp["date"] == day].sort_values("end")
                if hday.empty or sday.empty:
                    continue
                spot = {k: sday[k].to_numpy(float) for k in ("open", "high", "low", "close")}
                spot["end"] = sday["end"].to_numpy(int)
                one = og[og["contract"] == og["contract"].iloc[0]]
                contracts = {"bars": {name: _Contract(g) for name, g in og.groupby("contract")},
                             "by_key": {(float(k), t): name for (k, t), name in
                                        og.groupby(["strike", "option_type"])["contract"].first().items()},
                             "interval": HS.bar_interval(one["timestamp"])}
                for mode in SG.MODES:
                    trades.extend(_simulate_day(u, day, mode, hday, spot, contracts, step, costs, slip, lot, holdout,
                                                skips, prm))
        except Exception as exc:
            logger.warning("signal backtest %s %d-%02d skipped: %s", u, y, m, exc)
            skips[f"month_error {y}-{m:02d}"] = str(exc)[:120]
        if progress:
            progress(mi + 1, len(months))
    meta = {"underlying": u, "holdout": study.holdout, "lot": lot, "slippage_pts": slip, "skips": skips,
            "sessions": study.sessions, "first": study.first, "last": study.last, "rules_version": SG.RULES_VERSION,
            "params": prm}
    return pd.DataFrame(trades), meta


# ── summaries ────────────────────────────────────────────────────────
def _stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    r = t["rupees"]
    wins, losses = r[r > 0], r[r < 0]
    eq = r.cumsum()
    n = len(r)
    return {
        "trades": int(n), "win_rate": round(float((r > 0).mean() * 100), 1),
        "avg_pts": round(float(t["pts"].mean()), 2), "avg_rupees": round(float(r.mean())),
        "total_rupees": round(float(r.sum())),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) and losses.sum() else None,
        "t_stat": round(float(r.mean() / (r.std() / np.sqrt(n))), 2) if n > 2 and r.std() > 0 else None,
        "max_drawdown": round(float((eq - eq.cummax()).min())),
        "avg_hold_min": round(float(t["hold_min"].mean()), 1), "stale": int(t["stale"].sum()),
    }


def verdict(s: dict) -> dict:
    n, t, avg = s.get("trades", 0), s.get("t_stat"), s.get("avg_rupees", 0)
    if n < 20:
        return {"grade": "thin", "text": f"Only {n} trades after the holdout date — too few to judge."}
    if avg > 0 and t is not None and t >= 2:
        return {"grade": "proven", "text": "Profitable on unseen sessions with statistical weight (t ≥ 2)."}
    if avg > 0:
        return {"grade": "promising", "text": "Profitable on unseen sessions, but not yet statistically proven."}
    return {"grade": "losing", "text": "Lost money on unseen sessions after costs — paper trade only."}


def summarize(trades: pd.DataFrame, meta: dict) -> dict:
    out = {**meta, "generated": time.strftime("%Y-%m-%d %H:%M:%S"), "by_setup": [], "by_mode": [], "exits": [],
           "monthly": [], "equity": {}, "recent": []}
    if trades.empty:
        return out
    t = trades.sort_values(["date", "entry_time"])
    for (setup, mode), g in t.groupby(["setup", "mode"]):
        tr, ho = _stats(g[~g["holdout"]]), _stats(g[g["holdout"]])
        out["by_setup"].append({"setup": setup, "label": SG.SETUPS[setup]["label"], "side": SG.SETUPS[setup]["side"],
                                "mode": mode, "train": tr, "holdout": ho, "all": _stats(g), "verdict": verdict(ho)})
    for mode, g in t.groupby("mode"):
        ho = _stats(g[g["holdout"]])
        out["by_mode"].append({"mode": mode, "train": _stats(g[~g["holdout"]]), "holdout": ho, "all": _stats(g),
                               "verdict": verdict(ho)})
        eq = g.assign(cum=g["rupees"].cumsum())
        out["equity"][mode] = [{"date": a, "equity": round(float(b)), "holdout": bool(h)}
                               for a, b, h in zip(eq["date"], eq["cum"], eq["holdout"])]
        for mon, gm in g.groupby(g["date"].str[:7]):
            s = _stats(gm)
            out["monthly"].append({"mode": mode, "month": mon, "trades": s["trades"], "total_rupees": s["total_rupees"],
                                   "win_rate": s["win_rate"]})
    ex = t.groupby(["mode", "exit_reason"]).agg(trades=("rupees", "size"), avg_rupees=("rupees", "mean")).reset_index()
    out["exits"] = [{"mode": r.mode, "reason": r.exit_reason, "trades": int(r.trades), "avg_rupees": round(float(r.avg_rupees))}
                    for r in ex.itertuples()]
    out["recent"] = t.tail(40).iloc[::-1].to_dict("records")
    return out


def stats_for(summary: Optional[dict], setup: str) -> dict:
    """Holdout record of one setup in both modes — shown next to every live signal."""
    if not summary:
        return {}
    return {r["mode"]: {"holdout": r["holdout"], "verdict": r["verdict"], "all": r["all"]}
            for r in summary.get("by_setup", []) if r["setup"] == setup}


# ── lifecycle: cached per underlying, run in the background ─────────
_summaries: dict[str, dict] = {}
_states: dict[str, dict] = {}
_lock = threading.Lock()


def _cache_path(u: str, sig: str) -> Path:
    return HS.CACHE_DIR / f"signals_{u}_{sig}_{SG.RULES_VERSION}.parquet"


def status(underlying: str) -> dict:
    u = underlying.upper()
    return {"underlying": u, **_states.get(u, {"status": "idle"})}


def get(underlying: str) -> Optional[dict]:
    """Summary for ``underlying``; starts the backtest in the background when missing or stale."""
    u = underlying.upper()
    study = HS.get(u)
    cur = _summaries.get(u)
    if study is None:
        return cur
    if cur is None or cur.get("_sig") != study.sig:
        ensure_started(u)
    return cur


def ensure_started(u: str, force: bool = False) -> None:
    with _lock:
        st = _states.setdefault(u, {"status": "idle"})
        if st.get("status") == "running":
            return
        study = HS._studies.get(u)
        if study is None:
            return
        cur = _summaries.get(u)
        if not force and cur is not None and cur.get("_sig") == study.sig:
            return
        if not force and st.get("status") == "error" and st.get("sig") == study.sig:
            return
        st.update(status="running", done=0, total=0, error=None, sig=study.sig)
    threading.Thread(target=_run, args=(u, force), daemon=True, name=f"oi-signal-bt-{u}").start()


def _run(u: str, force: bool) -> None:
    st = _states[u]
    t0 = time.monotonic()
    try:
        study = HS._studies[u]
        path = _cache_path(u, study.sig)
        if path.exists() and not force:
            trades = pd.read_parquet(path)
            meta = {"underlying": u, "holdout": study.holdout, "lot": lot_for(u), "slippage_pts": SLIPPAGE.get(u, 0.75),
                    "skips": {}, "sessions": study.sessions, "first": study.first, "last": study.last,
                    "rules_version": SG.RULES_VERSION}
        else:
            trades, meta = run(u, lambda i, n: st.update(done=i, total=n))
            HS.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            trades.to_parquet(path, index=False)
            for old in HS.CACHE_DIR.glob(f"signals_{u}_*.parquet"):
                if old != path:
                    old.unlink(missing_ok=True)
        summary = summarize(trades, meta)
        summary["_sig"] = study.sig
        _summaries[u] = summary
        st.update(status="ready", seconds=round(time.monotonic() - t0, 1), trades=int(len(trades)))
        logger.info("signal backtest %s: %d trades (%.1fs)", u, len(trades), time.monotonic() - t0)
    except Exception as exc:
        logger.error("signal backtest %s failed: %s | %s", u, exc, traceback.format_exc())
        st.update(status="error", error=str(exc)[:300])
