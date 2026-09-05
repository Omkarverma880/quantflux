"""
Report artefacts: the CSVs, the charts and the printed summary (§19-§28, §33, §36).

Everything lands in one output directory so a run is self-contained and
shareable. Charts use a non-interactive matplotlib backend so this works
head-less on a server.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import matplotlib.ticker as mticker      # noqa: E402
import pandas as pd                      # noqa: E402

from core.logger import get_logger       # noqa: E402
from research.nifty_open_reversion import metrics as M   # noqa: E402
from research.nifty_open_reversion.config import Config  # noqa: E402

logger = get_logger("research.nifty_open_reversion.reports")

BG = "#0b1220"; FG = "#e2e8f0"; GRID = "#1e293b"
UP = "#26a69a"; DOWN = "#ef5350"; ACCENT = "#38bdf8"; WARN = "#f59e0b"

OPTION_WARNING = (
    "THIS IS A SPOT / INDEX BACKTEST.\n"
    "P&L is NIFTY points x quantity. It does NOT model option premium, delta,\n"
    "gamma, theta, implied volatility, bid/ask spread, strike selection, expiry\n"
    "or option slippage. Do not read these numbers as option returns."
)


def _style(ax, title: str, ylabel: str = ""):
    ax.set_facecolor(BG)
    ax.figure.patch.set_facecolor(BG)
    ax.set_title(title, color=FG, fontsize=13, fontweight="bold", pad=12)
    ax.set_ylabel(ylabel, color=FG, fontsize=10)
    ax.tick_params(colors="#94a3b8", labelsize=9)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.7, alpha=0.8)


def _rupees(ax):
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))


def _transitions(df: pd.DataFrame) -> list[tuple]:
    """(date, lots) for each step up in position size."""
    out, prev = [], None
    for _, r in df.iterrows():
        if prev is not None and r["lots"] != prev:
            out.append((r["date"], int(r["lots"])))
        prev = r["lots"]
    return out


def equity_curve(df: pd.DataFrame, cfg: Config, path: Path):
    fig, ax = plt.subplots(figsize=(14, 7))
    _style(ax, "Equity curve — NIFTY open ±%g progressive scaling" % cfg.entry_offset, "Equity (₹)")
    ax.plot(df["date"], df["equity"], color=ACCENT, linewidth=1.6, label="Equity")
    for level, label, colour in (
        (cfg.starting_capital, f"Start ₹{cfg.starting_capital:,.0f}", "#64748b"),
        (cfg.starting_capital + cfg.profit_threshold_2, f"2 lots ₹{cfg.starting_capital + cfg.profit_threshold_2:,.0f}", "#a78bfa"),
        (cfg.starting_capital + cfg.profit_threshold_3, f"3 lots ₹{cfg.starting_capital + cfg.profit_threshold_3:,.0f}", "#fbbf24"),
        (cfg.starting_capital + cfg.profit_threshold_4, f"4 lots ₹{cfg.starting_capital + cfg.profit_threshold_4:,.0f}", "#22c55e"),
    ):
        ax.axhline(level, color=colour, linestyle="--", linewidth=1, alpha=0.75)
        ax.text(df["date"].iloc[0], level, f" {label}", color=colour, fontsize=8, va="bottom")
    for d, lots in _transitions(df):
        ax.axvline(d, color=WARN, linestyle=":", linewidth=1.2, alpha=0.9)
        ax.text(d, ax.get_ylim()[1], f" →{lots} lots", color=WARN, fontsize=8, rotation=90, va="top")
    _rupees(ax)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=140, facecolor=BG); plt.close(fig)


def return_curve(df: pd.DataFrame, cfg: Config, path: Path):
    fig, ax = plt.subplots(figsize=(14, 6))
    _style(ax, "Cumulative return", "Return (%)")
    ax.plot(df["date"], df["return_pct"], color=UP, linewidth=1.6)
    ax.axhline(0, color="#64748b", linewidth=1)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}%"))
    fig.tight_layout(); fig.savefig(path, dpi=140, facecolor=BG); plt.close(fig)


def drawdown_curve(df: pd.DataFrame, path: Path):
    fig, ax = plt.subplots(figsize=(14, 5))
    _style(ax, "Drawdown from the running peak", "Drawdown (₹)")
    ax.fill_between(df["date"], df["drawdown"], 0, color=DOWN, alpha=0.45)
    ax.plot(df["date"], df["drawdown"], color=DOWN, linewidth=1)
    _rupees(ax)
    fig.tight_layout(); fig.savefig(path, dpi=140, facecolor=BG); plt.close(fig)


def lot_scaling(df: pd.DataFrame, path: Path):
    fig, ax = plt.subplots(figsize=(14, 4.5))
    _style(ax, "Position size over time", "Lots")
    ax.step(df["date"], df["lots"], where="post", color=WARN, linewidth=1.8)
    ax.set_yticks(sorted(df["lots"].unique()))
    ax.set_ylim(0, max(df["lots"]) + 0.6)
    for d, lots in _transitions(df):
        ax.annotate(f"{lots} lots\n{d.date()}", (d, lots), color=FG, fontsize=8,
                    xytext=(6, 8), textcoords="offset points")
    fig.tight_layout(); fig.savefig(path, dpi=140, facecolor=BG); plt.close(fig)


def pnl_distribution(df: pd.DataFrame, path: Path) -> dict:
    net = df["net_pnl"]
    stats = {
        "mean": round(float(net.mean()), 2), "median": round(float(net.median()), 2),
        "std": round(float(net.std(ddof=0)), 2) if len(net) > 1 else 0.0,
        "win_avg": round(float(net[net > 0].mean()), 2) if (net > 0).any() else 0.0,
        "loss_avg": round(float(net[net < 0].mean()), 2) if (net < 0).any() else 0.0,
    }
    fig, ax = plt.subplots(figsize=(12, 6))
    _style(ax, "Trade P&L distribution", "Trades")
    bins = min(60, max(10, len(net) // 8))
    ax.hist(net[net >= 0], bins=bins, color=UP, alpha=0.85, label="Winners")
    ax.hist(net[net < 0], bins=bins, color=DOWN, alpha=0.85, label="Losers")
    ax.axvline(stats["mean"], color=ACCENT, linestyle="--", linewidth=1.2,
               label=f"mean ₹{stats['mean']:,.0f}")
    ax.axvline(stats["median"], color=WARN, linestyle=":", linewidth=1.2,
               label=f"median ₹{stats['median']:,.0f}")
    ax.set_xlabel("Net P&L (₹)", color=FG, fontsize=10)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=140, facecolor=BG); plt.close(fig)
    return stats


def _fmt(v, money=True) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float) and v == float("inf"):
        return "∞"
    return f"₹{v:,.0f}" if money else f"{v}"


def final_report(res: dict, cfg: Config) -> str:
    """The printed summary (§33)."""
    s = res["summary"]
    L = []
    add = L.append
    add("=" * 68)
    add("NIFTY OPEN ±%g PROGRESSIVE SCALING BACKTEST" % cfg.entry_offset)
    add("=" * 68)
    add("")
    add(f"Data source:             {res.get('source', 'n/a')}")
    add(f"Period:                  {res.get('first_day', '-')} → {res.get('last_day', '-')}")
    add(f"Bars processed:          {res.get('bars', 0):,}")
    add(f"Starting Capital:        ₹{cfg.starting_capital:,.0f}")
    add("")
    add("Strategy:")
    unit = "%" if cfg.level_mode == "percent" else " pts"
    add(f"  BUY  = Open − {cfg.entry_offset:g}{unit}")
    add(f"  SELL = Open + {cfg.entry_offset:g}{unit}")
    add(f"  SL   = {cfg.stop_loss:g}{unit}")
    add(f"  TP   = {cfg.target:g}{unit}")
    add(f"  Entry window = {cfg.entry_start} → {cfg.entry_cutoff}, flat by {cfg.market_close}")
    add(f"  Max {cfg.max_per_side_per_day} per side, {cfg.max_trades_per_day} per day")
    add("")
    add("Position Scaling:")
    if cfg.scale_enabled:
        add(f"  ₹{cfg.profit_threshold_2:,.0f} profit  → 2 lots")
        add(f"  ₹{cfg.profit_threshold_3:,.0f} profit  → 3 lots")
        add(f"  ₹{cfg.profit_threshold_4:,.0f} profit → 4 lots")
        add(f"  Lot size = {cfg.lot_size}, max {cfg.max_lots} lots")
    else:
        add(f"  Fixed {cfg.base_lots} lot(s) × {cfg.lot_size}")
    add("")
    add("-" * 68)
    add("PERFORMANCE")
    add("-" * 68)
    add("")
    add(f"Trading Days:            {s['trading_days']:,}   (traded on {s['days_with_trades']:,})")
    add(f"Total Trades:            {s['total_trades']:,}")
    add(f"BUY Trades:              {s['buy_trades']:,}")
    add(f"SELL Trades:             {s['sell_trades']:,}")
    add(f"Win Rate:                {s['win_rate']}%   ({s['winning_trades']:,} W / {s['losing_trades']:,} L)")
    add(f"Exits:                   {s['target_exits']:,} target · {s['sl_exits']:,} stop · {s['eod_exits']:,} EOD")
    add("")
    add(f"Total NIFTY Points:      {s['total_points']:,.2f}")
    add(f"Avg Points / Trade:      {s['avg_points']:,.3f}")
    add("")
    add(f"Gross P&L:               {_fmt(s['gross_pnl'])}")
    add(f"Transaction Costs:       {_fmt(s['total_costs'])}")
    add(f"Total Profit:            {_fmt(s['total_pnl'])}")
    add(f"Final Equity:            {_fmt(s['final_equity'])}")
    add(f"Total Return:            {s['total_return_pct']:,.2f}%")
    add("")
    add(f"Maximum Drawdown:        {_fmt(s['max_drawdown'])}")
    add(f"Maximum Drawdown %:      {s['max_drawdown_pct']:,.2f}%")
    add(f"Profit Factor:           {_fmt(s['profit_factor'], money=False)}")
    add(f"Average Win / Loss:      {_fmt(s['avg_win'])} / {_fmt(s['avg_loss'])}")
    add(f"Largest Win / Loss:      {_fmt(s['largest_win'])} / {_fmt(s['largest_loss'])}")
    add(f"Max Winning Streak:      {s['max_win_streak']}")
    add(f"Max Losing Streak:       {s['max_loss_streak']}")
    add("")
    add("-" * 68)
    add("POSITION SCALING")
    add("-" * 68)
    for row in res.get("scaling", []):
        add("")
        add(f"{row['lots']} Lot{'s' if row['lots'] > 1 else ''} (qty {row['quantity']}):")
        add(f"  Start Date:            {row['start_date']}")
        add(f"  End Date:              {row['end_date']}")
        add(f"  Equity at start:       {_fmt(row['equity_at_start'])}  (profit {_fmt(row['profit_at_start'])})")
        add(f"  Trades:                {row['trades']:,}   win rate {row['win_rate']}%")
        add(f"  P&L:                   {_fmt(row['net_pnl'])}")
    add("")
    add("-" * 68)
    add("YEAR BY YEAR (§29 — walk-forward validation)")
    add("-" * 68)
    add("")
    add(f"{'Year':<8}{'Trades':>8}{'Win%':>8}{'Points':>12}{'Net P&L':>16}{'Return%':>10}{'MaxDD':>14}")
    for y in res.get("yearly", []):
        add(f"{int(y['year']):<8}{y['trades']:>8}{y['win_rate']:>8.1f}{y['points']:>12,.0f}"
            f"{y['net_pnl']:>16,.0f}{y['period_return_pct']:>10.1f}{y['max_drawdown']:>14,.0f}")
    add("")
    add("-" * 68)
    add("SANITY CHECKS (§37)")
    add("-" * 68)
    for k, v in (res.get("checks") or {}).items():
        add(f"  {'PASS' if v['ok'] else 'FAIL'}  {k}: {v['detail']}")
    add("")
    add("-" * 68)
    add("WARNING")
    add("-" * 68)
    add(OPTION_WARNING)
    if not cfg.costs.any_on:
        add("")
        add("Costs and slippage are ZERO in this run — this is the pure theoretical")
        add("point result. Switch the cost knobs on for a realistic figure.")
    add("")
    add("=" * 68)
    add(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    add("=" * 68)
    return "\n".join(L)


def write_all(res: dict, cfg: Config, out_dir: Path) -> dict:
    """Write every artefact and return the file paths (§36)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    df = M.frame(res["trades"])

    def _csv(name: str, frame_: pd.DataFrame):
        if frame_ is None or frame_.empty:
            return
        p = out_dir / name
        frame_.to_csv(p, index=False)
        files[name] = str(p)

    if not df.empty:
        _csv("trade_log.csv", df.drop(columns=["entry_dt"], errors="ignore"))
    _csv("daily_summary.csv", res.get("daily_df"))
    _csv("monthly_summary.csv", res.get("monthly_df"))
    _csv("yearly_summary.csv", res.get("yearly_df"))

    if not df.empty:
        try:
            equity_curve(df, cfg, out_dir / "equity_curve.png"); files["equity_curve.png"] = str(out_dir / "equity_curve.png")
            return_curve(df, cfg, out_dir / "return_curve.png"); files["return_curve.png"] = str(out_dir / "return_curve.png")
            drawdown_curve(df, out_dir / "drawdown_curve.png"); files["drawdown_curve.png"] = str(out_dir / "drawdown_curve.png")
            lot_scaling(df, out_dir / "lot_scaling.png"); files["lot_scaling.png"] = str(out_dir / "lot_scaling.png")
            res["distribution"] = pnl_distribution(df, out_dir / "trade_pnl_distribution.png")
            files["trade_pnl_distribution.png"] = str(out_dir / "trade_pnl_distribution.png")
        except Exception as exc:
            logger.error("chart generation failed: %s", exc)

    report = final_report(res, cfg)
    (out_dir / "final_report.txt").write_text(report, encoding="utf-8")
    files["final_report.txt"] = str(out_dir / "final_report.txt")
    res["report"] = report
    res["files"] = files
    return files
