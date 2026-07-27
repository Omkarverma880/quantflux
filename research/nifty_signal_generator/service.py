"""
NIFTY Signal Generator — service / data-orchestration layer.

Read-only research engine. It NEVER places orders or mutates strategy state.
It reuses the existing shared services:
  • ``core.broker.Broker``            — historical candles, LTP, instruments
  • ``research.option_chain.OptionChain`` — instrument / expiry / spot resolution
  • ``research.vwap_pvwap`` constants  — market timings, candle helpers

For each completed candle of the selected timeframe it produces one row:

    Time | Call OI | Put OI | Diff | PCR | Option Signal
         | VWAP | Previous VWAP | Nifty Price | VWAP Signal

Per-strike OI is summed internally and never surfaced individually (spec).
"""
from __future__ import annotations

import threading
import time
from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.vwap_pvwap import (
    MARKET_OPEN, MARKET_CLOSE, _candle_dt, _parse_expiry,
)
from research.nifty_signal_generator import calculations as calc
from research.nifty_signal_generator.config import load_config, save_config, sanitize
from research.nifty_signal_generator.constants import (
    TIMEFRAME_MAP, MARKETS, DEFAULT_MARKET,
)

logger = get_logger("research.nifty_signal_generator")

# Base interval → minutes (for candle-completion + bucketing).
_INTERVAL_MINUTES = {
    "minute": 1, "3minute": 3, "5minute": 5, "10minute": 10,
    "15minute": 15, "30minute": 30, "60minute": 60,
}

# How far back non-intraday timeframes look (approx trading periods worth).
_LOOKBACK_DAYS = {"day": 45, "week": 220, "month": 420}

# Safety cap so a very wide intraday range can never explode API usage.
_MAX_STRIKES = 60

# Re-fetch today's still-growing candle series at most this often (seconds).
_TODAY_TTL = 45.0


