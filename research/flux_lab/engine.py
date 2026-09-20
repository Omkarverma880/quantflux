"""
Flux Lab — the event-driven engine.

Time moves forward one bar at a time. On each bar the engine hands ``rules.evaluate`` a single
row of the causal feature frame; if the strategy fires, the trade is filled on a **later** bar
according to the execution model and then walked forward tick by tick until an exit condition is
met. Nothing about the entry can depend on a bar the engine has not reached yet.

Three things this engine refuses to do, because each one silently manufactures profit:

* **It never reads a future bar to decide.** The decision row is `frame.iloc[i]`; the fill is at
  `i + entry_offset`; the walk starts after the fill.
* **It never reports NIFTY points as option P&L.** Every trade carries both, separately: what the
  index did, and what the contract you would actually have bought did.
* **It never invents a contract.** A strike is only tradable if it is present in the stored option
  data for that session and that expiry, at that minute.

The same ``decide`` function drives replay and live paper trading; only the source of the bars
changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.flux_lab import features as FE
from research.flux_lab import rules as RU
from research.options_lab.execution import (
    LAST_BAR, SESSION_OPEN, STEP, CostModel, DayBook, Signal, resolve_contract,
)

logger = get_logger("research.flux_lab.engine")

LOT_SIZE = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "SENSEX": 20}
ENTRY_MODELS = ("signal_close", "next_open", "next_close", "delay")
EXPIRY_RULES = ("nearest", "next", "monthly")
POINT_LADDER = [5, 10, 15, 20, 25, 30, 40, 50]


@dataclass
class Execution:
    """How a signal becomes a fill, and how the position is left."""
    entry_model: str = "next_open"      # signal_close | next_open | next_close | delay
    entry_delay_bars: int = 1           # used by "delay"
    target_pct: float = 30.0            # option premium, % — 0 disables
    stop_pct: float = 15.0
    target_pts: float = 0.0             # option premium, absolute points — overrides % when > 0
    stop_pts: float = 0.0
    trail_after_pct: float = 0.0
    trail_giveback_pct: float = 0.0
    max_hold_min: int = 30              # 0 = until square-off
    squareoff_min: int = 915            # 15:15
    exit_on_opposite: bool = False
    slippage_pts: float = 0.5           # option premium points, per side
    costs_on: bool = True


@dataclass
class Risk:
    max_trades_per_day: int = 3         # 0 = unlimited
    cooldown_min: int = 10
    max_daily_loss: float = 0.0         # rupees, 0 = off
    max_consecutive_losses: int = 0     # 0 = off
    one_position_at_a_time: bool = True
    lots: int = 1
    min_option_volume: float = 0.0
    min_option_oi: float = 0.0
    min_entry_premium: float = 5.0      # refuse fills on near-worthless contracts


@dataclass
class Selection:
    moneyness: int = 0                  # 0 ATM, +1 first OTM, -1 first ITM (signed by side)
    expiry_rule: str = "nearest"
    min_dte: int = 0                    # skip expiry-day contracts when set to 1


@dataclass
class RunConfig:
    underlying: str = "NIFTY"
    start: str = ""
    end: str = ""
    timeframe: int = 5                  # minutes
    strategy: dict = field(default_factory=dict)
    execution: Execution = field(default_factory=Execution)
    risk: Risk = field(default_factory=Risk)
    selection: Selection = field(default_factory=Selection)
    capital: float = 200000.0

    def as_dict(self) -> dict:
        return {
            "underlying": self.underlying, "start": self.start, "end": self.end,
            "timeframe": self.timeframe, "strategy": self.strategy,
            "execution": self.execution.__dict__, "risk": self.risk.__dict__,
            "selection": self.selection.__dict__, "capital": self.capital,
            "engine_version": RU.ENGINE_VERSION,
        }


def config_from(payload: dict) -> RunConfig:
    p = payload or {}
    return RunConfig(
        underlying=(p.get("underlying") or "NIFTY").upper(),
        start=str(p.get("start") or ""), end=str(p.get("end") or ""),
        timeframe=max(1, min(int(p.get("timeframe") or 5), 60)),
        strategy=p.get("strategy") or {},
        execution=Execution(**{k: v for k, v in (p.get("execution") or {}).items()
                               if k in Execution.__dataclass_fields__}),
        risk=Risk(**{k: v for k, v in (p.get("risk") or {}).items() if k in Risk.__dataclass_fields__}),
        selection=Selection(**{k: v for k, v in (p.get("selection") or {}).items()
                               if k in Selection.__dataclass_fields__}),
        capital=float(p.get("capital") or 200000),
    )


# ── the shared decision ──────────────────────────────────────────────
def decide(row: pd.Series, strategy: RU.Strategy) -> RU.Decision:
    """The one decision function. Backtest, replay and paper trading all call this."""
    return RU.evaluate(row, strategy)


# ── expiry choice, from what was actually listed that day ────────────
def pick_expiry(book: DayBook, day: date, rule: str, min_dte: int = 0) -> Optional[str]:
    """Choose the series to trade from the expiries present in that session's stored data.

    Using the day's own data — rather than today's instrument master — is what keeps the
    backtest honest across expiry changes and contract retirements.
    """
    live = []
    for e in book.expiries:
        try:
            ed = pd.Timestamp(e).date()
        except Exception:
            continue
        dte = (ed - day).days
        if dte >= min_dte:
            live.append((dte, e))
    if not live:
        return None
    live.sort()
    if rule == "next" and len(live) > 1:
        return live[1][1]
    if rule == "monthly":
        by_month: dict = {}
        for dte, e in live:
            ed = pd.Timestamp(e).date()
            by_month.setdefault((ed.year, ed.month), []).append((dte, e))
        month = sorted(by_month)[0]
        return sorted(by_month[month])[-1][1]        # last expiry of the nearest month
    return live[0][1]


# ── one simulated trade ──────────────────────────────────────────────
def simulate(book: DayBook, sig: Signal, cfg: RunConfig, expiry: Optional[str],
             lot_size: int, costs: CostModel) -> Optional[dict]:
    """Fill the signal and walk it forward to its exit.

    Reuses the Market Store's minute grid and the shared ``CostModel``; the walk is the Flux
    Lab's own because it tracks the index and the option side by side and supports absolute
    premium levels, which the Options Lab's percentage-only rule does not.
    """
    ex, risk = cfg.execution, cfg.risk
    t = book.idx(sig.minute)
    offset = {"signal_close": 0, "next_open": 1, "next_close": 1}.get(ex.entry_model, max(1, ex.entry_delay_bars))
    i0 = t + offset
    last = min(book.idx(ex.squareoff_min), len(book.grid) - 1)
    if i0 < 0 or i0 > last:
        return {"skipped": "fill bar falls outside the session"}

    con = resolve_contract(book, sig, cfg.underlying, expiry=expiry, at_minute=book.grid[i0])
    if con is None:
        return {"skipped": "no such strike in the stored data for that expiry"}
    M = book.meta[con]
    if M["miss"][i0]:
        return {"skipped": "the contract did not trade on the fill bar"}

    # liquidity gates, read at the fill bar only
    if risk.min_option_volume and "vol" in M and float(M["vol"][i0]) < risk.min_option_volume:
        return {"skipped": f"option volume {float(M['vol'][i0]):.0f} below the floor"}
    if risk.min_option_oi and "oi" in M and float(M["oi"][i0]) < risk.min_option_oi:
        return {"skipped": f"option OI {float(M['oi'][i0]):.0f} below the floor"}

    op, hi, lo, cl = M["op"], M["hi"], M["lo"], M["cl"]
    raw_entry = cl[i0] if ex.entry_model in ("signal_close", "next_close") else op[i0]
    if not np.isfinite(raw_entry) or raw_entry < risk.min_entry_premium:
        return {"skipped": f"entry premium {raw_entry:.2f} below the floor"}
    entry = float(raw_entry) + ex.slippage_pts            # buying: pay up

    tgt_px = entry + ex.target_pts if ex.target_pts > 0 else (entry * (1 + ex.target_pct / 100) if ex.target_pct > 0 else None)
    stp_px = entry - ex.stop_pts if ex.stop_pts > 0 else (entry * (1 - ex.stop_pct / 100) if ex.stop_pct > 0 else None)
    end = last if ex.max_hold_min <= 0 else min(i0 + ex.max_hold_min, last)

    spot = book.spot
    spot_entry = float(spot[i0]) if spot is not None else float("nan")
    eps = 1e-6
    exit_px, exit_i, reason = None, end, "TIME" if ex.max_hold_min > 0 else "EOD"
    mfe = mae = 0.0                                       # option premium points
    spot_mfe = spot_mae = 0.0                             # index points, in the signal's favour
    peak = 0.0
    ladder_time: dict[int, int] = {}

    for j in range(i0, end + 1):
        mfe = max(mfe, float(hi[j]) - entry)
        mae = min(mae, float(lo[j]) - entry)
        if spot is not None and np.isfinite(spot_entry):
            fav = (float(spot[j]) - spot_entry) if sig.side == "CE" else (spot_entry - float(spot[j]))
            spot_mfe = max(spot_mfe, fav)
            spot_mae = min(spot_mae, fav)
            for pts in POINT_LADDER:                      # when each index milestone was first met
                if pts not in ladder_time and spot_mfe >= pts:
                    ladder_time[pts] = j - i0
        if stp_px is not None and float(lo[j]) <= stp_px + eps:
            px = stp_px
            if j > i0 and float(op[j]) < stp_px:
                px = float(op[j])                         # opened through the stop
            exit_px, exit_i, reason = px, j, "SL"
            break
        if tgt_px is not None and float(hi[j]) >= tgt_px - eps:
            px = tgt_px
            if j > i0 and float(op[j]) > tgt_px:
                px = float(op[j])
            exit_px, exit_i, reason = px, j, "TARGET"
            break
        if ex.trail_after_pct > 0:
            peak = max(peak, (float(hi[j]) / entry - 1) * 100)
            if peak >= ex.trail_after_pct and (float(cl[j]) / entry - 1) * 100 <= peak - ex.trail_giveback_pct:
                exit_px, exit_i, reason = float(cl[j]), j, "TRAIL"
                break
    if exit_px is None:
        exit_px = float(cl[exit_i])
        if exit_i >= last:
            reason = "EOD"
    exit_px = max(float(exit_px) - ex.slippage_pts, 0.05)  # selling: give up the spread

    qty = risk.lots * lot_size
    gross_pts = exit_px - entry
    charges = costs.round_trip(entry, exit_px, qty) if ex.costs_on else 0.0
    pnl = gross_pts * qty - charges
    spot_exit = float(spot[exit_i]) if spot is not None else float("nan")
    spot_move = (spot_exit - spot_entry) if sig.side == "CE" else (spot_entry - spot_exit)
    return {
        "contract": con, "strike": M["strike"], "expiry": M["expiry"], "side": sig.side,
        "entry_time": _hhmm(int(book.grid[i0])), "exit_time": _hhmm(int(book.grid[exit_i])),
        "entry_minute": int(book.grid[i0]), "exit_minute": int(book.grid[exit_i]),
        "option_entry": round(entry, 2), "option_exit": round(exit_px, 2),
        "spot_entry": round(spot_entry, 2), "spot_exit": round(spot_exit, 2),
        "spot_move_pts": round(float(spot_move), 2),
        "spot_mfe_pts": round(float(spot_mfe), 2), "spot_mae_pts": round(float(spot_mae), 2),
        "option_mfe_pts": round(float(mfe), 2), "option_mae_pts": round(float(mae), 2),
        "option_mfe_pct": round(float(mfe) / entry * 100, 2),
        "option_mae_pct": round(float(mae) / entry * 100, 2),
        "gross_pts": round(float(gross_pts), 2), "qty": qty, "lots": risk.lots,
        "charges": round(float(charges), 2), "pnl": round(float(pnl), 2),
        "exit_reason": reason, "held_min": int(book.grid[exit_i] - book.grid[i0]),
        "target_px": round(tgt_px, 2) if tgt_px else None,
        "stop_px": round(stp_px, 2) if stp_px else None,
        "gap_bars": int(M["miss"][i0:exit_i + 1].sum()),
        "ladder_time": {str(k): v for k, v in sorted(ladder_time.items())},
        "dte": (pd.Timestamp(M["expiry"]).date() - pd.Timestamp(book_day(book)).date()).days
        if book_day(book) else None,
    }


def book_day(book: DayBook):
    return getattr(book, "_day", None)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


# ── the run ──────────────────────────────────────────────────────────
def run(bars: pd.DataFrame, options: pd.DataFrame, cfg: RunConfig,
        vix: Optional[pd.DataFrame] = None, futures: Optional[pd.DataFrame] = None,
        progress: Optional[Callable[[str], None]] = None) -> dict:
    """Walk the whole period bar by bar and return every signal, trade and skip."""
    say = progress or (lambda _m: None)
    strat = RU.from_config(cfg.strategy)
    params = {**FE.DEFAULT_PARAMS, **(strat.params or {})}
    tf = FE.resample(bars, cfg.timeframe)
    frame = FE.build(tf, params, vix=vix, futures=futures)
    if frame.empty:
        return {"status": "error", "message": "no index bars in that range", "trades": [], "signals": []}

    costs = CostModel(slippage_pts=0.0)          # slippage is applied on the fill, not twice
    lot = LOT_SIZE.get(cfg.underlying, 65)
    opts = options.copy()
    if not opts.empty:
        ts = pd.to_datetime(opts["timestamp"])
        opts["date"] = ts.dt.date
        opts["m"] = ts.dt.hour * 60 + ts.dt.minute
    by_day_opts = dict(tuple(opts.groupby("date"))) if not opts.empty else {}

    trades: list[dict] = []
    signals: list[dict] = []
    skips: dict[str, int] = {}
    equity = cfg.capital
    peak = equity
    consecutive_losses = 0
    days = sorted(frame["date"].unique())
    say(f"{len(days)} sessions · {len(frame)} bars")

    for n, day in enumerate(days, 1):
        if n % 25 == 0:
            say(f"{n}/{len(days)} sessions")
        dayframe = frame[frame["date"] == day].reset_index(drop=True)
        day_opts = by_day_opts.get(day)
        book = None
        if day_opts is not None and len(day_opts):
            spot_day = dayframe[["minute", "close"]].rename(columns={"minute": "m"})
            book = DayBook(day_opts, spot_day)
            book._day = day
        expiry = pick_expiry(book, day, cfg.selection.expiry_rule, cfg.selection.min_dte) if book else None

        taken = 0
        day_pnl = 0.0
        busy_until = -1
        cooldown_until = -1
        for i in range(len(dayframe)):
            row = dayframe.iloc[i]                       # ← the ONLY row the strategy may see
            minute = int(row["minute"])
            decision = decide(row, strat)
            if not decision.fired:
                continue
            record = {
                "date": str(day), "time": _hhmm(minute), "minute": minute, "side": strat.side,
                "spot": round(float(row["close"]), 2),
                "reasons": decision.reasons, "regime": FE.regime(row),
                "bucket": str(row.get("bucket")), "dow": int(row.get("dow", 0)),
                "month": str(row.get("month")), "year": int(row.get("year", 0)),
                "indicators": _snapshot(row),
            }
            # ── risk gates, all based on simulated time ──
            gate = None
            if cfg.risk.max_trades_per_day and taken >= cfg.risk.max_trades_per_day:
                gate = "daily trade limit reached"
            elif cfg.risk.one_position_at_a_time and minute <= busy_until:
                gate = "a position was still open"
            elif minute < cooldown_until:
                gate = f"cooling down until {_hhmm(cooldown_until)}"
            elif cfg.risk.max_daily_loss and day_pnl <= -abs(cfg.risk.max_daily_loss):
                gate = "daily loss limit hit"
            elif cfg.risk.max_consecutive_losses and consecutive_losses >= cfg.risk.max_consecutive_losses:
                gate = "consecutive-loss limit hit"
            elif book is None:
                gate = "no option data stored for this session"
            elif expiry is None:
                gate = "no tradable expiry in this session's data"
            if gate:
                record["outcome"] = "skipped"
                record["skip_reason"] = gate
                skips[gate] = skips.get(gate, 0) + 1
                signals.append(record)
                continue

            sig = Signal(date=day, minute=minute, side=strat.side, action="BUY",
                         moneyness=cfg.selection.moneyness, lots=cfg.risk.lots, tag=strat.name)
            res = simulate(book, sig, cfg, expiry, lot, costs)
            if res is None or res.get("skipped"):
                why = (res or {}).get("skipped", "could not be filled")
                record["outcome"] = "skipped"
                record["skip_reason"] = why
                skips[why] = skips.get(why, 0) + 1
                signals.append(record)
                continue

            taken += 1
            day_pnl += res["pnl"]
            equity += res["pnl"]
            peak = max(peak, equity)
            consecutive_losses = consecutive_losses + 1 if res["pnl"] < 0 else 0
            busy_until = res["exit_minute"]
            cooldown_until = res["exit_minute"] + cfg.risk.cooldown_min
            trade = {**record, **res, "outcome": "traded", "trade_id": len(trades) + 1,
                     "equity": round(equity, 2), "drawdown": round(equity - peak, 2)}
            trades.append(trade)
            record["outcome"] = "traded"
            record["trade_id"] = trade["trade_id"]
            signals.append(record)

    say(f"done · {len(trades)} trades from {len(signals)} signals")
    return {"status": "ok", "trades": trades, "signals": signals, "skips": skips,
            "sessions": len(days), "bars": int(len(frame)),
            "first_session": str(days[0]) if days else None,
            "last_session": str(days[-1]) if days else None}


def _snapshot(row: pd.Series) -> dict:
    """The indicator values behind a signal, as they stood on the decision bar."""
    keys = ["close", "ema_fast", "ema_slow", "ema_trend", "vwap", "rsi", "atr", "bb_width",
            "bb_width_avg", "vol_ratio", "or_high", "or_low", "prev_high", "prev_low",
            "day_high_so_far", "day_low_so_far", "gap_pts", "vix", "basis"]
    out = {}
    for k in keys:
        v = row.get(k)
        try:
            f = float(v)
            out[k] = round(f, 2) if np.isfinite(f) else None
        except (TypeError, ValueError):
            out[k] = None
    return out
