"""
Research module #2 — Option-Chain data + per-strike 1-minute downloader.

Read-only: a live NIFTY option chain (CE/PE) around ATM with LTP, %change,
OHLC, volume, OI, VWAP (day average price) and computed IV + Greeks, plus a
per-strike download of 1-minute candles (OHLCV + OI + running VWAP) for manual
research / learning. Never places orders.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.vwap_pvwap import (
    _parse_expiry, _candle_dt, INDEX_NAME, INDEX_SPOT_TRADINGSYMBOL,
    MARKET_OPEN, MARKET_CLOSE,
)
from research.black_scholes import implied_vol, greeks

logger = get_logger("research.option_chain")

RISK_FREE = 0.065          # ~6.5% — for IV / Greeks
EXPIRY_CLOSE = dtime(15, 30)


class OptionChain:
    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        self._nfo: Optional[list[dict]] = None
        self._nfo_date: Optional[date] = None
        self._prev_oi: dict[int, float] = {}      # token -> prev-day close OI (cached/day)
        self._prev_oi_date: Optional[date] = None

    # ── instruments ──
    def _nifty_options(self) -> list[dict]:
        today = date.today()
        if self._nfo is not None and self._nfo_date == today:
            return self._nfo
        opts = []
        try:
            for inst in self.broker.get_instruments("NFO"):
                if inst.get("name") != INDEX_NAME or inst.get("instrument_type") not in ("CE", "PE"):
                    continue
                exp = _parse_expiry(inst.get("expiry"))
                if not exp:
                    continue
                opts.append({
                    "tradingsymbol": inst.get("tradingsymbol"),
                    "token": int(inst["instrument_token"]),
                    "strike": float(inst.get("strike", 0) or 0),
                    "type": inst.get("instrument_type"), "expiry": exp,
                })
        except Exception as exc:
            logger.error("Option chain NFO fetch failed: %s", exc)
        self._nfo, self._nfo_date = opts, today
        return opts

    def _expiries(self) -> list[date]:
        return sorted({o["expiry"] for o in self._nifty_options()})

    def _expiry_for(self, expiry_type: str, day: date) -> Optional[date]:
        exps = self._expiries()
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

    def _spot(self) -> float:
        try:
            return float((self.broker.get_ltp([f"NSE:{INDEX_SPOT_TRADINGSYMBOL}"]) or {})
                         .get(f"NSE:{INDEX_SPOT_TRADINGSYMBOL}", 0) or 0)
        except Exception:
            return 0.0

    def _resolve(self, expiry: date, strike: float, opt_type: str) -> Optional[dict]:
        for o in self._nifty_options():
            if o["expiry"] == expiry and o["type"] == opt_type and abs(o["strike"] - strike) < 0.5:
                return o
        return None

    def list_expiries(self) -> dict:
        return {"status": "ok", "expiries": [e.isoformat() for e in self._expiries()]}

    def _prev_oi_for(self, tokens: list[int]) -> dict[int, float]:
        """Previous trading day's close OI per token (cached for the day).

        One historical 'day' call per *new* token — slow only on the first
        snapshot of the day; auto-refreshes reuse the cache.
        """
        today = date.today()
        if self._prev_oi_date != today:
            self._prev_oi = {}
            self._prev_oi_date = today
        frm = datetime.combine(today - timedelta(days=7), MARKET_OPEN)
        to = datetime.combine(today - timedelta(days=1), MARKET_CLOSE)
        for t in tokens:
            if t in self._prev_oi:
                continue
            try:
                candles = self.broker.get_historical_data(t, frm, to, "day", oi=True) or []
                self._prev_oi[t] = float(candles[-1].get("oi", 0) or 0) if candles else 0.0
            except Exception:
                self._prev_oi[t] = 0.0
        return self._prev_oi

    @staticmethod
    def _buildup(price_chg: Optional[float], oi_chg: Optional[float]) -> Optional[str]:
        if price_chg is None or oi_chg is None or oi_chg == 0:
            return None
        if price_chg >= 0 and oi_chg > 0:
            return "Long Buildup"
        if price_chg < 0 and oi_chg > 0:
            return "Short Buildup"
        if price_chg >= 0 and oi_chg < 0:
            return "Short Covering"
        return "Long Unwinding"

    # ── live snapshot ──
    def snapshot(self, expiry_type: str = "weekly", count: int = 15,
                 interval: int = 50, expiry: Optional[str] = None) -> dict:
        with self._lock:
            spot = self._spot()
            if spot <= 0:
                return {"status": "error", "message": "No NIFTY spot — market open & Zerodha connected?"}
            exp = _parse_expiry(expiry) if expiry else self._expiry_for(expiry_type, date.today())
            if not exp:
                return {"status": "error", "message": "No expiry resolvable"}

            atm = int(round(spot / interval) * interval)
            strikes = [atm + i * interval for i in range(-count, count + 1)]

            keys, meta = [], {}
            for s in strikes:
                for typ in ("CE", "PE"):
                    o = self._resolve(exp, s, typ)
                    if o:
                        k = f"NFO:{o['tradingsymbol']}"
                        keys.append(k)
                        meta[k] = (s, typ, o)
            if not keys:
                return {"status": "error", "message": f"No contracts listed for {exp.isoformat()}"}

            try:
                quotes = self.broker.get_quote(keys) or {}
            except Exception as exc:
                logger.error("Option chain quote failed: %s", exc)
                quotes = {}

            prev_oi = self._prev_oi_for([m[2]["token"] for m in meta.values()])

            T = max((datetime.combine(exp, EXPIRY_CLOSE) - datetime.now()).total_seconds(), 0) / (365 * 24 * 3600)
            by_strike = {s: {"strike": s, "ce": None, "pe": None,
                             "ce_itm": s < spot, "pe_itm": s > spot} for s in strikes}

            for k, (strike, typ, o) in meta.items():
                q = quotes.get(k) or {}
                ltp = float(q.get("last_price", 0) or 0)
                ohlc = q.get("ohlc") or {}
                close = float(ohlc.get("close", 0) or 0)
                vol = float(q.get("volume", 0) or 0)
                oi = float(q.get("oi", 0) or 0)
                vwap = float(q.get("average_price", 0) or 0)
                chg = ((ltp - close) / close * 100) if close else 0.0
                oi_chg = oi - prev_oi.get(o["token"], 0.0)
                iv = implied_vol(ltp, spot, strike, T, RISK_FREE, typ == "CE")
                g = greeks(spot, strike, T, RISK_FREE, iv, typ == "CE") if iv else {}
                cell = {
                    "symbol": o["tradingsymbol"], "token": o["token"],
                    "ltp": round(ltp, 2), "change_pct": round(chg, 2),
                    "open": round(float(ohlc.get("open", 0) or 0), 2),
                    "high": round(float(ohlc.get("high", 0) or 0), 2),
                    "low": round(float(ohlc.get("low", 0) or 0), 2),
                    "close": round(close, 2), "volume": int(vol), "oi": int(oi),
                    "oi_change": int(oi_chg), "buildup": self._buildup(chg, oi_chg),
                    "vwap": round(vwap, 2), "iv": round(iv * 100, 2) if iv else None,
                    **g,
                }
                by_strike[strike]["ce" if typ == "CE" else "pe"] = cell

            # ── Chain-level metrics: PCR, Max Pain, totals ──
            ce_oi = {s: (by_strike[s]["ce"]["oi"] if by_strike[s]["ce"] else 0) for s in strikes}
            pe_oi = {s: (by_strike[s]["pe"]["oi"] if by_strike[s]["pe"] else 0) for s in strikes}
            tot_ce_oi, tot_pe_oi = sum(ce_oi.values()), sum(pe_oi.values())
            tot_ce_vol = sum((by_strike[s]["ce"]["volume"] if by_strike[s]["ce"] else 0) for s in strikes)
            tot_pe_vol = sum((by_strike[s]["pe"]["volume"] if by_strike[s]["pe"] else 0) for s in strikes)
            pcr = round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi else None
            max_pain, best = None, None
            for E in strikes:
                loss = (sum(ce_oi[s] * max(0, E - s) for s in strikes)
                        + sum(pe_oi[s] * max(0, s - E) for s in strikes))
                if best is None or loss < best:
                    best, max_pain = loss, E

            return {
                "status": "ok", "spot": round(spot, 2), "atm": atm,
                "expiry": exp.isoformat(), "expiry_type": expiry_type,
                "interval": interval, "count": count,
                "days_to_expiry": (exp - date.today()).days,
                "pcr": pcr, "max_pain": max_pain,
                "total_ce_oi": int(tot_ce_oi), "total_pe_oi": int(tot_pe_oi),
                "total_ce_vol": int(tot_ce_vol), "total_pe_vol": int(tot_pe_vol),
                "fetched_at": datetime.now().strftime("%H:%M:%S"),
                "rows": [by_strike[s] for s in strikes],
            }

    # ── per-strike 1-minute download ──
    def download(self, token: int, symbol: str, day: Optional[str] = None) -> dict:
        try:
            d = _parse_expiry(day) if day else date.today()
        except Exception:
            d = date.today()
        frm = datetime.combine(d, MARKET_OPEN)
        to = datetime.combine(d, MARKET_CLOSE)
        try:
            candles = self.broker.get_historical_data(int(token), frm, to, "minute", oi=True) or []
        except Exception as exc:
            logger.error("Option chain download failed (%s): %s", symbol, exc)
            return {"status": "error", "message": str(exc)}

        rows, cum_pv, cum_v = [], 0.0, 0.0
        for c in candles:
            dt = _candle_dt(c)
            o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            v = float(c.get("volume", 0) or 0)
            tp = (h + l + cl) / 3.0
            cum_pv += tp * v
            cum_v += v
            rows.append({
                "datetime": dt.strftime("%Y-%m-%d %H:%M") if dt else "",
                "open": round(o, 2), "high": round(h, 2), "low": round(l, 2),
                "close": round(cl, 2), "volume": int(v),
                "oi": int(c.get("oi", 0) or 0),
                "vwap": round(cum_pv / cum_v, 2) if cum_v > 0 else round(tp, 2),
            })
        return {"status": "ok", "symbol": symbol, "date": d.isoformat(), "rows": rows}
