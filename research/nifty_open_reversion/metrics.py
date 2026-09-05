"""
Performance metrics and period summaries.

Everything is derived from the trade rows the engine produced — one source of
truth, so the headline numbers and the CSVs can never disagree.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from research.nifty_open_reversion.config import Config


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def frame(trades: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["entry_dt"] = pd.to_datetime(df["entry_time"])
    return df


def streaks(pnl: list[float]) -> tuple[int, int]:
    """(longest winning streak, longest losing streak)."""
    best_w = best_l = cur_w = cur_l = 0
    for p in pnl:
        if p > 0:
            cur_w += 1; cur_l = 0
        elif p < 0:
            cur_l += 1; cur_w = 0
        else:
            cur_w = cur_l = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return best_w, best_l


def profit_factor(pnl: pd.Series) -> Optional[float]:
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return round(float(gains / losses), 3)


def max_drawdown(equity: pd.Series) -> tuple[float, float]:
    """(worst ₹ drawdown, worst % of the running peak)."""
    if equity.empty:
        return 0.0, 0.0
    peak = equity.cummax()
    dd = equity - peak
    dd_pct = (dd / peak.replace(0, pd.NA)) * 100.0
    return round(float(dd.min()), 2), round(float(dd_pct.min() or 0), 2)


def summary(trades: list[dict], days: list, cfg: Config) -> dict:
    """The headline block (§23)."""
    df = frame(trades)
    traded_days = len([d for d in days if d.trades])
    base = {
        "trading_days": len(days), "days_with_trades": traded_days,
        "total_trades": 0, "buy_trades": 0, "sell_trades": 0,
        "winning_trades": 0, "losing_trades": 0, "win_rate": 0.0,
        "total_points": 0.0, "avg_points": 0.0,
        "total_pnl": 0.0, "avg_pnl": 0.0, "gross_pnl": 0.0, "total_costs": 0.0,
        "starting_capital": cfg.starting_capital, "final_equity": cfg.starting_capital,
        "total_return_pct": 0.0, "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
        "max_win_streak": 0, "max_loss_streak": 0,
        "avg_win": 0.0, "avg_loss": 0.0, "largest_win": 0.0, "largest_loss": 0.0,
        "profit_factor": None, "expectancy": 0.0,
        "target_exits": 0, "sl_exits": 0, "eod_exits": 0,
    }
    if df.empty:
        return base

    net = df["net_pnl"]
    wins = df[net > 0]
    losses = df[net < 0]
    dd, dd_pct = max_drawdown(df["equity"])
    w, l = streaks(net.tolist())
    final_eq = float(df["equity"].iloc[-1])
    total = float(net.sum())
    base.update({
        "total_trades": len(df),
        "buy_trades": int((df["side"] == "BUY").sum()),
        "sell_trades": int((df["side"] == "SELL").sum()),
        "winning_trades": len(wins), "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(df) * 100.0, 2),
        "total_points": round(float(df["nifty_points"].sum()), 2),
        "avg_points": round(float(df["nifty_points"].mean()), 3),
        "total_pnl": round(total, 2),
        "avg_pnl": round(float(net.mean()), 2),
        "gross_pnl": round(float(df["gross_pnl"].sum()), 2),
        "total_costs": round(float(df["transaction_cost"].sum()), 2),
        "final_equity": round(final_eq, 2),
        "total_return_pct": round((final_eq - cfg.starting_capital) / cfg.starting_capital * 100.0, 2),
        "max_drawdown": dd, "max_drawdown_pct": dd_pct,
        "max_win_streak": w, "max_loss_streak": l,
        "avg_win": round(float(wins["net_pnl"].mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses["net_pnl"].mean()), 2) if len(losses) else 0.0,
        "largest_win": round(float(net.max()), 2),
        "largest_loss": round(float(net.min()), 2),
        "profit_factor": profit_factor(net),
        "expectancy": round(float(net.mean()), 2),
        "target_exits": int((df["exit_reason"] == "TARGET").sum()),
        "sl_exits": int((df["exit_reason"] == "SL").sum()),
        "eod_exits": int((df["exit_reason"] == "EOD").sum()),
        "median_pnl": round(float(net.median()), 2),
        "pnl_std": round(float(net.std(ddof=0)), 2) if len(df) > 1 else 0.0,
    })
    return base


def daily_summary(trades: list[dict], days: list, cfg: Config) -> pd.DataFrame:
    """§20 — one row per session, including the days that never traded."""
    df = frame(trades)
    by_day = {d.day.isoformat(): d for d in days}
    rows = []
    equity = cfg.starting_capital
    per_day = {k: g for k, g in df.groupby(df["date"].dt.date)} if not df.empty else {}
    for key in sorted(by_day):
        d = by_day[key]
        g = per_day.get(d.day)
        buy_pnl = sell_pnl = 0.0
        lots_used = ""
        if g is not None and len(g):
            buy_pnl = round(float(g[g["side"] == "BUY"]["net_pnl"].sum()), 2)
            sell_pnl = round(float(g[g["side"] == "SELL"]["net_pnl"].sum()), 2)
            equity = float(g["equity"].iloc[-1])
            lots_used = "/".join(str(x) for x in g["lots"].tolist())
        daily = round(buy_pnl + sell_pnl, 2)
        sides = {t.side for t in d.trades}
        rows.append({
            "date": key, "daily_open": d.daily_open,
            "buy_triggered": "BUY" in sides, "sell_triggered": "SELL" in sides,
            "both_side_day": {"BUY", "SELL"} <= sides,
            "buy_pnl": buy_pnl, "sell_pnl": sell_pnl, "daily_pnl": daily,
            "cumulative_profit": round(equity - cfg.starting_capital, 2),
            "equity": round(equity, 2),
            "return_pct": round((equity - cfg.starting_capital) / cfg.starting_capital * 100.0, 3),
            "lots_used": lots_used, "bars": d.bars, "exact_open": d.exact_open,
        })
    return pd.DataFrame(rows)


def _period_rows(df: pd.DataFrame, keys: list[str], cfg: Config) -> pd.DataFrame:
    out = []
    for key, g in df.groupby(keys, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        net = g["net_pnl"]
        wins = int((net > 0).sum())
        eq_end = float(g["equity"].iloc[-1])
        dd, dd_pct = max_drawdown(g["equity"])
        row = dict(zip(keys, key))
        row.update({
            "trades": len(g), "winning_trades": wins, "losing_trades": int((net < 0).sum()),
            "win_rate": round(wins / len(g) * 100.0, 2),
            "points": round(float(g["nifty_points"].sum()), 2),
            "gross_pnl": round(float(g["gross_pnl"].sum()), 2),
            "net_pnl": round(float(net.sum()), 2),
            "cumulative_profit": round(eq_end - cfg.starting_capital, 2),
            "ending_equity": round(eq_end, 2),
            "return_pct": round((eq_end - cfg.starting_capital) / cfg.starting_capital * 100.0, 2),
            "period_return_pct": round(float(net.sum()) / cfg.starting_capital * 100.0, 2),
            "max_drawdown": dd, "max_drawdown_pct": dd_pct,
            "avg_trade": round(float(net.mean()), 2),
            "profit_factor": profit_factor(net),
        })
        out.append(row)
    return pd.DataFrame(out)


def monthly_summary(trades: list[dict], cfg: Config) -> pd.DataFrame:
    df = frame(trades)
    return pd.DataFrame() if df.empty else _period_rows(df, ["year", "month"], cfg)


def yearly_summary(trades: list[dict], cfg: Config) -> pd.DataFrame:
    df = frame(trades)
    return pd.DataFrame() if df.empty else _period_rows(df, ["year"], cfg)


def scaling_report(trades: list[dict], cfg: Config) -> list[dict]:
    """§23 — when each position size started, and what it earned."""
    df = frame(trades)
    if df.empty:
        return []
    out = []
    for lots, g in df.groupby("lots", sort=True):
        net = g["net_pnl"]
        first = g.iloc[0]
        out.append({
            "lots": int(lots), "quantity": int(first["quantity"]),
            "start_date": str(g["date"].min().date()), "end_date": str(g["date"].max().date()),
            "start_trade_no": int(first["trade_no"]),
            "equity_at_start": round(float(first["equity"] - first["net_pnl"]), 2),
            "profit_at_start": round(float(first["equity"] - first["net_pnl"] - cfg.starting_capital), 2),
            "trades": len(g), "winning_trades": int((net > 0).sum()),
            "win_rate": round(float((net > 0).sum()) / len(g) * 100.0, 2),
            "net_pnl": round(float(net.sum()), 2),
            "points": round(float(g["nifty_points"].sum()), 2),
        })
    return out
