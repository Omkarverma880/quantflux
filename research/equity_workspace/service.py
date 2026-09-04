"""
Equity Strategy Workspace — one consolidated, read-only screener.

Point it at a watchlist (or a single stock), pick a candle timeframe, hit Smart
Scan: for every stock it reports the live price, today's volume, open interest
when the stock is in F&O, and a tick per strategy showing which of the entry
conditions it satisfies right now — with a score out of the applicable
strategies so the strongest names sort to the top.

It NEVER places an order, never touches strategy state and never imports a live
engine. Candle fetches are minimised and cached: the daily and 60-minute series
only need completed sessions, so they are pulled once per day per stock and
today's bar is taken from the quote instead.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.pmvwap_straddle.universe import Universe
from research.equity_workspace import indicators as ind
from research.equity_workspace import signals as sig
from research.equity_workspace.config import (
    TF_HISTORY_DAYS, load_config, save_config, sanitize,
)

logger = get_logger("research.equity_workspace")

MARKET_OPEN = datetime.min.time().replace(hour=9, minute=15)
MARKET_CLOSE = datetime.min.time().replace(hour=15, minute=30)
QUOTE_CHUNK = 200


def _fmt_qty(v: Optional[float]) -> Optional[float]:
    return None if v is None else float(v)


class EquityWorkspaceService:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._hist_cache: dict = {}       # (token, interval, day) → completed candles
        self._fut_map: dict = {}          # name → front-month FUT record
        self._fut_day: Optional[date] = None

    def load_config(self):
        return load_config()

    def save_config(self, partial):
        return save_config(partial)

    # ── instruments ──────────────────────────────────────────────────
    def _futures_map(self) -> dict:
        """name → nearest-expiry FUT contract, for the open-interest column."""
        today = date.today()
        if self._fut_day == today and self._fut_map:
            return self._fut_map
        self.universe._ensure_nfo()                       # read-only warm-up
        out: dict = {}
        for rec in getattr(self.universe, "_nfo", []):
            if rec.get("type") != "FUT" or rec.get("expiry") < today:
                continue
            cur = out.get(rec["name"])
            if cur is None or rec["expiry"] < cur["expiry"]:
                out[rec["name"]] = rec
        self._fut_map, self._fut_day = out, today
        return out

    # ── market data ──────────────────────────────────────────────────
    def _history(self, token: int, interval: str, days: int, *, completed_only: bool) -> list[dict]:
        """Candles for one instrument. ``completed_only`` ends the range at the
        previous session, which makes the series stable for the whole day and so
        safe to cache — today's bar comes from the live quote instead."""
        today = date.today()
        end = today - timedelta(days=1) if completed_only else today
        key = (int(token), interval, end.isoformat(), days)
        if completed_only:
            hit = self._hist_cache.get(key)
            if hit is not None:
                return hit
        frm = datetime.combine(today - timedelta(days=days), MARKET_OPEN)
        to = min(datetime.combine(end, MARKET_CLOSE), datetime.now())
        try:
            raw = self.broker.get_historical_data(token, frm, to, interval) or []
        except Exception as exc:
            logger.debug("workspace history failed (%s %s): %s", token, interval, exc)
            raw = []
        out = []
        for c in raw:
            dt = ind.candle_dt(c)
            if dt is None:
                continue
            c["_dt"] = dt
            c["_d"] = dt.date()
            out.append(c)
        out.sort(key=lambda c: c["_dt"])
        if completed_only:
            self._hist_cache[key] = out
        return out

    def _quotes(self, keys: list[str]) -> dict:
        out: dict = {}
        for i in range(0, len(keys), QUOTE_CHUNK):
            chunk = keys[i:i + QUOTE_CHUNK]
            try:
                out.update(self.broker.get_quote(chunk) or {})
            except Exception as exc:
                logger.debug("workspace quote chunk failed: %s", exc)
        return out

    @staticmethod
    def _synthetic_today(q: dict, ltp: float, volume: float) -> Optional[dict]:
        """Today's daily bar, built from the quote so the daily history fetch can
        stop at yesterday (and stay cacheable)."""
        ohlc = (q or {}).get("ohlc") or {}
        o = float(ohlc.get("open") or 0) or ltp
        h = float(ohlc.get("high") or 0) or ltp
        l = float(ohlc.get("low") or 0) or ltp
        if not ltp:
            return None
        today = date.today()
        dt = datetime.combine(today, MARKET_OPEN)
        return {"date": dt, "_dt": dt, "_d": today, "open": o, "high": max(h, ltp),
                "low": min(l, ltp) if l else ltp, "close": ltp, "volume": volume}

    # ── the scan ─────────────────────────────────────────────────────
    def scan(self, symbols: list[str], overrides=None) -> dict:
        started = datetime.now()
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            keys = [k for k in (cfg["enabled"] or sig.STRATEGY_KEYS) if k in sig.EVALUATORS]
            if not keys:
                keys = list(sig.STRATEGY_KEYS)
            needs = sig.needed_data(keys)
            tf = cfg["timeframe"]
            today = date.today()

            names, seen = [], set()
            for s in symbols or []:
                n = (s or "").strip().upper()
                if n and n not in seen:
                    seen.add(n)
                    names.append(n)
            total = len(names)
            cap = int(cfg["max_stocks"])
            truncated = bool(cap and total > cap)
            if cap:
                names = names[:cap]

            # 1. resolve tokens
            resolved, unresolved = [], []
            for n in names:
                try:
                    token, exch = self.universe.resolve_equity_token(n)
                except Exception:
                    token, exch = None, None
                if token:
                    resolved.append({"underlying": n, "token": int(token), "exchange": exch})
                else:
                    unresolved.append(n)

            # 2. one batched quote round for the stocks, one for their futures
            futs = self._futures_map()
            eq_keys = [f"{r['exchange']}:{r['underlying']}" for r in resolved]
            eq_q = self._quotes(eq_keys)
            fno_names = [r["underlying"] for r in resolved if r["underlying"] in futs]
            fut_q = self._quotes([f"NFO:{futs[n]['tradingsymbol']}" for n in fno_names]) if fno_names else {}

            # 3. per stock: candles → evaluators
            rows = []
            for r in resolved:
                name, token, exch = r["underlying"], r["token"], r["exchange"]
                q = eq_q.get(f"{exch}:{name}") or {}
                ltp = float(q.get("last_price") or 0)
                volume = float(q.get("volume") or q.get("volume_traded") or 0)
                ohlc = q.get("ohlc") or {}
                prev_close = float(ohlc.get("close") or 0)
                fut = futs.get(name)
                oi = None
                if fut:
                    fq = fut_q.get(f"NFO:{fut['tradingsymbol']}") or {}
                    oi = _fmt_qty(fq.get("oi"))

                ctx = {"symbol": name, "exchange": exch, "ltp": ltp, "volume": volume,
                       "tf": tf, "cfg": cfg, "today": today, "quote": q}
                try:
                    if "tf" in needs:
                        ctx["tf_candles"] = self._history(
                            token, tf, TF_HISTORY_DAYS.get(tf, 30), completed_only=False)
                    if "daily" in needs:
                        d_days = int(cfg["hammer_lookback"] * 1.6) + 45
                        daily = list(self._history(token, "day", d_days, completed_only=True))
                        synth = self._synthetic_today(q, ltp, volume)
                        if synth and (not daily or daily[-1]["_d"] < today):
                            daily.append(synth)
                        ctx["daily"] = daily
                    if "hour" in needs:
                        ctx["hour"] = self._history(
                            token, "60minute", int(cfg["first_hour_days"]) * 3 + 12,
                            completed_only=True)
                except Exception as exc:
                    logger.debug("workspace candles failed for %s: %s", name, exc)

                results = sig.evaluate(ctx, keys)
                matched = [k for k, v in results.items() if v.get("ok") is True]
                applicable = [k for k, v in results.items() if v.get("ok") is not None]
                rows.append({
                    "underlying": name, "exchange": exch, "is_fno": bool(fut),
                    "ltp": round(ltp, 2) if ltp else None,
                    "change_pct": ind.pct(ltp, prev_close) if (ltp and prev_close) else None,
                    "volume": volume or None, "oi": oi,
                    "lot_size": fut.get("lot_size") if fut else None,
                    "future": fut.get("tradingsymbol") if fut else None,
                    "score": len(matched), "applicable": len(applicable),
                    "matched": matched, "signals": results,
                })

            min_score = int(cfg["min_score"])
            if min_score:
                rows = [r for r in rows if r["score"] >= min_score]
            rows.sort(key=lambda r: (-r["score"], -(r["change_pct"] or 0), r["underlying"]))

            took = round((datetime.now() - started).total_seconds(), 1)
            return {"status": "ok", "timeframe": tf, "date": today.isoformat(),
                    "strategies": [s for s in sig.STRATEGIES if s["key"] in keys],
                    "keys": keys, "max_score": len(keys), "rows": rows,
                    "scanned": len(resolved), "requested": total, "truncated": truncated,
                    "unresolved": unresolved[:50], "config": cfg, "took_secs": took,
                    "generated_at": started.strftime("%Y-%m-%d %H:%M:%S")}


