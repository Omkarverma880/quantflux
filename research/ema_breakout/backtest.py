"""
20 EMA Single Candle Breakout over stored history.

The signal is decided on the index, exactly as the live desk decides it, and then priced on
the REAL option contract the signal implies — the same prints the Options Lab reads, never a
model. The two layers are kept apart on purpose: ``index_points`` shows what the rule did to
the underlying, and ``option`` shows what the trade actually returned after charges. They can
disagree sharply, and when they do the option column is the one that matters.

No look-ahead, by construction:

    the state machine sees bar i and nothing after it
    a trade opens on the CLOSE of its entry bar and closes on the CLOSE of its exit bar,
      which is what ``process_orders_on_close=true`` means in the source
    the contract is fixed by name at entry and never re-centred because spot moved
    a minute with no print carries the last trade and is counted, never interpolated

What the stored chain can and cannot support is reported rather than assumed: if a strike the
settings ask for was never captured, the trade is skipped with the reason, and that count is
part of the result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.ema_breakout import strategy as ST
from research.market_store import store as MS
from research.options_lab.execution import CostModel

logger = get_logger("research.ema_breakout.backtest")

# Backtest-only defaults. The live desk never uses these — it reads lot size from the
# broker's instrument master, because exchanges revise them and a stored contract carries no
# lot size with it.
LOT_SIZE = {"NIFTY": 65, "BANKNIFTY": 35, "FINNIFTY": 65,
            "MIDCPNIFTY": 120, "SENSEX": 20, "BANKEX": 30}


@dataclass
class Config:
    start: str = ""
    end: str = ""
    params: Optional[ST.Params] = None
    lot_size: int = 0                   # 0 = take the index default below
    slippage_pts: float = 0.5           # per side, premium points
    strike_tolerance: int = 2           # how many strike steps off target is still acceptable

    def __post_init__(self):
        if self.params is None:
            self.params = ST.Params()
        if not self.lot_size:
            self.lot_size = LOT_SIZE.get(self.params.index.upper(), 65)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["params"] = self.params.as_dict()
        return d


# ── data ────────────────────────────────────────────────────────────────────
def _spot(index: str, start: str, end: str) -> pd.DataFrame:
    cols = ["timestamp", "open", "high", "low", "close"]
    d = MS.read("spot", index.upper(), start, end, columns=cols)
    if d.empty:
        return d
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    d["minute"] = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d["date"] = d["timestamp"].dt.date
    return d


def resample(d: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Minute bars into the chart's candle, anchored on the session open.

    Anchoring matters: a 5-minute candle must run 09:15-09:19 as the chart shows it, not
    09:10-09:14 off a midnight anchor, or every signal lands on the wrong bar.
    """
    if minutes <= 1 or d.empty:
        return d.copy()
    out = []
    for day, g in d.groupby("date", sort=True):
        g = g.sort_values("timestamp")
        bucket = ST.SESSION_OPEN + ((g["minute"] - ST.SESSION_OPEN) // minutes) * minutes
        a = (g.assign(bucket=bucket).groupby("bucket")
             .agg(timestamp=("timestamp", "first"), open=("open", "first"),
                  high=("high", "max"), low=("low", "min"), close=("close", "last"))
             .reset_index())
        a["minute"] = a["bucket"].astype(int)
        a["date"] = day
        out.append(a.drop(columns=["bucket"]))
    return pd.concat(out, ignore_index=True) if out else d.iloc[0:0].copy()


def _options(index: str, start: str, end: str) -> pd.DataFrame:
    d = MS.read("options", index.upper(), start, end,
                columns=["timestamp", "contract", "expiry_date", "strike", "option_type",
                         "open", "high", "low", "close"])
    if d.empty:
        return d
    d["ts"] = pd.to_datetime(d["timestamp"])
    d["date"] = d["ts"].dt.date
    d["m"] = d["ts"].dt.hour * 60 + d["ts"].dt.minute
    d["expiry_date"] = pd.to_datetime(d["expiry_date"]).dt.date
    return d


def _price_at(day_df: pd.DataFrame, contract: str, minute: int) -> tuple[Optional[float], bool]:
    """A contract's price at a minute, carrying the last print forward.

    The flag says the price had to be carried. Those are counted rather than hidden: on a
    thin strike a carried price is where a backtest quietly turns into fiction.
    """
    g = day_df[(day_df["contract"] == contract) & (day_df["m"] <= minute)]
    if g.empty:
        return None, False
    row = g.sort_values("m").iloc[-1]
    return float(row["close"]), bool(int(row["m"]) != minute)


def _pick_contract(day_df: pd.DataFrame, opt_type: str, want_strike: float,
                   minute: int, step: float, tol_steps: int) -> Optional[dict]:
    """The listed strike nearest the one the settings ask for, within tolerance."""
    avail = day_df[(day_df["option_type"] == opt_type) & (day_df["m"] <= minute)]
    if avail.empty:
        return None
    exp = sorted(avail["expiry_date"].dropna().unique())
    if not exp:
        return None
    near = avail[avail["expiry_date"] == exp[0]]
    strikes = sorted(near["strike"].dropna().unique())
    if not strikes:
        return None
    best = min(strikes, key=lambda s: abs(float(s) - want_strike))
    off_by = abs(float(best) - want_strike)
    if off_by > tol_steps * step:
        return {"ok": False, "strike": float(best), "off_by": off_by,
                "why": (f"the nearest listed {opt_type} to {want_strike:g} is {best:g}, "
                        f"{off_by:.0f} points away — more than the "
                        f"{tol_steps} strike(s) allowed. The stored chain does not reach "
                        f"that strike.")}
    row = near[near["strike"] == best].iloc[0]
    return {"ok": True, "contract": row["contract"], "strike": float(best),
            "expiry": row["expiry_date"], "off_by": off_by}


# ── statistics ──────────────────────────────────────────────────────────────
def _stats(pnl: np.ndarray) -> dict:
    base = {"trades": 0, "net": 0.0, "avg": None, "median": None, "win_rate": None,
            "wins": 0, "losses": 0, "avg_win": None, "avg_loss": None, "best": None,
            "worst": None, "profit_factor": None, "max_drawdown": None,
            "t_stat": None, "p_value": None}
    if not len(pnl):
        return base
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq = np.cumsum(pnl)
    gl = float(-losses.sum())
    t = (float(pnl.mean() / (pnl.std(ddof=1) / np.sqrt(len(pnl))))
         if len(pnl) > 1 and pnl.std(ddof=1) > 0 else None)
    p = None
    if len(pnl) >= 5:
        rng = np.random.default_rng(7)
        p = float((rng.choice(pnl, size=(5000, len(pnl)), replace=True).mean(axis=1) <= 0).mean())
    base.update({
        "trades": int(len(pnl)), "net": round(float(pnl.sum()), 0),
        "avg": round(float(pnl.mean()), 1), "median": round(float(np.median(pnl)), 1),
        "win_rate": round(100.0 * len(wins) / len(pnl), 1),
        "wins": int(len(wins)), "losses": int(len(losses)),
        "avg_win": round(float(wins.mean()), 1) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 1) if len(losses) else None,
        "best": round(float(pnl.max()), 0), "worst": round(float(pnl.min()), 0),
        "profit_factor": round(float(wins.sum()) / gl, 2) if gl > 0 else None,
        "max_drawdown": round(float((np.maximum.accumulate(eq) - eq).max()), 0),
        "t_stat": round(t, 2) if t is not None else None,
        "p_value": round(p, 4) if p is not None else None,
    })
    return base


