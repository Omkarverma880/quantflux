"""
The backtest core: bars in, trades out.

Split the same way the rest of the research packages are, and for the same
reason — the split is what keeps the run honest:

  1. ``scan`` — pure price and premium work. For each eligible session it builds
     the legs at the entry bar and walks the day forward looking for the stop,
     the target, or the time exit. It knows nothing about the account, so nothing
     about money can leak into a signal.

  2. ``account`` — the money. It takes those trades in strict date order and
     turns each into rupees at a fixed size.

Two rules that decide whether the result is real:

  * Same-bar rule. Within one minute we cannot know whether the high or the low
    came first, so the leg is always marked against the WORSE of the two. A stop
    that could have been hit is always taken.
  * Causality. σ, the implied-vol regime and every filter are computed from data
    strictly before the session they gate. A day never sees its own volatility.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time as dtime
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from research.index_straddle.config import Config
from research.index_straddle import optmodel as M

STOP, TARGET, EOD = "SL", "TARGET", "EOD"
SESSION_END_MIN = 930          # 15:30 in minutes from midnight


def _hhmm(s: str, fallback: dtime) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return fallback


def _minute(t: dtime) -> int:
    return t.hour * 60 + t.minute


@dataclass
class RawTrade:
    trade_date: date
    dte: int
    entry_time: str
    exit_time: str
    spot_entry: float
    spot_exit: float
    call_strike: float
    put_strike: float
    de0: float
    iv_regime: float
    gross_premium: float      # mid, both legs, at entry
    call_entry: float         # the CE leg's own premium at entry
    put_entry: float          # the PE leg's own premium at entry
    call_exit: float          # the CE leg at exit
    put_exit: float           # the PE leg at exit
    basis: float              # credit received (short) or debit paid (long)
    exit_premium: float       # what it cost to close, after spread
    ret: float                # net fraction of basis, after all friction
    exit_reason: str
    bars_held: int
    mae: float                # worst mark-to-market seen, as a fraction
    prior_range_sigma: float
    gap_sigma: float
    open_range_sigma: float

    def as_dict(self) -> dict:
        d = asdict(self)
        d["trade_date"] = str(self.trade_date)
        return d


def expiry_weekday(day: date) -> int:
    """NSE weekly expiry weekday: Thursday before Sep-2025, Tuesday after."""
    return 3 if day < date(2025, 9, 1) else 1


def calendar_dte(day: date) -> int:
    """Calendar days from ``day`` to that week's expiry."""
    return (expiry_weekday(day) - day.weekday()) % 7


