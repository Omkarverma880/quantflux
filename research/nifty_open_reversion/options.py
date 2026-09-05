"""
The options overlay (§39).

The signal is always generated on the INDEX. This module decides what to trade
when the instrument mode is not spot:

    option_buy   BUY signal  → BUY CALL        SELL signal → BUY PUT
    option_sell  BUY signal  → SELL PUT        SELL signal → SELL CALL

Strike is ATM plus a configurable offset in the out-of-the-money direction
(0 = ATM, 50, 100 …). P&L is real premium in and premium out — never a
delta approximation — so an option run needs the contract's own candles and is
therefore bounded by whatever option history the broker actually serves.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from core.logger import get_logger
from research.nifty_open_reversion.config import Config

logger = get_logger("research.nifty_open_reversion.options")

STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25}


def map_signal(side: str, mode: str) -> tuple[str, str]:
    """(option type, action) for a spot signal."""
    if mode == "option_buy":
        return ("CE", "BUY") if side == "BUY" else ("PE", "BUY")
    if mode == "option_sell":
        return ("PE", "SELL") if side == "BUY" else ("CE", "SELL")
    raise ValueError(f"not an option mode: {mode}")


def atm_strike(spot: float, step: int) -> float:
    return round(spot / step) * step


def strike_for(spot: float, opt_type: str, offset: int, step: int) -> float:
    """Strike ``offset`` points from ATM, signed: negative = ITM, positive = OTM.

    A call goes out-of-the-money as the strike rises, a put as it falls — so the
    same signed offset means the same moneyness for both.
    """
    atm = atm_strike(spot, step)
    if not offset:
        return atm
    return atm + offset if opt_type == "CE" else atm - offset


def moneyness(offset: int) -> str:
    if offset == 0:
        return "ATM"
    return f"{abs(offset)} {'OTM' if offset > 0 else 'ITM'}"


def describe(cfg: Config, side: str, spot: float, index: str = "NIFTY") -> dict:
    opt_type, action = map_signal(side, cfg.instrument_mode)
    step = STRIKE_STEP.get(index, 50)
    return {"opt_type": opt_type, "action": action,
            "strike": strike_for(spot, opt_type, cfg.strike_offset, step),
            "atm": atm_strike(spot, step), "step": step,
            "moneyness": moneyness(cfg.strike_offset)}


class OptionResolver:
    """Finds the contract for a given day and serves its candles, cached."""

    def __init__(self, broker, universe, index: str = "NIFTY"):
        self.broker = broker
        self.universe = universe
        self.index = index
        self._contracts: dict = {}
        self._candles: dict = {}

    def contract(self, day: date, opt_type: str, strike: float, expiry_type: str) -> Optional[dict]:
        key = (day, opt_type, strike, expiry_type)
        if key in self._contracts:
            return self._contracts[key]
        rec = None
        try:
            exp = self.universe.expiry_for(self.index, expiry_type, day)
            if exp:
                rec = self.universe.resolve(self.index, exp, strike, opt_type)
        except Exception as exc:
            logger.debug("option resolve failed (%s %s %s): %s", day, strike, opt_type, exc)
        self._contracts[key] = rec
        return rec

    def candles(self, token: int, day: date) -> pd.DataFrame:
        key = (token, day)
        if key in self._candles:
            return self._candles[key]
        frm = datetime.combine(day, datetime.min.time().replace(hour=9, minute=15))
        to = datetime.combine(day, datetime.min.time().replace(hour=15, minute=30))
        try:
            raw = self.broker.get_historical_data(token, frm, to, "minute") or []
        except Exception as exc:
            logger.debug("option candles failed (%s %s): %s", token, day, exc)
            raw = []
        if not raw:
            df = pd.DataFrame()
        else:
            df = pd.DataFrame(raw)
            df["_ts"] = pd.to_datetime(df["date"], utc=True, format="mixed") \
                          .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            df = df.set_index("_ts").sort_index()[["open", "high", "low", "close"]].astype(float)
        self._candles[key] = df
        return df


def price_at(df: pd.DataFrame, when: datetime, field: str = "open") -> Optional[float]:
    """The contract's price at (or immediately after) a timestamp."""
    if df is None or df.empty:
        return None
    at = df[df.index >= when]
    if at.empty:
        return float(df.iloc[-1]["close"])
    return float(at.iloc[0][field])


def apply(raws: list, cfg: Config, resolver: OptionResolver) -> tuple[list, list]:
    """Convert underlying signals into option round trips.

    The stop and target still come from the UNDERLYING by default (that is the
    strategy), so the option is exited at the timestamp the index rule fired.
    Returns (option_trades, skipped) where a skip carries its reason.
    """
    out, skipped = [], []
    for t in raws:
        try:
            opt_type, action = map_signal(t.side, cfg.instrument_mode)
            step = STRIKE_STEP.get("NIFTY", 50)
            strike = strike_for(t.daily_open, opt_type, cfg.strike_offset, step)
            rec = resolver.contract(t.day, opt_type, strike, cfg.expiry_type)
            if not rec:
                skipped.append({"day": t.day.isoformat(), "side": t.side,
                                "reason": f"no {int(strike)} {opt_type} contract"})
                continue
            df = resolver.candles(rec["token"], t.day)
            if df.empty:
                skipped.append({"day": t.day.isoformat(), "side": t.side,
                                "reason": f"no candles for {rec['tradingsymbol']}"})
                continue
            entry = price_at(df, t.entry_time, "open")
            exit_ = price_at(df, t.exit_time, "close")
            if not entry or not exit_:
                skipped.append({"day": t.day.isoformat(), "side": t.side,
                                "reason": "premium unavailable at the signal time"})
                continue
            pts = (exit_ - entry) if action == "BUY" else (entry - exit_)
            out.append({
                "day": t.day, "side": t.side, "action": action, "opt_type": opt_type,
                "strike": strike, "moneyness": moneyness(cfg.strike_offset),
                "tradingsymbol": rec["tradingsymbol"],
                "expiry": rec["expiry"].isoformat() if rec.get("expiry") else "",
                "entry_time": t.entry_time, "exit_time": t.exit_time,
                "entry_premium": round(entry, 2), "exit_premium": round(exit_, 2),
                "points": round(float(pts), 2), "underlying_points": t.points,
                "index_entry": t.entry_price, "index_exit": t.exit_price,
                "index_sl": t.stop_loss, "index_target": t.target,
                "exit_reason": t.exit_reason, "daily_open": t.daily_open,
            })
        except Exception as exc:
            skipped.append({"day": t.day.isoformat(), "side": t.side, "reason": str(exc)[:120]})
    return out, skipped