def _verdict(s: dict, skipped: int, days: int, action: str) -> dict:
    n, t = s["trades"], s.get("t_stat")
    net = s.get("net") or 0.0
    if n == 0:
        return {"tone": "none", "headline": "No trade could be priced in this span.",
                "detail": f"{skipped} signal(s) were skipped — the reasons are listed below. "
                          f"Usually the stored chain does not carry the strike the settings "
                          f"ask for."}
    if n < 30:
        return {"tone": "thin", "headline": f"{n} trades is too few to conclude anything.",
                "detail": f"Net {net:,.0f} over {days} sessions. On this few trades one good "
                          f"or bad day moves the total a long way."}
    if net <= 0:
        return {"tone": "bad", "headline": f"This lost money: {net:,.0f} over {n} trades.",
                "detail": ("The signals fired and the option P&L was negative after real "
                           "charges." + ("" if action == ST.BUY else
                                         " Selling caps the gain and leaves the loss open, so "
                                         "read the worst trade before the total."))}
    if t is None or t < 2.0:
        return {"tone": "weak",
                "headline": f"Profitable but not significant: {net:,.0f} over {n} trades, t={t}.",
                "detail": "A t-statistic under 2 means a run of luck this size is ordinary."}
    return {"tone": "ok", "headline": f"Net {net:,.0f} over {n} trades, t={t}.",
            "detail": f"Worst trade {s.get('worst'):,.0f}, max drawdown "
                      f"{s.get('max_drawdown'):,.0f} — judge the total against those."}


