"""
Backtest orchestration: data in, artefacts out.

Loads the bars (CSV or broker), runs the engine, builds every summary, runs the
integrity checks from §37, and — when asked — writes the CSVs, charts and the
printed report into ``backtest_results/``.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from core.logger import get_logger
from research.nifty_open_reversion import data as D
from research.nifty_open_reversion import engine as E
from research.nifty_open_reversion import metrics as M
from research.nifty_open_reversion import options as O
from research.nifty_open_reversion import reports as R
from research.nifty_open_reversion.config import Config, RESULTS_DIR

logger = get_logger("research.nifty_open_reversion.service")


def _hhmm(s: str, fallback: dtime) -> dtime:
    return E._hhmm(s, fallback)


# ── §37 integrity checks ─────────────────────────────────────────────
def checks(res: dict, cfg: Config, df: pd.DataFrame) -> dict:
    trades = res["trades"]
    out: dict[str, dict] = {}

    def add(name, ok, detail):
        out[name] = {"ok": bool(ok), "detail": detail}

    total_net = round(sum(t["net_pnl"] for t in trades), 2)
    final_eq = trades[-1]["equity"] if trades else cfg.starting_capital
    ident = abs(final_eq - (cfg.starting_capital + total_net)) < 0.01
    add("equity identity", ident,
        f"{cfg.starting_capital:,.0f} + {total_net:,.2f} = {cfg.starting_capital + total_net:,.2f} "
        f"vs final {final_eq:,.2f}")

    ret = (final_eq - cfg.starting_capital) / cfg.starting_capital * 100.0
    ret_ok = (not trades) or abs(ret - trades[-1]["return_pct"]) < 0.01
    add("return %", ret_ok, f"{ret:.4f}% computed vs {trades[-1]['return_pct'] if trades else 0}% recorded")

    overnight = [t for t in trades if t["entry_time"][:10] != t["exit_time"][:10]]
    add("no overnight positions", not overnight, f"{len(overnight)} trade(s) span a date boundary")

    per_day: dict = {}
    for t in trades:
        per_day.setdefault(t["date"], []).append(t["side"])
    too_many = {d: v for d, v in per_day.items()
                if len(v) > cfg.max_trades_per_day
                or v.count("BUY") > cfg.max_per_side_per_day
                or v.count("SELL") > cfg.max_per_side_per_day}
    add("per-day trade caps", not too_many,
        f"{len(too_many)} day(s) exceed {cfg.max_per_side_per_day}/side, {cfg.max_trades_per_day}/day")

    cutoff = _hhmm(cfg.entry_cutoff, dtime(10, 30))
    late = [t for t in trades if datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M").time() > cutoff]
    add("entry cutoff", not late, f"{len(late)} entry/entries after {cfg.entry_cutoff}")

    lots = [t["lots"] for t in trades]
    add("position size never decreases", all(b >= a for a, b in zip(lots, lots[1:])),
        f"sizes seen: {sorted(set(lots)) or '-'}")

    # each trade's size must follow from the profit realised BEFORE it (§30)
    bad_size = 0
    eq = cfg.starting_capital
    ratchet = cfg.base_lots
    for t in trades:
        want = max(E.lots_for(eq - cfg.starting_capital, cfg), ratchet)
        if want != t["lots"]:
            bad_size += 1
        ratchet = max(ratchet, t["lots"])
        eq = t["equity"]
    add("no look-ahead in sizing", bad_size == 0,
        f"{bad_size} trade(s) sized from anything other than prior realised profit")

    seq_ok = all(a["entry_time"] <= b["entry_time"] for a, b in zip(trades, trades[1:]))
    add("chronological sequencing", seq_ok, "trades are ordered by entry timestamp")

    exact = sum(1 for d in res["days"] if d.exact_open)
    add("09:15 open used", exact == len(res["days"]),
        f"{exact}/{len(res['days'])} sessions had an exact {cfg.entry_start} candle")
    return out


# ── the run ──────────────────────────────────────────────────────────
def load_data(cfg: Config, broker=None, token: Optional[int] = None) -> tuple[pd.DataFrame, str]:
    if cfg.csv_path:
        return D.load_csv(cfg.csv_path), f"CSV {Path(cfg.csv_path).name}"
    if broker is None or not token:
        raise ValueError("No CSV path given and no broker/token available")
    start = date.fromisoformat(cfg.start_date) if cfg.start_date else date.today() - timedelta(days=365)
    end = date.fromisoformat(cfg.end_date) if cfg.end_date else date.today()
    return D.load_from_broker(broker, token, start, end), f"broker {cfg.symbol}"


def run(cfg: Config, *, df: Optional[pd.DataFrame] = None, broker=None,
        token: Optional[int] = None, write: bool = False,
        out_dir: Optional[Path] = None, universe=None) -> dict:
    cfg = cfg.sanitized()
    source = "provided frame"
    if df is None:
        df, source = load_data(cfg, broker, token)
    df = D.slice_dates(df, cfg.start_date, cfg.end_date)
    if df.empty:
        return {"status": "error", "message": "No bars to test after cleaning and slicing"}

    res = E.run(df, cfg)
    res["status"] = "ok"
    res["source"] = source
    res["bars"] = int(len(df))
    res["first_day"] = str(df.index[0].date())
    res["last_day"] = str(df.index[-1].date())
    res["rows_dropped"] = int(df.attrs.get("rows_dropped", 0))
    res["duplicates_removed"] = int(df.attrs.get("duplicates_removed", 0))

    res["summary"] = M.summary(res["trades"], res["days"], cfg)
    res["daily_df"] = M.daily_summary(res["trades"], res["days"], cfg)
    res["monthly_df"] = M.monthly_summary(res["trades"], cfg)
    res["yearly_df"] = M.yearly_summary(res["trades"], cfg)
    res["monthly"] = res["monthly_df"].to_dict("records") if not res["monthly_df"].empty else []
    res["yearly"] = res["yearly_df"].to_dict("records") if not res["yearly_df"].empty else []
    res["scaling"] = M.scaling_report(res["trades"], cfg)
    res["checks"] = checks(res, cfg, df)
    res["config"] = cfg.to_dict()

    # ── options overlay (§39) ──
    if cfg.instrument_mode != "spot":
        if broker is None or universe is None:
            res["option_error"] = ("Option mode needs a live broker connection for premium "
                                   "history — the spot result is shown instead.")
        else:
            resolver = O.OptionResolver(broker, universe)
            legs, skipped = O.apply(res["raw"], cfg, resolver)
            res["option_trades"] = legs
            res["option_skipped"] = skipped[:50]
            res["option_summary"] = _option_summary(legs, cfg)

    if write:
        R.write_all(res, cfg, Path(out_dir or RESULTS_DIR))
    else:
        res["report"] = R.final_report(res, cfg)
    return res


def _option_summary(legs: list[dict], cfg: Config) -> dict:
    """Premium-based P&L for the option legs, sized the same way."""
    if not legs:
        return {"trades": 0, "note": "no option legs could be priced"}
    equity = cfg.starting_capital
    ratchet = cfg.base_lots
    rows = []
    for i, leg in enumerate(sorted(legs, key=lambda x: x["entry_time"]), start=1):
        lots = max(E.lots_for(equity - cfg.starting_capital, cfg), ratchet)
        ratchet = lots
        qty = lots * cfg.lot_size
        net = leg["points"] * qty
        equity += net
        rows.append({**leg, "trade_no": i, "lots": lots, "quantity": qty,
                     "day": leg["day"].isoformat(),
                     "entry_time": leg["entry_time"].strftime("%Y-%m-%d %H:%M"),
                     "exit_time": leg["exit_time"].strftime("%Y-%m-%d %H:%M"),
                     "net_pnl": round(net, 2), "equity": round(equity, 2)})
    net_total = sum(r["net_pnl"] for r in rows)
    wins = sum(1 for r in rows if r["net_pnl"] > 0)
    return {
        "trades": len(rows), "winning_trades": wins,
        "win_rate": round(wins / len(rows) * 100.0, 2),
        "total_premium_points": round(sum(r["points"] for r in rows), 2),
        "net_pnl": round(net_total, 2), "final_equity": round(equity, 2),
        "return_pct": round(net_total / cfg.starting_capital * 100.0, 2),
        "rows": rows[-500:],
        "note": ("Premium in / premium out on the actual contract — no delta model. "
                 "Coverage is limited to whatever option history the broker serves."),
    }
