"""
Research module #3 — HL + VWAP Research Lab.

Read-only research/backtest workspace for an intraday options strategy built on:
  • First-hour (09:15-10:15) high/low opening range
  • Rolling previous-5-trading-day first-hour ranges (d1..d5)
  • Today's running VWAP (HLC3 × volume)
  • Previous-day full-session VWAP (frozen horizontal reference)
  • Spot breakout / VWAP-retest / confluence signals
  • Option premium backtesting (ATM / ITM / OTM / manual)

Strictly analytics — never places orders, never touches live trading.

Data source modes
-----------------
  A) Zerodha historical (currently-listed instruments only)
  B) Manual CSV (expired options / external datasets)
"""
from __future__ import annotations

import csv as _csv
import io
import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.vwap_pvwap import _candle_dt, _parse_expiry

logger = get_logger("research.hl_vwap_lab")

EOD_EXIT = dtime(15, 25)

# Spot symbol, F&O exchange, option 'name', strike step (defaults; lot_size is
# user-configurable in the params).
INDEX = {
    "NIFTY":     {"spot": "NSE:NIFTY 50",          "exch": "NFO", "name": "NIFTY",     "step": 50,  "lot": 75},
    "BANKNIFTY": {"spot": "NSE:NIFTY BANK",        "exch": "NFO", "name": "BANKNIFTY", "step": 100, "lot": 15},
    "FINNIFTY":  {"spot": "NSE:NIFTY FIN SERVICE", "exch": "NFO", "name": "FINNIFTY",  "step": 50,  "lot": 40},
    "SENSEX":    {"spot": "BSE:SENSEX",            "exch": "BFO", "name": "SENSEX",     "step": 100, "lot": 10},
}

INTERVALS = {"minute", "3minute", "5minute"}


