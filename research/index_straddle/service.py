"""
Backtest orchestration: config in, a complete result out.

Handles data loading (uploaded CSV or a broker pull), the two premium sources,
the run itself, the summary blocks, the integrity checks, and the CSV artefacts.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.nifty_open_reversion import data as D          # shared, read-only
from research.index_straddle import engine as E
from research.index_straddle import metrics as MET
from research.index_straddle import optmodel as M
from research.index_straddle import options as O
from research.index_straddle.config import Config, RESULTS_DIR

logger = get_logger("research.index_straddle.service")

MIN_BARS_PER_SESSION = 300

# Sigma and the implied-vol regime are EWMAs over the PRIOR 20 sessions, shifted
# one day. A session cannot be evaluated until that history exists — so a run
# that starts exactly at the requested date silently loses its first ~6 days.
# We therefore always load this much extra history BEFORE the window, compute the
# indicators on the full series, and only then cut the trades back to the window.
WARMUP_CALENDAR_DAYS = 60
MIN_WARMUP_SESSIONS = 6

# A broker pull fetches 1-minute bars in 60-day chunks, and the shared loader
# swallows a failed chunk with nothing but a log line. That is how a run quietly
# ends up with half the history it asked for, so we measure what actually arrived
# against what the calendar says should be there.
BROKER_DEFAULT_START = date(2022, 1, 1)
NSE_HOLIDAYS_PER_YEAR = 14          # typical; only used to size the expectation
SESSION_COVERAGE_WARN = 0.90


def load_data(cfg: Config, broker=None, token: Optional[int] = None) -> tuple[pd.DataFrame, str]:
    """Uploaded CSV first, broker pull otherwise. One shape either way.

    Loads WARMUP_CALENDAR_DAYS of history BEFORE cfg.start_date so the volatility
    indicators are already warm on the first day the user actually asked for. The
    trades are cut back to the requested window afterwards, in ``run``.
    """
    warm_start = ""
    if cfg.start_date:
        warm_start = str(pd.Timestamp(cfg.start_date).date()
                         - pd.Timedelta(days=WARMUP_CALENDAR_DAYS))
    if cfg.csv_path:
        df = D.load_csv(cfg.csv_path)
        src = f"csv:{Path(cfg.csv_path).name}"
    else:
        if broker is None or not token:
            raise ValueError("Upload a 1-minute CSV, or connect Zerodha to pull history")
        end = (pd.Timestamp(cfg.end_date).date() if cfg.end_date else date.today())
        if cfg.start_date:
            start = pd.Timestamp(warm_start).date()
        else:
            start = date(2022, 1, 1)
        df = D.load_from_broker(broker, token, start, end, "minute")
        src = "broker:NIFTY 50"
    # slice to [start - warmup, end]; the window itself is applied to the trades
    df = D.slice_dates(df, warm_start, cfg.end_date)
    return df, src


def drop_partial_sessions(df: pd.DataFrame, min_bars: int = MIN_BARS_PER_SESSION):
    """Special sessions (Muhurat and similar) are not normal trading days."""
    if df.empty:
        return df, 0
    n = df.groupby(df.index.date).size()
    good = set(n[n >= min_bars].index)
    dropped = int(len(n) - len(good))
    return df[[d in good for d in df.index.date]], dropped


def coverage(df: pd.DataFrame, cfg: Config) -> dict:
    """How many sessions were even eligible, and why the rest were not."""
    if df.empty:
        return {}
    days = sorted({d for d in df.index.date})
    if cfg.start_date:
        w = pd.Timestamp(cfg.start_date).date()
        days = [d for d in days if d >= w]
    if not days:
        return {}
    in_dte = [d for d in days if cfg.dte_min <= E.calendar_dte(d) <= cfg.dte_max]
    return {
        "sessions": len(days),
        "in_dte_window": len(in_dte),
        "dte_window": f"{cfg.dte_min}–{cfg.dte_max}",
        "first_day": str(days[0]), "last_day": str(days[-1]),
    }


def data_coverage_warning(df: pd.DataFrame, cfg: Config, source: str) -> str:
    """Did we actually receive the history we asked for?

    Compares the sessions present against the number of weekdays the span should
    contain. A short or gappy pull is the most likely reason a run returns fewer
    trades than expected, and it is otherwise invisible.
    """
    if df.empty:
        return ""
    days = sorted({d for d in df.index.date})
    first, last = days[0], days[-1]
    span_days = (last - first).days + 1
    if span_days < 30:
        return ""
    weekdays = sum(1 for i in range(span_days)
                   if (first + pd.Timedelta(days=i)).weekday() < 5)
    expected = weekdays - int(NSE_HOLIDAYS_PER_YEAR * span_days / 365.0)
    got = len(days)
    msgs = []
    if expected > 0 and got < expected * SESSION_COVERAGE_WARN:
        msgs.append(
            f"Only {got} trading sessions arrived for {first} to {last}, where roughly "
            f"{expected} were expected — about {100 * got / expected:.0f}% coverage. "
            "Some history is missing.")
    # a broker pull that never reached the requested start
    if source.startswith("broker") and not cfg.start_date:
        late = (first - BROKER_DEFAULT_START).days
        if late > 45:
            msgs.append(
                f"The pull was requested from {BROKER_DEFAULT_START} but the earliest bar "
                f"received is {first} — Zerodha served {late} days less than asked. "
                "Historical minute data is a paid Kite add-on and is capped by "
                "subscription; upload a CSV for the full history.")
    if msgs:
        msgs.append("Chunked broker pulls skip a failed request with only a log warning, "
                    "so check the server log for 'history chunk failed'.")
    return " ".join(msgs)


def run(cfg: Config, *, df: Optional[pd.DataFrame] = None, broker=None,
        token: Optional[int] = None, universe=None, write: bool = True,
        out_dir: Optional[Path] = None) -> dict:
    cfg = cfg.sanitized()
    if df is None:
        df, source = load_data(cfg, broker=broker, token=token)
    else:
        source = "supplied"
    if df.empty:
        return {"status": "error", "message": "No usable bars after cleaning"}

    rows_dropped = int(df.attrs.get("rows_dropped", 0))
    dupes = int(df.attrs.get("duplicates_removed", 0))
    df, partial_dropped = drop_partial_sessions(df)
    if df.empty:
        return {"status": "error", "message": "No full sessions in the data"}

    # ── premium source ──
    legs_for_day = None
    contract_example = None
    if cfg.premium_source == "broker":
        if broker is None or universe is None:
            return {"status": "error",
                    "message": "Real-premium mode needs a connected Zerodha session. "
                               "Switch the premium source to 'model', or connect the broker."}
        resolver = O.ContractResolver(universe, cfg.index)
        seen = {}

        def legs_for_day(d, day_frame):
            t_in = E._hhmm(cfg.entry_time, datetime.strptime("10:00", "%H:%M").time())
            rows = np.where(day_frame.index.time >= t_in)[0]
            if len(rows) == 0:
                return None
            spot = float(day_frame["close"].values[int(rows[0])])
            legs = O.broker_day_legs(resolver, broker, cfg, d, spot)
            if legs and not seen:
                seen.update(legs.get("contracts") or {})
            return legs
        contract_example = seen

    # How much of the loaded data is warm-up rather than the requested window?
    sessions_all = sorted({d for d in df.index.date})
    win_start = pd.Timestamp(cfg.start_date).date() if cfg.start_date else None
    warmup_sessions = len([d for d in sessions_all if win_start and d < win_start])

    res = E.run(df, cfg, legs_for_day)
    trades = res["trades"]
    # Trades inside the warm-up belong to history, not to the user's window.
    if win_start:
        trades = [t for t in trades if pd.Timestamp(t["date"]).date() >= win_start]
        # re-run the account so equity and drawdown start at the window, not before
        kept = {t["date"] for t in trades}
        raws = [r for r in res["raw"] if str(r.trade_date) in kept]
        trades = E.account(raws, cfg)

    warmup_note = ""
    if win_start:
        if warmup_sessions == 0:
            warmup_note = ("No history before the start date, so the first ~6 sessions "
                           "of the window had no volatility baseline and were skipped. "
                           "Use a dataset that begins earlier, or widen the start date.")
        elif warmup_sessions < MIN_WARMUP_SESSIONS:
            warmup_note = (f"Only {warmup_sessions} session(s) of warm-up available; "
                           f"{MIN_WARMUP_SESSIONS} are needed for a stable baseline. "
                           "Early days in the window may have been skipped.")
    if cfg.premium_source == "broker" and contract_example is not None:
        contract_example = dict(contract_example) or None

    data_warning = data_coverage_warning(df, cfg, source)
    summ = MET.summary(trades, cfg, sessions=len(set(df.index.date)))
    out = {
        "status": "ok",
        "summary": summ,
        "monthly": MET.by_period(trades, "M"),
        "yearly": MET.by_period(trades, "Y"),
        "by_dte": MET.by_dte(trades),
        "checks": MET.integrity(trades, cfg, df),
        "coverage": coverage(df, cfg),
        "config": cfg.to_dict(),
        "describe": cfg.describe(),
        "source": source,
        "premium_source": cfg.premium_source,
        "bars": int(len(df)),
        "first_day": str(min(df.index.date)), "last_day": str(max(df.index.date)),
        "rows_dropped": rows_dropped, "duplicates_removed": dupes,
        "partial_sessions_dropped": partial_dropped,
        "data_warning": data_warning,
        "sessions_loaded": len(set(df.index.date)),
        "warmup_sessions": warmup_sessions,
        "warmup_note": warmup_note,
        "window_start": str(win_start) if win_start else "",
        "window_end": cfg.end_date or "",
        "trades": trades,
        "trade_count": len(trades),
        "contract_example": contract_example,
        "equity_curve": [{"date": t["date"], "equity": t["equity"],
                          "drawdown": t["drawdown"], "pnl": t["pnl"],
                          "return_pct": t["return_pct"]} for t in trades],
        "files": {},
    }

    if write and trades:
        d = Path(out_dir or RESULTS_DIR)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = f"index_straddle_{cfg.direction}_{stamp}"
        try:
            tp = d / f"{tag}_trades.csv"
            pd.DataFrame(trades).to_csv(tp, index=False)
            sp = d / f"{tag}_summary.csv"
            pd.DataFrame([summ]).to_csv(sp, index=False)
            out["files"] = {"trades": str(tp), "summary": str(sp)}
        except Exception as exc:
            logger.error("index_straddle write failed: %s", exc)
            out["write_error"] = str(exc)
    return out


# ── the research-parity reference, shown in the UI ───────────────────
RESEARCH_REFERENCE = {
    "label": "Research result (documented)",
    "config": "SHORT ATM straddle · 0–1 DTE · 10:00→15:20 · stop −35% · "
              "skip prior-day range > 1.3σ · 3 lots × 65 · model premium",
    "dataset": "NIFTY 50 1-minute, 2022-01-03 → 2026-09-11, 1,152 full sessions",
    "trades": 421, "win_rate": 72.9, "mean_return_pct": 16.31,
    "t_stat": 10.0, "pnl_per_year": 483431, "max_drawdown": -65297,
    "note": "The engine reports P&L on the cash basis (credit actually received "
            "after the spread) where the research script used the raw mid, so "
            "app figures run about 1% lower. Everything else matches exactly.",
}


def parity(summary: dict) -> dict:
    """Compare a run against the documented research figures."""
    ref = RESEARCH_REFERENCE
    def delta(got, want):
        if not want:
            return None
        return round((got - want) / abs(want) * 100, 2)
    rows = [
        ("Trades", summary.get("total_trades", 0), ref["trades"]),
        ("Win rate %", summary.get("win_rate", 0), ref["win_rate"]),
        ("Mean return %", summary.get("mean_return_pct", 0), ref["mean_return_pct"]),
        ("t-stat", summary.get("t_stat", 0), ref["t_stat"]),
        ("P&L / year", summary.get("pnl_per_year", 0), ref["pnl_per_year"]),
    ]
    items = [{"metric": m, "engine": g, "research": w, "delta_pct": delta(g, w)}
             for m, g, w in rows]
    worst = max((abs(i["delta_pct"]) for i in items if i["delta_pct"] is not None),
                default=0.0)
    return {"reference": ref, "items": items,
            "matches": bool(worst <= 2.0), "worst_delta_pct": worst}
