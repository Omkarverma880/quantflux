"""
Hybrid option premium source — REAL first, MODELLED only as a labelled fallback.

Zerodha's ``instruments()`` lists only *currently tradable* contracts, so an
option that has already expired has no premium history. Rather than silently
dropping every older signal (which would make long backtests useless) or
silently inventing prices (which would make them dishonest), this module:

  1. tries the real premium candles for the resolved contract  → source "REAL"
  2. falls back to Black-Scholes on the index path             → source "MODELLED"

Every bar and every resulting trade carries its ``source``, and the backtest
reports statistics split by source so a modelled result can never be mistaken
for a tradable one.

Modelling caveats (surfaced in the UI): a single VIX-derived sigma ignores the
volatility smile/skew, so deep ITM/OTM strikes are the least reliable, and it
assumes European exercise with no dividend.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from core.logger import get_logger
from research.black_scholes import bs_price
from research.prev_period_vwap import _candle_dt

logger = get_logger("research.vwap_options.pricing")

VIX_SYMBOL = "INDIA VIX"
_MIN_T = 1.0 / (365.0 * 24.0)          # floor time-to-expiry at ~1 hour


class PremiumSource:
    """Resolves premium bars for a contract, caching per (token, day)."""

    def __init__(self, broker):
        self.broker = broker
        self._cache: dict = {}
        self._vix: dict[date, float] = {}
        self._vix_token: Optional[int] = None
        self._vix_loaded = False

    # ── India VIX (IV input) ──
    def _resolve_vix_token(self) -> Optional[int]:
        if self._vix_token is not None:
            return self._vix_token
        try:
            for inst in self.broker.get_instruments("NSE") or []:
                if inst.get("tradingsymbol") == VIX_SYMBOL or inst.get("name") == VIX_SYMBOL:
                    self._vix_token = int(inst["instrument_token"])
                    return self._vix_token
        except Exception as exc:
            logger.debug("VIX token lookup failed: %s", exc)
        return None

    def vix_for(self, day: date, start: date, end: date) -> Optional[float]:
        """Day-close India VIX, loaded once for the whole backtest window."""
        if not self._vix_loaded:
            self._vix_loaded = True
            tok = self._resolve_vix_token()
            if tok:
                try:
                    rows = self.broker.get_historical_data(
                        tok, datetime.combine(start - timedelta(days=10), datetime.min.time()),
                        datetime.combine(end + timedelta(days=1), datetime.min.time()), "day") or []
                    for r in rows:
                        dt = _candle_dt(r)
                        if dt:
                            self._vix[dt.date()] = float(r.get("close", 0) or 0)
                except Exception as exc:
                    logger.debug("VIX history failed: %s", exc)
        if not self._vix:
            return None
        d, tries = day, 0
        while d not in self._vix and tries < 10:      # walk back to the last traded day
            d -= timedelta(days=1)
            tries += 1
        return self._vix.get(d)

    def sigma_for(self, day: date, cfg: dict, start: date, end: date) -> float:
        if cfg.get("iv_source") == "fixed":
            return max(0.01, float(cfg["iv_fixed_pct"]) / 100.0)
        v = self.vix_for(day, start, end)
        return max(0.01, (v / 100.0) if v else float(cfg["iv_fixed_pct"]) / 100.0)

    # ── REAL premium candles ──
    def real_bars(self, token: int, day: date, timeframe: str) -> list[dict]:
        key = (token, day, timeframe)
        if key in self._cache:
            return self._cache[key]
        rows: list[dict] = []
        try:
            frm = datetime.combine(day, datetime.min.time().replace(hour=9, minute=15))
            to = min(datetime.combine(day, datetime.min.time().replace(hour=15, minute=30)),
                     datetime.now())
            rows = self.broker.get_historical_data(token, frm, to, timeframe) or []
            for r in rows:
                r["_dt"] = _candle_dt(r)
        except Exception as exc:
            logger.debug("real premium fetch failed (%s %s): %s", token, day, exc)
            rows = []
        self._cache[key] = rows
        return rows

    # ── MODELLED premium from the index path ──
    @staticmethod
    def modelled_bars(index_bars: list[dict], strike: float, opt_type: str,
                      expiry: date, sigma: float, rate_pct: float) -> list[dict]:
        """Black-Scholes premium per bar. A call is increasing in S (index high →
        premium high); a put is decreasing (index low → premium high)."""
        is_call = opt_type == "CE"
        r = float(rate_pct) / 100.0
        out = []
        for b in index_bars:
            dt = b.get("_dt")
            if dt is None:
                continue
            T = max(_MIN_T, (datetime.combine(expiry, datetime.min.time().replace(hour=15, minute=30))
                             - dt).total_seconds() / (365.0 * 24 * 3600.0))
            px = lambda S: bs_price(float(S), float(strike), T, r, sigma, is_call)  # noqa: E731
            c = px(b["close"])
            hi_s, lo_s = (b["high"], b["low"]) if is_call else (b["low"], b["high"])
            out.append({"_dt": dt, "close": round(c, 2),
                        "high": round(px(hi_s), 2), "low": round(px(lo_s), 2),
                        "open": round(px(b["open"]), 2)})
        return out

    # ── the hybrid entry point ──
    def bars_for(self, contract: Optional[dict], index_bars: list[dict], day: date,
                 cfg: dict, start: date, end: date) -> tuple[list[dict], str, Optional[str]]:
        """(bars, source, note). source ∈ REAL | MODELLED | NONE."""
        tf = cfg["timeframe"]
        if contract and contract.get("token"):
            rows = self.real_bars(int(contract["token"]), day, tf)
            if rows:
                return rows, "REAL", None
        if not cfg.get("allow_modelled", True):
            return [], "NONE", "no real premium data and modelled pricing is disabled"
        if not contract:
            return [], "NONE", "no contract could be derived"
        sigma = self.sigma_for(day, cfg, start, end)
        bars = self.modelled_bars(index_bars, contract["strike"], contract["type"],
                                  contract["expiry"], sigma, cfg["risk_free_pct"])
        if not bars:
            return [], "NONE", "index path unavailable for modelling"
        return bars, "MODELLED", f"Black-Scholes @ IV {round(sigma * 100, 1)}%"
