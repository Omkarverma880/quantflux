"""
Vectorised Black-Scholes implied volatility for whole columns of option bars.

``research.black_scholes.implied_vol`` prices one option at a time — fine for a live
chain of 30 contracts, far too slow for a year of uploaded minute bars. This is the
same model (European BS, continuous rate) solved by bisection on numpy arrays.

RATE matches the IV already stored in the Market Store (verified: BS at r = 10% with a
15:30 expiry reproduces it to 0.01 vol points), so computed and stored IV are comparable.
"""
from __future__ import annotations

import numpy as np

RATE = 0.10
YEAR_S = 365 * 24 * 3600


def _ncdf(x: np.ndarray) -> np.ndarray:
    # Abramowitz–Stegun 7.1.26 erf, |error| < 1.5e-7 — no scipy on the server
    z = np.abs(x) / np.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * erf)


def bs_price(S, K, T, sigma, is_call, r: float = RATE) -> np.ndarray:
    S, K, T, sigma = (np.asarray(a, float) for a in (S, K, T, sigma))
    is_call = np.asarray(is_call, bool)
    sq = sigma * np.sqrt(np.maximum(T, 1e-12))
    d1 = (np.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    disc = K * np.exp(-r * T)
    call = S * _ncdf(d1) - disc * _ncdf(d2)
    put = disc * _ncdf(-d2) - S * _ncdf(-d1)
    return np.where(is_call, call, put)


def implied_vol(price, S, K, T, is_call, r: float = RATE, iters: int = 48) -> np.ndarray:
    """Annualised IV in VOL POINTS (e.g. 12.5), NaN where no volatility can explain the price."""
    price, S, K, T = (np.asarray(a, float) for a in (price, S, K, T))
    is_call = np.asarray(is_call, bool)
    intrinsic = np.where(is_call, np.maximum(S - K * np.exp(-r * T), 0), np.maximum(K * np.exp(-r * T) - S, 0))
    ok = (price > 0) & (S > 0) & (K > 0) & (T > 0) & (price >= intrinsic - 0.05)
    lo = np.full(price.shape, 1e-4)
    hi = np.full(price.shape, 5.0)
    Sx, Kx, Tx = np.where(ok, S, 1.0), np.where(ok, K, 1.0), np.where(ok, T, 1.0)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        rich = bs_price(Sx, Kx, Tx, mid, is_call, r) > price
        hi = np.where(rich, mid, hi)
        lo = np.where(rich, lo, mid)
    iv = 0.5 * (lo + hi) * 100
    # pinned at the bracket edge = the price is outside what BS can produce
    return np.where(ok & (iv > 0.05) & (iv < 499), iv, np.nan)
