"""
Chart Simulation — the single-instrument charting desk.

Four instrument classes share one engine:

  • ``equity``       — any NSE/BSE cash stock
  • ``fno_option``   — a stock's future or one of its option contracts
  • ``index``        — NIFTY / BANKNIFTY / FINNIFTY spot
  • ``index_option`` — an index option contract

History is fetched in chunks and stitched, so a daily chart really does reach
back years and a 1-minute chart reaches as far as the broker allows. Read-only:
this module fetches candles and computes indicators. It places no orders.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.pmvwap_straddle.universe import Universe
from research.simulation import indicators as ind
from research.simulation.config import (
    INDICATOR_KEYS, TF_DEFAULT_DAYS, TF_MAX_DAYS, load_config, sanitize, save_config,
)

logger = get_logger("research.simulation")

MARKET_OPEN = datetime.min.time().replace(hour=9, minute=15)
MARKET_CLOSE = datetime.min.time().replace(hour=15, minute=30)

INDEX_SPOT = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
              "FINNIFTY": "NIFTY FIN SERVICE", "MIDCPNIFTY": "NIFTY MID SELECT"}
DERIVATIVE_KINDS = {"fno_option", "index_option"}


class SimulationService:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._index_tokens: dict = {}
        self._hist: dict = {}          # (token, tf, from, to) → candles, completed days only

    def load_config(self):
        return load_config()

    def save_config(self, partial):
        return save_config(partial)

    # ── instruments ──────────────────────────────────────────────────
    def _index_token(self, spot_symbol: str) -> Optional[int]:
        if spot_symbol in self._index_tokens:
            return self._index_tokens[spot_symbol]
        tok = None
        try:
            for inst in self.broker.get_instruments("NSE") or []:
                if inst.get("tradingsymbol") == spot_symbol or inst.get("name") == spot_symbol:
                    tok = int(inst["instrument_token"])
                    break
        except Exception as exc:
            logger.debug("simulation index token failed: %s", exc)
        self._index_tokens[spot_symbol] = tok
        return tok

    def chain(self, name: str) -> dict:
        """Expiries, strikes and futures for one F&O name — powers the pickers."""
        name = (name or "").strip().upper()
        self.universe._ensure_nfo()                      # read-only warm-up
        today = date.today()
        rows = [r for r in getattr(self.universe, "_nfo", [])
                if r.get("name") == name and r.get("expiry") and r["expiry"] >= today]
        if not rows:
            return {"status": "error", "message": f"{name} has no live F&O contracts"}
        futures = sorted(({"tradingsymbol": r["tradingsymbol"], "expiry": r["expiry"].isoformat(),
                           "token": r["token"], "lot_size": r["lot_size"]}
                          for r in rows if r["type"] == "FUT"), key=lambda x: x["expiry"])
        opts = [r for r in rows if r["type"] in ("CE", "PE")]
        expiries = sorted({r["expiry"] for r in opts})
        by_expiry = {}
        for e in expiries:
            strikes = sorted({r["strike"] for r in opts if r["expiry"] == e})
            by_expiry[e.isoformat()] = strikes
        # The last expiry in a calendar month is the monthly contract; the rest
        # are weeklies — the same split every trading terminal offers.
        by_month: dict = {}
        for e in expiries:
            by_month.setdefault((e.year, e.month), []).append(e)
        monthly = {max(v) for v in by_month.values()}
        kinds = {e.isoformat(): ("monthly" if e in monthly else "weekly") for e in expiries}
        return {"status": "ok", "name": name, "futures": futures,
                "expiries": [e.isoformat() for e in expiries], "strikes": by_expiry,
                "expiry_type": kinds,
                "weekly": [e.isoformat() for e in expiries if e not in monthly],
                "monthly": [e.isoformat() for e in expiries if e in monthly],
                "lot_size": (futures[0]["lot_size"] if futures else
                             (opts[0]["lot_size"] if opts else 0))}

    def resolve(self, *, kind: str, symbol: str = "", index: str = "", expiry: str = "",
                strike: Optional[float] = None, opt_type: str = "CE",
                tradingsymbol: str = "") -> dict:
        """Turn a UI selection into {token, tradingsymbol, exchange, label}."""
        kind = kind or "equity"
        if kind == "equity":
            name = (symbol or "").strip().upper()
            token, exch = self.universe.resolve_equity_token(name)
            if not token:
                return {"status": "error", "message": f"{name}: no NSE/BSE equity token"}
            return {"status": "ok", "token": int(token), "tradingsymbol": name,
                    "exchange": exch, "label": name, "kind": kind, "quote_key": f"{exch}:{name}"}

        if kind == "index":
            idx = (index or symbol or "NIFTY").strip().upper()
            spot = INDEX_SPOT.get(idx, idx)
            token = self._index_token(spot)
            if not token:
                return {"status": "error", "message": f"Could not resolve {spot}"}
            return {"status": "ok", "token": int(token), "tradingsymbol": spot, "exchange": "NSE",
                    "label": idx, "kind": kind, "quote_key": f"NSE:{spot}"}

        # derivatives — a future or an option contract
        name = (index or symbol or "").strip().upper()
        self.universe._ensure_nfo()
        rows = [r for r in getattr(self.universe, "_nfo", []) if r.get("name") == name]
        if tradingsymbol:
            rec = next((r for r in rows if r["tradingsymbol"] == tradingsymbol.strip().upper()), None)
        else:
            try:
                exp = date.fromisoformat(expiry) if expiry else None
            except ValueError:
                exp = None
            if opt_type == "FUT":
                cands = sorted((r for r in rows if r["type"] == "FUT" and (not exp or r["expiry"] == exp)),
                               key=lambda r: r["expiry"])
                rec = cands[0] if cands else None
            else:
                rec = next((r for r in rows if r["type"] == opt_type and r["expiry"] == exp
                            and abs(r["strike"] - float(strike or 0)) < 0.01), None)
        if not rec:
            return {"status": "error", "message": f"Could not resolve that {name} contract"}
        return {"status": "ok", "token": int(rec["token"]), "tradingsymbol": rec["tradingsymbol"],
                "exchange": "NFO", "label": rec["tradingsymbol"], "kind": kind,
                "expiry": rec["expiry"].isoformat(), "strike": rec["strike"],
                "opt_type": rec["type"], "lot_size": rec["lot_size"],
                "quote_key": f"NFO:{rec['tradingsymbol']}"}

    # ── week / month are built from daily bars ───────────────────────
    @staticmethod
    def _aggregate(daily: list[dict], basis: str) -> list[dict]:
        """Roll daily candles up into weekly or monthly ones.

        The broker's historical endpoint is only dependable down to `day`, so
        the higher timeframes are aggregated here instead of trusting it — a
        weekly or monthly chart then works for every instrument, always."""
        if basis == "week":
            def key(d):
                iso = d.isocalendar()
                return (iso[0], iso[1])
        else:
            def key(d):
                return (d.year, d.month)
        out: list[dict] = []
        cur = None
        bar = None
        for c in daily:
            k = key(c["_dt"].date())
            if k != cur:
                if bar:
                    out.append(bar)
                cur = k
                bar = {"date": c["_dt"], "_dt": c["_dt"], "open": float(c["open"]),
                       "high": float(c["high"]), "low": float(c["low"]),
                       "close": float(c["close"]), "volume": float(c.get("volume", 0) or 0),
                       "oi": float(c.get("oi", 0) or 0)}
            else:
                bar["high"] = max(bar["high"], float(c["high"]))
                bar["low"] = min(bar["low"], float(c["low"]))
                bar["close"] = float(c["close"])
                bar["volume"] += float(c.get("volume", 0) or 0)
                bar["oi"] = float(c.get("oi", 0) or 0)
        if bar:
            out.append(bar)
        return out

    # ── history (chunked + stitched) ─────────────────────────────────
    def history(self, token: int, tf: str, days: int, *, oi: bool = False) -> list[dict]:
        if tf in ("week", "month"):
            return self._aggregate(self.history(token, "day", days, oi=oi), tf)
        today = date.today()
        start = today - timedelta(days=days)
        window = TF_MAX_DAYS.get(tf, 90)
        out: list[dict] = []
        seen: set = set()
        cur = start
        while cur <= today:
            chunk_end = min(cur + timedelta(days=window - 1), today)
            frm = datetime.combine(cur, MARKET_OPEN)
            to = min(datetime.combine(chunk_end, MARKET_CLOSE), datetime.now())
            key = (int(token), tf, cur.isoformat(), chunk_end.isoformat(), bool(oi))
            cached = self._hist.get(key) if chunk_end < today else None
            if cached is not None:
                raw = cached
            else:
                try:
                    raw = self.broker.get_historical_data(token, frm, to, tf, oi=oi) or []
                except TypeError:
                    raw = self.broker.get_historical_data(token, frm, to, tf) or []
                except Exception as exc:
                    logger.debug("simulation history chunk failed (%s %s %s): %s", token, tf, cur, exc)
                    raw = []
                if chunk_end < today:
                    self._hist[key] = raw
            for c in raw:
                dt = c.get("date")
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt)
                    except Exception:
                        continue
                if isinstance(dt, datetime):
                    dt = dt.replace(tzinfo=None)
                else:
                    continue
                if dt in seen:
                    continue
                seen.add(dt)
                out.append({**c, "_dt": dt})
            cur = chunk_end + timedelta(days=1)
        out.sort(key=lambda c: c["_dt"])
        return out

    # ── the chart payload ────────────────────────────────────────────
    def chart(self, selection: dict, overrides=None, levels: Optional[list[dict]] = None) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            kind = selection.get("kind") or cfg["kind"]
            inst = self.resolve(kind=kind, symbol=selection.get("symbol", ""),
                                index=selection.get("index", ""), expiry=selection.get("expiry", ""),
                                strike=selection.get("strike"), opt_type=selection.get("opt_type", "CE"),
                                tradingsymbol=selection.get("tradingsymbol", ""))
            if inst.get("status") != "ok":
                return inst
            tf = cfg["timeframe"]
            days = int(cfg["history_days"]) or TF_DEFAULT_DAYS.get(tf, 180)
            want_oi = kind in DERIVATIVE_KINDS
            candles = self.history(inst["token"], tf, days, oi=want_oi)
            if not candles:
                return {"status": "error",
                        "message": f"{inst['label']}: no {tf} candles in the last {days} days"}

            # Compute EVERY indicator, not just the ticked ones: the rack then
            # toggles instantly without a round-trip, which is what makes the
            # chart feel like a chart instead of a form.
            keys = list(INDICATOR_KEYS)
            series = ind.compute(candles, keys, cfg)

            bars = int(cfg["bars"])
            total = len(candles)
            off = max(0, total - bars)
            view = candles[off:]

            def cut(v):
                if isinstance(v, list):
                    return v[off:]
                if isinstance(v, dict):
                    return {k: cut(x) for k, x in v.items()}
                return v

            shown: dict = {}
            for k, v in series.items():
                if k in ("fourth_candle", "hammer") and isinstance(v, dict):
                    # marks are bar indices (re-based), "days" is a per-session
                    # summary that must not be sliced like an aligned series.
                    marks = [{**m, "idx": m["idx"] - off} for m in v.get("marks", [])
                             if m["idx"] >= off]
                    shown[k] = {**{kk: (vv if kk == "days" else cut(vv))
                                   for kk, vv in v.items() if kk != "marks"},
                                "marks": marks}
                else:
                    shown[k] = cut(v)

            ltp = self._ltp(inst.get("quote_key")) or float(view[-1]["close"])
            lv = self._levels_status(candles, levels or [], cfg, ltp)
            return {
                "status": "ok", "instrument": inst, "timeframe": tf,
                "has_volume": ind.has_volume(view),
                "candles": [{"t": c["_dt"].strftime("%Y-%m-%d %H:%M"),
                             "o": round(float(c["open"]), 2), "h": round(float(c["high"]), 2),
                             "l": round(float(c["low"]), 2), "c": round(float(c["close"]), 2),
                             "v": round(float(c.get("volume", 0) or 0)),
                             "oi": round(float(c.get("oi", 0) or 0)) if want_oi else None}
                            for c in view],
                "series": shown, "indicators": cfg["indicators"], "computed": keys, "levels": lv,
                "ltp": round(ltp, 2), "bars_total": total, "bars_shown": len(view),
                "history_days": days, "config": cfg,
                "first_bar": view[0]["_dt"].strftime("%Y-%m-%d %H:%M"),
                "generated_at": datetime.now().strftime("%H:%M:%S"),
            }

    def _ltp(self, key: Optional[str]) -> Optional[float]:
        if not key:
            return None
        try:
            d = self.broker.get_ltp([key]) or {}
            v = d.get(key)
            return float(v) if v else None
        except Exception:
            return None

    @staticmethod
    def _levels_status(candles: list[dict], levels: list[dict], cfg: dict, ltp: float) -> list[dict]:
        out = []
        for lv in levels:
            try:
                price = float(lv.get("price") or 0)
            except (TypeError, ValueError):
                continue
            st = ind.level_status(candles, price, float(cfg["level_near_pct"]))
            out.append({**lv, **st})
        order = {"TOUCHED": 0, "NEAR": 1, "ABOVE": 2, "BELOW": 3, "unknown": 4}
        out.sort(key=lambda r: (order.get(r.get("state"), 9), abs(r.get("distance_pct") or 999)))
        return out