# ── the day scan ─────────────────────────────────────────────────────
def scan_day(day: date, frame: pd.DataFrame, cfg: Config, *,
             iv_regime: float, sigma: float, prior_range_s: float,
             gap_s: float, legs_frames: Optional[dict] = None) -> Optional[RawTrade]:
    """One session → at most one trade. Returns None when the day is skipped."""
    dte = calendar_dte(day)
    if not (cfg.dte_min <= dte <= cfg.dte_max):
        return None
    if cfg.weekdays and day.weekday() not in cfg.weekdays:
        return None
    if not np.isfinite(iv_regime) or not np.isfinite(sigma) or sigma <= 0:
        return None

    # ── day filters, all from data strictly before this session ──
    if cfg.skip_prior_range_sigma > 0 and np.isfinite(prior_range_s):
        if prior_range_s > cfg.skip_prior_range_sigma:
            return None
    if cfg.skip_gap_sigma > 0 and np.isfinite(gap_s):
        if abs(gap_s) > cfg.skip_gap_sigma:
            return None

    t_in = _hhmm(cfg.entry_time, dtime(10, 0))
    t_out = _hhmm(cfg.exit_time, dtime(15, 20))
    times = frame.index.time
    ent_rows = np.where(times >= t_in)[0]
    if len(ent_rows) == 0:
        return None
    i = int(ent_rows[0])
    out_rows = np.where(times <= t_out)[0]
    if len(out_rows) == 0:
        return None
    e = int(out_rows[-1])
    if e - i < 5:
        return None

    # opening-range filter measured up to the entry bar
    o_hi = float(frame["high"].values[:i + 1].max())
    o_lo = float(frame["low"].values[:i + 1].min())
    open_range_s = (o_hi - o_lo) / sigma if sigma > 0 else np.nan
    if cfg.skip_open_range_sigma > 0 and np.isfinite(open_range_s):
        if open_range_s > cfg.skip_open_range_sigma:
            return None

    hi = frame["high"].values.astype(float)
    lo = frame["low"].values.astype(float)
    cl = frame["close"].values.astype(float)
    spot0 = float(cl[i])
    step = 50 if cfg.index == "NIFTY" else 100
    atm = round(spot0 / step) * step
    entry_min = _minute(frame.index[i].time())
    de0 = M.effective_dte(dte, entry_min, SESSION_END_MIN)

    # Strike selection. "atm_offset" is a fixed distance; "premium" instead picks
    # the strike whose premium prices nearest a target, so the choice tracks
    # volatility rather than a fixed number of points.
    if cfg.strike_mode == "premium" and cfg.structure != "strangle":
        from research.index_straddle.options import strike_nearest_premium
        kc = strike_nearest_premium(cfg, spot0, de0, iv_regime, cfg.target_premium, True)
        kp = strike_nearest_premium(cfg, spot0, de0, iv_regime, cfg.target_premium, False)
    else:
        kc, kp = cfg.legs(atm, step)

    # Pricing: either the calibrated model (spot + de -> premium), or the two
    # contracts' own candles. With real candles the bar's own high/low IS the
    # extreme, so the same-bar rule is applied on the legs directly.
    real = None
    if legs_frames:
        c_df, p_df = legs_frames.get("call"), legs_frames.get("put")
        if c_df is None or p_df is None or c_df.empty or p_df.empty:
            return None
        real = (c_df.reindex(frame.index, method="ffill"),
                p_df.reindex(frame.index, method="ffill"))
        if real[0]["close"].isna().all() or real[1]["close"].isna().all():
            return None

    def comb(s: float, d: float) -> float:
        return (M.premium(s, kc, d, iv_regime, True)
                + M.premium(s, kp, d, iv_regime, False))

    def comb_real(j: int, which: str) -> float:
        c, p = real
        cv, pv = c[which].iloc[j], p[which].iloc[j]
        if not (np.isfinite(cv) and np.isfinite(pv)):
            return float("nan")
        return float(cv) + float(pv)

    if real:
        ce0 = float(real[0]["close"].iloc[i]); pe0 = float(real[1]["close"].iloc[i])
    else:
        ce0 = M.premium(spot0, kc, de0, iv_regime, True)
        pe0 = M.premium(spot0, kp, de0, iv_regime, False)
    gross = comb_real(i, "close") if real else (ce0 + pe0)
    if real and not np.isfinite(gross):
        return None
    if gross < 1e-6:
        return None

    hs = cfg.costs.half_spread_pct / 100.0
    bk = cfg.costs.brokerage_pct / 100.0
    short = cfg.is_short
    # you sell at the bid and buy at the ask
    basis = gross * (1.0 - hs) if short else gross * (1.0 + hs)

    stop_f = cfg.stop_pct / 100.0
    tgt_f = cfg.target_pct / 100.0
    mae = 0.0
    reason, ret, held, exit_prem = EOD, 0.0, e - i, 0.0
    ce1 = pe1 = 0.0

    for j in range(i + 1, e + 1):
        mins = _minute(frame.index[j].time())
        de = M.effective_dte(dte, mins, SESSION_END_MIN)
        if real:
            # both legs at their own bar extremes: the conservative bound
            p_hi = comb_real(j, "high")
            p_lo = comb_real(j, "low")
            if not (np.isfinite(p_hi) and np.isfinite(p_lo)):
                continue
        else:
            p_hi = comb(hi[j], de)
            p_lo = comb(lo[j], de)
        # same-bar rule: mark against the worse extreme for this direction
        adverse = max(p_hi, p_lo) if short else min(p_hi, p_lo)
        favour = min(p_hi, p_lo) if short else max(p_hi, p_lo)

        if short:
            mtm_bad = (basis - adverse * (1.0 + hs)) / basis - bk
            mtm_good = (basis - favour * (1.0 + hs)) / basis - bk
        else:
            mtm_bad = adverse * (1.0 - hs) / basis - 1.0 - bk
            mtm_good = favour * (1.0 - hs) / basis - 1.0 - bk

        if mtm_bad < mae:
            mae = mtm_bad
        if mtm_bad <= -stop_f:
            reason, ret, held = STOP, -stop_f - bk, j - i
            exit_prem = adverse * (1.0 + hs) if short else adverse * (1.0 - hs)
            if real:
                ce1 = float(real[0]["high" if short else "low"].iloc[j])
                pe1 = float(real[1]["high" if short else "low"].iloc[j])
            else:
                sx = hi[j] if (p_hi >= p_lo) == short else lo[j]
                ce1 = M.premium(sx, kc, de, iv_regime, True)
                pe1 = M.premium(sx, kp, de, iv_regime, False)
            break
        if tgt_f > 0 and mtm_good >= tgt_f:
            reason, ret, held = TARGET, tgt_f - bk, j - i
            exit_prem = favour * (1.0 + hs) if short else favour * (1.0 - hs)
            if real:
                ce1 = float(real[0]["low" if short else "high"].iloc[j])
                pe1 = float(real[1]["low" if short else "high"].iloc[j])
            else:
                sx = lo[j] if (p_hi >= p_lo) == short else hi[j]
                ce1 = M.premium(sx, kc, de, iv_regime, True)
                pe1 = M.premium(sx, kp, de, iv_regime, False)
            break
    else:
        mins = _minute(frame.index[e].time())
        de = M.effective_dte(dte, mins, SESSION_END_MIN)
        if real:
            ce1 = float(real[0]["close"].iloc[e]); pe1 = float(real[1]["close"].iloc[e])
            close_p = ce1 + pe1
        else:
            ce1 = M.premium(float(cl[e]), kc, de, iv_regime, True)
            pe1 = M.premium(float(cl[e]), kp, de, iv_regime, False)
            close_p = ce1 + pe1
        if not np.isfinite(close_p):
            return None
        if short:
            exit_prem = close_p * (1.0 + hs)
            ret = (basis - exit_prem) / basis - bk
        else:
            exit_prem = close_p * (1.0 - hs)
            ret = exit_prem / basis - 1.0 - bk

    return RawTrade(
        trade_date=day, dte=dte,
        entry_time=frame.index[i].strftime("%H:%M"),
        exit_time=frame.index[min(i + held, e)].strftime("%H:%M"),
        spot_entry=round(spot0, 2), spot_exit=round(float(cl[e]), 2),
        call_strike=float(kc), put_strike=float(kp),
        de0=round(de0, 4), iv_regime=round(float(iv_regime), 5),
        gross_premium=round(gross, 2),
        call_entry=round(ce0, 2), put_entry=round(pe0, 2),
        call_exit=round(ce1, 2), put_exit=round(pe1, 2),
        basis=round(basis, 2),
        exit_premium=round(exit_prem, 2), ret=float(ret),
        exit_reason=reason, bars_held=int(held), mae=float(mae),
        prior_range_sigma=float(prior_range_s) if np.isfinite(prior_range_s) else 0.0,
        gap_sigma=float(gap_s) if np.isfinite(gap_s) else 0.0,
        open_range_sigma=float(open_range_s) if np.isfinite(open_range_s) else 0.0,
    )


