"""
The backtest core: bars in, trades out.

Deliberately split in two, because that split is what keeps the run honest:

  1. ``scan_day`` / ``scan`` — pure price work. For each session it finds the
     09:15 open, places the two levels, and walks the bars forward looking for
     entries and then exits. It knows nothing about money, so nothing about the
     account can leak into a signal.

  2. ``account`` — the money. It takes those signals in strict entry-time order
     and sizes each one from the profit realised *before* it. A trade that
     crosses a threshold is never resized retroactively.

Same-candle rule: when one bar touches both the stop and the target we cannot
know which came first, so the stop is always taken (§9).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time as dtime
from typing import Iterable, Optional

import pandas as pd

from research.nifty_open_reversion.config import Config

BUY, SELL = "BUY", "SELL"
TARGET, STOP, EOD = "TARGET", "SL", "EOD"


def _hhmm(s: str, fallback: dtime) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return fallback


@dataclass
class RawTrade:
    """One completed round trip on the underlying, before any sizing."""
    day: date
    side: str
    daily_open: float
    entry_time: datetime
    entry_price: float
    stop_loss: float
    target: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    points: float                    # signed: positive = in favour
    exact_open: bool = True
    bars_held: int = 0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["day"] = self.day.isoformat()
        d["entry_time"] = self.entry_time.strftime("%Y-%m-%d %H:%M")
        d["exit_time"] = self.exit_time.strftime("%Y-%m-%d %H:%M")
        return d


@dataclass
class DayResult:
    day: date
    daily_open: Optional[float]
    exact_open: bool
    bars: int
    trades: list                     # list[RawTrade]
    skipped: str = ""


# ── one session ──────────────────────────────────────────────────────
def scan_day(day: date, frame: pd.DataFrame, cfg: Config) -> DayResult:
    """Walk one session and return its completed trades."""
    if frame is None or frame.empty:
        return DayResult(day, None, False, 0, [], "no bars")

    start_t = _hhmm(cfg.entry_start, dtime(9, 15))
    cutoff_t = _hhmm(cfg.entry_cutoff, dtime(10, 30))
    close_t = _hhmm(cfg.market_close, dtime(15, 30))

    frame = frame[(frame.index.time >= start_t) & (frame.index.time <= close_t)]
    if frame.empty:
        return DayResult(day, None, False, 0, [], "no in-session bars")

    exact = bool((frame.index.time == start_t).any())
    open_row = frame[frame.index.time == start_t].iloc[0] if exact else frame.iloc[0]
    daily_open = float(open_row["open"])
    if daily_open <= 0:
        return DayResult(day, None, exact, len(frame), [], "invalid open")

    off = cfg.offset_for(daily_open)
    sl_pts = cfg.sl_for(daily_open)
    tp_pts = cfg.tp_for(daily_open)

    ts = frame.index.to_pydatetime()
    highs = frame["high"].to_numpy()
    lows = frame["low"].to_numpy()
    closes = frame["close"].to_numpy()
    n = len(frame)

    trades: list[RawTrade] = []
    for side in ([BUY] if cfg.trade_buy else []) + ([SELL] if cfg.trade_sell else []):
        long_ = side == BUY
        level = daily_open - off if long_ else daily_open + off
        stop = level - sl_pts if long_ else level + sl_pts
        tgt = level + tp_pts if long_ else level - tp_pts

        taken = 0
        i = 0
        while i < n and taken < cfg.max_per_side_per_day:
            # ── look for the entry ──
            entry_i = None
            while i < n:
                if ts[i].time() > cutoff_t:
                    break                          # §4: no new entries after the cutoff
                hit = lows[i] <= level if long_ else highs[i] >= level
                if hit:
                    entry_i = i
                    break
                i += 1
            if entry_i is None:
                break

            # ── walk forward to the exit, stop before target on the same bar ──
            exit_i = exit_px = None
            reason = ""
            for j in range(entry_i, n):
                # On the ENTRY bar only the stop is eligible: the entry print and
                # a target print inside the same minute cannot be ordered from
                # OHLC, and crediting the target there would flatter the result.
                # From the next bar the §9 rule applies — stop before target.
                first_bar = j == entry_i
                if long_:
                    if lows[j] <= stop:
                        exit_i, exit_px, reason = j, stop, STOP
                        break
                    if not first_bar and highs[j] >= tgt:
                        exit_i, exit_px, reason = j, tgt, TARGET
                        break
                else:
                    if highs[j] >= stop:
                        exit_i, exit_px, reason = j, stop, STOP
                        break
                    if not first_bar and lows[j] <= tgt:
                        exit_i, exit_px, reason = j, tgt, TARGET
                        break
            if exit_i is None:                     # §10: never carry overnight
                exit_i, exit_px, reason = n - 1, float(closes[n - 1]), EOD

            pts = (exit_px - level) if long_ else (level - exit_px)
            trades.append(RawTrade(
                day=day, side=side, daily_open=round(daily_open, 2),
                entry_time=ts[entry_i], entry_price=round(level, 2),
                stop_loss=round(stop, 2), target=round(tgt, 2),
                exit_time=ts[exit_i], exit_price=round(float(exit_px), 2),
                exit_reason=reason, points=round(float(pts), 2), exact_open=exact,
                bars_held=int(exit_i - entry_i)))
            taken += 1
            i = exit_i + 1                          # a re-entry can only start after the exit

    trades.sort(key=lambda t: (t.entry_time, t.side))
    if len(trades) > cfg.max_trades_per_day:
        trades = trades[:cfg.max_trades_per_day]    # §11: chronological, then capped
    return DayResult(day, round(daily_open, 2), exact, len(frame), trades)


def scan(df: pd.DataFrame, cfg: Config) -> list[DayResult]:
    """Every session in the frame, chronologically."""
    out: list[DayResult] = []
    if df is None or df.empty:
        return out
    for d, day_frame in df.groupby(df.index.date, sort=True):
        out.append(scan_day(d, day_frame, cfg))
    return out


# ── sizing & the account ─────────────────────────────────────────────
def lots_for(cumulative_profit: float, cfg: Config) -> int:
    """§13 — thresholds on cumulative PROFIT, never on account value, and the
    size only ever ratchets up."""
    if not cfg.scale_enabled:
        return cfg.base_lots
    if cumulative_profit <= cfg.profit_threshold_2:
        lots = 1
    elif cumulative_profit <= cfg.profit_threshold_3:
        lots = 2
    elif cumulative_profit <= cfg.profit_threshold_4:
        lots = 3
    else:
        lots = 4
    return max(cfg.base_lots, min(cfg.max_lots, lots))


def trade_cost(entry: float, exit_: float, qty: int, cfg: Config) -> float:
    """Round-trip cost in rupees. Zero by default, so the base run is the pure
    point result; switch the knobs on for a realistic one."""
    c = cfg.costs
    if not c.any_on:
        return 0.0
    turnover = (abs(entry) + abs(exit_)) * qty
    brokerage = 2.0 * c.brokerage_per_order
    exch = turnover * c.exchange_pct / 100.0
    charges = (turnover * (c.stt_pct + c.sebi_pct + c.stamp_pct) / 100.0) + exch
    gst = (brokerage + exch) * c.gst_pct / 100.0
    return round(brokerage + charges + gst, 2)


def account(raws: Iterable[RawTrade], cfg: Config) -> list[dict]:
    """Sequence the trades by entry time and run the money through them."""
    rows: list[dict] = []
    equity = float(cfg.starting_capital)
    peak = equity
    lots_ratchet = cfg.base_lots

    for k, t in enumerate(sorted(raws, key=lambda x: (x.entry_time, x.side)), start=1):
        cum_before = equity - cfg.starting_capital
        lots = lots_for(cum_before, cfg)
        lots = max(lots, lots_ratchet)              # position size never decreases
        lots_ratchet = lots
        qty = lots * cfg.lot_size

        pts = t.points - 2.0 * cfg.costs.slippage_points   # a slip on each side
        gross = pts * qty
        cost = trade_cost(t.entry_price, t.exit_price, qty, cfg)
        net = gross - cost
        equity += net
        peak = max(peak, equity)
        cum = equity - cfg.starting_capital

        rows.append({
            "trade_no": k,
            "date": t.day.isoformat(),
            "year": t.day.year,
            "month": t.day.month,
            "side": t.side,
            "daily_open": t.daily_open,
            "entry_time": t.entry_time.strftime("%Y-%m-%d %H:%M"),
            "entry_price": t.entry_price,
            "stop_loss": t.stop_loss,
            "target": t.target,
            "exit_time": t.exit_time.strftime("%Y-%m-%d %H:%M"),
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "nifty_points": round(pts, 2),
            "lots": lots,
            "quantity": qty,
            "gross_pnl": round(gross, 2),
            "transaction_cost": round(cost, 2),
            "net_pnl": round(net, 2),
            "cumulative_profit": round(cum, 2),
            "equity": round(equity, 2),
            "return_pct": round(cum / cfg.starting_capital * 100.0, 4),
            "peak_equity": round(peak, 2),
            "drawdown": round(equity - peak, 2),
            "bars_held": t.bars_held,
        })
    return rows


def run(df: pd.DataFrame, cfg: Config) -> dict:
    """Full pass: scan every session, then account for the trades."""
    days = scan(df, cfg)
    raws = [t for d in days for t in d.trades]
    trades = account(raws, cfg)
    return {"days": days, "raw": raws, "trades": trades}
