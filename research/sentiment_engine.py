"""
Research module #4 — Market Sentiment Analyzer.

Read-only: aggregates global + domestic + derivative + technical indicators into
an overall Indian-market (NIFTY) sentiment with confidence, reasoning and a
trade-bias recommendation. Never places orders.

Data sources
------------
  • Zerodha (authenticated)  — India VIX, NIFTY/BankNifty, index candles
  • OptionChain module       — PCR, Max Pain, ATM IV (reused, no re-fetch logic)
  • Yahoo Finance (public)   — global indices, Brent, US 10Y, USDINR
                               (best-effort; graceful fallback if unreachable)

All thresholds / weights / bias rules live in sentiment_config.json.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import requests

from core.broker import Broker
from core.logger import get_logger
from research.vwap_pvwap import _candle_dt, INDEX_SPOT_TRADINGSYMBOL, MARKET_OPEN, MARKET_CLOSE
from research.option_chain import OptionChain

logger = get_logger("research.sentiment")

CONFIG_FILE = Path(__file__).resolve().parents[1] / "sentiment_config.json"
DEFAULT_CFG = {
    "weights": {"macro": 0.35, "derivative": 0.35, "technical": 0.30},
    "classification": {"strong_bull": 7, "bull": 3, "neutral": -3, "bear": -7},
    "vix": {"high": 16, "elevated": 14, "low": 11},
    "global_change_pct": {"strong": 1.5, "mild": 0.5},
    "crude_change_pct": {"bear_above": 2.0, "bull_below": -2.0},
    "usdinr_change_pct": {"bear_above": 0.3, "bull_below": -0.3},
    "bond_change_pct": {"bear_above": 1.5, "bull_below": -1.5},
    "fii_dii": {"fii_net_cr": None, "dii_net_cr": None, "strong_cr": 2000},
    "gift_nifty_change_pct": None,
    "pcr": {"bull_above": 1.2, "bear_below": 0.8},
    "iv": {"high": 18, "low": 11},
    "confidence": {"min": 40, "max": 98},
    "event_dates": {},
    "yahoo_symbols": {},
    "trade_bias": {},
    # Entry rule — what score/confidence actually justifies a trade
    "action": {"strong_score": 5.0, "moderate_score": 2.5, "min_confidence": 60, "high_vix_block": 20},
    # Global market sessions in IST + how each one feeds the Indian market
    "market_hours": {
        "US (S&P / Nasdaq / Dow)": {"open": "19:00", "close": "01:30", "region": "US",
            "relation": "Overnight cue — Wall Street's close sets global risk appetite before our open"},
        "Japan (Nikkei)": {"open": "05:30", "close": "11:30", "region": "Asia",
            "relation": "First Asian read of the day; runs through our morning session"},
        "Korea (KOSPI)": {"open": "05:30", "close": "12:00", "region": "Asia",
            "relation": "Live Asian peer through our morning"},
        "Hong Kong (Hang Seng)": {"open": "07:00", "close": "13:30", "region": "Asia",
            "relation": "Opens ~2h before NIFTY; strong tone-setter for our session"},
        "GIFT Nifty (SGX)": {"open": "06:30", "close": "02:45", "region": "India",
            "relation": "Most direct pre-open & intraday cue for NIFTY"},
        "India (NIFTY / Sensex)": {"open": "09:15", "close": "15:30", "region": "India",
            "relation": "Home market"},
    },
}


def _clamp(v, lo=-10.0, hi=10.0):
    return max(lo, min(hi, v))


def _lin(chg, full):
    """Map a % change to a −10..+10 score (±full% → ±10)."""
    if not full:
        return 0.0
    return round(_clamp((chg / full) * 10.0), 1)


def _signal(score):
    if score >= 2:
        return "Bullish"
    if score <= -2:
        return "Bearish"
    return "Neutral"


class SentimentEngine:
    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        self._chain = OptionChain(broker)
        self._history: list[dict] = []     # in-memory trend (per process)
        self._cache = None
        self._cache_at: Optional[datetime] = None
        self._fii_dii = None               # (fii_net_cr, dii_net_cr) cached/day
        self._fii_dii_date: Optional[date] = None
        self._fut_sym: Optional[str] = None      # near-month NIFTY future (cached/day)
        self._fut_date: Optional[date] = None
        self.user_id: Optional[int] = None       # set by route → enables durable DB config

    # ──────────── config ────────────
    # Resolution order (lowest→highest priority):
    #   DEFAULT_CFG  <  sentiment_config.json (repo seed)  <  DB row (UI edits)
    # On Railway the filesystem is ephemeral, so UI edits are persisted to the
    # DB (research_config table) and survive restarts/redeploys.
    CONFIG_NAME = "sentiment"

    @staticmethod
    def _file_config() -> dict:
        base = json.loads(json.dumps(DEFAULT_CFG))
        try:
            base.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass
        return base

    def _db_get_config(self) -> Optional[dict]:
        if not self.user_id:
            return None
        from core.database import get_db_session
        from core.models import ResearchConfig
        db = get_db_session()
        try:
            row = db.query(ResearchConfig).filter_by(
                user_id=self.user_id, name=self.CONFIG_NAME).first()
            return dict(row.config) if row and row.config else None
        except Exception as exc:
            logger.debug("sentiment db config read failed: %s", exc)
            return None
        finally:
            db.close()

    def _db_set_config(self, cfg: dict) -> bool:
        if not self.user_id:
            return False
        from core.database import get_db_session
        from core.models import ResearchConfig
        db = get_db_session()
        try:
            row = db.query(ResearchConfig).filter_by(
                user_id=self.user_id, name=self.CONFIG_NAME).first()
            if row:
                row.config = cfg
            else:
                db.add(ResearchConfig(user_id=self.user_id,
                                      name=self.CONFIG_NAME, config=cfg))
            db.commit()
            return True
        except Exception as exc:
            db.rollback()
            logger.debug("sentiment db config write failed: %s", exc)
            return False
        finally:
            db.close()

    def load_config(self) -> dict:
        """Effective config: file/default seed overlaid with the durable DB row."""
        base = self._file_config()
        db_cfg = self._db_get_config()
        if db_cfg:
            self._deep_merge(base, db_cfg)
        return base

    @staticmethod
    def _deep_merge(base: dict, upd: dict):
        for k, v in (upd or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                SentimentEngine._deep_merge(base[k], v)
            else:
                base[k] = v

    def save_config(self, partial: dict) -> dict:
        """Deep-merge a partial config into the durable store and return the
        full effective config. Persists to the DB when a user is bound
        (survives redeploys); otherwise falls back to the JSON file (local)."""
        cur = self.load_config()
        self._deep_merge(cur, partial or {})
        if not self._db_set_config(cur):
            try:
                CONFIG_FILE.write_text(json.dumps(cur, indent=2))
            except Exception as exc:
                logger.debug("sentiment file config write failed: %s", exc)
        return cur

    # ──────────── FII / DII (NSE public, cached daily) ────────────
    def _fetch_fii_dii(self):
        today = date.today()
        if self._fii_dii is not None and self._fii_dii_date == today:
            return self._fii_dii
        fii = dii = None
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/",
            })
            try:
                s.get("https://www.nseindia.com", timeout=6)
            except Exception:
                pass
            r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=8)
            r.raise_for_status()
            for row in (r.json() or []):
                cat = (row.get("category") or "").upper()
                try:
                    net = float(str(row.get("netValue", "0")).replace(",", ""))
                except Exception:
                    continue
                if "FII" in cat or "FPI" in cat:
                    fii = net
                elif "DII" in cat:
                    dii = net
        except Exception as exc:
            logger.debug("NSE FII/DII fetch failed: %s", exc)
        self._fii_dii, self._fii_dii_date = (fii, dii), today
        return self._fii_dii

    # ──────────── NIFTY near-month future (GIFT Nifty proxy) ────────────
    def _nifty_future(self) -> Optional[dict]:
        """Auto GIFT-Nifty stand-in: near-month NIFTY future change% + basis
        (premium/discount to spot). Symbol resolved once per day."""
        from research.vwap_pvwap import _parse_expiry as _pe
        today = date.today()
        if self._fut_date != today:
            self._fut_sym, self._fut_date = None, today
            try:
                futs = []
                for inst in self.broker.get_instruments("NFO"):
                    if inst.get("name") == "NIFTY" and inst.get("instrument_type") == "FUT":
                        exp = _pe(inst.get("expiry"))
                        if exp and exp >= today:
                            futs.append((exp, inst.get("tradingsymbol")))
                if futs:
                    futs.sort(key=lambda x: x[0])
                    self._fut_sym = futs[0][1]
            except Exception as exc:
                logger.debug("NIFTY future resolve failed: %s", exc)
        if not self._fut_sym:
            return None
        try:
            key = f"NFO:{self._fut_sym}"
            q = (self.broker.get_quote([key]) or {}).get(key, {})
            ltp = float(q.get("last_price") or 0)
            prev = float((q.get("ohlc") or {}).get("close") or 0)
            if ltp <= 0:
                return None
            spot = float((self.broker.get_ltp([f"NSE:{INDEX_SPOT_TRADINGSYMBOL}"]) or {})
                         .get(f"NSE:{INDEX_SPOT_TRADINGSYMBOL}", 0) or 0)
            return {"ltp": round(ltp, 2), "change_pct": round((ltp - prev) / prev * 100, 2) if prev else 0.0,
                    "basis": round(ltp - spot, 2) if spot else 0.0, "symbol": self._fut_sym}
        except Exception as exc:
            logger.debug("NIFTY future quote failed: %s", exc)
            return None

    # ──────────── fetchers ────────────
    def _yahoo(self, symbol: str) -> Optional[dict]:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"range": "5d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=6,
            )
            r.raise_for_status()
            meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
            price = float(meta.get("regularMarketPrice") or 0)
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
            if price and prev:
                return {"value": round(price, 2), "prev": round(prev, 2),
                        "change_pct": round((price - prev) / prev * 100, 2)}
        except Exception as exc:
            logger.debug("Yahoo %s failed: %s", symbol, exc)
        return None

    def _zerodha_quote(self, key: str) -> Optional[dict]:
        try:
            q = (self.broker.get_quote([key]) or {}).get(key, {})
            price = float(q.get("last_price") or 0)
            prev = float((q.get("ohlc") or {}).get("close") or 0)
            if price:
                return {"value": round(price, 2), "prev": round(prev, 2),
                        "change_pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
                        "open": float((q.get("ohlc") or {}).get("open") or 0)}
        except Exception as exc:
            logger.debug("Zerodha quote %s failed: %s", key, exc)
        return None

    # ──────────── scoring groups ────────────
    def _macro(self, cfg: dict) -> tuple[list[dict], list[str]]:
        rows, reasons = [], []
        g = cfg["global_change_pct"]
        # Global equity indices (Yahoo)
        for name, sym in (cfg.get("yahoo_symbols") or {}).items():
            d = self._yahoo(sym)
            if not d:
                rows.append({"group": "macro", "indicator": name, "value": None,
                             "change_pct": None, "signal": "—", "score": 0, "available": False})
                continue
            chg = d["change_pct"]
            if name in ("Brent Crude",):
                score = _lin(-chg, cfg["crude_change_pct"]["bear_above"])     # crude up → bearish
            elif name in ("USDINR",):
                score = _lin(-chg, cfg["usdinr_change_pct"]["bear_above"])    # INR weak → bearish
            elif name in ("US 10Y Yield",):
                score = _lin(-chg, cfg["bond_change_pct"]["bear_above"])      # yields up → bearish
            else:
                score = _lin(chg, g["strong"])                               # equity indices
            rows.append({"group": "macro", "indicator": name, "value": d["value"],
                         "change_pct": chg, "signal": _signal(score), "score": score, "available": True})
            if abs(chg) >= g["mild"]:
                reasons.append(f"{name} {'up' if chg >= 0 else 'down'} {abs(chg):.1f}%")
        # India VIX (Zerodha)
        vix = self._zerodha_quote("NSE:INDIA VIX")
        if vix:
            vc = cfg["vix"]
            lvl = vix["value"]
            s = -8 if lvl >= vc["high"] else (-4 if lvl >= vc["elevated"] else (4 if lvl <= vc["low"] else 0))
            s += -2 if vix["change_pct"] > 5 else (2 if vix["change_pct"] < -5 else 0)
            s = _clamp(s)
            rows.append({"group": "macro", "indicator": "India VIX", "value": lvl,
                         "change_pct": vix["change_pct"], "signal": _signal(s), "score": s, "available": True})
            if lvl >= vc["elevated"]:
                reasons.append(f"India VIX elevated at {lvl:.1f}")
        # GIFT Nifty — manual config override, else auto-derive from NIFTY future
        gn = cfg.get("gift_nifty_change_pct")
        if gn is not None:
            s = _lin(float(gn), g["strong"])
            rows.append({"group": "macro", "indicator": "GIFT Nifty (manual)", "value": None, "source": "manual",
                         "change_pct": round(float(gn), 2), "signal": _signal(s), "score": s, "available": True})
            reasons.append(f"GIFT Nifty {'up' if gn >= 0 else 'down'} {abs(gn):.1f}%")
        else:
            fut = self._nifty_future()
            if fut:
                chg = fut["change_pct"]
                s = _clamp(_lin(chg, g["strong"]) + (1 if fut["basis"] > 5 else -1 if fut["basis"] < -5 else 0))
                rows.append({"group": "macro", "indicator": "NIFTY Fut (GIFT proxy · auto)", "value": fut["ltp"],
                             "source": "auto", "change_pct": chg, "signal": _signal(s), "score": round(s, 1),
                             "available": True})
                if abs(chg) >= g["mild"]:
                    reasons.append(f"NIFTY future {'up' if chg >= 0 else 'down'} {abs(chg):.1f}% ({'premium' if fut['basis'] >= 0 else 'discount'})")
            else:
                rows.append({"group": "macro", "indicator": "GIFT Nifty (unavailable)", "value": None,
                             "source": "unavailable", "change_pct": None, "signal": "—", "score": 0, "available": False})

        # FII/DII — config override, else auto-fetch from NSE
        fd = cfg.get("fii_dii") or {}
        fii, dii, src = fd.get("fii_net_cr"), fd.get("dii_net_cr"), "manual"
        if fii is None and dii is None:
            afii, adii = self._fetch_fii_dii()
            if afii is not None or adii is not None:
                fii, dii, src = afii, adii, "auto"
            else:
                src = "unavailable"
        strong = float(fd.get("strong_cr") or 2000)
        if src == "unavailable":
            rows.append({"group": "macro", "indicator": "FII/DII (NSE unavailable)", "value": None,
                         "source": "unavailable", "change_pct": None, "signal": "—", "score": 0, "available": False})
        else:
            if fii is not None:
                fii = float(fii); s = _lin(fii, strong)
                rows.append({"group": "macro", "indicator": f"FII net ₹cr ({src})", "value": round(fii),
                             "source": src, "change_pct": None, "signal": _signal(s), "score": s, "available": True})
                reasons.append(f"FIIs {'bought' if fii >= 0 else 'sold'} ₹{abs(fii):.0f}cr")
            if dii is not None:
                dii = float(dii); s = round(_lin(dii, strong) * 0.6, 1)
                rows.append({"group": "macro", "indicator": f"DII net ₹cr ({src})", "value": round(dii),
                             "source": src, "change_pct": None, "signal": _signal(s), "score": s, "available": True})
        return rows, reasons

    def _derivative(self, cfg: dict) -> tuple[list[dict], list[str]]:
        rows, reasons = [], []
        try:
            snap = self._chain.snapshot(expiry_type="weekly", count=10, interval=50)
        except Exception as exc:
            logger.debug("sentiment chain failed: %s", exc)
            snap = {"status": "error"}
        if snap.get("status") != "ok":
            return rows, reasons
        spot, atm = snap["spot"], snap["atm"]
        pcr = snap.get("pcr")
        if pcr is not None:
            pc = cfg["pcr"]
            s = 6 if pcr >= pc["bull_above"] else (-6 if pcr <= pc["bear_below"] else _lin(pcr - 1, 0.4))
            rows.append({"group": "derivative", "indicator": "PCR", "value": pcr,
                         "change_pct": None, "signal": _signal(s), "score": _clamp(s), "available": True})
            reasons.append(f"PCR {pcr} ({'put support' if pcr >= pc['bull_above'] else 'call heavy' if pcr <= pc['bear_below'] else 'balanced'})")
        mp = snap.get("max_pain")
        if mp:
            s = _clamp((spot - mp) / max(snap.get("interval", 50), 1) * 3)
            rows.append({"group": "derivative", "indicator": "Max Pain", "value": mp,
                         "change_pct": None, "signal": _signal(s), "score": s, "available": True})
            reasons.append(f"Spot {'above' if spot >= mp else 'below'} Max Pain {mp}")
        # ATM IV (avg of ATM CE/PE)
        atm_row = next((r for r in snap.get("rows", []) if r["strike"] == atm), None)
        if atm_row:
            ivs = [x["iv"] for x in (atm_row.get("ce"), atm_row.get("pe")) if x and x.get("iv")]
            if ivs:
                iv = round(sum(ivs) / len(ivs), 1)
                ic = cfg["iv"]
                s = -3 if iv >= ic["high"] else (2 if iv <= ic["low"] else 0)
                rows.append({"group": "derivative", "indicator": "ATM IV", "value": iv,
                             "change_pct": None, "signal": _signal(s), "score": s, "available": True})
        # OI dominance
        tce, tpe = snap.get("total_ce_oi", 0), snap.get("total_pe_oi", 0)
        if tce and tpe:
            dom = (tpe - tce) / (tpe + tce)         # +ve = puts dominate = bullish support
            s = _clamp(dom * 12)
            rows.append({"group": "derivative", "indicator": "OI Put/Call dominance", "value": round(dom, 2),
                         "change_pct": None, "signal": _signal(s), "score": s, "available": True})
        return rows, reasons

    def _technical(self, cfg: dict) -> tuple[list[dict], list[str]]:
        rows, reasons = [], []
        token = None
        # resolve NIFTY 50 index token
        try:
            for inst in self.broker.get_instruments("NSE"):
                if inst.get("tradingsymbol") == INDEX_SPOT_TRADINGSYMBOL:
                    token = int(inst["instrument_token"]); break
        except Exception:
            token = None
        if not token:
            return rows, reasons
        today = date.today()
        prev = today - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)

        def fetch(d):
            try:
                return self.broker.get_historical_data(token, datetime.combine(d, MARKET_OPEN),
                                                       datetime.combine(d, MARKET_CLOSE), "minute") or []
            except Exception:
                return []
        tc, pc = fetch(today), fetch(prev)
        spot = 0.0
        try:
            spot = float((self.broker.get_ltp([f"NSE:{INDEX_SPOT_TRADINGSYMBOL}"]) or {}).get(f"NSE:{INDEX_SPOT_TRADINGSYMBOL}", 0))
        except Exception:
            pass
        if tc:
            spot = spot or float(tc[-1]["close"])
            # today VWAP (cum HLC3)
            pv = n = 0.0
            for c in tc:
                pv += (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
                n += 1
            vwap = pv / n if n else spot
            s = _clamp((spot - vwap) / max(vwap, 1) * 1000)
            rows.append({"group": "technical", "indicator": "Price vs VWAP", "value": round(vwap, 2),
                         "change_pct": None, "signal": _signal(s), "score": s, "available": True})
            reasons.append(f"Price {'above' if spot >= vwap else 'below'} today VWAP")
            # opening range (first 15m)
            orh = max((float(c["high"]) for c in tc[:15]), default=None)
            orl = min((float(c["low"]) for c in tc[:15]), default=None)
            if orh and orl:
                s = 5 if spot > orh else (-5 if spot < orl else 0)
                rows.append({"group": "technical", "indicator": "Opening Range", "value": f"{round(orl)}–{round(orh)}",
                             "change_pct": None, "signal": _signal(s), "score": s, "available": True})
        if pc:
            ph = max(float(c["high"]) for c in pc)
            pl = min(float(c["low"]) for c in pc)
            pcl = float(pc[-1]["close"])
            # prev-day high/low
            s = 5 if spot > ph else (-5 if spot < pl else _clamp((spot - (ph + pl) / 2) / max((ph - pl) / 2, 1) * 5))
            rows.append({"group": "technical", "indicator": "vs Prev-Day H/L", "value": f"{round(pl)}–{round(ph)}",
                         "change_pct": None, "signal": _signal(s), "score": s, "available": True})
            # CPR pivot
            pivot = (ph + pl + pcl) / 3
            bc, tcp = (ph + pl) / 2, pivot + (pivot - (ph + pl) / 2)
            s = 5 if spot > max(tcp, pivot) else (-5 if spot < min(bc, pivot) else 0)
            rows.append({"group": "technical", "indicator": "CPR", "value": round(pivot, 2),
                         "change_pct": None, "signal": _signal(s), "score": s, "available": True})
            if spot > ph:
                reasons.append("Price above previous-day high")
            elif spot < pl:
                reasons.append("Price below previous-day low")
        return rows, reasons

    # ──────────── assemble ────────────
    @staticmethod
    def _group_score(rows: list[dict], group: str) -> float:
        vals = [r["score"] for r in rows if r["group"] == group and r.get("available")]
        return round(_clamp(sum(vals) / len(vals)), 2) if vals else 0.0

    @staticmethod
    def _market_sessions(cfg: dict, now: datetime) -> list[dict]:
        """Open/closed status (IST) for each global market + its link to India."""
        def _hm(s):
            h, m = str(s).split(":")
            return dtime(int(h), int(m))
        weekend = now.weekday() >= 5
        t = now.time()
        out = []
        for name, m in (cfg.get("market_hours") or {}).items():
            try:
                o, c = _hm(m["open"]), _hm(m["close"])
            except Exception:
                continue
            inwin = (o <= t <= c) if o <= c else (t >= o or t <= c)
            out.append({"name": name, "region": m.get("region", ""),
                        "open": m.get("open"), "close": m.get("close"),
                        "relation": m.get("relation", ""),
                        "status": "Open" if (inwin and not weekend) else "Closed"})
        return out

    def _build_action(self, cfg: dict, final: float, confidence: int,
                      event_risk: str, vix_val) -> dict:
        """Turn score + confidence into a concrete Buy CE / Buy PE / Wait call,
        with the exact thresholds that gate the decision."""
        a = cfg.get("action", {})
        strong = float(a.get("strong_score", 5))
        mod = float(a.get("moderate_score", 2.5))
        min_conf = float(a.get("min_confidence", 60))
        vix_block = float(a.get("high_vix_block", 20))
        mag = abs(final)
        conf_ok = confidence >= min_conf
        vix_ok = vix_val is None or float(vix_val) < vix_block
        event_ok = event_risk != "High"

        if mag < mod or not conf_ok:
            decision, label, strength, color = "WAIT", "Wait / No clear edge", "—", "amber"
        else:
            bull = final > 0
            if mag >= strong and vix_ok and event_ok:
                strength = "Strong"
            elif mag >= strong:
                strength = "Cautious"      # strong score but VIX/event headwind
            else:
                strength = "Moderate"
            decision = "BUY_CE" if bull else "BUY_PE"
            label = "Buy Call (CE)" if bull else "Buy Put (PE)"
            color = "green" if bull else "red"

        checklist = [
            {"label": f"Directional score ≥ {mod:g}", "ok": mag >= mod, "detail": f"{final:+.1f}"},
            {"label": f"Confidence ≥ {int(min_conf)}%", "ok": conf_ok, "detail": f"{confidence}%"},
            {"label": f"India VIX < {vix_block:g}", "ok": vix_ok,
             "detail": (f"{float(vix_val):.1f}" if vix_val is not None else "n/a")},
            {"label": "No major event today", "ok": event_ok,
             "detail": ("clear" if event_ok else "event risk")},
        ]
        if decision == "WAIT":
            if conf_ok and mag < mod:
                why = (f"Score {final:+.1f} sits inside the ±{mod:g} no-trade band — no directional "
                       f"edge. Favour range/Iron-condor or premium selling, or wait.")
            else:
                why = (f"Only {confidence}% of indicators agree (need ≥{int(min_conf)}%). Signals are "
                       f"mixed — stay out or sell premium until they line up.")
        else:
            side = "calls" if decision == "BUY_CE" else "puts"
            why = (f"Score {final:+.1f} ({strength.lower()}) with {confidence}% agreement → "
                   f"buy {side} (ATM / slightly-ITM).")
            if strength == "Cautious":
                why += " High VIX/event — size down and keep tight stops."
        return {"decision": decision, "label": label, "strength": strength, "color": color,
                "headline": why, "checklist": checklist,
                "thresholds": {"strong": strong, "moderate": mod,
                               "min_confidence": int(min_conf), "vix_block": vix_block}}

    def snapshot(self, force: bool = False) -> dict:
        with self._lock:
            now = datetime.now()
            if not force and self._cache and self._cache_at and (now - self._cache_at).total_seconds() < 60:
                return self._cache
            cfg = self.load_config()
            rows, reasons = [], []
            for fn in (self._macro, self._derivative, self._technical):
                try:
                    r, rs = fn(cfg)
                    rows += r
                    reasons += rs
                except Exception as exc:
                    logger.error("sentiment group failed: %s", exc)

            macro = self._group_score(rows, "macro")
            deriv = self._group_score(rows, "derivative")
            tech = self._group_score(rows, "technical")
            w = cfg["weights"]
            final = round(macro * w["macro"] + deriv * w["derivative"] + tech * w["technical"], 2)

            cl = cfg["classification"]
            if final >= cl["strong_bull"]:
                sentiment = "Strong Bullish"
            elif final >= cl["bull"]:
                sentiment = "Bullish"
            elif final > cl["neutral"]:
                sentiment = "Neutral"
            elif final > cl["bear"]:
                sentiment = "Bearish"
            else:
                sentiment = "Strong Bearish"

            scored = [r for r in rows if r.get("available")]
            sign = 1 if final > 0 else (-1 if final < 0 else 0)
            agree = sum(1 for r in scored if (r["score"] > 0) == (sign > 0) and r["score"] != 0) if sign else 0
            conf_raw = (agree / len(scored) * 100) if scored else 0
            cc = cfg["confidence"]
            confidence = int(max(cc["min"], min(cc["max"], conf_raw))) if scored else 0

            bias = cfg.get("trade_bias", {}).get(sentiment, [])
            # event risk: configured date or NFO expiry today
            ev = cfg.get("event_dates", {}).get(now.date().isoformat())
            if not ev:
                try:
                    if now.date().isoformat() in self._chain.list_expiries().get("expiries", []):
                        ev = "Weekly expiry"
                except Exception:
                    pass
            event_risk = "High" if ev else "Low"

            status = ("Pre-Open" if now.time() < MARKET_OPEN else
                      "Open" if now.time() <= MARKET_CLOSE and now.weekday() < 5 else "Closed")

            vix_val = next((r["value"] for r in rows
                            if r["indicator"] == "India VIX" and r.get("available")), None)
            action = self._build_action(cfg, final, confidence, event_risk, vix_val)
            markets = self._market_sessions(cfg, now)

            # history (per process)
            self._history.append({"t": now.strftime("%H:%M"), "score": final, "sentiment": sentiment})
            self._history = self._history[-60:]

            out = {
                "status": "ok", "sentiment": sentiment, "confidence": confidence,
                "macro_score": macro, "derivative_score": deriv, "technical_score": tech,
                "final_score": final, "reasons": reasons[:12],
                "trade_bias": bias, "event_risk": event_risk, "event_label": ev,
                "market_status": status, "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "weights": w, "indicators": rows, "history": list(self._history),
                "action": action, "markets": markets,
            }
            self._cache, self._cache_at = out, now
            return out
