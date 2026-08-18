"""
4th-Candle CASH-EQUITY Strategy — backtest + simulate service (read-only).

Trades the STOCK directly (LONG on a CALL bias, SHORT on a PUT bias) as MIS
intraday or CNC holding, simulating target/SL on the same underlying 5-min
candles used to detect the breakout — so it works for long historical backtests
where option data doesn't exist. NEVER places orders.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.prev_period_vwap import _candle_dt
from research.pmvwap_straddle.universe import Universe
from research import costs
from research.fourth_candle_equity import calculations as calc
from research.fourth_candle_equity.config import load_config, save_config, sanitize, TIMEFRAME

logger = get_logger("research.fourth_candle_equity")

MARKET_OPEN = datetime.min.time().replace(hour=9, minute=15)
MARKET_CLOSE = datetime.min.time().replace(hour=15, minute=30)


class FourthCandleEquityResearch:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._ul_cache: dict = {}

    def load_config(self):
        return load_config()

    def save_config(self, partial):
        return save_config(partial)

    # ── candle fetch (clamp `to` to now for today; don't cache the forming day) ──
    def _underlying_5m(self, token: int, start: date, end: date) -> list[dict]:
        key = (token, start, end)
        now = datetime.now()
        is_today = end >= now.date()
        if not is_today and key in self._ul_cache:
            return self._ul_cache[key]
        frm = datetime.combine(start, MARKET_OPEN)
        to = min(datetime.combine(end, MARKET_CLOSE), now)
        try:
            raw = self.broker.get_historical_data(token, frm, to, TIMEFRAME) or []
        except Exception as exc:
            logger.warning("equity 5m failed (%s): %s", token, exc)
            raw = []
        for c in raw:
            c["_dt"] = _candle_dt(c)
        if not is_today:
            self._ul_cache[key] = raw
        return raw

    # ── one stock/day → a trade row ──
    def _row_for_day(self, name, day, all_candles, cfg) -> Optional[dict]:
        dc = calc.day_candles(all_candles, day)
        if len(dc) < 5:
            return None
        an = calc.analyze_day(dc, reverse=bool(cfg.get("reverse_signal")))
        if not an:
            return None
        base = {"date": day.isoformat(), "underlying": name, "colors": an["colors"],
                "bias": an["bias"], "fourth_high": an["fourth_high"], "fourth_low": an["fourth_low"]}
        if not an["bias"]:
            return {**base, "status": "NO TRADE", "notes": "First 3 candles mixed — no bias"}
        bo = calc.find_breakout(dc, an, entry_cutoff=cfg["entry_cutoff"])
        if not bo:
            return {**base, "status": "NO BREAKOUT",
                    "notes": f"{an['bias'].upper()} bias but no break by {cfg['entry_cutoff']}"}

        direction = calc.direction_for(an["bias"])
        side = "LONG" if direction == "long" else "SHORT"
        entry_dt = bo["dt"]
        entry = float(bo["underlying"])
        qty = calc.position_qty(cfg, entry)
        bdiag = {**base, "breakout_time": entry_dt.strftime("%d-%b %H:%M"), "side": side}
        if qty <= 0:
            return {**bdiag, "status": "NO QTY", "notes": "Capital/entry too small for 1 share"}

        product = cfg["product"]
        sq = calc._parse_hhmm(cfg["square_off"])
        today = date.today()
        if product == "MIS":
            forward = [(c["_dt"], float(c["close"]), float(c["high"]), float(c["low"]))
                       for c in all_candles if c.get("_dt") and c["_dt"] > entry_dt
                       and c["_dt"].date() == day and c["_dt"].time() <= sq]
            square_off_reached = (day < today) or (day == today and datetime.now().time() >= sq)
            end_day = day
        else:                                             # CNC holding
            end_day = day + timedelta(days=int(cfg["max_hold_days"]))
            forward = [(c["_dt"], float(c["close"]), float(c["high"]), float(c["low"]))
                       for c in all_candles if c.get("_dt") and c["_dt"] > entry_dt
                       and c["_dt"].date() <= end_day]
            square_off_reached = (end_day < today) or (end_day == today and datetime.now().time() >= sq)

        target, stop = calc.resolve_target_sl(entry, direction, cfg)
        sim = calc.simulate_equity(entry, forward, direction=direction, target=target,
                                   stop=stop, qty=qty, square_off_reached=square_off_reached)

        cost = 0.0
        if cfg.get("apply_costs") and not sim["open"] and sim["exit"] is not None:
            cc = costs.cost_config(cfg, "equity")
            cost = round(costs.roundtrip_cost(entry, sim["exit"], qty, **cc), 2)
        mtm = round(sim["mtm"] - cost, 2) if (cfg.get("apply_costs") and not sim["open"]) else sim["mtm"]
        exit_dt = sim["exit_dt"]
        hold_days = (exit_dt.date() - entry_dt.date()).days if exit_dt else None
        hold_label = None
        if exit_dt:
            secs = max(0, int((exit_dt - entry_dt).total_seconds()))
            d, rem = divmod(secs, 86400)
            h, m = divmod(rem, 3600)[0], (rem % 3600) // 60
            hold_label = (f"{d}d {h}h" if h else f"{d}d") if d >= 1 else (f"{h}h {m}m" if h else f"{m}m")

        return {
            **base, "breakout_time": entry_dt.strftime("%d-%b %H:%M"), "side": side,
            "qty": qty, "entry": round(entry, 2), "target": target, "sl": stop,
            "exit": sim["exit"], "exit_time": exit_dt.strftime("%d-%b %H:%M") if exit_dt else None,
            "exit_reason": sim["exit_reason"], "mtm": mtm, "cost": cost,
            "max_profit": sim["max_profit"], "max_loss": sim["max_loss"],
            "status": sim["exit_reason"], "open": sim["open"], "hold_days": hold_days,
            "hold_label": hold_label, "entry_time": entry_dt.strftime("%d-%b %H:%M"),
            "product": product,
            "notes": f"{side} {'squared off' if sim['exit_reason'] == 'SQUAREOFF' else sim['exit_reason'].lower()}",
        }

    # ── backtest across a stock's days ──
    def backtest_stock(self, name, days, cfg, include_non_trades=False) -> list[dict]:
        token, _exch = self.universe.resolve_equity_token(name)
        if not token:
            return []
        buffer = int(cfg["max_hold_days"]) + 3 if cfg["product"] == "CNC" else 0
        candles = self._underlying_5m(token, min(days), max(days) + timedelta(days=buffer))
        if not candles:
            return []
        includable = {"NO BREAKOUT", "NO QTY"}
        rows = []
        for day in days:
            try:
                row = self._row_for_day(name, day, candles, cfg)
                if not row:
                    continue
                status = row.get("status")
                if status == "NO TRADE":
                    continue
                if status in includable and not include_non_trades:
                    continue
                rows.append(row)
            except Exception as exc:
                logger.debug("equity row %s %s failed: %s", name, day, exc)
        return rows

    def backtest(self, overrides=None, *, symbol=None, symbols=None, start=None, end=None,
                 include_non_trades=False, apply_caps=True) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            try:
                s = date.fromisoformat(start) if start else self._latest_weekday()
                e = date.fromisoformat(end) if end else s
            except ValueError:
                return {"status": "error", "message": "Invalid date (YYYY-MM-DD)"}
            if e < s:
                s, e = e, s
            days = self._trading_days(s, e)
            if not days:
                return {"status": "error", "message": "No trading days in range"}
            if symbol:
                names, mode = [symbol.strip().upper()], "single"
            elif symbols:
                names = [str(x).strip().upper() for x in symbols if str(x).strip()]
                mode = "watchlist"
            else:
                names = [x["name"] for x in self.universe.equities()]
                mode = "multi"
            total = len(names)
            cap = int(cfg["max_stocks"])
            truncated = bool(cap and total > cap)
            if cap:
                names = names[:cap]
            rows, scanned = [], 0
            for nm in names:
                try:
                    rows.extend(self.backtest_stock(nm, days, cfg, include_non_trades))
                except Exception as exc:
                    logger.warning("equity backtest_stock %s failed: %s", nm, exc)
                scanned += 1
            rows.sort(key=lambda r: (r["date"], r.get("breakout_time") or "", r["underlying"]))
            signals_total = sum(1 for r in rows if r.get("qty"))
            caps_note = None
            if apply_caps:
                taken = self._apply_caps(rows, cfg, include_non_trades)
                if taken < signals_total:
                    caps_note = (f"Portfolio caps applied — {taken} of {signals_total} signals taken "
                                 f"(Max Positions {cfg['max_positions']}, Max Long {cfg['max_long']}, "
                                 f"Max Short {cfg['max_short']}). Matches Live/Paper. Uncheck 'Apply caps' for all.")
            note = (f"Showing first {len(names)} of {total} F&O stocks (Max Stocks = {cap})."
                    if truncated else None)
            return {"status": "ok", "mode": mode, "start": s.isoformat(), "end": e.isoformat(),
                    "stocks_scanned": scanned, "universe_size": total, "truncated": truncated,
                    "note": note, "caps_note": caps_note, "caps_applied": bool(apply_caps),
                    "signals_total": signals_total, "config": cfg, "stats": self._stats(rows),
                    "rows": rows, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    @staticmethod
    def _apply_caps(rows, cfg, include_non_trades) -> int:
        max_pos = int(cfg["max_positions"]); max_l = int(cfg["max_long"]); max_s = int(cfg["max_short"])
        per_day: dict = {}
        taken = 0
        trades = sorted((r for r in rows if r.get("qty")),
                        key=lambda r: (r["date"], r.get("breakout_time") or "", r["underlying"]))
        drop = []
        for r in trades:
            cnt = per_day.setdefault(r["date"], [0, 0, 0])   # [positions, long, short]
            is_long = r.get("side") == "LONG"
            full = (cnt[0] >= max_pos or (is_long and cnt[1] >= max_l)
                    or (not is_long and cnt[2] >= max_s))
            if full:
                if include_non_trades:
                    r["status"] = "CAP SKIPPED"
                    r["notes"] = f"portfolio cap reached ({r.get('side')})"
                    r["capped"] = True
                    r["qty"] = None
                else:
                    drop.append(id(r))
            else:
                cnt[0] += 1
                cnt[1] += 1 if is_long else 0
                cnt[2] += 0 if is_long else 1
                taken += 1
        if drop:
            drop_set = set(drop)
            rows[:] = [r for r in rows if id(r) not in drop_set]
        return taken

    # ── single-stock detailed simulate ──
    def simulate(self, symbol, overrides=None, day=None) -> dict:
        cfg = sanitize({**self.load_config(), **(overrides or {})})
        name = (symbol or "").strip().upper()
        try:
            d = date.fromisoformat(day) if day else self._latest_weekday()
        except ValueError:
            return {"status": "error", "message": "Invalid date"}
        token, _exch = self.universe.resolve_equity_token(name)
        if not token:
            return {"status": "error", "message": f"{name}: no NSE/BSE equity token"}
        buffer = int(cfg["max_hold_days"]) + 3 if cfg["product"] == "CNC" else 0
        candles = self._underlying_5m(token, d, d + timedelta(days=buffer))
        dc = calc.day_candles(candles, d)
        if len(dc) < 5:
            return {"status": "error", "message": f"{name} {d}: not enough 5-min candles"}
        an = calc.analyze_day(dc, reverse=bool(cfg.get("reverse_signal")))
        timeline = [{"time": c["_dt"].strftime("%H:%M"), "open": round(float(c["open"]), 2),
                     "high": round(float(c["high"]), 2), "low": round(float(c["low"]), 2),
                     "close": round(float(c["close"]), 2), "color": calc.candle_color(c)}
                    for c in dc]
        row = self._row_for_day(name, d, candles, cfg) if an else None
        return {"status": "ok", "symbol": name, "date": d.isoformat(), "config": cfg,
                "analysis": an, "timeline": timeline, "trade": row}

    # ── helpers ──
    @staticmethod
    def _latest_weekday() -> date:
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    @staticmethod
    def _trading_days(s, e) -> list[date]:
        out, d = [], s
        while d <= e:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out

    @staticmethod
    def _stats(rows) -> dict:
        traded = [r for r in rows if r.get("qty") and not r.get("capped")]
        n = len(traded)
        if not n:
            return {"total": 0, "wins": 0, "win_rate": 0.0, "total_mtm": 0.0,
                    "long": 0, "short": 0, "best": 0.0, "worst": 0.0}
        wins = [r for r in traded if (r["mtm"] or 0) > 0]
        mtms = [r["mtm"] or 0 for r in traded]
        return {"total": n, "wins": len(wins), "win_rate": round(len(wins) / n * 100, 1),
                "total_mtm": round(sum(mtms), 2), "best": round(max(mtms), 2), "worst": round(min(mtms), 2),
                "long": sum(1 for r in traded if r.get("side") == "LONG"),
                "short": sum(1 for r in traded if r.get("side") == "SHORT"),
                "total_cost": round(sum(float(r.get("cost") or 0) for r in traded), 2)}
