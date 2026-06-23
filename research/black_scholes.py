"""
Black-Scholes helpers — implied volatility + Greeks for the option-chain
research module. Kite quotes don't carry IV/Greeks, so we derive them from the
live premium, spot, strike and time-to-expiry.
"""
from __future__ import annotations

from math import log, sqrt, exp
from statistics import NormalDist
from typing import Optional

_N = NormalDist()


def _cdf(x: float) -> float:
    return _N.cdf(x)


def _pdf(x: float) -> float:
    return _N.pdf(x)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    if is_call:
        return S * _cdf(d1) - K * exp(-r * T) * _cdf(d2)
    return K * exp(-r * T) * _cdf(-d2) - S * _cdf(-d1)


def implied_vol(price: float, S: float, K: float, T: float, r: float, is_call: bool) -> Optional[float]:
    """Invert the BS price for sigma via bisection. Returns annualised vol."""
    if price <= 0 or T <= 0 or S <= 0:
        return None
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    if price < intrinsic - 0.05:
        return None
    lo, hi = 1e-4, 5.0
    mid = 0.2
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        diff = bs_price(S, K, T, r, mid, is_call) - price
        if abs(diff) < 0.01:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return mid


def greeks(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> dict:
    if not sigma or T <= 0 or S <= 0:
        return {}
    d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    delta = _cdf(d1) if is_call else _cdf(d1) - 1.0
    gamma = _pdf(d1) / (S * sigma * sqrt(T))
    vega = S * _pdf(d1) * sqrt(T) / 100.0           # per 1% vol move
    theta_core = -(S * _pdf(d1) * sigma) / (2 * sqrt(T))
    if is_call:
        theta = (theta_core - r * K * exp(-r * T) * _cdf(d2)) / 365.0
    else:
        theta = (theta_core + r * K * exp(-r * T) * _cdf(-d2)) / 365.0
    return {
        "delta": round(delta, 3),
        "gamma": round(gamma, 5),
        "vega": round(vega, 2),
        "theta": round(theta, 2),
    }
