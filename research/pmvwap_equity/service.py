"""
Prev-Month-VWAP Equity-Holding Research — service / orchestration layer.

Read-only research engine. NEVER places orders. Simulates buying the equity as
a holding when price meets the Previous-Month VWAP with the Previous-Week VWAP
above it, then tracks the holding forward to a target / stop / max-hold exit.

Reuses the existing Broker, the shared Prev-Period VWAP engine, and the F&O
``Universe`` from the straddle module (single source of truth for the
instrument dump / lot sizes / tokens — no duplication).
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.prev_period_vwap import compute_prev_period_vwaps, _candle_dt, daily_gap_map
from research.pmvwap_straddle.universe import Universe
from research.pmvwap_equity import calculations as calc
from research.pmvwap_equity.config import load_config, save_config, sanitize
from research.pmvwap_equity.constants import (
    TIMEFRAME_MAP, MARKET_OPEN, MARKET_CLOSE, RESEARCH_ID,
)

logger = get_logger("research.pmvwap_equity")


class PMVwapEquityResearch:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._ul_cache: dict[tuple, list[dict]] = {}

    def load_config(self) -> dict:
        return load_config()

    def save_config(self, partial: dict) -> dict:
        return save_config(partial)

    def list_universe(self) -> dict:
        try:
            eq = self.universe.equities()
            return {"status": "ok", "count": len(eq), "stocks": eq}
        except Exception as exc:
            logger.error("universe list failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def _trading_days(start: date, end: date) -> list[date]:
        days, d = [], start
        while d <= end:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days

    def _candles(self, token: int, frm: datetime, to: datetime, tf: str) -> list[dict]:
        key = (token, frm, to, tf)
        if key in self._ul_cache:
            return self._ul_cache[key]
        try:
            raw = self.broker.get_historical_data(token, frm, to, TIMEFRAME_MAP[tf]) or []
        except Exception as exc:
            logger.warning("Equity candles failed (token=%s): %s", token, exc)
            raw = []
        for c in raw:
            c["_dt"] = _candle_dt(c)
        self._ul_cache[key] = raw
        return raw

    def backtest_stock(self, name: str, days: list[date], cfg: dict) -> list[dict]:
        tf = cfg["timeframe"]
        token = self.universe.nse_token(name)
        if not token:
            return []
        # History for VWAP + forward room for the holding to reach its exit.
        frm = datetime.combine(min(days) - timedelta(days=int(cfg["history_days"])), MARKET_OPEN)
        to = datetime.combine(max(days) + timedelta(days=int(cfg["max_hold_days"]) + 5), MARKET_CLOSE)
        candles = self._candles(token, frm, to, tf)
        if not candles:
            return []
        vwaps = compute_prev_period_vwaps(candles)
        gaps = daily_gap_map(candles)

        rows: list[dict] = []
        for day in days:
            signals = calc.find_holding_signals(
                candles, vwaps, entry_mode=cfg["entry_mode"], buffer=float(cfg["vwap_buffer"]),
                require_pw_above=bool(cfg["require_pw_above_pm"]), entry_start=cfg["entry_start"],
                signal_cutoff=cfg["signal_cutoff"], one_per_day=bool(cfg["one_signal_per_day"]), day=day)
            for sig in signals:
                entry_price = sig["close"]
                qty = calc.position_qty(int(cfg["capital_per_trade"]), int(cfg["fixed_qty"]), entry_price)
                if qty <= 0:
                    continue
                entry_dt = sig["dt"]
                forward = [c for c in candles if c.get("_dt") and c["_dt"] > entry_dt]
                sim = calc.simulate_holding(
                    entry_price, forward, target_pct=float(cfg["target_pct"]),
                    stop_pct=float(cfg["stop_pct"]), max_hold_days=int(cfg["max_hold_days"]),
                    exit_on=cfg["exit_on"], qty=qty, entry_day=day)
                rows.append({
                    "research_id": RESEARCH_ID,
                    "date": day.isoformat(), "time": entry_dt.strftime("%H:%M"),
                    "underlying": name, "entry_price": entry_price,
                    "prev_month_vwap": sig["prev_month_vwap"], "prev_week_vwap": sig["prev_week_vwap"],
                    "direction": sig["direction"], "qty": qty,
                    "capital": round(entry_price * qty, 2),
                    "target_price": sim["target_price"], "stop_price": sim["stop_price"],
                    "exit_price": sim["exit_price"], "exit_date": sim["exit_date"],
                    "exit_time": sim["exit_time"], "exit_reason": sim["exit_reason"],
                    "hold_days": sim["hold_days"], "return_pct": sim["return_pct"],
                    "mtm": sim["mtm"], "status": sim["status"],
                    "gap_pct": gaps.get(day),
                    "notes": f"green {'>' if sig['prev_week_vwap'] and sig['prev_week_vwap'] > sig['prev_month_vwap'] else '<='} purple",
                })
        return rows

    def backtest(self, overrides: Optional[dict] = None, *, symbol: Optional[str] = None,
                 start: Optional[str] = None, end: Optional[str] = None) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            try:
                s = date.fromisoformat(start) if start else self._latest_weekday()
                e = date.fromisoformat(end) if end else s
            except ValueError:
                return {"status": "error", "message": "Invalid date (use YYYY-MM-DD)"}
            if e < s:
                s, e = e, s
            days = self._trading_days(s, e)
            if not days:
                return {"status": "error", "message": "No trading days in range."}

            if symbol:
                names = [symbol.upper()]
            else:
                names = [x["name"] for x in self.universe.equities()]
                if int(cfg["max_stocks"]) > 0:
                    names = names[: int(cfg["max_stocks"])]
            if not names:
                return {"status": "error", "message": "Universe empty."}

            t0 = _time.monotonic()
            rows: list[dict] = []
            scanned = 0
            for nm in names:
                try:
                    rows.extend(self.backtest_stock(nm, days, cfg))
                except Exception as exc:
                    logger.warning("equity backtest_stock %s failed: %s", nm, exc)
                scanned += 1

            rows.sort(key=lambda r: (r["date"], r["time"], r["underlying"]))
            stats = self._stats(rows)
            logger.info("PMVWAP equity backtest: %d holdings across %d stocks / %d days | %dms",
                        len(rows), scanned, len(days), int((_time.monotonic() - t0) * 1000))
            return {
                "status": "ok", "research_id": RESEARCH_ID,
                "mode": "single" if symbol else "multi", "symbol": symbol,
                "start": s.isoformat(), "end": e.isoformat(),
                "stocks_scanned": scanned, "config": cfg, "stats": stats, "rows": rows,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    @staticmethod
    def _latest_weekday() -> date:
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    @staticmethod
    def _stats(rows: list[dict]) -> dict:
        n = len(rows)
        if not n:
            return {"total_signals": 0, "win_rate": 0.0, "wins": 0, "losses": 0,
                    "total_mtm": 0.0, "avg_return_pct": 0.0, "avg_hold_days": 0.0,
                    "highest_winner": 0.0, "largest_drawdown": 0.0, "open": 0}
        wins = [r for r in rows if r["mtm"] > 0]
        mtms = [r["mtm"] for r in rows]
        return {
            "total_signals": n, "wins": len(wins), "losses": n - len(wins),
            "win_rate": round(len(wins) / n * 100.0, 1),
            "total_mtm": round(sum(mtms), 2),
            "avg_return_pct": round(sum(r["return_pct"] for r in rows) / n, 2),
            "avg_hold_days": round(sum(r["hold_days"] for r in rows) / n, 1),
            "highest_winner": round(max(mtms), 2),
            "largest_drawdown": round(min(mtms), 2),
            "open": sum(1 for r in rows if r["status"] == calc.STATE_OPEN),
        }
