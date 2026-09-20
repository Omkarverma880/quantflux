"""
My Equity Workspace — the read side.

Turns the saved list into the table (price, RSI, extremes, volume flow, sensitivity, level
watch) and any one stock into an X-ray (chart with bands, indicator cards, entry zones with
their tested record, and headlines).

Read-only against the market: quotes and candles only, never an order. Daily history comes
from the incremental Parquet cache, so a refresh costs one historical call per stock plus a
single bulk quote call for the whole list.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from core.logger import get_logger
from research.my_equity import cache as CACHE
from research.my_equity import metrics as MX
from research.my_equity import news as NEWS
from research.my_equity import store as ST
from research.my_equity import zones as ZN

logger = get_logger("research.my_equity.service")

QUOTE_CHUNK = 200
ROWS_TTL_S = 20             # a re-render within this window reuses the last build
CHART_BARS = 220            # daily candles sent to the chart
INTRADAY_INTERVAL = "15minute"
INTRADAY_DAYS = 6


def _q(exchange: str, symbol: str) -> str:
    return f"{(exchange or 'NSE').upper()}:{symbol.upper()}"


class MyEquityService:
    def __init__(self, broker=None, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self._lock = threading.Lock()
        self._inst: dict[str, dict] = {}          # exchange → {tradingsymbol: {...}}
        self._inst_day: dict[str, date] = {}
        self._rows_cache: dict[int, tuple[float, dict]] = {}

    # ── instruments ──────────────────────────────────────────────────
    def _equities(self, exchange: str) -> dict:
        """Day-cached cash-equity instruments of one exchange, with the company name."""
        today = date.today()
        if self._inst_day.get(exchange) == today and exchange in self._inst:
            return self._inst[exchange]
        out: dict[str, dict] = {}
        try:
            for i in self.broker.get_instruments(exchange) or []:
                if i.get("instrument_type") == "EQ" and i.get("segment") == exchange:
                    ts = i.get("tradingsymbol")
                    if ts:
                        out[ts] = {"symbol": ts, "token": int(i["instrument_token"]),
                                   "exchange": exchange, "company": (i.get("name") or ts).strip()}
        except Exception as exc:
            logger.error("instrument dump %s failed: %s", exchange, exc)
            return self._inst.get(exchange, {})
        self._inst[exchange], self._inst_day[exchange] = out, today
        return out

    def token_for(self, s) -> Optional[int]:
        """The stock's instrument token, looked up once and saved back on the row."""
        if s.token:
            return int(s.token)
        if self.broker is None:
            return None
        hit = self.resolve(s.symbol, s.exchange)
        return int(hit["token"]) if hit else None

    def resolve(self, symbol: str, exchange: Optional[str] = None) -> Optional[dict]:
        """Find a cash equity by trading symbol — NSE first, then BSE, unless one is named."""
        symbol = (symbol or "").strip().upper()
        for ex in ([exchange.upper()] if exchange else ["NSE", "BSE"]):
            hit = self._equities(ex).get(symbol)
            if hit:
                return dict(hit)
        return None

    def search(self, q: str, limit: int = 15) -> list[dict]:
        """Type-ahead over NSE and BSE: symbol prefix first, then company-name matches."""
        q = (q or "").strip().upper()
        if len(q) < 1:
            return []
        prefix, contains, byname = [], [], []
        for ex in ("NSE", "BSE"):
            for ts, rec in self._equities(ex).items():
                if ts == q or ts.startswith(q):
                    prefix.append(rec)
                elif q in ts:
                    contains.append(rec)
                elif q in (rec["company"] or "").upper():
                    byname.append(rec)
        seen, out = set(), []
        for rec in (sorted(prefix, key=lambda r: (len(r["symbol"]), r["symbol"]))
                    + sorted(contains, key=lambda r: (len(r["symbol"]), r["symbol"]))
                    + sorted(byname, key=lambda r: r["symbol"])):
            if rec["symbol"] in seen:               # prefer the NSE listing
                continue
            seen.add(rec["symbol"])
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    # ── market data ──────────────────────────────────────────────────
    def quotes(self, keys: list[str]) -> dict:
        out: dict = {}
        for i in range(0, len(keys), QUOTE_CHUNK):
            chunk = keys[i:i + QUOTE_CHUNK]
            try:
                out.update(self.broker.get_quote(chunk) or {})
            except Exception as exc:
                logger.warning("quote failed for %d symbols: %s", len(chunk), exc)
        return out

    def _daily(self, symbol: str, token: int, exchange: str, refresh: bool) -> pd.DataFrame:
        if not token or (refresh and self.broker is None):
            refresh = False
        return CACHE.daily(self.broker, symbol, token, exchange, refresh=refresh)

    # ── the table ────────────────────────────────────────────────────
    def rows(self, db, user_id: int, refresh: bool = True, force: bool = False) -> dict:
        hit = self._rows_cache.get(user_id)
        if hit and not force and refresh and time.time() - hit[0] < ROWS_TTL_S:
            return {**hit[1], "cached": True}
        stocks = ST.list_stocks(db, user_id)
        if not stocks:
            return {"status": "ok", "rows": [], "count": 0, "connected": self.broker is not None,
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        quotes = self.quotes([_q(s.exchange, s.symbol) for s in stocks]) if self.broker else {}
        rows, problems = [], []
        for s in stocks:
            try:
                rows.append(self._row(db, s, quotes.get(_q(s.exchange, s.symbol)) or {}, refresh))
            except Exception as exc:
                logger.error("row failed for %s: %s", s.symbol, exc)
                problems.append(f"{s.symbol}: {str(exc)[:120]}")
                rows.append({**ST.to_dict(s), "error": str(exc)[:160]})
        out = {"status": "ok", "rows": rows, "count": len(rows), "problems": problems,
               "connected": self.broker is not None,
               "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        self._rows_cache[user_id] = (time.time(), out)
        return out

    def _row(self, db, s, quote: dict, refresh: bool) -> dict:
        base = ST.to_dict(s)
        token = self.token_for(s)
        if token and token != s.token:
            ST.update(db, s.user_id, s.id, token=token)
        raw = self._daily(s.symbol, token, s.exchange, refresh)
        ltp = float(quote.get("last_price") or 0) or None
        day = quote.get("ohlc") or {}
        if raw.empty:
            return {**base, "ltp": ltp, "history": False,
                    "message": "no daily history yet — refresh once while Zerodha is connected"}
        d = MX.enrich(raw)
        px = ltp or float(d["close"].iloc[-1])
        ext = MX.extremes(d, px)
        flow = MX.volume_flow(d, quote.get("volume"))
        snap = MX.snapshot(d, px)
        sens = MX.sensitivity(d, px, ext, flow)
        watch = self._levels(db, s, px, day)
        return {
            **base, "history": True, "ltp": px, "token": token,
            "prev_close": float(day.get("close") or 0) or snap.get("prev_close"),
            "change_pct": MX._pct(px, day.get("close") or snap.get("prev_close")),
            "day_high": float(day.get("high") or 0) or None, "day_low": float(day.get("low") or 0) or None,
            "rsi": snap.get("rsi"), "rsi_state": _rsi_state(snap.get("rsi")),
            "extremes": ext, "volume": flow, "sensitivity": sens, "snapshot": snap,
            "watch": watch, "alerts": _alerts(snap, ext, flow, watch),
            "days_since_added": (date.today() - s.added_on).days if s.added_on else None,
        }

    def _levels(self, db, s, ltp: float, day: dict) -> dict:
        """How close price is to the levels written down when the stock was added."""
        levels = [float(x) for x in (s.levels or [])]
        if not levels or not ltp:
            return {"levels": levels, "touched": False, "touched_today": False}
        tol = float(s.touch_pct if s.touch_pct is not None else 0.25)
        rows = [{"level": lv, "distance_pct": round((ltp - lv) / lv * 100, 2)} for lv in levels]
        nearest = min(rows, key=lambda r: abs(r["distance_pct"]))
        touched = abs(nearest["distance_pct"]) <= tol
        lo, hi = float(day.get("low") or 0), float(day.get("high") or 0)
        today_hit = [r["level"] for r in rows if lo and hi and lo <= r["level"] <= hi]
        if touched:
            ST.mark_touch(db, s, nearest["level"])
        return {"levels": levels, "rows": rows, "nearest": nearest, "tolerance_pct": tol,
                "touched": bool(touched), "touched_today": bool(today_hit),
                "touched_today_levels": today_hit}

    # ── the X-ray ────────────────────────────────────────────────────
    def xray(self, db, user_id: int, stock_id: int, *, with_news: bool = True,
             with_intraday: bool = True, refresh: bool = True) -> dict:
        s = ST.get(db, user_id, stock_id)
        if s is None:
            return {"status": "error", "message": "that stock is not in your workspace"}
        quote = {}
        if self.broker:
            quote = (self.quotes([_q(s.exchange, s.symbol)]) or {}).get(_q(s.exchange, s.symbol)) or {}
        token = self.token_for(s)
        raw = self._daily(s.symbol, token, s.exchange, refresh)
        if raw.empty:
            return {"status": "error", "message": "no daily history for this stock yet",
                    "stock": ST.to_dict(s)}
        d = MX.enrich(raw)
        ltp = float(quote.get("last_price") or 0) or float(d["close"].iloc[-1])
        day = quote.get("ohlc") or {}
        ext = MX.extremes(d, ltp)
        flow = MX.volume_flow(d, quote.get("volume"), bars=20)
        snap = MX.snapshot(d, ltp)
        sens = MX.sensitivity(d, ltp, ext, flow)
        zres = ZN.entry_zones(d, ltp, [float(x) for x in (s.levels or [])])
        entry = ZN.best_entry(d, ltp, zres.get("zones", []), sens)
        out = {
            "status": "ok", "stock": ST.to_dict(s), "company": s.company, "ltp": ltp,
            "change_pct": MX._pct(ltp, day.get("close") or snap.get("prev_close")),
            "day": {"open": day.get("open"), "high": day.get("high"), "low": day.get("low"),
                    "prev_close": day.get("close"), "volume": quote.get("volume")},
            "snapshot": snap, "extremes": ext, "volume": flow, "sensitivity": sens,
            "zones": zres.get("zones", []), "setups": zres.get("setups", {}),
            "entry": entry, "atr": zres.get("atr"), "horizon": zres.get("horizon"),
            "watch": self._levels(db, s, ltp, day),
            "daily": _chart_payload(d.tail(CHART_BARS)),
            "cache": CACHE.status(s.symbol, s.exchange),
        }
        if with_intraday and self.broker and token:
            out["intraday"] = self._intraday(token)
        if with_news:
            out["news"] = NEWS.headlines(s.symbol, s.company)
        return out

    def _intraday(self, token: int) -> dict:
        """The last couple of sessions at 15 minutes, with the session VWAP band."""
        end = date.today()
        try:
            rows = self.broker.get_historical_data(token, end - timedelta(days=INTRADAY_DAYS),
                                                   end, INTRADAY_INTERVAL) or []
        except Exception as exc:
            return {"available": False, "message": str(exc)[:160]}
        if not rows:
            return {"available": False, "message": "no intraday candles"}
        df = pd.DataFrame(rows)
        ts = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert("Asia/Kolkata")
        df["timestamp"] = ts.dt.tz_localize(None)
        df["session"] = df["timestamp"].dt.date
        keep = sorted(df["session"].unique())[-2:]
        df = df[df["session"].isin(keep)].reset_index(drop=True)
        bands = (df.groupby("session", group_keys=False)
                   .apply(lambda g: MX.session_vwap_bands(g, 1.5)).reset_index(drop=True))
        df = pd.concat([df.reset_index(drop=True), bands], axis=1)
        return {
            "available": True, "interval": INTRADAY_INTERVAL,
            "sessions": [str(x) for x in keep],
            "candles": [{"t": r.timestamp.strftime("%d %b %H:%M"), "open": MX._f(r.open),
                         "high": MX._f(r.high), "low": MX._f(r.low), "close": MX._f(r.close),
                         "volume": MX._f(r.volume)} for r in df.itertuples()],
            "overlays": [
                {"key": "vwap", "label": "VWAP", "color": "#38bdf8",
                 "values": [MX._f(v) for v in df["vwap"]]},
                {"key": "vwap_upper", "label": "VWAP +1.5σ", "color": "#64748b",
                 "values": [MX._f(v) for v in df["vwap_upper"]]},
                {"key": "vwap_lower", "label": "VWAP −1.5σ", "color": "#64748b",
                 "values": [MX._f(v) for v in df["vwap_lower"]]},
            ],
        }


def _chart_payload(d: pd.DataFrame) -> dict:
    return {
        "candles": [{"t": str(r.date), "open": MX._f(r.open), "high": MX._f(r.high),
                     "low": MX._f(r.low), "close": MX._f(r.close), "volume": MX._f(r.volume)}
                    for r in d.itertuples()],
        "overlays": [
            {"key": "ema20", "label": "20 EMA", "color": "#f59e0b", "values": [MX._f(v) for v in d["ema20"]]},
            {"key": "ema50", "label": "50 EMA", "color": "#a78bfa", "values": [MX._f(v) for v in d["ema50"]]},
            {"key": "ema200", "label": "200 EMA", "color": "#22d3ee", "values": [MX._f(v) for v in d["ema200"]]},
            {"key": "bb_upper", "label": "Bollinger +2σ", "color": "#64748b", "values": [MX._f(v) for v in d["bb_upper"]]},
            {"key": "bb_lower", "label": "Bollinger −2σ", "color": "#64748b", "values": [MX._f(v) for v in d["bb_lower"]]},
            {"key": "vwap", "label": "20d VWAP", "color": "#34d399", "values": [MX._f(v) for v in d["vwap"]]},
        ],
        "rsi": [MX._f(v) for v in d["rsi"]],
        "volume": [MX._f(v) for v in d["volume"]],
    }


def _rsi_state(v: Optional[float]) -> str:
    if v is None:
        return "none"
    if v < 30:
        return "oversold"
    if v > 70:
        return "overbought"
    if v < 45:
        return "weak"
    if v > 55:
        return "strong"
    return "neutral"


def _alerts(snap: dict, ext: dict, flow: dict, watch: dict) -> list[dict]:
    """The short, blinking-worthy facts: level touches, RSI extremes, range edges, volume."""
    out: list[dict] = []
    lv = (watch.get("nearest") or {}).get("level")
    if watch.get("touched") and lv is not None:
        out.append({"key": "level", "tone": "alert", "text": f"At your research level {lv:g}"})
    elif watch.get("touched_today"):
        lv = ", ".join(f"{x:g}" for x in watch.get("touched_today_levels", []))
        out.append({"key": "level_today", "tone": "warn", "text": f"Touched {lv} earlier today"})
    rsi = snap.get("rsi")
    if rsi is not None and rsi < 30:
        out.append({"key": "rsi", "tone": "alert", "text": f"RSI {rsi:.0f} — oversold"})
    elif rsi is not None and rsi > 70:
        out.append({"key": "rsi", "tone": "warn", "text": f"RSI {rsi:.0f} — overbought"})
    if ext.get("from_52w_high") is not None and ext["from_52w_high"] >= -1:
        out.append({"key": "52w", "tone": "good", "text": "At its 52-week high"})
    if ext.get("from_52w_low") is not None and ext["from_52w_low"] <= 1:
        out.append({"key": "52wl", "tone": "warn", "text": "At its 52-week low"})
    if (flow.get("vs_avg20") or 0) >= 2:
        out.append({"key": "vol", "tone": "good", "text": f"Volume {flow['vs_avg20']}× the 20-day average"})
    if snap.get("bb_squeeze"):
        out.append({"key": "squeeze", "tone": "info", "text": "Bollinger squeeze — bands at their tightest"})
    return out
