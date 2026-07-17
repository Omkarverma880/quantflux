"""
Research module #5 — NIFTY Sentiment Analyzer.

Read-only market-bias analytics built on the live prices of NIFTY 50's most
heavily-weighted constituents. It NEVER places orders and does not touch any
trading strategy — it only reads market data through the existing per-user
Broker (batched ``quote()`` for the fast path, time-boxed historical for the
technical analytics table).

Two independent analyzers:

  1. Top-Stocks sentiment — the top-weighted constituents (≈53% of the index)
     treated as a 100% universe. Each stock's weighted contribution
     (weight × %change) is combined so ONE red heavyweight never single-handedly
     flips the bias — the whole basket decides.

  2. Sector sentiment — the full configured universe grouped by sector, with a
     weighted sector score.

Plus a live analytics table (5-min volume, 20/200 EMA, VWAP, previous-day VWAP,
trend) for the major movers.

Efficiency: the sentiment cards + contributor/sector tables come from a SINGLE
batched ``quote()`` call. The heavier per-stock technical analytics use one
historical call per symbol, time-boxed and TTL-cached so a refresh never hangs
or hammers Zerodha's rate limit (same guard pattern as the option-chain module).
"""
from __future__ import annotations

import json
import threading
import time as _time
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from config import settings
from core.broker import Broker
from core.logger import get_logger

logger = get_logger("research.nifty_sentiment")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# Optional on-disk override (durable, editable without redeploy). If absent the
# embedded defaults below are used.
CONFIG_FILE = settings.DATA_DIR / "research" / "nifty_sentiment.json"

