"""
Strategy 12 — 200 EMA Pull-Back (intraday NIFTY options).

Concept
-------
Continuously scans the NIFTY **index**. Two EMAs are computed on a
configurable index timeframe:

    • Fast EMA (default 20)   → trend filter
    • Slow EMA (default 200)  → pull-back entry zone

Entry (NO candle confirmation, immediate MARKET order):

    • Fast EMA > Slow EMA  AND  index LTP touches the Slow EMA  → BUY CALL
    • Fast EMA < Slow EMA  AND  index LTP touches the Slow EMA  → BUY PUT

Option selection (ATM / ITM / OTM offsets) resolves the strike from the
current NIFTY price; the nearest ≥ today expiry contract is traded.

Risk management is a **hidden** SL / Target engine — nothing but the entry
and the exit MARKET order is ever sent to the broker. Both levels are
derived from the **option's** ATR (not the index):

    • Hidden SL     = entry − ATR × sl_mult      (trails up, never down)
    • Hidden Target = entry + ATR × tgt_mult      (ATR mode)
                    = entry + target_points        (points mode, hardcoded)
                    = none — SL-only trailing      (none mode)

The SL / Target are re-computed on a configurable cadence (default every
1 minute) from a fresh option ATR. When the option LTP comes within
``exit_proximity`` (default 5) points of the hidden SL or hidden Target the
position is flattened with a MARKET order.

Market-order protection is handled by the broker layer (MARKET → buffered
LIMIT), so live deployment never fails on Zerodha's "market protection"
rejection.

State machine
-------------
    IDLE → ORDER_PLACED → POSITION_OPEN → COMPLETED → IDLE

Everything (framework, broker, hidden SL/TGT pattern, logging, order
history, RiskController, persistence) is reused from the existing
application — no existing strategy is modified.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime, timedelta
from enum import Enum
from typing import Optional

from config import settings
from core.broker import (
    Broker, OrderRequest,
    Exchange, OrderSide, OrderType, ProductType,
)
from core.logger import get_logger
from core.risk_controller import RiskController

logger = get_logger("strategy12.ema_pullback")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy12_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy12_trades.json"

INDEX_NAME = "NIFTY"
INDEX_SPOT_TRADINGSYMBOL = "NIFTY 50"
STRIKE_INTERVAL = 50

# Chart timeframe label → (kite interval, aggregation factor).
# Kite natively supports minute/3/5/10/15/30/60minute/day/week/month.
# 2h & 4h are aggregated up from 60-minute candles.
TIMEFRAMES: dict[str, tuple[str, int]] = {
    "1minute":  ("minute", 1),
    "3minute":  ("3minute", 1),
    "5minute":  ("5minute", 1),
    "10minute": ("10minute", 1),
    "15minute": ("15minute", 1),
    "30minute": ("30minute", 1),
    "1hour":    ("60minute", 1),
    "2hour":    ("60minute", 2),
    "4hour":    ("60minute", 4),
    "day":      ("day", 1),
    "week":     ("week", 1),
    "month":    ("month", 1),
}

# Timeframe → warm-up calendar days to fetch so the slow EMA converges.
_TF_WARMUP_DAYS = {
    "1minute": 5, "3minute": 8, "5minute": 12, "10minute": 20,
    "15minute": 30, "30minute": 45, "1hour": 70, "2hour": 120,
    "4hour": 200, "day": 500, "week": 2000, "month": 4000,
}

OPTION_MODES = ["ATM", "ITM_100", "ITM_200", "ITM_300", "OTM_100", "OTM_200", "OTM_300"]


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


# ── Pure indicator helpers ──────────────────────────────────

def ema_series(values: list[float], period: int) -> list[float]:
    """Exponential moving average. Returns a same-length list (seeded with
    the first value; NaN-free so it is JSON-safe)."""
    n = len(values)
    if n == 0 or period <= 0:
        return []
    out = [0.0] * n
    k = 2.0 / (period + 1.0)
    out[0] = float(values[0])
    for i in range(1, n):
        out[i] = float(values[i]) * k + out[i - 1] * (1.0 - k)
    return out


def atr_series(highs: list[float], lows: list[float], closes: list[float],
               period: int = 14) -> list[float]:
    """Wilder's Average True Range. Returns a same-length list."""
    n = len(closes)
    if n == 0:
        return []
    tr = [0.0] * n
    tr[0] = float(highs[0]) - float(lows[0])
    for i in range(1, n):
        h, l, pc = float(highs[i]), float(lows[i]), float(closes[i - 1])
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    out = [0.0] * n
    if n <= period:
        # Not enough bars for a full Wilder seed — use the running mean.
        run = 0.0
        for i in range(n):
            run += tr[i]
            out[i] = run / (i + 1)
        return out
    seed = sum(tr[1:period + 1]) / period
    out[period] = seed
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    # back-fill the warm-up region with the seed so callers never read 0
    for i in range(period):
        out[i] = seed
    return out


def _aggregate(candles: list[dict], factor: int) -> list[dict]:
    """Aggregate N base candles into one (used for 2h / 4h from 60-minute)."""
    if factor <= 1 or not candles:
        return candles
    out: list[dict] = []
    for i in range(0, len(candles), factor):
        chunk = candles[i:i + factor]
        if not chunk:
            continue
        out.append({
            "date": chunk[0].get("date"),
            "open": chunk[0].get("open"),
            "high": max(float(c.get("high", 0) or 0) for c in chunk),
            "low": min(float(c.get("low", 0) or 0) for c in chunk),
            "close": chunk[-1].get("close"),
            "volume": sum(float(c.get("volume", 0) or 0) for c in chunk),
        })
    return out


def _candle_time_str(ts) -> str:
    if hasattr(ts, "strftime"):
        return ts.strftime("%H:%M")
    return str(ts)[11:16] if ts else ""


