"""
OPEI service / live-data orchestration.

Read-only. Reuses the existing Broker (Zerodha Kite) and OptionChain for spot /
instrument / expiry resolution, and black_scholes for greeks. Every refresh it:

  1. resolves the ATM and the selected CE + PE strikes (auto-updates with ATM),
  2. pulls a single batched quote (spot, VIX, CE, PE, nearby-strike OI for PCR,
     top-10 breadth) + cached premium candles,
  3. derives ~40 features per side across all confluence categories,
  4. runs the scoring engine → 0–100 score, breakdown, reasons and the top-N
     premium entry levels,

and returns a compact snapshot for the dashboard. It never places orders.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.option_chain import OptionChain
from research.vwap_pvwap import _parse_expiry, _candle_dt, MARKET_OPEN, MARKET_CLOSE
from research.black_scholes import implied_vol, greeks
from research.opei import indicators as ind
from research.opei import engine as eng
from research.opei.config import load_config, save_config, sanitize
from research.opei.constants import (
    STRIKE_OFFSETS, INDEX_SPOT_TRADINGSYMBOL, VIX_TRADINGSYMBOL, INSTITUTIONAL_THRESHOLD,
)

logger = get_logger("research.opei")

RISK_FREE = 0.065
EXPIRY_CLOSE = dtime(15, 30)
_TF = {"1m": "minute", "3m": "3minute", "5m": "5minute", "15m": "15minute"}
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}


class OPEIEngine:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self._lock = threading.Lock()
        self._chain = OptionChain(broker)
        self._candle_cache: dict[tuple, tuple[float, list[dict]]] = {}

    # ── config passthrough ──
    def load_config(self) -> dict:
        return load_config()

    def save_config(self, partial: dict) -> dict:
        return save_config(partial)

    # ── helpers ──
    def _candles(self, token: int, tf: str) -> list[dict]:
        key = (token, tf, date.today())
        cached = self._candle_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < 20:
            return cached[1]
        frm = datetime.combine(date.today(), MARKET_OPEN)
        to = datetime.combine(date.today(), MARKET_CLOSE)
        try:
            raw = self.broker.get_historical_data(token, frm, to, _TF.get(tf, "5minute"), oi=True) or []
        except Exception as exc:
            logger.debug("opei candles failed %s: %s", token, exc)
            raw = cached[1] if cached else []
        self._candle_cache[key] = (now, raw)
        return raw

    @staticmethod
    def _session(t: dtime) -> str:
        if t < dtime(10, 15):
            return "opening"
        if dtime(12, 0) <= t < dtime(13, 15):
            return "lunch"
        if t >= dtime(14, 30):
            return "closing"
        return "mid"

    @staticmethod
    def _buildup(candles: list[dict]) -> Optional[str]:
        if len(candles) < 2:
            return None
        oi0 = float(candles[0].get("oi", 0) or 0)
        oi1 = float(candles[-1].get("oi", 0) or 0)
        p0 = float(candles[0].get("open", 0) or 0)
        p1 = float(candles[-1].get("close", 0) or 0)
        if oi0 == 0 or p0 == 0:
            return None
        price_up, oi_up = p1 >= p0, oi1 > oi0
        if price_up and oi_up:
            return "Long Buildup"
        if not price_up and oi_up:
            return "Short Buildup"
        if price_up and not oi_up:
            return "Short Covering"
        return "Long Unwinding"

    def _features(self, side: str, symbol: str, strike: int, q: dict, candles: list[dict],
                  spot: float, expiry: date, breadth: float, vix: float, vix_chg: float,
                  pcr: Optional[float], tf: str, now: datetime) -> dict:
        ohlc = q.get("ohlc") or {}
        ltp = float(q.get("last_price", 0) or 0)
        depth = q.get("depth") or {}
        buys, sells = depth.get("buy") or [], depth.get("sell") or []
        bid = float(buys[0]["price"]) if buys else 0.0
        ask = float(sells[0]["price"]) if sells else 0.0
        bid_qty = sum(float(b.get("quantity", 0) or 0) for b in buys)
        ask_qty = sum(float(s.get("quantity", 0) or 0) for s in sells)
        spread_pct = ((ask - bid) / ((ask + bid) / 2.0) * 100.0) if (ask and bid) else None
        depth_imb = (bid_qty / (bid_qty + ask_qty)) if (bid_qty + ask_qty) else None

        cl = ind.closes(candles) if candles else [ltp]
        # Stable anchor = close of the last COMPLETED candle (levels are built off
        # this, so they don't jitter every second on live ticks).
        mins = _TF_MIN.get(tf, 5)
        completed = [c for c in candles if _candle_dt(c) and _candle_dt(c) + timedelta(minutes=mins) <= now]
        anchor = round(float(completed[-1]["close"]), 2) if completed else ltp
        atr = ind.atr(candles) if len(candles) > 15 else None
        atr_prev = ind.atr(candles[:-5]) if len(candles) > 20 else None
        vols = ind.volumes(candles)
        rel_vol = (vols[-1] / (sum(vols[:-1]) / max(1, len(vols) - 1))) if len(vols) > 3 and sum(vols[:-1]) else 1.0
        bb = ind.bollinger(cl)
        bb_prev = ind.bollinger(cl[:-10]) if len(cl) > 30 else bb

        # greeks / IV
        iv = None
        try:
            T = max((datetime.combine(expiry, EXPIRY_CLOSE) - now).total_seconds(), 0) / (365 * 24 * 3600)
            if ltp > 0 and spot > 0 and T > 0:
                iv = implied_vol(ltp, spot, strike, T, RISK_FREE, side == "CE")
        except Exception:
            iv = None

        return {
            "side": side, "symbol": symbol, "strike": strike,
            "premium": ltp, "anchor": anchor, "atr": atr,
            "vwap": float(q.get("average_price", 0) or 0) or ind.running_vwap(candles),
            "ema9": ind.ema(cl, 9), "ema20": ind.ema(cl, 20), "ema50": ind.ema(cl, 50),
            "premium_slope": ind.slope(cl), "rsi": ind.rsi(cl), "adx": ind.adx(candles),
            "roc": ind.roc(cl), "macd_hist": ind.macd(cl)["hist"],
            "bb": bb, "donchian": ind.donchian(candles), "pa": ind.price_action(candles),
            "swing": ind.swing_levels(candles),
            "rel_volume": round(rel_vol, 2),
            "vol_expansion": len(vols) > 6 and sum(vols[-3:]) > sum(vols[-6:-3]) * 1.2,
            "atr_expansion": bool(atr and atr_prev and atr > atr_prev * 1.1),
            "compression_then_expansion": bool(bb.get("width") and bb_prev.get("width")
                                               and bb["width"] > bb_prev["width"] * 1.3),
            "buildup": self._buildup(candles), "pcr": pcr,
            "vix_change": vix_chg, "iv": round(iv * 100, 2) if iv else None,
            "iv_rank": None,
            "spread_pct": round(spread_pct, 2) if spread_pct is not None else None,
            "depth_imbalance": round(depth_imb, 2) if depth_imb is not None else None,
            "breadth": breadth, "session": self._session(now.time()),
            "expiry_day": expiry == now.date(), "_tf_min": _TF_MIN.get(tf, 5),
            "bid": round(bid, 2), "ask": round(ask, 2),
            "oi": int(float(q.get("oi", 0) or 0)),
            "volume": int(float(q.get("volume", 0) or 0)),
            "change_pct": round(((ltp - float(ohlc.get("close", 0) or 0)) /
                                 float(ohlc.get("close", 1) or 1) * 100.0), 2) if ohlc.get("close") else 0.0,
        }

    # ── snapshot ──
    def snapshot(self, overrides: Optional[dict] = None) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            now = datetime.now()
            tf = cfg["timeframe"] if cfg["timeframe"] in _TF else "5m"

            spot = self._chain._spot()
            if spot <= 0:
                return {"status": "error", "message": "No live NIFTY spot — market open & Zerodha connected?"}
            atm = int(round(spot / 50) * 50)
            expiry = self._chain._expiry_for(cfg["expiry_type"], date.today())
            if not expiry:
                return {"status": "error", "message": "No option expiry resolvable"}

            mag = STRIKE_OFFSETS[cfg["strike"]]
            ce_strike, pe_strike = atm - mag, atm + mag
            ce = self._chain._resolve(expiry, ce_strike, "CE")
            pe = self._chain._resolve(expiry, pe_strike, "PE")
            if not ce or not pe:
                return {"status": "error", "message": f"CE/PE not listed for {expiry} ({ce_strike}/{pe_strike})"}

            # ── batched quote: CE, PE, VIX, PCR strikes, breadth ──
            from research.nifty_sentiment import DEFAULT_CONSTITUENTS
            top = sorted(DEFAULT_CONSTITUENTS, key=lambda c: c.get("weight", 0), reverse=True)[:10]
            pcr_strikes = [atm + i * 50 for i in range(-3, 4)]
            pcr_keys = []
            for s in pcr_strikes:
                for typ, o in (("CE", self._chain._resolve(expiry, s, "CE")),
                               ("PE", self._chain._resolve(expiry, s, "PE"))):
                    if o:
                        pcr_keys.append((f"NFO:{o['tradingsymbol']}", typ))
            keys = ([f"NFO:{ce['tradingsymbol']}", f"NFO:{pe['tradingsymbol']}",
                     f"NSE:{VIX_TRADINGSYMBOL}"]
                    + [k for k, _ in pcr_keys]
                    + [f"NSE:{c['symbol']}" for c in top])
            try:
                quotes = self.broker.get_quote(list(dict.fromkeys(keys))) or {}
            except Exception as exc:
                logger.error("opei quote failed: %s", exc)
                return {"status": "error", "message": f"Quote fetch failed: {exc}"}

            # PCR
            tot_ce = sum(float((quotes.get(k) or {}).get("oi", 0) or 0) for k, t in pcr_keys if t == "CE")
            tot_pe = sum(float((quotes.get(k) or {}).get("oi", 0) or 0) for k, t in pcr_keys if t == "PE")
            pcr = round(tot_pe / tot_ce, 2) if tot_ce else None

            # VIX
            vq = quotes.get(f"NSE:{VIX_TRADINGSYMBOL}") or {}
            vix = float(vq.get("last_price", 0) or 0)
            vix_chg = round(vix - float((vq.get("ohlc") or {}).get("close", vix) or vix), 2)

            # Breadth (weighted %chg of top-10)
            wsum = tot_contrib = 0.0
            for c in top:
                q = quotes.get(f"NSE:{c['symbol']}") or {}
                close = float((q.get("ohlc") or {}).get("close", 0) or 0)
                ltp = float(q.get("last_price", 0) or 0)
                if close > 0 and ltp > 0:
                    w = float(c.get("weight", 0))
                    wsum += w
                    tot_contrib += w * (ltp - close) / close * 100.0
            breadth = round(tot_contrib / wsum, 3) if wsum else 0.0

            # ── Directional market bias (breadth + PCR) — a CALL and a PUT can't
            # both be high-probability explosive-up at once, so we favour one side.
            b = breadth + (0.2 if (pcr is not None and pcr >= 1.1) else (-0.2 if (pcr is not None and pcr <= 0.9) else 0.0))
            if b > 0.15:
                bias_dir, bias_strength, preferred = "bullish", min(abs(b) / 0.6, 1.0), "CE"
            elif b < -0.15:
                bias_dir, bias_strength, preferred = "bearish", min(abs(b) / 0.6, 1.0), "PE"
            else:
                bias_dir, bias_strength, preferred = "neutral", 0.0, None

            out = {"status": "ok", "config": cfg, "spot": round(spot, 2), "atm": atm,
                   "expiry": expiry.isoformat(), "strike_label": cfg["strike"],
                   "vix": round(vix, 2), "vix_change": vix_chg, "pcr": pcr, "breadth": breadth,
                   "bias": bias_dir, "bias_strength": round(bias_strength, 2), "preferred_side": preferred,
                   "fetched_at": now.strftime("%H:%M:%S"), "sides": {}}

            for side, o, strike in (("CE", ce, ce_strike), ("PE", pe, pe_strike)):
                q = quotes.get(f"NFO:{o['tradingsymbol']}") or {}
                candles = self._candles(int(o["token"]), tf)
                feat = self._features(side, o["tradingsymbol"], strike, q, candles, spot,
                                      expiry, breadth, vix, vix_chg, pcr, tf, now)
                ev = eng.evaluate_side(feat, cfg["weights"], cfg, bias_dir, bias_strength)
                out["sides"][side] = {
                    "symbol": o["tradingsymbol"], "strike": strike, "token": int(o["token"]),
                    **ev, "features": {k: feat[k] for k in (
                        "premium", "atr", "vwap", "rsi", "adx", "roc", "rel_volume",
                        "buildup", "iv", "spread_pct", "depth_imbalance", "oi", "volume",
                        "change_pct", "session")},
                }
            return out

    def institutional_recs(self, snap: dict, threshold: Optional[int] = None) -> list[dict]:
        """Extract qualifying best levels from a snapshot for logging / alerts."""
        recs = []
        cfg = snap.get("config") or {}
        thr = int(threshold if threshold is not None
                  else cfg.get("alert_min_confidence", cfg.get("institutional_threshold", INSTITUTIONAL_THRESHOLD)))
        for side, sd in (snap.get("sides") or {}).items():
            for lv in sd.get("levels", []):
                if lv.get("is_best") and lv.get("confidence", 0) >= thr:
                    recs.append({"side": side, "symbol": sd["symbol"], "strike": sd["strike"],
                                 "premium": sd["premium"], "reasons": sd["reasons"], **lv})
        return recs
