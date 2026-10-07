"""
institutional_flow_service — assembles the FII_DII_Equity Activity Watcher.

It joins three things that are each honest on their own:

  * the aggregate FII/DII cash flow NSE publishes after the close,
  * price and volume for a chosen universe, taken from the broker connection the app
    already has rather than a second one,
  * the previous session's snapshot, kept so the screen can ask what happened next.

Where a dataset does not exist — intraday institutional flow, daily stock-level attribution —
the service returns the reason instead of a number. Nothing here derives one data type from
another: a shareholding change never becomes a day's buying, and a daily aggregate is never
sliced into hours.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

from core.logger import get_logger
from research.institutional_flow import session as SESS
from research.institutional_flow import sources as SRC

logger = get_logger("research.institutional_flow")

QUOTE_CHUNK = 150
ROWS_TTL_S = 30.0          # a re-render inside this window reuses the last build
AVG_VOL_DAYS = 20

UNIVERSES = ("NIFTY50", "FII_ACTIVITY", "DII_ACTIVITY", "FII_DII", "ALL", "WATCHLIST")


def calculate_net_flow(buy: Optional[float], sell: Optional[float]) -> Optional[float]:
    """Net = buy − sell. Returns None when either side is missing, never 0."""
    if buy is None or sell is None:
        return None
    return round(float(buy) - float(sell), 2)


def calculate_relative_volume(volume: Optional[float], avg: Optional[float]) -> Optional[float]:
    """Today's volume against its 20-day average. None when the average is unknown."""
    if not volume or not avg or float(avg) <= 0:
        return None
    return round(float(volume) / float(avg), 3)


def confirmation(activity: Optional[str], change_pct: Optional[float],
                 rvol: Optional[float]) -> str:
    """Whether price and volume agree with the institutional activity on record.

    This is a description of three numbers lining up, not a prediction and not a signal. It
    needs real institutional activity to describe — price and volume alone cannot confirm
    something nobody has reported.
    """
    if not activity or activity == "price_volume_only":
        return "No institutional data"
    if change_pct is None or rvol is None:
        return "Unconfirmed"
    up, heavy = change_pct > 0, rvol > 1.0
    if activity.endswith("increase") and up and heavy:
        return "Possible positive confirmation"
    if activity.endswith("decrease") and not up and heavy:
        return "Possible negative confirmation"
    return "Unconfirmed"


def reaction(change_pct: Optional[float], rvol: Optional[float],
             yesterday_activity: Optional[str]) -> str:
    """How a stock behaved today against what was recorded about it yesterday.

    An analytical label applied after the fact. It says what happened, not what will.
    """
    if change_pct is None:
        return "No data"
    bullish = bool(yesterday_activity and yesterday_activity.endswith("increase"))
    heavy = bool(rvol and rvol > 1.0)
    strong = abs(change_pct) >= 2.0
    if abs(change_pct) < 0.25:
        return "No significant movement"
    agrees = (change_pct > 0) == bullish
    if agrees and strong and heavy:
        return "Strong continuation"
    if agrees and heavy:
        return "Positive confirmation"
    if agrees:
        return "Weak confirmation"
    if strong and heavy:
        return "Reversal"
    return "Negative confirmation"


