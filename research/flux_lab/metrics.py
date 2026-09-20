"""
Flux Lab — what the trades actually say.

Everything here is counted from the simulated trade records: no extrapolation, no annualising a
short sample, no "expected" anything. Where a number cannot be computed from the data at hand
the field is ``None`` and the UI prints "insufficient data" rather than a zero that reads like a
result.

Two distinctions are kept everywhere, because collapsing them is how backtests lie:

* **index points vs option rupees** — the NIFTY move and the contract's P&L are reported side by
  side and never added together;
* **opportunity vs outcome** — how far price travelled in your favour (MFE) is reported next to
  what the exit rules actually captured. An opportunity is not a profit.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

import numpy as np
import pandas as pd

from research.flux_lab.engine import POINT_LADDER

DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return round(x, 4) if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _block(t: pd.DataFrame) -> dict:
    """The core statistics for any subset of trades."""
    if t.empty:
        return {"trades": 0}
    pnl = t["pnl"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_profit, gross_loss = float(wins.sum()), float(abs(losses.sum()))
    return {
        "trades": int(len(t)),
        "wins": int((pnl > 0).sum()), "losses": int((pnl < 0).sum()),
        "win_rate": _f((pnl > 0).mean() * 100),
        "gross_profit": _f(gross_profit), "gross_loss": _f(gross_loss),
        "costs": _f(t["charges"].sum()),
        "net_pnl": _f(pnl.sum()),
        "avg_trade": _f(pnl.mean()), "median_trade": _f(pnl.median()),
        "best_trade": _f(pnl.max()), "worst_trade": _f(pnl.min()),
        "profit_factor": _f(gross_profit / gross_loss) if gross_loss > 0 else None,
        "expectancy": _f(pnl.mean()),
        "avg_win": _f(wins.mean()) if len(wins) else None,
        "avg_loss": _f(losses.mean()) if len(losses) else None,
        "avg_spot_move": _f(t["spot_move_pts"].mean()),
        "avg_spot_mfe": _f(t["spot_mfe_pts"].mean()),
        "avg_spot_mae": _f(t["spot_mae_pts"].mean()),
        "avg_option_mfe_pct": _f(t["option_mfe_pct"].mean()),
        "avg_option_mae_pct": _f(t["option_mae_pct"].mean()),
        "avg_hold_min": _f(t["held_min"].mean()), "max_hold_min": _f(t["held_min"].max()),
    }


def _equity(t: pd.DataFrame, capital: float) -> dict:
    """Equity curve and drawdown, from the realised trade sequence in order."""
    if t.empty:
        return {"curve": [], "max_drawdown": None}
    eq = capital + t["pnl"].astype(float).cumsum()
    peak = eq.cummax()
    dd = eq - peak
    dd_pct = dd / peak.replace(0, np.nan) * 100
    # longest stretch without a new equity high
    flat, longest, run = 0, 0, 0
    for is_peak in (eq >= peak).tolist():
        run = 0 if is_peak else run + 1
        longest = max(longest, run)
        flat = longest
    return {
        "curve": [{"i": i + 1, "date": str(d), "equity": _f(e), "drawdown": _f(x)}
                  for i, (d, e, x) in enumerate(zip(t["date"], eq, dd))],
        "starting_capital": _f(capital), "ending_equity": _f(eq.iloc[-1]),
        "peak_equity": _f(peak.max()),
        "max_drawdown": _f(dd.min()), "max_drawdown_pct": _f(dd_pct.min()),
        "avg_drawdown": _f(dd[dd < 0].mean()) if (dd < 0).any() else 0.0,
        "recovery_factor": _f(t["pnl"].sum() / abs(dd.min())) if dd.min() < 0 else None,
        "longest_flat_trades": int(flat),
    }


def _streaks(t: pd.DataFrame) -> dict:
    best = worst = cur = 0
    sign = 0
    for p in t["pnl"].astype(float):
        s = 1 if p > 0 else -1 if p < 0 else 0
        cur = cur + s if s == sign else s
        sign = s
        best, worst = max(best, cur), min(worst, cur)
    return {"max_consecutive_wins": int(best), "max_consecutive_losses": int(abs(worst))}


def _ladder(t: pd.DataFrame, signals: pd.DataFrame) -> dict:
    """How often the index travelled N points in the signal's favour before the trade ended.

    This answers "was a 25-point target realistic?" with counts, not opinion. It is measured on
    the trade's own path (MFE), so it never assumes a move that happened after the exit.
    """
    if t.empty:
        return {"levels": [], "note": "no trades"}
    mfe = t["spot_mfe_pts"].astype(float)
    out = []
    for pts in POINT_LADDER:
        hit = mfe >= pts
        times = []
        for lt in t.loc[hit, "ladder_time"]:
            if isinstance(lt, dict) and str(pts) in lt:
                times.append(lt[str(pts)])
        out.append({
            "points": pts, "trades_reaching": int(hit.sum()),
            "pct_of_trades": _f(hit.mean() * 100),
            "median_bars_to_reach": _f(np.median(times)) if times else None,
        })
    return {"levels": out, "measured_on": int(len(t))}


def _mfe_mae(t: pd.DataFrame) -> dict:
    """The distribution behind stop and target choices — index points and option percentages."""
    if t.empty:
        return {}
    q = [10, 25, 50, 75, 90]
    return {
        "spot_mfe_percentiles": {str(p): _f(np.percentile(t["spot_mfe_pts"], p)) for p in q},
        "spot_mae_percentiles": {str(p): _f(np.percentile(t["spot_mae_pts"], p)) for p in q},
        "option_mfe_pct_percentiles": {str(p): _f(np.percentile(t["option_mfe_pct"], p)) for p in q},
        "option_mae_pct_percentiles": {str(p): _f(np.percentile(t["option_mae_pct"], p)) for p in q},
        "scatter": [{"mfe": _f(a), "mae": _f(b), "pnl": _f(c), "reason": r}
                    for a, b, c, r in zip(t["spot_mfe_pts"], t["spot_mae_pts"],
                                          t["pnl"], t["exit_reason"])][:600],
    }


def _quality(t: pd.DataFrame) -> dict:
    """Classify every trade by how it behaved, not only by whether it paid."""
    if t.empty:
        return {}
    kinds = []
    for r in t.itertuples():
        if r.exit_reason == "TARGET" and r.held_min <= 5:
            kinds.append("immediate winner")
        elif r.exit_reason == "TARGET":
            kinds.append("delayed winner")
        elif r.exit_reason == "SL":
            kinds.append("stopped out")
        elif abs(r.spot_mfe_pts) < 5 and abs(r.spot_mae_pts) < 5:
            kinds.append("no meaningful movement")
        elif r.pnl > 0:
            kinds.append("time exit, in profit")
        else:
            kinds.append("time exit, in loss")
    c = Counter(kinds)
    return {"classes": [{"kind": k, "trades": v, "pct": _f(v / len(t) * 100)}
                        for k, v in c.most_common()],
            "time_to_target_median_min": _f(t.loc[t["exit_reason"] == "TARGET", "held_min"].median())
            if (t["exit_reason"] == "TARGET").any() else None,
            "time_to_stop_median_min": _f(t.loc[t["exit_reason"] == "SL", "held_min"].median())
            if (t["exit_reason"] == "SL").any() else None}


def _by(t: pd.DataFrame, key: str, label_fn=None) -> list[dict]:
    rows = []
    for value, g in t.groupby(key, sort=True):
        rows.append({"key": label_fn(value) if label_fn else str(value), **_block(g)})
    return rows


def _regimes(t: pd.DataFrame) -> dict:
    """Performance under conditions derived from the data, not hand-labelled."""
    if t.empty or "regime" not in t.columns:
        return {}
    out = {}
    for dim in ("trend", "volatility", "vix", "gap", "range_day"):
        buckets: dict[str, list] = {}
        for r in t.itertuples():
            reg = getattr(r, "regime", None) or {}
            v = reg.get(dim)
            if v:
                buckets.setdefault(v, []).append(r.Index)
        rows = [{"key": k, **_block(t.loc[idx])} for k, idx in buckets.items()]
        if rows:
            out[dim] = sorted(rows, key=lambda r: -r["trades"])
    return out


def _daily(t: pd.DataFrame, signals: pd.DataFrame) -> list[dict]:
    if signals.empty:
        return []
    out = []
    for day, sg in signals.groupby("date", sort=True):
        g = t[t["date"] == day]
        pnl = g["pnl"].astype(float) if len(g) else pd.Series(dtype=float)
        eq = pnl.cumsum()
        dd = (eq - eq.cummax()).min() if len(eq) else 0.0
        out.append({
            "date": str(day), "signals": int(len(sg)), "trades": int(len(g)),
            "wins": int((pnl > 0).sum()), "losses": int((pnl < 0).sum()),
            "gross": _f((pnl + g["charges"]).sum()) if len(g) else 0.0,
            "costs": _f(g["charges"].sum()) if len(g) else 0.0,
            "net_pnl": _f(pnl.sum()) if len(g) else 0.0,
            "max_dd": _f(dd),
            "target_hits": int((g["exit_reason"] == "TARGET").sum()) if len(g) else 0,
            "stop_hits": int((g["exit_reason"] == "SL").sum()) if len(g) else 0,
            "best_spot_mfe": _f(g["spot_mfe_pts"].max()) if len(g) else None,
        })
    return out


def _opportunity(t: pd.DataFrame, signals: pd.DataFrame) -> dict:
    """How often the setup appeared at all, and how often the index offered a real move.

    Counted per session so "how frequently does this happen?" has an answer in days, not in
    a total that hides months of silence.
    """
    if signals.empty:
        return {}
    days = signals["date"].nunique()
    per_day = signals.groupby("date").size()
    traded_days = t["date"].nunique() if not t.empty else 0
    out = {
        "sessions_with_signals": int(days),
        "days_with_2plus_signals": int((per_day >= 2).sum()),
        "avg_signals_per_day": _f(per_day.mean()),
        "sessions_traded": int(traded_days),
    }
    if not t.empty:
        by_day_mfe = t.groupby("date")["spot_mfe_pts"].max()
        for pts in (25, 30, 40):
            out[f"days_with_{pts}pt_opportunity"] = int((by_day_mfe >= pts).sum())
            out[f"pct_days_with_{pts}pt"] = _f((by_day_mfe >= pts).mean() * 100)
    return out


def summarise(result: dict, cfg, trading_days: Optional[int] = None) -> dict:
    """Everything the report needs, computed once."""
    trades = pd.DataFrame(result.get("trades") or [])
    signals = pd.DataFrame(result.get("signals") or [])
    if trades.empty:
        return {
            "status": "ok", "insufficient_data": True,
            "headline": {"trades": 0},
            "message": "No trade was taken in this period — the conditions never lined up, "
                       "or every signal was blocked. The signal log shows which.",
            "signals": int(len(signals)),
            "skips": result.get("skips", {}),
            "sessions": result.get("sessions"),
            "opportunity": _opportunity(trades, signals),
        }
    head = _block(trades)
    head.update(_streaks(trades))
    days = trading_days or result.get("sessions") or trades["date"].nunique()
    head["trades_per_day"] = _f(len(trades) / days) if days else None
    head["trades_per_week"] = _f(len(trades) / (days / 5)) if days else None
    head["trades_per_month"] = _f(len(trades) / (days / 21)) if days else None
    head["sessions"] = int(days)
    head["signals"] = int(len(signals))
    head["signal_to_trade_pct"] = _f(len(trades) / len(signals) * 100) if len(signals) else None

    monthly = _by(trades, "month")
    months_profit = sum(1 for m in monthly if (m.get("net_pnl") or 0) > 0)
    return {
        "status": "ok", "insufficient_data": len(trades) < 30,
        "headline": head,
        "equity": _equity(trades, float(getattr(cfg, "capital", 200000))),
        "ladder": _ladder(trades, signals),
        "mfe_mae": _mfe_mae(trades),
        "quality": _quality(trades),
        "by_time": _by(trades, "bucket"),
        "by_dow": _by(trades, "dow", lambda v: DOW[int(v)] if 0 <= int(v) < 7 else str(v)),
        "by_month": monthly,
        "by_year": _by(trades, "year"),
        "by_exit": _by(trades, "exit_reason"),
        "by_side": _by(trades, "side"),
        "by_expiry_dte": _by(trades, "dte") if "dte" in trades.columns else [],
        "regimes": _regimes(trades),
        "daily": _daily(trades, signals),
        "opportunity": _opportunity(trades, signals),
        "months": {"profitable": months_profit, "losing": len(monthly) - months_profit,
                   "best": max(monthly, key=lambda m: m.get("net_pnl") or 0, default=None),
                   "worst": min(monthly, key=lambda m: m.get("net_pnl") or 0, default=None)},
        "skips": result.get("skips", {}),
        "range": {"first": result.get("first_session"), "last": result.get("last_session"),
                  "sessions": result.get("sessions"), "bars": result.get("bars")},
    }


def monte_carlo(trades: list[dict], runs: int = 500, seed: int = 7) -> dict:
    """Two different questions, two different resamplings.

    * **Shuffle** — the same trades in a different order. The total cannot change (addition does
      not care about order), so this says nothing about the outcome and everything about the
      *path*: how deep a drawdown the same edge could have put you through.
    * **Bootstrap** — draw the same number of trades *with replacement*. This does move the
      total, and answers "how much of this result could be the particular sample I happened to
      get?" It assumes the trades are independent and identically distributed, which they are
      not quite, so read it as a spread and not as a probability.

    Neither is a forecast. Both are reproducible: the seed is fixed and reported.
    """
    pnl = np.array([t["pnl"] for t in trades], dtype=float)
    if pnl.size < 10:
        return {"insufficient_data": True, "trades": int(pnl.size)}
    rng = np.random.default_rng(seed)              # fixed seed keeps the report reproducible
    shuffle_dd, boot_final, boot_dd = [], [], []
    for _ in range(runs):
        eq = np.cumsum(rng.permutation(pnl))
        shuffle_dd.append(float((eq - np.maximum.accumulate(eq)).min()))
        sample = rng.choice(pnl, size=pnl.size, replace=True)
        beq = np.cumsum(sample)
        boot_final.append(float(beq[-1]))
        boot_dd.append(float((beq - np.maximum.accumulate(beq)).min()))
    pct = [5, 25, 50, 75, 95]
    return {
        "runs": runs, "seed": seed, "trades": int(pnl.size),
        "actual_net_pnl": _f(float(pnl.sum())),
        "shuffle_drawdown_percentiles": {str(p): _f(np.percentile(shuffle_dd, p)) for p in pct},
        "final_pnl_percentiles": {str(p): _f(np.percentile(boot_final, p)) for p in pct},
        "max_drawdown_percentiles": {str(p): _f(np.percentile(boot_dd, p)) for p in pct},
        "worse_than_zero_pct": _f(float((np.array(boot_final) <= 0).mean() * 100)),
        "note": ("Drawdown percentiles come from reordering your actual trades; the P&L spread "
                 "comes from resampling them with replacement. The total of a reshuffle is always "
                 "identical, which is why it is not reported as a range."),
    }
