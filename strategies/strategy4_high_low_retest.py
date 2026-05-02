"""
Strategy 4 — Previous Day First-Hour High-Low Retest.

Concept
-------
Use yesterday's 9:15–10:15 candle as the day's reference range:
    prev_high  → resistance
    prev_low   → support

Live signals (NIFTY spot):
    • Breakout  → Retest hold above prev_high  → BUY ATM CALL
    • Breakdown → Retest reject below prev_low → BUY ATM PUT
    • Fake breakdown (price reclaims prev_low after dipping)  → BUY CALL
    • Fake breakout (price loses prev_high after popping)     → BUY PUT
    • Inside range → NO TRADE

Entries are placed at ATM strike at MARKET (per spec).  SL / Target are
managed as shadow orders that promote to real exchange orders when LTP
gets close, identical to the S1 pattern.  Auto square-off at 15:15 IST.

State machine
-------------
    IDLE
      → BREAKOUT_WATCH      (spot has gone above prev_high)
      → BREAKDOWN_WATCH     (spot has gone below prev_low)
      → ORDER_PLACED        (entry order sent to broker)
      → POSITION_OPEN       (entry filled — managing SL/TGT)
      → COMPLETED           (exited — locked until next day)

Trades-per-day cap is configurable; default = 1.
"""
from __future__ import annotations

import bisect
import json
import threading
from datetime import date, datetime, time as dtime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from config import settings
from core.broker import (
    Broker, OrderRequest,
    Exchange, OrderSide, OrderType, ProductType,
)
from core.logger import get_logger

logger = get_logger("strategy4.high_low_retest")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
FIRST_HOUR_END = dtime(10, 15)
PRE_CLOSE_EXIT = dtime(15, 15)

GANN_CSV = Path(__file__).resolve().parent.parent / "gann_levels.csv"

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy4_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy4_trades.json"
ORDER_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "order_history.json"


