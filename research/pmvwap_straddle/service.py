"""
Prev-Month-VWAP Straddle Research — service / orchestration layer.

Read-only research engine. NEVER places orders. Reuses the existing Broker for
historical candles, the instrument dump for the F&O universe / option lookup /
lot sizes, and the shared Prev-Period VWAP engine (exact Pine port).

Per stock/day it:
  1. builds Prev-Month VWAP from the underlying candles,
  2. finds bars that cross it from below,
  3. simulates buying an ATM CE + PE straddle,
  4. exits each leg independently at the combined-premium target,
  5. emits one research-log row per signal.

Later this becomes a live strategy by swapping the virtual fills for real
orders — nothing else changes.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.prev_period_vwap import compute_prev_period_vwaps, _candle_dt
from research.pmvwap_straddle import calculations as calc
from research.pmvwap_straddle.config import load_config, save_config, sanitize
from research.pmvwap_straddle.constants import (
    TIMEFRAME_MAP, MARKET_OPEN, MARKET_CLOSE, RESEARCH_ID,
)
from research.pmvwap_straddle.universe import Universe

logger = get_logger("research.pmvwap_straddle")


def _hhmm_time(s: str):
    from datetime import time as dtime
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(15, 20)


class PMVwapStraddleResearch:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._ul_cache: dict[tuple, list[dict]] = {}     # (token, from, to, tf) → candles
        self._opt_cache: dict[tuple, dict] = {}          # (token, day, tf) → {dt: close}

    # ── config ──
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

    # ── candle helpers ──
    @staticmethod
    def _trading_days(start: date, end: date) -> list[date]:
        days, d = [], start
        while d <= end:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days

    def _underlying_candles(self, token: int, frm: datetime, to: datetime, tf: str) -> list[dict]:
        key = (token, frm, to, tf)
        if key in self._ul_cache:
            return self._ul_cache[key]
        try:
            raw = self.broker.get_historical_data(token, frm, to, TIMEFRAME_MAP[tf]) or []
        except Exception as exc:
            logger.warning("Underlying candles failed (token=%s): %s", token, exc)
            raw = []
        for c in raw:
            c["_dt"] = _candle_dt(c)
        self._ul_cache[key] = raw
        return raw

    def _option_series(self, token: int, day: date, tf: str) -> dict:
        key = (token, day, tf)
        if key in self._opt_cache:
            return self._opt_cache[key]
        frm = datetime.combine(day, MARKET_OPEN)
        to = datetime.combine(day, MARKET_CLOSE)
        series: dict = {}
        try:
            for c in self.broker.get_historical_data(token, frm, to, TIMEFRAME_MAP[tf]) or []:
                dt = _candle_dt(c)
                if dt is not None:
                    series[dt] = float(c.get("close", 0) or 0)
        except Exception as exc:
            logger.warning("Option candles failed (token=%s %s): %s", token, day, exc)
        self._opt_cache[key] = series
        return series

    # ── per-stock backtest across a set of days ──
    def backtest_stock(self, name: str, days: list[date], cfg: dict) -> list[dict]:
        tf = cfg["timeframe"]
        token = self.universe.nse_token(name)
        if not token:
            logger.debug("No NSE token for %s", name)
            return []
        lot = self.universe.lot_size(name)
        squareoff = _hhmm_time(cfg["square_off"])

        # One history fetch covering all requested days + Prev-Month lookback.
        frm = datetime.combine(min(days) - timedelta(days=int(cfg["history_days"])), MARKET_OPEN)
        to = datetime.combine(max(days), MARKET_CLOSE)
        candles = self._underlying_candles(token, frm, to, tf)
        if not candles:
            return []
        vwaps = compute_prev_period_vwaps(candles)

        rows: list[dict] = []
        for day in days:
            signals = calc.find_entry_signals(
                candles, vwaps, buffer=float(cfg["vwap_buffer"]),
                entry_start=cfg["entry_start"], signal_cutoff=cfg["signal_cutoff"],
                one_per_day=bool(cfg["one_signal_per_day"]), day=day)
            for sig in signals:
                row = self._simulate_signal(name, token, lot, tf, day, sig, vwaps, candles, squareoff, cfg)
                if row:
                    rows.append(row)
        return rows

    def _simulate_signal(self, name, token, lot, tf, day, sig, vwaps, candles, squareoff, cfg) -> Optional[dict]:
        underlying_ltp = sig["close"]
        expiry = self.universe.expiry_for(name, cfg["expiry_type"], day)
        if not expiry:
            return None
        atm = self.universe.atm_strike(name, expiry, underlying_ltp)
        if atm is None:
            return None
        ce = self.universe.resolve(name, expiry, atm, "CE")
        pe = self.universe.resolve(name, expiry, atm, "PE")
        if not ce or not pe:
            return None
        lot = lot or ce.get("lot_size", 0) or pe.get("lot_size", 0)

        ce_series = self._option_series(ce["token"], day, tf)
        pe_series = self._option_series(pe["token"], day, tf)
        entry_dt = sig["dt"]
        ce_entry = ce_series.get(entry_dt)
        pe_entry = pe_series.get(entry_dt)
        if not ce_entry or not pe_entry:
            return None       # no premium data at entry (illiquid / missing) — skip

        def forward(series):
            return [(dt, series[dt]) for dt in sorted(series)
                    if dt > entry_dt and dt.time() <= squareoff]

        sim = calc.simulate_straddle(
            ce_entry, pe_entry, forward(ce_series), forward(pe_series),
            target_pct=float(cfg["target_pct"]), lot_size=int(lot or 0))

        # signal age = minutes from entry to the later of the two exits
        exits = [t for t in (sim["ce_exit_time"], sim["pe_exit_time"]) if t]
        signal_age = None
        if exits:
            last = max(datetime.strptime(t, "%H:%M").time() for t in exits)
            signal_age = int((datetime.combine(day, last) - entry_dt).total_seconds() // 60)

        return {
            "research_id": RESEARCH_ID,
            "date": day.isoformat(),
            "time": entry_dt.strftime("%H:%M"),
            "underlying": name,
            "underlying_ltp": underlying_ltp,
            "prev_month_vwap": sig["level"],
            "direction": sig["direction"],
            "atm_strike": atm,
            "ce_symbol": ce["tradingsymbol"], "pe_symbol": pe["tradingsymbol"],
            "expiry": expiry.isoformat(), "lot_size": int(lot or 0),
            "entry_ce": round(ce_entry, 2), "entry_pe": round(pe_entry, 2),
            "exit_ce": sim["ce_exit"], "exit_pe": sim["pe_exit"],
            "ce_exit_time": sim["ce_exit_time"], "pe_exit_time": sim["pe_exit_time"],
            "ce_exit_reason": sim["ce_exit_reason"], "pe_exit_reason": sim["pe_exit_reason"],
            "combined_premium": sim["combined_entry"],
            "target_premium": sim["target_premium"],
            "ce_mtm": sim["ce_mtm"], "pe_mtm": sim["pe_mtm"], "combined_mtm": sim["combined_mtm"],
            "targets_hit": sim["targets_hit"],
            "status": sim["status"],
            "signal_age": signal_age,
            "notes": f"{sim['targets_hit']}/2 legs hit target",
        }

    # ── top-level backtest (single stock or whole universe) ──
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
                return {"status": "error", "message": "No trading days in the selected range."}

            if symbol:
                names = [symbol.upper()]
            else:
                names = [x["name"] for x in self.universe.equities()]
                names = self._apply_universe_filters(names, cfg)
                if int(cfg["max_stocks"]) > 0:
                    names = names[: int(cfg["max_stocks"])]
            if not names:
                return {"status": "error", "message": "Universe empty after filters."}

            t0 = _time.monotonic()
            rows: list[dict] = []
            budget = float(cfg["per_stock_budget_ms"]) / 1000.0 if cfg["per_stock_budget_ms"] else 0
            scanned = 0
            for nm in names:
                try:
                    rows.extend(self.backtest_stock(nm, days, cfg))
                except Exception as exc:
                    logger.warning("backtest_stock %s failed: %s", nm, exc)
                scanned += 1
                if budget and (_time.monotonic() - t0) > budget * len(names):
                    logger.warning("multi-scan time budget hit after %d/%d stocks", scanned, len(names))
                    break

            rows.sort(key=lambda r: (r["date"], r["time"], r["underlying"]))
            stats = self._stats(rows)
            logger.info("PMVWAP straddle backtest: %d signals across %d stocks / %d days | %dms",
                        len(rows), scanned, len(days), int((_time.monotonic() - t0) * 1000))
            return {
                "status": "ok", "research_id": RESEARCH_ID,
                "mode": "single" if symbol else "multi",
                "symbol": symbol, "start": s.isoformat(), "end": e.isoformat(),
                "stocks_scanned": scanned, "config": cfg,
                "stats": stats, "rows": rows,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    @staticmethod
    def _latest_weekday() -> date:
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def _apply_universe_filters(self, names: list[str], cfg: dict) -> list[str]:
        """Price/volume/volatility/sector/ban filters via one batched quote.

        Cheap pre-filter so multi-scans skip stocks that can't qualify. ATR-based
        filters need candles, so they are approximated here by the day range %;
        the exact ATR filter can be layered in the Summary-Report phase."""
        need = any([cfg["min_price"], cfg["max_price"], cfg["min_volume"],
                    cfg["high_vol_only"], cfg["ignore_ban"]])
        if not need:
            return names
        keys = [f"NSE:{n}" for n in names]
        quotes = {}
        try:
            # Kite quote() caps ~500 instruments/call — chunk it.
            for i in range(0, len(keys), 400):
                quotes.update(self.broker.get_quote(keys[i:i + 400]) or {})
        except Exception as exc:
            logger.warning("universe filter quote failed: %s", exc)
            return names
        out = []
        for n in names:
            q = quotes.get(f"NSE:{n}") or {}
            ltp = float(q.get("last_price", 0) or 0)
            ohlc = q.get("ohlc") or {}
            hi = float(ohlc.get("high", 0) or 0)
            lo = float(ohlc.get("low", 0) or 0)
            vol = float(q.get("volume", 0) or 0)
            if cfg["min_price"] and ltp < cfg["min_price"]:
                continue
            if cfg["max_price"] and ltp > cfg["max_price"]:
                continue
            if cfg["min_volume"] and vol < cfg["min_volume"]:
                continue
            if cfg["high_vol_only"] and ltp > 0:
                rng_pct = ((hi - lo) / ltp * 100.0) if (hi and lo) else 0.0
                if rng_pct < float(cfg["high_vol_threshold"]):
                    continue
            out.append(n)
        return out

    @staticmethod
    def _stats(rows: list[dict]) -> dict:
        n = len(rows)
        if not n:
            return {"total_signals": 0, "win_rate": 0.0, "wins": 0, "losses": 0,
                    "avg_combined_premium": 0.0, "avg_time_to_target": None,
                    "highest_winner": 0.0, "largest_drawdown": 0.0, "total_mtm": 0.0,
                    "full_exit": 0, "half_exit": 0}
        wins = [r for r in rows if r["combined_mtm"] > 0]
        ages = [r["signal_age"] for r in rows if r.get("signal_age") is not None and r["targets_hit"] > 0]
        mtms = [r["combined_mtm"] for r in rows]
        return {
            "total_signals": n,
            "wins": len(wins), "losses": n - len(wins),
            "win_rate": round(len(wins) / n * 100.0, 1),
            "avg_combined_premium": round(sum(r["combined_premium"] for r in rows) / n, 2),
            "avg_time_to_target": round(sum(ages) / len(ages), 1) if ages else None,
            "highest_winner": round(max(mtms), 2),
            "largest_drawdown": round(min(mtms), 2),
            "total_mtm": round(sum(mtms), 2),
            "full_exit": sum(1 for r in rows if r["status"] == calc.STATE_FULL),
            "half_exit": sum(1 for r in rows if r.get("targets_hit") == 1),
        }
