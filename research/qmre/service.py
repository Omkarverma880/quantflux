"""
QMRE service — data access + orchestration (read-only market data; paper only).

Builds the point-in-time context each stock needs (prior-day levels, ATR, expected
volume-by-now, benchmark return, optional live depth) and feeds the SHARED engine
for Live, Replay, Single-Stock and Backtest. Never places an order.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, time as dtime, timezone
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.prev_period_vwap import _candle_dt
from research.pmvwap_straddle.universe import Universe
from research.qmre import engine, paper
from research.qmre.config import load_config, save_config, TIMEFRAME
from research.qmre.regime import regime_from_pulse

logger = get_logger("research.qmre")

IST = timezone(timedelta(hours=5, minutes=30))
MKT_OPEN = dtime(9, 15)
MKT_CLOSE = dtime(15, 30)
SESSION_MIN = (15 * 60 + 30) - (9 * 60 + 15)      # 375 minutes


class QMREService:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._daily_cache: dict = {}
        self._intra_cache: dict = {}

    # ── universe ──
    def _names(self, cfg, symbols) -> list[str]:
        if symbols:
            return [str(s).strip().upper() for s in symbols if str(s).strip()]
        names = [x["name"] for x in self.universe.equities()]
        if int(cfg.get("max_stocks", 0)) > 0:
            names = names[: int(cfg["max_stocks"])]
        return names

    # ── candle fetch (look-ahead-safe: never fetch beyond cutoff) ──
    def _daily(self, token: int, end: date, lookback: int) -> list[dict]:
        key = (token, end, lookback)
        if key in self._daily_cache:
            return self._daily_cache[key]
        frm = datetime.combine(end - timedelta(days=lookback * 2 + 20), MKT_OPEN)
        to = datetime.combine(end, MKT_CLOSE)
        try:
            rows = self.broker.get_historical_data(token, frm, to, "day") or []
        except Exception:
            rows = []
        for c in rows:
            c["_d"] = self._row_date(c)
        self._daily_cache[key] = rows
        return rows

    def _intraday(self, token: int, day: date, cutoff: datetime) -> list[dict]:
        # cache per (token, day); for a still-forming today, refresh at most once
        # per 5-min bucket so auto-refresh doesn't re-hammer Kite every scan.
        today = datetime.now(IST).date()
        now = datetime.now()
        bucket = now.replace(second=0, microsecond=0, minute=(now.minute // 5) * 5)
        key = (token, day)
        entry = self._intra_cache.get(key)
        rows = entry[1] if entry else None
        fresh = entry and (day < today or entry[0] == bucket)
        if rows is None or not fresh:
            frm = datetime.combine(day, MKT_OPEN)
            to = min(datetime.combine(day, MKT_CLOSE), now)
            try:
                rows = self.broker.get_historical_data(token, frm, to, TIMEFRAME) or []
            except Exception:
                rows = []
            for c in rows:
                c["_dt"] = _candle_dt(c)
            self._intra_cache[key] = (bucket if day >= today else day, rows)
        return [c for c in rows if c.get("_dt") and c["_dt"] <= cutoff]

    @staticmethod
    def _row_date(c) -> Optional[date]:
        d = c.get("date")
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, date):
            return d
        try:
            return date.fromisoformat(str(d)[:10])
        except Exception:
            return None

    @staticmethod
    def _atr(daily: list[dict], period: int) -> float:
        if len(daily) < 2:
            return 0.0
        trs = []
        for i in range(1, len(daily)):
            h = float(daily[i]["high"]); l = float(daily[i]["low"])
            pc = float(daily[i - 1]["close"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        trs = trs[-period:]
        return sum(trs) / len(trs) if trs else 0.0

    def _stock_ctx(self, name, token, day, cutoff, cfg) -> dict:
        daily = self._daily(token, day - timedelta(days=1), int(cfg["rvol_lookback_days"]))
        prev = [c for c in daily if c.get("_d") and c["_d"] < day]
        prev_close = float(prev[-1]["close"]) if prev else 0.0
        prev_high = float(prev[-1]["high"]) if prev else 0.0
        prev_low = float(prev[-1]["low"]) if prev else 0.0
        atr = self._atr(prev[-(int(cfg["atr_period"]) + 1):], int(cfg["atr_period"]))
        vols = [float(c.get("volume", 0) or 0) for c in prev[-int(cfg["rvol_lookback_days"]):]]
        avg_day_vol = sum(vols) / len(vols) if vols else 0.0
        vals = [float(c.get("close", 0) or 0) * float(c.get("volume", 0) or 0)
                for c in prev[-int(cfg["rvol_lookback_days"]):]]
        avg_day_value_cr = (sum(vals) / len(vals) / 1e7) if vals else None
        # time-of-day RVOL normalisation (approx: session fraction elapsed)
        elapsed = max(1.0, (cutoff.hour * 60 + cutoff.minute) - (9 * 60 + 15))
        frac = min(1.0, elapsed / SESSION_MIN)
        expected_cum_vol = avg_day_vol * frac
        return {"prev_close": prev_close, "prev_high": prev_high, "prev_low": prev_low,
                "atr": round(atr, 2), "atr_pct": round(atr / prev_close * 100, 2) if prev_close else 0,
                "expected_cum_vol": expected_cum_vol, "avg_day_value_cr": round(avg_day_value_cr, 2) if avg_day_value_cr else None}

    # ── market regime + benchmark return ──
    def _index_token(self, name="NIFTY 50") -> Optional[int]:
        try:
            for inst in self.broker.get_instruments("NSE") or []:
                if inst.get("tradingsymbol") == name or inst.get("name") == name:
                    return int(inst["instrument_token"])
        except Exception:
            pass
        return None

    def _market_ctx(self, day, cutoff, cfg, live: bool) -> dict:
        # regime via Market Pulse when live; else neutral (kept simple/robust)
        regime = {"score": 0.0, "label": "NEUTRAL", "available": False}
        if live:
            try:
                from research.market_pulse import MarketPulse
                mp = MarketPulse(self.broker)
                regime = regime_from_pulse(mp.snapshot())
            except Exception as exc:
                logger.debug("qmre regime failed: %s", exc)
        # benchmark intraday return to cutoff
        bench_ret = 0.0
        tok = self._index_token(cfg.get("rs_benchmark", "NIFTY 50"))
        if tok:
            ic = self._intraday(tok, day, cutoff)
            dailyi = self._daily(tok, day - timedelta(days=1), 5)
            pi = [c for c in dailyi if c.get("_d") and c["_d"] < day]
            if ic and pi:
                pc = float(pi[-1]["close"]) or float(ic[0]["open"])
                bench_ret = (float(ic[-1]["close"]) - pc) / pc * 100 if pc else 0.0
        return {"regime_score": regime["score"], "regime_label": regime["label"],
                "regime_available": regime["available"], "bench_ret_pct": round(bench_ret, 2),
                "day_change_pct": round(bench_ret, 2)}

    def _depth_batch(self, names) -> dict:
        """One batched Kite quote() for the whole universe (not one call/stock).
        Returns {symbol: depth-imbalance dict}."""
        out: dict = {}
        keys = [f"NSE:{n}" for n in names]
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            try:
                q = self.broker.get_quote(chunk) or {}
            except Exception:
                q = {}
            for k, v in q.items():
                d = (v or {}).get("depth") or {}
                bid = sum(float(l.get("quantity", 0) or 0) for l in (d.get("buy") or [])[:5])
                ask = sum(float(l.get("quantity", 0) or 0) for l in (d.get("sell") or [])[:5])
                tot = bid + ask
                out[k.split(":", 1)[-1]] = ({"available": True, "imbalance": round((bid - ask) / tot, 3),
                                             "bid": int(bid), "ask": int(ask)} if tot > 0 else {"available": False})
        return out

    # ── LIVE / REPLAY scan (same path; live=True adds depth + regime) ──
    def scan(self, cfg, *, symbols=None, day=None, at_time=None, top_n=None, live=True) -> dict:
        with self._lock:
            names = self._names(cfg, symbols)
            if not names:
                return {"status": "error", "message": "No stocks in universe"}
            if day:
                d = date.fromisoformat(day)
                hh, mm = (at_time or "15:15").split(":")
                cutoff = datetime.combine(d, dtime(int(hh), int(mm)))
                live = False
            else:
                now = datetime.now()
                d, cutoff = now.date(), now
            market = self._market_ctx(d, cutoff, cfg, live)
            depth_map = self._depth_batch(names) if live else {}
            items = []
            for nm in names:
                token, _ex = self.universe.resolve_equity_token(nm)
                if not token:
                    continue
                candles = self._intraday(token, d, cutoff)
                if len(candles) < 3:
                    continue
                if cfg.get("min_price") and float(candles[-1]["close"]) < float(cfg["min_price"]):
                    continue
                ctx = self._stock_ctx(nm, token, d, cutoff, cfg)
                if live:
                    ctx["depth"] = depth_map.get(nm)
                items.append({"symbol": nm, "candles": candles, "ctx": ctx})
            ranked = engine.rank(items, market, cfg)
            n = int(top_n or cfg.get("top_n", 5))
            classes = {}
            for c in ranked:
                classes[c["class"]] = classes.get(c["class"], 0) + 1
            return {"status": "ok", "live": live, "date": d.isoformat(),
                    "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                    "market": market, "scanned": len(items), "counts": classes,
                    "top": ranked[:n], "all": ranked, "config_version": cfg["strategy_version"],
                    "generated_at": datetime.now(IST).strftime("%H:%M:%S")}

    # ── SINGLE STOCK forensics: score at time + what happened after ──
    def single_stock(self, name, cfg, *, day=None, at_time=None) -> dict:
        with self._lock:
            name = (name or "").strip().upper()
            token, ex = self.universe.resolve_equity_token(name)
            if not token:
                return {"status": "error", "message": f"{name}: not a tradable NSE/BSE equity"}
            d = date.fromisoformat(day) if day else datetime.now(IST).date()
            hh, mm = (at_time or "09:45").split(":")
            cutoff = datetime.combine(d, dtime(int(hh), int(mm)))
            candles = self._intraday(token, d, cutoff)
            if len(candles) < 3:
                return {"status": "error", "message": "Not enough candles by that time"}
            market = self._market_ctx(d, cutoff, cfg, live=(day is None))
            ctx = self._stock_ctx(name, token, d, cutoff, cfg)
            cand = engine.evaluate(candles, {**ctx, "symbol": name}, market, cfg)
            # what happened after — need the full day (+ swing days)
            full = self._intraday(token, d, datetime.combine(d, MKT_CLOSE))
            after = self._outcomes(full, cutoff, cand["risk"]["entry"]) if cand else []
            return {"status": "ok", "symbol": name, "date": d.isoformat(),
                    "at_time": at_time, "market": market, "candidate": cand,
                    "outcomes": after, "config_version": cfg["strategy_version"]}

    @staticmethod
    def _outcomes(full: list[dict], cutoff: datetime, entry: float) -> list[dict]:
        fwd = [c for c in full if c.get("_dt") and c["_dt"] > cutoff]
        out = []
        for label, mins in [("5m", 5), ("10m", 10), ("15m", 15), ("30m", 30), ("60m", 60), ("120m", 120)]:
            upto = [c for c in fwd if (c["_dt"] - cutoff).total_seconds() <= mins * 60]
            if not upto:
                continue
            px = float(upto[-1]["close"])
            mfe = max(float(c["high"]) for c in upto)
            mae = min(float(c["low"]) for c in upto)
            out.append({"t": label, "price": round(px, 2), "ret_pct": round((px - entry) / entry * 100, 2),
                        "mfe_pct": round((mfe - entry) / entry * 100, 2),
                        "mae_pct": round((mae - entry) / entry * 100, 2)})
        if fwd:
            px = float(fwd[-1]["close"])
            out.append({"t": "EOD", "price": round(px, 2), "ret_pct": round((px - entry) / entry * 100, 2),
                        "mfe_pct": round((max(float(c["high"]) for c in fwd) - entry) / entry * 100, 2),
                        "mae_pct": round((min(float(c["low"]) for c in fwd) - entry) / entry * 100, 2)})
        return out

    # ── BACKTEST: one trade per stock per day, entered at first qualifying cutoff ──
    def backtest(self, cfg, *, symbols=None, start=None, end=None) -> dict:
        with self._lock:
            names = self._names(cfg, symbols)
            if not names:
                return {"status": "error", "message": "No stocks in universe"}
            s = date.fromisoformat(start) if start else datetime.now(IST).date()
            e = date.fromisoformat(end) if end else s
            if e < s:
                s, e = e, s
            days = [d for d in self._daterange(s, e) if d.weekday() < 5]
            entry_classes = set(cfg.get("entry_classes", ["A+", "A"]))
            interval = int(cfg.get("replay_interval_min", 5))
            eod_h, eod_m = (cfg.get("eod_exit_time", "15:15")).split(":")
            trades = []
            for d in days:
                market = self._market_ctx(d, datetime.combine(d, dtime(int(eod_h), int(eod_m))), cfg, live=False)
                day_taken = 0
                for nm in names:
                    if cfg.get("max_trades_per_day") and day_taken >= int(cfg["max_trades_per_day"]):
                        break
                    token, _ex = self.universe.resolve_equity_token(nm)
                    if not token:
                        continue
                    full = self._intraday(token, d, datetime.combine(d, MKT_CLOSE))
                    if len(full) < 6:
                        continue
                    ctx = self._stock_ctx(nm, token, d, datetime.combine(d, MKT_OPEN), cfg)
                    trade = self._sim_day(nm, full, d, market, ctx, cfg, entry_classes, interval)
                    if trade:
                        trades.append(trade)
                        day_taken += 1
            trades.sort(key=lambda t: (t["date"], t["entry_time"]))
            return {"status": "ok", "start": s.isoformat(), "end": e.isoformat(),
                    "days": len(days), "config": cfg, "config_version": cfg["strategy_version"],
                    "trades": trades, "stats": self._bt_stats(trades, cfg),
                    "generated_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")}

    def _sim_day(self, name, full, day, market, ctx, cfg, entry_classes, interval):
        # walk cutoffs; enter at the first candle whose class qualifies
        eod_h, eod_m = (cfg.get("eod_exit_time", "15:15")).split(":")
        eod = datetime.combine(day, dtime(int(eod_h), int(eod_m)))
        or_min = int(cfg["opening_range_min"])
        start_dt = datetime.combine(day, MKT_OPEN) + timedelta(minutes=or_min)
        cutoffs = [c["_dt"] for c in full if c.get("_dt") and start_dt <= c["_dt"] <= eod]
        cutoffs = cutoffs[::max(1, interval // 5)]
        for cutoff in cutoffs:
            candles = [c for c in full if c["_dt"] <= cutoff]
            cand = engine.evaluate(candles, {**ctx, "symbol": name}, market, cfg)
            if not cand or cand["class"] not in entry_classes:
                continue
            rp, sz = cand["risk"], cand["sizing"]
            if sz["qty"] <= 0:
                return None
            # first qualifying signal → attempt a fill per entry type (realistic):
            #   NOW      → fill at the cutoff candle
            #   BREAK    → fill only when a later candle trades through the buy-stop
            #   PULLBACK → fill only when a later candle retraces to the entry
            future = [c for c in full if c["_dt"] > cutoff and c["_dt"] <= eod]
            etype, entry = rp["entry_type"], rp["entry"]
            fill_dt = None
            if etype == "NOW":
                fill_dt = cutoff
            elif etype == "BREAK":
                for c in future:
                    if float(c["high"]) >= entry:
                        fill_dt = c["_dt"]; break
            else:  # PULLBACK
                for c in future:
                    if float(c["low"]) <= entry:
                        fill_dt = c["_dt"]; break
            if fill_dt is None:
                return None                       # signalled but never filled → no trade
            fwd = [(c["_dt"], float(c["close"]), float(c["high"]), float(c["low"]))
                   for c in full if c["_dt"] > fill_dt and c["_dt"] <= eod]
            sim = paper.simulate_forward(entry, fwd, sl=rp["sl"], target=rp["target1"],
                                         qty=sz["qty"], square_off_reached=True, cfg=cfg)
            return {"date": day.isoformat(), "symbol": name, "entry_time": fill_dt.strftime("%H:%M"),
                    "signal_time": cutoff.strftime("%H:%M"), "entry_type": etype,
                    "class": cand["class"], "score": cand["score"], "regime": market["regime_label"],
                    "entry": sim["fill_entry"], "sl": rp["sl"], "target": rp["target1"], "rr": rp["rr"],
                    "qty": sz["qty"], "exit": sim["exit"],
                    "exit_time": sim["exit_dt"].strftime("%H:%M") if sim["exit_dt"] else None,
                    "exit_reason": sim["exit_reason"], "mtm": sim["mtm"], "cost": sim.get("cost", 0),
                    "mfe": sim["mfe"], "mae": sim["mae"], "rvol": cand["features"].get("rvol")}
        return None

    @staticmethod
    def _bt_stats(trades, cfg) -> dict:
        n = len(trades)
        if not n:
            return {"trades": 0, "win_rate": 0, "total_mtm": 0, "profit_factor": 0,
                    "expectancy": 0, "best": 0, "worst": 0, "avg_win": 0, "avg_loss": 0,
                    "max_drawdown": 0, "return_pct": 0}
        mtms = [t["mtm"] for t in trades]
        wins = [m for m in mtms if m > 0]
        losses = [m for m in mtms if m < 0]
        gross_w = sum(wins); gross_l = -sum(losses)
        pf = round(gross_w / gross_l, 2) if gross_l else (gross_w and 999.0 or 0.0)
        wr = len(wins) / n
        exp = round(wr * (gross_w / len(wins) if wins else 0) - (1 - wr) * (gross_l / len(losses) if losses else 0), 2)
        # equity curve drawdown
        eq, peak, dd = 0.0, 0.0, 0.0
        for m in mtms:
            eq += m; peak = max(peak, eq); dd = min(dd, eq - peak)
        cap = float(cfg.get("starting_capital", 1000000)) or 1
        return {"trades": n, "win_rate": round(wr * 100, 1), "total_mtm": round(sum(mtms), 2),
                "profit_factor": pf, "expectancy": exp, "best": round(max(mtms), 2), "worst": round(min(mtms), 2),
                "avg_win": round(gross_w / len(wins), 2) if wins else 0,
                "avg_loss": round(-gross_l / len(losses), 2) if losses else 0,
                "max_drawdown": round(dd, 2), "return_pct": round(sum(mtms) / cap * 100, 2)}

    @staticmethod
    def _daterange(s, e):
        d = s
        while d <= e:
            yield d
            d += timedelta(days=1)