def _coverage(index: str) -> Optional[str]:
    try:
        s = MS.summary() or {}
        parts = []
        for kind, label in (("spot", "index minutes"), ("options", "options")):
            blk = ((s.get(kind) or {}).get("by_underlying") or {}).get(index.upper()) or {}
            a, b = blk.get("first_month"), blk.get("last_month")
            if a and b:
                parts.append(f"{label} {a} to {b}")
        return "; ".join(parts) or None
    except Exception as exc:
        logger.debug("coverage lookup failed: %s", exc)
    return None


# ── the run ─────────────────────────────────────────────────────────────────
def run(cfg: Config) -> dict:
    p = cfg.params
    if not cfg.start or not cfg.end:
        return {"status": "bad_request", "message": "Give a start and an end date."}
    if pd.Timestamp(cfg.start) > pd.Timestamp(cfg.end):
        return {"status": "bad_request", "message": "The start date is after the end date."}

    spot = _spot(p.index, cfg.start, cfg.end)
    if spot.empty:
        return {"status": "no_data",
                "message": (f"The store holds no {p.index.upper()} index minutes in that "
                            f"range."),
                "coverage": _coverage(p.index), "config": cfg.as_dict()}

    bars = resample(spot, p.timeframe)
    need = p.ema_length + 4
    if len(bars) < need:
        return {"status": "no_data",
                "message": f"only {len(bars)} candles in range — the {p.ema_length} EMA needs "
                           f"at least {need}",
                "config": cfg.as_dict()}

    res = ST.run(bars, p)
    raw_trades = [t for t in res["trades"] if t.get("exit_bar") is not None]
    days = sorted(spot["date"].unique())
    span = {"start": str(days[0]), "end": str(days[-1]), "days": len(days),
            "candles": int(len(bars)), "timeframe": p.timeframe}

    # what the rule did to the index itself, before any option is involved
    pts = []
    for t in raw_trades:
        d = t["exit_close"] - t["entry_close"]
        pts.append(d if t["direction"] == "LONG" else -d)
    index_points = {
        "trades": len(pts),
        "net_points": round(float(np.sum(pts)), 1) if pts else 0.0,
        "avg_points": round(float(np.mean(pts)), 2) if pts else None,
        "win_rate": (round(100.0 * sum(1 for x in pts if x > 0) / len(pts), 1)
                     if pts else None),
        "note": ("What the rule did to the index, in points. It is not the trade — an option "
                 "has premium, decay and a spread, and the option column below is the one "
                 "that was actually paid."),
    }

    if not raw_trades:
        return {"status": "no_trades", "span": span, "index": p.index.upper(),
                "rules": ST.describe(p), "index_points": index_points,
                "events": res["events"][-300:], "event_count": len(res["events"]),
                "overall": _stats(np.array([])), "trades": [], "skipped": 0,
                "skipped_reasons": [],
                "verdict": {"tone": "none",
                            "headline": "The rule produced no completed trade in this span.",
                            "detail": "Every event it did produce is listed in the log — "
                                      "usually setups armed and expired without a "
                                      "confirmation candle."},
                "config": cfg.as_dict()}

    opts = _options(p.index, cfg.start, cfg.end)
    if opts.empty:
        return {"status": "no_option_data",
                "message": (f"{len(raw_trades)} trades fired on the index, but the store holds "
                            f"no {p.index.upper()} option minutes in that range to price them "
                            f"on. The index-points column below still applies."),
                "span": span, "index": p.index.upper(), "rules": ST.describe(p),
                "index_points": index_points, "coverage": _coverage(p.index),
                "overall": _stats(np.array([])), "trades": [], "skipped": len(raw_trades),
                "skipped_reasons": [("no option data in the store for this range",
                                     len(raw_trades))],
                "events": res["events"][-300:], "event_count": len(res["events"]),
                "verdict": {"tone": "none",
                            "headline": "No option prices for this range.",
                            "detail": "The signals are real; there is nothing stored to price "
                                      "them against."},
                "config": cfg.as_dict()}

    costs = CostModel(slippage_pts=cfg.slippage_pts)
    qty = cfg.lot_size * max(1, p.lots)
    out_trades, skipped, why_counts = [], 0, {}

    def skip(reason: str) -> None:
        nonlocal skipped
        skipped += 1
        why_counts[reason[:160]] = why_counts.get(reason[:160], 0) + 1

    for t in raw_trades:
        entry_day = date.fromisoformat(t["entry_date"])
        exit_day = date.fromisoformat(t["exit_date"])
        # A candle is stamped with the minute it STARTS, but the decision is made on its
        # CLOSE. Pricing the fill at the start minute buys the option before the move that
        # triggered the signal had happened — a look-ahead that flattered every result until
        # it was found. The fill belongs on the candle's last minute.
        entry_min = t["entry_minute"] + p.timeframe - 1
        exit_min = t["exit_minute"] + p.timeframe - 1
        side = ST.LONG if t["direction"] == "LONG" else ST.SHORT
        leg = ST.option_for(side, p.option_action, t["entry_close"], p)
        din = opts[opts["date"] == entry_day]
        if din.empty:
            skip(f"no option prints stored for {entry_day}")
            continue
        pick = _pick_contract(din, leg["option_type"], leg["strike"], entry_min,
                              p.step, cfg.strike_tolerance)
        if pick is None:
            skip(f"no {leg['option_type']} listed on {entry_day}")
            continue
        if not pick.get("ok"):
            skip(pick["why"])
            continue
        entry_px, entry_carried = _price_at(din, pick["contract"], entry_min)
        if entry_px is None or entry_px <= 0:
            skip(f"{pick['contract']} had no usable price at entry")
            continue
        dout = opts[opts["date"] == exit_day]
        exit_px, carried = _price_at(dout, pick["contract"], exit_min)
        if exit_px is None or exit_px <= 0:
            skip(f"{pick['contract']} had no price to close against on {exit_day}")
            continue

        long_opt = p.option_action == ST.BUY
        fill_in = entry_px + cfg.slippage_pts if long_opt else entry_px - cfg.slippage_pts
        fill_out = exit_px - cfg.slippage_pts if long_opt else exit_px + cfg.slippage_pts
        gross = ((fill_out - fill_in) if long_opt else (fill_in - fill_out)) * qty
        buy_px, sell_px = ((fill_in, fill_out) if long_opt else (fill_out, fill_in))
        charge = costs.round_trip(buy_px, sell_px, qty)
        out_trades.append({
            **t,
            "option_type": leg["option_type"], "action": p.option_action,
            "contract": pick["contract"], "strike": pick["strike"],
            "expiry": str(pick["expiry"]),
            "entry_time": ST.hhmm(entry_min), "exit_time": ST.hhmm(exit_min),
            "carried_entry_price": entry_carried,
            "entry_premium": round(entry_px, 2), "exit_premium": round(exit_px, 2),
            "qty": qty, "lots": p.lots,
            "index_points": round((t["exit_close"] - t["entry_close"])
                                  * (1 if side == ST.LONG else -1), 1),
            "gross_rs": round(gross, 0), "costs_rs": round(charge, 0),
            "pnl_rs": round(gross - charge, 0), "carried_exit_price": carried,
        })

    pnl = np.array([t["pnl_rs"] for t in out_trades], dtype=float)
    overall = _stats(pnl)
    by_dir = {d: _stats(np.array([t["pnl_rs"] for t in out_trades
                                  if t["direction"] == d], dtype=float))
              for d in ("LONG", "SHORT")}
    reasons: dict[str, dict] = {}
    for t in out_trades:
        b = reasons.setdefault(t["exit_reason"], {"n": 0, "net": 0.0})
        b["n"] += 1
        b["net"] = round(b["net"] + t["pnl_rs"], 0)

    return {
        "status": "ok" if out_trades else "no_trades",
        "span": span, "index": p.index.upper(), "lot_size": cfg.lot_size,
        "rules": ST.describe(p),
        "signals": len(raw_trades), "taken": len(out_trades), "skipped": skipped,
        "skipped_reasons": sorted(why_counts.items(), key=lambda kv: -kv[1])[:8],
        "index_points": index_points,
        "overall": overall, "by_direction": by_dir, "exit_reasons": reasons,
        "trades": out_trades[-400:],
        "trades_truncated": ({"shown": 400, "of": len(out_trades)}
                             if len(out_trades) > 400 else None),
        "events": res["events"][-300:], "event_count": len(res["events"]),
        "verdict": _verdict(overall, skipped, len(days), p.option_action),
        "costs": {"slippage_pts_per_side": cfg.slippage_pts,
                  "note": "Zerodha statutory charges on the real side, plus slippage against "
                          "the position on entry and exit."},
        "lot_note": ("Lot size is a backtest setting — a stored contract carries none. The "
                     "live desk reads it from the broker's instrument master instead."),
        "config": cfg.as_dict(),
    }
