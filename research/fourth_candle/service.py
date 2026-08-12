"""
4th-Candle Strategy — backtest + simulate service (read-only research paths).

Reuses the existing Broker (historical candles) and the straddle module's F&O
Universe (instrument dump → equity token, F&O list, expiry, ATM strike, option
resolution, lot size). Backtest/simulate NEVER place orders. Per stock/day it:
  1. builds 5-min candles, classifies the first 3 candles,
  2. marks the 4th-candle high/low,
  3. finds the breakout, buys the ATM CE/PE, simulates to target/SL or expiry,
  4. emits max profit / max loss / current MTM per trade.
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
from research.fourth_candle import calculations as calc
from research.fourth_candle.config import (
    load_config, save_config, sanitize, TIMEFRAME,
)

logger = get_logger("research.fourth_candle")

MARKET_OPEN = datetime.min.time().replace(hour=9, minute=15)
MARKET_CLOSE = datetime.min.time().replace(hour=15, minute=30)


class FourthCandleResearch:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._ul_cache: dict = {}
        self._opt_cache: dict = {}

    # ── config ──
    def load_config(self):
        return load_config()

    def save_config(self, partial):
        return save_config(partial)

    # ── candle fetch ──
    @staticmethod
    def _fetch_window(end: date):
        """(to, is_today). Kite's historical API returns nothing when to_date is in
        the future, so for the current day clamp `to` to now (not 15:30)."""
        now = datetime.now()
        eod = datetime.combine(end, MARKET_CLOSE)
        return (min(eod, now), end >= now.date())

    def _underlying_5m(self, token: int, start: date, end: date) -> list[dict]:
        key = (token, start, end)
        to, is_today = self._fetch_window(end)
        if not is_today and key in self._ul_cache:   # today is still forming — don't reuse
            return self._ul_cache[key]
        frm = datetime.combine(start, MARKET_OPEN)
        try:
            raw = self.broker.get_historical_data(token, frm, to, TIMEFRAME) or []
        except Exception as exc:
            logger.warning("underlying 5m failed (%s): %s", token, exc)
            raw = []
        for c in raw:
            c["_dt"] = _candle_dt(c)
        if not is_today:
            self._ul_cache[key] = raw
        return raw

    def _option_ohlc(self, token: int, start: date, end: date) -> dict:
        key = (token, start, end)
        to, is_today = self._fetch_window(end)
        if not is_today and key in self._opt_cache:
            return self._opt_cache[key]
        frm = datetime.combine(start, MARKET_OPEN)
        series = {}
        try:
            for c in self.broker.get_historical_data(token, frm, to, TIMEFRAME) or []:
                dt = _candle_dt(c)
                if dt is not None:
                    series[dt] = (float(c.get("close", 0) or 0), float(c.get("high", 0) or 0),
                                  float(c.get("low", 0) or 0))
        except Exception as exc:
            logger.warning("option ohlc failed (%s): %s", token, exc)
        if not is_today:
            self._opt_cache[key] = series
        return series

    # ── one stock/day → a trade row (or None) ──
    def _row_for_day(self, name, day, day_candles, cfg) -> Optional[dict]:
        an = calc.analyze_day(day_candles)
        if not an:
            return None
        base = {"date": day.isoformat(), "underlying": name,
                "colors": an["colors"], "bias": an["bias"],
                "fourth_high": an["fourth_high"], "fourth_low": an["fourth_low"]}
        if not an["bias"]:
            return {**base, "status": "NO TRADE", "notes": "First 3 candles mixed — no bias"}
        bo = calc.find_breakout(day_candles, an, entry_cutoff=cfg["entry_cutoff"])
        if not bo:
            return {**base, "status": "NO BREAKOUT",
                    "notes": f"{an['bias'].upper()} bias but no break by {cfg['entry_cutoff']}"}

        # breakout found — carry it into any diagnostic row so a skipped stock is
        # visible with a reason instead of silently disappearing
        bdiag = {**base, "breakout_time": bo["dt"].strftime("%d-%b %H:%M"),
                 "opt_type": bo["opt_type"]}
        expiry = self.universe.expiry_for(name, cfg["expiry_type"], day)
        if not expiry:
            return {**bdiag, "status": "NO OPTION DATA", "notes": "No option expiry resolved"}
        atm = self.universe.atm_strike(name, expiry, bo["underlying"])
        if atm is None:
            return {**bdiag, "status": "NO OPTION DATA", "notes": "No ATM strike resolved",
                    "expiry": expiry.isoformat()}
        opt = self.universe.resolve(name, expiry, atm, bo["opt_type"])
        if not opt:
            return {**bdiag, "status": "NO OPTION DATA", "expiry": expiry.isoformat(),
                    "strike": atm, "notes": f"No {bo['opt_type']} contract for {atm}"}
        lot = int(self.universe.lot_size(name) or opt.get("lot_size", 0) or 0)
        qty = lot * int(cfg["lots"])

        end_day = expiry if cfg.get("hold_to_expiry", True) else day
        series = self._option_ohlc(opt["token"], day, end_day)
        entry_dt = bo["dt"]
        rec = series.get(entry_dt)
        if not rec or not rec[0]:
            return {**bdiag, "status": "NO OPTION DATA", "symbol": opt["tradingsymbol"],
                    "strike": atm, "expiry": expiry.isoformat(),
                    "notes": f"No option price at breakout {bo['dt'].strftime('%H:%M')} "
                             f"({opt['tradingsymbol']}) — thin/illiquid or data gap"}
        entry = rec[0]
        squareoff = calc._parse_hhmm(cfg["square_off"])
        forward = [(dt, series[dt][0], series[dt][1], series[dt][2]) for dt in sorted(series)
                   if dt > entry_dt and dt.date() <= end_day
                   and not (dt.date() == expiry and dt.time() > squareoff)]
        today = date.today()
        square_off_reached = (expiry < today) or (expiry == today and datetime.now().time() >= squareoff)

        target, stop = calc.resolve_target_sl(entry, cfg)
        sim = calc.simulate_option(entry, forward, target=target, stop=stop, qty=qty,
                                   square_off_reached=square_off_reached)

        cost = 0.0
        if cfg.get("apply_costs") and not sim["open"] and sim["exit"] is not None:
            cc = costs.cost_config(cfg, "option")
            cost = round(costs.roundtrip_cost(entry, sim["exit"], qty, **cc), 2)
        mtm = round(sim["mtm"] - cost, 2) if (cfg.get("apply_costs") and not sim["open"]) else sim["mtm"]
        exit_dt = sim["exit_dt"]
        hold_days = (exit_dt.date() - entry_dt.date()).days if exit_dt else None
        # granular hold — same-day target/SL should still show how long it was held
        hold_label = None
        if exit_dt:
            secs = max(0, int((exit_dt - entry_dt).total_seconds()))
            d, rem = divmod(secs, 86400)
            h, m = divmod(rem, 3600)[0], (rem % 3600) // 60
            if d >= 1:
                hold_label = f"{d}d {h}h" if h else f"{d}d"
            elif h >= 1:
                hold_label = f"{h}h {m}m" if m else f"{h}h"
            else:
                hold_label = f"{m}m"

        return {
            **base, "breakout_time": entry_dt.strftime("%d-%b %H:%M"), "opt_type": bo["opt_type"],
            "symbol": opt["tradingsymbol"], "strike": atm, "expiry": expiry.isoformat(),
            "lot": lot, "qty": qty, "entry": round(entry, 2),
            "target": target, "sl": stop,
            "exit": sim["exit"], "exit_time": exit_dt.strftime("%d-%b %H:%M") if exit_dt else None,
            "exit_reason": sim["exit_reason"], "mtm": mtm, "cost": cost,
            "max_profit": sim["max_profit"], "max_loss": sim["max_loss"],
            "status": sim["exit_reason"], "open": sim["open"], "hold_days": hold_days,
            "hold_label": hold_label, "entry_time": entry_dt.strftime("%d-%b %H:%M"),
            "notes": self._note(sim, an["bias"], hold_days),
        }

    @staticmethod
    def _note(sim, bias, hold_days):
        hold = f" (held {hold_days}d)" if hold_days else ""
        side = "CALL" if bias == "call" else "PUT"
        if sim["open"]:
            return f"{side} OPEN — running{hold}"
        return {"TARGET": f"{side} hit target{hold}", "STOP": f"{side} hit stop{hold}"}.get(
            sim["exit_reason"], f"{side} squared off on expiry{hold}")

    # ── backtest across a stock's days ──
    def backtest_stock(self, name: str, days: list[date], cfg: dict,
                       include_non_trades: bool = False) -> list[dict]:
        token = self.universe.nse_token(name)
        if not token:
            return []
        candles = self._underlying_5m(token, min(days), max(days))
        if not candles:
            return []
        # only stocks that HAD a bias but didn't complete a trade are diagnostic-worthy;
        # mixed-candle days (NO TRADE) are noise and are never shown.
        includable = {"NO BREAKOUT", "NO OPTION DATA"}
        rows = []
        for day in days:
            dc = calc.day_candles(candles, day)
            if len(dc) < 5:
                continue
            try:
                row = self._row_for_day(name, day, dc, cfg)
                if not row:
                    continue
                status = row.get("status")
                if status == "NO TRADE":
                    continue                     # mixed first-3 candles — skip entirely
                if status in includable and not include_non_trades:
                    continue
                rows.append(row)
            except Exception as exc:
                logger.debug("row_for_day %s %s failed: %s", name, day, exc)
        return rows

    def backtest(self, overrides=None, *, symbol=None, symbols=None, start=None, end=None,
                 include_non_trades=False) -> dict:
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
            # Max Stocks caps how many symbols are scanned (0 = all). 5-min all-F&O
            # runs are heavy but complete; the user controls the trade-off.
            total = len(names)
            cap = int(cfg["max_stocks"])          # 0 = scan everything
            truncated = bool(cap and total > cap)
            if cap:
                names = names[:cap]
            rows, t0, scanned = [], _time.monotonic(), 0
            for nm in names:
                try:
                    rows.extend(self.backtest_stock(nm, days, cfg, include_non_trades))
                except Exception as exc:
                    logger.warning("backtest_stock %s failed: %s", nm, exc)
                scanned += 1
            rows.sort(key=lambda r: (r["date"], r.get("breakout_time") or "", r["underlying"]))
            note = (f"Showing first {len(names)} of {total} F&O stocks (Max Stocks = {cap}). "
                    "Set Max Stocks to 0 to scan all — heavier but complete." if truncated else None)
            return {"status": "ok", "mode": mode, "start": s.isoformat(), "end": e.isoformat(),
                    "stocks_scanned": scanned, "universe_size": total, "truncated": truncated,
                    "note": note, "config": cfg, "stats": self._stats(rows),
                    "rows": rows, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # ── detailed single-stock simulate (lock-free: never block on a running backtest) ──
    def simulate(self, symbol: str, overrides=None, day: Optional[str] = None) -> dict:
        if True:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            name = symbol.strip().upper()
            try:
                d = date.fromisoformat(day) if day else self._latest_weekday()
            except ValueError:
                return {"status": "error", "message": "Invalid date"}
            token = self.universe.nse_token(name)
            if not token:
                return {"status": "error", "message": f"{name}: no NSE token / not F&O"}
            candles = self._underlying_5m(token, d, d)
            dc = calc.day_candles(candles, d)
            if len(dc) < 5:
                return {"status": "error", "message": f"{name} {d}: not enough 5-min candles"}
            an = calc.analyze_day(dc)
            timeline = [{"time": c["_dt"].strftime("%H:%M"), "open": round(float(c["open"]), 2),
                         "high": round(float(c["high"]), 2), "low": round(float(c["low"]), 2),
                         "close": round(float(c["close"]), 2), "color": calc.candle_color(c)}
                        for c in dc]
            row = self._row_for_day(name, d, dc, cfg) if an else None
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
    def _trading_days(s: date, e: date) -> list[date]:
        out, d = [], s
        while d <= e:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out

    @staticmethod
    def _stats(rows: list[dict]) -> dict:
        traded = [r for r in rows if r.get("qty")]
        n = len(traded)
        if not n:
            return {"total": 0, "wins": 0, "win_rate": 0.0, "total_mtm": 0.0,
                    "calls": 0, "puts": 0, "best": 0.0, "worst": 0.0}
        wins = [r for r in traded if (r["mtm"] or 0) > 0]
        mtms = [r["mtm"] or 0 for r in traded]
        return {"total": n, "wins": len(wins), "win_rate": round(len(wins) / n * 100, 1),
                "total_mtm": round(sum(mtms), 2), "best": round(max(mtms), 2),
                "worst": round(min(mtms), 2),
                "calls": sum(1 for r in traded if r.get("opt_type") == "CE"),
                "puts": sum(1 for r in traded if r.get("opt_type") == "PE"),
                "total_cost": round(sum(float(r.get("cost") or 0) for r in traded), 2)}