def _candle_dt(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts
    if hasattr(ts, "isoformat"):
        try:
            return datetime.fromisoformat(ts.isoformat())
        except Exception:
            return None
    try:
        return datetime.fromisoformat(str(ts)[:19])
    except Exception:
        return None


class Strategy12EmaPullback:
    """200-EMA pull-back NIFTY-options strategy (live or paper)."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker
        self._check_lock = threading.Lock()

        # ── Config ──
        self.fast_ema = max(1, int(config.get("fast_ema", 20)))
        self.slow_ema = max(2, int(config.get("slow_ema", 200)))
        self.timeframe = str(config.get("timeframe", "1minute"))
        if self.timeframe not in TIMEFRAMES:
            self.timeframe = "1minute"
        self.option_selection = str(config.get("option_selection", "ITM_100")).upper()
        if self.option_selection not in OPTION_MODES:
            self.option_selection = "ITM_100"
        self.lot_size = int(config.get("lot_size", 65))
        self.lots = max(1, int(config.get("lots", 1)))
        self.atr_period = max(1, int(config.get("atr_period", 14)))
        self.sl_mult = float(config.get("sl_mult", 3))
        self.target_mode = str(config.get("target_mode", "atr")).lower()   # atr|points|none
        self.tgt_mult = float(config.get("tgt_mult", 9))
        self.target_points = float(config.get("target_points", 30))
        self.atr_update_minutes = max(1, int(config.get("atr_update_minutes", 1)))
        self.exit_proximity = float(config.get("exit_proximity", 5))
        self.touch_buffer = float(config.get("touch_buffer", 2))
        self.enable_reentry = bool(config.get("enable_reentry", False))
        self.strike_interval = int(config.get("strike_interval", STRIKE_INTERVAL))
        self.index_name = str(config.get("index_name", INDEX_NAME)).upper()
        self.start_time = self._parse_time(config.get("start_time", "09:20"), dtime(9, 20))
        self.end_time = self._parse_time(config.get("end_time", "15:10"), dtime(15, 10))
        self.max_trades_per_day = max(1, int(config.get("max_trades_per_day", 5)))
        self.max_daily_loss = float(config.get("max_daily_loss", 0) or 0)  # 0 = disabled

        # ── State ──
        self.is_active: bool = False
        self.state: State = State.IDLE
        self.scenario: str = "—"
        self.signal: str = "NO_TRADE"          # BUY_CALL / BUY_PUT / NO_TRADE
        self._trading_date: Optional[date] = None
        self._armed: bool = True               # ready to detect a fresh touch
        self.last_check_at: Optional[datetime] = None

        # ── Live index / indicator snapshot ──
        self.spot_price: float = 0.0
        self._prev_spot: float = 0.0
        self.ema_fast_val: float = 0.0
        self.ema_slow_val: float = 0.0
        self._ema_series_cache: list[dict] = []
        self._ema_fetch_at: Optional[datetime] = None
        self._index_token: Optional[int] = None

        # ── Active trade ──
        self.signal_type: Optional[str] = None    # CE / PE
        self.entry_reason: str = ""
        self.atm_strike: int = 0
        self.strike: int = 0
        self.option_symbol: str = ""
        self.option_token: int = 0
        self.option_expiry: str = ""
        self.fill_price: float = 0.0
        self.current_ltp: float = 0.0
        self.entry_atr: float = 0.0
        self.sl_price: float = 0.0
        self.target_price: float = 0.0            # 0 → no target (none mode)
        self._last_atr_update: Optional[datetime] = None

        # ── Orders (broker sees entry + exit only) ──
        self.entry_order: Optional[dict] = None
        self._exiting: bool = False               # duplicate-exit guard

        # ── Bookkeeping ──
        self._trades_today: int = 0
        self._realized_today: float = 0.0
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None
        self.trade_log: list[dict] = []
        self.markers: list[dict] = []             # chart entry/exit arrows

        # ── Risk / re-entry controller ──
        self.risk = RiskController()
        self._sync_risk_reentry()

    # ── Derived ─────────────────────────────────────────

    @property
    def quantity(self) -> int:
        return max(0, int(self.lots) * int(self.lot_size))

    @staticmethod
    def _parse_time(val, default: dtime) -> dtime:
        try:
            if isinstance(val, dtime):
                return val
            hh, mm = str(val).split(":")[:2]
            return dtime(int(hh), int(mm))
        except Exception:
            return default

    def _sync_risk_reentry(self):
        """Map the single `enable_reentry` checkbox onto the RiskController."""
        self.risk.update_config(
            allow_reentry_after_target=self.enable_reentry,
            allow_reentry_after_sl=self.enable_reentry,
            require_manual_confirmation_after_sl=False,
            auto_pause_after_sl=False,
            require_fresh_crossover=False,
            entry_cooldown_seconds=0,
            max_reentries_per_day=self.max_trades_per_day,
            max_sl_hits_per_day=self.max_trades_per_day,
            max_consecutive_losses=self.max_trades_per_day,
        )

    # ── Public controls ─────────────────────────────────

    def start(self, config: dict):
        self.apply_config(config, save=False)
        self.is_active = True
        self._check_day_reset()
        self._save_state()
        logger.info(
            "Strategy 12 started: EMA %d/%d @ %s | option=%s | SL=ATR×%.2f | "
            "target=%s | reentry=%s",
            self.fast_ema, self.slow_ema, self.timeframe, self.option_selection,
            self.sl_mult, self._target_desc(), self.enable_reentry,
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 12 stopped")

    def _target_desc(self) -> str:
        if self.target_mode == "points":
            return f"{self.target_points:.0f}pts"
        if self.target_mode == "none":
            return "NONE (trail SL)"
        return f"ATR×{self.tgt_mult:.2f}"

    def apply_config(self, config: dict, save: bool = True) -> None:
        for k in ("fast_ema", "slow_ema", "lot_size", "lots", "atr_period",
                  "strike_interval", "max_trades_per_day", "atr_update_minutes"):
            if k in config and config.get(k) not in (None, ""):
                setattr(self, k, int(config[k]))
        for k in ("sl_mult", "tgt_mult", "target_points", "exit_proximity",
                  "touch_buffer", "max_daily_loss"):
            if k in config and config.get(k) not in (None, ""):
                setattr(self, k, float(config[k]))
        if "timeframe" in config and str(config["timeframe"]) in TIMEFRAMES:
            new_tf = str(config["timeframe"])
            if new_tf != self.timeframe:
                self.timeframe = new_tf
                self._ema_series_cache = []       # force EMA recompute on TF change
                self._ema_fetch_at = None
        if "option_selection" in config and str(config["option_selection"]).upper() in OPTION_MODES:
            self.option_selection = str(config["option_selection"]).upper()
        if "target_mode" in config and config["target_mode"]:
            self.target_mode = str(config["target_mode"]).lower()
        if "index_name" in config and config["index_name"]:
            self.index_name = str(config["index_name"]).upper()
        if "start_time" in config:
            self.start_time = self._parse_time(config["start_time"], self.start_time)
        if "end_time" in config:
            self.end_time = self._parse_time(config["end_time"], self.end_time)
        if "enable_reentry" in config:
            self.enable_reentry = bool(config["enable_reentry"])

        self.lots = max(1, int(self.lots))
        self.slow_ema = max(2, int(self.slow_ema))
        self.fast_ema = max(1, int(self.fast_ema))
        self._sync_risk_reentry()

        # Recompute hidden levels immediately for an open position.
        if self.state == State.POSITION_OPEN and self.fill_price > 0 and self.entry_atr > 0:
            self._recompute_levels(self.current_ltp or self.fill_price, self.entry_atr, initial=False)

        if save:
            self._save_state()

    # ── Daily reset ─────────────────────────────────────

    def _check_day_reset(self):
        today = date.today()
        if self._trading_date == today:
            return
        old_date = self._trading_date
        self._trading_date = today
        if self.state in (State.POSITION_OPEN, State.ORDER_PLACED) and self.fill_price > 0:
            logger.warning("S12 orphaned %s from %s — recording BROKER_SQUAREOFF",
                           self.state.value, old_date)
            self._record_trade("BROKER_SQUAREOFF", self.current_ltp or self.fill_price,
                               closed_date=old_date or today, exit_time="15:29")
        # Reset intraday state (config is preserved)
        self.state = State.IDLE
        self.scenario = "—"
        self.signal = "NO_TRADE"
        self._armed = True
        self._reset_trade_fields()
        self._trades_today = 0
        self._realized_today = 0.0
        self._instruments_cache = None
        self.markers = []
        self._ema_series_cache = []
        self._ema_fetch_at = None
        self.risk.reset_for_new_day()
        self._save_state()

    def _reset_trade_fields(self):
        self.signal_type = None
        self.entry_reason = ""
        self.atm_strike = 0
        self.strike = 0
        self.option_symbol = ""
        self.option_token = 0
        self.option_expiry = ""
        self.fill_price = 0.0
        self.current_ltp = 0.0
        self.entry_atr = 0.0
        self.sl_price = 0.0
        self.target_price = 0.0
        self.entry_order = None
        self._exiting = False
        self._last_atr_update = None

    # ── Instruments / strike resolution ─────────────────

    def _nfo_instruments(self) -> list[dict]:
        today = date.today()
        if self._instruments_cache and self._instruments_date == today:
            return self._instruments_cache
        self._instruments_cache = self.broker.get_instruments("NFO")
        self._instruments_date = today
        return self._instruments_cache

    def _resolve_index_token(self) -> Optional[int]:
        if self._index_token:
            return self._index_token
        try:
            for inst in self.broker.get_instruments("NSE"):
                if inst.get("tradingsymbol") == INDEX_SPOT_TRADINGSYMBOL:
                    self._index_token = int(inst["instrument_token"])
                    return self._index_token
        except Exception as exc:
            logger.error("S12 index token lookup failed: %s", exc)
        return None

    def _select_strike(self, spot: float, opt_type: str) -> int:
        """Resolve the target strike from the option-selection mode."""
        atm = int(round(spot / self.strike_interval) * self.strike_interval)
        mode = self.option_selection
        if mode == "ATM":
            return atm
        try:
            kind, depth_s = mode.split("_")
            depth = int(depth_s)
        except Exception:
            return atm
        if kind == "ITM":
            return atm - depth if opt_type == "CE" else atm + depth
        # OTM
        return atm + depth if opt_type == "CE" else atm - depth

    def _find_option(self, strike: int, opt_type: str,
                     on_or_after: Optional[date] = None) -> Optional[dict]:
        """Nearest-expiry (≥ on_or_after) option for the given strike/type."""
        ref = on_or_after or date.today()
        candidates = []
        for inst in self._nfo_instruments():
            if (inst.get("name") == self.index_name
                    and inst.get("instrument_type") == opt_type
                    and float(inst.get("strike", 0) or 0) == float(strike)):
                expiry = inst.get("expiry")
                if isinstance(expiry, str):
                    try:
                        expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                if expiry and expiry >= ref:
                    candidates.append((expiry, inst))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    # ── Index EMAs ──────────────────────────────────────

    def _index_candles(self, to_dt: Optional[datetime] = None,
                       warmup_days: Optional[int] = None) -> list[dict]:
        token = self._resolve_index_token()
        if not token:
            return []
        kite_interval, factor = TIMEFRAMES[self.timeframe]
        days = warmup_days if warmup_days is not None else _TF_WARMUP_DAYS.get(self.timeframe, 5)
        to = to_dt or datetime.now()
        frm = datetime.combine((to - timedelta(days=days)).date(), MARKET_OPEN)
        try:
            rows = self.broker.get_historical_data(token, frm, to, kite_interval) or []
        except Exception as exc:
            logger.debug("S12 index candles fetch failed: %s", exc)
            return []
        return _aggregate(rows, factor)

    def _refresh_emas(self, force: bool = False):
        """Recompute the fast/slow EMA series from index history (throttled)."""
        now = datetime.now()
        if (not force and self._ema_fetch_at
                and (now - self._ema_fetch_at).total_seconds() < 20
                and self._ema_series_cache):
            return
        candles = self._index_candles()
        if not candles:
            return
        closes = [float(c.get("close", 0) or 0) for c in candles]
        ef = ema_series(closes, self.fast_ema)
        es = ema_series(closes, self.slow_ema)
        series = []
        for i, c in enumerate(candles):
            series.append({
                "t": _candle_time_str(c.get("date")),
                "dt": _candle_dt(c.get("date")),
                "c": closes[i],
                "ema_fast": round(ef[i], 2),
                "ema_slow": round(es[i], 2),
            })
        # Keep only today's candles for the live chart (warm-up is off-screen).
        today = date.today()
        todays = [s for s in series if s["dt"] and s["dt"].date() == today] or series[-200:]
        self._ema_series_cache = todays
        self._ema_fetch_at = now
        if ef:
            self.ema_fast_val = round(ef[-1], 2)
            self.ema_slow_val = round(es[-1], 2)

    def refresh_spot(self) -> float:
        try:
            ltp = self.broker.get_ltp([f"NSE:{INDEX_SPOT_TRADINGSYMBOL}"]) or {}
            v = float(ltp.get(f"NSE:{INDEX_SPOT_TRADINGSYMBOL}", 0) or 0)
            if v > 0:
                if self.spot_price > 0:
                    self._prev_spot = self.spot_price
                self.spot_price = v
        except Exception as exc:
            logger.debug("S12 spot fetch failed: %s", exc)
        return self.spot_price

    def index_series(self) -> list[dict]:
        """Today's index candles with EMA overlays (for the chart)."""
        self._refresh_emas()
        out = []
        for s in self._ema_series_cache:
            out.append({"t": s["t"], "c": s["c"],
                        "ema_fast": s["ema_fast"], "ema_slow": s["ema_slow"]})
        return out

    # ── Option ATR ──────────────────────────────────────

    def _option_atr(self, token: int, to_dt: Optional[datetime] = None) -> float:
        if not token:
            return 0.0
        kite_interval, factor = TIMEFRAMES[self.timeframe]
        to = to_dt or datetime.now()
        # enough bars for the ATR period + a little slack
        days = _TF_WARMUP_DAYS.get(self.timeframe, 5)
        frm = datetime.combine((to - timedelta(days=days)).date(), MARKET_OPEN)
        try:
            rows = self.broker.get_historical_data(token, frm, to, kite_interval) or []
        except Exception as exc:
            logger.debug("S12 option ATR fetch failed: %s", exc)
            return 0.0
        rows = _aggregate(rows, factor)
        if not rows:
            return 0.0
        highs = [float(r.get("high", 0) or 0) for r in rows]
        lows = [float(r.get("low", 0) or 0) for r in rows]
        closes = [float(r.get("close", 0) or 0) for r in rows]
        a = atr_series(highs, lows, closes, self.atr_period)
        return round(a[-1], 2) if a else 0.0

    # ── Main check ──────────────────────────────────────

    def check(self, *_args, **_kwargs) -> dict:
        # Keep managing an open position even after manual stop.
        if not self.is_active and self.state != State.POSITION_OPEN:
            return self.get_status()
        if not self._check_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self.last_check_at = datetime.now()
            self._check_day_reset()

            self.refresh_spot()
            self._refresh_emas()

            # 15:15 hard square-off of any open position
            if self.state == State.POSITION_OPEN and datetime.now().time() >= PRE_CLOSE_EXIT:
                self._exit_position("AUTO_SQUAREOFF", self.current_ltp or self.fill_price)
                self._prev_spot = self.spot_price
                return self.get_status()

            if self.state == State.IDLE:
                self._scan_for_touch()
            elif self.state == State.ORDER_PLACED:
                self._check_entry_fill()
            elif self.state == State.POSITION_OPEN:
                self._manage_position()

            if self.spot_price > 0:
                self._prev_spot = self.spot_price
            return self.get_status()
        finally:
            self._check_lock.release()

    # ── Entry scan ──────────────────────────────────────

    def _within_trading_window(self) -> bool:
        now = datetime.now().time()
        return self.start_time <= now <= self.end_time

    def _touched_slow_ema(self) -> bool:
        """True when the index LTP touches the slow EMA (crossing or within
        the touch buffer)."""
        ema = self.ema_slow_val
        cur = self.spot_price
        prev = self._prev_spot or cur
        if ema <= 0 or cur <= 0:
            return False
        if abs(cur - ema) <= self.touch_buffer:
            return True
        # crossing between the previous and current LTP
        lo, hi = (prev, cur) if prev <= cur else (cur, prev)
        return lo <= ema <= hi

    def _scan_for_touch(self):
        if self.ema_fast_val <= 0 or self.ema_slow_val <= 0 or self.spot_price <= 0:
            self.scenario = "Waiting for index EMAs / spot"
            return
        if not self._within_trading_window():
            self.scenario = f"Outside window {self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')}"
            return
        if self._trades_today >= self.max_trades_per_day:
            self.scenario = "Max trades reached"
            return
        if self.max_daily_loss > 0 and self._realized_today <= -abs(self.max_daily_loss):
            self.scenario = f"Max daily loss ₹{self.max_daily_loss:.0f} hit"
            return
        if not self.enable_reentry and self._trades_today >= 1:
            self.scenario = "Re-entry disabled — one trade/day"
            return

        # Re-arm once price has left the EMA zone.
        gap = abs(self.spot_price - self.ema_slow_val)
        if not self._armed:
            if gap > max(self.touch_buffer * 2, self.touch_buffer + 1):
                self._armed = True
            else:
                self.scenario = f"Disarmed — price {gap:.1f} pts from 200 EMA"
                return

        trend_up = self.ema_fast_val > self.ema_slow_val
        if not self._touched_slow_ema():
            self.scenario = (
                f"Armed | spot {self.spot_price:.1f} · EMA{self.fast_ema} "
                f"{self.ema_fast_val:.1f} {'>' if trend_up else '<'} EMA{self.slow_ema} "
                f"{self.ema_slow_val:.1f}"
            )
            self.signal = "NO_TRADE"
            return

        opt_type = "CE" if trend_up else "PE"
        side = "CALL" if trend_up else "PUT"
        ok, reason = self.risk.allow_entry(side=side, current_price=self.spot_price,
                                           line_price=self.ema_slow_val)
        if not ok:
            self.scenario = f"{side} touch blocked — {reason}"
            self.signal = "NO_TRADE"
            self._armed = False
            return

        self.signal = "BUY_CALL" if trend_up else "BUY_PUT"
        self.entry_reason = (
            f"Spot {self.spot_price:.1f} touched 200 EMA {self.ema_slow_val:.1f} | "
            f"EMA{self.fast_ema} {self.ema_fast_val:.1f} "
            f"{'>' if trend_up else '<'} EMA{self.slow_ema} {self.ema_slow_val:.1f}"
        )
        self.scenario = f"{side} entry — 200 EMA pull-back"
        self._fire_entry(opt_type)

    # ── Entry ───────────────────────────────────────────

    def _fire_entry(self, opt_type: str):
        spot = self.spot_price
        strike = self._select_strike(spot, opt_type)
        opt = self._find_option(strike, opt_type)
        if not opt:
            logger.warning("S12 no %s option for strike %s — skip", opt_type, strike)
            self.scenario = f"No {opt_type} {strike} contract listed"
            self._armed = False
            return
        self.signal_type = opt_type
        self.atm_strike = int(round(spot / self.strike_interval) * self.strike_interval)
        self.strike = int(strike)
        self.option_symbol = opt["tradingsymbol"]
        self.option_token = int(opt.get("instrument_token") or 0)
        exp = opt.get("expiry")
        self.option_expiry = exp.isoformat() if hasattr(exp, "isoformat") else str(exp or "")
        if opt.get("lot_size"):
            self.lot_size = int(opt["lot_size"])
        self._armed = False
        self._place_entry_order()

    def _place_entry_order(self):
        prev_state = self.state
        self.state = State.ORDER_PLACED
        # Immediate MARKET order — broker layer converts to a protected LIMIT.
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"]) or {}
            ref_ltp = float(ltp_map.get(f"NFO:{self.option_symbol}", 0) or 0)
        except Exception:
            ref_ltp = 0.0
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                side=OrderSide.BUY, quantity=self.quantity,
                order_type=OrderType.MARKET, product=ProductType.MIS,
                tag="S12ENTRY",
            )
            resp = self.broker.place_order(req)
            self.entry_order = {
                "order_id": resp.order_id, "status": resp.status,
                "is_paper": resp.is_paper, "price": ref_ltp,
                "timestamp": datetime.now().isoformat(),
            }
            if resp.is_paper:
                self.fill_price = ref_ltp
                self.entry_order["status"] = "COMPLETE"
                self._on_entry_filled()
            else:
                self._save_state()
                logger.info("S12 entry order placed: %s (%s)", resp.order_id, self.option_symbol)
        except Exception as exc:
            logger.error("S12 entry order failed: %s", exc)
            self.state = prev_state
            self.entry_order = None
            self.scenario = f"Entry failed: {exc}"
            self._save_state()

    def _check_entry_fill(self):
        if not self.entry_order:
            self.state = State.IDLE
            return
        if self.entry_order.get("is_paper"):
            return
        placed_at = self.entry_order.get("timestamp")
        if placed_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(placed_at)).total_seconds()
            except Exception:
                elapsed = 0
            if elapsed > 60 and self.entry_order.get("status") != "COMPLETE":
                logger.info("S12 entry stale (%.0fs) — cancelling", elapsed)
                self._cancel_order(self.entry_order.get("order_id"))
                self.state = State.IDLE
                self.scenario = "Entry cancelled — stale order"
                self._reset_trade_fields()
                self._save_state()
                return
        try:
            for o in self.broker.get_orders():
                if str(o.get("order_id")) != str(self.entry_order["order_id"]):
                    continue
                status = o.get("status", "")
                if status == "COMPLETE":
                    self.fill_price = float(o.get("average_price", self.entry_order.get("price") or 0))
                    self.entry_order["status"] = "COMPLETE"
                    self._on_entry_filled()
                elif status in ("CANCELLED", "REJECTED"):
                    logger.warning("S12 entry %s — re-arm", status)
                    self.state = State.IDLE
                    self.scenario = f"Entry {status} — re-arming"
                    self._reset_trade_fields()
                    self._save_state()
                break
        except Exception as exc:
            logger.error("S12 fill check failed: %s", exc)

    def _on_entry_filled(self):
        if self.fill_price <= 0:
            self.fill_price = self.entry_order.get("price") or 0.05
        atr = self._option_atr(self.option_token)
        if atr <= 0:
            atr = max(1.0, self.fill_price * 0.1)   # defensive fallback
        self.entry_atr = atr
        self._recompute_levels(self.fill_price, atr, initial=True)
        self.current_ltp = self.fill_price
        self.state = State.POSITION_OPEN
        self._last_atr_update = datetime.now()
        self.risk.record_entry(side="CALL" if self.signal_type == "CE" else "PUT")
        self.markers.append({
            "type": "ENTRY", "side": self.signal_type,
            "time": datetime.now().strftime("%H:%M:%S"),
            "spot": round(self.spot_price, 2), "price": round(self.fill_price, 2),
            "strike": self.strike, "symbol": self.option_symbol,
        })
        self._save_state()
        logger.info(
            "S12 ENTRY %s %s @%.2f | ATR %.2f | SL %.2f | TGT %s",
            self.signal_type, self.option_symbol, self.fill_price, atr,
            self.sl_price, f"{self.target_price:.2f}" if self.target_price else "—",
        )

    def _recompute_levels(self, ref_ltp: float, atr: float, initial: bool):
        """(Re)compute the hidden SL & Target. SL trails up, never down."""
        ref = ref_ltp if ref_ltp > 0 else self.fill_price
        new_sl = max(0.05, ref - atr * self.sl_mult) if initial else \
            max(self.sl_price, ref - atr * self.sl_mult)
        # never let a trailing SL exceed the live LTP
        if not initial and ref > 0:
            new_sl = min(new_sl, ref - 0.05)
        self.sl_price = round(max(0.05, new_sl), 2)

        if self.target_mode == "points":
            self.target_price = round(self.fill_price + self.target_points, 2)
        elif self.target_mode == "none":
            self.target_price = 0.0
        else:  # atr
            self.target_price = round(self.fill_price + atr * self.tgt_mult, 2)

    # ── Position management ─────────────────────────────

    def _manage_position(self):
        if not self.option_symbol or self.fill_price <= 0:
            return
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"]) or {}
            ltp = float(ltp_map.get(f"NFO:{self.option_symbol}", 0) or 0)
        except Exception as exc:
            logger.debug("S12 LTP refresh failed: %s", exc)
            ltp = self.current_ltp
        if ltp > 0:
            self.current_ltp = ltp
        ltp = self.current_ltp
        if ltp <= 0:
            return

        # Periodic ATR / SL / Target recompute
        now = datetime.now()
        if (self._last_atr_update is None
                or (now - self._last_atr_update).total_seconds() >= self.atr_update_minutes * 60):
            atr = self._option_atr(self.option_token, now)
            if atr > 0:
                self.entry_atr = atr
            self._recompute_levels(ltp, self.entry_atr, initial=False)
            self._last_atr_update = now
            self._save_state()

        # Hidden exits (proximity-triggered MARKET exit) — SL first.
        if self.sl_price > 0 and ltp <= self.sl_price + self.exit_proximity:
            self._exit_position("SL_HIT", ltp)
            return
        if self.target_price > 0 and ltp >= self.target_price - self.exit_proximity:
            self._exit_position("TARGET_HIT", ltp)
            return

    def _exit_position(self, exit_type: str, exit_price: float):
        if self._exiting:
            return
        self._exiting = True
        try:
            if self.option_symbol and self.quantity > 0:
                try:
                    req = OrderRequest(
                        tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                        side=OrderSide.SELL, quantity=self.quantity,
                        order_type=OrderType.MARKET, product=ProductType.MIS,
                        tag=f"S12{'SL' if exit_type == 'SL_HIT' else 'TGT' if exit_type == 'TARGET_HIT' else 'SQ'}",
                    )
                    self.broker.place_order(req)
                except Exception as exc:
                    logger.error("S12 exit order failed: %s", exc)

            price = float(exit_price or self.current_ltp or self.fill_price)
            self._record_trade(exit_type, price)
            self.markers.append({
                "type": "EXIT", "side": self.signal_type, "exit_type": exit_type,
                "time": datetime.now().strftime("%H:%M:%S"),
                "spot": round(self.spot_price, 2), "price": round(price, 2),
                "strike": self.strike, "symbol": self.option_symbol,
            })
            self.state = State.COMPLETED
            self.scenario = f"Last: {exit_type}"
            self._save_state()
            logger.info("S12 EXIT %s %s @%.2f", exit_type, self.option_symbol, price)

            # re-arm for the next touch if re-entry allowed & caps not hit
            self._reset_trade_fields()
            if (self.is_active and self.enable_reentry
                    and self._trades_today < self.max_trades_per_day
                    and not (self.max_daily_loss > 0 and self._realized_today <= -abs(self.max_daily_loss))):
                self.state = State.IDLE
                self._armed = False   # require a fresh touch (leave-then-touch)
            else:
                self.state = State.IDLE if self.is_active else State.COMPLETED
                self._armed = False
            self._save_state()
        finally:
            self._exiting = False

    def _record_trade(self, exit_type: str, exit_price: float,
                      closed_date: Optional[date] = None, exit_time: Optional[str] = None):
        pnl = round((float(exit_price) - self.fill_price) * self.quantity, 2)
        self._trades_today += 1
        self._realized_today += pnl
        try:
            self.risk.record_exit(
                exit_type=exit_type,
                side="CALL" if self.signal_type == "CE" else "PUT",
                line_price=float(self.ema_slow_val or 0), pnl=pnl,
            )
        except Exception as exc:
            logger.warning("S12 risk.record_exit failed: %s", exc)
        trade = {
            "date": (closed_date or self._trading_date or date.today()).isoformat(),
            "signal": self.signal_type,
            "direction": "CALL" if self.signal_type == "CE" else "PUT",
            "strike": self.strike,
            "option": self.option_symbol,
            "expiry": self.option_expiry,
            "entry_price": round(self.fill_price, 2),
            "exit_price": round(float(exit_price), 2),
            "exit_type": exit_type,
            "entry_atr": round(self.entry_atr, 2),
            "sl_price": round(self.sl_price, 2),
            "target_price": round(self.target_price, 2),
            "lot_size": self.lot_size, "lots": self.lots, "qty": self.quantity,
            "exit_time": exit_time or datetime.now().strftime("%H:%M:%S"),
            "pnl": pnl,
            "timestamp": datetime.now().isoformat(),
        }
        self.trade_log.append(trade)
        self._append_trade_history(trade)

    def _cancel_order(self, order_id):
        if not order_id or str(order_id).startswith("PAPER"):
            return
        try:
            self.broker.cancel_order(order_id)
        except Exception as exc:
            logger.debug("S12 cancel order failed: %s", exc)

    # ── Persistence ─────────────────────────────────────

    def _config_dict(self) -> dict:
        return {
            "fast_ema": self.fast_ema, "slow_ema": self.slow_ema,
            "timeframe": self.timeframe, "option_selection": self.option_selection,
            "lot_size": self.lot_size, "lots": self.lots, "quantity": self.quantity,
            "atr_period": self.atr_period, "sl_mult": self.sl_mult,
            "target_mode": self.target_mode, "tgt_mult": self.tgt_mult,
            "target_points": self.target_points,
            "atr_update_minutes": self.atr_update_minutes,
            "exit_proximity": self.exit_proximity, "touch_buffer": self.touch_buffer,
            "enable_reentry": self.enable_reentry, "strike_interval": self.strike_interval,
            "index_name": self.index_name,
            "start_time": self.start_time.strftime("%H:%M"),
            "end_time": self.end_time.strftime("%H:%M"),
            "max_trades_per_day": self.max_trades_per_day,
            "max_daily_loss": self.max_daily_loss,
        }

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            STATE_FILE.write_text(json.dumps({
                "is_active": self.is_active,
                "state": self.state.value,
                "scenario": self.scenario,
                "signal": self.signal,
                "trading_date": self._trading_date.isoformat() if self._trading_date else None,
                "armed": self._armed,
                "spot_price": self.spot_price,
                "ema_fast_val": self.ema_fast_val, "ema_slow_val": self.ema_slow_val,
                "signal_type": self.signal_type, "entry_reason": self.entry_reason,
                "atm_strike": self.atm_strike, "strike": self.strike,
                "option_symbol": self.option_symbol, "option_token": self.option_token,
                "option_expiry": self.option_expiry,
                "fill_price": self.fill_price, "current_ltp": self.current_ltp,
                "entry_atr": self.entry_atr, "sl_price": self.sl_price,
                "target_price": self.target_price,
                "last_atr_update": self._last_atr_update.isoformat() if self._last_atr_update else None,
                "entry_order": self.entry_order,
                "trades_today": self._trades_today, "realized_today": self._realized_today,
                "trade_log": self.trade_log[-50:], "markers": self.markers[-50:],
                "config": self._config_dict(),
                "risk": self.risk.serialize(),
            }, indent=2, default=str))
        except Exception as exc:
            logger.error("S12 state save failed: %s", exc)

    def restore_state(self) -> bool:
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
            cfg = data.get("config") or {}
            if cfg:
                self.apply_config(cfg, save=False)
            self.is_active = bool(data.get("is_active"))
            self.state = State(data.get("state", "IDLE"))
            self.scenario = str(data.get("scenario", "—"))
            self.signal = str(data.get("signal", "NO_TRADE"))
            td = data.get("trading_date")
            self._trading_date = date.fromisoformat(td) if td else None
            self._armed = bool(data.get("armed", True))
            self.spot_price = float(data.get("spot_price") or 0)
            self.ema_fast_val = float(data.get("ema_fast_val") or 0)
            self.ema_slow_val = float(data.get("ema_slow_val") or 0)
            self.signal_type = data.get("signal_type")
            self.entry_reason = str(data.get("entry_reason") or "")
            self.atm_strike = int(data.get("atm_strike") or 0)
            self.strike = int(data.get("strike") or 0)
            self.option_symbol = str(data.get("option_symbol") or "")
            self.option_token = int(data.get("option_token") or 0)
            self.option_expiry = str(data.get("option_expiry") or "")
            self.fill_price = float(data.get("fill_price") or 0)
            self.current_ltp = float(data.get("current_ltp") or 0)
            self.entry_atr = float(data.get("entry_atr") or 0)
            self.sl_price = float(data.get("sl_price") or 0)
            self.target_price = float(data.get("target_price") or 0)
            lau = data.get("last_atr_update")
            self._last_atr_update = datetime.fromisoformat(lau) if lau else None
            self.entry_order = data.get("entry_order")
            self._trades_today = int(data.get("trades_today") or 0)
            self._realized_today = float(data.get("realized_today") or 0)
            self.trade_log = list(data.get("trade_log") or [])
            self.markers = list(data.get("markers") or [])
            self.risk.restore(data.get("risk") or {})
            self._sync_risk_reentry()
            return True
        except Exception as exc:
            logger.warning("S12 state restore failed: %s", exc)
            return False

    def _append_trade_history(self, trade: dict):
        TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if TRADE_HISTORY_FILE.exists():
            try:
                existing = json.loads(TRADE_HISTORY_FILE.read_text())
            except Exception:
                existing = []
        existing.append(trade)
        try:
            TRADE_HISTORY_FILE.write_text(json.dumps(existing, indent=2, default=str))
        except Exception as exc:
            logger.error("S12 trade history append failed: %s", exc)

    # ── Status payload ──────────────────────────────────

    def get_status(self) -> dict:
        try:
            self._check_day_reset()
        except Exception:
            pass
        unrealized = 0.0
        if self.state == State.POSITION_OPEN and self.current_ltp > 0 and self.fill_price > 0:
            unrealized = round((self.current_ltp - self.fill_price) * self.quantity, 2)
        return {
            "strategy": "strategy12_ema_pullback",
            "is_active": self.is_active,
            "state": self.state.value,
            "scenario": self.scenario,
            "signal": self.signal,
            "armed": self._armed,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "index": {
                "spot": round(self.spot_price, 2),
                "ema_fast": self.ema_fast_val, "ema_slow": self.ema_slow_val,
                "trend": "UP" if self.ema_fast_val > self.ema_slow_val else "DOWN",
            },
            "trade": {
                "signal_type": self.signal_type,
                "entry_reason": self.entry_reason,
                "strike": self.strike, "atm_strike": self.atm_strike,
                "option_symbol": self.option_symbol,
                "option_expiry": self.option_expiry,
                "fill_price": round(self.fill_price, 2),
                "current_ltp": round(self.current_ltp, 2),
                "entry_atr": round(self.entry_atr, 2),
                "sl_price": round(self.sl_price, 2),
                "target_price": round(self.target_price, 2),
                "unrealized_pnl": unrealized,
            },
            "orders": {"entry": self.entry_order},
            "config": self._config_dict(),
            "trades_today": self._trades_today,
            "realized_today": round(self._realized_today, 2),
            "trade_log": self.trade_log[-20:],
            "markers": self.markers[-40:],
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "risk": self.risk.status_payload(),
        }

    # ── Backtest ────────────────────────────────────────

    def backtest(self, trade_date: date) -> dict:
        """Replay a single session with the identical live decision logic."""
        return _run_ema_backtest(self, [trade_date])

    def backtest_multi(self, days: int) -> dict:
        """Replay the last N calendar trading days."""
        days = max(1, min(int(days), 365))
        today = date.today()
        sessions: list[date] = []
        d = today
        # collect the last `days` weekdays (skip weekends; holidays yield no data)
        while len(sessions) < days:
            if d.weekday() < 5:
                sessions.append(d)
            d -= timedelta(days=1)
            if (today - d).days > days + 400:
                break
        sessions.reverse()
        return _run_ema_backtest(self, sessions)