NL = "\n"


def format_scan_message(result: dict, cfg: dict) -> str:
    """Crisp Telegram digest — the stocks clearing the most strategies."""
    rows = [r for r in (result.get("rows") or [])
            if r["score"] >= int(cfg.get("telegram_min_score", 0) or 0) and r["score"] > 0]
    top = rows[:int(cfg.get("telegram_top_n", 15) or 15)]
    lines = ["🎯 <b>EQUITY WORKSPACE — SMART SCAN</b>",
             f"{result.get('date')}  ·  {result.get('timeframe')}  ·  "
             f"{result.get('scanned', 0)} stocks  ·  max score {result.get('max_score', 0)}"]
    if not top:
        lines += ["", "No stock clears the minimum score right now."]
        return NL.join(lines)
    by_key = {s["key"]: s for s in (result.get("strategies") or [])}
    lines.append("")
    for r in top:
        names = ", ".join(by_key.get(k, {}).get("short", k) for k in r["matched"])
        ltp = f"₹{r['ltp']}" if r.get("ltp") else "—"
        chg = f" ({r['change_pct']:+.2f}%)" if r.get("change_pct") is not None else ""
        lines.append(f"<b>{r['underlying']}</b>  {r['score']}/{r['applicable']}  ·  {ltp}{chg}")
        lines.append(f"   {names}")
    if len(rows) > len(top):
        lines += ["", f"… and {len(rows) - len(top)} more on the workspace."]
    lines += ["", "Legend: " + " · ".join(
        f"{s['short']}={s['name']}" for s in (result.get("strategies") or []))]
    return NL.join(lines)
