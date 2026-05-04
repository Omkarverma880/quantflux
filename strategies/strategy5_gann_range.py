"""
Strategy 5 — Gann Level Range Retest.

Concept
-------
Use the user's Gann level grid (gann_levels.csv) as a **dynamic** range
that updates live with the spot. At any moment the active range is:

    gann_lower  =  largest Gann level <= spot
    gann_upper  =  smallest Gann level >  spot

While the strategy is IDLE the active range floats with the spot — as
the spot crosses a Gann level the range shifts to the next pair, and
the UI/levels stream reflect the change instantly. There is *no*
previous-day or first-hour dependency.

When the spot closes above gann_upper the level is **locked** and the
state machine enters BREAKOUT_WATCH; symmetrically below gann_lower
enters BREAKDOWN_WATCH. From there, retest / fake-out logic, entries,
ITM CE/PE buy, shadow SL/TGT and 15:15 auto square-off are identical
to Strategy 5.

Live signals (NIFTY spot):
    • Breakout  → Retest hold above gann_upper  → BUY ITM CALL
    • Breakdown → Retest reject below gann_lower → BUY ITM PUT
    • Fake breakdown (price reclaims gann_lower) → BUY CALL
    • Fake breakout (price loses gann_upper)     → BUY PUT
    • Inside the active gann range → NO TRADE

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

logger = get_logger("strategy5.gann_range")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
FIRST_HOUR_END = dtime(10, 15)
PRE_CLOSE_EXIT = dtime(15, 15)

GANN_CSV = Path(__file__).resolve().parent.parent / "gann_levels.csv"

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy5_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy5_trades.json"
ORDER_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "order_history.json"


class State(str, Enum):
    IDLE = "IDLE"
    BREAKOUT_WATCH = "BREAKOUT_WATCH"
    BREAKDOWN_WATCH = "BREAKDOWN_WATCH"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy5GannRange:
    """First-hour high/low retest strategy on NIFTY spot."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # ── Config (with sane NIFTY defaults) ──
        self.sl_points = float(config.get("sl_points", 30))
        self.target_points = float(config.get("target_points", 60))
        # Per-lot quantity (NIFTY = 65 by default; broker.get_option_info
        # overwrites this with the live exchange value once we resolve a
        # contract). Order quantity = lots × lot_size.
        self.lot_size = int(config.get("lot_size", 65))
        # Number of lots to trade. Order quantity = lots × lot_size.
        self.lots = max(1, int(config.get("lots", 1)))
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
        # When True (default for S5) only the safer trend-confirmation
        # retest entries fire (Breakout→Retest→CALL, Breakdown→Retest→PUT).
        # Fake-breakout / fake-breakdown reversal entries are skipped.
        self.retest_only = bool(config.get("retest_only", True))
        # ITM offset (in index points) added to strike. For BUY CALL the
        # strike used is (ATM − itm_offset); for BUY PUT it is
        # (ATM + itm_offset). Use 0 to trade ATM.
        self.itm_offset = int(config.get("itm_offset", 100))
        # Gann target: use Gann level grid instead of flat target_points
        self.gann_target = bool(config.get("gann_target", False))
        # How many Gann levels above fill price to target (1, 2, 3, …)
        self.gann_count = max(1, int(config.get("gann_count", 1)))
        self.gann_levels = self._load_gann_levels()
        # Max accepted entry slippage in option ₹ (fill - signal LTP).
        # If breached, the position is flattened immediately to protect
        # against runaway fills in fast markets.
        self.max_entry_slippage = float(config.get("max_entry_slippage", 8))
        # Index symbol — fixed to NIFTY for now per spec.
        self.index_name = str(config.get("index_name", "NIFTY")).upper()

        # ── Reference levels (active Gann pair) ──
        self.gann_upper: float = 0.0
        self.gann_lower: float = 0.0

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

        # Counter used to throttle the (relatively heavy) positions
        # endpoint poll inside _check_exit — see manual-exit detection.
        self._exit_check_count: int = 0

        # ── Misc ──
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None
        self._trades_today: int = 0
        self.trade_log: list[dict] = []
        # Heartbeat: timestamp of the last completed check() call.
        # The frontend uses this to render a stale-loop warning when
        # ticks stop arriving during market hours.
        self.last_check_at: Optional[datetime] = None

    # ── Public controls ───────────────────────────────

    @property
    def quantity(self) -> int:
        """Total order quantity = lots × lot_size (per-lot multiplier)."""
        return max(0, int(self.lots) * int(self.lot_size))

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
        # Anchor Gann pair at start (uses last known spot; if 0 the
        # next live tick will trigger _refresh_active_range to anchor).
        self._anchor_gann_range()
        self._save_state()
        logger.info(
            "Strategy 5 started: SL=%s TGT=%s LOT=%s GANN=[%.2f, %.2f]",
            self.sl_points, self.target_points, self.lot_size,
            self.gann_lower, self.gann_upper,
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 5 stopped")

    def apply_config(self, config: dict, save: bool = True) -> None:
        self.sl_points = float(config.get("sl_points", self.sl_points))
        self.target_points = float(config.get("target_points", self.target_points))
        self.lot_size = int(config.get("lot_size", self.lot_size))
        self.lots = max(1, int(config.get("lots", self.lots)))
        self.strike_interval = int(config.get("strike_interval", self.strike_interval))
        self.sl_proximity = float(config.get("sl_proximity", self.sl_proximity))
        self.target_proximity = float(config.get("target_proximity", self.target_proximity))
        self.retest_buffer = float(config.get("retest_buffer", self.retest_buffer))
        self.max_breakout_extension = float(config.get("max_breakout_extension", self.max_breakout_extension))
        self.max_trades_per_day = int(config.get("max_trades_per_day", self.max_trades_per_day))
        self.allow_reentry = bool(config.get("allow_reentry", self.allow_reentry))
        self.retest_only = bool(config.get("retest_only", self.retest_only))
        self.itm_offset = int(config.get("itm_offset", self.itm_offset))
        self.gann_target = bool(config.get("gann_target", self.gann_target))
        self.gann_count = max(1, int(config.get("gann_count", self.gann_count)))
        self.max_entry_slippage = float(config.get("max_entry_slippage", self.max_entry_slippage))
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
                "S5 orphaned %s from %s — recording BROKER_SQUAREOFF",
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
                "pnl": round(((self.current_ltp or self.fill_price) - self.fill_price) * self.quantity, 2),
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
        self.gann_upper = 0.0
        self.gann_lower = 0.0
        self._save_state()
        logger.info("S5 new trading day %s — reset to IDLE", today)

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

    def _compute_gann_range(self, spot: float) -> tuple[float, float]:
        """Return the active (lower, upper) Gann pair for the given spot.

        lower = largest gann level <= spot (or spot - 50 fallback)
        upper = smallest gann level >  spot (or spot + 50 fallback)
        """
        if not self.gann_levels:
            return (spot - 50.0, spot + 50.0)
        lvls = self.gann_levels
        idx_up = bisect.bisect_right(lvls, spot)
        upper = float(lvls[idx_up]) if idx_up < len(lvls) else float(lvls[-1] + 50)
        idx_low = bisect.bisect_left(lvls, spot)
        # idx_low points to first level >= spot; we want the one strictly below
        lower = float(lvls[idx_low - 1]) if idx_low > 0 else float(lvls[0] - 50)
        # Guard: if spot equals a gann level, bisect_left would put it at that
        # index, leaving lower as the previous level — that's correct.
        return (lower, upper)

    def _refresh_active_range(self) -> bool:
        """Anchor the Gann pair if we don't have one yet.

        The pair is HELD CONSTANT while we are IDLE so that a real
        breakout / breakdown can be detected when the spot closes
        outside it. Re-anchoring happens explicitly via
        `_anchor_gann_range()` on day-open and after a watch is
        abandoned or a trade closes (with allow_reentry).
        Returns True if we just set the initial anchor.
        """
        if self.spot_price <= 0:
            return False
        if self.gann_lower > 0 and self.gann_upper > 0:
            return False
        # First-time anchor (day start, fresh state)
        return self._anchor_gann_range()

    def _anchor_gann_range(self) -> bool:
        """Re-compute and lock the Gann pair around the current spot."""
        if self.spot_price <= 0:
            return False
        new_lo, new_hi = self._compute_gann_range(self.spot_price)
        if new_lo == self.gann_lower and new_hi == self.gann_upper:
            return False
        logger.info(
            "S5 Gann pair anchored: lower=%.2f upper=%.2f (spot=%.2f)",
            new_lo, new_hi, self.spot_price,
        )
        self.gann_lower = new_lo
        self.gann_upper = new_hi
        return True

    def fetch_levels(self, force: bool = False) -> dict:
        """Compute the live Gann range for the current spot.

        Unlike S4 there is no historical lookback — the range is derived
        on-demand from the configured Gann grid + current spot LTP. The
        `force` flag is kept for API compatibility but is a no-op.
        """
        spot = self.spot_price
        if spot <= 0:
            try:
                ltp_map = self.broker.get_ltp(["NSE:NIFTY 50"])
                spot = float(ltp_map.get("NSE:NIFTY 50", 0) or 0)
                if spot > 0:
                    self.spot_price = spot
            except Exception as exc:
                logger.warning("S5 spot fetch for levels failed: %s", exc)
        if spot <= 0:
            return {"status": "error", "message": "No spot price available — cannot compute Gann range"}

        # Only mutate when IDLE; otherwise just report the locked pair
        if self.state == State.IDLE:
            if force or self.gann_lower <= 0 or self.gann_upper <= 0:
                self.gann_lower, self.gann_upper = self._compute_gann_range(spot)
                self._save_state()

        return self._levels_payload()

    def _levels_payload(self, source_date: Optional[date] = None) -> dict:
        return {
            "status": "ok",
            "gann_upper": self.gann_upper,
            "gann_lower": self.gann_lower,
            "spot": self.spot_price,
            "locked": self.state != State.IDLE,
            "source": "gann_csv",
            "gann_count_total": len(self.gann_levels),
            "for_date": (self._trading_date or date.today()).isoformat(),
        }

    def get_intraday_series(self, target_day: Optional[date] = None) -> list[dict]:
        """Return today's NIFTY 50 minute-candle close series since 9:15
        until now. Used to seed the live chart with real intraday data.
        """
        token = self._resolve_index_token()
        if not token:
            return []
        day = target_day or date.today()
        from_dt = datetime.combine(day, MARKET_OPEN)
        now = datetime.now()
        if day == date.today() and now.time() < MARKET_CLOSE:
            to_dt = now
        else:
            to_dt = datetime.combine(day, MARKET_CLOSE)
        try:
            candles = self.broker.get_historical_data(
                instrument_token=token,
                from_date=from_dt,
                to_date=to_dt,
                interval="minute",
            ) or []
        except Exception as exc:
            logger.warning("S5 intraday fetch failed: %s", exc)
            return []
        out: list[dict] = []
        for c in candles:
            ts = c.get("date")
            if hasattr(ts, "strftime"):
                t_str = ts.strftime("%H:%M:%S")
            else:
                t_str = str(ts)[-8:] if ts else ""
            out.append({"t": t_str, "y": float(c.get("close", 0))})
        return out

    # ── Main check ────────────────────────────────────

    def check(self, spot_price: float) -> dict:
        # Allow management of an open position even after a manual stop —
        # otherwise SL/TGT promotion and 15:15 auto square-off would be
        # silently skipped (the bug seen in live testing on 04-May).
        if not self.is_active and self.state != State.POSITION_OPEN:
            return self.get_status()

        if not self._check_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self.last_check_at = datetime.now()
            self._check_day_reset()

            if spot_price > 0:
                self.spot_price = spot_price

            # Refresh the active Gann pair from live spot. While IDLE the
            # range floats with the spot; once we cross a level and lock
            # into a watch state, the boundary is held until the trade
            # cycle completes.
            self._refresh_active_range()

            # Auto square-off
            if self.state == State.POSITION_OPEN and datetime.now().time() >= PRE_CLOSE_EXIT:
                logger.info("S5 auto square-off triggered")
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
        if self.gann_upper <= 0 or self.gann_lower <= 0 or self.spot_price <= 0:
            self.scenario = "Loading levels…"
            return
        # Trades-per-day cap
        if self._trades_today >= self.max_trades_per_day:
            self.scenario = "Max trades reached"
            return

        # No time gating — Gann levels are static reference grid; the
        # active pair is recomputed each tick from the spot. We act on
        # the first close beyond the active pair.

        if self.spot_price > self.gann_upper:
            self.state = State.BREAKOUT_WATCH
            self._level_crossed = "ABOVE_HIGH"
            self.spot_extreme = self.spot_price
            self.scenario = "Breakout — waiting for retest"
            self.signal = "NO_TRADE"
            logger.info("S5 BREAKOUT_WATCH @ spot=%.2f (high=%.2f)", self.spot_price, self.gann_upper)
        elif self.spot_price < self.gann_lower:
            self.state = State.BREAKDOWN_WATCH
            self._level_crossed = "BELOW_LOW"
            self.spot_extreme = self.spot_price
            self.scenario = "Breakdown — waiting for retest"
            self.signal = "NO_TRADE"
            logger.info("S5 BREAKDOWN_WATCH @ spot=%.2f (low=%.2f)", self.spot_price, self.gann_lower)
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
        if self.state == State.BREAKOUT_WATCH and self.spot_price < self.gann_lower:
            logger.info(
                "S5 dynamic flip BREAKOUT→BREAKDOWN (spot=%.2f cut gann_lower=%.2f)",
                self.spot_price, self.gann_lower,
            )
            self.state = State.BREAKDOWN_WATCH
            self._level_crossed = "BELOW_LOW"
            self.spot_extreme = self.spot_price
            self.scenario = "Flipped → Breakdown watch"
            self.signal = "NO_TRADE"
        elif self.state == State.BREAKDOWN_WATCH and self.spot_price > self.gann_upper:
            logger.info(
                "S5 dynamic flip BREAKDOWN→BREAKOUT (spot=%.2f cut gann_upper=%.2f)",
                self.spot_price, self.gann_upper,
            )
            self.state = State.BREAKOUT_WATCH
            self._level_crossed = "ABOVE_HIGH"
            self.spot_extreme = self.spot_price
            self.scenario = "Flipped → Breakout watch"
            self.signal = "NO_TRADE"

        if self.state == State.BREAKOUT_WATCH:
            # Track extreme above gann_upper
            if self.spot_price > self.spot_extreme:
                self.spot_extreme = self.spot_price

            # Reclaim abandon (retest_only): if the spot has dropped back
            # well below gann_upper the breakout thesis is invalidated.
            # Without this, the strategy would sit in BREAKOUT_WATCH for
            # hours and fire a stale BUY CALL on any later wick that
            # touched the level — the mirror-image of the bad PUT trade
            # observed on 04-May.
            if self.retest_only and self.spot_price < self.gann_upper - self.retest_buffer / 2:
                logger.info(
                    "S5 BREAKOUT_WATCH abandoned — spot %.2f reclaimed below gann_upper %.2f (retest_only)",
                    self.spot_price, self.gann_upper,
                )
                self.scenario = "Reclaim — re-anchored"
                self.signal = "NO_TRADE"
                self.state = State.IDLE
                self._level_crossed = None
                self.spot_extreme = 0.0
                self._anchor_gann_range()
                return

            # Fake breakout: price came back below gann_upper → BUY PUT
            # (skipped when retest_only is enabled)
            if not self.retest_only and self.spot_price < self.gann_upper - self.retest_buffer / 2:
                self.scenario = "Fake Breakout → PUT"
                self.signal = "BUY_PUT"
                self.entry_reason = (
                    f"Spot reclaimed below gann_upper {self.gann_upper:.2f} "
                    f"after extension to {self.spot_extreme:.2f}"
                )
                self._fire_entry("PE")
                return

            # Retest hold: price pulled back near level but stayed above → BUY CALL
            if (
                self.gann_upper <= self.spot_price <= self.gann_upper + self.retest_buffer
                and self.spot_extreme - self.gann_upper >= 2  # at least 2pt extension
            ):
                self.scenario = "Breakout → Retest → CALL"
                self.signal = "BUY_CALL"
                self.entry_reason = (
                    f"Retest hold above {self.gann_upper:.2f} "
                    f"(peak {self.spot_extreme:.2f})"
                )
                self._fire_entry("CE")
                return

            # If extension goes very far without retest, abandon and
            # re-anchor the Gann pair to the new band the spot now sits in.
            if self.spot_extreme - self.gann_upper > self.max_breakout_extension:
                self.scenario = "Extended — re-anchored"
                self.signal = "NO_TRADE"
                self.state = State.IDLE
                self._level_crossed = None
                self.spot_extreme = 0.0
                self._anchor_gann_range()
                return

        elif self.state == State.BREAKDOWN_WATCH:
            if self.spot_price < self.spot_extreme:
                self.spot_extreme = self.spot_price

            # Reclaim abandon (retest_only): if the spot has rallied back
            # well above gann_lower the breakdown thesis is invalidated.
            # This was the exact bug seen on 04-May — spot dipped to
            # 24019 (below gann_lower 24025), then bounced into the
            # range and stayed bullish; hours later a single wick to
            # ~24025 fired a BUY PUT against a clear up-move.
            if self.retest_only and self.spot_price > self.gann_lower + self.retest_buffer / 2:
                logger.info(
                    "S5 BREAKDOWN_WATCH abandoned — spot %.2f reclaimed above gann_lower %.2f (retest_only)",
                    self.spot_price, self.gann_lower,
                )
                self.scenario = "Reclaim — re-anchored"
                self.signal = "NO_TRADE"
                self.state = State.IDLE
                self._level_crossed = None
                self.spot_extreme = 0.0
                self._anchor_gann_range()
                return

            # Fake breakdown: price reclaimed above gann_lower → BUY CALL
            # (skipped when retest_only is enabled)
            if not self.retest_only and self.spot_price > self.gann_lower + self.retest_buffer / 2:
                self.scenario = "Fake Breakdown → CALL"
                self.signal = "BUY_CALL"
                self.entry_reason = (
                    f"Spot reclaimed above gann_lower {self.gann_lower:.2f} "
                    f"after dip to {self.spot_extreme:.2f}"
                )
                self._fire_entry("CE")
                return

            # Retest reject: price pulled back near level but stayed below → BUY PUT
            if (
                self.gann_lower - self.retest_buffer <= self.spot_price <= self.gann_lower
                and self.gann_lower - self.spot_extreme >= 2
            ):
                self.scenario = "Breakdown → Retest → PUT"
                self.signal = "BUY_PUT"
                self.entry_reason = (
                    f"Retest reject below {self.gann_lower:.2f} "
                    f"(trough {self.spot_extreme:.2f})"
                )
                self._fire_entry("PE")
                return

            if self.gann_lower - self.spot_extreme > self.max_breakout_extension:
                self.scenario = "Extended — re-anchored"
                self.signal = "NO_TRADE"
                self.state = State.IDLE
                self._level_crossed = None
                self.spot_extreme = 0.0
                self._anchor_gann_range()
                return

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
            logger.error("S5 no %s option at strike %s (ATM=%s, offset=%s)",
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
            logger.error("S5 LTP fetch failed: %s", exc)
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
                quantity=self.quantity,
                order_type=OrderType.MARKET,
                product=ProductType.MIS,
                tag="S5ENTRY",
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
                logger.info("S5 entry order placed: %s", resp.order_id)
        except Exception as exc:
            logger.error("S5 entry order failed: %s", exc)
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
        if self.spot_price > 0 and self.signal_type and self.gann_upper > 0 and self.gann_lower > 0:
            flipped = (
                (self.signal_type == "CE" and self.spot_price < self.gann_lower)
                or (self.signal_type == "PE" and self.spot_price > self.gann_upper)
            )
            if flipped:
                logger.info(
                    "S5 spot flipped against pending %s entry — cancelling order",
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
                    logger.info("S5 entry stale (%.0fs) — cancelling", elapsed)
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
                        # ── P3: entry slippage guard ──
                        ref = float(self.option_ltp or 0)
                        slip = self.fill_price - ref
                        if (
                            ref > 0
                            and self.max_entry_slippage > 0
                            and slip > self.max_entry_slippage
                        ):
                            logger.warning(
                                "S5 entry slippage breach: ref=%.2f fill=%.2f slip=%.2f > max=%.2f — flattening",
                                ref, self.fill_price, slip, self.max_entry_slippage,
                            )
                            self._slippage_flatten(ref, slip)
                            return
                        self._on_entry_filled()
                    elif status in ("CANCELLED", "REJECTED"):
                        self.entry_order["status"] = status
                        self.state = State.COMPLETED
                        self._save_state()
                        logger.warning("S5 entry %s", status)
                    break
        except Exception as exc:
            logger.error("S5 fill check failed: %s", exc)

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
            "S5 position open. Entry=%.2f SL=%.2f TGT=%.2f",
            self.fill_price, self.sl_price, self.target_price,
        )

    # ── Exit handling ─────────────────────────────────

    def _check_exit(self):
        # Resilient LTP read: fall back to the most-recent cached LTP
        # when the broker quote endpoint flakes out — without this, a
        # single API hiccup would silently skip SL/TGT promotion (the
        # bug seen on 04-May).
        ltp = 0.0
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            ltp = float(ltp_map.get(f"NFO:{self.option_symbol}", 0) or 0)
        except Exception as exc:
            logger.warning(
                "S5 exit-LTP fetch failed (%s) — falling back to cached %.2f",
                exc, self.current_ltp,
            )
        if ltp <= 0:
            ltp = float(self.current_ltp or 0)
        if ltp <= 0:
            return
        self.current_ltp = ltp

        # ── Manual-exit detection ──
        # If the user closed the position via Kite Web / mobile (or the
        # broker squared it off externally), the net qty for our option
        # symbol drops to zero. Detect that and complete the trade so
        # the UI doesn't keep showing "Position Open" forever.
        if (
            not settings.PAPER_TRADE
            and self.option_symbol
            and self.fill_price > 0
        ):
            self._exit_check_count = (self._exit_check_count + 1) % 10
            if self._exit_check_count == 0:
                try:
                    positions = self.broker.get_positions()
                    matched = next(
                        (p for p in positions if p.tradingsymbol == self.option_symbol),
                        None,
                    )
                    if matched is not None and int(matched.quantity) == 0:
                        logger.warning(
                            "S5 manual exit detected (%s qty=0) — closing trade",
                            self.option_symbol,
                        )
                        self._cancel_order(self.sl_order)
                        self._cancel_order(self.target_order)
                        self._complete_trade("MANUAL_EXIT", ltp)
                        return
                except Exception as exc:
                    logger.debug("S5 position poll failed: %s", exc)

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
                        quantity=self.quantity, order_type=OrderType.LIMIT,
                        product=ProductType.MIS, price=exit_price, tag="S5SL",
                    ))
                    self._complete_trade("SL_HIT", ltp)
                    return
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.SL_M,
                    product=ProductType.MIS, trigger_price=self.sl_price, tag="S5SL",
                ))
                self.sl_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "price": self.sl_price,
                    "timestamp": datetime.now().isoformat(),
                }
                self._save_state()
            except Exception as exc:
                logger.error("S5 SL placement failed: %s", exc)
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
                        quantity=self.quantity, order_type=OrderType.LIMIT,
                        product=ProductType.MIS, price=exit_price, tag="S5TGT",
                    ))
                    self._complete_trade("TARGET_HIT", ltp)
                    return
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.LIMIT,
                    product=ProductType.MIS, price=self.target_price, tag="S5TGT",
                ))
                self.target_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "price": self.target_price,
                    "timestamp": datetime.now().isoformat(),
                }
                self._save_state()
            except Exception as exc:
                logger.error("S5 target placement failed: %s", exc)
                self.target_shadow = True

    def _slippage_flatten(self, ref_ltp: float, slip: float):
        """Immediately exit a position whose entry slipped beyond the
        configured guard. Records a SLIPPAGE_REJECT trade and stops the
        strategy for the day."""
        # Cancel pending shadow legs (paranoia — they shouldn't exist yet
        # because _on_entry_filled never ran, but guard anyway).
        self._cancel_order(self.sl_order)
        self._cancel_order(self.target_order)
        if not settings.PAPER_TRADE and self.option_symbol and self.lot_size > 0:
            try:
                self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.MARKET,
                    product=ProductType.MIS,
                    tag="S5SLIP",
                ))
            except Exception as exc:
                logger.error("S5 slippage-flatten failed: %s", exc)

        # Best-effort exit price: market sell ≈ ref LTP (option may dip on impact).
        exit_price = ref_ltp
        pnl = round((exit_price - self.fill_price) * self.quantity, 2)
        trade = {
            "date": (self._trading_date or date.today()).isoformat(),
            "signal": self.signal_type,
            "scenario": self.scenario,
            "option": self.option_symbol,
            "atm_strike": self.atm_strike,
            "strike": self.strike,
            "entry_price": self.fill_price,
            "exit_type": "SLIPPAGE_REJECT",
            "exit_price": exit_price,
            "exit_time": datetime.now().strftime("%H:%M:%S"),
            "lot_size": self.lot_size,
            "pnl": pnl,
            "slippage": round(slip, 2),
            "ref_ltp": round(ref_ltp, 2),
            "fill_price": self.fill_price,
            "timestamp": datetime.now().isoformat(),
        }
        self.trade_log.append(trade)
        self._trades_today += 1

        self.fill_price = 0.0
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True
        self.is_active = False  # stop for the day to avoid runaway losses
        self.state = State.COMPLETED
        self.scenario = "Stopped — entry slippage breach"
        self._save_state()

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
                    quantity=self.quantity, order_type=OrderType.LIMIT,
                    product=ProductType.MIS,
                    price=max(0.05, round(exit_price * 0.90, 2)),
                    tag="S5SQOFF",
                ))
            except Exception as exc:
                logger.error("S5 squareoff failed: %s", exc)
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
            logger.warning("S5 cancel failed: %s", exc)

    def _complete_trade(self, exit_type: str, exit_price: float):
        pnl = (exit_price - self.fill_price) * self.quantity
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
        logger.info("S5 trade done: %s | Entry=%.2f Exit=%.2f PnL=%.2f",
                    exit_type, self.fill_price, exit_price, pnl)

        # Reset trade-specific fields
        self.fill_price = 0.0
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True

        # Re-entry decision. Unlike S4, S5 honors `allow_reentry` even
        # after SL — Gann levels are static reference points so a fresh
        # band anchor around the current spot is a valid new setup.
        # The `max_trades_per_day` cap still bounds total exposure.
        if self.allow_reentry and self._trades_today < self.max_trades_per_day:
            self.state = State.IDLE
            self._level_crossed = None
            self.spot_extreme = 0.0
            self.signal_type = None
            self.signal = "NO_TRADE"
            self.scenario = "Re-armed after " + exit_type
            # Re-anchor band around the current spot (post-exit price).
            self._anchor_gann_range()
            logger.info(
                "S5 re-armed after %s (trades=%d/%d) — new band [%.2f, %.2f]",
                exit_type, self._trades_today, self.max_trades_per_day,
                self.gann_lower, self.gann_upper,
            )
        else:
            # Hard stop: SL with no reentry allowed, or trade cap reached
            if exit_type == "SL_HIT" and not self.allow_reentry:
                self.is_active = False
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
        change ≈ |spot move| × delta (delta=1.0 for ITM, capped at SL/TGT
        index points). This produces a realistic *signal-quality* backtest.

        Unlike S4 there is no previous-day dependency: the active Gann
        pair is recomputed from each minute close while we are IDLE.
        """
        anchor = target_date or date.today()
        token = self._resolve_index_token()
        if not token:
            return {"status": "error", "message": "Could not resolve NIFTY 50 token"}
        if not self.gann_levels:
            return {"status": "error", "message": "Gann levels CSV not loaded — cannot backtest S5"}

        # Walk back up to 10 calendar days for a session that has minute data.
        for offset in range(0, 10):
            sim_day = anchor - timedelta(days=offset)
            if sim_day.weekday() >= 5:
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
            return self._run_backtest_sim(sim_day, tcandles)

        return {"status": "error", "message": "No suitable historical session found within 10 days"}

    def backtest_multi(self, days: int = 30) -> dict:
        """Run `backtest()` over the last N trading days and aggregate.

        Skips weekends. Each individual day uses the same spot-proxy
        sim engine. Cap is enforced at the route layer (default 30, hard
        max 60) — but we also clamp here defensively.
        """
        days = max(1, min(int(days), 60))
        today = date.today()
        per_day: list[dict] = []
        cursor = today
        scanned = 0
        while len(per_day) < days and scanned < days * 3:  # tolerate weekends/holidays
            scanned += 1
            cursor = cursor - timedelta(days=1)
            if cursor.weekday() >= 5:
                continue
            res = self.backtest(target_date=cursor)
            if res.get("status") != "ok":
                continue
            sim_day_iso = res.get("sim_date")
            if any(d["date"] == sim_day_iso for d in per_day):
                # backtest() walks back up to 10 days — if it landed on a
                # date we already covered, skip to avoid duplicates.
                continue
            summary = res.get("summary", {})
            per_day.append({
                "date": sim_day_iso,
                "trades": summary.get("total_trades", 0),
                "wins": summary.get("wins", 0),
                "losses": summary.get("losses", 0),
                "pnl": summary.get("total_pnl", 0.0),
            })

        per_day.sort(key=lambda d: d["date"])

        total_trades = sum(d["trades"] for d in per_day)
        total_wins = sum(d["wins"] for d in per_day)
        total_losses = sum(d["losses"] for d in per_day)
        total_pnl = round(sum(d["pnl"] for d in per_day), 2)
        win_rate = round((total_wins / total_trades * 100), 2) if total_trades else 0.0
        avg_pnl_per_day = round(total_pnl / len(per_day), 2) if per_day else 0.0

        # Equity curve + max drawdown
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for d in per_day:
            equity += d["pnl"]
            peak = max(peak, equity)
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        # Max consecutive losing days
        max_consec = 0
        run = 0
        for d in per_day:
            if d["pnl"] < 0:
                run += 1
                max_consec = max(max_consec, run)
            else:
                run = 0

        # Expectancy per trade (avg PnL per trade)
        expectancy = round(total_pnl / total_trades, 2) if total_trades else 0.0

        return {
            "status": "ok",
            "requested_days": days,
            "covered_days": len(per_day),
            "summary": {
                "total_trades": total_trades,
                "wins": total_wins,
                "losses": total_losses,
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "avg_pnl_per_day": avg_pnl_per_day,
                "max_drawdown": round(max_dd, 2),
                "max_consecutive_losses": max_consec,
                "expectancy": expectancy,
                "lot_size": self.lot_size,
            },
            "daily": per_day,
            "note": (
                "Aggregated spot-proxy backtest. Δ=1 assumption — actual "
                "option PnL will differ slightly. Use for signal-quality "
                "validation across the window, not exact PnL."
            ),
        }

    def _run_backtest_sim(
        self,
        sim_day: date,
        candles: list[dict],
    ) -> dict:
        """Pure-python replay using spot-Δ≈1 proxy with dynamic Gann range.

        While in IDLE we recompute the active (gann_lower, gann_upper)
        pair from the closing price of each minute candle. As soon as a
        bar closes outside the active pair, the boundary is locked and
        the state machine enters BREAKOUT/BREAKDOWN_WATCH at that level
        — identical to live behavior.
        """
        retest_buf = float(self.retest_buffer)
        max_ext = float(self.max_breakout_extension)
        sl_pts = float(self.sl_points)
        tgt_pts = float(self.target_points)
        max_trades = int(self.max_trades_per_day)
        itm_offset = int(self.itm_offset)
        retest_only = bool(self.retest_only)
        lvls = self.gann_levels

        st = "IDLE"
        side = None  # CE / PE
        extreme = 0.0
        trades = []
        events = []
        entry_spot = 0.0
        trades_done = 0
        spot_series = []
        gann_band_series = []   # per-bar active (lower, upper) trail
        # Anchored Gann pair. Held constant while IDLE so a real
        # breakout can be detected. Re-anchored only when (a) the day
        # opens, (b) a watch is abandoned (price extended), or (c) a
        # trade closes and reentry is allowed.
        locked_upper = 0.0
        locked_lower = 0.0
        initial_open = float(candles[0]["open"]) if candles else 0.0

        def _gann_pair(spot: float) -> tuple[float, float]:
            if not lvls:
                return (spot - 50.0, spot + 50.0)
            i_up = bisect.bisect_right(lvls, spot)
            up = float(lvls[i_up]) if i_up < len(lvls) else float(lvls[-1] + 50)
            i_lo = bisect.bisect_left(lvls, spot)
            lo = float(lvls[i_lo - 1]) if i_lo > 0 else float(lvls[0] - 50)
            return (lo, up)

        # Anchor at day open
        if initial_open > 0:
            locked_lower, locked_upper = _gann_pair(initial_open)

        def _synth_symbol(strike: int, opt: str) -> str:
            return f"{self.index_name} {int(strike)} {opt}"

        def _itm_strike(spot: float, opt: str) -> int:
            atm = self._calc_atm(spot)
            return int(atm - itm_offset) if opt == "CE" else int(atm + itm_offset)

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

        def _record_trade(t_str: str, exit_spot: float, exit_type: str, label: str):
            if side == "CE":
                move = exit_spot - entry_spot
            else:
                move = entry_spot - exit_spot
            pnl = move * self.quantity
            strike_ = _itm_strike(entry_spot, side)
            trades.append({
                "time": t_str,
                "side": side,
                "strike": strike_,
                "option_symbol": _synth_symbol(strike_, side),
                "entry": round(entry_spot, 2),
                "exit": round(exit_spot, 2),
                "exit_type": exit_type,
                "pnl": round(pnl, 2),
                "gann_upper": round(locked_upper, 2),
                "gann_lower": round(locked_lower, 2),
            })
            events.append({
                "t": t_str,
                "kind": exit_type if exit_type in ("SL", "TGT") else "EXIT",
                "label": label,
            })

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

            # Auto square-off
            if st == "POSITION_OPEN" and t_ >= PRE_CLOSE_EXIT:
                _record_trade(t_str, close, "AUTO_SQUAREOFF", "Auto SqOff")
                st = "COMPLETED"; side = None
                gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})
                continue

            # Position management
            if st == "POSITION_OPEN":
                if side == "CE":
                    adverse = entry_spot - low
                    favor = high - entry_spot
                    if adverse >= sl_pts:
                        _record_trade(t_str, entry_spot - sl_pts, "SL_HIT", "SL")
                        trades_done += 1
                        if trades_done >= max_trades or not self.allow_reentry:
                            st = "COMPLETED"
                        else:
                            st = "IDLE"
                            locked_lower, locked_upper = _gann_pair(close)
                        side = None
                        gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})
                        continue
                    if favor >= tgt_pts:
                        _record_trade(t_str, entry_spot + tgt_pts, "TARGET_HIT", "TGT")
                        trades_done += 1
                        if trades_done >= max_trades or not self.allow_reentry:
                            st = "COMPLETED"
                        else:
                            st = "IDLE"
                            # Re-anchor band to wherever the spot now sits
                            locked_lower, locked_upper = _gann_pair(close)
                        side = None
                        gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})
                        continue
                else:  # PE
                    adverse = high - entry_spot
                    favor = entry_spot - low
                    if adverse >= sl_pts:
                        _record_trade(t_str, entry_spot + sl_pts, "SL_HIT", "SL")
                        trades_done += 1
                        if trades_done >= max_trades or not self.allow_reentry:
                            st = "COMPLETED"
                        else:
                            st = "IDLE"
                            locked_lower, locked_upper = _gann_pair(close)
                        side = None
                        gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})
                        continue
                    if favor >= tgt_pts:
                        _record_trade(t_str, entry_spot - tgt_pts, "TARGET_HIT", "TGT")
                        trades_done += 1
                        if trades_done >= max_trades or not self.allow_reentry:
                            st = "COMPLETED"
                        else:
                            st = "IDLE"
                            locked_lower, locked_upper = _gann_pair(close)
                        side = None
                        gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})
                        continue

            if st == "COMPLETED":
                gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})
                continue

            # IDLE: band is anchored (locked at open / re-anchor). Detect
            # a real breakout/breakdown when spot closes outside it.
            if st == "IDLE":
                # Safety: if no anchor yet (e.g. open was 0), anchor now.
                if locked_upper <= 0 or locked_lower <= 0:
                    locked_lower, locked_upper = _gann_pair(close)
                gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})
                if trades_done >= max_trades:
                    st = "COMPLETED"; continue
                if close > locked_upper:
                    st = "BREAKOUT_WATCH"; extreme = close
                    events.append({"t": t_str, "kind": "WATCH", "label": f"Breakout {locked_upper:.0f}"})
                elif close < locked_lower:
                    st = "BREAKDOWN_WATCH"; extreme = close
                    events.append({"t": t_str, "kind": "WATCH", "label": f"Breakdown {locked_lower:.0f}"})
                continue

            # WATCH states: boundary held; render constant locked pair
            gann_band_series.append({"t": t_str, "lo": locked_lower, "up": locked_upper, "locked": True})

            # Dynamic flip vs locked boundary
            if st == "BREAKOUT_WATCH" and close < locked_lower:
                st = "BREAKDOWN_WATCH"; extreme = close
                events.append({"t": t_str, "kind": "FLIP", "label": "→ Breakdown"})
            elif st == "BREAKDOWN_WATCH" and close > locked_upper:
                st = "BREAKOUT_WATCH"; extreme = close
                events.append({"t": t_str, "kind": "FLIP", "label": "→ Breakout"})

            # Watch → entries
            if st == "BREAKOUT_WATCH":
                if close > extreme:
                    extreme = close
                if not retest_only and close < locked_upper - retest_buf / 2:
                    side = "PE"; entry_spot = close; st = "POSITION_OPEN"
                    events.append({"t": t_str, "kind": "ENTRY", "label": "Fake Breakout → PUT"})
                    continue
                if locked_upper <= close <= locked_upper + retest_buf and (extreme - locked_upper) >= 2:
                    side = "CE"; entry_spot = close; st = "POSITION_OPEN"
                    events.append({"t": t_str, "kind": "ENTRY", "label": "Breakout → Retest CALL"})
                    continue
                if extreme - locked_upper > max_ext:
                    st = "IDLE"
                    # Re-anchor: spot has extended into a new band
                    locked_lower, locked_upper = _gann_pair(close)
                    events.append({"t": t_str, "kind": "ABANDON", "label": "Extended"})

            elif st == "BREAKDOWN_WATCH":
                if close < extreme:
                    extreme = close
                if not retest_only and close > locked_lower + retest_buf / 2:
                    side = "CE"; entry_spot = close; st = "POSITION_OPEN"
                    events.append({"t": t_str, "kind": "ENTRY", "label": "Fake Breakdown → CALL"})
                    continue
                if locked_lower - retest_buf <= close <= locked_lower and (locked_lower - extreme) >= 2:
                    side = "PE"; entry_spot = close; st = "POSITION_OPEN"
                    events.append({"t": t_str, "kind": "ENTRY", "label": "Breakdown → Retest PUT"})
                    continue
                if locked_lower - extreme > max_ext:
                    st = "IDLE"
                    locked_lower, locked_upper = _gann_pair(close)
                    events.append({"t": t_str, "kind": "ABANDON", "label": "Extended"})

        total_pnl = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] <= 0)

        # Initial / final Gann pair for top-level summary cards
        first_band = gann_band_series[0] if gann_band_series else None
        last_band = gann_band_series[-1] if gann_band_series else None

        return {
            "status": "ok",
            "sim_date": sim_day.isoformat(),
            "itm_offset": itm_offset,
            "gann_count_total": len(lvls),
            "gann_upper": (first_band["up"] if first_band else 0.0),
            "gann_lower": (first_band["lo"] if first_band else 0.0),
            "final_gann_upper": (last_band["up"] if last_band else 0.0),
            "final_gann_lower": (last_band["lo"] if last_band else 0.0),
            "trades": trades,
            "events": events,
            "spot_series": spot_series,
            "gann_band_series": gann_band_series,
            "summary": {
                "total_trades": len(trades),
                "wins": wins,
                "losses": losses,
                "total_pnl": round(total_pnl, 2),
                "lot_size": self.lot_size,
            },
            "note": (
                "Backtest assumes option Δ=1 (spot move ≈ option move). "
                "Gann pair is anchored at day open and held until a "
                "watch is abandoned or a trade closes — only then it "
                "re-anchors to the new band. Live engine buys real "
                "ITM CE/PE; actual option PnL will differ slightly "
                "due to theta/IV."
            ),
        }

    # ── Persistence ───────────────────────────────────

    def _save_state(self):
        data = {
            "is_active": self.is_active,
            "state": self.state.value,
            "scenario": self.scenario,
            "signal": self.signal,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "gann_upper": self.gann_upper,
            "gann_lower": self.gann_lower,
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
                "lots": self.lots,
                "quantity": self.quantity,
                "strike_interval": self.strike_interval,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "retest_buffer": self.retest_buffer,
                "max_breakout_extension": self.max_breakout_extension,
                "max_trades_per_day": self.max_trades_per_day,
                "allow_reentry": self.allow_reentry,
                "retest_only": self.retest_only,
                "itm_offset": self.itm_offset,
                "gann_target": self.gann_target,
                "gann_count": self.gann_count,
                "max_entry_slippage": self.max_entry_slippage,
                "index_name": self.index_name,
            },
            "saved_at": datetime.now().isoformat(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.error("S5 save_state failed: %s", exc)

    def _append_trade_history(self, trade: dict):
        try:
            trades = []
            if TRADE_HISTORY_FILE.exists():
                trades = json.loads(TRADE_HISTORY_FILE.read_text())
            trades.append(trade)
            TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRADE_HISTORY_FILE.write_text(json.dumps(trades, indent=2, default=str))
        except Exception as exc:
            logger.error("S5 trade history append failed: %s", exc)

    def restore_state(self) -> bool:
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
        except Exception as exc:
            logger.warning("S5 restore_state read failed: %s", exc)
            return False

        saved_date = data.get("trading_date", "")
        if saved_date != date.today().isoformat():
            logger.info("S5 state file from %s — skipping restore", saved_date)
            return False

        try:
            self.is_active = bool(data.get("is_active", False))
            self.state = State(data.get("state", "IDLE"))
            self.scenario = data.get("scenario", "—")
            self.signal = data.get("signal", "NO_TRADE")
            self._trading_date = date.today()
            self.gann_upper = float(data.get("gann_upper", 0) or 0)
            self.gann_lower = float(data.get("gann_lower", 0) or 0)
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
            logger.warning("S5 restore_state apply failed: %s", exc)
            return False

    # ── Status payload ────────────────────────────────

    def get_status(self) -> dict:
        unrealized = 0.0
        if self.state == State.POSITION_OPEN and self.current_ltp > 0 and self.fill_price > 0:
            unrealized = round((self.current_ltp - self.fill_price) * self.quantity, 2)
        return {
            "is_active": self.is_active,
            "state": self.state.value,
            "scenario": self.scenario,
            "signal": self.signal,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "levels": {
                "gann_upper": self.gann_upper,
                "gann_lower": self.gann_lower,
                "locked": self.state != State.IDLE,
                "source": "gann_csv",
                "gann_count_total": len(self.gann_levels),
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
                "lots": self.lots,
                "quantity": self.quantity,
                "strike_interval": self.strike_interval,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "retest_buffer": self.retest_buffer,
                "max_breakout_extension": self.max_breakout_extension,
                "max_trades_per_day": self.max_trades_per_day,
                "allow_reentry": self.allow_reentry,
                "retest_only": self.retest_only,
                "itm_offset": self.itm_offset,
                "gann_target": self.gann_target,
                "gann_count": self.gann_count,
                "max_entry_slippage": self.max_entry_slippage,
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
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
        }