class NiftySignalGenerator:
    """Generates PCR (option-chain) + VWAP signals per completed candle."""

    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self._lock = threading.Lock()
        self._index_tokens: dict[str, int] = {}     # spot tradingsymbol → token
        # caches
        self._nfo_cache: dict[str, tuple[date, list[dict]]] = {}   # NFO name → (date, options)
        self._candle_cache: dict[tuple, tuple[float, list[dict]]] = {}   # key → (ts, candles)
        self._row_cache: dict[tuple, dict] = {}     # (scope, candle_iso) → row

    # ── configuration passthrough ────────────────────────────────
    def load_config(self) -> dict:
        return load_config()

    def save_config(self, partial: dict) -> dict:
        cfg = save_config(partial)
        # config change → historical table must regenerate cleanly
        self._row_cache.clear()
        return cfg

    # ── instrument helpers (self-contained, market-parameterised) ──
    def _market_cfg(self, key: str) -> dict:
        m = MARKETS.get(key)
        if not m or not m.get("enabled"):
            return MARKETS[DEFAULT_MARKET]
        return m

    def _resolve_index_token(self, spot_tradingsymbol: str) -> Optional[int]:
        """NSE index instrument token for the spot symbol (cached per symbol)."""
        if spot_tradingsymbol in self._index_tokens:
            return self._index_tokens[spot_tradingsymbol]
        try:
            for inst in self.broker.get_instruments("NSE"):
                if inst.get("tradingsymbol") == spot_tradingsymbol:
                    tok = int(inst["instrument_token"])
                    self._index_tokens[spot_tradingsymbol] = tok
                    return tok
        except Exception as exc:
            logger.error("Index token lookup failed (%s): %s", spot_tradingsymbol, exc)
        return None

    def _market_options(self, name: str) -> list[dict]:
        """Currently-listed CE/PE option contracts for an index (NFO ``name``),
        cached for the day. Filtering by ``name`` keeps each instrument's chain
        fully independent — NIFTY and BANKNIFTY never mix."""
        today = date.today()
        cached = self._nfo_cache.get(name)
        if cached and cached[0] == today:
            return cached[1]
        opts: list[dict] = []
        try:
            for inst in self.broker.get_instruments("NFO"):
                if inst.get("name") != name or inst.get("instrument_type") not in ("CE", "PE"):
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
                })
        except Exception as exc:
            logger.error("NFO options fetch failed (%s): %s", name, exc)
        self._nfo_cache[name] = (today, opts)
        return opts

    def _market_expiry_for(self, name: str, expiry_type: str, day: date) -> Optional[date]:
        exps = sorted({o["expiry"] for o in self._market_options(name)})
        if expiry_type == "monthly":
            by_month: dict = {}
            for e in exps:
                k = (e.year, e.month)
                if k not in by_month or e > by_month[k]:
                    by_month[k] = e
            exps = sorted(by_month.values())
        for e in exps:
            if e >= day:
                return e
        return exps[-1] if exps else None

    def _market_resolve(self, name: str, expiry: date, strike: float, opt_type: str) -> Optional[dict]:
        for o in self._market_options(name):
            if o["expiry"] == expiry and o["type"] == opt_type and abs(o["strike"] - strike) < 0.5:
                return o
        return None

    # ── candle fetching (cached; today gets a short TTL) ──────────
    def _get_candles(self, token: int, frm: datetime, to: datetime,
                     interval: str, oi: bool, force: bool = False) -> list[dict]:
        key = (token, frm, to, interval, oi)
        cached = self._candle_cache.get(key)
        is_today = to.date() >= date.today()
        now = time.monotonic()
        # ``force`` bypasses the today-TTL so a newly-completed candle is always
        # frozen from post-close (final) data — never a stale pre-close read.
        if cached and not force and (not is_today or now - cached[0] < _TODAY_TTL):
            return cached[1]
        try:
            candles = self.broker.get_historical_data(token, frm, to, interval, oi=oi) or []
        except Exception as exc:
            logger.warning("Historical fetch failed (token=%s %s): %s", token, interval, exc)
            candles = cached[1] if cached else []
        self._candle_cache[key] = (now, candles)
        return candles

    # ── window resolution ─────────────────────────────────────────
    @staticmethod
    def _latest_trading_day(d: Optional[date]) -> date:
        d = d or date.today()
        while d.weekday() >= 5:      # roll Sat/Sun back to Friday
            d -= timedelta(days=1)
        return d

    def _window(self, tf: dict, target: Optional[date]) -> tuple[datetime, datetime, date]:
        """Return (from_dt, to_dt, anchor_day) for the fetch."""
        if tf["intraday"]:
            day = self._latest_trading_day(target)
            return (datetime.combine(day, MARKET_OPEN),
                    datetime.combine(day, MARKET_CLOSE), day)
        back = _LOOKBACK_DAYS.get(tf["interval"], 45)
        end = self._latest_trading_day(target)
        return (datetime.combine(end - timedelta(days=back), MARKET_OPEN),
                datetime.combine(end, MARKET_CLOSE), end)

    # ── bucketing ─────────────────────────────────────────────────
    @staticmethod
    def _group_key(dt: datetime, intraday: bool):
        return dt.date() if intraday else "all"

    def _build_buckets(self, index_candles: list[dict], tf: dict) -> list[dict]:
        """Aggregate base candles into output candles with a running VWAP.

        Each bucket carries: end datetime, close price, cumulative VWAP (anchored
        per-session for intraday, per-window otherwise) and its member datetimes.
        """
        agg = int(tf.get("agg", 1))
        intraday = tf["intraday"]
        # sort + attach datetimes
        rows = []
        for c in index_candles:
            dt = _candle_dt(c)
            if dt:
                rows.append((dt, c))
        rows.sort(key=lambda r: r[0])

        buckets: list[dict] = []
        cur_group = None
        typ_sum = 0.0
        typ_cnt = 0
        chunk: list[tuple[datetime, dict]] = []

        def flush(chunk_rows):
            nonlocal typ_sum, typ_cnt
            if not chunk_rows:
                return
            for _dt, c in chunk_rows:
                typ_sum += calc.typical_price(c)
                typ_cnt += 1
            last_dt, last_c = chunk_rows[-1]
            buckets.append({
                "end_dt": last_dt,
                "member_dts": [d for d, _ in chunk_rows],
                "close": round(float(last_c["close"]), 2),
                "vwap": calc.cumulative_vwap(typ_sum, typ_cnt),
            })

        for dt, c in rows:
            g = self._group_key(dt, intraday)
            if g != cur_group:
                flush(chunk)
                chunk = []
                cur_group = g
                typ_sum = 0.0
                typ_cnt = 0
            chunk.append((dt, c))
            if len(chunk) >= agg:
                flush(chunk)
                chunk = []
        flush(chunk)
        return buckets

    def _is_completed(self, end_dt: datetime, tf: dict) -> bool:
        """A bucket is complete once its final base candle's period has elapsed."""
        now = datetime.now()
        if tf["intraday"]:
            mins = _INTERVAL_MINUTES.get(tf["interval"], 1)
            return now >= end_dt + timedelta(minutes=mins)
        # day / week / month: completed if the period is not the current one
        if end_dt.date() < date.today():
            return True
        return now.time() >= MARKET_CLOSE

    # ── OI series ─────────────────────────────────────────────────
    @staticmethod
    def _oi_at(series: list[tuple[datetime, float]], when: datetime) -> Optional[float]:
        """Last OI at or before ``when`` (options can miss illiquid candles)."""
        if not series:
            return None
        dts = [d for d, _ in series]
        i = bisect_right(dts, when)
        return series[i - 1][1] if i > 0 else None

    def _option_oi_series(self, name: str, expiry: date, strike: int, opt_type: str,
                          frm: datetime, to: datetime, interval: str,
                          force: bool = False) -> list[tuple[datetime, float]]:
        o = self._market_resolve(name, expiry, float(strike), opt_type)
        if not o:
            return []
        candles = self._get_candles(int(o["token"]), frm, to, interval, oi=True, force=force)
        out: list[tuple[datetime, float]] = []
        for c in candles:
            dt = _candle_dt(c)
            if dt is not None:
                out.append((dt, float(c.get("oi", 0) or 0)))
        out.sort(key=lambda r: r[0])
        return out

    # ── row assembly ──────────────────────────────────────────────
    def generate_table_row(self, bucket: dict, cfg: dict, name: str, expiry: date,
                           oi_cache: dict, frm: datetime, to: datetime,
                           interval: str, prev_vwap: Optional[float],
                           force_oi: bool = False) -> dict:
        interval_step = int(cfg["strike_interval"])
        count = int(cfg["strike_count"])
        price = bucket["close"]
        vwap = bucket["vwap"]
        end_dt = bucket["end_dt"]

        atm = calc.get_atm_strike(price, interval_step)
        strikes = calc.generate_selected_strikes(atm, interval_step, count)

        def side_oi(opt_type: str) -> dict[int, Optional[float]]:
            res: dict[int, Optional[float]] = {}
            for s in strikes:
                ck = (opt_type, s)
                if ck not in oi_cache:
                    oi_cache[ck] = self._option_oi_series(
                        name, expiry, s, opt_type, frm, to, interval, force=force_oi)
                res[s] = self._oi_at(oi_cache[ck], end_dt)
            return res

        call_oi = calc.calculate_call_sum(side_oi("CE"), strikes)
        put_oi = calc.calculate_put_sum(side_oi("PE"), strikes)
        diff = calc.calculate_difference(put_oi, call_oi)
        pcr = calc.calculate_pcr(put_oi, call_oi)
        option_signal = calc.generate_option_signal(pcr)
        vwap_signal = calc.generate_vwap_signal(price, vwap)

        return {
            "time": end_dt.strftime("%H:%M"),
            "date": end_dt.strftime("%Y-%m-%d"),
            "datetime": end_dt.isoformat(),
            "atm": atm,                       # internal / diagnostics — not a UI column
            "call_oi": call_oi,
            "put_oi": put_oi,
            "diff": diff,
            "pcr": pcr,
            "option_signal": option_signal,
            "option_color": calc.signal_color(option_signal),
            "vwap": vwap,
            "previous_vwap": prev_vwap,       # always in the data model (UI may hide)
            "price": price,
            "vwap_signal": vwap_signal,
            "vwap_color": calc.signal_color(vwap_signal),
        }

    # ── historical generation ─────────────────────────────────────
    def load_historical_rows(self, cfg: dict, target: Optional[date]) -> dict:
        market = self._market_cfg(cfg["market"])
        tf = TIMEFRAME_MAP[cfg["timeframe"]]
        interval = tf["interval"]
        frm, to, anchor = self._window(tf, target)

        token = self._resolve_index_token(market["spot_tradingsymbol"])
        if not token:
            return {"status": "error",
                    "message": f"Could not resolve {market['label']} index token — Zerodha connected?"}

        expiry = self._market_expiry_for(market["name"], cfg["expiry_type"], anchor)
        if not expiry:
            return {"status": "error", "message": "No option expiry resolvable for OI series."}

        scope = (cfg["market"], cfg["timeframe"], cfg["strike_interval"],
                 cfg["strike_count"], cfg["expiry_type"], anchor.isoformat())

        index_candles = self._get_candles(token, frm, to, interval, oi=False)
        if not index_candles:
            return {"status": "error",
                    "message": "No index candles for the selected window (market closed / holiday?)"}

        buckets = [b for b in self._build_buckets(index_candles, tf) if self._is_completed(b["end_dt"], tf)]
        if not buckets:
            return {"status": "error", "message": "No completed candles yet for this timeframe."}

        # ── Immutable-freeze guarantee ────────────────────────────────
        # A completed candle is computed exactly once and then frozen. If any
        # completed candle is not yet frozen (i.e. it just closed), refetch the
        # index — and, below, its OI — with ``force`` so the value we freeze is
        # from FINAL post-close data, never a stale pre-close read. Candles that
        # are already frozen are never refetched or recomputed.
        need_fresh = any((scope, b["end_dt"].isoformat()) not in self._row_cache for b in buckets)
        if need_fresh:
            index_candles = self._get_candles(token, frm, to, interval, oi=False, force=True)
            buckets = [b for b in self._build_buckets(index_candles, tf) if self._is_completed(b["end_dt"], tf)]

        # Pre-check the strike-union size so we never explode API usage.
        union: set[int] = set()
        for b in buckets:
            atm = calc.get_atm_strike(b["close"], int(cfg["strike_interval"]))
            union.update(calc.generate_selected_strikes(atm, int(cfg["strike_interval"]), int(cfg["strike_count"])))
        wide = len(union) > _MAX_STRIKES
        if wide:
            logger.warning("Strike union %d exceeds cap %d — OI limited to latest candles",
                           len(union), _MAX_STRIKES)

        oi_cache: dict = {}
        rows: list[dict] = []
        prev_vwap: Optional[float] = None
        prev_group = None
        t0 = time.monotonic()

        for b in buckets:
            # reset "previous VWAP" continuity at each new session (intraday)
            g = self._group_key(b["end_dt"], tf["intraday"])
            if g != prev_group:
                prev_vwap = None
                prev_group = g

            ckey = (scope, b["end_dt"].isoformat())
            row = self._row_cache.get(ckey)
            if row is None:
                # Newly-completed candle → compute once from fresh OI and FREEZE.
                row = self.generate_table_row(
                    b, cfg, market["name"], expiry, oi_cache, frm, to, interval,
                    prev_vwap, force_oi=need_fresh)
                self._row_cache[ckey] = row
            else:
                # Already frozen — reuse verbatim (previous_vwap is deterministic).
                row = {**row, "previous_vwap": prev_vwap}
            rows.append(row)
            prev_vwap = b["vwap"]

        exec_ms = int((time.monotonic() - t0) * 1000)
        last = rows[-1]
        # "Current" = the most recent completed candle's ATM + its selected strikes.
        latest_atm = last["atm"]
        latest_strikes = calc.generate_selected_strikes(
            latest_atm, int(cfg["strike_interval"]), int(cfg["strike_count"]))
        logger.info(
            "NiftySignalGenerator %s %s: %d rows | ATM=%s CallOI=%s PutOI=%s Diff=%s "
            "PCR=%s VWAP=%s PrevVWAP=%s LTP=%s Opt=%s VWAPsig=%s | %dms",
            cfg["market"], cfg["timeframe"], len(rows), last["atm"], last["call_oi"],
            last["put_oi"], last["diff"], last["pcr"], last["vwap"], last["previous_vwap"],
            last["price"], last["option_signal"], last["vwap_signal"], exec_ms,
        )

        return {
            "status": "ok",
            "market": cfg["market"],
            "market_label": market["label"],
            "timeframe": cfg["timeframe"],
            "timeframe_label": tf["label"],
            "strike_interval": cfg["strike_interval"],
            "strike_count": cfg["strike_count"],
            "total_strikes": int(cfg["strike_count"]) * 2 + 1,
            "expiry": expiry.isoformat(),
            "expiry_type": cfg["expiry_type"],
            "session_day": anchor.isoformat(),
            "atm": latest_atm,
            "strikes": latest_strikes,
            "oi_limited": wide,
            "fetched_at": datetime.now().strftime("%H:%M:%S"),
            "rows": rows,          # chronological (oldest → newest)
        }

    # ── public entry point ────────────────────────────────────────
    def snapshot(self, overrides: Optional[dict] = None, target_date: Optional[str] = None) -> dict:
        """Generate the full table for the current/selected session.

        Uses the persisted config, optionally overlaid with request ``overrides``
        (so the UI can switch timeframe/interval without saving). Completed
        candles are cached, so a live refresh only computes the newly-closed one.
        """
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            day = None
            if target_date:
                day = _parse_expiry(target_date)
                if day is None:
                    return {"status": "error", "message": "Invalid date (use YYYY-MM-DD)"}
            try:
                res = self.load_historical_rows(cfg, day)
            except Exception as exc:
                logger.error("NiftySignalGenerator snapshot failed: %s", exc)
                return {"status": "error", "message": str(exc)}
            res["config"] = cfg
            return res
