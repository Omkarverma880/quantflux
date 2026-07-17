"""
Research module #6 — Market Pulse (dashboard confirmations).

Read-only. Computes the technical "confirmation" signals that power the
one-glance Market Dashboard and that aren't already produced by the existing
SentimentEngine / NiftySentiment engines:

  • Cumulative Volume bias        (reuses the CumulativeVolume strategy engine)
  • NIFTY 20 DMA / 200 DMA         (daily simple moving averages + structure)
  • VWAP vs Previous-day VWAP      (today HLC3-VWAP vs prior session)
  • Psychological round-number     (nearest 100-level support/resistance + break)
  • Gann Square-of-9 levels        (45°-spaced support/resistance + breakout)

Everything else on the dashboard (global sentiment, FII/DII, derivative &
technical scores, 10-stock weightage, sector strength) is served by the
already-existing endpoints and composed on the front-end, so nothing here is
duplicated and no existing module is modified.

Efficiency: one index daily fetch + one today-minute + one prev-day-minute
fetch per snapshot, cached ~30s. Never places orders.
"""
from __future__ import annotations

import csv
import math
import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from config import settings
from core.broker import Broker
from core.logger import get_logger

logger = get_logger("research.market_pulse")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
INDEX_SPOT_TRADINGSYMBOL = "NIFTY 50"
GANN_CSV = settings.BASE_DIR / "gann_levels.csv"


def _bias_from(score: float, threshold: float = 0.0) -> str:
    if score > threshold:
        return "Bullish"
    if score < -threshold:
        return "Bearish"
    return "Neutral"


def _arrow(bias: str) -> str:
    return {"Bullish": "up", "Bearish": "down"}.get(bias, "flat")


