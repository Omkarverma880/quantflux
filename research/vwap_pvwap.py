"""
Research module #1 — VWAP vs Previous-Day VWAP cross (NIFTY options).

This is a RESEARCH / BACKTEST engine only. It never places live orders, never
mutates strategy state, and never touches credentials. It reuses the existing
``Broker`` (Zerodha Kite) wrapper for historical data exclusively.

Concept
-------
On every trading day we compute:
  • Current-day running VWAP   (cumulative Σ(typical×vol) / Σ(vol))
  • Previous-day VWAP          (the prior session's full-day VWAP — a constant
                                horizontal reference for the current day)

A *crossover* is any minute where the running VWAP crosses the previous-day
VWAP (either direction). The crossover is treated purely as a trigger — at
each valid crossover we BUY **both** a CALL and a PUT and track each leg.

Entry rules
-----------
  • No entry before 09:30, no new signal after 15:15.
  • Only one trade active at a time (a "trade" = one crossover = CE + PE legs).
  • Max 3 trades per day; re-entry allowed after a trade's legs close.

Per-leg exit priority
---------------------
  1. Stop-loss   — premium drops 100 points from entry
  2. Target      — premium rises 300 points from entry
  3. Opposite crossover signal
  4. Force exit at 15:20

Variants (4)
------------
  1. Weekly  expiry + 200-ITM
  2. Weekly  expiry + ATM
  3. Monthly expiry + 200-ITM
  4. Monthly expiry + ATM

Data-availability notes (important, surfaced in the UI)
-------------------------------------------------------
  • Zerodha ``instruments()`` only lists *currently tradable* contracts, so
    options that already expired in the lookback window cannot be resolved to
    an instrument_token and their premium history is unavailable — those
    signals are skipped and logged.
  • The NIFTY 50 index carries no traded volume in historical candles, so the
    VWAP is computed on the index typical price (equal-weighted). The volume
    formula is retained so a volume-bearing source (e.g. NIFTY futures) can be
    swapped in via ``_underlying_candles`` without touching the rest.
"""
from __future__ import annotations

import math
import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger

logger = get_logger("research.vwap_pvwap")

# ── Constants (configurable defaults) ─────────────────────────────
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
ENTRY_START = dtime(9, 30)      # no entries before this
SIGNAL_CUTOFF = dtime(15, 15)   # ignore signals after this
FORCE_EXIT = dtime(15, 20)      # force-exit any open leg

STRIKE_INTERVAL = 50
ITM_OFFSET = 200
LOT_SIZE = 65
LOTS = 3
QTY = LOT_SIZE * LOTS           # 195

SL_POINTS = 100.0
TARGET_POINTS = 300.0
MAX_TRADES_PER_DAY = 3

INDEX_NAME = "NIFTY"
INDEX_SPOT_TRADINGSYMBOL = "NIFTY 50"

VARIANTS = [
    {"key": "weekly_itm",  "label": "Weekly · 200 ITM",  "expiry": "weekly",  "strike": "ITM"},
    {"key": "weekly_atm",  "label": "Weekly · ATM",       "expiry": "weekly",  "strike": "ATM"},
    {"key": "monthly_itm", "label": "Monthly · 200 ITM",  "expiry": "monthly", "strike": "ITM"},
    {"key": "monthly_atm", "label": "Monthly · ATM",      "expiry": "monthly", "strike": "ATM"},
]


