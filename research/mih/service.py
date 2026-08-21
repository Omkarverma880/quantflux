"""
MIH service (Research #14) — read-only market intelligence.

One batched Kite quote() sweep covers the whole universe (LTP, OHLC, day volume,
VWAP). A separate *progressive* daily-history enrichment fills 52-week / 20-day
levels, average volume and ATR — capped per scan and day-cached, so coverage
grows across refreshes instead of hammering the API. Anything not yet enriched is
reported as missing, never guessed.

This module places no orders and imports no execution client.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.pmvwap_straddle.universe import Universe
from research.mih import ideas as idea_mod
from research.mih import scanners as scan_mod
from research.mih import scoring
from research.mih.config import sanitize
from research.mih.sectors import sector_of

logger = get_logger("research.mih")
IST = timezone(timedelta(hours=5, minutes=30))
_QUOTE_BATCH = 400
SESSION_MIN = 375.0


class MarketHub:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._enrich: dict[str, dict] = {}
        self._enrich_day: Optional[date] = None
        self._snap_cache: Optional[tuple] = None

    # ── market session ──
    @staticmethod
    def market_status() -> dict:
        now = datetime.now(IST)
        hm = now.hour * 60 + now.minute
        if now.weekday() >= 5:
            state = "CLOSED"
        elif hm < 9 * 60:
            state = "CLOSED"
        elif hm < 9 * 60 + 15:
            state = "PRE-OPEN"
        elif hm <= 15 * 60 + 30:
            state = "OPEN"
        elif hm <= 16 * 60:
            state = "POST-MARKET"
        else:
            state = "CLOSED"
        return {"state": state, "is_open": state == "OPEN",
                "time": now.strftime("%H:%M:%S"), "date": now.date().isoformat()}

    # ── universe ──
    def _names(self, cfg, symbols) -> list[str]:
        if symbols:
            return list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))
        names = [x["name"] for x in self.universe.equities()]
        if int(cfg.get("max_stocks", 0)) > 0:
            names = names[: int(cfg["max_stocks"])]
        return names

    # ── progressive daily enrichment (52w / 20d / avg vol / ATR) ──
    def _enrich_levels(self, names: list[str], cfg: dict):
        today = datetime.now(IST).date()
        if self._enrich_day != today:
            self._enrich, self._enrich_day = {}, today
        todo = [n for n in names if n not in self._enrich][: int(cfg["enrich_cap"])]
        if not todo:
            return
        lookback = int(cfg["enrich_lookback_days"])
        frm = datetime.combine(today - timedelta(days=int(lookback * 1.6) + 10), datetime.min.time())
        to = datetime.combine(today, datetime.min.time())
        for nm in todo:
            try:
                token, _ex = self.universe.resolve_equity_token(nm)
                if not token:
                    self._enrich[nm] = {}
                    continue
                rows = self.broker.get_historical_data(token, frm, to, "day") or []
                rows = [r for r in rows if r.get("high") and r.get("low")]
                if len(rows) < 20:
                    self._enrich[nm] = {}
                    continue
                yr = rows[-lookback:]
                d20 = rows[-21:-1] or rows[-20:]
                vols = [float(r.get("volume", 0) or 0) for r in rows[-21:-1] if r.get("volume")]
                trs = []
                for i in range(max(1, len(rows) - 15), len(rows)):
                    h, l = float(rows[i]["high"]), float(rows[i]["low"])
                    pc = float(rows[i - 1]["close"])
                    trs.append(max(h - l, abs(h - pc), abs(l - pc)))
                self._enrich[nm] = {
                    "high_52w": round(max(float(r["high"]) for r in yr), 2),
                    "low_52w": round(min(float(r["low"]) for r in yr), 2),
                    "high_20d": round(max(float(r["high"]) for r in d20), 2),
                    "low_20d": round(min(float(r["low"]) for r in d20), 2),
                    "avg_vol_20": round(sum(vols) / len(vols), 0) if vols else None,
                    "atr": round(sum(trs) / len(trs), 2) if trs else None,
                }
            except Exception as exc:
                logger.debug("mih enrich %s failed: %s", nm, exc)
                self._enrich[nm] = {}

    # ── snapshot sweep (one batched quote call) ──
    def snapshot(self, cfg: dict, symbols=None, force=False) -> dict:
        names = self._names(cfg, symbols)
        if not names:
            return {"status": "error", "message": "No stocks in universe"}
        now = datetime.now(IST)
        if (not force and self._snap_cache and symbols is None
                and (now - self._snap_cache[0]).total_seconds() < 20):
            return self._snap_cache[1]

        quotes: dict = {}
        keys = [f"NSE:{n}" for n in names]
        for i in range(0, len(keys), _QUOTE_BATCH):
            try:
                quotes.update(self.broker.get_quote(keys[i:i + _QUOTE_BATCH]) or {})
            except Exception as exc:
                logger.warning("mih quote batch failed: %s", exc)
        self._enrich_levels(names, cfg)

        elapsed = max(1.0, (now.hour * 60 + now.minute) - (9 * 60 + 15))
        frac = min(1.0, elapsed / SESSION_MIN)
        rows = []
        for nm in names:
            q = quotes.get(f"NSE:{nm}")
            if not q:
                continue
            ohlc = q.get("ohlc") or {}
            ltp = float(q.get("last_price", 0) or 0)
            prev = float(ohlc.get("close", 0) or 0)
            vol = float(q.get("volume", q.get("volume_traded", 0)) or 0)
            if ltp < float(cfg["min_price"]) or vol < float(cfg["min_volume"]):
                continue
            lev = self._enrich.get(nm) or {}
            avg_vol = lev.get("avg_vol_20")
            rvol = round(vol / (avg_vol * frac), 2) if (avg_vol and frac > 0) else None
            rows.append({
                "symbol": nm, "sector": sector_of(nm), "ltp": round(ltp, 2),
                "open": round(float(ohlc.get("open", 0) or 0), 2),
                "high": round(float(ohlc.get("high", 0) or 0), 2),
                "low": round(float(ohlc.get("low", 0) or 0), 2),
                "prev_close": round(prev, 2),
                "change": round(ltp - prev, 2) if prev else None,
                "change_pct": round((ltp - prev) / prev * 100, 2) if prev else None,
                "volume": int(vol), "rvol": rvol,
                "vwap": round(float(q.get("average_price", 0) or 0), 2) or None,
                "high_52w": lev.get("high_52w"), "low_52w": lev.get("low_52w"),
                "high_20d": lev.get("high_20d"), "low_20d": lev.get("low_20d"),
                "atr": lev.get("atr"),
            })
        enriched = sum(1 for r in rows if r.get("high_52w"))
        out = {"status": "ok", "rows": rows, "scanned": len(rows), "universe": len(names),
               "enriched": enriched, "market": self.market_status(),
               "updated_at": now.strftime("%H:%M:%S")}
        if symbols is None:
            self._snap_cache = (now, out)
        return out

    # ── full dashboard payload ──
    def dashboard(self, cfg: dict, symbols=None) -> dict:
        with self._lock:
            cfg = sanitize(cfg)
            snap = self.snapshot(cfg, symbols)
            if snap.get("status") != "ok":
                return snap
            rows, n = snap["rows"], int(cfg["top_n"])
            movers = sorted([r for r in rows if r.get("change_pct") is not None],
                            key=lambda r: r["change_pct"])
            scanners = {k: scan_mod.run_scanner(k, rows, cfg, n) for k in scan_mod.SCANNERS}

            scored = []
            for r in rows:
                sc = scoring.score_stock(r, cfg)
                scored.append({**r, "score": sc["score"], "grade": sc["grade"],
                               "breakdown": sc["breakdown"]})
            scored.sort(key=lambda r: r["score"], reverse=True)

            built = []
            for r in scored:
                idea = idea_mod.build_idea(r, {"score": r["score"], "grade": r["grade"],
                                               "breakdown": r["breakdown"]}, cfg)
                if idea:
                    built.append(idea)
                if len(built) >= int(cfg["idea_max"]):
                    break

            return {
                "status": "ok", "market": snap["market"], "updated_at": snap["updated_at"],
                "coverage": {"scanned": snap["scanned"], "universe": snap["universe"],
                             "enriched": snap["enriched"]},
                "movers": {"gainers": movers[::-1][:n], "losers": movers[:n],
                           "high_52w": scanners["high_52w"]["rows"],
                           "low_52w": scanners["low_52w"]["rows"]},
                "sectors": self._sector_breadth(rows),
                "scanner_groups": scan_mod.GROUPS, "scanners": scanners,
                "top_scores": scored[: n * 2], "ideas": built,
                "module_version": "1.0",
            }

    @staticmethod
    def _sector_breadth(rows: list[dict]) -> list[dict]:
        agg: dict[str, dict] = {}
        for r in rows:
            c = r.get("change_pct")
            if c is None:
                continue
            s = agg.setdefault(r["sector"], {"sector": r["sector"], "adv": 0, "dec": 0,
                                             "unch": 0, "sum": 0.0, "n": 0})
            s["n"] += 1
            s["sum"] += c
            if c > 0.05:
                s["adv"] += 1
            elif c < -0.05:
                s["dec"] += 1
            else:
                s["unch"] += 1
        out = []
        for s in agg.values():
            if not s["n"]:
                continue
            s["avg_change"] = round(s["sum"] / s["n"], 2)
            s["breadth"] = round(s["adv"] / s["n"] * 100, 1)
            del s["sum"]
            out.append(s)
        out.sort(key=lambda s: s["avg_change"], reverse=True)
        return out

    # ── one scanner, full list ──
    def scanner(self, key: str, cfg: dict, symbols=None, limit: int = 0) -> dict:
        with self._lock:
            cfg = sanitize(cfg)
            snap = self.snapshot(cfg, symbols)
            if snap.get("status") != "ok":
                return snap
            res = scan_mod.run_scanner(key, snap["rows"], cfg, limit)
            res["status"] = "ok"
            res["market"] = snap["market"]
            return res

    # ── single stock detail ──
    def stock(self, symbol: str, cfg: dict) -> dict:
        with self._lock:
            cfg = sanitize(cfg)
            nm = (symbol or "").strip().upper()
            snap = self.snapshot(cfg, [nm], force=True)
            if snap.get("status") != "ok" or not snap["rows"]:
                return {"status": "error", "message": f"{nm}: no live quote available"}
            r = snap["rows"][0]
            sc = scoring.score_stock(r, cfg)
            idea = idea_mod.build_idea(r, sc, cfg)
            matched = []
            for k, spec in scan_mod.SCANNERS.items():
                if scan_mod.run_scanner(k, [r], cfg)["count"]:
                    matched.append({"key": k, "label": spec[0], "direction": spec[2]})
            return {"status": "ok", "market": snap["market"], "snapshot": r,
                    "score": sc, "idea": idea, "screens": matched}

    # ── market news (reuses the existing shared News module) ──
    @staticmethod
    def news(limit: int = 8) -> dict:
        try:
            from research.news_sentiment import NewsSentiment
            snap = NewsSentiment().snapshot()
            items = (snap.get("headlines") or snap.get("items") or [])[:limit]
            return {"status": "ok", "available": bool(items), "items": items,
                    "bias": snap.get("bias"), "sources": snap.get("sources")}
        except Exception as exc:
            logger.debug("mih news failed: %s", exc)
            return {"status": "ok", "available": False, "items": [],
                    "note": "News feed unavailable right now."}