def scan(df: pd.DataFrame, cfg: Config, legs_for_day=None) -> list[RawTrade]:
    """Walk every session in ``df``. ``premium_fn_for(day)`` may supply a real
    premium function for that date (broker source); None falls back to the model."""
    if df.empty:
        return []
    ivs = M.iv_regime_series(df, vrp=cfg.vrp)
    sigs = M.daily_sigma_series(df)
    prs = M.prior_day_range_sigma(df)
    gps = M.gap_sigma(df)

    out: list[RawTrade] = []
    for d, day in df.groupby(df.index.date, sort=True):
        key = pd.Timestamp(d)
        t = scan_day(
            d, day, cfg,
            iv_regime=float(ivs.get(key, np.nan)),
            sigma=float(sigs.get(key, np.nan)),
            prior_range_s=float(prs.get(key, np.nan)),
            gap_s=float(gps.get(key, np.nan)),
            legs_frames=(legs_for_day(d, day) if legs_for_day else None),
        )
        if t is not None:
            out.append(t)
    return out


def _story(t: RawTrade, cfg: Config, qty: int, pnl: float) -> str:
    """One sentence a human can check against their own broker statement."""
    act = "Sold" if cfg.is_short else "Bought"
    got = "collected" if cfg.is_short else "paid"
    strikes = (f"{int(t.call_strike)} CE @ Rs{t.call_entry:.2f} and "
               f"{int(t.put_strike)} PE @ Rs{t.put_entry:.2f}")
    head = (f"{t.entry_time}: NIFTY at {t.spot_entry:.0f}. {act} {strikes} "
            f"({qty} qty each) - {got} Rs{t.basis * qty:,.0f} net of the spread.")
    if t.exit_reason == STOP:
        why = (f"{t.exit_time}: the two legs together moved {cfg.stop_pct:.0f}% "
               f"{'against' if cfg.is_short else 'below'} entry, so the stop closed both.")
    elif t.exit_reason == TARGET:
        why = f"{t.exit_time}: combined premium hit the {cfg.target_pct:.0f}% target, closed both."
    else:
        why = (f"{t.exit_time}: square-off time, closed both legs at "
               f"Rs{t.call_exit:.2f} / Rs{t.put_exit:.2f}.")
    tail = (f" Bought them back for Rs{t.exit_premium * qty:,.0f}" if cfg.is_short
            else f" Sold them for Rs{t.exit_premium * qty:,.0f}")
    res = f" Result: {'profit' if pnl >= 0 else 'loss'} of Rs{abs(pnl):,.0f}."
    return head + " " + why + tail + "." + res


