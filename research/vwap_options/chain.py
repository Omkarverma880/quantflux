"""
NIFTY option contract resolution for the VWAP engine.

Wraps the existing ``research.option_chain.OptionChain`` (instrument dump,
expiries, contract lookup) and adds the piece a multi-day backtest needs:
**ATM-relative strike offsets**. A fixed strike number cannot travel across a
date range because ATM moves; an offset in strike-steps resolves to the correct
absolute strike for each signal date.

Resolution failures return a reason string rather than None-with-no-explanation,
so the backtest can log exactly why a signal produced no trade.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from core.logger import get_logger
from research.option_chain import OptionChain
from research.vwap_options.config import LADDER_STEPS, STRIKE_STEP

logger = get_logger("research.vwap_options.chain")


def atm_strike(spot: float, step: int = STRIKE_STEP) -> Optional[float]:
    if not spot or spot <= 0:
        return None
    return float(round(spot / step) * step)


def strike_for_offset(spot: float, offset_steps: int, step: int = STRIKE_STEP) -> Optional[float]:
    """ATM + (offset × step). offset 0 = ATM, +2 = ATM+100, -2 = ATM-100."""
    atm = atm_strike(spot, step)
    return None if atm is None else atm + int(offset_steps) * step


def ladder(spot: float, steps: int = LADDER_STEPS, step: int = STRIKE_STEP) -> list[dict]:
    """The 50-strike selection ladder, expressed as ATM-relative offsets so a
    choice stays meaningful on every date of a range."""
    atm = atm_strike(spot, step)
    if atm is None:
        return []
    out = []
    for i in range(-steps, steps + 1):
        if i == 0:
            label = "ATM"
        else:
            label = f"ATM{'+' if i > 0 else '-'}{abs(i) * step}"
        out.append({"offset_steps": i, "strike": atm + i * step, "label": label,
                    "moneyness": "ATM" if i == 0 else ("OTM_CE/ITM_PE" if i > 0 else "ITM_CE/OTM_PE")})
    return out


class NiftyChain:
    """Thin, cached facade over OptionChain for the VWAP engine."""

    def __init__(self, broker):
        self.chain = OptionChain(broker)
        self.broker = broker

    # ── expiry ──
    def expiry_for(self, expiry_type: str, day: date, min_days: int = 0) -> Optional[date]:
        """Nearest expiry on/after ``day``; rolls forward when it is closer than
        ``min_days`` so a trade is not opened into an expiry that is too near."""
        exp = self.chain._expiry_for(expiry_type, day)
        if exp is None:
            return None
        if min_days > 0 and (exp - day).days < min_days:
            nxt = self.chain._expiry_for(expiry_type, exp + timedelta(days=1))
            return nxt or exp
        return exp

    def expiries(self) -> list[date]:
        return self.chain._expiries()

    def spot(self) -> float:
        return self.chain._spot()

    # ── contract ──
    def resolve(self, spot: float, offset_steps: int, opt_type: str,
                expiry_type: str, day: date, min_days: int = 0) -> tuple[Optional[dict], Optional[str]]:
        """(contract, reason). ``contract`` = {tradingsymbol, token, strike, type, expiry}.

        A ``None`` contract always comes with a reason — most often that Kite lists
        only currently tradable instruments, so an already-expired contract cannot
        be resolved (the caller may then fall back to modelled pricing).
        """
        strike = strike_for_offset(spot, offset_steps)
        if strike is None:
            return None, "no spot price to derive ATM"
        expiry = self.expiry_for(expiry_type, day, min_days)
        if expiry is None:
            return None, "no expiry available in the instrument dump"
        opt = self.chain._resolve(expiry, strike, opt_type)
        if not opt:
            return None, (f"{opt_type} {int(strike)} exp {expiry} not listed "
                          "(expired contracts are absent from the instrument dump)")
        return opt, None

    def synthetic_contract(self, spot: float, offset_steps: int, opt_type: str,
                           expiry_type: str, day: date, min_days: int = 0) -> Optional[dict]:
        """A contract descriptor for MODELLED pricing when no real instrument
        exists. Carries no token — pricing must come from Black-Scholes."""
        strike = strike_for_offset(spot, offset_steps)
        if strike is None:
            return None
        expiry = self.expiry_for(expiry_type, day, min_days) or _synthetic_expiry(expiry_type, day)
        if expiry is None:
            return None
        return {"tradingsymbol": f"NIFTY{expiry:%y%b}{int(strike)}{opt_type}".upper(),
                "token": None, "strike": float(strike), "type": opt_type, "expiry": expiry,
                "synthetic": True}


def _synthetic_expiry(expiry_type: str, day: date) -> date:
    """Derive a plausible expiry when the dump has none for a historical date:
    weekly = next Thursday, monthly = last Thursday of the month."""
    if expiry_type == "monthly":
        nxt = date(day.year + (day.month // 12), (day.month % 12) + 1, 1)
        last = nxt - timedelta(days=1)
        while last.weekday() != 3:                 # Thursday
            last -= timedelta(days=1)
        return last if last >= day else _synthetic_expiry("monthly", nxt)
    d = day
    while d.weekday() != 3:
        d += timedelta(days=1)
    return d
