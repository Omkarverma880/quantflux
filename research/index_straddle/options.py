"""
Strike selection and the real-premium (broker) source.

Two ways to price a backtest, and the difference matters enough that the UI names
it explicitly:

  ``model``   the calibrated research pricer. Works over the whole index history,
              reproduces the documented result, and is the only way to study
              4.7 years — no broker serves that much per-strike option history.

  ``broker``  the contract's own minute candles. Real traded premium, therefore
              the honest test — but bounded by whatever option history Zerodha
              actually returns, usually a few months.

Strike selection is shared by both, and by the live engine, so a backtest and a
live order pick the same contract from the same spot.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from core.logger import get_logger
from research.index_straddle.config import Config

logger = get_logger("research.index_straddle.options")

STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25}


def _as_date(v) -> Optional[date]:
    """Broker expiry fields arrive as date, datetime or string. Normalise."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(str(v)).date()
    except Exception:
        return None


def step_for(index: str) -> int:
    return STRIKE_STEP.get((index or "NIFTY").upper(), 50)


def atm_strike(spot: float, step: int) -> float:
    return round(spot / step) * step


def moneyness_label(offset: int) -> str:
    if offset == 0:
        return "ATM"
    return f"{abs(offset)} {'OTM' if offset > 0 else 'ITM'}"


def legs_for(cfg: Config, spot: float, de: float = 1.0,
             iv_regime: float = 0.12) -> dict:
    """Resolve both legs from a spot price. One place, used everywhere.

    ``de`` and ``iv_regime`` are only consulted when strike_mode is "premium";
    the defaults keep the ATM-offset path callable with a spot alone.
    """
    step = step_for(cfg.index)
    atm = atm_strike(spot, step)
    if cfg.strike_mode == "premium" and cfg.structure != "strangle":
        kc = strike_nearest_premium(cfg, spot, de, iv_regime, cfg.target_premium, True)
        kp = strike_nearest_premium(cfg, spot, de, iv_regime, cfg.target_premium, False)
    else:
        kc, kp = cfg.legs(atm, step)
    action = "SELL" if cfg.is_short else "BUY"
    if cfg.structure == "strangle":
        label = f"{cfg.wing_offset}-wide strangle"
    elif cfg.strike_mode == "premium":
        label = f"~Rs{cfg.target_premium:g} premium straddle"
    else:
        label = f"{moneyness_label(cfg.strike_offset)} straddle"
    return {
        "atm": atm, "step": step,
        "call_strike": kc, "put_strike": kp,
        "action": action, "label": label,
        "qty": cfg.qty, "lots": cfg.lots,
    }


def strike_nearest_premium(cfg: Config, spot: float, de: float, iv_regime: float,
                           target: float, is_call: bool, span: int = 20) -> float:
    """Pick the strike whose modelled premium is closest to ``target``.

    Used by ``strike_mode == "premium"`` — 'sell the 100-rupee option' rather
    than 'sell the 200-point-OTM option'. Premium is the thing the trader is
    actually choosing; the strike that delivers it moves with volatility.
    """
    from research.index_straddle import optmodel as M
    step = step_for(cfg.index)
    atm = atm_strike(spot, step)
    best, best_d = atm, float("inf")
    for i in range(-span, span + 1):
        k = atm + i * step
        if k <= 0:
            continue
        p = M.premium(spot, k, de, iv_regime, is_call)
        d = abs(p - target)
        if d < best_d:
            best, best_d = k, d
    return best


# ── real contract resolution (broker source + live) ──────────────────
def expiry_weekday(day: date) -> int:
    """NSE weekly expiry weekday: Thursday before Sep-2025, Tuesday after."""
    return 3 if day < date(2025, 9, 1) else 1


def weekly_expiry(day: date) -> date:
    return day + timedelta(days=(expiry_weekday(day) - day.weekday()) % 7)


def monthly_expiry(day: date) -> date:
    """Last weekly expiry of the month."""
    d = weekly_expiry(day)
    while True:
        nxt = d + timedelta(days=7)
        if nxt.month != d.month:
            return d
        d = nxt


