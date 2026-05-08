"""
Strategy 6 — Manual CALL / PUT Line Touch Entry.

Concept
-------
Two user-controlled horizontal price levels on NIFTY spot:
    • CALL Line  → BUY ITM CALL when spot touches it
    • PUT  Line  → BUY ITM PUT  when spot touches it

There is NO retest, no candle confirmation — a direct touch fires the
entry. Lines can be edited from the UI (drag, double-click, or numeric
input) at any time. If a line is moved across the current spot, no
entry fires until the spot actually crosses it on a subsequent tick.

Position Management
-------------------
Identical to Strategy 4: shadow SL / Target promote to real exchange
orders on proximity, auto square-off at 15:15 IST. Once a trade is
active, no new entries are armed until that trade completes (SL or
TGT). After completion, next entry is allowed up to a configurable
``max_trades_per_day`` cap (default 3).

State machine
-------------
    IDLE → ORDER_PLACED → POSITION_OPEN → COMPLETED
                                      ↘ (re-entry allowed) → IDLE
"""
from __future__ import annotations

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

logger = get_logger("strategy6.call_put_lines")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy6_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy6_trades.json"


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy6CallPutLines:
    """Manual CALL/PUT line touch-entry strategy on NIFTY spot."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # ── Config (NIFTY defaults, mirrors S4) ──
        self.sl_points = float(config.get("sl_points", 30))
        self.target_points = float(config.get("target_points", 60))
        self.lot_size = int(config.get("lot_size", 65))
        self.lots = max(1, int(config.get("lots", 1)))
        self.strike_interval = int(config.get("strike_interval", 50))
        self.sl_proximity = float(config.get("sl_proximity", 5))
        self.target_proximity = float(config.get("target_proximity", 5))
        self.max_trades_per_day = max(1, int(config.get("max_trades_per_day", 3)))
        self.itm_offset = int(config.get("itm_offset", 100))
        self.max_entry_slippage = float(config.get("max_entry_slippage", 8))
        self.index_name = str(config.get("index_name", "NIFTY")).upper()

        # ── User-defined levels (the heart of S6) ──
        # 0.0 = unset; both must be set & distinct for the strategy to arm.
        self.call_line: float = float(config.get("call_line", 0) or 0)
        self.put_line: float = float(config.get("put_line", 0) or 0)

        # ── State ──
        self.is_active: bool = False
        self.state: State = State.IDLE
        self.scenario: str = "—"
        self.signal: str = "NO_TRADE"   # BUY_CALL / BUY_PUT / NO_TRADE
        self._trading_date: Optional[date] = None
        self._check_lock = threading.Lock()

        # ── Live tracking ──
        self.spot_price: float = 0.0
        self._prev_spot: float = 0.0  # last tick spot — used to detect a
                                      # *crossing* of either line so we
                                      # don't fire instantly when a line
                                      # is moved through current spot.

        # ── Trade detail ──
        self.signal_type: Optional[str] = None  # CE / PE
        self.entry_reason: str = ""
        self.atm_strike: int = 0
        self.strike: int = 0
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

        self._exit_check_count: int = 0

        # ── Misc ──
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None
        self._trades_today: int = 0
        self.trade_log: list[dict] = []
        self.last_check_at: Optional[datetime] = None

    # ── Derived ───────────────────────────────────────
    @property
    def quantity(self) -> int:
        return max(0, int(self.lots) * int(self.lot_size))

    # ── Public controls ───────────────────────────────

    def start(self, config: dict):
        self.apply_config(config, save=False)
        self.is_active = True
        self._check_day_reset()
        self._save_state()
        logger.info(
            "Strategy 6 started: SL=%s TGT=%s LOT=%s CALL=%s PUT=%s",
            self.sl_points, self.target_points, self.lot_size,
            self.call_line, self.put_line,
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 6 stopped")

    def apply_config(self, config: dict, save: bool = True) -> None:
        self.sl_points = float(config.get("sl_points", self.sl_points))
        self.target_points = float(config.get("target_points", self.target_points))
        self.lot_size = int(config.get("lot_size", self.lot_size))
        self.lots = max(1, int(config.get("lots", self.lots)))
        self.strike_interval = int(config.get("strike_interval", self.strike_interval))
        self.sl_proximity = float(config.get("sl_proximity", self.sl_proximity))
        self.target_proximity = float(config.get("target_proximity", self.target_proximity))
        self.max_trades_per_day = max(1, int(config.get("max_trades_per_day", self.max_trades_per_day)))
        self.itm_offset = int(config.get("itm_offset", self.itm_offset))
        self.max_entry_slippage = float(config.get("max_entry_slippage", self.max_entry_slippage))
        self.index_name = str(config.get("index_name", self.index_name)).upper()
        if "call_line" in config:
            self.call_line = float(config.get("call_line") or 0)
        if "put_line" in config:
            self.put_line = float(config.get("put_line") or 0)

        # Recompute SL/TGT on an open position when shadow legs are alive
        if self.state == State.POSITION_OPEN and self.fill_price > 0:
            if self.target_shadow:
                self.target_price = float(self.fill_price + self.target_points)
                if self.target_order:
                    self.target_order["price"] = self.target_price
            if self.sl_shadow:
                self.sl_price = max(0.05, self.fill_price - self.sl_points)
                if self.sl_order:
                    self.sl_order["price"] = self.sl_price

        if save:
            self._save_state()

    def set_lines(self, call_line: Optional[float] = None,
                  put_line: Optional[float] = None) -> dict:
        """Update one or both lines. Treat as a fresh "arm" — reset
        ``_prev_spot`` so a line moved through current spot does NOT
        instantly fire an entry on the same tick.
        """
        changed = False
        if call_line is not None:
            v = float(call_line or 0)
            if v != self.call_line:
                self.call_line = v
                changed = True
        if put_line is not None:
            v = float(put_line or 0)
            if v != self.put_line:
                self.put_line = v
                changed = True
        if changed:
            # Re-anchor crossing detector to current spot.
            self._prev_spot = self.spot_price
            self._save_state()
            logger.info(
                "S6 lines updated: CALL=%.2f PUT=%.2f (anchor spot=%.2f)",
                self.call_line, self.put_line, self.spot_price,
            )
        return {"call_line": self.call_line, "put_line": self.put_line}

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
                "S6 orphaned %s from %s — recording BROKER_SQUAREOFF",
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
        self._prev_spot = 0.0
        self._trades_today = 0
        self._instruments_cache = None
        self._save_state()
        logger.info("S6 new trading day %s — reset to IDLE", today)

    # ── Instruments / index resolution ────────────────

    def _resolve_index_token(self) -> Optional[int]:
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

    def get_intraday_series(self, target_day: Optional[date] = None) -> list[dict]:
        """Today's NIFTY 50 minute-candle close series since 9:15."""
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
            logger.warning("S6 intraday fetch failed: %s", exc)
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
        # Continue managing an open position even after a manual stop —
        # SL/TGT promotion and 15:15 squareoff must keep running.
        if not self.is_active and self.state != State.POSITION_OPEN:
            return self.get_status()

        if not self._check_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self.last_check_at = datetime.now()
            self._check_day_reset()

            if spot_price > 0:
                # Seed prev_spot on first valid tick
                if self._prev_spot <= 0:
                    self._prev_spot = spot_price
                self.spot_price = spot_price

            # Auto square-off
            if self.state == State.POSITION_OPEN and datetime.now().time() >= PRE_CLOSE_EXIT:
                logger.info("S6 auto square-off triggered")
                self._auto_square_off()
                return self.get_status()

            if self.state == State.IDLE:
                self._scan_for_touch()
            elif self.state == State.ORDER_PLACED:
                self._check_entry_fill()
            elif self.state == State.POSITION_OPEN:
                self._check_exit()

            # Update prev_spot AFTER scan so crossing detection uses
            # the previous tick as the comparison baseline.
            if spot_price > 0:
                self._prev_spot = spot_price

            self._refresh_current_ltp()
            return self.get_status()
        finally:
            self._check_lock.release()

    # ── Setup detection ───────────────────────────────

    def _scan_for_touch(self):
        if self.spot_price <= 0:
            self.scenario = "Waiting for spot…"
            return
        if self.call_line <= 0 and self.put_line <= 0:
            self.scenario = "Set CALL / PUT lines to arm"
            return
        if self._trades_today >= self.max_trades_per_day:
            self.scenario = "Max trades reached"
            return

        prev = self._prev_spot or self.spot_price
        spot = self.spot_price

        # CALL line touch: detect a crossing UP through call_line OR an
        # equal-touch tick. Crossing-only avoids false fires when a line
        # is moved through the current spot — set_lines() re-anchors
        # _prev_spot to the spot at the time of the move.
        if self.call_line > 0:
            crossed_up = prev < self.call_line <= spot
            equal_touch = abs(spot - self.call_line) < 0.05 and prev != spot
            if crossed_up or equal_touch:
                self.scenario = f"CALL line {self.call_line:.2f} touched"
                self.signal = "BUY_CALL"
                self.entry_reason = (
                    f"Spot {spot:.2f} crossed CALL line {self.call_line:.2f}"
                )
                self._fire_entry("CE")
                return

        if self.put_line > 0:
            crossed_down = prev > self.put_line >= spot
            equal_touch = abs(spot - self.put_line) < 0.05 and prev != spot
            if crossed_down or equal_touch:
                self.scenario = f"PUT line {self.put_line:.2f} touched"
                self.signal = "BUY_PUT"
                self.entry_reason = (
                    f"Spot {spot:.2f} crossed PUT line {self.put_line:.2f}"
                )
                self._fire_entry("PE")
                return

        # Idle scenario summary for UI
        if self.call_line > 0 and self.put_line > 0:
            d_call = self.call_line - spot
            d_put = spot - self.put_line
            self.scenario = (
                f"Armed | CALL {self.call_line:.0f} (+{d_call:.1f}) "
                f"| PUT {self.put_line:.0f} (-{d_put:.1f})"
            )
        elif self.call_line > 0:
            self.scenario = f"Armed CALL only @ {self.call_line:.0f}"
        elif self.put_line > 0:
            self.scenario = f"Armed PUT only @ {self.put_line:.0f}"
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
        if opt_type == "CE":
            self.strike = int(self.atm_strike - self.itm_offset)
        else:
            self.strike = int(self.atm_strike + self.itm_offset)

        opt_info = self._find_option(self.strike, opt_type)
        if not opt_info:
            logger.error("S6 no %s option at strike %s (ATM=%s, offset=%s)",
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
            logger.error("S6 LTP fetch failed: %s", exc)
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
                tag="S6ENTRY",
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
            elif str(resp.status).upper() in ("REJECTED", "CANCELLED"):
                # Broker rejected synchronously (e.g. insufficient funds).
                # Reset to IDLE immediately so the next line touch can
                # re-arm — don't sit in ORDER_PLACED for 60s.
                logger.warning(
                    "S6 entry %s synchronously — resetting to IDLE for re-arm",
                    resp.status,
                )
                self.entry_order = None
                self.signal_type = None
                self.signal = "NO_TRADE"
                self.scenario = f"Entry {resp.status} — re-arming"
                self.option_symbol = ""
                self.option_token = 0
                self.option_ltp = 0.0
                self.fill_price = 0.0
                self.atm_strike = 0
                self.strike = 0
                self.state = State.IDLE
                self._save_state()
            else:
                self._save_state()
                logger.info("S6 entry order placed: %s", resp.order_id)
        except Exception as exc:
            logger.error("S6 entry order failed: %s", exc)
            self.state = prev_state
            self.entry_order = None
            self._save_state()

    def _check_entry_fill(self):
        if not self.entry_order:
            self.state = State.IDLE
            return

        # Staleness: cancel if unfilled for >30s
        placed_at = self.entry_order.get("timestamp")
        if placed_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(placed_at)).total_seconds()
            except Exception:
                elapsed = 0
            if elapsed > 30 and self.entry_order.get("status") != "COMPLETE":
                logger.info("S6 entry stale (%.0fs) — cancelling", elapsed)
                # Best-effort cancel; never let an exception here keep us
                # stuck in ORDER_PLACED.
                try:
                    self._cancel_order(self.entry_order)
                except Exception as exc:
                    logger.warning("S6 cancel failed (ignoring): %s", exc)
                self.entry_order = None
                self.signal_type = None
                self.signal = "NO_TRADE"
                self.scenario = "Cancelled — stale order"
                self.option_symbol = ""
                self.option_token = 0
                self.option_ltp = 0.0
                self.fill_price = 0.0
                self.atm_strike = 0
                self.strike = 0
                self.state = State.IDLE
                self._save_state()
                return

        if self.entry_order.get("is_paper"):
            return
        try:
            orders = self.broker.get_orders()
            for o in orders:
                if str(o.get("order_id")) == str(self.entry_order["order_id"]):
                    status = o.get("status", "")
                    if status == "COMPLETE":
                        self.fill_price = float(o.get("average_price", self.option_ltp))
                        self.entry_order["status"] = "COMPLETE"
                        ref = float(self.option_ltp or 0)
                        slip = self.fill_price - ref
                        if (
                            ref > 0
                            and self.max_entry_slippage > 0
                            and slip > self.max_entry_slippage
                        ):
                            logger.warning(
                                "S6 entry slippage breach: ref=%.2f fill=%.2f slip=%.2f > max=%.2f — flattening",
                                ref, self.fill_price, slip, self.max_entry_slippage,
                            )
                            self._slippage_flatten(ref, slip)
                            return
                        self._on_entry_filled()
                    elif status in ("CANCELLED", "REJECTED"):
                        logger.warning(
                            "S6 entry %s — resetting to IDLE for re-arm", status,
                        )
                        self.entry_order = None
                        self.signal_type = None
                        self.signal = "NO_TRADE"
                        self.scenario = f"Entry {status} — re-arming"
                        self.entry_reason = ""
                        self.option_symbol = ""
                        self.option_token = 0
                        self.option_ltp = 0.0
                        self.fill_price = 0.0
                        self.sl_price = 0.0
                        self.target_price = 0.0
                        self.current_ltp = 0.0
                        self.atm_strike = 0
                        self.strike = 0
                        self.state = State.IDLE
                        self._save_state()
                    break
        except Exception as exc:
            logger.error("S6 fill check failed: %s", exc)

    def _on_entry_filled(self):
        self.target_price = float(self.fill_price + self.target_points)
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
            "S6 position open. Entry=%.2f SL=%.2f TGT=%.2f",
            self.fill_price, self.sl_price, self.target_price,
        )

    # ── Exit handling ─────────────────────────────────

    def _check_exit(self):
        ltp = 0.0
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            ltp = float(ltp_map.get(f"NFO:{self.option_symbol}", 0) or 0)
        except Exception as exc:
            logger.warning(
                "S6 exit-LTP fetch failed (%s) — falling back to cached %.2f",
                exc, self.current_ltp,
            )
        if ltp <= 0:
            ltp = float(self.current_ltp or 0)
        if ltp <= 0:
            return
        self.current_ltp = ltp

        # Manual exit detection (broker-side close)
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
                            "S6 manual exit detected (%s qty=0) — closing trade",
                            self.option_symbol,
                        )
                        self._cancel_order(self.sl_order)
                        self._cancel_order(self.target_order)
                        self._complete_trade("MANUAL_EXIT", ltp)
                        return
                except Exception as exc:
                    logger.debug("S6 position poll failed: %s", exc)

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
                    and oid == str(self.sl_order["order_id"])
                ):
                    if status == "COMPLETE":
                        self._cancel_order(self.target_order)
                        self._complete_trade("SL_HIT", self.sl_price)
                        return
                    if status in ("CANCELLED", "REJECTED"):
                        logger.warning(
                            "S6 SL order %s on exchange (%s) — reverting to shadow",
                            oid, status,
                        )
                        self.sl_order = None
                        self.sl_shadow = True
                if (
                    not self.target_shadow and self.target_order
                    and oid == str(self.target_order["order_id"])
                ):
                    if status == "COMPLETE":
                        self._cancel_order(self.sl_order)
                        self._complete_trade("TARGET_HIT", self.target_price)
                        return
                    if status in ("CANCELLED", "REJECTED"):
                        logger.warning(
                            "S6 TGT order %s on exchange (%s) — reverting to shadow",
                            oid, status,
                        )
                        self.target_order = None
                        self.target_shadow = True

        # SL leg promotion
        if self.sl_shadow and ltp <= (self.sl_price + self.sl_proximity):
            logger.info(
                "S6 SL proximity hit: ltp=%.2f sl=%.2f prox=%.2f — promoting",
                ltp, self.sl_price, self.sl_proximity,
            )
            if not self.target_shadow and self.target_order:
                self._cancel_order(self.target_order)
                self.target_order = None
                self.target_shadow = True
            self.sl_shadow = False
            try:
                if ltp <= self.sl_price:
                    self.broker.place_order(OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO, side=OrderSide.SELL,
                        quantity=self.quantity, order_type=OrderType.MARKET,
                        product=ProductType.MIS, tag="S6SL",
                    ))
                    logger.warning("S6 SL breach — MARKET sell @ ltp=%.2f", ltp)
                    self._complete_trade("SL_HIT", ltp)
                    return
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.SL_M,
                    product=ProductType.MIS, trigger_price=self.sl_price, tag="S6SL",
                ))
                self.sl_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "price": self.sl_price,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info("S6 SL-M order placed on exchange: %s", resp.order_id)
                self._save_state()
            except Exception as exc:
                logger.error("S6 SL placement failed: %s", exc)
                self.sl_shadow = True

        # Target leg promotion
        if self.target_shadow and ltp >= (self.target_price - self.target_proximity):
            logger.info(
                "S6 TGT proximity hit: ltp=%.2f tgt=%.2f prox=%.2f — promoting",
                ltp, self.target_price, self.target_proximity,
            )
            if not self.sl_shadow and self.sl_order:
                self._cancel_order(self.sl_order)
                self.sl_order = None
                self.sl_shadow = True
            self.target_shadow = False
            try:
                if ltp >= self.target_price:
                    self.broker.place_order(OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO, side=OrderSide.SELL,
                        quantity=self.quantity, order_type=OrderType.MARKET,
                        product=ProductType.MIS, tag="S6TGT",
                    ))
                    logger.warning("S6 TGT breach — MARKET sell @ ltp=%.2f", ltp)
                    self._complete_trade("TARGET_HIT", ltp)
                    return
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.LIMIT,
                    product=ProductType.MIS, price=self.target_price, tag="S6TGT",
                ))
                self.target_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "price": self.target_price,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info("S6 TGT LIMIT order placed on exchange: %s", resp.order_id)
                self._save_state()
            except Exception as exc:
                logger.error("S6 target placement failed: %s", exc)
                self.target_shadow = True

    def _slippage_flatten(self, ref_ltp: float, slip: float):
        self._cancel_order(self.sl_order)
        self._cancel_order(self.target_order)
        if not settings.PAPER_TRADE and self.option_symbol and self.lot_size > 0:
            try:
                self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.MARKET,
                    product=ProductType.MIS,
                    tag="S6SLIP",
                ))
            except Exception as exc:
                logger.error("S6 slippage-flatten failed: %s", exc)

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
        self._append_trade_history(trade)
        self._trades_today += 1

        self.fill_price = 0.0
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True
        self.is_active = False
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
                    tag="S6SQOFF",
                ))
            except Exception as exc:
                logger.error("S6 squareoff failed: %s", exc)
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
            logger.warning("S6 cancel failed: %s", exc)

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
        logger.info("S6 trade done: %s | Entry=%.2f Exit=%.2f PnL=%.2f",
                    exit_type, self.fill_price, exit_price, pnl)

        # Reset trade-specific fields
        self.fill_price = 0.0
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True
        self.sl_price = 0.0
        self.target_price = 0.0
        self.current_ltp = 0.0
        self.option_ltp = 0.0
        self.option_symbol = ""
        self.option_token = 0
        self.atm_strike = 0
        self.strike = 0
        self.signal_type = None
        self.signal = "NO_TRADE"
        self.entry_reason = ""

        # Re-arm policy: only block further entries on slippage; both
        # SL and TGT are eligible for re-entry up to the daily cap (per
        # spec — max trades/day governs).
        if self._trades_today < self.max_trades_per_day:
            self.state = State.IDLE
            # Re-anchor crossing detector so an existing line that's
            # already on the "wrong" side doesn't instantly re-fire.
            self._prev_spot = self.spot_price
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

    # ── Persistence ───────────────────────────────────

    def _save_state(self):
        data = {
            "is_active": self.is_active,
            "state": self.state.value,
            "scenario": self.scenario,
            "signal": self.signal,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "call_line": self.call_line,
            "put_line": self.put_line,
            "spot_price": self.spot_price,
            "prev_spot": self._prev_spot,
            "signal_type": self.signal_type,
            "entry_reason": self.entry_reason,
            "atm_strike": self.atm_strike,
            "strike": self.strike,
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
                "max_trades_per_day": self.max_trades_per_day,
                "itm_offset": self.itm_offset,
                "max_entry_slippage": self.max_entry_slippage,
                "index_name": self.index_name,
                "call_line": self.call_line,
                "put_line": self.put_line,
            },
            "saved_at": datetime.now().isoformat(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.error("S6 save_state failed: %s", exc)

    def _append_trade_history(self, trade: dict):
        try:
            trades = []
            if TRADE_HISTORY_FILE.exists():
                trades = json.loads(TRADE_HISTORY_FILE.read_text())
            trades.append(trade)
            TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRADE_HISTORY_FILE.write_text(json.dumps(trades, indent=2, default=str))
        except Exception as exc:
            logger.error("S6 trade history append failed: %s", exc)

    def restore_state(self) -> bool:
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
        except Exception as exc:
            logger.warning("S6 restore_state read failed: %s", exc)
            return False

        saved_date = data.get("trading_date", "")
        # If state is from a past day, only restore the lines (so the
        # user doesn't lose their drawn levels overnight) — everything
        # else gets reset by _check_day_reset on first check().
        is_today = saved_date == date.today().isoformat()
        try:
            self.call_line = float(data.get("call_line", 0) or 0)
            self.put_line = float(data.get("put_line", 0) or 0)
            cfg = data.get("config", {}) or {}
            if cfg:
                # Don't let config-stored call/put_line overwrite the
                # top-level (which is canonical) — apply_config reads
                # from the same dict so we strip those keys here.
                cfg = {k: v for k, v in cfg.items() if k not in ("call_line", "put_line")}
                self.apply_config(cfg, save=False)
            if not is_today:
                logger.info("S6 state file from %s — only lines restored", saved_date)
                self._trading_date = date.today()
                return True

            self.is_active = bool(data.get("is_active", False))
            self.state = State(data.get("state", "IDLE"))
            self.scenario = data.get("scenario", "—")
            self.signal = data.get("signal", "NO_TRADE")
            self._trading_date = date.today()
            self.spot_price = float(data.get("spot_price", 0) or 0)
            self._prev_spot = float(data.get("prev_spot", 0) or 0)
            self.signal_type = data.get("signal_type")
            self.entry_reason = data.get("entry_reason", "")
            self.atm_strike = int(data.get("atm_strike", 0) or 0)
            self.strike = int(data.get("strike", 0) or 0)
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
            return True
        except Exception as exc:
            logger.warning("S6 restore_state apply failed: %s", exc)
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
            "lines": {
                "call_line": self.call_line,
                "put_line": self.put_line,
            },
            "spot": {
                "price": self.spot_price,
                "prev": self._prev_spot,
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
                "max_trades_per_day": self.max_trades_per_day,
                "itm_offset": self.itm_offset,
                "max_entry_slippage": self.max_entry_slippage,
                "index_name": self.index_name,
                "call_line": self.call_line,
                "put_line": self.put_line,
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
