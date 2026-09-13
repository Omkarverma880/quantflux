"""
Research-parity check.

Runs the app's engine on the same NIFTY history the research used and prints the
result beside the published figures. Run it after any change to the engine or the
pricer — if these numbers drift, the documentation is no longer describing the
code.

    python -m research.index_straddle.parity_check  <path-to-nifty-1min.csv>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── published research figures (QUANTFLUX_STRATEGY_DOCUMENTATION.html) ──
PUBLISHED = {
    "trades": 421,
    "win_rate": 72.9,
    "mean_ret_pct": 16.31,
    "t_stat": 10.00,
    "per_year": 483431,
    "jul_sep_2026_trades": 20,
    "jul_sep_2026_wins": 17,
    "jul_sep_2026_pnl": 157926,
}


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts = next(c for c in ("timestamp", "date", "datetime", "time") if c in df.columns)
    t = pd.to_datetime(df[ts], errors="coerce", utc=True, format="mixed")
    df["_ts"] = t.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.dropna(subset=["_ts"]).sort_values("_ts").set_index("_ts")
    df = df[["open", "high", "low", "close"]].astype(float)
    # keep only full sessions, as the research did
    n = df.groupby(df.index.date).size()
    good = set(n[n >= 368].index)
    return df[[d in good for d in df.index.date]]


def main(path: str):
    from research.index_straddle.config import Config
    from research.index_straddle import engine as E

    df = load(path)
    print(f"bars {len(df):,}  sessions {df.index.normalize().nunique():,}  "
          f"{df.index[0].date()} → {df.index[-1].date()}\n")

    cfg = Config()          # research defaults
    print("config:", cfg.describe(), "\n")
    res = E.run(df, cfg)
    T = pd.DataFrame(res["trades"])
    if T.empty:
        print("NO TRADES — check the data or the config")
        return

    T["d"] = pd.to_datetime(T["date"])
    yrs = (T["d"].max() - T["d"].min()).days / 365.25
    ret = T["return_pct"] / 100.0
    t_stat = ret.mean() / (ret.std() / np.sqrt(len(ret)))
    per_year = T["pnl"].sum() / yrs
    eq = T["pnl"].cumsum()
    maxdd = float((eq - eq.cummax()).min())

    W = T[(T["d"] >= "2026-07-01") & (T["d"] <= "2026-09-11")]

    rows = [
        ("trades", len(T), PUBLISHED["trades"]),
        ("win rate %", round((ret > 0).mean() * 100, 1), PUBLISHED["win_rate"]),
        ("mean return %", round(ret.mean() * 100, 2), PUBLISHED["mean_ret_pct"]),
        ("t-stat", round(t_stat, 2), PUBLISHED["t_stat"]),
        ("Rs / year", round(per_year), PUBLISHED["per_year"]),
        ("Jul-Sep 26 trades", len(W), PUBLISHED["jul_sep_2026_trades"]),
        ("Jul-Sep 26 wins", int((W["pnl"] > 0).sum()), PUBLISHED["jul_sep_2026_wins"]),
        ("Jul-Sep 26 P&L", round(W["pnl"].sum()), PUBLISHED["jul_sep_2026_pnl"]),
    ]
    print(f"{'metric':<22}{'engine':>14}{'research':>14}{'delta':>12}")
    print("-" * 62)
    ok = True
    for name, got, want in rows:
        if want:
            d = (got - want) / abs(want) * 100
        else:
            d = 0.0
        flag = "" if abs(d) <= 2.0 else "  <-- CHECK"
        if abs(d) > 2.0:
            ok = False
        print(f"{name:<22}{got:>14,}{want:>14,}{d:>11.1f}%{flag}")
    print("-" * 62)
    print(f"max drawdown: Rs {maxdd:,.0f}")
    print("\nPARITY OK — engine reproduces the research" if ok else
          "\nPARITY DRIFT — investigate before trusting the numbers")
    print("\nyear by year:")
    T["yr"] = T["d"].dt.year
    g = T.groupby("yr").agg(n=("pnl", "size"),
                            win=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
                            pnl=("pnl", "sum"))
    print(g.to_string())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    main(sys.argv[1])
