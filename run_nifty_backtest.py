#!/usr/bin/env python
"""
NIFTY opening-price mean-reversion — command-line backtest runner.

    python run_nifty_backtest.py --csv data/nifty_1min.csv
    python run_nifty_backtest.py --csv data/nifty_1min.csv --offset 40 --sl 30 --tp 60
    python run_nifty_backtest.py --csv data/nifty_1min.csv --cutoff 10:00 --out results/cutoff_1000
    python run_nifty_backtest.py --csv data/nifty_1min.csv --sweep offset=30,40,50,60,70

Writes trade_log.csv, daily/monthly/yearly summaries, five charts and
final_report.txt into the output directory (default: data/backtest_results).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# make the repo importable when run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research.nifty_open_reversion.config import Config, Costs, RESULTS_DIR   # noqa: E402
from research.nifty_open_reversion import data as D                           # noqa: E402
from research.nifty_open_reversion import service as S                        # noqa: E402


def build_config(a) -> Config:
    cfg = Config(
        csv_path=a.csv or "",
        entry_offset=a.offset, stop_loss=a.sl, target=a.tp,
        level_mode=a.level_mode,
        entry_start=a.start_time, entry_cutoff=a.cutoff, market_close=a.close_time,
        starting_capital=a.capital, lot_size=a.lot_size,
        base_lots=a.base_lots, max_lots=a.max_lots, scale_enabled=not a.no_scaling,
        profit_threshold_2=a.t2, profit_threshold_3=a.t3, profit_threshold_4=a.t4,
        max_trades_per_day=a.max_trades, max_per_side_per_day=a.max_per_side,
        trade_buy=not a.sell_only, trade_sell=not a.buy_only,
        start_date=a.start or "", end_date=a.end or "",
    )
    if a.costs:
        cfg.costs = Costs(brokerage_per_order=a.brokerage, slippage_points=a.slippage,
                          stt_pct=a.stt, exchange_pct=a.exchange, gst_pct=a.gst,
                          sebi_pct=a.sebi, stamp_pct=a.stamp)
    return cfg.sanitized()


def sweep(spec: str, base: Config, df, out_root: Path):
    """--sweep offset=30,40,50 → one run per value, plus a comparison table."""
    field, _, values = spec.partition("=")
    field = field.strip()
    alias = {"offset": "entry_offset", "sl": "stop_loss", "tp": "target",
             "cutoff": "entry_cutoff"}
    field = alias.get(field, field)
    if not hasattr(base, field):
        raise SystemExit(f"Unknown sweep field: {field}")
    rows = []
    for raw in [v.strip() for v in values.split(",") if v.strip()]:
        val = raw if field == "entry_cutoff" else float(raw)
        cfg = base.sanitized()
        setattr(cfg, field, val)
        cfg = cfg.sanitized()
        res = S.run(cfg, df=df, write=True, out_dir=out_root / f"{field}_{raw.replace(':', '')}")
        s = res["summary"]
        rows.append((raw, s["total_trades"], s["win_rate"], s["total_points"],
                     s["total_pnl"], s["total_return_pct"], s["max_drawdown"],
                     s["profit_factor"]))
        print(f"  {field}={raw:<8} trades {s['total_trades']:>5}  win {s['win_rate']:>6.2f}%  "
              f"points {s['total_points']:>10,.0f}  P&L ₹{s['total_pnl']:>12,.0f}  "
              f"ret {s['total_return_pct']:>8.2f}%  DD ₹{s['max_drawdown']:>10,.0f}")
    print()
    print(f"{'value':<10}{'trades':>8}{'win%':>9}{'points':>12}{'net P&L':>16}{'return%':>10}{'maxDD':>14}{'PF':>8}")
    for r in rows:
        pf = "-" if r[7] is None else f"{r[7]:.2f}"
        print(f"{r[0]:<10}{r[1]:>8}{r[2]:>9.2f}{r[3]:>12,.0f}{r[4]:>16,.0f}{r[5]:>10.2f}{r[6]:>14,.0f}{pf:>8}")


def main():
    p = argparse.ArgumentParser(description="NIFTY open ±offset mean-reversion backtest")
    p.add_argument("--csv", help="1-minute OHLC CSV (Zerodha columns)")
    p.add_argument("--start", help="first date, YYYY-MM-DD")
    p.add_argument("--end", help="last date, YYYY-MM-DD")
    p.add_argument("--out", default=str(RESULTS_DIR), help="output directory")

    g = p.add_argument_group("strategy")
    g.add_argument("--offset", type=float, default=50.0)
    g.add_argument("--sl", type=float, default=50.0)
    g.add_argument("--tp", type=float, default=50.0)
    g.add_argument("--level-mode", choices=["points", "percent"], default="points")
    g.add_argument("--start-time", default="09:15")
    g.add_argument("--cutoff", default="10:30")
    g.add_argument("--close-time", default="15:30")
    g.add_argument("--max-trades", type=int, default=2)
    g.add_argument("--max-per-side", type=int, default=1)
    g.add_argument("--buy-only", action="store_true")
    g.add_argument("--sell-only", action="store_true")

    c = p.add_argument_group("capital")
    c.add_argument("--capital", type=float, default=250_000.0)
    c.add_argument("--lot-size", type=int, default=65)
    c.add_argument("--base-lots", type=int, default=1)
    c.add_argument("--max-lots", type=int, default=4)
    c.add_argument("--no-scaling", action="store_true")
    c.add_argument("--t2", type=float, default=300_000.0)
    c.add_argument("--t3", type=float, default=600_000.0)
    c.add_argument("--t4", type=float, default=1_000_000.0)

    k = p.add_argument_group("costs (all off unless --costs)")
    k.add_argument("--costs", action="store_true")
    k.add_argument("--brokerage", type=float, default=20.0)
    k.add_argument("--slippage", type=float, default=0.0)
    k.add_argument("--stt", type=float, default=0.0)
    k.add_argument("--exchange", type=float, default=0.0)
    k.add_argument("--gst", type=float, default=0.0)
    k.add_argument("--sebi", type=float, default=0.0)
    k.add_argument("--stamp", type=float, default=0.0)

    p.add_argument("--sweep", help="e.g. offset=30,40,50,60 or cutoff=10:00,10:30,11:00")
    a = p.parse_args()

    if not a.csv:
        raise SystemExit("--csv is required (a live-broker run is available from the web UI)")

    cfg = build_config(a)
    print(f"Loading {a.csv} …")
    df = D.slice_dates(D.load_csv(a.csv), cfg.start_date, cfg.end_date)
    print(f"  {len(df):,} bars  {df.index[0]} → {df.index[-1]}")

    out = Path(a.out)
    if a.sweep:
        print(f"\nSweeping {a.sweep}\n")
        sweep(a.sweep, cfg, df, out)
        return

    res = S.run(cfg, df=df, write=True, out_dir=out)
    if res.get("status") != "ok":
        raise SystemExit(res.get("message", "backtest failed"))
    print()
    print(res["report"])
    print()
    print(f"Artefacts written to {out.resolve()}")
    for name in sorted(res.get("files", {})):
        print(f"  {name}")


if __name__ == "__main__":
    main()