class State(str, Enum):
    IDLE = "IDLE"
    BREAKOUT_WATCH = "BREAKOUT_WATCH"
    BREAKDOWN_WATCH = "BREAKDOWN_WATCH"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy4HighLowRetest:
    """First-hour high/low retest strategy on NIFTY spot."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # ── Config (with sane NIFTY defaults) ──
        self.sl_points = float(config.get("sl_points", 30))
        self.target_points = float(config.get("target_points", 60))
        self.lot_size = int(config.get("lot_size", 75))
        self.strike_interval = int(config.get("strike_interval", 50))
        self.sl_proximity = float(config.get("sl_proximity", 5))
        self.target_proximity = float(config.get("target_proximity", 5))
        self.retest_buffer = float(config.get("retest_buffer", 8))
        # Max distance the spot can travel past the level before we consider
        # the move "extended" and stop arming a retest entry.
        self.max_breakout_extension = float(config.get("max_breakout_extension", 60))
        # Max trades per session (resets each day)
        self.max_trades_per_day = int(config.get("max_trades_per_day", 1))
        # Re-entry: continue scanning after one trade closes (within the cap)
        self.allow_reentry = bool(config.get("allow_reentry", False))
        # ITM offset (in index points) added to strike. For BUY CALL the
        # strike used is (ATM − itm_offset); for BUY PUT it is
        # (ATM + itm_offset). Use 0 to trade ATM.
        self.itm_offset = int(config.get("itm_offset", 100))
        # Gann target: use Gann level grid instead of flat target_points
        self.gann_target = bool(config.get("gann_target", False))
        # How many Gann levels above fill price to target (1, 2, 3, …)
        self.gann_count = max(1, int(config.get("gann_count", 1)))
        self.gann_levels = self._load_gann_levels()
        # Index symbol — fixed to NIFTY for now per spec.
        self.index_name = str(config.get("index_name", "NIFTY")).upper()

        # ── Reference levels ──
        self.prev_high: float = 0.0
        self.prev_low: float = 0.0
        self._levels_for_date: Optional[date] = None

        # ── State ──
        self.is_active: bool = False
        self.state: State = State.IDLE
        self.scenario: str = "—"
        self.signal: str = "NO_TRADE"   # BUY_CALL / BUY_PUT / NO_TRADE
        self._trading_date: Optional[date] = None
        self._check_lock = threading.Lock()

        # ── Live tracking ──
        self.spot_price: float = 0.0
        self.spot_extreme: float = 0.0   # most-extreme spot since arming
        self._level_crossed: Optional[str] = None  # "ABOVE_HIGH" / "BELOW_LOW"

        # ── Trade detail ──
        self.signal_type: Optional[str] = None  # CE / PE
        self.entry_reason: str = ""
        self.atm_strike: int = 0
        self.strike: int = 0   # actually-traded strike (ATM ± itm_offset)
        self.option_symbol: str = ""
        self.option_token: int = 0
        self.option_ltp: float = 0.0
        self.fill_price: float = 0.0
        self.sl_price: float = 0.0
        self.target_price: float = 0.0
        self.current_ltp: float = 0.0

        # ── Orders ──
        self.entry_order: Optional[dict] = None
        self.sl_order: Optional[dict] = None
        self.target_order: Optional[dict] = None
        self.sl_shadow: bool = True
        self.target_shadow: bool = True

        # ── Misc ──
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None
        self._trades_today: int = 0
        self.trade_log: list[dict] = []

    # ── Public controls ───────────────────────────────

    @staticmethod
    def _load_gann_levels() -> list[int]:
        levels = []
        if GANN_CSV.exists():
            with open(GANN_CSV, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and line.isdigit():
                        levels.append(int(line))
        levels.sort()
        return levels

    def _ceil_gann(self, price: float) -> float:
        """Smallest gann level strictly greater than price."""
        if not self.gann_levels:
            return price + self.target_points
        idx = bisect.bisect_right(self.gann_levels, price)
        if idx < len(self.gann_levels):
            return float(self.gann_levels[idx])
        return float(self.gann_levels[-1])

    def _nth_gann_above(self, price: float, n: int) -> float:
        """N-th Gann level above the given price."""
        if not self.gann_levels:
            return price + self.target_points * max(1, n)
        idx = bisect.bisect_right(self.gann_levels, price)
        target_idx = idx + (max(1, n) - 1)
        if target_idx < len(self.gann_levels):
            return float(self.gann_levels[target_idx])
        return float(self.gann_levels[-1])

    def _compute_target(self, fill_price: float) -> float:
        """Resolve target price honoring gann_target / gann_count config."""
        if self.gann_target and self.gann_levels:
            tgt = self._nth_gann_above(fill_price, self.gann_count)
            # Safety: never below fill_price + 1
            if tgt <= fill_price:
                tgt = fill_price + max(1.0, self.target_points)
            return float(tgt)
        return float(fill_price + self.target_points)

    def start(self, config: dict):
        self.apply_config(config, save=False)
        self.is_active = True
        self._check_day_reset()
        self._save_state()
        logger.info(
            "Strategy 4 started: SL=%s TGT=%s LOT=%s",
            self.sl_points, self.target_points, self.lot_size,
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 4 stopped")

    def apply_config(self, config: dict, save: bool = True) -> None:
        self.sl_points = float(config.get("sl_points", self.sl_points))
        self.target_points = float(config.get("target_points", self.target_points))
        self.lot_size = int(config.get("lot_size", self.lot_size))
        self.strike_interval = int(config.get("strike_interval", self.strike_interval))
        self.sl_proximity = float(config.get("sl_proximity", self.sl_proximity))
        self.target_proximity = float(config.get("target_proximity", self.target_proximity))
        self.retest_buffer = float(config.get("retest_buffer", self.retest_buffer))
        self.max_breakout_extension = float(config.get("max_breakout_extension", self.max_breakout_extension))
        self.max_trades_per_day = int(config.get("max_trades_per_day", self.max_trades_per_day))
        self.allow_reentry = bool(config.get("allow_reentry", self.allow_reentry))
        self.itm_offset = int(config.get("itm_offset", self.itm_offset))
        self.gann_target = bool(config.get("gann_target", self.gann_target))
        self.gann_count = max(1, int(config.get("gann_count", self.gann_count)))
        self.index_name = str(config.get("index_name", self.index_name)).upper()

        # Recompute SL / target on an open position when shadow legs are alive
        if self.state == State.POSITION_OPEN and self.fill_price > 0:
            if self.target_shadow:
                self.target_price = self._compute_target(self.fill_price)
                if self.target_order:
                    self.target_order["price"] = self.target_price
            if self.sl_shadow:
                self.sl_price = max(0.05, self.fill_price - self.sl_points)
                if self.sl_order:
                    self.sl_order["price"] = self.sl_price

        if save:
            self._save_state()

    # ── Daily reset ───────────────────────────────────

    def _check_day_reset(self):
        today = date.today()
        if self._trading_date == today:
            return

        old_date = self._trading_date
        self._trading_date = today

        # Recover any orphan trade from previous day
        if self.state in (State.POSITION_OPEN, State.ORDER_PLACED) and self.fill_price > 0:
            logger.warning(
                "S4 orphaned %s from %s — recording BROKER_SQUAREOFF",
                self.state.value, old_date,
            )
            trade = {
                "date": (old_date or today).isoformat(),
                "signal": self.signal_type,
                "scenario": self.scenario,
                "option": self.option_symbol,
                "atm_strike": self.atm_strike,
                "entry_price": self.fill_price,
                "exit_type": "BROKER_SQUAREOFF",
                "exit_price": self.current_ltp or self.fill_price,
                "exit_time": "15:29",
                "lot_size": self.lot_size,
                "pnl": round(((self.current_ltp or self.fill_price) - self.fill_price) * self.lot_size, 2),
                "timestamp": datetime.now().isoformat(),
            }
            self.trade_log.append(trade)
            self._append_trade_history(trade)

        # Reset everything except is_active and config
        self.state = State.IDLE
        self.scenario = "—"
        self.signal = "NO_TRADE"
        self.signal_type = None
        self.entry_reason = ""
        self.atm_strike = 0
        self.strike = 0
        self.option_symbol = ""
        self.option_token = 0
        self.option_ltp = 0.0
        self.fill_price = 0.0
        self.current_ltp = 0.0
        self.sl_price = 0.0
        self.target_price = 0.0
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True
        self._level_crossed = None
        self.spot_extreme = 0.0
        self._trades_today = 0
        self._instruments_cache = None
        self._levels_for_date = None
        self.prev_high = 0.0
        self.prev_low = 0.0
        self._save_state()
        logger.info("S4 new trading day %s — reset to IDLE", today)

    # ── Reference levels ──────────────────────────────

    def _resolve_index_token(self) -> Optional[int]:
        """Resolve NIFTY 50 spot instrument_token (cached for the day)."""
        try:
            instruments = self._get_nse_instruments()
            for inst in instruments:
                if inst.get("tradingsymbol") == "NIFTY 50":
                    return int(inst["instrument_token"])
        except Exception as exc:
            logger.warning("Index token lookup failed: %s", exc)
        return None

    def _get_nse_instruments(self) -> list[dict]:
        today = date.today()
        if self._instruments_cache and self._instruments_date == today and self._instruments_cache.get("NSE"):
            return self._instruments_cache["NSE"]
        cache = self._instruments_cache or {}
        cache["NSE"] = self.broker.get_instruments("NSE")
        self._instruments_cache = cache
        self._instruments_date = today
        return cache["NSE"]

    def _get_nfo_instruments(self) -> list[dict]:
        today = date.today()
        if self._instruments_cache and self._instruments_date == today and self._instruments_cache.get("NFO"):
            return self._instruments_cache["NFO"]
        cache = self._instruments_cache or {}
        cache["NFO"] = self.broker.get_instruments("NFO")
        self._instruments_cache = cache
        self._instruments_date = today
        return cache["NFO"]

    def _previous_trading_day(self, today: date) -> date:
        """Walk back skipping Sat/Sun. Holidays are not handled — we fall
        back to whichever day actually returned candles."""
        d = today - timedelta(days=1)
        while d.weekday() >= 5:  # 5=Sat, 6=Sun
            d -= timedelta(days=1)
        return d

    def fetch_levels(self, force: bool = False) -> dict:
        """Fetch (and cache) yesterday's 9:15–10:15 high/low.

        Falls back through a 10-day window of trading days in case the
        immediate previous day was a holiday.  Returns a dict suitable
        for the API response.
        """
        today = date.today()
        if not force and self._levels_for_date == today and self.prev_high > 0:
            return self._levels_payload()

        token = self._resolve_index_token()
        if not token:
            return {"status": "error", "message": "Could not resolve NIFTY 50 token"}

        candidate = today
        for _ in range(10):
            candidate = self._previous_trading_day(candidate)
            try:
                from_dt = datetime.combine(candidate, MARKET_OPEN)
                to_dt = datetime.combine(candidate, FIRST_HOUR_END)
                candles = self.broker.get_historical_data(
                    instrument_token=token,
                    from_date=from_dt,
                    to_date=to_dt,
                    interval="minute",
                )
            except Exception as exc:
                logger.warning("Historical fetch for %s failed: %s", candidate, exc)
                candles = []
            if not candles:
                continue

            highs = [float(c["high"]) for c in candles]
            lows = [float(c["low"]) for c in candles]
            self.prev_high = max(highs)
            self.prev_low = min(lows)
            self._levels_for_date = today
            logger.info(
                "S4 levels resolved from %s: high=%.2f low=%.2f (%d candles)",
                candidate, self.prev_high, self.prev_low, len(candles),
            )
            self._save_state()
            return self._levels_payload(source_date=candidate)

        return {"status": "error", "message": "No previous-day 9:15-10:15 candles available"}

    def _levels_payload(self, source_date: Optional[date] = None) -> dict:
        return {
            "status": "ok",
            "prev_high": self.prev_high,
            "prev_low": self.prev_low,
            "source_date": source_date.isoformat() if source_date else (
                self._levels_for_date.isoformat() if self._levels_for_date else None
            ),
            "for_date": (self._trading_date or date.today()).isoformat(),
        }

    # ── Main check ────────────────────────────────────

    def check(self, spot_price: float) -> dict:
        if not self.is_active:
            return self.get_status()

        if not self._check_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self._check_day_reset()

            # Lazy-load levels (first tick of day, or after restart)
            if self.prev_high <= 0 or self.prev_low <= 0:
                self.fetch_levels()

            if spot_price > 0:
                self.spot_price = spot_price

            # Auto square-off
            if self.state == State.POSITION_OPEN and datetime.now().time() >= PRE_CLOSE_EXIT:
                logger.info("S4 auto square-off triggered")
                self._auto_square_off()
                return self.get_status()

            if self.state == State.IDLE:
                self._scan_for_setup()
            elif self.state in (State.BREAKOUT_WATCH, State.BREAKDOWN_WATCH):
                self._scan_retest()
            elif self.state == State.ORDER_PLACED:
                self._check_entry_fill()
            elif self.state == State.POSITION_OPEN:
                self._check_exit()

            self._refresh_current_ltp()
            return self.get_status()
        finally:
            self._check_lock.release()

    # ── Setup detection ───────────────────────────────

    def _scan_for_setup(self):
        if self.prev_high <= 0 or self.prev_low <= 0 or self.spot_price <= 0:
            self.scenario = "Loading levels…"
            return
        # Trades-per-day cap
        if self._trades_today >= self.max_trades_per_day:
            self.scenario = "Max trades reached"
            return

        # NOTE: We intentionally do NOT wait for 10:15 today. The levels
        # are derived from yesterday's first hour; today we are free to
        # act on the very first cross of those levels for fastest entry.

        if self.spot_price > self.prev_high:
            self.state = State.BREAKOUT_WATCH
            self._level_crossed = "ABOVE_HIGH"
            self.spot_extreme = self.spot_price
            self.scenario = "Breakout — waiting for retest"
            self.signal = "NO_TRADE"
            logger.info("S4 BREAKOUT_WATCH @ spot=%.2f (high=%.2f)", self.spot_price, self.prev_high)
        elif self.spot_price < self.prev_low:
            self.state = State.BREAKDOWN_WATCH
            self._level_crossed = "BELOW_LOW"
            self.spot_extreme = self.spot_price
            self.scenario = "Breakdown — waiting for retest"
            self.signal = "NO_TRADE"
            logger.info("S4 BREAKDOWN_WATCH @ spot=%.2f (low=%.2f)", self.spot_price, self.prev_low)
        else:
            self.scenario = "Sideways → NO TRADE"
            self.signal = "NO_TRADE"

    def _scan_retest(self):
        """Look for retest / fake-out patterns and arm an entry.

        Dynamic side switching: if the spot crosses the OPPOSITE level
        while we were waiting for a retest on the other side, we flip
        state immediately. This makes S4 react to fast trend reversals
        instead of being stuck watching one side.
        """
        if self.spot_price <= 0:
            return

        # Dynamic flip: spot punched through the opposite level
        if self.state == State.BREAKOUT_WATCH and self.spot_price < self.prev_low:
            logger.info(
                "S4 dynamic flip BREAKOUT→BREAKDOWN (spot=%.2f cut prev_low=%.2f)",
                self.spot_price, self.prev_low,
            )
            self.state = State.BREAKDOWN_WATCH
            self._level_crossed = "BELOW_LOW"
            self.spot_extreme = self.spot_price
            self.scenario = "Flipped → Breakdown watch"
            self.signal = "NO_TRADE"
        elif self.state == State.BREAKDOWN_WATCH and self.spot_price > self.prev_high:
            logger.info(
                "S4 dynamic flip BREAKDOWN→BREAKOUT (spot=%.2f cut prev_high=%.2f)",
                self.spot_price, self.prev_high,
            )
            self.state = State.BREAKOUT_WATCH
            self._level_crossed = "ABOVE_HIGH"
            self.spot_extreme = self.spot_price
            self.scenario = "Flipped → Breakout watch"
            self.signal = "NO_TRADE"

        if self.state == State.BREAKOUT_WATCH:
            # Track extreme above prev_high
            if self.spot_price > self.spot_extreme:
                self.spot_extreme = self.spot_price

            # Fake breakout: price came back below prev_high → BUY PUT
            if self.spot_price < self.prev_high - self.retest_buffer / 2:
                self.scenario = "Fake Breakout → PUT"
                self.signal = "BUY_PUT"
                self.entry_reason = (
                    f"Spot reclaimed below prev_high {self.prev_high:.2f} "
                    f"after extension to {self.spot_extreme:.2f}"
                )
                self._fire_entry("PE")
                return

            # Retest hold: price pulled back near level but stayed above → BUY CALL
            if (
                self.prev_high <= self.spot_price <= self.prev_high + self.retest_buffer
                and self.spot_extreme - self.prev_high >= 2  # at least 2pt extension
            ):
                self.scenario = "Breakout → Retest → CALL"
                self.signal = "BUY_CALL"
                self.entry_reason = (
                    f"Retest hold above {self.prev_high:.2f} "
                    f"(peak {self.spot_extreme:.2f})"
                )
                self._fire_entry("CE")
                return

            # If extension goes very far without retest, abandon (move "extended")
            if self.spot_extreme - self.prev_high > self.max_breakout_extension:
                self.scenario = "Extended — no retest"
                self.signal = "NO_TRADE"

        elif self.state == State.BREAKDOWN_WATCH:
            if self.spot_price < self.spot_extreme:
                self.spot_extreme = self.spot_price

            # Fake breakdown: price reclaimed above prev_low → BUY CALL
            if self.spot_price > self.prev_low + self.retest_buffer / 2:
                self.scenario = "Fake Breakdown → CALL"
                self.signal = "BUY_CALL"
                self.entry_reason = (
                    f"Spot reclaimed above prev_low {self.prev_low:.2f} "
                    f"after dip to {self.spot_extreme:.2f}"
                )
                self._fire_entry("CE")
                return

            # Retest reject: price pulled back near level but stayed below → BUY PUT
            if (
                self.prev_low - self.retest_buffer <= self.spot_price <= self.prev_low
                and self.prev_low - self.spot_extreme >= 2
            ):
                self.scenario = "Breakdown → Retest → PUT"
                self.signal = "BUY_PUT"
                self.entry_reason = (
                    f"Retest reject below {self.prev_low:.2f} "
                    f"(trough {self.spot_extreme:.2f})"
                )
                self._fire_entry("PE")
                return

            if self.prev_low - self.spot_extreme > self.max_breakout_extension:
                self.scenario = "Extended — no retest"
                self.signal = "NO_TRADE"

    # ── Option resolution & entry ─────────────────────

    def _calc_atm(self, spot: float) -> int:
        return round(spot / self.strike_interval) * self.strike_interval

    def _find_option(self, strike: int, opt_type: str) -> Optional[dict]:
        instruments = self._get_nfo_instruments()
        today = date.today()
        candidates = []
        for inst in instruments:
            if (
                inst.get("name") == self.index_name
                and inst.get("instrument_type") == opt_type
                and float(inst.get("strike", 0) or 0) == float(strike)
            ):
                expiry = inst.get("expiry")
                if isinstance(expiry, str):
                    try:
                        expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                if expiry and expiry >= today:
                    candidates.append((expiry, inst))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _fire_entry(self, opt_type: str):
        self.signal_type = opt_type
        self.atm_strike = self._calc_atm(self.spot_price)
        # ITM strike: CE shifts strike DOWN; PE shifts strike UP (so the
        # option is in-the-money relative to current spot).
        if opt_type == "CE":
            self.strike = int(self.atm_strike - self.itm_offset)
        else:
            self.strike = int(self.atm_strike + self.itm_offset)

        opt_info = self._find_option(self.strike, opt_type)
        if not opt_info:
            logger.error("S4 no %s option at strike %s (ATM=%s, offset=%s)",
                         opt_type, self.strike, self.atm_strike, self.itm_offset)
            self.scenario = f"No {opt_type} option found at {self.strike}"
            self.state = State.IDLE
            return

        self.option_symbol = opt_info["tradingsymbol"]
        self.option_token = int(opt_info["instrument_token"])
        if opt_info.get("lot_size"):
            self.lot_size = int(opt_info["lot_size"])

        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            self.option_ltp = float(ltp_map.get(f"NFO:{self.option_symbol}", 0) or 0)
        except Exception as exc:
            logger.error("S4 LTP fetch failed: %s", exc)
            self.option_ltp = 0.0

        self._place_entry_order()

    def _place_entry_order(self):
        prev_state = self.state
        self.state = State.ORDER_PLACED
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol,
                exchange=Exchange.NFO,
                side=OrderSide.BUY,
                quantity=self.lot_size,
                order_type=OrderType.MARKET,
                product=ProductType.MIS,
                tag="S4ENTRY",
            )
            resp = self.broker.place_order(req)
            self.entry_order = {
                "order_id": resp.order_id,
                "status": resp.status,
                "is_paper": resp.is_paper,
                "price": self.option_ltp,
                "timestamp": datetime.now().isoformat(),
            }
            if resp.is_paper and resp.status == "COMPLETE":
                self.fill_price = self.option_ltp
                self.entry_order["status"] = "COMPLETE"
                self._on_entry_filled()
            else:
                self._save_state()
                logger.info("S4 entry order placed: %s", resp.order_id)
        except Exception as exc:
            logger.error("S4 entry order failed: %s", exc)
            self.state = prev_state
            self.entry_order = None
            self._save_state()

    def _check_entry_fill(self):
        if not self.entry_order:
            self.state = State.IDLE
            return

        # ── Dynamic flip guard while order is pending ──
        # If the spot crossed the opposite side while we are waiting for
        # a fill, cancel the pending order and revert to IDLE so the
        # next tick can fire the opposite direction.
        if self.spot_price > 0 and self.signal_type and self.prev_high > 0 and self.prev_low > 0:
            flipped = (
                (self.signal_type == "CE" and self.spot_price < self.prev_low)
                or (self.signal_type == "PE" and self.spot_price > self.prev_high)
            )
            if flipped:
                logger.info(
                    "S4 spot flipped against pending %s entry — cancelling order",
                    self.signal_type,
                )
                self._cancel_order(self.entry_order)
                self.entry_order["status"] = "CANCELLED"
                self.state = State.IDLE
                self.signal_type = None
                self.signal = "NO_TRADE"
                self.scenario = "Cancelled — direction flipped"
                self._save_state()
                return

        # Staleness: cancel if unfilled for >60s
        placed_at = self.entry_order.get("timestamp")
        if placed_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(placed_at)).total_seconds()
                if elapsed > 60 and self.entry_order.get("status") != "COMPLETE":
                    logger.info("S4 entry stale (%.0fs) — cancelling", elapsed)
                    self._cancel_order(self.entry_order)
                    self.entry_order["status"] = "CANCELLED"
                    self.state = State.IDLE
                    self.signal_type = None
                    self.scenario = "Cancelled — stale order"
                    self._save_state()
                    return
            except Exception:
                pass

        if self.entry_order.get("is_paper"):
            return  # already handled in place
        try:
            orders = self.broker.get_orders()
            for o in orders:
                if str(o.get("order_id")) == str(self.entry_order["order_id"]):
                    status = o.get("status", "")
                    if status == "COMPLETE":
                        self.fill_price = float(o.get("average_price", self.option_ltp))
                        self.entry_order["status"] = "COMPLETE"
                        self._on_entry_filled()
                    elif status in ("CANCELLED", "REJECTED"):
                        self.entry_order["status"] = status
                        self.state = State.COMPLETED
                        self._save_state()
                        logger.warning("S4 entry %s", status)
                    break
        except Exception as exc:
            logger.error("S4 fill check failed: %s", exc)

    def _on_entry_filled(self):
        self.target_price = self._compute_target(self.fill_price)
        self.sl_price = max(0.05, self.fill_price - self.sl_points)
        self.sl_shadow = True
        self.target_shadow = True
        self.sl_order = {
            "order_id": "SHADOW-SL",
            "status": "SHADOW",
            "price": self.sl_price,
            "timestamp": datetime.now().isoformat(),
        }
        self.target_order = {
            "order_id": "SHADOW-TGT",
            "status": "SHADOW",
            "price": self.target_price,
            "timestamp": datetime.now().isoformat(),
        }
        self.state = State.POSITION_OPEN
        self._trades_today += 1
        self._save_state()
        logger.info(
            "S4 position open. Entry=%.2f SL=%.2f TGT=%.2f",
            self.fill_price, self.sl_price, self.target_price,
        )

    # ── Exit handling ─────────────────────────────────

    def _check_exit(self):
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            ltp = float(ltp_map.get(f"NFO:{self.option_symbol}", 0) or 0)
        except Exception:
            return
        if ltp <= 0:
            return
        self.current_ltp = ltp

        if settings.PAPER_TRADE:
            if ltp <= self.sl_price:
                self._complete_trade("SL_HIT", self.sl_price)
            elif ltp >= self.target_price:
                self._complete_trade("TARGET_HIT", self.target_price)
            return

        # Live: shadow → real exchange order on proximity / breach
        if not self.sl_shadow or not self.target_shadow:
            try:
                orders = self.broker.get_orders()
            except Exception:
                orders = []
            for o in orders:
                oid = str(o.get("order_id", ""))
                status = o.get("status", "")
                if (
                    not self.sl_shadow and self.sl_order
                    and oid == str(self.sl_order["order_id"]) and status == "COMPLETE"
                ):
                    self._cancel_order(self.target_order)
                    self._complete_trade("SL_HIT", self.sl_price)
                    return
                if (
                    not self.target_shadow and self.target_order
                    and oid == str(self.target_order["order_id"]) and status == "COMPLETE"
                ):
                    self._cancel_order(self.sl_order)
                    self._complete_trade("TARGET_HIT", self.target_price)
                    return

        # SL leg promotion
        if self.sl_shadow and ltp <= (self.sl_price + self.sl_proximity):
            if not self.target_shadow and self.target_order:
                self._cancel_order(self.target_order)
                self.target_order = None
                self.target_shadow = True
            self.sl_shadow = False
            try:
                if ltp <= self.sl_price:
                    exit_price = max(0.05, round(ltp * 0.90, 2))
                    self.broker.place_order(OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO, side=OrderSide.SELL,
                        quantity=self.lot_size, order_type=OrderType.LIMIT,
                        product=ProductType.MIS, price=exit_price, tag="S4SL",
                    ))
                    self._complete_trade("SL_HIT", ltp)
                    return
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.lot_size, order_type=OrderType.SL_M,
                    product=ProductType.MIS, trigger_price=self.sl_price, tag="S4SL",
                ))
                self.sl_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "price": self.sl_price,
                    "timestamp": datetime.now().isoformat(),
                }
                self._save_state()
            except Exception as exc:
                logger.error("S4 SL placement failed: %s", exc)
                self.sl_shadow = True

        # Target leg promotion
        if self.target_shadow and ltp >= (self.target_price - self.target_proximity):
            if not self.sl_shadow and self.sl_order:
                self._cancel_order(self.sl_order)
                self.sl_order = None
                self.sl_shadow = True
            self.target_shadow = False
            try:
                if ltp >= self.target_price:
                    exit_price = max(0.05, round(ltp * 0.90, 2))
                    self.broker.place_order(OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO, side=OrderSide.SELL,
                        quantity=self.lot_size, order_type=OrderType.LIMIT,
                        product=ProductType.MIS, price=exit_price, tag="S4TGT",
                    ))
                    self._complete_trade("TARGET_HIT", ltp)
                    return
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.lot_size, order_type=OrderType.LIMIT,
                    product=ProductType.MIS, price=self.target_price, tag="S4TGT",
                ))
                self.target_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "price": self.target_price,
                    "timestamp": datetime.now().isoformat(),
                }
                self._save_state()
            except Exception as exc:
                logger.error("S4 target placement failed: %s", exc)
                self.target_shadow = True

    def _auto_square_off(self):
        self._cancel_order(self.sl_order)
        self._cancel_order(self.target_order)
        exit_price = self.current_ltp
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            exit_price = float(ltp_map.get(f"NFO:{self.option_symbol}", exit_price) or exit_price)
        except Exception:
            pass
        if not settings.PAPER_TRADE:
            try:
                self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.lot_size, order_type=OrderType.LIMIT,
                    product=ProductType.MIS,
                    price=max(0.05, round(exit_price * 0.90, 2)),
                    tag="S4SQOFF",
                ))
            except Exception as exc:
                logger.error("S4 squareoff failed: %s", exc)
                return
        self._complete_trade("AUTO_SQUAREOFF", exit_price)

    def _cancel_order(self, order: Optional[dict]):
        if not order:
            return
        if order.get("status") == "SHADOW" or str(order.get("order_id", "")).startswith("SHADOW"):
            return
        try:
            self.broker.kite.cancel_order(variety="regular", order_id=order["order_id"])
        except Exception as exc:
            logger.warning("S4 cancel failed: %s", exc)

    def _complete_trade(self, exit_type: str, exit_price: float):
        pnl = (exit_price - self.fill_price) * self.lot_size
        if exit_type == "SL_HIT":
            pnl = -abs(pnl)
        trade = {
            "date": (self._trading_date or date.today()).isoformat(),
            "signal": self.signal_type,
            "scenario": self.scenario,
            "option": self.option_symbol,
            "atm_strike": self.atm_strike,
            "strike": self.strike,
            "entry_price": self.fill_price,
            "exit_type": exit_type,
            "exit_price": exit_price,
            "exit_time": datetime.now().strftime("%H:%M:%S"),
            "lot_size": self.lot_size,
            "pnl": round(pnl, 2),
            "timestamp": datetime.now().isoformat(),
        }
        self.trade_log.append(trade)
        if self.sl_order:
            self.sl_order["status"] = "COMPLETE" if exit_type == "SL_HIT" else "CANCELLED"
        if self.target_order:
            self.target_order["status"] = "COMPLETE" if exit_type == "TARGET_HIT" else "CANCELLED"
        logger.info("S4 trade done: %s | Entry=%.2f Exit=%.2f PnL=%.2f",
                    exit_type, self.fill_price, exit_price, pnl)

        # Reset trade-specific fields
        self.fill_price = 0.0
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True

        if exit_type == "SL_HIT":
            self.is_active = False
            self.state = State.COMPLETED
        elif self.allow_reentry and self._trades_today < self.max_trades_per_day:
            # Re-arm for another setup
            self.state = State.IDLE
            self._level_crossed = None
            self.spot_extreme = 0.0
        else:
            self.state = State.COMPLETED
        self._save_state()
        self._append_trade_history(trade)

    # ── LTP refresh ───────────────────────────────────

    def _refresh_current_ltp(self):
        if not self.option_symbol:
            return
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            ltp = float(ltp_map.get(f"NFO:{self.option_symbol}", 0) or 0)
            if ltp > 0:
                self.current_ltp = ltp
                self.option_ltp = ltp
        except Exception:
            pass

    # ── Backtest ──────────────────────────────────────

    def backtest(self, target_date: Optional[date] = None) -> dict:
        """Replay this strategy on a given trading day using minute candles.

        For options, an exact replay would require historical option chain
        prices we don't have. Instead we use a spot-proxy: option price
        change ≈ |spot move| × delta (delta=1.0 for ATM, capped at SL/TGT
        index points). This produces a realistic *signal-quality* backtest
        — useful for "did the levels hold today?" — and is clearly labeled
        as a simulation in the UI.

        Walks back up to 10 days to find a session with prev-day levels
        AND that session's minute candles available.
        """
        anchor = target_date or date.today()
        token = self._resolve_index_token()
        if not token:
            return {"status": "error", "message": "Could not resolve NIFTY 50 token"}

        # Find latest day whose previous-trading-day has 9:15-10:15 data
        # AND that day itself has 9:15-15:30 minute data available.
        for offset in range(0, 10):
            sim_day = anchor - timedelta(days=offset)
            if sim_day.weekday() >= 5:
                continue
            prev = self._previous_trading_day(sim_day)

            try:
                pcandles = self.broker.get_historical_data(
                    instrument_token=token,
                    from_date=datetime.combine(prev, MARKET_OPEN),
                    to_date=datetime.combine(prev, FIRST_HOUR_END),
                    interval="minute",
                )
            except Exception:
                pcandles = []
            if not pcandles:
                continue
            try:
                tcandles = self.broker.get_historical_data(
                    instrument_token=token,
                    from_date=datetime.combine(sim_day, MARKET_OPEN),
                    to_date=datetime.combine(sim_day, MARKET_CLOSE),
                    interval="minute",
                )
            except Exception:
                tcandles = []
            if not tcandles:
                continue

            prev_high = max(float(c["high"]) for c in pcandles)
            prev_low = min(float(c["low"]) for c in pcandles)

            return self._run_backtest_sim(sim_day, prev, prev_high, prev_low, tcandles)

        return {"status": "error", "message": "No suitable historical session found within 10 days"}

    def _run_backtest_sim(
        self,
        sim_day: date,
        prev_day: date,
        prev_high: float,
        prev_low: float,
        candles: list[dict],
    ) -> dict:
        """Pure-python replay using the live ITM-strike rule.

        For each entry, the strike is computed exactly the way live does
        it (ATM ± itm_offset). We then attempt to fetch real minute
        candles for that option contract on sim_day. When we have real
        option data, SL/TGT and exit P&L are computed in option points
        (the way they're hit in live). When the contract for that day is
        no longer queryable (expired & no longer in the instrument
        master), we fall back to a spot-proxy (option Δ ≈ 1) and tag
        the trade with `data_source = "PROXY"` so the user can see which
        rows used real prices.
        """
        retest_buf = float(self.retest_buffer)
        max_ext = float(self.max_breakout_extension)
        sl_pts = float(self.sl_points)
        tgt_pts = float(self.target_points)
        max_trades = int(self.max_trades_per_day)
        itm_offset = int(self.itm_offset)

        # Per-(strike, opt_type) cache so back-to-back entries on the
        # same contract only fetch once.
        opt_cache: dict[tuple, Optional[dict]] = {}

        def _fetch_option_data(strike: int, opt_type: str) -> Optional[dict]:
            """Returns {symbol, token, minutes: {HH:MM -> {o,h,l,c}}} or None."""
            key = (strike, opt_type)
            if key in opt_cache:
                return opt_cache[key]
            info = self._find_option(strike, opt_type)
            if not info:
                opt_cache[key] = None
                return None
            try:
                rows = self.broker.get_historical_data(
                    instrument_token=int(info["instrument_token"]),
                    from_date=datetime.combine(sim_day, MARKET_OPEN),
                    to_date=datetime.combine(sim_day, MARKET_CLOSE),
                    interval="minute",
                )
            except Exception as exc:
                logger.warning("S4 backtest option fetch failed for %s: %s",
                               info.get("tradingsymbol"), exc)
                opt_cache[key] = None
                return None
            if not rows:
                opt_cache[key] = None
                return None
            minutes: dict[str, dict] = {}
            for r in rows:
                d = r.get("date")
                if isinstance(d, str):
                    try:
                        tt = datetime.fromisoformat(d.replace("Z", "+00:00")).time()
                    except Exception:
                        continue
                else:
                    try:
                        tt = d.time()
                    except Exception:
                        continue
                minutes[tt.strftime("%H:%M")] = {
                    "o": float(r.get("open", 0) or 0),
                    "h": float(r.get("high", 0) or 0),
                    "l": float(r.get("low", 0) or 0),
                    "c": float(r.get("close", 0) or 0),
                }
            entry = {
                "symbol": info.get("tradingsymbol", ""),
                "token": int(info["instrument_token"]),
                "minutes": minutes,
            }
            opt_cache[key] = entry
            return entry

        st = "IDLE"
        # When in POSITION_OPEN, `pos` holds the open trade context.
        pos: Optional[dict] = None
        extreme = 0.0
        trades = []
        events = []
        trades_done = 0
        spot_series = []
        any_real = False
        any_proxy = False

        def candle_time(c):
            d = c.get("date")
            if isinstance(d, str):
                try:
                    return datetime.fromisoformat(d.replace("Z", "+00:00")).time()
                except Exception:
                    return None
            try:
                return d.time()
            except Exception:
                return None

        def _open_position(t_str: str, side: str, entry_spot: float) -> Optional[dict]:
            """Resolve strike + entry premium for a new entry. Returns
            the position dict (or None on failure)."""
            atm = self._calc_atm(entry_spot)
            strike_ = atm - itm_offset if side == "CE" else atm + itm_offset
            opt_data = _fetch_option_data(strike_, side)
            entry_premium = 0.0
            data_source = "PROXY"
            symbol = f"{self.index_name} {strike_} {side}"
            if opt_data and opt_data["minutes"].get(t_str):
                entry_premium = opt_data["minutes"][t_str]["c"]
                data_source = "REAL"
                symbol = opt_data["symbol"] or symbol
            if entry_premium <= 0:
                # Real data available but premium unprintable -> fall back
                data_source = "PROXY"
                # Use a notional ITM premium of itm_offset + a small extrinsic
                # so SL/TGT in option points scale correctly.
                entry_premium = float(itm_offset + 50)
            return {
                "side": side,
                "strike": int(strike_),
                "atm": int(atm),
                "symbol": symbol,
                "entry_spot": entry_spot,
                "entry_premium": round(entry_premium, 2),
                "sl_premium": round(max(0.05, entry_premium - sl_pts), 2),
                "tgt_premium": round(entry_premium + tgt_pts, 2),
                "entry_time": t_str,
                "data_source": data_source,
                "opt_data": opt_data,
            }

        def _close_position(p: dict, t_str: str, exit_premium: float, kind: str, label: str):
            nonlocal any_real, any_proxy
            pnl = (exit_premium - p["entry_premium"]) * self.lot_size
            trades.append({
                "time": t_str,
                "side": p["side"],
                "strike": p["strike"],
                "option_symbol": p["symbol"],
                "entry": p["entry_spot"],
                "exit": p["entry_spot"],  # spot column kept for chart marker
                "entry_premium": p["entry_premium"],
                "exit_premium": round(exit_premium, 2),
                "exit_type": kind,
                "pnl": round(pnl, 2),
                "data_source": p["data_source"],
            })
            events.append({"t": t_str, "kind": kind if kind in ("SL", "TGT") else "EXIT", "label": label})
            if p["data_source"] == "REAL":
                any_real = True
            else:
                any_proxy = True

        for c in candles:
            t_ = candle_time(c)
            if not t_:
                continue
            high = float(c["high"]); low = float(c["low"]); close = float(c["close"])
            t_str = t_.strftime("%H:%M")
            spot_series.append({
                "t": t_str,
                "o": float(c["open"]), "h": high, "l": low, "c": close,
            })

            # ── Auto square-off ──
            if st == "POSITION_OPEN" and pos and t_ >= PRE_CLOSE_EXIT:
                # Real exit premium if available, else proxy from spot move
                exit_prem = 0.0
                if pos["data_source"] == "REAL" and pos["opt_data"]:
                    cell = pos["opt_data"]["minutes"].get(t_str)
                    if cell:
                        exit_prem = cell["c"]
                if exit_prem <= 0:
                    # Spot-proxy fallback (delta=1)
                    if pos["side"] == "CE":
                        exit_prem = pos["entry_premium"] + (close - pos["entry_spot"])
                    else:
                        exit_prem = pos["entry_premium"] + (pos["entry_spot"] - close)
                    pos["data_source"] = "PROXY"
                _close_position(pos, t_str, max(0.05, exit_prem), "AUTO_SQUAREOFF", "Auto SqOff")
                st = "COMPLETED"; pos = None
                continue

            # ── Position management ──
            if st == "POSITION_OPEN" and pos:
                hit_sl = False
                hit_tgt = False
                exit_prem = 0.0

                if pos["data_source"] == "REAL" and pos["opt_data"]:
                    cell = pos["opt_data"]["minutes"].get(t_str)
                    if cell:
                        # Use option's own intra-bar high/low — matches live
                        # SL_M / LIMIT trigger semantics exactly.
                        if cell["l"] <= pos["sl_premium"]:
                            hit_sl = True
                            exit_prem = pos["sl_premium"]
                        elif cell["h"] >= pos["tgt_premium"]:
                            hit_tgt = True
                            exit_prem = pos["tgt_premium"]
                else:
                    # Spot-proxy: option move ≈ favorable spot move
                    if pos["side"] == "CE":
                        favor = high - pos["entry_spot"]
                        adverse = pos["entry_spot"] - low
                    else:
                        favor = pos["entry_spot"] - low
                        adverse = high - pos["entry_spot"]
                    if adverse >= sl_pts:
                        hit_sl = True
                        exit_prem = pos["sl_premium"]
                    elif favor >= tgt_pts:
                        hit_tgt = True
                        exit_prem = pos["tgt_premium"]

                if hit_sl:
                    _close_position(pos, t_str, exit_prem, "SL_HIT", "SL")
                    trades_done += 1
                    st = "COMPLETED"  # mirror live: SL deactivates for the day
                    pos = None
                    continue
                if hit_tgt:
                    _close_position(pos, t_str, exit_prem, "TARGET_HIT", "TGT")
                    trades_done += 1
                    if trades_done >= max_trades or not self.allow_reentry:
                        st = "COMPLETED"
                    else:
                        st = "IDLE"
                    pos = None
                    continue

            if st == "COMPLETED":
                continue

            # ── IDLE: detect cross ──
            if st == "IDLE":
                if trades_done >= max_trades:
                    st = "COMPLETED"; continue
                if close > prev_high:
                    st = "BREAKOUT_WATCH"; extreme = close
                    events.append({"t": t_str, "kind": "WATCH", "label": "Breakout"})
                elif close < prev_low:
                    st = "BREAKDOWN_WATCH"; extreme = close
                    events.append({"t": t_str, "kind": "WATCH", "label": "Breakdown"})
                continue

            # ── Dynamic flip ──
            if st == "BREAKOUT_WATCH" and close < prev_low:
                st = "BREAKDOWN_WATCH"; extreme = close
                events.append({"t": t_str, "kind": "FLIP", "label": "→ Breakdown"})
            elif st == "BREAKDOWN_WATCH" and close > prev_high:
                st = "BREAKOUT_WATCH"; extreme = close
                events.append({"t": t_str, "kind": "FLIP", "label": "→ Breakout"})

            # ── Watch states → entries ──
            if st == "BREAKOUT_WATCH":
                if close > extreme:
                    extreme = close
                # Fake breakout → BUY PUT
                if close < prev_high - retest_buf / 2:
                    pos = _open_position(t_str, "PE", close)
                    if pos:
                        st = "POSITION_OPEN"
                        events.append({"t": t_str, "kind": "ENTRY", "label": "Fake Breakout → PUT"})
                    continue
                # Retest hold → BUY CALL
                if prev_high <= close <= prev_high + retest_buf and (extreme - prev_high) >= 2:
                    pos = _open_position(t_str, "CE", close)
                    if pos:
                        st = "POSITION_OPEN"
                        events.append({"t": t_str, "kind": "ENTRY", "label": "Breakout → Retest CALL"})
                    continue
                if extreme - prev_high > max_ext:
                    st = "IDLE"
                    events.append({"t": t_str, "kind": "ABANDON", "label": "Extended"})

            elif st == "BREAKDOWN_WATCH":
                if close < extreme:
                    extreme = close
                # Fake breakdown → BUY CALL
                if close > prev_low + retest_buf / 2:
                    pos = _open_position(t_str, "CE", close)
                    if pos:
                        st = "POSITION_OPEN"
                        events.append({"t": t_str, "kind": "ENTRY", "label": "Fake Breakdown → CALL"})
                    continue
                # Retest reject → BUY PUT
                if prev_low - retest_buf <= close <= prev_low and (prev_low - extreme) >= 2:
                    pos = _open_position(t_str, "PE", close)
                    if pos:
                        st = "POSITION_OPEN"
                        events.append({"t": t_str, "kind": "ENTRY", "label": "Breakdown → Retest PUT"})
                    continue
                if prev_low - extreme > max_ext:
                    st = "IDLE"
                    events.append({"t": t_str, "kind": "ABANDON", "label": "Extended"})

        total_pnl = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] <= 0)

        if any_real and not any_proxy:
            note = (
                "All trades use REAL minute-level option prices for the resolved "
                f"ITM (offset={itm_offset}) strike. SL/TGT are evaluated on the "
                "option's intra-bar high/low — matches live execution exactly."
            )
        elif any_real and any_proxy:
            note = (
                "Mixed sources: rows tagged REAL use the actual option's minute "
                "candles; rows tagged PROXY fall back to spot-Δ≈1 (option contract "
                "for that day was not queryable)."
            )
        else:
            note = (
                f"Option historical data unavailable for the resolved ITM strike "
                f"(offset={itm_offset}). Using spot-proxy (Δ≈1). PnL is approximate."
            )

        return {
            "status": "ok",
            "sim_date": sim_day.isoformat(),
            "prev_date": prev_day.isoformat(),
            "prev_high": prev_high,
            "prev_low": prev_low,
            "itm_offset": itm_offset,
            "trades": trades,
            "events": events,
            "spot_series": spot_series,
            "summary": {
                "total_trades": len(trades),
                "wins": wins,
                "losses": losses,
                "total_pnl": round(total_pnl, 2),
                "lot_size": self.lot_size,
                "data_source": "REAL" if any_real and not any_proxy else (
                    "MIXED" if any_real else "PROXY"
                ),
            },
            "note": note,
        }

    # ── Persistence ───────────────────────────────────

    def _save_state(self):
        data = {
            "is_active": self.is_active,
            "state": self.state.value,
            "scenario": self.scenario,
            "signal": self.signal,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "prev_high": self.prev_high,
            "prev_low": self.prev_low,
            "levels_for_date": self._levels_for_date.isoformat() if self._levels_for_date else None,
            "spot_price": self.spot_price,
            "spot_extreme": self.spot_extreme,
            "level_crossed": self._level_crossed,
            "signal_type": self.signal_type,
            "entry_reason": self.entry_reason,
            "atm_strike": self.atm_strike,
            "option_symbol": self.option_symbol,
            "option_token": self.option_token,
            "option_ltp": self.option_ltp,
            "fill_price": self.fill_price,
            "sl_price": self.sl_price,
            "target_price": self.target_price,
            "current_ltp": self.current_ltp,
            "entry_order": self.entry_order,
            "sl_order": self.sl_order,
            "target_order": self.target_order,
            "sl_shadow": self.sl_shadow,
            "target_shadow": self.target_shadow,
            "trades_today": self._trades_today,
            "trade_log": self.trade_log[-50:],
            "config": {
                "sl_points": self.sl_points,
                "target_points": self.target_points,
                "lot_size": self.lot_size,
                "strike_interval": self.strike_interval,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "retest_buffer": self.retest_buffer,
                "max_breakout_extension": self.max_breakout_extension,
                "max_trades_per_day": self.max_trades_per_day,
                "allow_reentry": self.allow_reentry,
                "itm_offset": self.itm_offset,
                "gann_target": self.gann_target,
                "gann_count": self.gann_count,
                "index_name": self.index_name,
            },
            "saved_at": datetime.now().isoformat(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.error("S4 save_state failed: %s", exc)

    def _append_trade_history(self, trade: dict):
        try:
            trades = []
            if TRADE_HISTORY_FILE.exists():
                trades = json.loads(TRADE_HISTORY_FILE.read_text())
            trades.append(trade)
            TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRADE_HISTORY_FILE.write_text(json.dumps(trades, indent=2, default=str))
        except Exception as exc:
            logger.error("S4 trade history append failed: %s", exc)

    def restore_state(self) -> bool:
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
        except Exception as exc:
            logger.warning("S4 restore_state read failed: %s", exc)
            return False

        saved_date = data.get("trading_date", "")
        if saved_date != date.today().isoformat():
            logger.info("S4 state file from %s — skipping restore", saved_date)
            return False

        try:
            self.is_active = bool(data.get("is_active", False))
            self.state = State(data.get("state", "IDLE"))
            self.scenario = data.get("scenario", "—")
            self.signal = data.get("signal", "NO_TRADE")
            self._trading_date = date.today()
            self.prev_high = float(data.get("prev_high", 0) or 0)
            self.prev_low = float(data.get("prev_low", 0) or 0)
            lfd = data.get("levels_for_date")
            self._levels_for_date = (
                datetime.strptime(lfd, "%Y-%m-%d").date() if lfd else None
            )
            self.spot_price = float(data.get("spot_price", 0) or 0)
            self.spot_extreme = float(data.get("spot_extreme", 0) or 0)
            self._level_crossed = data.get("level_crossed")
            self.signal_type = data.get("signal_type")
            self.entry_reason = data.get("entry_reason", "")
            self.atm_strike = int(data.get("atm_strike", 0) or 0)
            self.option_symbol = data.get("option_symbol", "")
            self.option_token = int(data.get("option_token", 0) or 0)
            self.option_ltp = float(data.get("option_ltp", 0) or 0)
            self.fill_price = float(data.get("fill_price", 0) or 0)
            self.sl_price = float(data.get("sl_price", 0) or 0)
            self.target_price = float(data.get("target_price", 0) or 0)
            self.current_ltp = float(data.get("current_ltp", 0) or 0)
            self.entry_order = data.get("entry_order")
            self.sl_order = data.get("sl_order")
            self.target_order = data.get("target_order")
            self.sl_shadow = bool(data.get("sl_shadow", True))
            self.target_shadow = bool(data.get("target_shadow", True))
            self._trades_today = int(data.get("trades_today", 0) or 0)
            self.trade_log = list(data.get("trade_log", []))
            cfg = data.get("config", {}) or {}
            if cfg:
                self.apply_config(cfg, save=False)
            return True
        except Exception as exc:
            logger.warning("S4 restore_state apply failed: %s", exc)
            return False

    # ── Status payload ────────────────────────────────

    def get_status(self) -> dict:
        unrealized = 0.0
        if self.state == State.POSITION_OPEN and self.current_ltp > 0 and self.fill_price > 0:
            unrealized = round((self.current_ltp - self.fill_price) * self.lot_size, 2)
        return {
            "is_active": self.is_active,
            "state": self.state.value,
            "scenario": self.scenario,
            "signal": self.signal,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "levels": {
                "prev_high": self.prev_high,
                "prev_low": self.prev_low,
                "source_date": self._levels_for_date.isoformat() if self._levels_for_date else None,
            },
            "spot": {
                "price": self.spot_price,
                "extreme": self.spot_extreme,
                "level_crossed": self._level_crossed,
            },
            "config": {
                "sl_points": self.sl_points,
                "target_points": self.target_points,
                "lot_size": self.lot_size,
                "strike_interval": self.strike_interval,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "retest_buffer": self.retest_buffer,
                "max_breakout_extension": self.max_breakout_extension,
                "max_trades_per_day": self.max_trades_per_day,
                "allow_reentry": self.allow_reentry,
                "itm_offset": self.itm_offset,
                "gann_target": self.gann_target,
                "gann_count": self.gann_count,
                "index_name": self.index_name,
            },
            "trade": {
                "signal_type": self.signal_type,
                "entry_reason": self.entry_reason,
                "atm_strike": self.atm_strike,
                "strike": self.strike,
                "option_symbol": self.option_symbol,
                "option_ltp": self.option_ltp,
                "fill_price": self.fill_price,
                "sl_price": self.sl_price,
                "target_price": self.target_price,
                "current_ltp": self.current_ltp,
                "unrealized_pnl": unrealized,
            },
            "orders": {
                "entry": self.entry_order,
                "sl": self.sl_order,
                "target": self.target_order,
                "sl_shadow": self.sl_shadow,
                "target_shadow": self.target_shadow,
            },
            "trades_today": self._trades_today,
            "trade_log": self.trade_log[-20:],
        }