# ───────────────────────── Backtest engine ─────────────────────────
# Self-contained EMA-pullback replay. Uses the SAME entry / hidden-SL /
# hidden-target / proximity-exit rules as the live strategy so the report
# reflects real behaviour (no simplified shortcuts).

def _run_ema_backtest(strat: "Strategy12EmaPullback", sessions: list[date]) -> dict:
    broker = strat.broker
    kite_interval, factor = TIMEFRAMES[strat.timeframe]
    warmup_days = _TF_WARMUP_DAYS.get(strat.timeframe, 5)

    token = strat._resolve_index_token()
    if not token:
        return {"status": "error", "message": "NIFTY index token not resolvable",
                "trades": [], "equity_curve": [], "stats": {}}

    all_trades: list[dict] = []
    equity_curve: list[dict] = []
    cum_pnl = 0.0
    running_eq = 0.0
    opt_cache: dict[int, list[dict]] = {}
    trade_no = 0

    for session in sessions:
        # index candles with warm-up
        frm = datetime.combine(session - timedelta(days=warmup_days), MARKET_OPEN)
        to = datetime.combine(session, MARKET_CLOSE)
        try:
            rows = broker.get_historical_data(token, frm, to, kite_interval) or []
        except Exception as exc:
            logger.debug("S12 backtest index fetch %s failed: %s", session, exc)
            continue
        rows = _aggregate(rows, factor)
        if not rows:
            continue
        closes = [float(r.get("close", 0) or 0) for r in rows]
        ef = ema_series(closes, strat.fast_ema)
        es = ema_series(closes, strat.slow_ema)

        # session-only candle indices
        sess_idx = [i for i, r in enumerate(rows)
                    if (_candle_dt(r.get("date")) or datetime.min).date() == session]
        if not sess_idx:
            continue

        armed = True
        prev_close = None
        trades_today = 0
        realized_today = 0.0

        for i in sess_idx:
            r = rows[i]
            cdt = _candle_dt(r.get("date"))
            if not cdt:
                continue
            ctime = cdt.time()
            price = closes[i]
            ema_fast = ef[i]
            ema_slow = es[i]
            prev_close = closes[i - 1] if i > 0 else price

            if not (strat.start_time <= ctime <= strat.end_time):
                continue
            if trades_today >= strat.max_trades_per_day:
                break
            if not strat.enable_reentry and trades_today >= 1:
                break
            if strat.max_daily_loss > 0 and realized_today <= -abs(strat.max_daily_loss):
                break

            gap = abs(price - ema_slow)
            if not armed:
                if gap > max(strat.touch_buffer * 2, strat.touch_buffer + 1):
                    armed = True
                else:
                    continue

            # touch detection (buffer or cross vs previous close)
            lo, hi = (prev_close, price) if prev_close <= price else (price, prev_close)
            touched = abs(price - ema_slow) <= strat.touch_buffer or (lo <= ema_slow <= hi)
            if not touched:
                continue

            trend_up = ema_fast > ema_slow
            opt_type = "CE" if trend_up else "PE"
            strike = strat._select_strike(price, opt_type)
            opt = strat._find_option(int(strike), opt_type, on_or_after=session)
            armed = False
            if not opt:
                continue
            otoken = int(opt.get("instrument_token") or 0)
            if not otoken:
                continue

            # option candles for the session (cached)
            if otoken not in opt_cache:
                try:
                    o_frm = datetime.combine(session, MARKET_OPEN)
                    o_to = datetime.combine(session, MARKET_CLOSE)
                    o_rows = broker.get_historical_data(otoken, o_frm, o_to, kite_interval) or []
                    opt_cache[otoken] = _aggregate(o_rows, factor)
                except Exception:
                    opt_cache[otoken] = []
            ocandles = opt_cache[otoken]
            if not ocandles:
                continue

            # locate the entry option candle (first at/after this index candle time)
            entry_pos = None
            for j, oc in enumerate(ocandles):
                odt = _candle_dt(oc.get("date"))
                if odt and odt >= cdt:
                    entry_pos = j
                    break
            if entry_pos is None:
                continue

            fill = float(ocandles[entry_pos].get("open", 0) or ocandles[entry_pos].get("close", 0) or 0)
            if fill <= 0:
                continue

            # ATR at entry (Wilder up to entry_pos)
            ohi = [float(c.get("high", 0) or 0) for c in ocandles[:entry_pos + 1]]
            olo = [float(c.get("low", 0) or 0) for c in ocandles[:entry_pos + 1]]
            ocl = [float(c.get("close", 0) or 0) for c in ocandles[:entry_pos + 1]]
            a = atr_series(ohi, olo, ocl, strat.atr_period)
            atr = a[-1] if a else 0.0
            if atr <= 0:
                atr = max(1.0, fill * 0.1)

            sl_price = max(0.05, fill - atr * strat.sl_mult)
            if strat.target_mode == "points":
                target_price = fill + strat.target_points
            elif strat.target_mode == "none":
                target_price = 0.0
            else:
                target_price = fill + atr * strat.tgt_mult

            # walk forward option candles → hidden SL/target proximity exits
            exit_price = 0.0
            exit_type = ""
            exit_time = ""
            last_update_pos = entry_pos
            cur_atr = atr
            update_bars = max(1, int(round(strat.atr_update_minutes /
                                           _tf_minutes(strat.timeframe)))) if _tf_minutes(strat.timeframe) else 1
            for k in range(entry_pos + 1, len(ocandles)):
                oc = ocandles[k]
                odt = _candle_dt(oc.get("date"))
                oh = float(oc.get("high", 0) or 0)
                ol = float(oc.get("low", 0) or 0)
                ocl_k = float(oc.get("close", 0) or 0)

                # periodic recompute (trailing SL up, target from latest ATR)
                if k - last_update_pos >= update_bars:
                    cur_atr = a2 if (a2 := _atr_upto(ocandles, k, strat.atr_period)) > 0 else cur_atr
                    trail = ocl_k - cur_atr * strat.sl_mult
                    sl_price = max(sl_price, min(trail, ocl_k - 0.05))
                    sl_price = max(0.05, sl_price)
                    if strat.target_mode == "atr":
                        target_price = fill + cur_atr * strat.tgt_mult
                    last_update_pos = k

                # 15:15 square-off
                if odt and odt.time() >= PRE_CLOSE_EXIT:
                    exit_price, exit_type, exit_time = ocl_k, "AUTO_SQUAREOFF", _candle_time_str(odt)
                    break
                # SL proximity (low reaches within proximity of SL)
                if sl_price > 0 and ol <= sl_price + strat.exit_proximity:
                    exit_price, exit_type = max(sl_price, ol), "SL_HIT"
                    exit_time = _candle_time_str(odt)
                    break
                # Target proximity (high reaches within proximity of target)
                if target_price > 0 and oh >= target_price - strat.exit_proximity:
                    exit_price, exit_type = min(target_price, oh), "TARGET_HIT"
                    exit_time = _candle_time_str(odt)
                    break

            if not exit_type:  # ran out of candles — close on last
                last = ocandles[-1]
                exit_price = float(last.get("close", 0) or fill)
                exit_type = "AUTO_SQUAREOFF"
                exit_time = _candle_time_str(_candle_dt(last.get("date")))

            qty = strat.quantity
            pnl = round((exit_price - fill) * qty, 2)
            cum_pnl += pnl
            running_eq += pnl
            trades_today += 1
            realized_today += pnl
            trade_no += 1

            hold_min = 0
            try:
                x = datetime.strptime(exit_time, "%H:%M")
                e = cdt
                hold_min = max(0, int((x.hour * 60 + x.minute) - (e.hour * 60 + e.minute)))
            except Exception:
                pass

            all_trades.append({
                "trade_no": trade_no,
                "date": session.isoformat(),
                "time": _candle_time_str(cdt),
                "index_price": round(price, 2),
                "ema_fast": round(ema_fast, 2),
                "ema_slow": round(ema_slow, 2),
                "signal": "CALL" if opt_type == "CE" else "PUT",
                "option_type": opt_type,
                "strike": int(strike),
                "option_name": opt.get("tradingsymbol"),
                "entry_price": round(fill, 2),
                "exit_price": round(exit_price, 2),
                "qty": qty, "lots": strat.lots,
                "stoploss": round(sl_price, 2),
                "target": round(target_price, 2),
                "entry_atr": round(atr, 2),
                "exit_reason": exit_type,
                "holding_time": f"{hold_min}m",
                "pnl": pnl,
                "running_equity": round(running_eq, 2),
            })
            equity_curve.append({"t": f"{session.isoformat()} {exit_time}", "y": round(cum_pnl, 2)})

    wins = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] < 0]
    total = len(all_trades)
    stats = {
        "total_trades": total,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(100 * len(wins) / total, 2) if total else 0.0,
        "total_pnl": round(cum_pnl, 2),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0.0,
        "best": round(max((t["pnl"] for t in all_trades), default=0), 2),
        "worst": round(min((t["pnl"] for t in all_trades), default=0), 2),
        "max_drawdown": _bt_max_drawdown(equity_curve),
        "sessions": len(sessions),
    }
    return {
        "status": "ok",
        "trades": all_trades,
        "equity_curve": equity_curve,
        "stats": stats,
        "params": strat._config_dict(),
        "date_from": sessions[0].isoformat() if sessions else None,
        "date_to": sessions[-1].isoformat() if sessions else None,
    }


def _tf_minutes(tf: str) -> float:
    return {
        "1minute": 1, "3minute": 3, "5minute": 5, "10minute": 10, "15minute": 15,
        "30minute": 30, "1hour": 60, "2hour": 120, "4hour": 240,
        "day": 375, "week": 1875, "month": 7500,
    }.get(tf, 1)


def _atr_upto(candles: list[dict], pos: int, period: int) -> float:
    hi = [float(c.get("high", 0) or 0) for c in candles[:pos + 1]]
    lo = [float(c.get("low", 0) or 0) for c in candles[:pos + 1]]
    cl = [float(c.get("close", 0) or 0) for c in candles[:pos + 1]]
    a = atr_series(hi, lo, cl, period)
    return a[-1] if a else 0.0


def _bt_max_drawdown(curve: list[dict]) -> float:
    peak = 0.0
    max_dd = 0.0
    for p in curve:
        y = float(p.get("y", 0))
        if y > peak:
            peak = y
        dd = peak - y
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)
