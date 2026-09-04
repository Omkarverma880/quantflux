"""
Hammer at 3/6-Month Low — Breakout: backtest + simulate service (read-only).

Daily positional swing. On every trading day the PREVIOUS daily candle is
examined for the hammer setup (long lower wick, small body, tiny upper wick,
its low the lowest of the last N bars, preceded by N red candles). If the setup
holds and the CURRENT day trades above that candle's high, a LONG is taken at
the break (or at the open on a gap-up).

The exit path is simulated on DAILY bars *after* the breakout day — a daily
candle carries no intraday path, so the breakout day itself is never used to
resolve the target/stop (it would be guesswork about whether the low printed
before or after the trigger). NEVER places orders.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.prev_period_vwap import _candle_dt
from research.pmvwap_straddle.universe import Universe
from research import costs
from research.hammer_breakout import calculations as calc
from research.hammer_breakout.config import load_config, save_config, sanitize, TIMEFRAME

logger = get_logger("research.hammer_breakout")

MARKET_OPEN = datetime.min.time().replace(hour=9, minute=15)
MARKET_CLOSE = datetime.min.time().replace(hour=15, minute=30)


def warmup_days(cfg: dict) -> int:
    """Calendar days of daily history needed behind the range start so the
    lookback window and the red-candle run are both fully populated."""
    bars = int(cfg["low_lookback"]) + int(cfg["red_before"]) + 5
    return int(bars * 1.55) + 15


class HammerBreakoutResearch:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._daily_cache: dict = {}

    def load_config(self):
        return load_config()

    def save_config(self, partial):
        return save_config(partial)

    # ── candle fetch (clamp `to` to now; never cache a range ending today) ──
    def _daily(self, token: int, start: date, end: date) -> list[dict]:
        key = (token, start, end)
        now = datetime.now()
        is_today = end >= now.date()
        if not is_today and key in self._daily_cache:
            return self._daily_cache[key]
        frm = datetime.combine(start, MARKET_OPEN)
        to = min(datetime.combine(end, MARKET_CLOSE), now)
        try:
            raw = self.broker.get_historical_data(token, frm, to, TIMEFRAME) or []
        except Exception as exc:
            logger.warning("daily candles failed (%s): %s", token, exc)
            raw = []
        out = []
        for c in raw:
            dt = _candle_dt(c)
            if dt is None:
                continue
            c["_dt"] = dt
            c["_d"] = dt.date()
            out.append(c)
        out.sort(key=lambda c: c["_d"])
        if not is_today:
            self._daily_cache[key] = out
        return out

    # ── one breakout day → a trade row ──
    def _row_for_index(self, name: str, candles: list[dict], j: int, cfg: dict) -> Optional[dict]:
        """``j`` is the index of the potential BREAKOUT day; ``j-1`` is the signal."""
        setup = calc.hammer_at(candles, j - 1, cfg)
        if not setup or not setup["ok"]:
            return None                                    # no hammer — nothing to report
        day = candles[j]
        base = {
            "date": day["_d"].isoformat(), "underlying": name,
            "signal_date": candles[j - 1]["_d"].isoformat(),
            "sig_high": round(setup["high"], 2), "sig_low": round(setup["low"], 2),
            "lower_wick_pct": setup["lower_wick_pct"], "body_pct": setup["body_pct"],
            "upper_wick_pct": setup["upper_wick_pct"], "lowest_low": setup["lowest_low"],
            "side": "LONG",
        }

        entry = calc.breakout_entry(day, setup["high"])
        if entry is None:
            return {**base, "status": "NO BREAKOUT", "open": False,
                    "notes": f"hammer on {base['signal_date']} but {base['sig_high']} never broken"}

        qty = calc.position_qty(cfg, entry)
        if qty <= 0:
            return {**base, "entry": entry, "status": "NO QTY", "open": False,
                    "notes": "Capital/entry too small for 1 share"}

        target, stop = calc.resolve_target_sl(entry, cfg, setup["low"])
        entry_day = day["_d"]
        end_day = entry_day + timedelta(days=int(cfg["max_hold_days"]))
        forward = [(c["_dt"], float(c["close"]), float(c["high"]), float(c["low"]))
                   for c in candles[j + 1:] if c["_d"] <= end_day]
        sq = calc._parse_hhmm(cfg["square_off"])
        today = date.today()
        square_off_reached = (end_day < today) or (end_day == today and datetime.now().time() >= sq)

        sim = calc.simulate_equity(entry, forward, direction="long", target=target,
                                   stop=stop, qty=qty, square_off_reached=square_off_reached)

        cost = 0.0
        if cfg.get("apply_costs") and not sim["open"] and sim["exit"] is not None:
            cc = costs.cost_config(cfg, "equity")
            cost = round(costs.roundtrip_cost(entry, sim["exit"], qty, **cc), 2)
        mtm = round(sim["mtm"] - cost, 2) if cost else sim["mtm"]
        exit_dt = sim["exit_dt"]
        hold_days = (exit_dt.date() - entry_day).days if exit_dt else (today - entry_day).days

        return {
            **base, "qty": qty, "entry": entry, "target": target, "sl": stop,
            "exit": sim["exit"], "exit_date": exit_dt.date().isoformat() if exit_dt else None,
            "exit_reason": sim["exit_reason"], "mtm": mtm, "cost": cost,
            "max_profit": sim["max_profit"], "max_loss": sim["max_loss"],
            "status": sim["exit_reason"], "open": sim["open"], "hold_days": hold_days,
            "hold_label": f"{hold_days}d", "product": cfg["product"],
            "notes": ("still open" if sim["open"]
                      else f"LONG {'squared off' if sim['exit_reason'] == 'SQUAREOFF' else sim['exit_reason'].lower()}"),
        }

    # ── backtest one stock over a date range ──
    def backtest_stock(self, name: str, s: date, e: date, cfg: dict,
                       include_non_trades: bool = False) -> list[dict]:
        token, _exch = self.universe.resolve_equity_token(name)
        if not token:
            return []
        buffer = int(cfg["max_hold_days"]) + 5
        candles = self._daily(token, s - timedelta(days=warmup_days(cfg)), e + timedelta(days=buffer))
        if len(candles) < 2:
            return []
        rows = []
        for j in range(1, len(candles)):
            d = candles[j]["_d"]
            if d < s or d > e:
                continue
            try:
                row = self._row_for_index(name, candles, j, cfg)
            except Exception as exc:
                logger.debug("hammer row %s %s failed: %s", name, d, exc)
                continue
            if not row:
                continue
            if row.get("status") in ("NO BREAKOUT", "NO QTY") and not include_non_trades:
                continue
            rows.append(row)
        return rows

    def backtest(self, overrides=None, *, symbol=None, symbols=None, start=None, end=None,
                 include_non_trades=False, apply_caps=True) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            try:
                s = date.fromisoformat(start) if start else self._latest_weekday()
                e = date.fromisoformat(end) if end else date.today()
            except ValueError:
                return {"status": "error", "message": "Invalid date (YYYY-MM-DD)"}
            if e < s:
                s, e = e, s
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
                    rows.extend(self.backtest_stock(nm, s, e, cfg, include_non_trades))
                except Exception as exc:
                    logger.warning("hammer backtest_stock %s failed: %s", nm, exc)
                scanned += 1
            rows.sort(key=lambda r: (r["date"], r["underlying"]))
            signals_total = sum(1 for r in rows if r.get("qty"))
            caps_note = None
            if apply_caps:
                taken = self._apply_caps(rows, cfg, include_non_trades)
                if taken < signals_total:
                    caps_note = (f"Portfolio caps applied — {taken} of {signals_total} signals taken "
                                 f"(Max Positions {cfg['max_positions']}, Max Long {cfg['max_long']}). "
                                 f"Matches Live/Paper. Uncheck 'Apply caps' for all.")
            note = (f"Showing first {len(names)} of {total} F&O stocks (Max Stocks = {cap})."
                    if truncated else None)
            return {"status": "ok", "mode": mode, "start": s.isoformat(), "end": e.isoformat(),
                    "stocks_scanned": scanned, "universe_size": total, "truncated": truncated,
                    "note": note, "caps_note": caps_note, "caps_applied": bool(apply_caps),
                    "signals_total": signals_total, "config": cfg, "stats": self._stats(rows),
                    "rows": rows, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    @staticmethod
    def _apply_caps(rows, cfg, include_non_trades) -> int:
        """Long-only portfolio caps, applied day by day in symbol order."""
        max_pos = min(int(cfg["max_positions"]), int(cfg["max_long"]))
        per_day: dict = {}
        taken = 0
        drop = []
        for r in sorted((x for x in rows if x.get("qty")), key=lambda x: (x["date"], x["underlying"])):
            cnt = per_day.get(r["date"], 0)
            if cnt >= max_pos:
                if include_non_trades:
                    r["status"] = "CAP SKIPPED"
                    r["notes"] = "portfolio cap reached"
                    r["capped"] = True
                    r["qty"] = None
                else:
                    drop.append(id(r))
            else:
                per_day[r["date"]] = cnt + 1
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
        buffer = int(cfg["max_hold_days"]) + 5
        candles = self._daily(token, d - timedelta(days=warmup_days(cfg)), d + timedelta(days=buffer))
        j = next((i for i, c in enumerate(candles) if c["_d"] == d), None)
        if j is None:
            return {"status": "error", "message": f"{name} {d}: no daily candle (holiday / not traded)"}
        if j == 0:
            return {"status": "error", "message": f"{name} {d}: no previous daily candle"}
        setup = calc.hammer_at(candles, j - 1, cfg)
        if setup is None:
            return {"status": "error",
                    "message": f"{name} {d}: needs {cfg['low_lookback']} daily bars before the signal candle"}
        broke = float(candles[j]["high"]) > setup["high"]
        checks = [
            {"label": f"Lower wick >= {cfg['lower_wick_min']}%", "value": f"{setup['lower_wick_pct']}%",
             "ok": setup["lower_wick_ok"]},
            {"label": f"Body < {cfg['body_max']}%", "value": f"{setup['body_pct']}%",
             "ok": setup["body_ok"]},
            {"label": f"Upper wick < {cfg['upper_wick_max']}%", "value": f"{setup['upper_wick_pct']}%",
             "ok": setup["upper_wick_ok"]},
            {"label": f"Low is lowest of {cfg['low_lookback']} bars",
             "value": f"{round(setup['low'], 2)} vs {setup['lowest_low']}", "ok": setup["low_at_extreme"]},
            {"label": f"{cfg['red_before']} red candles before",
             "value": ", ".join("red" if r else "green" for r in setup["reds_before"]) or "—",
             "ok": setup["red_ok"]},
            {"label": f"Day trades above {round(setup['high'], 2)}",
             "value": f"high {round(float(candles[j]['high']), 2)}", "ok": broke},
        ]
        window = candles[max(0, j - 12): j + 1]
        timeline = [{"date": c["_d"].isoformat(), "open": round(float(c["open"]), 2),
                     "high": round(float(c["high"]), 2), "low": round(float(c["low"]), 2),
                     "close": round(float(c["close"]), 2),
                     "red": float(c["close"]) < float(c["open"]),
                     "signal": c["_d"] == candles[j - 1]["_d"]} for c in window]
        row = self._row_for_index(name, candles, j, cfg)
        return {"status": "ok", "symbol": name, "date": d.isoformat(), "config": cfg,
                "signal_date": candles[j - 1]["_d"].isoformat(), "setup": setup,
                "checks": checks, "hammer": setup["ok"], "broke": broke,
                "timeline": timeline, "trade": row}

    # ── live scan: the setups armed for today across a watchlist ──
    def scan(self, symbols: list[str], overrides=None, day: Optional[date] = None) -> list[dict]:
        """Signal candles that qualify for a breakout on ``day`` (default today).
        Read-only — used by the Positions tab to preview what is armed."""
        cfg = sanitize({**self.load_config(), **(overrides or {})})
        d = day or date.today()
        out = []
        for name in symbols:
            name = (name or "").strip().upper()
            if not name:
                continue
            try:
                token, _exch = self.universe.resolve_equity_token(name)
                if not token:
                    continue
                candles = [c for c in self._daily(token, d - timedelta(days=warmup_days(cfg)), d)
                           if c["_d"] < d]                      # completed days only
                if len(candles) < 2:
                    continue
                setup = calc.hammer_at(candles, len(candles) - 1, cfg)
                if setup and setup["ok"]:
                    out.append({"underlying": name, "signal_date": candles[-1]["_d"].isoformat(),
                                "trigger": round(setup["high"], 2), "sig_low": round(setup["low"], 2),
                                "lower_wick_pct": setup["lower_wick_pct"], "body_pct": setup["body_pct"],
                                "upper_wick_pct": setup["upper_wick_pct"],
                                "lowest_low": setup["lowest_low"]})
            except Exception as exc:
                logger.debug("hammer scan %s failed: %s", name, exc)
        return out

    # ── helpers ──
    @staticmethod
    def _latest_weekday() -> date:
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    @staticmethod
    def _stats(rows) -> dict:
        traded = [r for r in rows if r.get("qty") and not r.get("capped")]
        n = len(traded)
        if not n:
            return {"total": 0, "wins": 0, "win_rate": 0.0, "total_mtm": 0.0,
                    "best": 0.0, "worst": 0.0, "open": 0, "avg_hold": 0.0, "total_cost": 0.0}
        wins = [r for r in traded if (r["mtm"] or 0) > 0]
        mtms = [r["mtm"] or 0 for r in traded]
        holds = [r.get("hold_days") or 0 for r in traded]
        return {"total": n, "wins": len(wins), "win_rate": round(len(wins) / n * 100, 1),
                "total_mtm": round(sum(mtms), 2), "best": round(max(mtms), 2),
                "worst": round(min(mtms), 2), "open": sum(1 for r in traded if r.get("open")),
                "avg_hold": round(sum(holds) / n, 1),
                "total_cost": round(sum(float(r.get("cost") or 0) for r in traded), 2)}