def expiry_for(cfg: Config, day: date) -> date:
    return monthly_expiry(day) if cfg.expiry_type == "monthly" else weekly_expiry(day)


class ContractResolver:
    """Finds tradingsymbol + token for a strike/expiry from the broker universe.

    Wraps whatever the universe exposes so the strategy and the backtest share
    one lookup. Returns None rather than raising — a missing contract should skip
    a day, not kill a run.
    """

    def __init__(self, universe=None, index: str = "NIFTY"):
        self.universe = universe
        self.index = (index or "NIFTY").upper()
        self._cache: dict = {}

    def resolve(self, strike: float, opt_type: str, expiry: date) -> Optional[dict]:
        if self.universe is None:
            return None
        key = (float(strike), opt_type, expiry)
        if key in self._cache:
            return self._cache[key]
        out = None
        try:
            for name in ("option_contract", "get_option", "find_option", "resolve_option"):
                fn = getattr(self.universe, name, None)
                if callable(fn):
                    out = fn(self.index, expiry, float(strike), opt_type)
                    if out:
                        break
            if out is None:
                fn = getattr(self.universe, "instruments", None)
                if callable(fn):
                    for row in fn("NFO") or []:
                        if (row.get("name") == self.index
                                and float(row.get("strike") or 0) == float(strike)
                                and row.get("instrument_type") == opt_type
                                and _as_date(row.get("expiry")) == expiry):
                            out = {"tradingsymbol": row.get("tradingsymbol"),
                                   "instrument_token": row.get("instrument_token"),
                                   "exchange": "NFO", "lot_size": row.get("lot_size")}
                            break
        except Exception as exc:
            logger.debug("contract resolve failed (%s %s %s): %s",
                         strike, opt_type, expiry, exc)
        self._cache[key] = out
        return out

    def candles(self, contract: dict, broker, day: date) -> Optional[pd.DataFrame]:
        """1-minute premium candles for one contract on one day."""
        if not contract or broker is None:
            return None
        tok = contract.get("instrument_token")
        if not tok:
            return None
        try:
            rows = broker.get_historical_data(
                tok, datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15),
                datetime.combine(day, datetime.min.time()).replace(hour=15, minute=30),
                "minute") or []
        except Exception as exc:
            logger.debug("option candles failed %s %s: %s", tok, day, exc)
            return None
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.columns = [str(c).lower() for c in df.columns]
        ts = next((c for c in ("date", "timestamp") if c in df.columns), None)
        if ts is None:
            return None
        t = pd.to_datetime(df[ts], errors="coerce", utc=True, format="mixed")
        df["_ts"] = t.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return df.dropna(subset=["_ts"]).set_index("_ts")[["open", "high", "low", "close"]].astype(float)


def broker_day_legs(resolver: ContractResolver, broker, cfg: Config,
                    day: date, spot_at_entry: float) -> Optional[dict]:
    """Real 1-minute premium candles for both legs on one session.

    Returns {"call": df, "put": df, "contracts": {...}} or None when either
    contract or its history is unavailable — in which case the engine SKIPS the
    day rather than quietly substituting the model. A backtest that silently
    mixes two pricing sources is worse than one that trades fewer days.
    """
    exp = expiry_for(cfg, day)
    step = step_for(cfg.index)
    atm = atm_strike(spot_at_entry, step)
    kc, kp = cfg.legs(atm, step)
    ce = resolver.resolve(kc, "CE", exp)
    pe = resolver.resolve(kp, "PE", exp)
    if not ce or not pe:
        return None
    c_df = resolver.candles(ce, broker, day)
    p_df = resolver.candles(pe, broker, day)
    if c_df is None or p_df is None or c_df.empty or p_df.empty:
        return None
    return {"call": c_df, "put": p_df,
            "contracts": {"call": ce, "put": pe, "expiry": str(exp),
                          "call_strike": kc, "put_strike": kp}}
