"""
What the price-action signals actually did, on real option candles.

The indicator draws labels. This answers the only question that matters about them: if you had
bought the option each label implies, what came back. Nothing here is modelled — the fills are
prints from the Market Store, through the Options Lab engine, which already enforces the rules
a backtest needs in order not to lie:

    decision on a CLOSED bar, fill at the next bar's OPEN
    the contract is fixed by name at entry and never silently re-ATM'd
    inside one bar the stop is tested BEFORE the target
    a bar that opens through the stop fills at that open
    real Zerodha charges plus slippage, on the actual side

Signals are generated on a continuous intraday series, the way the script runs on a
TradingView chart: the EMA and the swing window carry across the overnight gap. ``day_reset``
turns that off if you would rather each session start clean, and the summary says which was
used, because the two are not the same strategy.

No claim is made here about whether the signals are worth trading. The summary reports a
t-statistic and a bootstrap p-value on the per-trade P&L and splits the span in half, so the
number can be judged rather than believed. A short span with few trades usually cannot support
a conclusion, and the verdict says so when that is the case.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.market_store import store as MS
from research.options_lab.execution import CostModel, ExitRule, Signal, run_signals
from research.pas import strategy as ST

logger = get_logger("research.pas.backtest")

LOT = 65
SESSION_OPEN = 9 * 60 + 15
LAST_ENTRY = 15 * 60            # no new position in the closing minutes


@dataclass
class Config:
    start: str = ""
    end: str = ""
    timeframe: int = 5
    params: Optional[ST.Params] = None     # the indicator's own settings
    # ── the trade each signal implies ──
    moneyness: int = 0                     # 0 ATM, +1 one strike out, -1 one in
    lots: int = 1
    target_pct: float = 60.0               # on the OPTION premium, not on the index
    stop_pct: float = 30.0
    max_hold_min: int = 60
    squareoff: int = 15 * 60 + 15
    slippage_pts: float = 0.5              # per side, premium points
    one_at_a_time: bool = True
    day_reset: bool = False                # recompute the indicator per session
    last_entry: int = LAST_ENTRY

    def __post_init__(self):
        if self.params is None:
            self.params = ST.Params()

    def as_dict(self) -> dict:
        d = asdict(self)
        d["params"] = self.params.as_dict()
        return d


def _spot(start: str, end: str) -> pd.DataFrame:
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    d = MS.read("spot", "NIFTY", start, end, columns=cols)
    if d.empty:
        d = MS.read("spot", "NIFTY", start, end, columns=cols[:-1])
    if d.empty:
        return d
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    d["minute"] = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d["date"] = d["timestamp"].dt.date
    return d


def resample(d: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Minute bars into the chart's candle, stamped with the minute each one STARTS.

    The bucket is anchored on the session open, so a 5-minute candle runs 09:15-09:19 as the
    chart shows it — not 09:10-09:14 off a midnight anchor.
    """
    if minutes <= 1 or d.empty:
        return d.copy()
    out = []
    for day, g in d.groupby("date", sort=True):
        g = g.sort_values("timestamp")
        agg = {"timestamp": ("timestamp", "first"), "open": ("open", "first"),
               "high": ("high", "max"), "low": ("low", "min"), "close": ("close", "last")}
        if "volume" in g.columns:
            agg["volume"] = ("volume", "sum")
        bucket = SESSION_OPEN + ((g["minute"] - SESSION_OPEN) // minutes) * minutes
        r = g.assign(bucket=bucket).groupby("bucket").agg(**agg).reset_index()
        r["minute"] = r["bucket"].astype(int)
        r["date"] = day
        out.append(r.drop(columns=["bucket"]))
    return pd.concat(out, ignore_index=True) if out else d.iloc[0:0].copy()


def signals(bars: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, list[Signal]]:
    """Run the indicator, then turn each label into the option trade it implies."""
    p = cfg.params
    if cfg.day_reset:
        frames = [ST.prepare(g, p) for _, g in bars.groupby("date", sort=True)]
        d = pd.concat(frames, ignore_index=True) if frames else bars.iloc[0:0].copy()
    else:
        d = ST.prepare(bars, p)

    out: list[Signal] = []
    for i in range(len(d)):
        r = d.iloc[i]
        call, put = bool(r["call_signal"]), bool(r["put_signal"])
        if not (call or put):
            continue
        # the decision bar closes on its LAST minute; the engine fills the minute after that
        decide = int(r["minute"]) + cfg.timeframe - 1
        if decide > cfg.last_entry:
            continue
        level = r["resistance"] if call else r["support"]
        out.append(Signal(date=r["date"], minute=decide,
                          side=ST.CE if call else ST.PE, action="BUY",
                          moneyness=cfg.moneyness, lots=cfg.lots,
                          tag="BULL" if call else "BEAR",
                          score=float(level) if np.isfinite(level) else float("nan")))
    return d, out


def _tstat(x: np.ndarray) -> Optional[float]:
    if len(x) < 2 or x.std(ddof=1) == 0:
        return None
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def _bootstrap_p(x: np.ndarray, n: int = 5000, seed: int = 7) -> Optional[float]:
    """How often a resample of these same trades comes out at or below zero.

    The one-sided question "could this mean be luck", answered by resampling the trades
    themselves rather than assuming they are normally distributed — option P&L is not.
    """
    if len(x) < 5:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float((means <= 0).mean())


def _stats(pnl: np.ndarray) -> dict:
    """Every summary carries the same keys, so a zero-trade slice cannot crash a caller."""
    base = {"trades": 0, "net": 0.0, "avg": None, "median": None, "win_rate": None,
            "avg_win": None, "avg_loss": None, "best": None, "worst": None,
            "profit_factor": None, "max_drawdown": None, "t_stat": None, "p_value": None}
    if not len(pnl):
        return base
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq = np.cumsum(pnl)
    gross_loss = float(-losses.sum())
    t, p = _tstat(pnl), _bootstrap_p(pnl)
    base.update({
        "trades": int(len(pnl)),
        "net": round(float(pnl.sum()), 0),
        "avg": round(float(pnl.mean()), 1),
        "median": round(float(np.median(pnl)), 1),
        "win_rate": round(100.0 * len(wins) / len(pnl), 1),
        "avg_win": round(float(wins.mean()), 1) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 1) if len(losses) else None,
        "best": round(float(pnl.max()), 0),
        "worst": round(float(pnl.min()), 0),
        "profit_factor": round(float(wins.sum()) / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown": round(float((np.maximum.accumulate(eq) - eq).max()), 0),
        "t_stat": round(t, 2) if t is not None else None,
        "p_value": round(p, 4) if p is not None else None,
    })
    return base


def _verdict(s: dict, oos: dict, span_days: int) -> dict:
    """What the numbers do and do not support. Deliberately hard to read as a promise."""
    n, t = s["trades"], s.get("t_stat")
    net = s.get("net") or 0.0
    if n == 0:
        return {"tone": "none", "headline": "No signal fired in this span.",
                "detail": "Nothing to judge. Widen the dates, shorten the swing length, or "
                          "turn the VWAP filter off and see whether signals appear at all."}
    if n < 30:
        return {"tone": "thin",
                "headline": f"{n} trades is too few to conclude anything.",
                "detail": f"Net {net:,.0f} over {span_days} sessions. On this few trades the "
                          f"total would move a long way on one more good or bad day, so read "
                          f"it as a sample, not as an edge."}
    if net <= 0:
        return {"tone": "bad",
                "headline": f"This lost money: {net:,.0f} over {n} trades.",
                "detail": "The signals fired and the option P&L was negative after real "
                          "charges. Changing the exit percentages will change the number, but "
                          "changing them until it turns green is fitting, not testing."}
    if t is None or t < 2.0:
        return {"tone": "weak",
                "headline": f"Profitable but not significant: {net:,.0f} over {n} trades, "
                            f"t={t}.",
                "detail": "A t-statistic under 2 means a lucky run of this size is ordinary. "
                          "More history, not a better-tuned exit, is what would settle it."}
    held = (oos.get("net") or 0) > 0
    return {"tone": "ok" if held else "mixed",
            "headline": (f"Net {net:,.0f} over {n} trades, t={t}"
                         + ("." if held else " — but the second half gave it back.")),
            "detail": ("The second half of the span was positive too, which is the weakest "
                       "useful check there is. It is not forward performance."
                       if held else
                       "The first half carried the result and the second half did not hold up. "
                       "That pattern is what a fitted rule looks like.")}


def _coverage() -> Optional[str]:
    """What the store actually holds, so "no data" names a range instead of just failing.

    Both kinds are reported because a span can have spot and no options, which looks like a
    broken strategy if the message only mentions one of them.
    """
    try:
        s = MS.summary() or {}
        parts = []
        for kind, label in (("spot", "NIFTY minutes"), ("options", "NIFTY options")):
            blk = ((s.get(kind) or {}).get("by_underlying") or {}).get("NIFTY") or {}
            a, b = blk.get("first_month"), blk.get("last_month")
            if a and b:
                parts.append(f"{label} from {a} to {b}")
        return "; ".join(parts) or None
    except Exception as exc:
        logger.debug("coverage lookup failed: %s", exc)
    return None


def run(cfg: Config) -> dict:
    """The whole thing: the indicator's signals over the span, priced on real contracts."""
    if not cfg.start or not cfg.end:
        return {"status": "bad_request", "message": "Give a start and an end date."}
    if pd.Timestamp(cfg.start) > pd.Timestamp(cfg.end):
        return {"status": "bad_request", "message": "The start date is after the end date."}

    spot = _spot(cfg.start, cfg.end)
    if spot.empty:
        have = _coverage()
        return {"status": "no_data",
                "message": ("The store holds no NIFTY spot minutes in that range."
                            + (f" It covers {have}." if have else "")),
                "coverage": have, "config": cfg.as_dict()}

    first, last = min(spot["date"]), max(spot["date"])
    clipped = (str(first) != str(pd.Timestamp(cfg.start).date())
               or str(last) != str(pd.Timestamp(cfg.end).date()))
    span_days = int(spot["date"].nunique())

    bars = resample(spot, cfg.timeframe)
    need = cfg.params.swing_len + cfg.params.ema_len + 2
    if len(bars) < need:
        return {"status": "no_data",
                "message": f"only {len(bars)} candles in range — the {cfg.params.ema_len} EMA "
                           f"and the {cfg.params.swing_len}-bar swing need at least {need} "
                           f"before a signal means anything",
                "config": cfg.as_dict()}

    d, sigs = signals(bars, cfg)
    span = {"start": str(first), "end": str(last), "days": span_days,
            "candles": int(len(bars)), "timeframe": cfg.timeframe, "clipped": clipped,
            "note": ("The store does not cover the whole range you asked for, so this is the "
                     "overlap." if clipped else None)}
    if not sigs:
        return {"status": "no_signals",
                "message": ST.describe(cfg.params) + " Nothing met that in this span.",
                "span": span, "rules": ST.describe(cfg.params), "config": cfg.as_dict()}

    opts = MS.read("options", "NIFTY", cfg.start, cfg.end)
    if opts.empty:
        return {"status": "no_data",
                "message": f"{len(sigs)} signals fired, but the store holds no NIFTY option "
                           f"minutes in that range to price them on.",
                "signal_count": len(sigs), "span": span, "config": cfg.as_dict()}

    rule = ExitRule(target_pct=cfg.target_pct, stop_pct=cfg.stop_pct,
                    max_hold_min=cfg.max_hold_min, squareoff=cfg.squareoff)
    costs = CostModel(slippage_pts=cfg.slippage_pts)
    trades = run_signals(opts, spot, sigs, rule, LOT, costs, "NIFTY",
                         one_position_at_a_time=cfg.one_at_a_time)

    rows = [t.as_dict() for t in trades]
    pnl = np.array([r["pnl_rs"] for r in rows], dtype=float)
    overall = _stats(pnl)

    days = sorted({r["date"] for r in rows})
    half = days[len(days) // 2] if days else None
    ins = np.array([r["pnl_rs"] for r in rows if half and r["date"] < half], dtype=float)
    oos = np.array([r["pnl_rs"] for r in rows if half and r["date"] >= half], dtype=float)

    by_side = {tag: _stats(np.array([r["pnl_rs"] for r in rows if r["tag"] == tag],
                                    dtype=float))
               for tag in ("BULL", "BEAR")}

    reasons: dict[str, dict] = {}
    for r in rows:
        b = reasons.setdefault(r["exit_reason"], {"n": 0, "net": 0.0})
        b["n"] += 1
        b["net"] += r["pnl_rs"]
    for b in reasons.values():
        b["net"] = round(b["net"], 0)

    oos_stats = _stats(oos)
    return {
        "status": "ok",
        "span": span,
        "rules": ST.describe(cfg.params),
        "levels_basis": ("each session computed on its own" if cfg.day_reset else
                         "one continuous series, as the script runs on a chart — the EMA and "
                         "the swing window carry across the overnight gap"),
        "signal_count": len(sigs),
        "taken": len(rows),
        "skipped": len(sigs) - len(rows),
        "skipped_why": ("a signal that arrives while a trade is still open is not stacked, and "
                        "one with no tradable contract in the store is dropped"
                        if cfg.one_at_a_time else
                        "a signal with no tradable contract in the store is dropped"),
        "overall": overall,
        "per_day": round(float(pnl.sum()) / span_days, 0) if span_days else None,
        "in_sample": _stats(ins), "out_of_sample": oos_stats,
        "split_at": str(half) if half else None,
        "by_side": by_side,
        "exit_reasons": reasons,
        "trades": rows,
        "verdict": _verdict(overall, oos_stats, span_days),
        "config": cfg.as_dict(),
        "costs": {"slippage_pts_per_side": cfg.slippage_pts,
                  "note": "Zerodha statutory charges plus slippage, on the real side."},
    }
