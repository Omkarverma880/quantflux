"""
The calibrated option premium model.

This is the pricer the research ran on, reproduced exactly. It exists because a
backtest over 4.7 years of index history cannot use real option candles — no
broker serves that much per-strike option history — so the premium has to be
modelled. The model was fitted to a real NIFTY chain and then checked against it:

    calibration day   2026-09-11, 32 contracts, two expiries, 12,032 rows
    correlation       r = 0.988 vs traded premiums
    mean abs error    8.4 index points
    residual bias     about −6% on calls, +7% on puts (that day's call skew);
                      symmetric, so it largely cancels for a two-legged book

Time-value law. ATM premium as a fraction of spot follows a power law in ``de``,
the effective days to expiry = calendar days + the fraction of the session left:

    prem_frac(de) = A · de^B · (IV_regime / IV_BASE)

A = 0.0050 anchors the expiry-day open (0.50% of spot, the standard NIFTY 0-DTE
straddle level) and B = 0.372 was fitted from the measured 0.91% at de = 5.0.
B < 0.5 encodes the documented sub-square-root decay of index time value — a
pure calendar clock overstates weekend and overnight decay.

IMPORTANT — the honest limit. The calibration day was 4 DTE. The 0-DTE anchor is
brought in from outside the sample. Break-even for the short straddle sits at
IV/realized ≈ 1.10 against the 1.30 used here, which in premium terms means the
0-DTE ATM straddle at 09:35 must be worth more than ~0.42% of spot. Verify that
against a live chain before trusting the rupee figures.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

A_FRAC = 0.0050       # ATM premium / spot at de = 1.0, in the IV_BASE regime
B_POW = 0.372         # time-value decay exponent (fitted; < 0.5)
IV_BASE = 0.106       # the IV regime the anchors were measured in
STRADDLE_K = 0.79788  # 2·φ(0): ATM straddle = K · S · IV · √T
SESSION_MINUTES = 375.0
TRADING_DAYS = 252.0


def _ncdf(x: float) -> float:
    """Normal CDF via erf — exact to double precision, no table."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def premium(spot: float, strike: float, de: float, iv_regime: float,
            is_call: bool) -> float:
    """One option's premium.

    de         effective days to expiry (calendar days + fraction of session left)
    iv_regime  the regime implied vol as a decimal, e.g. 0.11 for 11%
    """
    if de <= 1e-6:
        return max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    if spot <= 0 or strike <= 0:
        return 0.0
    frac = A_FRAC * (de ** B_POW) * (iv_regime / IV_BASE)
    T = de / 365.0
    sq = math.sqrt(T)
    iv = max(frac / (STRADDLE_K * sq), 1e-4)
    v = iv * sq
    d1 = (math.log(spot / strike) + 0.5 * v * v) / v
    d2 = d1 - v
    if is_call:
        return spot * _ncdf(d1) - strike * _ncdf(d2)
    return strike * _ncdf(-d2) - spot * _ncdf(-d1)


def straddle_premium(spot: float, call_strike: float, put_strike: float,
                     de: float, iv_regime: float) -> float:
    """Combined premium of both legs."""
    return (premium(spot, call_strike, de, iv_regime, True)
            + premium(spot, put_strike, de, iv_regime, False))


def effective_dte(calendar_dte: int, minute_of_day: int,
                  session_end_min: int = 930) -> float:
    """de = calendar days to expiry + the fraction of today's session remaining.

    Validated against the calibration chain: on 2026-09-11 (Friday, expiry the
    following Tuesday) this gives de = 5.0 at the open, which is the value that
    reproduced the traded premiums.
    """
    left = max(0.0, (session_end_min - minute_of_day) / SESSION_MINUTES)
    return max(float(calendar_dte) + left, 1e-6)


def realized_vol_series(df: pd.DataFrame) -> pd.Series:
    """Annualised realized vol per session, from 1-minute closes."""
    close = df["close"]
    lr = np.log(close).diff()
    per_day = lr.groupby(df.index.date).std() * math.sqrt(SESSION_MINUTES * TRADING_DAYS)
    out = pd.Series(per_day.values, index=pd.to_datetime(list(per_day.index)))
    return out * 100.0


def iv_regime_series(df: pd.DataFrame, vrp: float = 1.30,
                     span: int = 20) -> pd.Series:
    """Regime implied vol per session: vrp × EWMA(realized vol), strictly causal.

    Shifted one day before the EWMA so a session never sees its own volatility.
    """
    rv = realized_vol_series(df)
    ew = rv.shift(1).ewm(span=span, min_periods=5).mean()
    return (vrp * ew / 100.0).clip(lower=0.06, upper=0.60)


def daily_sigma_series(df: pd.DataFrame, span: int = 20) -> pd.Series:
    """σ = EWMA of the daily true range, shifted — the unit every filter uses."""
    g = df.groupby(df.index.date)
    d = pd.DataFrame({"H": g["high"].max(), "L": g["low"].min(), "C": g["close"].last()})
    d.index = pd.to_datetime(list(d.index))
    prev = d["C"].shift()
    tr = pd.concat([d["H"] - d["L"], (d["H"] - prev).abs(), (d["L"] - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(span=span, min_periods=5).mean().shift(1)


def prior_day_range_sigma(df: pd.DataFrame) -> pd.Series:
    """Yesterday's range measured in σ — the filter that earned its place."""
    g = df.groupby(df.index.date)
    d = pd.DataFrame({"H": g["high"].max(), "L": g["low"].min()})
    d.index = pd.to_datetime(list(d.index))
    sig = daily_sigma_series(df)
    return ((d["H"] - d["L"]).shift(1) / sig).replace([np.inf, -np.inf], np.nan)


def gap_sigma(df: pd.DataFrame) -> pd.Series:
    """Overnight gap in σ."""
    g = df.groupby(df.index.date)
    d = pd.DataFrame({"O": g["open"].first(), "C": g["close"].last()})
    d.index = pd.to_datetime(list(d.index))
    sig = daily_sigma_series(df)
    return ((d["O"] - d["C"].shift()) / sig).replace([np.inf, -np.inf], np.nan)