def _f(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def _hlc3(c: dict) -> float:
    return (_f(c["high"]) + _f(c["low"]) + _f(c["close"])) / 3.0


class HlVwapLab:
    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        self._tokens: dict[str, int] = {}
        self._opt_cache: dict = {}
        self._last_records: list[dict] = []     # cached day_records of the last run
        self._last_trades: list[dict] = []      # cached spot trades of the last run

    # ──────────────── instruments / meta ────────────────

    def _spot_token(self, index: str) -> Optional[int]:
        cfg = INDEX.get(index)
        if not cfg:
            return None
        sym = cfg["spot"].split(":", 1)[1]
        exch = cfg["spot"].split(":", 1)[0]
        key = cfg["spot"]
        if key in self._tokens:
            return self._tokens[key]
        try:
            for inst in self.broker.get_instruments(exch):
                if inst.get("tradingsymbol") == sym:
                    self._tokens[key] = int(inst["instrument_token"])
                    return self._tokens[key]
        except Exception as exc:
            logger.error("HLVWAP spot token failed: %s", exc)
        return None

    def _options(self, index: str) -> list[dict]:
        cfg = INDEX.get(index)
        if not cfg:
            return []
        ck = f"opts:{index}:{date.today()}"
        if ck in self._opt_cache:
            return self._opt_cache[ck]
        out = []
        try:
            for inst in self.broker.get_instruments(cfg["exch"]):
                if inst.get("name") != cfg["name"] or inst.get("instrument_type") not in ("CE", "PE"):
                    continue
                exp = _parse_expiry(inst.get("expiry"))
                if not exp:
                    continue
                out.append({"tradingsymbol": inst.get("tradingsymbol"), "token": int(inst["instrument_token"]),
                            "strike": _f(inst.get("strike")), "type": inst.get("instrument_type"), "expiry": exp})
        except Exception as exc:
            logger.error("HLVWAP options fetch failed: %s", exc)
        self._opt_cache[ck] = out
        return out

    def meta(self, index: str) -> dict:
        cfg = INDEX.get(index)
        if not cfg:
            return {"status": "error", "message": "Unknown instrument"}
        opts = self._options(index)
        expiries = sorted({o["expiry"] for o in opts})
        spot = 0.0
        try:
            spot = _f((self.broker.get_ltp([cfg["spot"]]) or {}).get(cfg["spot"], 0))
        except Exception:
            pass
        atm = int(round(spot / cfg["step"]) * cfg["step"]) if spot else 0
        strikes = sorted({int(o["strike"]) for o in opts}) if opts else []
        return {"status": "ok", "index": index, "spot": round(spot, 2), "atm": atm,
                "step": cfg["step"], "lot": cfg["lot"],
                "expiries": [e.isoformat() for e in expiries], "strikes": strikes}

    # ──────────────── data fetch ────────────────

    def _spot_candles(self, index: str, start: date, end: date, interval: str) -> list[dict]:
        token = self._spot_token(index)
        if not token:
            return []
        out, cur = [], start - timedelta(days=6)   # buffer for prev-day VWAP / d1..d5
        chunk = timedelta(days=55)
        while cur <= end:
            ce = min(cur + chunk, end)
            try:
                c = self.broker.get_historical_data(
                    token, datetime.combine(cur, dtime(9, 0)), datetime.combine(ce, dtime(15, 40)), interval) or []
                out.extend(c)
            except Exception as exc:
                logger.warning("HLVWAP spot fetch %s→%s failed: %s", cur, ce, exc)
            cur = ce + timedelta(days=1)
        return out

    def _resolve_strike_opt(self, index: str, expiry: date, strike: int, opt_type: str) -> Optional[dict]:
        for o in self._options(index):
            if o["expiry"] == expiry and o["type"] == opt_type and abs(o["strike"] - strike) < 0.5:
                return o
        return None

    def _option_candles(self, token: int, day: date, interval: str) -> list[dict]:
        key = (token, day, interval)
        if key in self._opt_cache:
            return self._opt_cache[key]
        try:
            c = self.broker.get_historical_data(
                int(token), datetime.combine(day, dtime(9, 0)), datetime.combine(day, dtime(15, 40)),
                interval, oi=True) or []
        except Exception as exc:
            logger.warning("HLVWAP option candles failed: %s", exc)
            c = []
        self._opt_cache[key] = c
        return c

    # ──────────────── indicators ────────────────

    @staticmethod
    def _group_by_day(candles: list[dict]) -> dict[date, list[dict]]:
        g: dict[date, list[dict]] = {}
        for c in candles:
            dt = _candle_dt(c)
            if dt:
                g.setdefault(dt.date(), []).append(c)
        for d in g:
            g[d].sort(key=lambda x: _candle_dt(x))
        return g

    @staticmethod
    def _first_hour(day_candles: list[dict], start_t: dtime, minutes: int) -> tuple[Optional[float], Optional[float]]:
        end_t = (datetime.combine(date.today(), start_t) + timedelta(minutes=minutes)).time()
        highs, lows = [], []
        for c in day_candles:
            dt = _candle_dt(c)
            if dt and start_t <= dt.time() < end_t:
                highs.append(_f(c["high"]))
                lows.append(_f(c["low"]))
        return (max(highs) if highs else None, min(lows) if lows else None)

    @staticmethod
    def _full_vwap(day_candles: list[dict]) -> Optional[float]:
        pv = v = 0.0
        for c in day_candles:
            vol = _f(c.get("volume"))
            pv += _hlc3(c) * vol
            v += vol
        return (pv / v) if v > 0 else None

    @staticmethod
    def _running_vwap(day_candles: list[dict]) -> list[float]:
        out, pv, v = [], 0.0, 0.0
        for c in day_candles:
            vol = _f(c.get("volume"))
            pv += _hlc3(c) * vol
            v += vol
            out.append((pv / v) if v > 0 else _hlc3(c))
        return out

    # ──────────────── signal generation ────────────────

    def _signals_for_day(self, day: date, candles: list[dict], rv: list[float],
                         prev_vwap: Optional[float], dlevels: dict, mode: str,
                         start_t: dtime, fh_minutes: int) -> list[dict]:
        """One signal per qualifying candle; backtester enforces 1-at-a-time."""
        if prev_vwap is None or dlevels.get("d1_high") is None:
            return []
        fh_end = (datetime.combine(date.today(), start_t) + timedelta(minutes=fh_minutes)).time()
        sigs = []
        retest_armed = None  # for vwap_retest: 'CE'/'PE' breakout seen, waiting for retest
        for i, c in enumerate(candles):
            dt = _candle_dt(c)
            if not dt or dt.time() < fh_end:        # only after the first hour completes
                continue
            close = _f(c["close"])
            vwap = rv[i]
            bull = close > dlevels["d1_high"] and close > vwap and vwap > prev_vwap
            bear = close < dlevels["d1_low"] and close < vwap and vwap < prev_vwap

            if mode == "breakout":
                if bull:
                    sigs.append(self._sig(dt, close, "CE", "breakout"))
                elif bear:
                    sigs.append(self._sig(dt, close, "PE", "breakout"))

            elif mode == "vwap_retest":
                # arm on breakout, fire when price retests VWAP then rejects in direction
                if bull:
                    retest_armed = "CE"
                elif bear:
                    retest_armed = "PE"
                near = abs(close - vwap) <= max(2.0, vwap * 0.0008)
                if retest_armed == "CE" and near and close >= vwap:
                    sigs.append(self._sig(dt, close, "CE", "vwap_retest"))
                    retest_armed = None
                elif retest_armed == "PE" and near and close <= vwap:
                    sigs.append(self._sig(dt, close, "PE", "vwap_retest"))
                    retest_armed = None

            elif mode == "confluence":
                # breakout that occurs into a confluence zone (D-levels clustered or near prev VWAP)
                zone = self._confluence_zones(dlevels, prev_vwap)
                if bull and self._in_zone(dlevels["d1_high"], zone):
                    sigs.append(self._sig(dt, close, "CE", "confluence"))
                elif bear and self._in_zone(dlevels["d1_low"], zone):
                    sigs.append(self._sig(dt, close, "PE", "confluence"))
        return sigs

    @staticmethod
    def _sig(dt: datetime, price: float, typ: str, reason: str) -> dict:
        return {"dt": dt, "time": dt.strftime("%H:%M"), "price": round(price, 2),
                "type": typ, "reason": reason}

    @staticmethod
    def _confluence_zones(dlevels: dict, prev_vwap: Optional[float], tol: float = 0.0015) -> list[dict]:
        pts = []
        for k, v in dlevels.items():
            if v is not None:
                pts.append((k, v))
        if prev_vwap is not None:
            pts.append(("prev_vwap", prev_vwap))
        zones = []
        used = set()
        for i in range(len(pts)):
            if i in used:
                continue
            base = pts[i][1]
            cluster = [pts[i]]
            for j in range(i + 1, len(pts)):
                if j in used:
                    continue
                if abs(pts[j][1] - base) <= base * tol:
                    cluster.append(pts[j])
                    used.add(j)
            if len(cluster) >= 2:
                lo = min(p[1] for p in cluster)
                hi = max(p[1] for p in cluster)
                zones.append({"low": round(lo, 2), "high": round(hi, 2),
                              "members": [p[0] for p in cluster]})
        return zones

    @staticmethod
    def _in_zone(level: float, zones: list[dict]) -> bool:
        return any(z["low"] <= level <= z["high"] for z in zones)

    # ──────────────── backtest (spot) ────────────────

    def _backtest_spot(self, day_records: list[dict], params: dict) -> list[dict]:
        sl = _f(params.get("stop_loss"), 30)
        tgt = _f(params.get("target"), 60)
        cap = _f(params.get("capital_per_trade"), 0)
        trades = []
        for rec in day_records:        # chronological
            day = rec["date"]
            candles = rec["candles"]
            sig_by_idx = {s["dt"]: s for s in rec["signals"]}
            active = None
            for c in candles:
                dt = _candle_dt(c)
                hi, lo, close = _f(c["high"]), _f(c["low"]), _f(c["close"])
                if active is None:
                    s = sig_by_idx.get(dt)
                    if s:
                        entry = s["price"]
                        active = {
                            "date": day.isoformat(), "entry_time": s["time"], "signal_type": s["type"],
                            "reason": s["reason"], "entry_price": entry,
                            "sl": entry - sl if s["type"] == "CE" else entry + sl,
                            "tgt": entry + tgt if s["type"] == "CE" else entry - tgt,
                            "_sig": s,
                        }
                    continue
                # manage open trade
                long_ = active["signal_type"] == "CE"
                exit_price = exit_reason = None
                if long_:
                    if lo <= active["sl"]:
                        exit_price, exit_reason = active["sl"], "SL"
                    elif hi >= active["tgt"]:
                        exit_price, exit_reason = active["tgt"], "TARGET"
                else:
                    if hi >= active["sl"]:
                        exit_price, exit_reason = active["sl"], "SL"
                    elif lo <= active["tgt"]:
                        exit_price, exit_reason = active["tgt"], "TARGET"
                if exit_price is None and dt.time() >= EOD_EXIT:
                    exit_price, exit_reason = close, "EOD"
                if exit_price is not None:
                    pts = (exit_price - active["entry_price"]) if long_ else (active["entry_price"] - exit_price)
                    qty = int(cap / active["entry_price"]) if cap > 0 else 1
                    trades.append({
                        **{k: active[k] for k in ("date", "entry_time", "signal_type", "reason", "entry_price")},
                        "exit_time": dt.strftime("%H:%M"), "exit_price": round(exit_price, 2),
                        "points": round(pts, 2), "qty": qty, "pnl": round(pts * qty, 2),
                        "exit_reason": exit_reason, "_sig": active["_sig"],
                    })
                    active = None
            if active is not None:        # close at last candle
                c = candles[-1]
                close = _f(c["close"])
                long_ = active["signal_type"] == "CE"
                pts = (close - active["entry_price"]) if long_ else (active["entry_price"] - close)
                qty = int(cap / active["entry_price"]) if cap > 0 else 1
                trades.append({
                    **{k: active[k] for k in ("date", "entry_time", "signal_type", "reason", "entry_price")},
                    "exit_time": _candle_dt(c).strftime("%H:%M"), "exit_price": round(close, 2),
                    "points": round(pts, 2), "qty": qty, "pnl": round(pts * qty, 2),
                    "exit_reason": "EOD", "_sig": active["_sig"],
                })
        return trades

    # ──────────────── option backtest ────────────────

    def _option_trade(self, index: str, params: dict, trade: dict, spot_at_entry: float,
                      interval: str, day: date, opt_rows_by: Optional[dict]) -> Optional[dict]:
        cfg = INDEX[index]
        step = cfg["step"]
        atm = int(round(spot_at_entry / step) * step)
        typ = trade["signal_type"]
        mode = str(params.get("strike_mode", "ATM")).upper()
        if mode == "MANUAL" and params.get("strike"):
            strike = int(params["strike"])
        elif mode == "ITM":
            strike = atm - step if typ == "CE" else atm + step
        elif mode == "OTM":
            strike = atm + step if typ == "CE" else atm - step
        else:
            strike = atm
        lot = int(_f(params.get("lot_size"), cfg["lot"]))

        # candle series for that option on the day (CSV rows or Zerodha)
        series = None
        if opt_rows_by is not None:
            series = opt_rows_by.get((day.isoformat(), strike, typ))
            if series is None:
                # auto-match: nearest available strike of the same type/day in the CSV
                cands = [k for k in opt_rows_by if k[0] == day.isoformat() and k[2] == typ]
                if cands:
                    best = min(cands, key=lambda k: abs(k[1] - strike))
                    strike = best[1]
                    series = opt_rows_by[best]
        else:
            exp = _parse_expiry(params.get("expiry")) if params.get("expiry") else None
            if exp is None:
                # nearest expiry >= day
                exps = sorted({o["expiry"] for o in self._options(index) if o["expiry"] >= day})
                exp = exps[0] if exps else None
            if exp:
                o = self._resolve_strike_opt(index, exp, strike, typ)
                if o:
                    series = self._option_candles(o["token"], day, interval)
        if not series:
            return None

        def prem_at(t_str):
            for c in series:
                dt = _candle_dt(c)
                if dt and dt.strftime("%H:%M") >= t_str:
                    return _f(c["close"])
            return _f(series[-1]["close"]) if series else None

        entry_prem = prem_at(trade["entry_time"])
        exit_prem = prem_at(trade["exit_time"])
        if entry_prem is None or exit_prem is None or entry_prem <= 0:
            return None
        pnl = round((exit_prem - entry_prem) * lot, 2)   # long option only
        return {**trade, "strike": strike, "option_type": typ, "lot": lot,
                "premium_buy": round(entry_prem, 2), "premium_sell": round(exit_prem, 2),
                "option_pnl": pnl}

    # ──────────────── analytics ────────────────

    @staticmethod
    def _analytics(trades: list[dict], pnl_key: str) -> dict:
        pnls = [t.get(pnl_key, 0) or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        n = len(trades)
        net = round(sum(pnls), 2)
        gp, gl = sum(wins), abs(sum(losses))
        eq, peak, mdd, run = [], 0.0, 0.0, 0.0
        for p in pnls:
            run += p
            eq.append(round(run, 2))
            peak = max(peak, run)
            mdd = min(mdd, run - peak)
        by_day: dict = {}
        for t in trades:
            by_day[t["date"]] = by_day.get(t["date"], 0) + (t.get(pnl_key, 0) or 0)
        daily = [{"date": d, "pnl": round(v, 2)} for d, v in sorted(by_day.items())]
        return {
            "total_trades": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(100 * len(wins) / n, 1) if n else 0,
            "net_pnl": net, "avg_profit": round(gp / len(wins), 2) if wins else 0,
            "avg_loss": round(-gl / len(losses), 2) if losses else 0,
            "max_drawdown": round(mdd, 2),
            "profit_factor": round(gp / gl, 2) if gl > 0 else None,
            "expectancy": round(net / n, 2) if n else 0,
            "equity_curve": eq, "drawdown_curve": [round(e - max(eq[:i + 1] or [0]), 2) for i, e in enumerate(eq)],
            "daily_pnl": daily,
        }

    # ──────────────── main runner ────────────────

    def run(self, params: dict) -> dict:
        with self._lock:
            mode = params.get("mode", "zerodha")
            interval = params.get("interval", "minute")
            if interval not in INTERVALS:
                interval = "minute"
            index = params.get("instrument", "NIFTY")
            sh = int(_f(params.get("session_start_hour"), 9))
            sm = int(_f(params.get("session_start_min"), 15))
            fh = int(_f(params.get("first_hour_minutes"), 60))
            start_t = dtime(sh, sm)
            strat = params.get("strategy_mode", "breakout")

            # ── load spot candles ──
            if mode == "csv":
                spot_rows = params.get("spot_rows") or []
                candles = [{"date": r["datetime"], "open": r["open"], "high": r["high"],
                            "low": r["low"], "close": r["close"], "volume": r.get("volume", 0)} for r in spot_rows]
                opt_rows_by = self._index_option_rows(params.get("option_rows") or [])
            else:
                try:
                    start = date.fromisoformat(params["start"])
                    end = date.fromisoformat(params["end"])
                except Exception:
                    return {"status": "error", "message": "Invalid start/end date"}
                candles = self._spot_candles(index, start, end, interval)
                opt_rows_by = None
                if not candles:
                    return {"status": "error", "message": "No spot data returned (check dates / auth)"}

            grouped = self._group_by_day(candles)
            days = sorted(grouped.keys())
            if mode != "csv":
                days = [d for d in days if start <= d <= end]

            # ── per-day indicators + rolling d1..d5 ──
            fh_ranges: dict[date, tuple] = {}
            for d in sorted(grouped.keys()):
                fh_ranges[d] = self._first_hour(grouped[d], start_t, fh)

            day_records = []
            all_days_sorted = sorted(grouped.keys())
            chart_days = []
            for d in days:
                dc = grouped[d]
                rv = self._running_vwap(dc)
                idx = all_days_sorted.index(d)
                prevs = all_days_sorted[max(0, idx - 5):idx]      # up to 5 prior trading days
                prevs = list(reversed(prevs))                     # d1 = most recent prior
                dl = {}
                for n in range(5):
                    if n < len(prevs):
                        h, l = fh_ranges.get(prevs[n], (None, None))
                    else:
                        h, l = None, None
                    dl[f"d{n+1}_high"], dl[f"d{n+1}_low"] = h, l
                prev_vwap = self._full_vwap(grouped[prevs[0]]) if prevs else None
                sigs = self._signals_for_day(d, dc, rv, prev_vwap, dl, strat, start_t, fh)
                day_records.append({"date": d, "candles": dc, "signals": sigs,
                                    "dlevels": dl, "prev_vwap": prev_vwap, "rv": rv})
                chart_days.append(d.isoformat())

            # ── backtests ──
            spot_trades = self._backtest_spot(day_records, params)
            spot_an = self._analytics(spot_trades, "pnl")

            option_trades, option_an = [], None
            do_options = params.get("data_type") == "options" or mode == "csv"
            if do_options:
                rec_by_day = {r["date"]: r for r in day_records}
                for t in spot_trades:
                    d = date.fromisoformat(t["date"])
                    rec = rec_by_day.get(d)
                    spot_entry = t["entry_price"]
                    ot = self._option_trade(index, params, t, spot_entry, interval, d, opt_rows_by)
                    if ot:
                        option_trades.append(ot)
                option_an = self._analytics(option_trades, "option_pnl") if option_trades else None

            # ── chart payload: chosen day, else first signalled day ──
            chart = self._chart_payload(day_records, spot_trades, prefer_signal=True,
                                        target_day=params.get("chart_day"))
            self._last_records, self._last_trades = day_records, spot_trades

            for t in spot_trades:
                t.pop("_sig", None)
            for t in option_trades:
                t.pop("_sig", None)

            return {
                "status": "ok", "mode": mode, "instrument": index, "interval": interval,
                "strategy_mode": strat, "days": len(days),
                "params_used": {"stop_loss": _f(params.get("stop_loss"), 30), "target": _f(params.get("target"), 60),
                                "first_hour_minutes": fh, "session_start": start_t.strftime("%H:%M"),
                                "strike_mode": params.get("strike_mode", "ATM"),
                                "lot_size": int(_f(params.get("lot_size"), INDEX.get(index, {}).get("lot", 1)))},
                "spot": {"trades": spot_trades, **spot_an},
                "option": ({"trades": option_trades, **option_an} if option_an else None),
                "chart_dates": chart_days,
                "chart": chart,
            }

    def chart_for_day(self, day: str) -> dict:
        """Chart payload for a specific day — reuses the last run's cached data
        (no re-fetch)."""
        if not self._last_records:
            return {"status": "error", "message": "Run the research first"}
        chart = self._chart_payload(self._last_records, self._last_trades,
                                    prefer_signal=False, target_day=day)
        return {"status": "ok", "chart": chart}

    def _chart_payload(self, day_records: list[dict], spot_trades: list[dict],
                       prefer_signal: bool = True, target_day: Optional[str] = None) -> Optional[dict]:
        if not day_records:
            return None
        rec = None
        if target_day:
            for r in day_records:
                if r["date"].isoformat() == target_day:
                    rec = r
                    break
        if rec is None and prefer_signal:
            for r in reversed(day_records):
                if r["signals"]:
                    rec = r
                    break
        if rec is None:
            rec = day_records[-1]

        candles = []
        for i, c in enumerate(rec["candles"]):
            dt = _candle_dt(c)
            candles.append({"t": dt.strftime("%H:%M") if dt else "", "open": round(_f(c["open"]), 2),
                            "high": round(_f(c["high"]), 2), "low": round(_f(c["low"]), 2),
                            "close": round(_f(c["close"]), 2), "vwap": round(rec["rv"][i], 2)})
        markers = []
        dstr = rec["date"].isoformat()
        for t in spot_trades:
            if t["date"] == dstr:
                markers.append({"t": t["entry_time"], "price": t["entry_price"], "kind": t["signal_type"]})
                markers.append({"t": t["exit_time"], "price": t["exit_price"], "kind": "EXIT"})
        return {
            "date": dstr, "candles": candles,
            "dlevels": {k: (round(v, 2) if v is not None else None) for k, v in rec["dlevels"].items()},
            "prev_vwap": round(rec["prev_vwap"], 2) if rec["prev_vwap"] is not None else None,
            "confluence": self._confluence_zones(rec["dlevels"], rec["prev_vwap"]),
            "markers": markers,
        }

    # ──────────────── CSV ────────────────

    @staticmethod
    def _index_option_rows(rows: list[dict]) -> dict:
        by: dict = {}
        for r in rows:
            try:
                d = _candle_dt({"date": r["datetime"]}).date().isoformat()
                key = (d, int(_f(r["strike"])), str(r["type"]).upper())
                by.setdefault(key, []).append({"date": r["datetime"], "open": r["open"], "high": r["high"],
                                               "low": r["low"], "close": r["close"], "volume": r.get("volume", 0),
                                               "oi": r.get("oi", 0)})
            except Exception:
                continue
        for k in by:
            by[k].sort(key=lambda x: x["date"])
        return by

    @staticmethod
    def validate_csv(raw: bytes, kind: str) -> dict:
        spot_cols = ["datetime", "open", "high", "low", "close", "volume"]
        opt_cols = ["datetime", "expiry", "strike", "type", "open", "high", "low", "close", "volume", "oi"]
        req = spot_cols if kind == "spot" else opt_cols
        try:
            text = raw.decode("utf-8-sig")
        except Exception:
            return {"status": "error", "message": "File is not valid UTF-8 text"}
        reader = _csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return {"status": "error", "message": "Empty file / no header"}
        missing = [c for c in req if c not in reader.fieldnames]
        if missing:
            return {"status": "error", "message": f"Missing columns: {', '.join(missing)}"}
        rows, errors, seen = [], [], set()
        numcols = ["open", "high", "low", "close", "volume"] + (["strike", "oi"] if kind == "option" else [])
        for i, r in enumerate(reader, start=2):
            if all((str(v).strip() == "" for v in r.values())):
                continue
            try:
                datetime.fromisoformat(str(r["datetime"]).strip())
            except Exception:
                errors.append(f"Row {i}: bad datetime '{r.get('datetime')}'")
                continue
            ok = True
            for nc in numcols:
                try:
                    float(r[nc])
                except Exception:
                    errors.append(f"Row {i}: '{nc}' not numeric ('{r.get(nc)}')")
                    ok = False
            if kind == "option":
                if str(r.get("type", "")).upper() not in ("CE", "PE"):
                    errors.append(f"Row {i}: type must be CE/PE (got '{r.get('type')}')")
                    ok = False
                try:
                    _parse_expiry(r["expiry"])
                except Exception:
                    errors.append(f"Row {i}: bad expiry '{r.get('expiry')}'")
                    ok = False
            dup = (r["datetime"], r.get("strike", ""), r.get("type", ""))
            if dup in seen:
                errors.append(f"Row {i}: duplicate row")
                ok = False
            seen.add(dup)
            if ok:
                rows.append({k: r[k] for k in req})
            if len(errors) > 50:
                break
        if errors:
            return {"status": "error", "message": f"{len(errors)} validation error(s)",
                    "errors": errors[:50], "valid_rows": len(rows)}
        return {"status": "ok", "kind": kind, "rows": rows, "count": len(rows)}