class MarketPulse:
    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        self._cache: Optional[dict] = None
        self._cache_at: Optional[datetime] = None
        self._token: Optional[int] = None
        self._cv = None  # lazy CumulativeVolume engine (reused)

    # ── index token ──
    def _index_token(self) -> Optional[int]:
        if self._token:
            return self._token
        try:
            for inst in self.broker.get_instruments("NSE"):
                if inst.get("tradingsymbol") == INDEX_SPOT_TRADINGSYMBOL:
                    self._token = int(inst["instrument_token"])
                    return self._token
        except Exception as exc:
            logger.debug("market_pulse index token failed: %s", exc)
        return None

    def _spot(self) -> float:
        try:
            return float((self.broker.get_ltp([f"NSE:{INDEX_SPOT_TRADINGSYMBOL}"]) or {})
                         .get(f"NSE:{INDEX_SPOT_TRADINGSYMBOL}", 0) or 0)
        except Exception:
            return 0.0

    # ── DMA (daily simple moving averages) ──
    def _dma(self, token: int, spot: float) -> dict:
        to = datetime.now()
        frm = datetime.combine((to - timedelta(days=420)).date(), MARKET_OPEN)
        try:
            rows = self.broker.get_historical_data(token, frm, to, "day") or []
        except Exception as exc:
            logger.debug("market_pulse DMA fetch failed: %s", exc)
            rows = []
        closes = [float(r.get("close", 0) or 0) for r in rows if r.get("close")]
        if len(closes) < 20:
            return {"available": False}
        sma20 = sum(closes[-20:]) / 20.0
        sma200 = sum(closes[-200:]) / min(200, len(closes)) if len(closes) >= 200 else None
        price = spot or closes[-1]
        above20 = price > sma20
        above200 = (price > sma200) if sma200 else None
        # structure: golden (20>200) / death (20<200)
        cross = None
        if sma200:
            cross = "golden" if sma20 > sma200 else "death"
        score = (1 if above20 else -1) + (1 if above200 else -1 if above200 is False else 0) + \
                (0.5 if cross == "golden" else -0.5 if cross == "death" else 0)
        return {
            "available": True,
            "sma20": round(sma20, 2), "sma200": round(sma200, 2) if sma200 else None,
            "price": round(price, 2), "above_20": above20, "above_200": above200,
            "cross": cross, "bias": _bias_from(score), "arrow": _arrow(_bias_from(score)),
            "detail": f"Price {'>' if above20 else '<'} 20DMA · "
                      f"{'>' if above200 else '<' if above200 is False else '?'} 200DMA"
                      + (f" · {cross} cross" if cross else ""),
        }

    # ── VWAP / prev-day VWAP (HLC3 — index carries no volume) ──
    def _minute(self, token: int, day: date) -> list[dict]:
        try:
            return self.broker.get_historical_data(
                token, datetime.combine(day, MARKET_OPEN),
                datetime.combine(day, MARKET_CLOSE), "minute") or []
        except Exception:
            return []

    @staticmethod
    def _vwap(rows: list[dict]) -> float:
        pv = v = tp_sum = 0.0
        n = 0
        for c in rows:
            h = float(c.get("high", 0) or 0); l = float(c.get("low", 0) or 0)
            cl = float(c.get("close", 0) or 0); vol = float(c.get("volume", 0) or 0)
            tp = (h + l + cl) / 3.0
            pv += tp * vol; v += vol; tp_sum += tp; n += 1
        if v > 0:
            return pv / v
        return tp_sum / n if n else 0.0

    def _prev_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def _vwap_block(self, token: int, spot: float) -> dict:
        # Find the most recent session that actually has intraday candles. This
        # keeps the tile populated after hours / before the open (when "today"
        # has no candles yet) by falling back to the last traded session.
        cur_day = date.today()
        tc = []
        for _ in range(6):
            tc = self._minute(token, cur_day)
            if tc:
                break
            cur_day = self._prev_trading_day(cur_day)
        if not tc:
            return {"available": False}
        stale = cur_day != date.today()

        pd = self._prev_trading_day(cur_day)
        pc = []
        for _ in range(6):
            pc = self._minute(token, pd)
            if pc:
                break
            pd = self._prev_trading_day(pd)

        vwap = self._vwap(tc)
        prev_vwap = self._vwap(pc) if pc else 0.0
        # after hours use the session close as the reference "price"
        price = spot if (spot and not stale) else float(tc[-1]["close"])
        if not vwap:
            return {"available": False}
        above = price > vwap
        vwap_up = (vwap > prev_vwap) if prev_vwap else None
        score = (1 if above else -1) + (0.5 if vwap_up else -0.5 if vwap_up is False else 0)
        return {
            "available": True, "stale": stale,
            "session": cur_day.isoformat(), "vwap": round(vwap, 2),
            "prev_vwap": round(prev_vwap, 2) if prev_vwap else None,
            "price": round(price, 2), "above_vwap": above, "vwap_rising": vwap_up,
            "bias": _bias_from(score), "arrow": _arrow(_bias_from(score)),
            "detail": (f"Price {'>' if above else '<'} VWAP"
                       + (f" · VWAP {'>' if vwap_up else '<'} P-VWAP" if prev_vwap else "")
                       + (" (last session)" if stale else "")),
        }

    # ── Broader index quotes (quick-view strip) ──
    INDICES = [
        ("NIFTY 50", "NSE:NIFTY 50"),
        ("NIFTY BANK", "NSE:NIFTY BANK"),
        ("NIFTY 200", "NSE:NIFTY 200"),
        ("NIFTY 500", "NSE:NIFTY 500"),
        ("SENSEX", "BSE:SENSEX"),
    ]

    # ── FII / DII net flow (NSE public, robust + sticky cache) ──
    _fii_dii_cache: Optional[dict] = None

    def _fii_dii(self) -> dict:
        today = date.today().isoformat()
        c = MarketPulse._fii_dii_cache
        # reuse today's good value; don't refetch once we have it
        if c and c.get("date") == today and c.get("available"):
            return c
        fii = dii = fii_date = None
        try:
            import requests
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://www.nseindia.com/reports/fii-dii",
                "Connection": "keep-alive",
            })
            # Seed the anti-bot cookies by visiting real pages first.
            for url in ("https://www.nseindia.com",
                        "https://www.nseindia.com/reports/fii-dii"):
                try:
                    s.get(url, timeout=6)
                except Exception:
                    pass
            r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=8)
            if r.status_code == 200:
                for row in (r.json() or []):
                    cat = (row.get("category") or "").upper()
                    try:
                        net = float(str(row.get("netValue", "0")).replace(",", ""))
                    except (TypeError, ValueError):
                        continue
                    fii_date = row.get("date") or fii_date
                    if "FII" in cat or "FPI" in cat:
                        fii = net
                    elif "DII" in cat:
                        dii = net
        except Exception as exc:
            logger.debug("market_pulse FII/DII fetch failed: %s", exc)

        if fii is not None or dii is not None:
            bias = "Bullish" if (fii or 0) > 0 else "Bearish" if (fii or 0) < 0 else "Neutral"
            MarketPulse._fii_dii_cache = {
                "available": True, "date": today, "fii": fii, "dii": dii,
                "data_date": fii_date, "bias": bias, "arrow": _arrow(bias), "stale": False,
            }
            return MarketPulse._fii_dii_cache
        # Fetch failed — KEEP the last good value (only mark it stale) rather
        # than blanking the tile. This is what caused FII/DII to vanish before.
        if c and c.get("available"):
            return {**c, "stale": True}
        return {"available": False, "date": today, "bias": "Neutral", "arrow": "flat"}

    def _indices(self) -> list[dict]:
        keys = [k for _, k in self.INDICES]
        try:
            q = self.broker.get_quote(keys) or {}
        except Exception as exc:
            logger.debug("market_pulse indices quote failed: %s", exc)
            q = {}
        out = []
        for name, key in self.INDICES:
            d = q.get(key) or {}
            ltp = float(d.get("last_price", 0) or 0)
            close = float((d.get("ohlc") or {}).get("close", 0) or 0)
            pct = ((ltp - close) / close * 100.0) if close else float(d.get("net_change", 0) or 0)
            out.append({"name": name, "ltp": round(ltp, 2),
                        "change_pct": round(pct, 2), "available": ltp > 0})
        return out

    # ── Psychological round-number levels ──
    def _psychological(self, spot: float, prev_close: float) -> dict:
        if spot <= 0:
            return {"available": False}
        step = 100
        support = math.floor(spot / step) * step
        resistance = math.ceil(spot / step) * step
        if resistance == support:
            resistance = support + step
        # breakout: prev close and spot straddle a round-100 level
        breakout = "none"
        if prev_close > 0:
            crossed = math.floor(min(prev_close, spot) / step) != math.floor(max(prev_close, spot) / step)
            if crossed:
                breakout = "up" if spot > prev_close else "down"
        dist_res = resistance - spot
        dist_sup = spot - support
        near = "resistance" if dist_res <= dist_sup else "support"
        score = (1 if breakout == "up" else -1 if breakout == "down"
                 else (0.4 if near == "support" else -0.4))
        return {
            "available": True, "support": support, "resistance": resistance,
            "spot": round(spot, 2), "breakout": breakout, "nearest": near,
            "dist_to_resistance": round(dist_res, 1), "dist_to_support": round(dist_sup, 1),
            "bias": _bias_from(score), "arrow": _arrow(_bias_from(score)),
            "detail": (f"Broke {breakout} through {resistance if breakout == 'up' else support}"
                       if breakout != "none"
                       else f"Between {support}–{resistance} (near {near})"),
        }

    # ── Gann levels (from gann_levels.csv — Square-of-9 ladder) ──
    _gann_csv: Optional[list[float]] = None

    @classmethod
    def _load_gann_levels(cls) -> list[float]:
        if cls._gann_csv is not None:
            return cls._gann_csv
        levels: list[float] = []
        try:
            with open(GANN_CSV, newline="") as f:
                for row in csv.reader(f):
                    if not row:
                        continue
                    try:
                        levels.append(float(str(row[0]).strip()))
                    except (ValueError, IndexError):
                        continue
        except Exception as exc:
            logger.debug("gann_levels.csv load failed: %s", exc)
        cls._gann_csv = sorted(set(levels))
        return cls._gann_csv

    @staticmethod
    def _sq9_fallback(spot: float) -> list[float]:
        r = math.sqrt(spot)
        step = 0.125
        base = round(r / step) * step
        return sorted({round((base + i * step) ** 2, 1) for i in range(-5, 6)})

    def _gann(self, spot: float, prev_close: float) -> dict:
        if spot <= 0:
            return {"available": False}
        levels = self._load_gann_levels() or self._sq9_fallback(spot)
        source = "csv" if self._load_gann_levels() else "sq9"
        support = max((l for l in levels if l <= spot), default=levels[0])
        resistance = min((l for l in levels if l >= spot), default=levels[-1])
        band = max(12.0, spot * 0.0008)      # ~20 pts near NIFTY 24k

        # Is price sitting AT a Gann node (testing it)?
        at = None
        if abs(resistance - spot) <= band:
            at = "resistance"
        elif abs(spot - support) <= band:
            at = "support"

        # Breakout if price crossed a Gann node since the previous close
        breakout = "none"
        if prev_close > 0:
            for lvl in levels:
                if min(prev_close, spot) < lvl <= max(prev_close, spot):
                    breakout = "up" if spot > prev_close else "down"
                    break

        if breakout == "up":
            score, status = 1.0, f"Broke above Gann {resistance:.0f}"
        elif breakout == "down":
            score, status = -1.0, f"Broke below Gann {support:.0f}"
        elif at == "resistance":
            score, status = -0.4, f"Testing Gann resistance {resistance:.0f}"
        elif at == "support":
            score, status = 0.4, f"Holding Gann support {support:.0f}"
        else:
            score = 0.3 if (spot - support) < (resistance - spot) else -0.3
            status = f"Between Gann {support:.0f}–{resistance:.0f}"

        return {
            "available": True, "source": source,
            "support": round(support, 2), "resistance": round(resistance, 2),
            "spot": round(spot, 2), "breakout": breakout, "at_level": at,
            "dist_to_resistance": round(resistance - spot, 1),
            "dist_to_support": round(spot - support, 1),
            "bias": _bias_from(score), "arrow": _arrow(_bias_from(score)),
            "detail": status,
        }

    # ── 5-day first-hour high/low (opening-hour range breakout) ──
    def _five_day_first_hour(self, token: int, spot: float, prev_close: float) -> dict:
        to = datetime.now()
        frm = datetime.combine((to - timedelta(days=12)).date(), MARKET_OPEN)
        try:
            rows = self.broker.get_historical_data(token, frm, to, "60minute") or []
        except Exception as exc:
            logger.debug("market_pulse 5d-first-hour fetch failed: %s", exc)
            rows = []
        # first 60-min candle (09:15) of each day
        first_by_day: dict = {}
        for r in rows:
            dt = r.get("date")
            d = dt.date() if hasattr(dt, "date") else None
            t = dt.time() if hasattr(dt, "time") else None
            if d is None:
                continue
            if d not in first_by_day and (t is None or t <= dtime(9, 30)):
                first_by_day[d] = r
        days = sorted(first_by_day.keys())[-5:]
        if not days:
            return {"available": False}
        highs = [float(first_by_day[d].get("high", 0) or 0) for d in days]
        lows = [float(first_by_day[d].get("low", 0) or 0) for d in days if first_by_day[d].get("low")]
        if not highs or not lows:
            return {"available": False}
        rng_high = max(highs)
        rng_low = min(lows)
        price = spot or prev_close
        band = max(12.0, price * 0.0008)

        if price > rng_high:
            score, status, breakout = 1.0, f"Breakout above {rng_high:.0f}", "up"
        elif price < rng_low:
            score, status, breakout = -1.0, f"Breakdown below {rng_low:.0f}", "down"
        elif abs(rng_high - price) <= band:
            score, status, breakout = -0.4, f"Testing resistance {rng_high:.0f}", "none"
        elif abs(price - rng_low) <= band:
            score, status, breakout = 0.4, f"Holding support {rng_low:.0f}", "none"
        else:
            score = 0.3 if (price - rng_low) < (rng_high - price) else -0.3
            status, breakout = f"Inside {rng_low:.0f}–{rng_high:.0f}", "none"

        return {
            "available": True, "days": len(days),
            "resistance": round(rng_high, 2), "support": round(rng_low, 2),
            "spot": round(price, 2), "breakout": breakout,
            "bias": _bias_from(score), "arrow": _arrow(_bias_from(score)),
            "detail": status,
        }

    # ── Cumulative volume (reuse the CumulativeVolume strategy engine) ──
    def _cumulative_volume(self, authenticated: bool) -> dict:
        try:
            if self._cv is None:
                from strategies.base_strategy import StrategyConfig
                from strategies.cumulative_volume import CumulativeVolumeStrategy
                cfg = StrategyConfig(name="market_pulse_cv", instruments=["NSE:NIFTY 50"])
                self._cv = CumulativeVolumeStrategy(cfg, self.broker)
            else:
                self._cv.broker = self.broker
            data = self._cv.compute(broker_authenticated=authenticated)
            trend = str(data.get("trend_bias", "Neutral"))
            bias = "Bullish" if trend.lower().startswith("bull") else \
                   "Bearish" if trend.lower().startswith("bear") else "Neutral"
            return {
                "available": True, "value": data.get("last_cumulative_volume", 0),
                "trend": trend, "bias": bias, "arrow": _arrow(bias),
                "is_demo": bool(data.get("is_demo")),
                "detail": f"Cumulative signed volume {int(data.get('last_cumulative_volume', 0)):,} — {trend}",
            }
        except Exception as exc:
            logger.debug("market_pulse cumulative volume failed: %s", exc)
            return {"available": False, "bias": "Neutral", "arrow": "flat"}

    # ── assemble ──
    def snapshot(self, authenticated: bool = True, force: bool = False) -> dict:
        with self._lock:
            now = datetime.now()
            if not force and self._cache and self._cache_at and (now - self._cache_at).total_seconds() < 30:
                return self._cache
            token = self._index_token()
            if not token:
                return {"status": "error", "message": "NIFTY index token not resolvable"}
            spot = self._spot()

            # prev close (yesterday's daily close)
            prev_close = 0.0
            day_change = 0.0
            try:
                drows = self.broker.get_historical_data(
                    token, datetime.combine((now - timedelta(days=8)).date(), MARKET_OPEN),
                    now, "day") or []
                if len(drows) >= 2:
                    prev_close = float(drows[-2].get("close", 0) or 0)
                    ref = spot or float(drows[-1].get("close", 0) or 0)
                    day_change = ((ref - prev_close) / prev_close * 100.0) if prev_close else 0.0
            except Exception:
                pass

            dma = self._dma(token, spot)
            vwap = self._vwap_block(token, spot)
            psych = self._psychological(spot, prev_close)
            gann = self._gann(spot, prev_close)
            five_dfh = self._five_day_first_hour(token, spot, prev_close)
            cumv = self._cumulative_volume(authenticated)

            signals = {"cumulative_volume": cumv, "dma": dma, "vwap": vwap,
                       "psychological": psych, "gann": gann, "five_day_fh": five_dfh}
            biases = [v.get("bias", "Neutral") for v in signals.values() if v.get("available")]
            bull = sum(1 for b in biases if b == "Bullish")
            bear = sum(1 for b in biases if b == "Bearish")
            net = bull - bear
            verdict = _bias_from(net)

            out = {
                "status": "ok",
                "spot": round(spot, 2), "prev_close": round(prev_close, 2),
                "day_change_pct": round(day_change, 2),
                "indices": self._indices(),
                "fii_dii": self._fii_dii(),
                "signals": signals,
                "confirmation": {"bullish": bull, "bearish": bear,
                                 "neutral": len(biases) - bull - bear,
                                 "total": len(biases), "net": net, "verdict": verdict,
                                 "arrow": _arrow(verdict)},
                "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._cache, self._cache_at = out, now
            return out