def _parse_expiry(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def _candle_dt(c: dict) -> Optional[datetime]:
    dt = c.get("date")
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    return dt if isinstance(dt, datetime) else None


class VwapPvwapResearch:
    """Read-only backtest engine for the VWAP / previous-day-VWAP cross."""

    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        # daily caches (per process; cheap and avoids repeated API calls)
        self._index_token: Optional[int] = None
        self._nfo_options: Optional[list[dict]] = None     # NIFTY CE/PE instruments
        self._nfo_date: Optional[date] = None
        self._spot_by_day: dict[date, list[dict]] = {}     # date -> minute candles
        self._opt_candle_cache: dict[tuple[int, date], list[dict]] = {}

    # ──────────────────── Instrument resolution ──────────────────

    def _resolve_index_token(self) -> Optional[int]:
        if self._index_token:
            return self._index_token
        try:
            for inst in self.broker.get_instruments("NSE"):
                if inst.get("tradingsymbol") == INDEX_SPOT_TRADINGSYMBOL:
                    self._index_token = int(inst["instrument_token"])
                    return self._index_token
        except Exception as exc:
            logger.error("Index token lookup failed: %s", exc)
        return None

    def _nifty_options(self) -> list[dict]:
        """Cached list of currently-listed NIFTY CE/PE option instruments."""
        today = date.today()
        if self._nfo_options is not None and self._nfo_date == today:
            return self._nfo_options
        opts: list[dict] = []
        try:
            for inst in self.broker.get_instruments("NFO"):
                if inst.get("name") != INDEX_NAME:
                    continue
                if inst.get("instrument_type") not in ("CE", "PE"):
                    continue
                exp = _parse_expiry(inst.get("expiry"))
                if not exp:
                    continue
                opts.append({
                    "tradingsymbol": inst.get("tradingsymbol"),
                    "token": int(inst["instrument_token"]),
                    "strike": float(inst.get("strike", 0) or 0),
                    "type": inst.get("instrument_type"),
                    "expiry": exp,
                    "lot_size": int(inst.get("lot_size", LOT_SIZE) or LOT_SIZE),
                })
        except Exception as exc:
            logger.error("NFO instruments fetch failed: %s", exc)
        self._nfo_options = opts
        self._nfo_date = today
        logger.info("Loaded %d listed NIFTY option contracts", len(opts))
        return opts

    def _available_expiries(self) -> list[date]:
        return sorted({o["expiry"] for o in self._nifty_options()})

    def _monthly_expiries(self) -> list[date]:
        """The last (largest) expiry within each calendar month = the monthly."""
        by_month: dict[tuple[int, int], date] = {}
        for e in self._available_expiries():
            key = (e.year, e.month)
            if key not in by_month or e > by_month[key]:
                by_month[key] = e
        return sorted(by_month.values())

    def _weekly_expiry_for(self, day: date) -> Optional[date]:
        for e in self._available_expiries():
            if e >= day:
                return e
        return None

    def _monthly_expiry_for(self, day: date) -> Optional[date]:
        for e in self._monthly_expiries():
            if e >= day:
                return e
        return None

    def _resolve_option(self, expiry: date, strike: float, opt_type: str) -> Optional[dict]:
        for o in self._nifty_options():
            if o["expiry"] == expiry and o["type"] == opt_type and abs(o["strike"] - strike) < 0.5:
                return o
        return None

    # ──────────────────── Historical data ────────────────────────

    def _trading_days(self, n: int) -> list[date]:
        """Last *n* weekdays (Mon–Fri) up to and including today if a weekday,
        else up to the most recent weekday. Holidays are tolerated downstream
        (days with no candles are skipped)."""
        days: list[date] = []
        d = date.today()
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        return sorted(days)

    def _load_spot_range(self, oldest: date, newest: date):
        """Fetch index minute candles for the whole window in one call and
        group them by day (cached)."""
        token = self._resolve_index_token()
        if not token:
            raise RuntimeError("Could not resolve NIFTY 50 index token")
        # one extra prior day so the first backtest day has a previous session
        start = oldest - timedelta(days=6)
        grouped: dict[date, list[dict]] = {}
        # Kite caps minute history at ~60 days per request — fetch in 55-day chunks.
        chunk = timedelta(days=55)
        cur = start
        while cur <= newest:
            chunk_end = min(cur + chunk, newest)
            frm = datetime.combine(cur, MARKET_OPEN)
            to = datetime.combine(chunk_end, MARKET_CLOSE)
            try:
                candles = self.broker.get_historical_data(token, frm, to, "minute") or []
            except Exception as exc:
                logger.error("Index minute fetch failed (%s→%s): %s", cur, chunk_end, exc)
                candles = []
            for c in candles:
                dt = _candle_dt(c)
                if dt:
                    grouped.setdefault(dt.date(), []).append(c)
            cur = chunk_end + timedelta(days=1)
        self._spot_by_day = grouped

    def _underlying_candles(self, day: date) -> list[dict]:
        """Hook for the VWAP/crossover underlying. Index today; swap to a
        futures token here to get a volume-weighted VWAP."""
        return self._spot_by_day.get(day, [])

    def _option_candles(self, token: int, day: date) -> list[dict]:
        key = (token, day)
        if key in self._opt_candle_cache:
            return self._opt_candle_cache[key]
        frm = datetime.combine(day, MARKET_OPEN)
        to = datetime.combine(day, MARKET_CLOSE)
        try:
            candles = self.broker.get_historical_data(token, frm, to, "minute") or []
        except Exception as exc:
            logger.warning("Option candle fetch failed (token=%s, %s): %s", token, day, exc)
            candles = []
        self._opt_candle_cache[key] = candles
        return candles

    # ──────────────────── VWAP / crossovers ──────────────────────

    @staticmethod
    def _full_day_vwap(candles: list[dict]) -> Optional[float]:
        cum_pv = cum_v = cum_tp = 0.0
        n = 0
        for c in candles:
            tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
            v = float(c.get("volume", 0) or 0)
            cum_pv += tp * v
            cum_v += v
            cum_tp += tp
            n += 1
        if n == 0:
            return None
        return cum_pv / cum_v if cum_v > 0 else cum_tp / n

    @staticmethod
    def _running_vwap_series(candles: list[dict]) -> list[float]:
        out: list[float] = []
        cum_pv = cum_v = cum_tp = 0.0
        n = 0
        for c in candles:
            tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
            v = float(c.get("volume", 0) or 0)
            cum_pv += tp * v
            cum_v += v
            cum_tp += tp
            n += 1
            out.append(cum_pv / cum_v if cum_v > 0 else cum_tp / n)
        return out

    def _crossovers(self, candles: list[dict], prev_vwap: float) -> list[dict]:
        """Return crossover events: {idx, time, dt, spot, direction}.

        direction = 'BULL' when running VWAP crosses above prev VWAP,
                    'BEAR' when it crosses below.
        Only events between ENTRY_START and SIGNAL_CUTOFF are returned.
        """
        rv = self._running_vwap_series(candles)
        events: list[dict] = []
        prev_diff = None
        for i, c in enumerate(candles):
            dt = _candle_dt(c)
            if not dt:
                continue
            diff = rv[i] - prev_vwap
            if prev_diff is not None and prev_diff != 0 and diff != 0:
                crossed_up = prev_diff < 0 < diff
                crossed_dn = prev_diff > 0 > diff
                if (crossed_up or crossed_dn) and ENTRY_START <= dt.time() <= SIGNAL_CUTOFF:
                    events.append({
                        "idx": i,
                        "time": dt.strftime("%H:%M"),
                        "dt": dt,
                        "spot": float(c["close"]),
                        "direction": "BULL" if crossed_up else "BEAR",
                    })
            prev_diff = diff
        return events

    @staticmethod
    def _atm(spot: float) -> int:
        return int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)

    def _strikes_for(self, spot: float, mode: str) -> tuple[int, int]:
        """Return (ce_strike, pe_strike) for the given variant mode."""
        atm = self._atm(spot)
        if mode == "ATM":
            return atm, atm
        # 200 ITM: CE 200 below spot, PE 200 above spot
        return atm - ITM_OFFSET, atm + ITM_OFFSET

    # ──────────────────── Leg simulation ─────────────────────────

    def _simulate_leg(self, option: dict, day: date, entry_dt: datetime,
                      opposite_dt: Optional[datetime]) -> Optional[dict]:
        candles = self._option_candles(option["token"], day)
        if not candles:
            return None
        # locate entry candle (first option candle at/after the signal minute)
        entry_idx = None
        for i, c in enumerate(candles):
            dt = _candle_dt(c)
            if dt and dt >= entry_dt:
                entry_idx = i
                break
        if entry_idx is None:
            return None

        entry_premium = float(candles[entry_idx]["close"])
        sl = entry_premium - SL_POINTS
        tgt = entry_premium + TARGET_POINTS

        exit_premium = exit_reason = exit_dt = None
        for c in candles[entry_idx + 1:]:
            dt = _candle_dt(c)
            if not dt:
                continue
            if dt.time() >= FORCE_EXIT:
                exit_premium, exit_reason, exit_dt = float(c["open"]), "TIME_EXIT", dt
                break
            lo, hi = float(c["low"]), float(c["high"])
            if lo <= sl:                                   # 1. SL
                exit_premium, exit_reason, exit_dt = sl, "SL", dt
                break
            if hi >= tgt:                                  # 2. Target
                exit_premium, exit_reason, exit_dt = tgt, "TARGET", dt
                break
            if opposite_dt and dt >= opposite_dt:          # 3. opposite signal
                exit_premium, exit_reason, exit_dt = float(c["close"]), "OPPOSITE_SIGNAL", dt
                break
        if exit_premium is None:                           # 4. ran out of candles
            last = candles[-1]
            exit_premium, exit_reason, exit_dt = float(last["close"]), "TIME_EXIT", _candle_dt(last)

        qty = option["lot_size"] * LOTS
        pnl = round((exit_premium - entry_premium) * qty, 2)
        return {
            "date": day.isoformat(),
            "entry_time": entry_dt.strftime("%H:%M"),
            "exit_time": exit_dt.strftime("%H:%M") if exit_dt else None,
            "direction": "CALL" if option["type"] == "CE" else "PUT",
            "option_type": option["type"],
            "expiry": option["expiry"].isoformat(),
            "strike": int(option["strike"]),
            "symbol": option["tradingsymbol"],
            "premium_buy": round(entry_premium, 2),
            "premium_sell": round(exit_premium, 2),
            "qty": qty,
            "pnl": pnl,
            "exit_reason": exit_reason,
        }

    # ──────────────────── Per-variant backtest ───────────────────

    def _run_variant(self, variant: dict, days: list[dict]) -> dict:
        """days: list of {date, crossovers} precomputed on the underlying."""
        expiry_mode = variant["expiry"]
        strike_mode = variant["strike"]
        trades: list[dict] = []
        skipped: list[dict] = []

        for day_rec in days:
            day = day_rec["date"]
            crossovers = day_rec["crossovers"]
            if not crossovers:
                continue

            if expiry_mode == "weekly":
                expiry = self._weekly_expiry_for(day)
            else:
                expiry = self._monthly_expiry_for(day)
            if not expiry:
                skipped.append({"date": day.isoformat(), "reason": "no expiry resolvable"})
                continue

            active_until: Optional[datetime] = None
            trades_today = 0

            for n, ev in enumerate(crossovers):
                if trades_today >= MAX_TRADES_PER_DAY:
                    break
                if active_until and ev["dt"] <= active_until:
                    continue  # only one active trade at a time

                # opposite crossover after this entry → exit trigger for the legs
                opposite_dt = None
                for later in crossovers[n + 1:]:
                    if later["direction"] != ev["direction"]:
                        opposite_dt = later["dt"]
                        break

                ce_strike, pe_strike = self._strikes_for(ev["spot"], strike_mode)
                ce = self._resolve_option(expiry, ce_strike, "CE")
                pe = self._resolve_option(expiry, pe_strike, "PE")
                if not ce or not pe:
                    skipped.append({
                        "date": day.isoformat(), "time": ev["time"],
                        "reason": f"contract not listed (expiry {expiry.isoformat()} "
                                  f"CE {ce_strike}/PE {pe_strike}) — likely expired",
                    })
                    continue

                legs = []
                for opt in (ce, pe):
                    leg = self._simulate_leg(opt, day, ev["dt"], opposite_dt)
                    if leg:
                        leg["expiry_type"] = expiry_mode
                        legs.append(leg)
                if not legs:
                    skipped.append({
                        "date": day.isoformat(), "time": ev["time"],
                        "reason": "no option candle data for the day",
                    })
                    continue

                trades.extend(legs)
                trades_today += 1
                active_until = max(
                    (datetime.combine(day, dtime.fromisoformat(l["exit_time"])) for l in legs if l["exit_time"]),
                    default=ev["dt"],
                )

        return {
            "key": variant["key"],
            "label": variant["label"],
            "trades": trades,
            "skipped": skipped,
            **self._metrics(trades),
        }

    # ──────────────────── Metrics ────────────────────────────────

    @staticmethod
    def _metrics(trades: list[dict]) -> dict:
        n = len(trades)
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        net = round(sum(pnls), 2)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        # equity curve + max drawdown (chronological)
        equity, peak, max_dd = [], 0.0, 0.0
        run = 0.0
        for t in trades:
            run += t["pnl"]
            equity.append(round(run, 2))
            peak = max(peak, run)
            max_dd = min(max_dd, run - peak)

        # daily PnL series → Sharpe (annualised) + daily bars
        by_day: dict[str, float] = {}
        for t in trades:
            by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["pnl"]
        daily = [{"date": d, "pnl": round(v, 2)} for d, v in sorted(by_day.items())]
        dvals = [d["pnl"] for d in daily]
        sharpe = 0.0
        if len(dvals) > 1:
            mean = sum(dvals) / len(dvals)
            var = sum((x - mean) ** 2 for x in dvals) / (len(dvals) - 1)
            std = math.sqrt(var)
            if std > 0:
                sharpe = round((mean / std) * math.sqrt(252), 2)

        win_rate = round(100 * len(wins) / n, 1) if n else 0.0
        avg_profit = round(gross_profit / len(wins), 2) if wins else 0.0
        avg_loss = round(-gross_loss / len(losses), 2) if losses else 0.0
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0)
        expectancy = round(net / n, 2) if n else 0.0

        # drawdown curve (running distance below peak)
        dd_curve, peak2 = [], 0.0
        run2 = 0.0
        for t in trades:
            run2 += t["pnl"]
            peak2 = max(peak2, run2)
            dd_curve.append(round(run2 - peak2, 2))

        return {
            "total_trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "net_pnl": net,
            "avg_profit": avg_profit,
            "avg_loss": avg_loss,
            "max_drawdown": round(max_dd, 2),
            "profit_factor": (None if profit_factor == float("inf") else profit_factor),
            "expectancy": expectancy,
            "sharpe": sharpe,
            "equity_curve": equity,
            "drawdown_curve": dd_curve,
            "daily_pnl": daily,
        }

    # ──────────────────── Public API ─────────────────────────────

    def run(self, days: int = 30, variant_keys: Optional[list[str]] = None) -> dict:
        """Run the backtest across the requested variants. Thread-safe."""
        with self._lock:
            days = max(1, min(int(days), 60))
            self._opt_candle_cache.clear()
            trading_days = self._trading_days(days)
            self._load_spot_range(trading_days[0], trading_days[-1])

            # crossovers are identical across variants → compute once per day
            day_recs: list[dict] = []
            covered = 0
            for d in trading_days:
                candles = self._underlying_candles(d)
                if not candles:
                    continue
                prev = self._previous_session_vwap(d, trading_days)
                if prev is None:
                    continue
                covered += 1
                day_recs.append({"date": d, "crossovers": self._crossovers(candles, prev)})

            selected = [v for v in VARIANTS if not variant_keys or v["key"] in variant_keys]
            results = {v["key"]: self._run_variant(v, day_recs) for v in selected}

            return {
                "status": "ok",
                "params": {
                    "days_requested": days,
                    "days_with_data": covered,
                    "lot_size": LOT_SIZE,
                    "lots": LOTS,
                    "qty": QTY,
                    "sl_points": SL_POINTS,
                    "target_points": TARGET_POINTS,
                    "max_trades_per_day": MAX_TRADES_PER_DAY,
                    "entry_start": ENTRY_START.strftime("%H:%M"),
                    "signal_cutoff": SIGNAL_CUTOFF.strftime("%H:%M"),
                    "force_exit": FORCE_EXIT.strftime("%H:%M"),
                },
                "vwap_basis": "NIFTY 50 index typical price (index has no traded "
                              "volume; volume-weighting seam available for futures)",
                "variants": results,
                "comparison": [
                    {
                        "key": r["key"], "label": r["label"],
                        "trades": r["total_trades"], "win_rate": r["win_rate"],
                        "pnl": r["net_pnl"], "max_dd": r["max_drawdown"],
                        "sharpe": r["sharpe"], "profit_factor": r["profit_factor"],
                    }
                    for r in results.values()
                ],
            }

    def _previous_session_vwap(self, day: date, all_days: list[date]) -> Optional[float]:
        """Full-day VWAP of the trading session immediately before *day*."""
        # find the most recent day < `day` that has candles
        prior = sorted(d for d in self._spot_by_day if d < day)
        for d in reversed(prior):
            v = self._full_day_vwap(self._spot_by_day[d])
            if v is not None:
                return v
        return None

    def signals(self, target_day: Optional[date] = None) -> dict:
        """Chart overlay for one day: NIFTY close series, running VWAP,
        previous-day VWAP (flat), and crossover markers."""
        with self._lock:
            day = target_day or self._trading_days(1)[-1]
            self._load_spot_range(day, day)
            candles = self._underlying_candles(day)
            if not candles:
                return {"status": "error", "message": f"No index data for {day.isoformat()}"}
            prev = self._previous_session_vwap(day, [day])
            rv = self._running_vwap_series(candles)
            series = []
            for i, c in enumerate(candles):
                dt = _candle_dt(c)
                series.append({
                    "t": dt.strftime("%H:%M") if dt else "",
                    "close": round(float(c["close"]), 2),
                    "vwap": round(rv[i], 2),
                    "prev_vwap": round(prev, 2) if prev is not None else None,
                })
            events = self._crossovers(candles, prev) if prev is not None else []
            markers = [
                {"t": e["time"], "spot": round(e["spot"], 2), "direction": e["direction"]}
                for e in events
            ]
            return {
                "status": "ok",
                "date": day.isoformat(),
                "prev_vwap": round(prev, 2) if prev is not None else None,
                "series": series,
                "markers": markers,
            }