class InstitutionalFlowService:
    """One per user, holding the broker connection and a short-lived view cache."""

    def __init__(self, broker=None, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self._lock = threading.Lock()
        self._rows_cache: dict = {}

    # ── the aggregate ────────────────────────────────────────────────
    def fetch_fii_dii_daily(self, force: bool = False) -> dict:
        raw = SRC.fetch_fii_dii_daily(force=force)
        fii, dii = raw.get("fii") or {}, raw.get("dii") or {}
        fii_net = fii.get("net") if fii.get("net") is not None else calculate_net_flow(fii.get("buy"), fii.get("sell"))
        dii_net = dii.get("net") if dii.get("net") is not None else calculate_net_flow(dii.get("buy"), dii.get("sell"))
        balance = None
        if fii_net is not None and dii_net is not None:
            balance = round(fii_net + dii_net, 2)
        return {
            **raw,
            "fii": {**fii, "net": fii_net} if fii else None,
            "dii": {**dii, "net": dii_net} if dii else None,
            "balance": balance,
            "balance_label": ("Combined net cash-market flow of FIIs and DIIs. It is not a "
                              "market-wide total — every buy has a seller, and the other side "
                              "here is retail, proprietary and corporate activity."),
            "age": SESS.data_age(raw.get("retrieved_at")),
        }

    def fetch_fii_stock_activity(self) -> dict:
        return SRC.fetch_stock_activity("FII")

    def fetch_dii_stock_activity(self) -> dict:
        return SRC.fetch_stock_activity("DII")

    def fetch_intraday(self) -> dict:
        return SRC.fetch_intraday_cash_flow()

    # ── the universe and its market data ─────────────────────────────
    def universe(self, name: str, db=None, limit: int = 50) -> dict:
        key = (name or "NIFTY50").upper()
        if key == "NIFTY50":
            u = SRC.nifty50_symbols()
            return {"name": key, "available": u.get("available"), "source": u.get("source"),
                    "symbols": [x["symbol"] for x in u.get("symbols", [])][:limit],
                    "meta": {x["symbol"]: x for x in u.get("symbols", [])},
                    "error": u.get("error")}
        if key == "WATCHLIST" and db is not None and self.user_id:
            try:
                from research.my_equity import store as MEST
                rows = MEST.list_stocks(db, self.user_id)
                return {"name": key, "available": True, "source": "My Equity Workspace",
                        "symbols": [r.symbol for r in rows][:limit],
                        "meta": {r.symbol: {"symbol": r.symbol, "company": r.company or ""}
                                 for r in rows}, "error": None}
            except Exception as exc:
                logger.debug("watchlist universe failed: %s", exc)
                return {"name": key, "available": False, "symbols": [], "meta": {},
                        "error": str(exc)[:120]}
        # The institutional universes need per-stock attribution, which no public source
        # publishes daily. Saying so beats showing an arbitrary list.
        if key in ("FII_ACTIVITY", "DII_ACTIVITY", "FII_DII"):
            return {"name": key, "available": False, "symbols": [], "meta": {},
                    "error": ("Stock-level daily institutional attribution is not available "
                              "from the current source, so this universe cannot be built.")}
        u = SRC.nifty50_symbols()
        return {"name": "ALL", "available": u.get("available"),
                "source": u.get("source") + " (the widest list available without a paid feed)",
                "symbols": [x["symbol"] for x in u.get("symbols", [])][:limit],
                "meta": {x["symbol"]: x for x in u.get("symbols", [])}, "error": u.get("error")}

    def fetch_live_market_data(self, symbols: list[str]) -> dict:
        """Quotes for a list of NSE symbols, through the broker the app already holds."""
        if self.broker is None or not symbols:
            return {}
        out: dict = {}
        keys = [f"NSE:{s}" for s in symbols]
        for i in range(0, len(keys), QUOTE_CHUNK):
            chunk = keys[i:i + QUOTE_CHUNK]
            try:
                out.update(self.broker.get_quote(chunk) or {})
            except Exception as exc:
                logger.debug("quote chunk failed: %s", exc)
        return out

    def _average_volume(self, symbol: str) -> Optional[float]:
        """20-day average volume from the market store, when that symbol is cached there."""
        try:
            # the workspace's own daily cache, read-only and offline: no second market-data
            # connection and no extra broker call just to get an average
            from research.my_equity import cache as MECACHE
            d = MECACHE._read(symbol, "NSE")
            if d is None or not len(d) or "volume" not in d:
                return None
            tail = d["volume"].tail(AVG_VOL_DAYS).dropna()
            return float(tail.mean()) if len(tail) else None
        except Exception:
            return None

    # ── the snapshot the page renders ────────────────────────────────
    def build_daily_snapshot(self, db=None, universe: str = "NIFTY50",
                             limit: int = 50, refresh: bool = True) -> dict:
        ck = f"{universe}:{limit}"
        with self._lock:
            hit = self._rows_cache.get(ck)
            if hit and refresh and time.time() - hit[0] < ROWS_TTL_S:
                return {**hit[1], "cached": True}

        status = SESS.market_status()
        agg = self.fetch_fii_dii_daily()
        uni = self.universe(universe, db=db, limit=limit)
        quotes = self.fetch_live_market_data(uni.get("symbols") or []) if refresh else {}

        stocks = []
        for sym in uni.get("symbols") or []:
            q = quotes.get(f"NSE:{sym}") or {}
            ohlc = q.get("ohlc") or {}
            ltp = float(q.get("last_price") or 0) or None
            prev = float(ohlc.get("close") or 0) or None
            chg = round((ltp - prev) / prev * 100, 2) if (ltp and prev) else None
            vol = q.get("volume") or q.get("volume_traded")
            avg = self._average_volume(sym)
            rvol = calculate_relative_volume(vol, avg)
            stocks.append({
                "symbol": sym,
                "company": (uni.get("meta", {}).get(sym) or {}).get("company") or "",
                "price": ltp, "price_change_pct": chg,
                "volume": int(vol) if vol else None,
                "average_volume": int(avg) if avg else None,
                "relative_volume": rvol,
                "institution": None,
                "activity_type": "price_volume_only",
                "confirmation": confirmation(None, chg, rvol),
                "source": "Zerodha quote" if ltp else "unavailable",
            })

        out = {
            "status": "ok",
            "market": status,
            "aggregate": agg,
            "intraday": self.fetch_intraday(),
            "fii_stocks": self.fetch_fii_stock_activity(),
            "dii_stocks": self.fetch_dii_stock_activity(),
            "universe": {k: v for k, v in uni.items() if k != "meta"},
            "stocks": stocks,
            "connected": self.broker is not None,
            "built_at": SRC.now_ist().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._rows_cache[ck] = (time.time(), out)
        return {**out, "cached": False}

    def invalidate(self) -> None:
        with self._lock:
            self._rows_cache.clear()

    # ── persistence ──────────────────────────────────────────────────
    def persist_session(self, db, agg: Optional[dict] = None) -> dict:
        """Write (or update) the row for the session NSE's data belongs to.

        Keyed on NSE's own ``data_date``, never on the day we happened to read it — otherwise a
        weekend read would create a session that never traded.
        """
        from core.models import InstitutionalDailySummary
        agg = agg or self.fetch_fii_dii_daily()
        if not agg.get("available") or not agg.get("data_date"):
            return {"saved": False, "reason": agg.get("error") or "no data to save"}
        d = date.fromisoformat(agg["data_date"])
        status = SESS.market_status()
        fii, dii = agg.get("fii") or {}, agg.get("dii") or {}
        try:
            row = (db.query(InstitutionalDailySummary)
                     .filter(InstitutionalDailySummary.trading_date == d).first())
            if row is None:
                row = InstitutionalDailySummary(trading_date=d)
                db.add(row)
            row.fii_buy, row.fii_sell, row.fii_net = fii.get("buy"), fii.get("sell"), fii.get("net")
            row.dii_buy, row.dii_sell, row.dii_net = dii.get("buy"), dii.get("sell"), dii.get("net")
            row.market_status = status["status"]
            row.data_type = agg.get("data_type")
            row.source = agg.get("source")
            row.source_url = agg.get("source_url")
            # final once the session that produced it has closed
            row.is_final = bool(d < date.today() or not status["live"])
            row.retrieved_at = datetime.utcnow()
            db.commit()
            return {"saved": True, "trading_date": d.isoformat(), "is_final": bool(row.is_final)}
        except Exception as exc:
            db.rollback()
            logger.warning("could not persist institutional session: %s", exc)
            return {"saved": False, "reason": str(exc)[:160]}

    def flow_table(self, db, days: int = 10) -> dict:
        """The by-date FII/DII table, including sessions whose figures have not arrived.

        NSE publishes once, after the close, so the session in progress — and sometimes the one
        that just ended — has no figures yet. Those rows are shown as "Not published yet" rather
        than omitted: a missing row reads as "nothing happened", which is a different claim.
        """
        hist = self.history(db, days=days)
        rows = {r["trading_date"]: r for r in hist.get("rows", [])}
        status = SESS.market_status()
        cursor = date.fromisoformat(status["session_date"])
        # walk back over real trading days so weekends never appear as missing sessions
        wanted: list[str] = []
        for _ in range(max(1, min(int(days), 400))):
            wanted.append(cursor.isoformat())
            cursor = SESS.previous_trading_day(cursor)
        # Two different kinds of blank, and conflating them would mislead. A session after the
        # earliest one on record is genuinely awaiting publication; one before it was published
        # long ago and simply never captured, because NSE only serves the latest day.
        earliest = min(rows) if rows else None
        out = []
        for d in wanted:
            r = rows.get(d)
            if r:
                out.append({**r, "published": True, "state": "published"})
                continue
            before_records = bool(earliest and d < earliest)
            out.append({
                "trading_date": d, "published": False,
                "state": "not_captured" if before_records else "pending",
                "note": ("Not captured — recording began "
                         + (earliest or "later")) if before_records else "Not published yet",
                "fii_buy": None, "fii_sell": None, "fii_net": None,
                "dii_buy": None, "dii_sell": None, "dii_net": None,
                "is_final": False, "source": None,
            })
        pending = sum(1 for r in out if r["state"] == "pending")
        missed = sum(1 for r in out if r["state"] == "not_captured")
        return {"available": True, "rows": out, "count": len(out),
                "pending": pending, "not_captured": missed,
                "earliest_recorded": earliest,
                "note": ("NSE publishes FII/DII cash-market activity once, after the close, and "
                         "serves only the latest session — so a day before this module started "
                         "recording cannot be backfilled. Pending means awaiting publication; "
                         "not captured means it was published before recording began. Neither "
                         "is shown as zero.")}

    def history(self, db, days: int = 10) -> dict:
        """Completed sessions, newest first, straight from storage."""
        from core.models import InstitutionalDailySummary
        try:
            rows = (db.query(InstitutionalDailySummary)
                      .order_by(InstitutionalDailySummary.trading_date.desc())
                      .limit(max(1, min(int(days), 400))).all())
        except Exception as exc:
            logger.debug("history read failed: %s", exc)
            return {"available": False, "rows": [], "error": str(exc)[:160]}
        out = []
        for r in rows:
            f = lambda v: None if v is None else float(v)      # noqa: E731
            out.append({
                "trading_date": r.trading_date.isoformat(),
                "fii_buy": f(r.fii_buy), "fii_sell": f(r.fii_sell), "fii_net": f(r.fii_net),
                "dii_buy": f(r.dii_buy), "dii_sell": f(r.dii_sell), "dii_net": f(r.dii_net),
                "is_final": bool(r.is_final), "source": r.source,
                "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None,
            })
        return {"available": True, "rows": out, "count": len(out), "error": None}

    # ── stock-level activity, from a source the user names ───────────
    def import_stock_activity(self, db, trading_date: str, institution: str,
                              increased: list, decreased: list, source: str) -> dict:
        """Record a stock-level activity list and say where it came from.

        No public feed publishes daily per-stock FII/DII attribution, so the list has to be
        named by whoever read it. What goes in the database is exactly that: symbols, a
        direction, the date, and the source string — stored as a *shareholding change*, which
        is what these lists actually report. The app then supplies the part it can verify by
        itself: price, volume, relative volume and the reaction.
        """
        from core.models import InstitutionalStockActivity
        d = date.fromisoformat(str(trading_date)[:10])
        who = "FII" if str(institution).upper().startswith("F") else "DII"
        src = (source or "").strip()[:160]
        if not src:
            raise ValueError("name the source of this list — an unattributed list is not evidence")

        def clean(seq):
            out, seen = [], set()
            for x in seq or []:
                sym = str(x).strip().upper()
                if sym and sym not in seen:
                    seen.add(sym)
                    out.append(sym)
            return out

        pairs = ([(s_, f"{who.lower()}_shareholding_increase") for s_ in clean(increased)]
                 + [(s_, f"{who.lower()}_shareholding_decrease") for s_ in clean(decreased)])
        if not pairs:
            raise ValueError("no symbols given")

        # replace this date+institution wholesale, so a re-import corrects rather than doubles
        db.query(InstitutionalStockActivity).filter(
            InstitutionalStockActivity.trading_date == d,
            InstitutionalStockActivity.institution == who).delete(synchronize_session=False)
        now = datetime.utcnow()
        for sym, kind in pairs:
            db.add(InstitutionalStockActivity(
                trading_date=d, symbol=sym, institution=who, activity_type=kind,
                source=src, retrieved_at=now))
        db.commit()
        self.invalidate()
        return {"saved": True, "trading_date": d.isoformat(), "institution": who,
                "increased": len(clean(increased)), "decreased": len(clean(decreased)),
                "source": src}

    def stock_activity(self, db, trading_date: Optional[str] = None,
                       institution: str = "FII") -> dict:
        """A stored activity list, priced with today's tape and scored for confirmation."""
        from core.models import InstitutionalStockActivity
        who = "FII" if str(institution).upper().startswith("F") else "DII"
        status = SESS.market_status()
        d = date.fromisoformat(trading_date[:10]) if trading_date else date.fromisoformat(status["session_date"])
        rows = (db.query(InstitutionalStockActivity)
                  .filter(InstitutionalStockActivity.trading_date == d,
                          InstitutionalStockActivity.institution == who).all())
        asked_for, fell_back = d, False
        if not rows and trading_date is None:
            # These lists are published about the session that just ended, so asking for the
            # session in progress usually finds nothing. Fall back to the most recent list
            # there is and say which one — the same rule the rest of this screen follows.
            latest = (db.query(InstitutionalStockActivity.trading_date)
                        .filter(InstitutionalStockActivity.institution == who)
                        .order_by(InstitutionalStockActivity.trading_date.desc()).first())
            if latest and latest[0]:
                d, fell_back = latest[0], True
                rows = (db.query(InstitutionalStockActivity)
                          .filter(InstitutionalStockActivity.trading_date == d,
                                  InstitutionalStockActivity.institution == who).all())
        if not rows:
            return {"available": False, "institution": who, "date": d.isoformat(),
                    "increased": [], "decreased": [],
                    "message": (f"No {who} stock list recorded for {d.strftime('%d %b %Y')}. "
                                "Daily per-stock attribution is not published by any free feed, "
                                "so a list has to be imported and its source named.")}
        syms = [r.symbol for r in rows]
        quotes = self.fetch_live_market_data(syms)
        inc, dec = [], []
        for r in rows:
            q = quotes.get(f"NSE:{r.symbol}") or {}
            ohlc = q.get("ohlc") or {}
            ltp = float(q.get("last_price") or 0) or None
            prev = float(ohlc.get("close") or 0) or None
            chg = round((ltp - prev) / prev * 100, 2) if (ltp and prev) else None
            move = round(ltp - prev, 2) if (ltp and prev) else None
            vol = q.get("volume") or q.get("volume_traded")
            avg = self._average_volume(r.symbol)
            rvol = calculate_relative_volume(vol, avg)
            rec = {
                "symbol": r.symbol, "activity_type": r.activity_type,
                "price": ltp, "price_move": move, "price_change_pct": chg,
                "volume": int(vol) if vol else None,
                "average_volume": int(avg) if avg else None,
                "relative_volume": rvol,
                "confirmation": confirmation(r.activity_type, chg, rvol),
                "reaction": reaction(chg, rvol, r.activity_type),
                "source": r.source,
            }
            (inc if r.activity_type.endswith("increase") else dec).append(rec)
        return {
            "available": True, "institution": who, "date": d.isoformat(),
            "is_latest_available": fell_back,
            "asked_for": asked_for.isoformat(),
            "increased": inc, "decreased": dec,
            "source": rows[0].source,
            "data_type": f"{who.lower()}_shareholding_change",
            "caveat": (f"These lists report a change in {who} shareholding, not a day's buying. "
                       "The price and volume beside each name are today's, from your broker — "
                       "they say whether the tape agreed, which is the only part that can be "
                       "verified here."),
        }

    def conflict(self, agg: Optional[dict] = None) -> dict:
        """FII and DII pulling against each other, stated from the aggregate only."""
        agg = agg or self.fetch_fii_dii_daily()
        fii = (agg.get("fii") or {}).get("net")
        dii = (agg.get("dii") or {}).get("net")
        if fii is None or dii is None:
            return {"available": False,
                    "message": "Needs both net figures, and at least one is unavailable."}
        opposed = (fii > 0) != (dii > 0)
        if opposed:
            who_in = "DII" if dii > 0 else "FII"
            who_out = "FII" if dii > 0 else "DII"
            headline = f"{who_out} selling + {who_in} buying"
        else:
            headline = ("FII and DII both net buyers" if fii > 0
                        else "FII and DII both net sellers" if fii < 0 else "Both flat")
        return {
            "available": True, "divergence": opposed, "headline": headline,
            "fii_net": fii, "dii_net": dii,
            "gap": round(abs(fii - dii), 2),
            "note": ("Informational only, and aggregate only — the public data does not say "
                     "which stocks the two sides disagreed on."),
        }
