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


DII_STOCK_MESSAGE = (
    "DII aggregate data is available. Security-level DII activity requires a stock-level "
    "institutional-data provider — no free source publishes which stocks DIIs bought or sold, "
    "and this screen will not infer a list of names from an aggregate figure."
)


def _as_iso(nse_date: str):
    """'06-OCT-2026' -> '2026-10-06'. Returns the input untouched if it is already ISO."""
    from datetime import datetime as _dt
    s = str(nse_date or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return _dt.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s or None


def delivery_reading(change_pct, delivery_pct, baseline_pct, surge) -> str:
    """What one session's delivery says, against that stock's own habit.

    A description of two numbers — where the price went, and whether an unusual share of the
    volume settled. It names no buyer, because NSE attributes none, and it is backward-looking:
    it says what the session looked like, never what the next one will do.
    """
    if surge is None or delivery_pct is None:
        return "No baseline yet"
    if change_pct is None:
        return "No price change recorded"
    heavy, light = surge >= 1.25, surge <= 0.80
    up, down = change_pct > 0.25, change_pct < -0.25
    if heavy and up:
        return "Delivery surge on a rising day"
    if heavy and down:
        return "Delivery surge into a fall"
    if heavy:
        return "Delivery surge, price flat"
    if light and up:
        return "Rose on unusually low delivery"
    if light and down:
        return "Fell on unusually low delivery"
    return "In line with its own norm"


class InstitutionalFlowService:
    """One per user, holding the broker connection and a short-lived view cache."""

    def __init__(self, broker=None, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self._lock = threading.Lock()
        self._rows_cache: dict = {}

    # ── the aggregate ────────────────────────────────────────────────
    def fetch_fii_dii_daily(self, force: bool = False) -> dict:
        """The session's aggregate cash flow, from NSE where possible and Moneycontrol where not.

        Two independent publishers of the same three numbers, so when both answer they are
        reconciled and any disagreement is reported rather than averaged away. When NSE blocks
        us — which it does — Moneycontrol carries the screen instead of the screen going blank.
        """
        raw = SRC.fetch_fii_dii_daily(force=force)
        # The cross-check is a second opinion, not the number. When NSE has already answered it
        # gets a short leash, because a slow or sulking secondary must never hold up the figure
        # the whole screen is built around — the page would paint "unavailable" while waiting on
        # a source it does not need. When NSE has failed, this *is* the number, so it gets the
        # full timeout.
        alt = SRC.fetch_fii_dii_moneycontrol(force=force,
                                             timeout=5 if raw.get("available") else 20)
        cross = None
        if raw.get("available") and alt.get("available"):
            if raw.get("data_date") == alt.get("data_date"):
                diffs = {}
                for who in ("fii", "dii"):
                    a, b = raw.get(who) or {}, alt.get(who) or {}
                    for field in ("buy", "sell", "net"):
                        if a.get(field) is not None and b.get(field) is not None:
                            d = round(abs(a[field] - b[field]), 2)
                            if d >= 1.0:
                                diffs["%s_%s" % (who, field)] = d
                cross = {"checked": True, "agrees": not diffs, "differences": diffs,
                         "against": alt.get("source"),
                         "message": ("NSE and Moneycontrol agree on this session."
                                     if not diffs else
                                     "NSE and Moneycontrol disagree on this session — treat the "
                                     "figures as provisional.")}
            else:
                cross = {"checked": False, "agrees": None, "differences": {},
                         "against": alt.get("source"),
                         "message": ("The two sources are reporting different sessions "
                                     "(NSE %s, Moneycontrol %s), so they were not compared."
                                     % (raw.get("data_date"), alt.get("data_date")))}
        elif alt.get("available") and not raw.get("available"):
            # NSE refused us; Moneycontrol publishes the same figures, so use them and say so
            raw = {**alt, "fallback_from": "NSE — FII/DII trading activity (cash market)",
                   "fallback_reason": raw.get("error") or "NSE did not answer"}
            cross = {"checked": False, "agrees": None, "differences": {},
                     "against": None,
                     "message": "NSE did not answer, so these figures are Moneycontrol's."}
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
            "cross_check": cross,
            "balance_label": ("Combined net cash-market flow of FIIs and DIIs. It is not a "
                              "market-wide total — every buy has a seller, and the other side "
                              "here is retail, proprietary and corporate activity."),
            "age": SESS.data_age(raw.get("retrieved_at")),
        }

    def fetch_fii_stock_activity(self) -> dict:
        """Superseded by ``fii_stock_lists``, which reads the published lists for real."""
        return SRC.fetch_stock_activity("FII")

    def fetch_dii_stock_activity(self) -> dict:
        return self.dii_stock_note()

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
            # The stock lists, the fund lists and the derivative books each have their own
            # endpoint and the page fetches them separately. Doing them here too meant the
            # session band — the one thing a trader looks at first — waited on two megabytes of
            # someone else's HTML before it could paint.
            "dii_stocks": {"available": False, "institution": "DII",
                           "data_type": "dii_shareholding_change",
                           "message": DII_STOCK_MESSAGE},
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
        # Three readings can cover the same date, and they are not equally good. Gross buy and
        # sell is the fuller record, so a row that has it is never replaced by one that does not.
        #
        #   stored row        what we captured ourselves — gross when we read it live
        #   live aggregate    today's reading, which has gross
        #   published series  ~30 sessions, net only
        #
        # Taking them in that order of richness keeps a backfill from erasing detail.
        def better(existing: Optional[dict], candidate: dict) -> bool:
            if existing is None:
                return True
            if candidate.get("fii_buy") is not None and existing.get("fii_buy") is None:
                return True
            if existing.get("fii_net") is None and candidate.get("fii_net") is not None:
                return True
            return False

        agg = self.fetch_fii_dii_daily()
        if agg.get("available") and agg.get("data_date"):
            fii, dii = agg.get("fii") or {}, agg.get("dii") or {}
            live = {"trading_date": agg["data_date"],
                    "fii_buy": fii.get("buy"), "fii_sell": fii.get("sell"), "fii_net": fii.get("net"),
                    "dii_buy": dii.get("buy"), "dii_sell": dii.get("sell"), "dii_net": dii.get("net"),
                    "is_final": bool(agg.get("is_final")), "source": agg.get("source"),
                    "backfilled": False, "retrieved_at": agg.get("retrieved_at")}
            if better(rows.get(agg["data_date"]), live):
                rows[agg["data_date"]] = live

        series = SRC.fetch_fii_dii_history()
        for r in (series.get("rows") or []):
            d = r["trading_date"]
            cand = {"trading_date": d,
                    "fii_buy": None, "fii_sell": None, "fii_net": r["fii_net"],
                    "dii_buy": None, "dii_sell": None, "dii_net": r["dii_net"],
                    "is_final": True, "source": series.get("source"),
                    "backfilled": True, "retrieved_at": series.get("retrieved_at")}
            if better(rows.get(d), cand):
                rows[d] = cand

        status = SESS.market_status()
        cursor = cursor_end = date.fromisoformat(status["session_date"])

        # The index reaction comes from NSE's own close archive rather than from the history
        # page, so the column survives losing Moneycontrol — which is a 403 away on any host it
        # dislikes, and was previously enough to blank it.
        idx = SRC.fetch_index_closes(cursor_end, sessions=max(12, min(int(days) + 4, 60)),
                                     is_trading_day=SESS.is_trading_day)
        for d, r in rows.items():
            r.setdefault("backfilled", False)
            r["gross_available"] = r.get("fii_buy") is not None
            marks = (idx.get("by_session") or {}).get(d) or {}
            nifty = marks.get("nifty") or {}
            bank = marks.get("banknifty") or {}
            r["nifty_close"] = nifty.get("close")
            r["nifty_change_pct"] = nifty.get("change_pct")
            r["banknifty_close"] = bank.get("close")
            r["banknifty_change_pct"] = bank.get("change_pct")
        # walk back over real trading days so weekends never appear as missing sessions
        wanted: list[str] = []
        for _ in range(max(1, min(int(days), 400))):
            wanted.append(cursor.isoformat())
            cursor = SESS.previous_trading_day(cursor)
        # Two different kinds of blank, and conflating them would mislead. A session after the
        # earliest one on record is genuinely awaiting publication; one before it was published
        # long ago and simply never captured, because NSE only serves the latest day.
        earliest = min(rows) if rows else None
        out, missed = [], 0
        for d in wanted:
            r = rows.get(d)
            if r:
                out.append({**r, "published": True, "state": "published"})
                continue
            if earliest and d < earliest:
                # Published long before anything here was recording, and no source we have
                # serves it. A row of dashes for every such day is noise, not information, so
                # the session is left out and only counted.
                missed += 1
                continue
            out.append({
                "trading_date": d, "published": False, "state": "pending",
                "note": "Not published yet",
                "fii_buy": None, "fii_sell": None, "fii_net": None,
                "dii_buy": None, "dii_sell": None, "dii_net": None,
                "is_final": False, "source": None,
            })
        pending = sum(1 for r in out if r["state"] == "pending")
        return {"available": True, "rows": out, "count": len(out),
                "pending": pending, "not_captured": missed,
                "earliest_recorded": earliest,
                "backfilled": sum(1 for r in out if r.get("backfilled")),
                "index_source": idx.get("source") if idx.get("available") else None,
                "sources": [x for x in ["NSE — FII/DII trading activity (cash market)",
                                        idx.get("source") if idx.get("available") else None,
                                        series.get("source") if series.get("available") else None]
                            if x],
                "note": ("Figures are published once, after the close. Today's session is read "
                         "from NSE with Moneycontrol as a cross-check; earlier sessions come "
                         "from Moneycontrol's history, which carries net figures only — so buy "
                         "and sell are blank on those rows rather than derived. Pending means "
                         "awaiting publication. A session no source can speak for is left out "
                         "rather than shown as a blank row. Nothing is ever shown as zero.")}

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


    # ── the published stock-level lists, joined to the tape ──────────
    def _price_row(self, symbol: Optional[str], quotes: dict) -> dict:
        """Today's price, volume and relative volume for one symbol, or honest blanks."""
        q = (quotes.get("NSE:%s" % symbol) or {}) if symbol else {}
        ohlc = q.get("ohlc") or {}
        ltp = float(q.get("last_price") or 0) or None
        prev = float(ohlc.get("close") or 0) or None
        vol = q.get("volume") or q.get("volume_traded")
        avg = self._average_volume(symbol) if symbol else None
        return {
            "price": ltp,
            "previous_close": prev,
            "price_move": round(ltp - prev, 2) if (ltp and prev) else None,
            "price_change_pct": round((ltp - prev) / prev * 100, 2) if (ltp and prev) else None,
            "day_open": float(ohlc.get("open") or 0) or None,
            "day_high": float(ohlc.get("high") or 0) or None,
            "day_low": float(ohlc.get("low") or 0) or None,
            "volume": int(vol) if vol else None,
            "average_volume": int(avg) if avg else None,
            "relative_volume": calculate_relative_volume(vol, avg),
        }

    def fii_stock_lists(self, refresh: bool = False) -> dict:
        """The FII lists — see ``holder_stock_lists``."""
        return self.holder_stock_lists("FII", refresh=refresh)

    def holder_stock_lists(self, institution: str = "FII", refresh: bool = False) -> dict:
        """Both published lists for one institution, resolved to NSE symbols and priced.

        Ranked by the size of the *percentage* change in holding, because that is the number the
        source actually publishes. Ranking these by a rupee figure would mean inventing one.
        """
        from research.institutional_flow import symbols as SYM

        who = str(institution).upper()
        out: dict = {"available": False, "institution": who, "sides": {},
                     "resolved": 0, "unresolved": 0,
                     "data_type": "%s_shareholding_change" % who.lower(),
                     "retrieved_at": SRC.now_ist().isoformat(timespec="seconds")}
        fetched, every_symbol = {}, []
        for side in ("bought", "sold"):
            raw = SRC.fetch_et_holder_stocks(who, side, force=refresh)
            fetched[side] = raw
            if raw.get("available"):
                for r in raw["rows"]:
                    hit = SYM.resolve(r.get("company"), r.get("slug"))
                    r["_symbol"] = hit["symbol"]
                    r["_via"] = hit["via"]
                    r["_listed_as"] = hit["company"]
                    if hit["symbol"]:
                        every_symbol.append(hit["symbol"])

        quotes = self.fetch_live_market_data(sorted(set(every_symbol)))
        for side, raw in fetched.items():
            if not raw.get("available"):
                out["sides"][side] = {"available": False, "rows": [], "count": 0,
                                      "institution": who, "data_type": raw.get("data_type"),
                                      "direction": raw.get("direction"),
                                      "error": raw.get("error"), "source": raw.get("source"),
                                      "source_url": raw.get("source_url")}
                continue
            rows = []
            for r in raw["rows"]:
                sym = r.get("_symbol")
                live = self._price_row(sym, quotes)
                qoq = r.get("holding_qoq_change_pct")
                rows.append({
                    "company": r["company"], "symbol": sym,
                    "listed_as": r.get("_listed_as"), "resolved_via": r.get("_via"),
                    "institution": who,
                    "holding_pct": r.get("holding_pct"),
                    "holding_qoq_change_pct": qoq,
                    "holding_yoy_change_pct": r.get("holding_yoy_change_pct"),
                    # kept under their old names so an FII caller written against the
                    # original shape keeps working
                    "fii_holding_pct": r.get("holding_pct") if who == "FII" else None,
                    "fii_holding_qoq_change_pct": qoq if who == "FII" else None,
                    "fii_holding_yoy_change_pct": (r.get("holding_yoy_change_pct")
                                                   if who == "FII" else None),
                    "dii_holding_pct": r.get("dii_holding_pct"),
                    # ET's own price, kept separately from the broker's — two readings of the
                    # same thing taken at different moments should not be merged into one column
                    "et_price": r.get("et_price"),
                    "et_price_change": r.get("et_price_change"),
                    "et_change_pct": r.get("et_change_pct"),
                    **live,
                    "activity_type": "%s_shareholding_%s" % (who.lower(), raw["direction"]),
                    "confirmation": confirmation(
                        "%s_shareholding_%s" % (who.lower(), raw["direction"]),
                        live["price_change_pct"], live["relative_volume"]),
                    "reaction": reaction(live["price_change_pct"], live["relative_volume"],
                                         "%s_shareholding_%s" % (who.lower(), raw["direction"])),
                    "price_source": "Zerodha quote" if live["price"] else (
                        "Economic Times (broker not connected)" if r.get("et_price") else "unavailable"),
                })
            rows.sort(key=lambda x: abs(x["holding_qoq_change_pct"] or 0), reverse=True)
            out["sides"][side] = {
                "available": True, "rows": rows, "count": len(rows),
                "institution": who, "data_type": raw["data_type"],
                "direction": raw["direction"], "source": raw["source"],
                "source_url": raw["source_url"], "measure": raw["measure"],
                "columns": raw.get("columns") or [],
                "stale": bool(raw.get("stale")), "error": raw.get("error"),
                "retrieved_at": raw.get("retrieved_at"),
            }
            out["resolved"] += sum(1 for r in rows if r["symbol"])
            out["unresolved"] += sum(1 for r in rows if not r["symbol"])
        out["available"] = any(v.get("available") for v in out["sides"].values())
        out["connected"] = self.broker is not None
        label = "FII" if who == "FII" else "mutual fund"
        out["ranked_by"] = ("Ranked by the quarter-on-quarter change in %s shareholding, "
                            "largest first." % label)
        out["caveat"] = ("These are %s *shareholding* changes, reported quarterly. The price "
                         "and volume beside each name are today's, so the right question is "
                         "whether the tape agrees — not whether %s traded it today."
                         % (label, "FIIs" if who == "FII" else "funds"))
        return out

    def sync_fii_stock_lists(self, db, trading_date: Optional[str] = None,
                             refresh: bool = True, institution: str = "FII") -> dict:
        """Store today's reading of both ET lists, so tomorrow can look back at it.

        One snapshot per date per institution, replaced on a re-read of the same day. Rows carry
        the holding percentage and its change; ``activity_value`` stays NULL, because no rupee
        figure exists for a shareholding change and a NULL is the honest way to say that.
        """
        from core.models import InstitutionalStockActivity
        who = str(institution).upper()
        lists = self.holder_stock_lists(who, refresh=refresh)
        if not lists.get("available"):
            return {"saved": False, "institution": who, "reason": "neither list could be read",
                    "errors": {k: v.get("error") for k, v in lists["sides"].items()}}
        d = (date.fromisoformat(str(trading_date)[:10]) if trading_date
             else date.fromisoformat(SESS.market_status()["session_date"]))
        try:
            db.query(InstitutionalStockActivity).filter(
                InstitutionalStockActivity.trading_date == d,
                InstitutionalStockActivity.institution == who).delete(synchronize_session=False)
            now = datetime.utcnow()
            written = 0
            for side, block in lists["sides"].items():
                if not block.get("available"):
                    continue
                for r in block["rows"]:
                    db.add(InstitutionalStockActivity(
                        trading_date=d,
                        symbol=r["symbol"] or (r["company"] or "")[:32].upper(),
                        company=r["company"], institution=who,
                        activity_type=r["activity_type"],
                        activity_value=None,                  # no rupee figure exists
                        activity_percentage=r["holding_qoq_change_pct"],
                        holding_pct=r["holding_pct"],
                        price=r["price"], price_change_pct=r["price_change_pct"],
                        volume=r["volume"], average_volume=r["average_volume"],
                        relative_volume=r["relative_volume"],
                        confirmation=(r["confirmation"] or "")[:32],
                        source=(block["source"] or "")[:160], retrieved_at=now))
                    written += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("could not store the FII stock lists: %s", exc)
            return {"saved": False, "reason": str(exc)[:200]}
        self.invalidate()
        return {"saved": True, "institution": who, "trading_date": d.isoformat(),
                "rows": written, "resolved": lists["resolved"],
                "unresolved": lists["unresolved"]}

    def dii_stock_note(self, refresh: bool = False) -> dict:
        """What can honestly be shown on the domestic side, by stock.

        There is no DII stock list. The published series stops at the aggregate, and inferring
        names from it is exactly the fabrication this screen refuses.

        What does exist is *mutual fund* shareholding, published the same way FII shareholding
        is. Mutual funds are the largest component of DII but they are not all of it, so it is
        offered under its own name with that stated — adjacent evidence, clearly labelled, not a
        stand-in for the DII figure.
        """
        mf = self.holder_stock_lists("MF", refresh=refresh)
        return {
            "available": False, "institution": "DII",
            "data_type": "dii_shareholding_change",
            "rows": [],
            "message": DII_STOCK_MESSAGE,
            "mutual_funds": mf,
            "mutual_funds_caveat": ("Mutual fund shareholding is published by stock, and funds "
                                    "are the largest part of DII — but DII also covers banks, "
                                    "insurers and other domestic institutions, whose holdings "
                                    "are not in this list. Read it as fund shareholding, not as "
                                    "the DII cash figure broken down."),
            "retrieved_at": SRC.now_ist().isoformat(timespec="seconds"),
        }

    # ── yesterday's list, measured against today's tape ──────────────
    def yesterday_tracker(self, db, limit: int = 15) -> dict:
        """Yesterday's stored list, priced with today's session.

        The point of keeping snapshots: a name that FIIs raised their holding in, which then
        fell on heavy volume today, is worth seeing. Without yesterday's row there is nothing
        to compare today against, so this reads from storage and never re-derives the list.
        """
        from core.models import InstitutionalStockActivity
        status = SESS.market_status()
        today = date.fromisoformat(status["session_date"])
        prev = SESS.previous_trading_day(today)

        rows = (db.query(InstitutionalStockActivity)
                  .filter(InstitutionalStockActivity.trading_date == prev,
                          InstitutionalStockActivity.institution == "FII").all())
        asked, fell_back = prev, False
        if not rows:
            latest = (db.query(InstitutionalStockActivity.trading_date)
                        .filter(InstitutionalStockActivity.trading_date < today,
                                InstitutionalStockActivity.institution == "FII")
                        .order_by(InstitutionalStockActivity.trading_date.desc()).first())
            if latest and latest[0]:
                prev, fell_back = latest[0], True
                rows = (db.query(InstitutionalStockActivity)
                          .filter(InstitutionalStockActivity.trading_date == prev,
                                  InstitutionalStockActivity.institution == "FII").all())
        if not rows:
            return {"available": False, "previous_session": asked.isoformat(), "rows": [],
                    "message": ("Nothing was recorded for %s yet. The lists are stored each "
                                "time this screen reads them, so a comparison becomes possible "
                                "from the next session onward."
                                % asked.strftime("%d %b %Y"))}

        ranked = sorted(rows, key=lambda r: abs(float(r.activity_percentage or 0)), reverse=True)
        top = ranked[:max(1, min(int(limit), 100)) * 2]
        quotes = self.fetch_live_market_data(sorted({r.symbol for r in top}))
        inc, dec = [], []
        for r in top:
            live = self._price_row(r.symbol, quotes)
            rec = {
                "symbol": r.symbol, "company": r.company,
                "activity_type": r.activity_type,
                "recorded_on": prev.isoformat(),
                "holding_pct": float(r.holding_pct) if r.holding_pct is not None else None,
                "holding_change_pct": (float(r.activity_percentage)
                                       if r.activity_percentage is not None else None),
                "price_then": float(r.price) if r.price is not None else None,
                **live,
                "moved_since": (round(live["price"] - float(r.price), 2)
                                if (live["price"] and r.price is not None) else None),
                "confirmation": confirmation(r.activity_type, live["price_change_pct"],
                                             live["relative_volume"]),
                "reaction": reaction(live["price_change_pct"], live["relative_volume"],
                                     r.activity_type),
                "source": r.source,
            }
            (inc if str(r.activity_type).endswith("increase") else dec).append(rec)
        cut = max(1, min(int(limit), 100))
        return {
            "available": True, "previous_session": prev.isoformat(),
            "asked_for": asked.isoformat(), "is_latest_available": fell_back,
            "increased": inc[:cut], "decreased": dec[:cut],
            "counts": {"increased": len(inc), "decreased": len(dec)},
            "source": rows[0].source,
            "data_type": "fii_shareholding_change",
            "note": ("Recorded on %s, priced with today's session. 'Moved since' compares "
                     "today's price with the price stored alongside the list."
                     % prev.strftime("%d %b %Y")),
        }

    # ── backfilling the by-date table ────────────────────────────────
    def backfill_history(self, db, force: bool = False) -> dict:
        """Fill the by-date table from Moneycontrol's multi-session series.

        NSE serves only the latest day, so without this a fresh install can show nothing but
        "not captured" for every session before the one it was first switched on for.

        That series carries net figures only. Buy and sell are left NULL on a backfilled row
        rather than derived, and a row we already hold gross figures for is never overwritten by
        one that has none.
        """
        from core.models import InstitutionalDailySummary
        hist = SRC.fetch_fii_dii_history(force=force)
        if not hist.get("available"):
            return {"saved": False, "reason": hist.get("error") or "history unavailable"}
        added = updated = skipped = 0
        try:
            existing = {r.trading_date: r for r in db.query(InstitutionalDailySummary).all()}
            for r in hist["rows"]:
                d = date.fromisoformat(r["trading_date"])
                row = existing.get(d)
                if row is None:
                    db.add(InstitutionalDailySummary(
                        trading_date=d, fii_net=r["fii_net"], dii_net=r["dii_net"],
                        market_status="CLOSED", data_type="aggregate_cash_flow",
                        source=hist["source"], source_url=hist["source_url"],
                        is_final=True, retrieved_at=datetime.utcnow()))
                    added += 1
                    continue
                # a stored row with gross figures is the better record — leave it alone
                if row.fii_buy is not None or row.dii_buy is not None:
                    skipped += 1
                    continue
                if row.fii_net is None or row.dii_net is None or force:
                    row.fii_net, row.dii_net = r["fii_net"], r["dii_net"]
                    row.source = row.source or hist["source"]
                    row.source_url = row.source_url or hist["source_url"]
                    row.is_final = True
                    row.retrieved_at = datetime.utcnow()
                    updated += 1
                else:
                    skipped += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("history backfill failed: %s", exc)
            return {"saved": False, "reason": str(exc)[:200]}
        return {"saved": True, "added": added, "updated": updated, "skipped": skipped,
                "covers": hist.get("covers"), "source": hist["source"],
                "note": ("Backfilled rows carry net figures only — the series has no gross buy "
                         "or sell, so those stay blank rather than being derived.")}

    def derivatives_history(self, days: int = 15) -> dict:
        """FII's four derivative books per session, beside the index close.

        Cash net alone says what FIIs did in equities; the index-options line often says the
        opposite, and a screen about institutional positioning that hides it is misleading.
        """
        hist = SRC.fetch_fii_dii_history()
        if not hist.get("available"):
            return {"available": False, "rows": [], "error": hist.get("error"),
                    "source": hist.get("source")}
        n = max(1, min(int(days), 60))
        return {"available": True, "rows": hist["rows"][:n], "count": min(n, hist["count"]),
                "source": hist["source"], "source_url": hist["source_url"],
                "stale": bool(hist.get("stale")), "retrieved_at": hist.get("retrieved_at"),
                "note": ("Cash figures are net ₹ crore in the equity market. The four FII "
                         "derivative columns are net ₹ crore in index futures, index options, "
                         "stock futures and stock options.")}


    # ── delivery: the daily, stock-level number that does exist ──────
    def delivery_screen(self, lookback: int = 11, min_turnover_cr: float = 25.0,
                        universe: str = "ALL", limit: int = 40, db=None,
                        rank: str = "surge") -> dict:
        """Which stocks were taken to delivery at a rate unlike their own recent norm.

        Delivery percentage is volume that settled rather than squared off intraday. It is the
        only daily per-stock figure NSE publishes that speaks to intent, and it attributes
        nothing — it does not say who took delivery, and nothing here claims it was an FII.

        A stock's own baseline is what matters: 60% delivery is unremarkable for a name that
        always runs at 60% and notable for one that usually runs at 25%. So the ranking is the
        ratio of today's delivery to the mean of the preceding sessions, not the raw figure.

        Rows need a real baseline to be ranked by surge, and a stock with too few prior
        sessions is carried with ``surge: None`` rather than given a flattering default.
        """
        from research.institutional_flow import symbols as SYM

        status = SESS.market_status()
        end = date.fromisoformat(status["session_date"])
        win = SRC.fetch_delivery_window(end, sessions=max(2, min(int(lookback), 30)),
                                        is_trading_day=SESS.is_trading_day)
        if not win.get("available"):
            return {"available": False, "rows": [], "error": win.get("error"),
                    "source": win.get("source"),
                    "message": ("NSE's delivery file for the latest session could not be read, "
                                "so there is nothing to screen. It is published after the close "
                                "and is not available during the session.")}

        sessions = win["sessions"]
        latest, priors = sessions[0], sessions[1:]
        today = win["by_session"][latest]

        listed = (SYM.index().get("by_symbol") or {})
        companies = (SYM.index().get("companies") or {})
        allowed = self._universe_filter(universe, db)

        rows = []
        for sym, r in today.items():
            # NSE's equity list is the ETF filter: a liquid fund or gold ETF settles almost
            # entirely to delivery and would own the top of this table while meaning nothing.
            if sym not in listed:
                continue
            if allowed is not None and sym not in allowed:
                continue
            if r["turnover_cr"] is None or r["turnover_cr"] < float(min_turnover_cr):
                continue
            if r["delivery_pct"] is None:
                continue
            hist = [win["by_session"][s][sym]["delivery_pct"]
                    for s in priors
                    if sym in win["by_session"][s]
                    and win["by_session"][s][sym]["delivery_pct"] is not None]
            base = round(sum(hist) / len(hist), 2) if len(hist) >= 3 else None
            surge = round(r["delivery_pct"] / base, 2) if (base and base > 0) else None
            vols = [win["by_session"][s][sym]["volume"]
                    for s in priors
                    if sym in win["by_session"][s] and win["by_session"][s][sym]["volume"]]
            vbase = (sum(vols) / len(vols)) if len(vols) >= 3 else None
            rows.append({
                **r,
                "company": companies.get(sym) or "",
                "delivery_baseline_pct": base,
                "delivery_surge": surge,
                "baseline_sessions": len(hist),
                "relative_volume": (round(r["volume"] / vbase, 2)
                                    if (vbase and r["volume"]) else None),
                "delivery_value_cr": (round(r["delivery_qty"] * r["vwap"] / 1e7, 2)
                                      if (r["delivery_qty"] and r["vwap"]) else None),
                "reading": delivery_reading(r["change_pct"], r["delivery_pct"], base, surge),
            })

        keyed = {
            "surge": lambda x: (x["delivery_surge"] if x["delivery_surge"] is not None else -1),
            "delivery": lambda x: (x["delivery_pct"] or 0),
            "value": lambda x: (x["delivery_value_cr"] or 0),
            "gain": lambda x: (x["change_pct"] if x["change_pct"] is not None else -999),
            "loss": lambda x: (-(x["change_pct"]) if x["change_pct"] is not None else -999),
            "turnover": lambda x: (x["turnover_cr"] or 0),
        }
        rows.sort(key=keyed.get(rank, keyed["surge"]), reverse=True)
        cut = max(1, min(int(limit), 200))

        agg = self.fetch_fii_dii_daily()
        same_day = bool(agg.get("available") and agg.get("data_date") == _as_iso(latest))
        return {
            "available": True,
            "session": _as_iso(latest), "session_label": latest,
            "baseline_sessions": priors,
            "rows": rows[:cut], "count": len(rows), "shown": min(cut, len(rows)),
            "universe": universe, "rank": rank,
            "min_turnover_cr": float(min_turnover_cr),
            "data_type": "stock_delivery",
            "source": win["source"], "source_url": win.get("source_url"),
            "retrieved_at": win.get("retrieved_at"),
            "context": ({"fii_net": (agg.get("fii") or {}).get("net"),
                         "dii_net": (agg.get("dii") or {}).get("net"),
                         "same_session": same_day} if agg.get("available") else None),
            "measure": ("Delivery percentage is the share of the day's traded volume that "
                        "settled rather than being squared off intraday. Ranked against each "
                        "stock's own mean over the previous %d sessions, because the level only "
                        "means something relative to that stock's habit." % len(priors)),
            "caveat": ("This says nothing about who took delivery. NSE does not attribute it, "
                       "and neither does this screen — it is not FII activity, and a high "
                       "reading is a description of one session, not a signal."),
        }

    def _universe_filter(self, universe: str, db=None):
        """The set of symbols a screen is allowed to show, or None for no restriction."""
        key = (universe or "ALL").upper()
        if key in ("ALL", ""):
            return None
        if key == "NIFTY50":
            u = SRC.nifty50_symbols()
            return {x["symbol"] for x in u.get("symbols", [])} or None
        if key == "FNO":
            try:
                if self.broker is None:
                    return None
                return {str(i.get("name") or "").strip().upper()
                        for i in self.broker.get_instruments("NFO") or []
                        if i.get("instrument_type") == "FUT" and i.get("name")} or None
            except Exception as exc:
                logger.debug("F&O universe unavailable: %s", exc)
                return None
        if key == "WATCHLIST" and db is not None and self.user_id:
            try:
                from research.my_equity import store as MEST
                return {r.symbol for r in MEST.list_stocks(db, self.user_id)} or None
            except Exception as exc:
                logger.debug("watchlist universe unavailable: %s", exc)
                return None
        if key == "FII_LIST":
            lists = self.fii_stock_lists()
            syms = set()
            for blk in (lists.get("sides") or {}).values():
                for r in blk.get("rows") or []:
                    if r.get("symbol"):
                        syms.add(r["symbol"])
            return syms or None
        return None

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
