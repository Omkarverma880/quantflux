"""
Pure calculation engine for the NIFTY Signal Generator.

Every function here is side-effect free and independently testable — no broker
calls, no I/O, no global state. The service layer feeds these functions raw
numbers fetched from the existing market-data services and assembles the rows.

Keeping the maths isolated makes it trivial to add future research metrics
(Change-in-OI, Max Pain, Delta/Gamma exposure, …) without disturbing the data
plumbing.
"""
from __future__ import annotations

from typing import Optional

from research.nifty_signal_generator.constants import (
    SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NEUTRAL, SIGNAL_COLORS,
)


# ── Strike selection ──────────────────────────────────────────────

def get_atm_strike(ltp: float, interval: int) -> int:
    """Nearest strike to ``ltp`` on the ``interval`` grid (e.g. 25136 → 25150)."""
    if interval <= 0:
        raise ValueError("strike interval must be positive")
    return int(round(ltp / interval) * interval)


def generate_selected_strikes(atm: int, interval: int, count: int) -> list[int]:
    """ATM plus ``count`` strikes above and below → (count*2)+1 strikes.

    The identical list is used for both CE and PE (spec requirement).
    """
    if interval <= 0:
        raise ValueError("strike interval must be positive")
    if count < 1:
        raise ValueError("strike count must be >= 1")
    return [atm + i * interval for i in range(-count, count + 1)]


# ── Open-interest sums ────────────────────────────────────────────

def calculate_call_sum(oi_by_strike: dict[int, Optional[float]], strikes: list[int]) -> Optional[int]:
    """Sum CE OI across ``strikes``. Returns None if no strike had usable OI."""
    return _sum_oi(oi_by_strike, strikes)


def calculate_put_sum(oi_by_strike: dict[int, Optional[float]], strikes: list[int]) -> Optional[int]:
    """Sum PE OI across ``strikes``. Returns None if no strike had usable OI."""
    return _sum_oi(oi_by_strike, strikes)


def _sum_oi(oi_by_strike: dict[int, Optional[float]], strikes: list[int]) -> Optional[int]:
    total = 0.0
    seen = False
    for s in strikes:
        v = oi_by_strike.get(s)
        if v is None:
            continue
        seen = True
        total += float(v)
    return int(round(total)) if seen else None


def calculate_difference(put_oi: Optional[int], call_oi: Optional[int]) -> Optional[int]:
    """Diff = Put OI − Call OI. Positive → bullish, negative → bearish."""
    if put_oi is None or call_oi is None:
        return None
    return int(put_oi - call_oi)


def calculate_pcr(put_oi: Optional[int], call_oi: Optional[int]) -> Optional[float]:
    """PCR = Put OI / Call OI, rounded to 2dp. None when call OI is missing/0."""
    if put_oi is None or not call_oi:
        return None
    return round(put_oi / call_oi, 2)


# ── Signals ───────────────────────────────────────────────────────

def _signal(is_buy: bool, is_sell: bool) -> str:
    if is_buy:
        return SIGNAL_BUY
    if is_sell:
        return SIGNAL_SELL
    return SIGNAL_NEUTRAL


def generate_option_signal(pcr: Optional[float]) -> Optional[str]:
    """PCR > 1 → BUY, < 1 → SELL, == 1 → NEUTRAL. None when PCR unavailable."""
    if pcr is None:
        return None
    return _signal(pcr > 1, pcr < 1)


def generate_vwap_signal(ltp: Optional[float], vwap: Optional[float]) -> Optional[str]:
    """LTP > VWAP → BUY, < VWAP → SELL, == VWAP → NEUTRAL."""
    if ltp is None or vwap is None:
        return None
    return _signal(ltp > vwap, ltp < vwap)


def signal_color(signal: Optional[str]) -> Optional[str]:
    return SIGNAL_COLORS.get(signal) if signal else None


# ── VWAP (session-anchored, equal-weighted typical price) ─────────
# The NIFTY index carries no traded volume in historical candles, so VWAP is
# the cumulative mean of typical prices ((H+L+C)/3) — identical to the basis
# used by the existing VWAP research module. A volume-bearing source (futures)
# can be swapped in later without changing callers.

def typical_price(candle: dict) -> float:
    return (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3.0


def cumulative_vwap(typical_sum: float, count: int) -> Optional[float]:
    if count <= 0:
        return None
    return round(typical_sum / count, 2)