# ── the money ────────────────────────────────────────────────────────
def account(raws: Iterable[RawTrade], cfg: Config) -> list[dict]:
    """Turn signals into rupees at a fixed size, in strict date order."""
    qty = cfg.qty
    equity = cfg.starting_capital
    peak = equity
    rows: list[dict] = []
    for t in sorted(raws, key=lambda r: r.trade_date):
        # ret is a fraction of the cash basis (credit received / debit paid)
        pnl = t.ret * t.basis * qty
        equity += pnl
        peak = max(peak, equity)
        dd = equity - peak
        d = t.as_dict()
        # Capital actually tied up: a SHORT straddle blocks margin; a LONG one
        # costs the debit and nothing else.
        if cfg.is_short:
            capital_used = cfg.margin_per_lot * cfg.lots
        else:
            capital_used = t.basis * qty
        act = "SELL" if cfg.is_short else "BUY"
        d.update({
            "qty": qty, "lots": cfg.lots,
            "action": act,
            "leg_summary": (f"{act} {int(t.call_strike)} CE + "
                            f"{act} {int(t.put_strike)} PE"),
            "call_action": f"{act} CE {int(t.call_strike)}",
            "put_action": f"{act} PE {int(t.put_strike)}",
            "capital_used": round(capital_used, 2),
            "return_on_capital_pct": round(t.ret * t.basis * qty / capital_used * 100, 3)
                                     if capital_used else 0.0,
            "credit_or_debit": "credit received" if cfg.is_short else "debit paid",
            "basis_value": round(t.basis * qty, 2),
            "exit_value": round(t.exit_premium * qty, 2),
            "call_entry_value": round(t.call_entry * qty, 2),
            "put_entry_value": round(t.put_entry * qty, 2),
            "pnl": round(pnl, 2),
            "return_pct": round(t.ret * 100.0, 4),
            "equity": round(equity, 2),
            "story": _story(t, cfg, qty, pnl),
            "drawdown": round(dd, 2),
            "drawdown_pct": round(dd / peak * 100.0 if peak else 0.0, 4),
            "date": str(t.trade_date),
        })
        rows.append(d)
    return rows


def run(df: pd.DataFrame, cfg: Config, legs_for_day=None) -> dict:
    raws = scan(df, cfg, legs_for_day)
    trades = account(raws, cfg)
    return {"raw": raws, "trades": trades}
