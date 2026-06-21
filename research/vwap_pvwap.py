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
SIGNAL_CUTOFF = dtime(15, 15)   # ignore new-entry signals after this
FORCE_EXIT = dtime(15, 20)      # force-exit ONLY at this time on the expiry day
                                # (positional hold — never an intraday square-off)

STRIKE_INTERVAL = 50
ITM_OFFSET = 200
LOT_SIZE = 65
LOTS = 3
QTY = LOT_SIZE * LOTS           # 195

SL_POINTS = 100.0
TARGET_POINTS = 300.0
MAX_TRADES_PER_DAY = 3

# A resolved expiry must be within this many days of the trade day, otherwise
# the *real* contract for that day has expired and is no longer listed — using
# the nearest still-listed (far-future) contract would be wrong, so we skip.
WEEKLY_MAX_DAYS = 10
MONTHLY_MAX_DAYS = 40

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
    if not isinstance(dt, datetime):
        return None
    # Kite returns tz-aware (IST) timestamps. Strip tzinfo so every comparison
    # in the engine (vs naive datetime.combine / dtime constants) is consistent
    # and never raises "can't compare offset-naive and offset-aware".
    return dt.replace(tzinfo=None)


class VwapPvwapResearch:
    """Read-only backtest engine for the VWAP / previous-day-VWAP cross."""

    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        # Configurable per-run params (defaults match the spec). Quantity is
        # always LOT_SIZE (65) × lots — e.g. 3 lots = 195, 4 lots = 260.
        self.lots = LOTS
        self.sl_points = SL_POINTS
        self.target_points = TARGET_POINTS
        self.max_trades_per_day = MAX_TRADES_PER_DAY  # re-entries allowed per day
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
        # extra prior days so the first backtest day has a previous session
        # even across weekends + holidays
        start = oldest - timedelta(days=10)
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

    def _option_candles(self, token: int, from_day: date, to_day: date) -> list[dict]:
        """Minute candles for an option across a date range (entry day → expiry
        day) — positional holds span multiple sessions."""
        key = (token, from_day, to_day)
        if key in self._opt_candle_cache:
            return self._opt_candle_cache[key]
        frm = datetime.combine(from_day, MARKET_OPEN)
        to = datetime.combine(to_day, MARKET_CLOSE)
        try:
            candles = self.broker.get_historical_data(token, frm, to, "minute") or []
        except Exception as exc:
            logger.warning("Option candle fetch failed (token=%s, %s→%s): %s",
                           token, from_day, to_day, exc)
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

    def _simulate_leg(self, option: dict, entry_dt: datetime,
                      expiry_date: date) -> Optional[dict]:
        """Positional simulation: hold from entry across days until the leg's
        own SL or Target is hit, else force-exit at 15:20 on the expiry day."""
        candles = self._option_candles(option["token"], entry_dt.date(), expiry_date)
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
        sl = entry_premium - self.sl_points
        tgt = entry_premium + self.target_points

        exit_premium = exit_reason = exit_dt = None
        for c in candles[entry_idx + 1:]:
            dt = _candle_dt(c)
            if not dt:
                continue
            lo, hi = float(c["low"]), float(c["high"])
            if lo <= sl:                                   # 1. SL (any day)
                exit_premium, exit_reason, exit_dt = sl, "SL", dt
                break
            if hi >= tgt:                                  # 2. Target (any day)
                exit_premium, exit_reason, exit_dt = tgt, "TARGET", dt
                break
            # 3. force-exit ONLY at 15:20 on the expiry day — never intraday
            if dt.date() >= expiry_date and dt.time() >= FORCE_EXIT:
                exit_premium, exit_reason, exit_dt = float(c["open"]), "EXPIRY", dt
                break
        if exit_premium is None:                           # ran out of candles
            last = candles[-1]
            exit_premium, exit_reason, exit_dt = float(last["close"]), "EXPIRY", _candle_dt(last)

        # Quantity is always the fixed NIFTY lot size (65) × configured lots,
        # never the instrument's lot_size — keeps it unambiguous (65×lots).
        qty = LOT_SIZE * self.lots
        pnl = round((exit_premium - entry_premium) * qty, 2)
        return {
            "date": entry_dt.date().isoformat(),
            "entry_time": entry_dt.strftime("%H:%M"),
            "exit_date": exit_dt.date().isoformat() if exit_dt else None,
            "exit_time": exit_dt.strftime("%H:%M") if exit_dt else None,
            "held_days": (exit_dt.date() - entry_dt.date()).days if exit_dt else 0,
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
        """days: list of {date, crossovers} precomputed on the underlying.

        Positional: one trade active at a time, possibly spanning multiple days.
        While a trade is active every crossover is ignored. After BOTH legs of
        the active trade have closed (own SL/Target, or expiry-day force-exit),
        the next crossover may re-enter — capped by max_trades_per_day per day.
        """
        expiry_mode = variant["expiry"]
        strike_mode = variant["strike"]
        max_days = WEEKLY_MAX_DAYS if expiry_mode == "weekly" else MONTHLY_MAX_DAYS
        trades: list[dict] = []
        skipped: list[dict] = []

        active_until: Optional[datetime] = None   # when the open trade's last leg closes (spans days)
        entries_per_day: dict[date, int] = {}

        # days is chronological; process every crossover across all days in order
        for day_rec in days:
            day = day_rec["date"]
            for ev in day_rec["crossovers"]:
                if active_until and ev["dt"] <= active_until:
                    continue  # a trade is active — hold, do nothing
                if entries_per_day.get(day, 0) >= self.max_trades_per_day:
                    continue

                if expiry_mode == "weekly":
                    expiry = self._weekly_expiry_for(day)
                else:
                    expiry = self._monthly_expiry_for(day)
                if not expiry:
                    skipped.append({"date": day.isoformat(), "time": ev["time"],
                                    "reason": f"no {expiry_mode} expiry resolvable"})
                    continue
                dist = (expiry - day).days
                if dist > max_days:
                    skipped.append({
                        "date": day.isoformat(), "time": ev["time"],
                        "reason": f"no live {expiry_mode} contract — nearest listed expiry "
                                  f"{expiry.isoformat()} is {dist}d out (real contract expired)",
                    })
                    continue

                ce_strike, pe_strike = self._strikes_for(ev["spot"], strike_mode)
                ce = self._resolve_option(expiry, ce_strike, "CE")
                pe = self._resolve_option(expiry, pe_strike, "PE")
                if not ce or not pe:
                    skipped.append({
                        "date": day.isoformat(), "time": ev["time"],
                        "reason": f"contract not listed (expiry {expiry.isoformat()} "
                                  f"CE {ce_strike}/PE {pe_strike})",
                    })
                    continue

                legs = []
                for opt in (ce, pe):
                    leg = self._simulate_leg(opt, ev["dt"], expiry)
                    if leg:
                        leg["expiry_type"] = expiry_mode
                        legs.append(leg)
                if not legs:
                    skipped.append({"date": day.isoformat(), "time": ev["time"],
                                    "reason": "no option candle data in entry→expiry range"})
                    continue

                trades.extend(legs)
                entries_per_day[day] = entries_per_day.get(day, 0) + 1
                # Trade stays active until BOTH legs have closed (max exit time,
                # which may be on a later day for a positional hold).
                active_until = max(
                    (datetime.combine(
                        date.fromisoformat(l["exit_date"]),
                        dtime.fromisoformat(l["exit_time"]),
                    ) for l in legs if l.get("exit_date") and l.get("exit_time")),
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

    def run(self, days: int = 30, variant_keys: Optional[list[str]] = None,
            target_date: Optional[date] = None, lots: Optional[int] = None,
            sl_points: Optional[float] = None, target_points: Optional[float] = None,
            max_trades_per_day: Optional[int] = None) -> dict:
        """Run the backtest across the requested variants. Thread-safe.

        If ``target_date`` is given, only that single day is backtested (using
        its own previous-session VWAP). Otherwise a rolling window of the last
        ``days`` trading days is used. ``lots`` / ``sl_points`` / ``target_points``
        override the defaults (qty = 65 × lots).
        """
        with self._lock:
            self.lots = max(1, int(lots)) if lots else LOTS
            self.sl_points = float(sl_points) if sl_points else SL_POINTS
            self.target_points = float(target_points) if target_points else TARGET_POINTS
            self.max_trades_per_day = max(1, int(max_trades_per_day)) if max_trades_per_day else MAX_TRADES_PER_DAY
            self._opt_candle_cache.clear()
            if target_date is not None:
                trading_days = [target_date]
            else:
                days = max(1, min(int(days), 60))
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
                    "mode": "single_day" if target_date is not None else "rolling",
                    "target_date": target_date.isoformat() if target_date is not None else None,
                    "days_requested": 1 if target_date is not None else days,
                    "days_with_data": covered,
                    "lot_size": LOT_SIZE,
                    "lots": self.lots,
                    "qty": LOT_SIZE * self.lots,
                    "sl_points": self.sl_points,
                    "target_points": self.target_points,
                    "max_trades_per_day": self.max_trades_per_day,
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

    def export_vwap(self, frm: date, to: date) -> dict:
        """Per-minute VWAP / previous-day VWAP / crossover flag rows for a date
        range, for independent verification in Excel. Crossover flag uses the
        same tradeable window (09:30–15:15) the backtest acts on."""
        with self._lock:
            if to < frm:
                frm, to = to, frm
            self._load_spot_range(frm, to)
            rows: list[dict] = []
            for d in sorted(x for x in self._spot_by_day if frm <= x <= to):
                candles = self._underlying_candles(d)
                if not candles:
                    continue
                prev = self._previous_session_vwap(d, [])
                rv = self._running_vwap_series(candles)
                flags = {}
                if prev is not None:
                    for e in self._crossovers(candles, prev):
                        flags[e["idx"]] = e["direction"]
                for i, c in enumerate(candles):
                    dt = _candle_dt(c)
                    rows.append({
                        "date": d.isoformat(),
                        "time": dt.strftime("%H:%M") if dt else "",
                        "close": round(float(c["close"]), 2),
                        "vwap": round(rv[i], 2),
                        "prev_vwap": round(prev, 2) if prev is not None else "",
                        "crossover": flags.get(i, ""),
                    })
            return {"status": "ok", "from": frm.isoformat(), "to": to.isoformat(), "rows": rows}
