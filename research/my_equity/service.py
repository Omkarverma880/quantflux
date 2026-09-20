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
from research.my_equity import fundamentals as FN
from research.my_equity import levels as LV
from research.my_equity import metrics as MX
from research.my_equity import news as NEWS
from research.my_equity import store as ST
from research.my_equity import zones as ZN

logger = get_logger("research.my_equity.service")

QUOTE_CHUNK = 200
ROWS_TTL_S = 20             # a re-render within this window reuses the last build
SECTOR_FILLS_PER_CALL = 3   # look up a few missing sectors per refresh, never the whole list
# Daily candles cost one Zerodha call per stock and Zerodha allows ~3 a second, so a hundred
# stocks must never all refresh on one page load. Each call refreshes only the few stalest
# names; the rest are served from the Parquet cache with today's bar rebuilt from the quote,
# which is exact and free. A background warmer keeps the cache fresh between visits.
MAX_DAILY_REFRESH_PER_CALL = 8
DAILY_FRESH_S = 1800        # a symbol refreshed within half an hour is fresh enough
WARM_EVERY_S = 60           # the background warmer runs at most once a minute per user
# An investment is judged over quarters and a swing over days, so the entry-zone history is
# replayed over the horizon that matches what you said you are doing with the stock.
HORIZON_BY_CATEGORY = {"INVESTMENT": 60, "SWING": 10}


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
        self._index_cache = None                  # NIFTY closes for relative strength
        self._index_at = 0.0
        self._daily_at: dict[str, float] = {}     # exchange:symbol -> last candle refresh
        self._warm_at = 0.0

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
        if refresh:
            self._daily_at[f"{exchange}:{symbol}"] = time.time()
        return CACHE.daily(self.broker, symbol, token, exchange, refresh=refresh)

    def _stale(self, stocks, limit: int) -> set[int]:
        """The ids whose daily history gets refreshed on this call — the stalest first."""
        now = time.time()
        ages = sorted(((now - self._daily_at.get(f"{s.exchange}:{s.symbol}", 0.0), s.id)
                       for s in stocks), reverse=True)
        return {sid for age, sid in ages[:max(0, limit)] if age >= DAILY_FRESH_S}

    def warm(self, db, user_id: int, limit: int = 3) -> int:
        """Refresh a few stocks' daily history in the background, so page loads stay instant.

        Called from a one-second loop, so it holds itself to one pass a minute: a hundred-name
        workspace is fully refreshed in under an hour of market time without ever competing
        with the page for Zerodha's rate limit.
        """
        if self.broker is None or time.time() - self._warm_at < WARM_EVERY_S:
            return 0
        self._warm_at = time.time()
        stocks = ST.list_stocks(db, user_id)
        done = 0
        for s in stocks:
            if s.id not in self._stale(stocks, limit):
                continue
            try:
                self._daily(s.symbol, self.token_for(s), s.exchange, True)
                done += 1
            except Exception as exc:
                logger.debug("warm failed for %s: %s", s.symbol, exc)
        if done:
            self._rows_cache.pop(user_id, None)
        return done

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
        if refresh:
            self._fill_sectors(db, stocks)
        # one bulk quote covers every stock; only the stalest few also refresh their candles
        fetching = self._stale(stocks, MAX_DAILY_REFRESH_PER_CALL) if refresh else set()
        rows, problems = [], []
        for s in stocks:
            try:
                rows.append(self._row(db, s, quotes.get(_q(s.exchange, s.symbol)) or {},
                                      s.id in fetching))
            except Exception as exc:
                logger.error("row failed for %s: %s", s.symbol, exc)
                problems.append(f"{s.symbol}: {str(exc)[:120]}")
                rows.append({**ST.to_dict(s), "error": str(exc)[:160]})
        out = {"status": "ok", "rows": rows, "count": len(rows), "problems": problems,
               "connected": self.broker is not None, "summary": _summary(rows),
               "sectors": sorted({r.get("sector") for r in rows if r.get("sector")}),
               "history_refreshed": len(fetching), "pending_history": max(0, len(stocks) - len(fetching)),
               "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        self._rows_cache[user_id] = (time.time(), out)
        return out

    def _fill_sectors(self, db, stocks) -> None:
        """Look up the sector of a few stocks that do not have one yet (cached, best effort)."""
        missing = [s for s in stocks if not s.sector][:SECTOR_FILLS_PER_CALL]
        for s in missing:
            try:
                sector, industry = FN.sector_of(s.symbol, s.exchange)
                if sector:
                    ST.update(db, s.user_id, s.id, sector=sector, industry=industry,
                              sector_source="auto")
            except Exception as exc:
                logger.debug("sector lookup failed for %s: %s", s.symbol, exc)

    def _row(self, db, s, quote: dict, refresh: bool) -> dict:
        base = ST.to_dict(s)
        token = self.token_for(s)
        if token and token != s.token:
            ST.update(db, s.user_id, s.id, token=token)
        raw = _with_quote_today(self._daily(s.symbol, token, s.exchange, refresh), quote)
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
        watch = self._levels(db, s, d, px, day)
        return {
            **base, "history": True, "ltp": px, "token": token,
            "prev_close": float(day.get("close") or 0) or snap.get("prev_close"),
            "change_pct": MX._pct(px, day.get("close") or snap.get("prev_close")),
            "day_high": float(day.get("high") or 0) or None, "day_low": float(day.get("low") or 0) or None,
            "rsi": snap.get("rsi"), "rsi_state": _rsi_state(snap.get("rsi")),
            "high_52w": ext.get("high_52w"), "from_52w_high": ext.get("from_52w_high"),
            "range_pos_52w": ext.get("range_pos_52w"),
            "volume_ratio": flow.get("vs_avg20"),
            "bias": {"score": sens.get("score"), "label": sens.get("label"), "tone": sens.get("tone")},
            "watch": watch, "notes": _watch_notes(snap, ext, flow, watch),
            "days_since_added": (date.today() - s.added_on).days if s.added_on else None,
        }

    def _levels(self, db, s, d, ltp: float, day: dict) -> dict:
        """Level state and, for the ones that have triggered, the P&L since they did."""
        watch = LV.evaluate(d, s.levels, s.added_on, ltp,
                            float(s.touch_pct if s.touch_pct is not None else 0.25), day)
        if watch.get("touched") and watch.get("nearest"):
            ST.mark_touch(db, s, watch["nearest"]["level"])
        watch["line"] = LV.summary_line(watch)
        return watch

    # ── the X-ray ────────────────────────────────────────────────────
    def xray(self, db, user_id: int, stock_id: int, *, with_news: bool = True,
             with_fundamentals: bool = True, refresh: bool = True) -> dict:
        """Everything known about one stock, in one payload.

        Deliberately no price chart: the workspace is about what the numbers say, and a chart
        lives better in the broker's own terminal. What you get instead is the level record,
        the technical state, the fundamentals, the performance table, the live order book and
        the entry zones with their tested history.
        """
        s = ST.get(db, user_id, stock_id)
        if s is None:
            return {"status": "error", "message": "that stock is not in your workspace"}
        quote = {}
        if self.broker:
            quote = (self.quotes([_q(s.exchange, s.symbol)]) or {}).get(_q(s.exchange, s.symbol)) or {}
        token = self.token_for(s)
        raw = _with_quote_today(self._daily(s.symbol, token, s.exchange, refresh), quote)
        if raw.empty:
            return {"status": "error", "message": "no daily history for this stock yet",
                    "stock": ST.to_dict(s)}
        d = MX.enrich(raw)
        ltp = float(quote.get("last_price") or 0) or float(d["close"].iloc[-1])
        day = quote.get("ohlc") or {}
        category = ST.clean_category(s.category)
        horizon = HORIZON_BY_CATEGORY.get(category, 10)
        ext = MX.extremes(d, ltp)
        flow = MX.volume_flow(d, quote.get("volume"), bars=20)
        snap = MX.snapshot(d, ltp)
        sens = MX.sensitivity(d, ltp, ext, flow)
        watch = self._levels(db, s, d, ltp, day)
        zres = ZN.entry_zones(d, ltp, LV.prices(s.levels), horizon=horizon)
        entry = ZN.best_entry(d, ltp, zres.get("zones", []), sens, horizon=horizon)
        out = {
            "status": "ok", "stock": ST.to_dict(s), "company": s.company, "ltp": ltp,
            "category": category, "horizon": horizon,
            "change_pct": MX._pct(ltp, day.get("close") or snap.get("prev_close")),
            "day": {"open": day.get("open"), "high": day.get("high"), "low": day.get("low"),
                    "prev_close": day.get("close"), "volume": quote.get("volume")},
            "snapshot": snap, "extremes": ext, "volume": flow, "sensitivity": sens,
            "watch": watch, "notes": _watch_notes(snap, ext, flow, watch),
            "performance": MX.performance(d, ltp), "risk": MX.risk_stats(d, ltp),
            "seasonality": MX.seasonality(d), "pivots": MX.pivots(d),
            "order_book": MX.order_book(quote),
            "relative": MX.relative_strength(d, self._index_daily()),
            "zones": zres.get("zones", []), "setups": zres.get("setups", {}),
            "entry": entry, "atr": zres.get("atr"),
            "cache": CACHE.status(s.symbol, s.exchange),
        }
        if with_fundamentals:
            out["fundamentals"] = FN.fetch(s.symbol, s.exchange)
        if with_news:
            out["news"] = NEWS.headlines(s.symbol, s.company)
        return out

    def _index_daily(self):
        """NIFTY daily closes from the Market Store, for the relative-strength table.

        Optional by design: no index data in the store simply means that one table is empty.
        """
        if self._index_cache is not None and time.time() - self._index_at < 3600:
            return self._index_cache
        try:
            from research.market_store import store as MS
            df = MS.read("spot", "NIFTY")
            self._index_cache = df[["timestamp", "close"]] if not df.empty else None
        except Exception as exc:
            logger.debug("index series unavailable: %s", exc)
            self._index_cache = None
        self._index_at = time.time()
        return self._index_cache


def _with_quote_today(d: pd.DataFrame, quote: dict) -> pd.DataFrame:
    """Add today's bar from the live quote when the cached history stops yesterday.

    The quote already carries today's open, high, low and volume, so a level touched this
    morning is picked up with no extra historical call — which is what lets a long list refresh
    inside Zerodha's rate limit.
    """
    day = (quote or {}).get("ohlc") or {}
    ltp = float(quote.get("last_price") or 0) if quote else 0
    open_ = float(day.get("open") or 0)
    if d.empty or not ltp or not open_:
        return d
    today = date.today()
    if pd.Timestamp(d["date"].iloc[-1]).date() >= today:
        return d
    bar = {"date": today, "open": open_,
           "high": max(float(day.get("high") or 0), ltp, open_),
           "low": min(float(day.get("low") or ltp) or ltp, ltp, open_),
           "close": ltp, "volume": float(quote.get("volume") or 0)}
    return pd.concat([d, pd.DataFrame([bar])], ignore_index=True)


def _summary(rows: list[dict]) -> dict:
    """The strip above the table: how the whole list is doing, at a glance."""
    live = [r for r in rows if r.get("history")]
    trig = [r for r in live if (r.get("watch") or {}).get("primary")]
    pnls = [(r["watch"]["primary"].get("pnl_pct") or 0) for r in trig]
    today = [r for r in live if (r.get("watch") or {}).get("touched")]
    return {
        "stocks": len(rows), "triggered": len(trig), "waiting": len(live) - len(trig),
        "at_level_now": len(today),
        "in_profit": sum(1 for p in pnls if p > 0), "in_loss": sum(1 for p in pnls if p < 0),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "best": max(((r["symbol"], r["watch"]["primary"]["pnl_pct"]) for r in trig
                     if r["watch"]["primary"].get("pnl_pct") is not None),
                    key=lambda x: x[1], default=None),
        "worst": min(((r["symbol"], r["watch"]["primary"]["pnl_pct"]) for r in trig
                      if r["watch"]["primary"].get("pnl_pct") is not None),
                     key=lambda x: x[1], default=None),
        "oversold": sum(1 for r in live if (r.get("rsi") or 50) <= 30),
        "overbought": sum(1 for r in live if (r.get("rsi") or 50) >= 80),
    }


def _rsi_state(v: Optional[float]) -> str:
    """The row colour rule: oversold is green, heavily overbought is red, the rest is plain."""
    if v is None:
        return "none"
    if v <= 30:
        return "oversold"
    if v >= 80:
        return "overbought"
    return "neutral"


def _watch_notes(snap: dict, ext: dict, flow: dict, watch: dict) -> list[dict]:
    """Short, plain-English notes — no indicator jargon, only what a person would say."""
    out: list[dict] = []
    line = watch.get("line")
    if line:
        out.append({"key": "level", "tone": "alert" if watch.get("touched") else
                    ("good" if (watch.get("pnl_pct") or 0) > 0 else
                     "warn" if watch.get("primary") else "info"), "text": line})
    rsi = snap.get("rsi")
    if rsi is not None and rsi <= 30:
        out.append({"key": "rsi", "tone": "good", "text": "Heavily sold off — buyers have stepped back"})
    elif rsi is not None and rsi >= 80:
        out.append({"key": "rsi", "tone": "warn", "text": "Very overbought — the move is stretched"})
    if ext.get("from_52w_high") is not None and ext["from_52w_high"] >= -1:
        out.append({"key": "52w", "tone": "good", "text": "At its highest in a year"})
    elif ext.get("from_52w_low") is not None and ext["from_52w_low"] <= 1:
        out.append({"key": "52wl", "tone": "warn", "text": "At its lowest in a year"})
    if (flow.get("vs_avg20") or 0) >= 2:
        out.append({"key": "vol", "tone": "info",
                    "text": f"Trading {flow['vs_avg20']}× its usual volume today"})
    elif (flow.get("vs_avg20") or 1) <= 0.4:
        out.append({"key": "vol_quiet", "tone": "info", "text": "Very thin volume today"})
    if snap.get("bb_squeeze"):
        out.append({"key": "squeeze", "tone": "info",
                    "text": "Unusually quiet — the daily range has shrunk to a coil"})
    return out
