"""Summary statistics for an index-straddle run."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from research.index_straddle.config import Config


def frame(trades: list[dict]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    df["d"] = pd.to_datetime(df["date"])
    return df


def max_drawdown(equity: pd.Series) -> tuple[float, float]:
    if equity.empty:
        return 0.0, 0.0
    peak = equity.cummax()
    dd = equity - peak
    i = dd.idxmin()
    return float(dd.min()), float(dd.min() / peak.loc[i] * 100.0 if peak.loc[i] else 0.0)


def streaks(vals: list[float]) -> tuple[int, int]:
    w = l = cw = cl = 0
    for v in vals:
        if v > 0:
            cw += 1; cl = 0; w = max(w, cw)
        elif v < 0:
            cl += 1; cw = 0; l = max(l, cl)
        else:
            cw = cl = 0
    return w, l


def summary(trades: list[dict], cfg: Config, sessions: int = 0) -> dict:
    base = {
        "sessions_scanned": sessions, "total_trades": 0, "trades_per_week": 0.0,
        "winning_trades": 0, "losing_trades": 0, "win_rate": 0.0,
        "total_pnl": 0.0, "avg_pnl": 0.0, "mean_return_pct": 0.0,
        "median_return_pct": 0.0, "t_stat": 0.0, "sharpe_trade": 0.0,
        "starting_capital": cfg.starting_capital, "final_equity": cfg.starting_capital,
        "total_return_pct": 0.0, "pnl_per_year": 0.0, "years": 0.0,
        "max_drawdown": 0.0, "max_drawdown_pct": 0.0, "return_over_dd": 0.0,
        "max_win_streak": 0, "max_loss_streak": 0,
        "avg_win": 0.0, "avg_loss": 0.0, "largest_win": 0.0, "largest_loss": 0.0,
        "profit_factor": None, "expectancy": 0.0,
        "stop_exits": 0, "target_exits": 0, "eod_exits": 0, "stop_rate": 0.0,
        "avg_credit": 0.0, "avg_bars_held": 0.0, "worst_mae_pct": 0.0,
        "capital_used": 0.0, "capital_basis": "", "avg_return_on_capital_pct": 0.0,
        "annual_return_on_capital_pct": 0.0, "peak_capital_used": 0.0,
    }
    df = frame(trades)
    if df.empty:
        return base

    pnl = df["pnl"]
    ret = df["return_pct"] / 100.0
    wins, losses = df[pnl > 0], df[pnl < 0]
    dd, dd_pct = max_drawdown(df["equity"])
    w, l = streaks(pnl.tolist())
    span_days = max((df["d"].max() - df["d"].min()).days, 1)
    years = span_days / 365.25
    sd = float(ret.std())
    n = len(df)

    base.update({
        "total_trades": n,
        "trades_per_week": round(n / (span_days / 7.0), 2),
        "winning_trades": len(wins), "losing_trades": len(losses),
        "win_rate": round(len(wins) / n * 100.0, 2),
        "total_pnl": round(float(pnl.sum()), 2),
        "avg_pnl": round(float(pnl.mean()), 2),
        "mean_return_pct": round(float(ret.mean()) * 100, 3),
        "median_return_pct": round(float(ret.median()) * 100, 3),
        "t_stat": round(float(ret.mean() / (sd / math.sqrt(n))), 2) if sd > 0 else 0.0,
        "sharpe_trade": round(float(ret.mean() / sd), 3) if sd > 0 else 0.0,
        "final_equity": round(float(df["equity"].iloc[-1]), 2),
        "total_return_pct": round((float(df["equity"].iloc[-1]) / cfg.starting_capital - 1) * 100, 2),
        "pnl_per_year": round(float(pnl.sum()) / years, 2) if years > 0 else 0.0,
        "years": round(years, 2),
        "max_drawdown": round(dd, 2), "max_drawdown_pct": round(dd_pct, 2),
        "return_over_dd": round(abs(float(pnl.sum()) / years / dd), 2) if dd else 0.0,
        "max_win_streak": w, "max_loss_streak": l,
        "avg_win": round(float(wins["pnl"].mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses["pnl"].mean()), 2) if len(losses) else 0.0,
        "largest_win": round(float(pnl.max()), 2),
        "largest_loss": round(float(pnl.min()), 2),
        "profit_factor": (round(float(wins["pnl"].sum() / abs(losses["pnl"].sum())), 3)
                          if len(losses) and losses["pnl"].sum() else None),
        "expectancy": round(float(pnl.mean()), 2),
        "stop_exits": int((df["exit_reason"] == "SL").sum()),
        "target_exits": int((df["exit_reason"] == "TARGET").sum()),
        "eod_exits": int((df["exit_reason"] == "EOD").sum()),
        "stop_rate": round(float((df["exit_reason"] == "SL").mean()) * 100, 2),
        "avg_credit": round(float(df["basis_value"].mean()), 2),
        "avg_bars_held": round(float(df["bars_held"].mean()), 1),
        "worst_mae_pct": round(float(df["mae"].min()) * 100, 2),
        # ── what the strategy actually ties up ──
        "capital_used": round(float(df["capital_used"].iloc[-1]), 2),
        "peak_capital_used": round(float(df["capital_used"].max()), 2),
        "capital_basis": ("margin blocked (short options)" if cfg.is_short
                          else "premium paid (long options)"),
        "avg_return_on_capital_pct": round(float(df["return_on_capital_pct"].mean()), 3),
        "annual_return_on_capital_pct": (
            round(float(pnl.sum()) / years / float(df["capital_used"].max()) * 100, 2)
            if years > 0 and float(df["capital_used"].max()) else 0.0),
    })
    return base


def by_period(trades: list[dict], freq: str) -> list[dict]:
    df = frame(trades)
    if df.empty:
        return []
    key = df["d"].dt.to_period(freq)
    g = df.groupby(key).agg(
        trades=("pnl", "size"),
        wins=("pnl", lambda x: int((x > 0).sum())),
        pnl=("pnl", "sum"),
        mean_return_pct=("return_pct", "mean"),
        stops=("exit_reason", lambda x: int((x == "SL").sum())),
        worst=("pnl", "min"),
    )
    out = []
    for p, r in g.iterrows():
        out.append({
            "period": str(p), "trades": int(r.trades), "wins": int(r.wins),
            "win_rate": round(float(r.wins) / float(r.trades) * 100, 1) if r.trades else 0.0,
            "pnl": round(float(r.pnl), 2),
            "mean_return_pct": round(float(r.mean_return_pct), 2),
            "stops": int(r.stops), "worst": round(float(r.worst), 2),
        })
    return out


def by_dte(trades: list[dict]) -> list[dict]:
    df = frame(trades)
    if df.empty:
        return []
    g = df.groupby("dte").agg(trades=("pnl", "size"),
                              wins=("pnl", lambda x: int((x > 0).sum())),
                              pnl=("pnl", "sum"),
                              mean_return_pct=("return_pct", "mean"))
    return [{"dte": int(k), "trades": int(r.trades), "wins": int(r.wins),
             "win_rate": round(float(r.wins) / float(r.trades) * 100, 1) if r.trades else 0.0,
             "pnl": round(float(r.pnl), 2),
             "mean_return_pct": round(float(r.mean_return_pct), 2)}
            for k, r in g.iterrows()]


def integrity(trades: list[dict], cfg: Config, df_bars: pd.DataFrame) -> dict:
    """Checks that would catch a broken run before the numbers are believed."""
    T = frame(trades)
    out = {}
    out["one_trade_per_day"] = bool(T.empty or not T["date"].duplicated().any())
    out["all_within_dte_window"] = bool(
        T.empty or ((T["dte"] >= cfg.dte_min) & (T["dte"] <= cfg.dte_max)).all())
    out["no_future_bars"] = bool(
        T.empty or (pd.to_datetime(T["date"]) <= df_bars.index.max()).all())
    out["entry_before_exit"] = bool(T.empty or (T["entry_time"] <= T["exit_time"]).all())
    out["stops_capped"] = bool(
        T.empty or (T.loc[T["exit_reason"] == "SL", "return_pct"]
                    <= -(cfg.stop_pct - 0.01)).all())
    out["premium_positive"] = bool(T.empty or (T["gross_premium"] > 0).all())
    out["costs_applied"] = bool(cfg.costs.any_on)
    out["all_passed"] = all(v for k, v in out.items() if k != "all_passed")
    return out