# ── Default NIFTY 50 constituent universe ───────────────────────────────
# weight = latest published free-float index weight (%). Sector uses the
# index's official sector buckets. The top 10 (~53.6%) match the NSE factsheet;
# the rest are included so the sector analyzer has real breadth. The engine
# re-normalises weights over whatever subset it analyses, so it is always
# internally consistent even if the exact tail weights drift.
DEFAULT_CONSTITUENTS: list[dict] = [
    {"symbol": "HDFCBANK",   "name": "HDFC Bank",            "weight": 11.18, "sector": "Financial Services"},
    {"symbol": "ICICIBANK",  "name": "ICICI Bank",           "weight": 9.01,  "sector": "Financial Services"},
    {"symbol": "RELIANCE",   "name": "Reliance Industries",  "weight": 8.00,  "sector": "Oil, Gas & Consumable Fuels"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel",        "weight": 5.15,  "sector": "Telecommunication"},
    {"symbol": "LT",         "name": "Larsen & Toubro",      "weight": 4.44,  "sector": "Construction"},
    {"symbol": "SBIN",       "name": "State Bank of India",  "weight": 3.88,  "sector": "Financial Services"},
    {"symbol": "AXISBANK",   "name": "Axis Bank",            "weight": 3.54,  "sector": "Financial Services"},
    {"symbol": "INFY",       "name": "Infosys",              "weight": 3.21,  "sector": "Information Technology"},
    {"symbol": "KOTAKBANK",  "name": "Kotak Mahindra Bank",  "weight": 2.64,  "sector": "Financial Services"},
    {"symbol": "ITC",        "name": "ITC",                  "weight": 2.53,  "sector": "Fast Moving Consumer Goods"},
    {"symbol": "TCS",        "name": "Tata Consultancy Svc", "weight": 3.55,  "sector": "Information Technology"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance",        "weight": 2.10,  "sector": "Financial Services"},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever",   "weight": 2.20,  "sector": "Fast Moving Consumer Goods"},
    {"symbol": "MARUTI",     "name": "Maruti Suzuki",        "weight": 1.90,  "sector": "Automobile and Auto Components"},
    {"symbol": "M&M",        "name": "Mahindra & Mahindra",  "weight": 2.15,  "sector": "Automobile and Auto Components"},
    {"symbol": "SUNPHARMA",  "name": "Sun Pharma",           "weight": 1.80,  "sector": "Healthcare"},
    {"symbol": "TATAMOTORS", "name": "Tata Motors",          "weight": 1.55,  "sector": "Automobile and Auto Components"},
    {"symbol": "NTPC",       "name": "NTPC",                 "weight": 1.50,  "sector": "Power"},
    {"symbol": "HCLTECH",    "name": "HCL Technologies",     "weight": 1.45,  "sector": "Information Technology"},
    {"symbol": "TITAN",      "name": "Titan Company",        "weight": 1.35,  "sector": "Consumer Durables"},
    {"symbol": "ULTRACEMCO", "name": "UltraTech Cement",     "weight": 1.30,  "sector": "Construction Materials"},
    {"symbol": "TATASTEEL",  "name": "Tata Steel",           "weight": 1.20,  "sector": "Metals & Mining"},
    {"symbol": "POWERGRID",  "name": "Power Grid Corp",      "weight": 1.10,  "sector": "Power"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints",         "weight": 1.00,  "sector": "Consumer Durables"},
]

DEFAULT_CONFIG = {
    "top_n": 10,                    # top-weighted stocks treated as the 100% universe
    "refresh_interval": 5,          # seconds (front-end auto-refresh)
    "sentiment_threshold": 0.05,    # |weighted %chg| below this → Neutral
    "enable_sector": True,
    "enable_trend": True,
    "show_only_top": False,
}


def _ema_last(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return 0.0
    k = 2.0 / (period + 1.0)
    e = float(values[0])
    for v in values[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def _bias(score: float, threshold: float) -> str:
    if score > threshold:
        return "Bullish"
    if score < -threshold:
        return "Bearish"
    return "Neutral"


def _gauge(score: float) -> float:
    """Map a weighted %-change score to a 0–100 sentiment gauge (50 = neutral)."""
    g = 50.0 + score * 25.0          # ±2% → 0 / 100
    return round(max(0.0, min(100.0, g)), 1)


def _trend(price: float, ema200: float, vwap: float) -> str:
    if price <= 0:
        return "Neutral"
    if ema200 > 0 and vwap > 0:
        if price > ema200 and price > vwap:
            return "Strong Bullish"
        if price < ema200 and price < vwap:
            return "Strong Bearish"
    return "Neutral"


class NiftySentiment:
    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        # analytics TTL cache
        self._analytics_cache: dict[str, dict] = {}
        self._analytics_at: float = 0.0

    # ── config ──
    def load_config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        try:
            if CONFIG_FILE.exists():
                cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
        except Exception as exc:
            logger.debug("nifty_sentiment config read failed: %s", exc)
        return cfg

    def save_config(self, partial: dict) -> dict:
        cfg = self.load_config()
        for k, v in (partial or {}).items():
            if k in DEFAULT_CONFIG and v is not None:
                cfg[k] = v
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
        except Exception as exc:
            logger.error("nifty_sentiment config save failed: %s", exc)
        return cfg

    def _constituents(self) -> list[dict]:
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text())
                cons = data.get("constituents")
                if isinstance(cons, list) and cons:
                    return cons
        except Exception:
            pass
        return DEFAULT_CONSTITUENTS

    # ── fast path: batched quote → sentiment cards + tables ──
    def snapshot(self, top_n: Optional[int] = None) -> dict:
        with self._lock:
            cfg = self.load_config()
            universe = sorted(self._constituents(), key=lambda c: c.get("weight", 0), reverse=True)
            if not universe:
                return {"status": "error", "message": "No constituents configured"}
            n = int(top_n or cfg.get("top_n", 10))
            n = max(1, min(n, len(universe)))
            top = universe[:n]

            keys = [f"NSE:{c['symbol']}" for c in universe]
            try:
                quotes = self.broker.get_quote(keys) or {}
            except Exception as exc:
                logger.error("nifty_sentiment quote failed: %s", exc)
                return {"status": "error", "message": f"Quote fetch failed: {exc}"}

            def _row(c: dict) -> Optional[dict]:
                q = quotes.get(f"NSE:{c['symbol']}") or {}
                ltp = float(q.get("last_price", 0) or 0)
                ohlc = q.get("ohlc") or {}
                close = float(ohlc.get("close", 0) or 0)
                if ltp <= 0 and close <= 0:
                    return None
                pct = ((ltp - close) / close * 100.0) if close else float(q.get("net_change", 0) or 0)
                return {
                    "symbol": c["symbol"], "name": c.get("name", c["symbol"]),
                    "sector": c.get("sector", "—"), "weight": float(c.get("weight", 0)),
                    "ltp": round(ltp, 2), "prev_close": round(close, 2),
                    "change_pct": round(pct, 2),
                    "volume": int(float(q.get("volume", 0) or 0)),
                    "day_open": round(float(ohlc.get("open", 0) or 0), 2),
                    "day_high": round(float(ohlc.get("high", 0) or 0), 2),
                    "day_low": round(float(ohlc.get("low", 0) or 0), 2),
                }

            # ── Top-stocks analyzer (top_n treated as 100% universe) ──
            top_rows = [r for r in (_row(c) for c in top) if r]
            wsum = sum(r["weight"] for r in top_rows) or 1.0
            pos = neg = 0.0
            for r in top_rows:
                frac = r["weight"] / wsum                 # re-normalised to 100%
                contrib = frac * r["change_pct"]          # weighted contribution
                r["norm_weight"] = round(frac * 100.0, 2)
                r["weighted_score"] = round(contrib, 4)
                r["contribution_pct"] = round(contrib, 3)
                r["trend"] = "Bullish" if r["change_pct"] > 0 else "Bearish" if r["change_pct"] < 0 else "Neutral"
                if contrib >= 0:
                    pos += contrib
                else:
                    neg += contrib
            top_score = round(pos + neg, 3)
            # Market-impact ranking: |weighted contribution| — not raw %change.
            top_rows.sort(key=lambda r: abs(r["weighted_score"]), reverse=True)
            for i, r in enumerate(top_rows, 1):
                r["impact_rank"] = i

            top_card = {
                "score": top_score, "gauge": _gauge(top_score),
                "bias": _bias(top_score, cfg["sentiment_threshold"]),
                "positive_score": round(pos, 3), "negative_score": round(neg, 3),
                "advancers": sum(1 for r in top_rows if r["change_pct"] > 0),
                "decliners": sum(1 for r in top_rows if r["change_pct"] < 0),
                "universe": n, "coverage_pct": round(sum(c["weight"] for c in top), 2),
            }

            # ── Sector analyzer (full configured universe) ──
            all_rows = [r for r in (_row(c) for c in universe) if r]
            sectors: dict[str, dict] = {}
            for r in all_rows:
                s = sectors.setdefault(r["sector"], {"sector": r["sector"], "weight": 0.0,
                                                     "wpct": 0.0, "sum_pct": 0.0, "count": 0,
                                                     "adv": 0, "dec": 0})
                s["weight"] += r["weight"]
                s["wpct"] += r["weight"] * r["change_pct"]
                s["sum_pct"] += r["change_pct"]
                s["count"] += 1
                if r["change_pct"] > 0:
                    s["adv"] += 1
                elif r["change_pct"] < 0:
                    s["dec"] += 1
            total_w = sum(s["weight"] for s in sectors.values()) or 1.0
            sec_pos = sec_neg = 0.0
            sector_rows = []
            for s in sectors.values():
                weighted_perf = s["wpct"] / s["weight"] if s["weight"] else 0.0
                avg_perf = s["sum_pct"] / s["count"] if s["count"] else 0.0
                frac = s["weight"] / total_w
                contrib = frac * weighted_perf
                if contrib >= 0:
                    sec_pos += contrib
                else:
                    sec_neg += contrib
                sector_rows.append({
                    "sector": s["sector"], "weight": round(s["weight"], 2),
                    "norm_weight": round(frac * 100.0, 2), "count": s["count"],
                    "avg_performance": round(avg_perf, 2),
                    "weighted_performance": round(weighted_perf, 2),
                    "contribution": round(contrib, 3),
                    "strength_score": round(_gauge(weighted_perf), 1),
                    "advancers": s["adv"], "decliners": s["dec"],
                    "bias": _bias(weighted_perf, cfg["sentiment_threshold"]),
                })
            sector_rows.sort(key=lambda x: abs(x["contribution"]), reverse=True)
            sector_score = round(sec_pos + sec_neg, 3)
            sector_card = {
                "score": sector_score, "gauge": _gauge(sector_score),
                "bias": _bias(sector_score, cfg["sentiment_threshold"]),
                "positive_score": round(sec_pos, 3), "negative_score": round(sec_neg, 3),
                "sectors": len(sector_rows),
            }

            # ── Overall (blend of the two independent analyzers) ──
            overall_score = round((top_score + sector_score) / 2.0, 3)
            overall = {
                "score": overall_score, "gauge": _gauge(overall_score),
                "bias": _bias(overall_score, cfg["sentiment_threshold"]),
            }

            return {
                "status": "ok",
                "fetched_at": datetime.now().strftime("%H:%M:%S"),
                "overall": overall,
                "top_card": top_card,
                "sector_card": sector_card,
                "top_stocks": top_rows,
                "sectors": sector_rows,
                "config": cfg,
            }

    # ── heavy path: per-stock technicals (time-boxed + TTL cache) ──
    def analytics(self, top_n: Optional[int] = None, budget_seconds: float = 7.0,
                  ttl_seconds: float = 45.0) -> dict:
        cfg = self.load_config()
        universe = sorted(self._constituents(), key=lambda c: c.get("weight", 0), reverse=True)
        n = int(top_n or cfg.get("top_n", 10))
        n = max(1, min(n, len(universe)))
        rows_cfg = universe[:n]

        now = _time.monotonic()
        fresh = (now - self._analytics_at) < ttl_seconds
        start = now
        today = date.today()
        frm = datetime.combine(today - timedelta(days=6), MARKET_OPEN)
        to = datetime.now()

        out = []
        for c in rows_cfg:
            sym = c["symbol"]
            cached = self._analytics_cache.get(sym)
            if fresh and cached:
                out.append(cached)
                continue
            if _time.monotonic() - start > budget_seconds:
                # out of budget — serve stale cache if we have it, else a stub
                out.append(cached or {"symbol": sym, "name": c.get("name", sym),
                                      "weight": c.get("weight", 0), "pending": True})
                continue
            try:
                token = self._token_for(sym)
                candles = self.broker.get_historical_data(token, frm, to, "minute") if token else []
            except Exception as exc:
                logger.debug("nifty_sentiment analytics fetch %s failed: %s", sym, exc)
                candles = []
            row = self._analyse_candles(c, candles or [])
            self._analytics_cache[sym] = row
            out.append(row)

        if not fresh:
            self._analytics_at = _time.monotonic()

        return {"status": "ok", "fetched_at": datetime.now().strftime("%H:%M:%S"), "rows": out}

    def _analyse_candles(self, c: dict, candles: list[dict]) -> dict:
        base = {"symbol": c["symbol"], "name": c.get("name", c["symbol"]),
                "sector": c.get("sector", "—"), "weight": float(c.get("weight", 0))}
        if not candles:
            base.update({"ltp": 0, "change_pct": 0, "vol_5min": 0, "ema20": 0,
                         "ema200": 0, "vwap": 0, "prev_vwap": 0, "trend": "Neutral"})
            return base
        today = date.today()
        closes = [float(x.get("close", 0) or 0) for x in candles]
        ltp = closes[-1]

        # split today vs previous session for VWAP / prev-VWAP
        def _cdate(x):
            d = x.get("date")
            return d.date() if hasattr(d, "date") else today
        todays = [x for x in candles if _cdate(x) == today]
        prev_days = sorted({_cdate(x) for x in candles if _cdate(x) < today})
        prev_day = prev_days[-1] if prev_days else None
        prevs = [x for x in candles if prev_day and _cdate(x) == prev_day]

        def _vwap(rows: list[dict]) -> float:
            pv = v = tp_sum = 0.0
            nn = 0
            for x in rows:
                h = float(x.get("high", 0) or 0); l = float(x.get("low", 0) or 0)
                cl = float(x.get("close", 0) or 0); vol = float(x.get("volume", 0) or 0)
                tp = (h + l + cl) / 3.0
                pv += tp * vol; v += vol; tp_sum += tp; nn += 1
            if v > 0:
                return pv / v
            return tp_sum / nn if nn else 0.0

        vwap = _vwap(todays or candles[-1:])
        prev_vwap = _vwap(prevs) if prevs else 0.0
        ema20 = _ema_last(closes, 20)
        ema200 = _ema_last(closes, 200)
        vol_5min = sum(float(x.get("volume", 0) or 0) for x in (todays[-5:] or candles[-5:]))
        prev_close = float((prevs[-1].get("close") if prevs else todays[0].get("open") if todays else ltp) or ltp)
        pct = ((ltp - prev_close) / prev_close * 100.0) if prev_close else 0.0

        base.update({
            "ltp": round(ltp, 2), "change_pct": round(pct, 2),
            "vol_5min": int(vol_5min), "ema20": round(ema20, 2), "ema200": round(ema200, 2),
            "vwap": round(vwap, 2), "prev_vwap": round(prev_vwap, 2),
            "trend": _trend(ltp, ema200, vwap),
        })
        return base

    # ── instrument-token resolver (cached for the day) ──
    _tok_cache: dict[str, int] = {}
    _tok_date: Optional[date] = None

    def _token_for(self, symbol: str) -> Optional[int]:
        today = date.today()
        if NiftySentiment._tok_date != today:
            NiftySentiment._tok_cache = {}
            NiftySentiment._tok_date = today
        if symbol in NiftySentiment._tok_cache:
            return NiftySentiment._tok_cache[symbol]
        try:
            for inst in self.broker.get_instruments("NSE"):
                ts = inst.get("tradingsymbol")
                if ts:
                    NiftySentiment._tok_cache[ts] = int(inst["instrument_token"])
        except Exception as exc:
            logger.debug("nifty_sentiment instrument load failed: %s", exc)
        return NiftySentiment._tok_cache.get(symbol)
