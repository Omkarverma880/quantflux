"""
F&O universe + option/lot-size resolution for the straddle research.

Reuses ONLY the existing Broker (Zerodha) — no new connections. The F&O-enabled
equity universe is derived from the instrument dump (every underlying with a
listed future), never hardcoded, and cached for the trading day. Lot sizes come
straight from the instrument dump per stock.
"""
from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.prev_period_vwap import _candle_dt  # noqa: reuse tz-normalising parser
from research.pmvwap_straddle.constants import INDEX_EXCLUDE

logger = get_logger("research.pmvwap_straddle.universe")


def _parse_expiry(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


class Universe:
    """Instrument-dump-backed F&O universe + option lookup (day-cached)."""

    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        self._day: Optional[date] = None
        self._nfo: list[dict] = []
        self._equities: dict[str, dict] = {}      # name → {name, lot_size}
        self._nse_tokens: dict[str, int] = {}     # tradingsymbol → token

    # ── caches ──
    def _ensure_nfo(self) -> None:
        today = date.today()
        if self._day == today and self._nfo:
            return
        with self._lock:
            if self._day == today and self._nfo:
                return
            nfo, equities = [], {}
            try:
                for inst in self.broker.get_instruments("NFO"):
                    name = inst.get("name")
                    itype = inst.get("instrument_type")
                    exp = _parse_expiry(inst.get("expiry"))
                    if not name or not exp:
                        continue
                    rec = {
                        "tradingsymbol": inst.get("tradingsymbol"),
                        "token": int(inst["instrument_token"]),
                        "name": name, "type": itype, "expiry": exp,
                        "strike": float(inst.get("strike", 0) or 0),
                        "lot_size": int(inst.get("lot_size", 0) or 0),
                    }
                    nfo.append(rec)
                    # F&O-enabled equity universe = names with a listed future.
                    if itype == "FUT" and name not in INDEX_EXCLUDE:
                        equities[name] = {"name": name, "lot_size": rec["lot_size"]}
            except Exception as exc:
                logger.error("NFO instrument dump failed: %s", exc)
                return
            self._nfo, self._equities, self._day = nfo, equities, today
            logger.info("F&O universe: %d equities, %d NFO contracts", len(equities), len(nfo))

    def equities(self) -> list[dict]:
        self._ensure_nfo()
        return sorted(self._equities.values(), key=lambda e: e["name"])

    def lot_size(self, name: str) -> int:
        self._ensure_nfo()
        return int(self._equities.get(name, {}).get("lot_size", 0) or 0)

    # ── option resolution ──
    def _opts(self, name: str) -> list[dict]:
        self._ensure_nfo()
        return [o for o in self._nfo if o["name"] == name and o["type"] in ("CE", "PE")]

    def expiry_for(self, name: str, expiry_type: str, day: date) -> Optional[date]:
        exps = sorted({o["expiry"] for o in self._opts(name)})
        if expiry_type == "monthly":
            by_month: dict = {}
            for e in exps:
                k = (e.year, e.month)
                if k not in by_month or e > by_month[k]:
                    by_month[k] = e
            exps = sorted(by_month.values())
        for e in exps:
            if e >= day:
                return e
        return exps[-1] if exps else None

    def atm_strike(self, name: str, expiry: date, price: float) -> Optional[float]:
        """Nearest LISTED strike to ``price`` (no assumed step — read from dump)."""
        strikes = sorted({o["strike"] for o in self._opts(name)
                          if o["expiry"] == expiry and o["strike"] > 0})
        if not strikes:
            return None
        return min(strikes, key=lambda s: abs(s - price))

    def resolve(self, name: str, expiry: date, strike: float, opt_type: str) -> Optional[dict]:
        for o in self._opts(name):
            if o["expiry"] == expiry and o["type"] == opt_type and abs(o["strike"] - strike) < 0.01:
                return o
        return None

    def nse_token(self, tradingsymbol: str) -> Optional[int]:
        if tradingsymbol in self._nse_tokens:
            return self._nse_tokens[tradingsymbol]
        try:
            for inst in self.broker.get_instruments("NSE"):
                if inst.get("tradingsymbol") == tradingsymbol and inst.get("segment") == "NSE":
                    tok = int(inst["instrument_token"])
                    self._nse_tokens[tradingsymbol] = tok
                    return tok
        except Exception as exc:
            logger.error("NSE token lookup failed (%s): %s", tradingsymbol, exc)
        return None
