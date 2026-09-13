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


def load_data(cfg: Config, broker=None, token: Optional[int] = None) -> tuple[pd.DataFrame, str]:
    """Uploaded CSV first, broker pull otherwise. One shape either way."""
    if cfg.csv_path:
        df = D.load_csv(cfg.csv_path)
        src = f"csv:{Path(cfg.csv_path).name}"
    else:
        if broker is None or not token:
            raise ValueError("Upload a 1-minute CSV, or connect Zerodha to pull history")
        end = date.today()
        start = pd.Timestamp(cfg.start_date).date() if cfg.start_date else date(2022, 1, 1)
        df = D.load_from_broker(broker, token, start, end, "minute")
        src = "broker:NIFTY 50"
    df = D.slice_dates(df, cfg.start_date, cfg.end_date)
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
    in_dte = [d for d in days if cfg.dte_min <= E.calendar_dte(d) <= cfg.dte_max]
    return {
        "sessions": len(days),
        "in_dte_window": len(in_dte),
        "dte_window": f"{cfg.dte_min}–{cfg.dte_max}",
        "first_day": str(days[0]), "last_day": str(days[-1]),
    }


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

    res = E.run(df, cfg, legs_for_day)
    trades = res["trades"]
    if cfg.premium_source == "broker" and contract_example is not None:
        contract_example = dict(contract_example) or None

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
