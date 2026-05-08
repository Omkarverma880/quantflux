"""
Strategy 7 — CE/PE Strike Line Touch Entry.

Concept
-------
A direct upgrade of Strategy 6 that monitors **option strike LTPs**
instead of the underlying NIFTY spot.

  • User picks a CE strike and a PE strike (5 above / 5 below ATM).
  • A horizontal CALL line is drawn on the CE strike's price chart.
  • A horizontal PUT  line is drawn on the PE strike's price chart.
  • When the CE strike's LTP touches the CALL line → BUY that CE.
  • When the PE strike's LTP touches the PUT  line → BUY that PE.

There is NO retest, no candle confirmation — direct touch entries.
Position management mirrors Strategy 6: shadow SL / TGT promote on
proximity, auto square-off at 15:15 IST, ``max_trades_per_day`` cap.

State machine
-------------
    IDLE → ORDER_PLACED → POSITION_OPEN → COMPLETED → IDLE
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

logger = get_logger("strategy7.strike_lines")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy7_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy7_trades.json"


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy7StrikeLines:
    """CE/PE strike line touch-entry strategy."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # ── Config ──
        self.sl_points       = float(config.get("sl_points", 30))
        self.target_points   = float(config.get("target_points", 60))
        self.lot_size        = int(config.get("lot_size", 65))
        self.lots            = max(1, int(config.get("lots", 1)))
        self.strike_interval = int(config.get("strike_interval", 50))
        self.sl_proximity    = float(config.get("sl_proximity", 5))
        self.target_proximity = float(config.get("target_proximity", 5))
        self.max_trades_per_day = max(1, int(config.get("max_trades_per_day", 3)))
        self.max_entry_slippage = float(config.get("max_entry_slippage", 8))
        self.index_name      = str(config.get("index_name", "NIFTY")).upper()

        # ── User-defined lines (option-price levels) ──
        self.call_line: float = float(config.get("call_line", 0) or 0)
        self.put_line:  float = float(config.get("put_line", 0) or 0)

        # ── Selected strikes ──
        self.ce_strike: int = int(config.get("ce_strike", 0) or 0)
        self.pe_strike: int = int(config.get("pe_strike", 0) or 0)
        self.ce_symbol: str = str(config.get("ce_symbol", "") or "")
        self.pe_symbol: str = str(config.get("pe_symbol", "") or "")
        self.ce_token: int  = int(config.get("ce_token", 0) or 0)
        self.pe_token: int  = int(config.get("pe_token", 0) or 0)

        # ── State ──
        self.is_active: bool = False
        self.state: State = State.IDLE
        self.scenario: str = "—"
        self.signal: str = "NO_TRADE"  # BUY_CALL / BUY_PUT / NO_TRADE
        self._trading_date: Optional[date] = None
        self._check_lock = threading.Lock()

        # ── Live LTPs (monitored) ──
        self.ce_ltp: float = 0.0
        self.pe_ltp: float = 0.0
        self._prev_ce: float = 0.0
        self._prev_pe: float = 0.0
        self.spot_price: float = 0.0  # underlying NIFTY (informational)

        # ── Active trade ──
        self.signal_type: Optional[str] = None  # "CE" / "PE"
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
        self.entry_order:  Optional[dict] = None
        self.sl_order:     Optional[dict] = None
        self.target_order: Optional[dict] = None
        self.sl_shadow: bool = True
        self.target_shadow: bool = True

        self._exit_check_count: int = 0
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None
        self._trades_today: int = 0
        self.trade_log: list[dict] = []
        self.last_check_at: Optional[datetime] = None

        # ── Risk / re-entry controller ──
        self.risk = RiskController()

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
            "Strategy 7 started: CE=%s PE=%s CALL=%.2f PUT=%.2f",
            self.ce_symbol or self.ce_strike, self.pe_symbol or self.pe_strike,
            self.call_line, self.put_line,
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 7 stopped")

    def apply_config(self, config: dict, save: bool = True) -> None:
        for k in ("sl_points", "target_points", "sl_proximity", "target_proximity",
                  "max_entry_slippage"):
            if k in config:
                setattr(self, k, float(config.get(k) or 0) or getattr(self, k))
        for k in ("lot_size", "strike_interval", "max_trades_per_day"):
            if k in config:
                setattr(self, k, int(config.get(k) or 0) or getattr(self, k))
        if "lots" in config:
            self.lots = max(1, int(config.get("lots") or self.lots))
        if "index_name" in config:
            self.index_name = str(config.get("index_name") or self.index_name).upper()
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
                  put_line:  Optional[float] = None) -> dict:
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
            # Re-anchor crossing detector to current LTP
            self._prev_ce = self.ce_ltp
            self._prev_pe = self.pe_ltp
            self._save_state()
            logger.info(
                "S7 lines updated: CALL=%.2f (CE=%.2f) | PUT=%.2f (PE=%.2f)",
                self.call_line, self.ce_ltp, self.put_line, self.pe_ltp,
            )
        return {"call_line": self.call_line, "put_line": self.put_line}

    def set_strikes(self, ce: Optional[dict], pe: Optional[dict]) -> dict:
        """Update one or both monitored strikes.

        Each value is either ``None`` (leave unchanged) or a dict with
        ``strike`` (int) and optionally ``tradingsymbol`` / ``token``.
        Selecting a new strike resets crossing detection on that side.
        """
        if ce is not None:
            new_strike = int(ce.get("strike") or 0)
            if new_strike != self.ce_strike:
                self.ce_strike = new_strike
                self.ce_symbol = str(ce.get("tradingsymbol") or "")
                self.ce_token  = int(ce.get("token") or 0)
                self.ce_ltp = 0.0
                self._prev_ce = 0.0
        if pe is not None:
            new_strike = int(pe.get("strike") or 0)
            if new_strike != self.pe_strike:
                self.pe_strike = new_strike
                self.pe_symbol = str(pe.get("tradingsymbol") or "")
                self.pe_token  = int(pe.get("token") or 0)
                self.pe_ltp = 0.0
                self._prev_pe = 0.0
        # Look up missing tradingsymbols if only strike was provided
        self._resolve_missing_strike_symbols()
        self._save_state()
        return {
            "ce_strike": self.ce_strike, "ce_symbol": self.ce_symbol,
            "pe_strike": self.pe_strike, "pe_symbol": self.pe_symbol,
        }

    def _resolve_missing_strike_symbols(self):
        if self.ce_strike and not self.ce_symbol:
            opt = self._find_option(self.ce_strike, "CE")
            if opt:
                self.ce_symbol = opt["tradingsymbol"]
                self.ce_token = int(opt.get("instrument_token") or 0)
        if self.pe_strike and not self.pe_symbol:
            opt = self._find_option(self.pe_strike, "PE")
            if opt:
                self.pe_symbol = opt["tradingsymbol"]
                self.pe_token = int(opt.get("instrument_token") or 0)

    # ── Daily reset ───────────────────────────────────

    def _check_day_reset(self):
        today = date.today()
        if self._trading_date == today:
            return
        old_date = self._trading_date
        self._trading_date = today
        if self.state in (State.POSITION_OPEN, State.ORDER_PLACED) and self.fill_price > 0:
            logger.warning("S7 orphaned %s from %s — recording BROKER_SQUAREOFF",
                           self.state.value, old_date)
            trade = {
                "date": (old_date or today).isoformat(),
                "signal": self.signal_type,
                "scenario": self.scenario,
                "option": self.option_symbol,
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
        # Reset
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
        self._prev_ce = 0.0
        self._prev_pe = 0.0
        self._trades_today = 0
        self._instruments_cache = None
        self._save_state()

    # ── Instruments / strike resolution ──────────────

    def _get_nfo_instruments(self) -> list[dict]:
        today = date.today()
        if self._instruments_cache and self._instruments_date == today and self._instruments_cache.get("NFO"):
            return self._instruments_cache["NFO"]
        cache = self._instruments_cache or {}
        cache["NFO"] = self.broker.get_instruments("NFO")
        self._instruments_cache = cache
        self._instruments_date = today
        return cache["NFO"]

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

    def list_strikes(self, spot_price: float, count: int = 5) -> dict:
        """Return ATM±count strikes with their CE & PE tradingsymbols."""
        if spot_price <= 0:
            return {"atm": 0, "strikes": []}
        atm = round(spot_price / self.strike_interval) * self.strike_interval
        strikes_int = [atm + i * self.strike_interval for i in range(-count, count + 1)]
        out = []
        for s in strikes_int:
            ce = self._find_option(s, "CE")
            pe = self._find_option(s, "PE")
            out.append({
                "strike": s,
                "ce_symbol": ce["tradingsymbol"] if ce else "",
                "ce_token":  int(ce["instrument_token"]) if ce else 0,
                "pe_symbol": pe["tradingsymbol"] if pe else "",
                "pe_token":  int(pe["instrument_token"]) if pe else 0,
                "is_atm": s == atm,
            })
        return {"atm": atm, "strikes": out}

    def fetch_ltps(self) -> dict:
        """Fetch live LTP for the currently selected CE & PE strikes."""
        keys = []
        if self.ce_symbol:
            keys.append(f"NFO:{self.ce_symbol}")
        if self.pe_symbol:
            keys.append(f"NFO:{self.pe_symbol}")
        keys.append("NSE:NIFTY 50")
        try:
            ltp = self.broker.get_ltp(keys) or {}
        except Exception as exc:
            logger.debug("S7 LTP fetch failed: %s", exc)
            ltp = {}
        ce = float(ltp.get(f"NFO:{self.ce_symbol}", 0) or 0) if self.ce_symbol else 0.0
        pe = float(ltp.get(f"NFO:{self.pe_symbol}", 0) or 0) if self.pe_symbol else 0.0
        spot = float(ltp.get("NSE:NIFTY 50", 0) or 0)
        if ce > 0:
            if self._prev_ce <= 0:
                self._prev_ce = ce
            self.ce_ltp = ce
        if pe > 0:
            if self._prev_pe <= 0:
                self._prev_pe = pe
            self.pe_ltp = pe
        if spot > 0:
            self.spot_price = spot
        return {"ce": ce, "pe": pe, "spot": spot}

    def get_intraday_series(self, side: str) -> list[dict]:
        """Today's minute-candle series for the chosen CE or PE strike."""
        token = self.ce_token if side == "CE" else self.pe_token
        if not token:
            return []
        from_dt = datetime.combine(date.today(), MARKET_OPEN)
        now = datetime.now()
        to_dt = now if now.time() < MARKET_CLOSE else datetime.combine(date.today(), MARKET_CLOSE)
        try:
            candles = self.broker.get_historical_data(
                instrument_token=token,
                from_date=from_dt, to_date=to_dt,
                interval="minute",
            ) or []
        except Exception as exc:
            logger.warning("S7 intraday fetch failed: %s", exc)
            return []
        out: list[dict] = []
        for c in candles:
            ts = c.get("date")
            t_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else (str(ts)[-8:] if ts else "")
            out.append({"t": t_str, "y": float(c.get("close", 0))})
        return out

    # ── Main check ────────────────────────────────────

    def check(self, *_args, **_kwargs) -> dict:
        # Continue managing an open position even after manual stop —
        # SL/TGT promotion and 15:15 squareoff must keep running.
        if not self.is_active and self.state != State.POSITION_OPEN:
            return self.get_status()
        if not self._check_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self.last_check_at = datetime.now()
            self._check_day_reset()

            # Always refresh option LTPs — these drive both detection
            # and the front-end live chart.
            self.fetch_ltps()

            # Auto square-off
            if self.state == State.POSITION_OPEN and datetime.now().time() >= PRE_CLOSE_EXIT:
                logger.info("S7 auto square-off triggered")
                self._auto_square_off()
                return self.get_status()

            if self.state == State.IDLE:
                self._scan_for_touch()
            elif self.state == State.ORDER_PLACED:
                self._check_entry_fill()
            elif self.state == State.POSITION_OPEN:
                self._check_exit()

            # Update prev refs AFTER scan
            if self.ce_ltp > 0:
                self._prev_ce = self.ce_ltp
            if self.pe_ltp > 0:
                self._prev_pe = self.pe_ltp

            self._refresh_current_ltp()
            return self.get_status()
        finally:
            self._check_lock.release()

    def _scan_for_touch(self):
        if self.call_line <= 0 and self.put_line <= 0:
            self.scenario = "Set CALL / PUT lines to arm"
            return
        if self._trades_today >= self.max_trades_per_day:
            self.scenario = "Max trades reached"
            return

        # Tick-level fresh-crossover arming
        if self.ce_ltp > 0:
            self.risk.update_price_for_arming(side="CALL", current_price=self.ce_ltp)
        if self.pe_ltp > 0:
            self.risk.update_price_for_arming(side="PUT", current_price=self.pe_ltp)

        # CALL touch on CE strike
        if self.call_line > 0 and self.ce_ltp > 0 and self.ce_strike > 0:
            prev = self._prev_ce or self.ce_ltp
            cur = self.ce_ltp
            crossed_up = prev < self.call_line <= cur
            crossed_dn = prev > self.call_line >= cur
            equal_touch = abs(cur - self.call_line) < 0.05 and prev != cur
            if crossed_up or crossed_dn or equal_touch:
                ok, reason = self.risk.allow_entry(
                    side="CALL", current_price=cur, line_price=self.call_line
                )
                if not ok:
                    self.scenario = f"CALL touch blocked — {reason}"
                    self.signal = "NO_TRADE"
                    return
                self.scenario = f"CALL line {self.call_line:.2f} touched on {self.ce_symbol}"
                self.signal = "BUY_CALL"
                self.entry_reason = (
                    f"{self.ce_symbol} LTP {cur:.2f} crossed CALL line {self.call_line:.2f}"
                )
                self._fire_entry("CE")
                return

        # PUT touch on PE strike
        if self.put_line > 0 and self.pe_ltp > 0 and self.pe_strike > 0:
            prev = self._prev_pe or self.pe_ltp
            cur = self.pe_ltp
            crossed_up = prev < self.put_line <= cur
            crossed_dn = prev > self.put_line >= cur
            equal_touch = abs(cur - self.put_line) < 0.05 and prev != cur
            if crossed_up or crossed_dn or equal_touch:
                ok, reason = self.risk.allow_entry(
                    side="PUT", current_price=cur, line_price=self.put_line
                )
                if not ok:
                    self.scenario = f"PUT touch blocked — {reason}"
                    self.signal = "NO_TRADE"
                    return
                self.scenario = f"PUT line {self.put_line:.2f} touched on {self.pe_symbol}"
                self.signal = "BUY_PUT"
                self.entry_reason = (
                    f"{self.pe_symbol} LTP {cur:.2f} crossed PUT line {self.put_line:.2f}"
                )
                self._fire_entry("PE")
                return

        # Idle status summary
        bits = []
        if self.call_line > 0 and self.ce_symbol:
            bits.append(f"CALL {self.call_line:.2f} ({self.ce_strike}CE @ {self.ce_ltp:.2f})")
        if self.put_line > 0 and self.pe_symbol:
            bits.append(f"PUT {self.put_line:.2f} ({self.pe_strike}PE @ {self.pe_ltp:.2f})")
        self.scenario = "Armed | " + " · ".join(bits) if bits else "Select strikes & lines to arm"
        self.signal = "NO_TRADE"

    # ── Entry / exit / orders ─────────────────────────

    def _fire_entry(self, opt_type: str):
        self.signal_type = opt_type
        self.atm_strike = round(self.spot_price / self.strike_interval) * self.strike_interval if self.spot_price > 0 else 0
        if opt_type == "CE":
            self.strike = self.ce_strike
            self.option_symbol = self.ce_symbol
            self.option_token = self.ce_token
            self.option_ltp = self.ce_ltp
        else:
            self.strike = self.pe_strike
            self.option_symbol = self.pe_symbol
            self.option_token = self.pe_token
            self.option_ltp = self.pe_ltp

        if not self.option_symbol:
            logger.error("S7 cannot fire entry — option symbol missing for %s", opt_type)
            self.scenario = f"No {opt_type} symbol resolved"
            self.state = State.IDLE
            return

        # If strike's lot_size differs from config, sync it
        opt_info = self._find_option(self.strike, opt_type)
        if opt_info and opt_info.get("lot_size"):
            self.lot_size = int(opt_info["lot_size"])

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
                tag="S7ENTRY",
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
                logger.info("S7 entry order placed: %s", resp.order_id)
        except Exception as exc:
            logger.error("S7 entry order failed: %s", exc)
            self.state = prev_state
            self.entry_order = None
            self._save_state()

    def _check_entry_fill(self):
        if not self.entry_order:
            self.state = State.IDLE
            return
        placed_at = self.entry_order.get("timestamp")
        if placed_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(placed_at)).total_seconds()
                if elapsed > 60 and self.entry_order.get("status") != "COMPLETE":
                    logger.info("S7 entry stale (%.0fs) — cancelling", elapsed)
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
                            ref > 0 and self.max_entry_slippage > 0
                            and slip > self.max_entry_slippage
                        ):
                            logger.warning(
                                "S7 entry slippage breach: ref=%.2f fill=%.2f slip=%.2f > max=%.2f",
                                ref, self.fill_price, slip, self.max_entry_slippage,
                            )
                            self._slippage_flatten(ref, slip)
                            return
                        self._on_entry_filled()
                    elif status in ("CANCELLED", "REJECTED"):
                        logger.warning("S7 entry %s — re-arm", status)
                        self._reset_to_idle(scenario=f"Entry {status} — re-arming")
                    break
        except Exception as exc:
            logger.error("S7 fill check failed: %s", exc)

    def _on_entry_filled(self):
        self.target_price = float(self.fill_price + self.target_points)
        self.sl_price = max(0.05, self.fill_price - self.sl_points)
        self.sl_shadow = True
        self.target_shadow = True
        self.sl_order = {
            "status": "SHADOW", "is_paper": False,
            "price": self.sl_price, "order_id": None,
        }
        self.target_order = {
            "status": "SHADOW", "is_paper": False,
            "price": self.target_price, "order_id": None,
        }
        self.state = State.POSITION_OPEN
        self.risk.record_entry(side="CALL" if self.signal_type == "CE" else "PUT")
        self._save_state()
        logger.info(
            "S7 entry filled: %s @ %.2f | SL %.2f | TGT %.2f",
            self.option_symbol, self.fill_price, self.sl_price, self.target_price,
        )

    def _refresh_current_ltp(self):
        if self.state != State.POSITION_OPEN or not self.option_symbol:
            return
        try:
            ltp = self.broker.get_ltp([f"NFO:{self.option_symbol}"]) or {}
            v = float(ltp.get(f"NFO:{self.option_symbol}", 0) or 0)
            if v > 0:
                self.current_ltp = v
        except Exception as exc:
            logger.debug("S7 LTP refresh failed: %s", exc)

    def _check_exit(self):
        if not self.option_symbol or self.fill_price <= 0:
            return
        ltp = self.current_ltp or self.option_ltp
        if ltp <= 0:
            return
        # Promote shadow → real
        if self.sl_shadow and ltp <= (self.sl_price + self.sl_proximity):
            self._promote_sl_to_market()
        if self.target_shadow and ltp >= (self.target_price - self.target_proximity):
            self._promote_target_to_market()

        # Hard exits if shadow still on
        if self.sl_shadow and ltp <= self.sl_price:
            self._exit_position("SL_HIT", ltp)
            return
        if self.target_shadow and ltp >= self.target_price:
            self._exit_position("TARGET_HIT", ltp)
            return
        # Real-order completion check
        try:
            self._check_real_exit_completion()
        except Exception:
            pass

    def _promote_sl_to_market(self):
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                side=OrderSide.SELL, quantity=self.quantity,
                order_type=OrderType.SL, product=ProductType.MIS,
                trigger_price=self.sl_price, price=self.sl_price,
                tag="S7SL",
            )
            resp = self.broker.place_order(req)
            self.sl_order = {
                "order_id": resp.order_id, "status": resp.status,
                "is_paper": resp.is_paper, "price": self.sl_price,
                "timestamp": datetime.now().isoformat(),
            }
            self.sl_shadow = False
            self._save_state()
        except Exception as exc:
            logger.error("S7 promote SL failed: %s", exc)

    def _promote_target_to_market(self):
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                side=OrderSide.SELL, quantity=self.quantity,
                order_type=OrderType.LIMIT, product=ProductType.MIS,
                price=self.target_price, tag="S7TGT",
            )
            resp = self.broker.place_order(req)
            self.target_order = {
                "order_id": resp.order_id, "status": resp.status,
                "is_paper": resp.is_paper, "price": self.target_price,
                "timestamp": datetime.now().isoformat(),
            }
            self.target_shadow = False
            self._save_state()
        except Exception as exc:
            logger.error("S7 promote TGT failed: %s", exc)

    def _check_real_exit_completion(self):
        if self.sl_shadow and self.target_shadow:
            return
        try:
            orders = self.broker.get_orders()
        except Exception:
            return
        for o in orders:
            oid = str(o.get("order_id"))
            status = o.get("status", "")
            if not self.sl_shadow and self.sl_order and oid == str(self.sl_order.get("order_id")) \
               and status == "COMPLETE":
                self._exit_position("SL_HIT", float(o.get("average_price", self.sl_price)))
                return
            if not self.target_shadow and self.target_order and oid == str(self.target_order.get("order_id")) \
               and status == "COMPLETE":
                self._exit_position("TARGET_HIT", float(o.get("average_price", self.target_price)))
                return

    def _exit_position(self, exit_type: str, exit_price: float):
        # Cancel the other leg
        try:
            if exit_type == "SL_HIT" and self.target_order and not self.target_shadow:
                self._cancel_order(self.target_order)
            if exit_type == "TARGET_HIT" and self.sl_order and not self.sl_shadow:
                self._cancel_order(self.sl_order)
        except Exception:
            pass

        # If shadow still on, place a market exit
        if (exit_type == "SL_HIT" and self.sl_shadow) or \
           (exit_type == "TARGET_HIT" and self.target_shadow):
            try:
                req = OrderRequest(
                    tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                    side=OrderSide.SELL, quantity=self.quantity,
                    order_type=OrderType.MARKET, product=ProductType.MIS,
                    tag=f"S7{'SL' if exit_type=='SL_HIT' else 'TGT'}",
                )
                resp = self.broker.place_order(req)
                if exit_type == "SL_HIT":
                    self.sl_order = {
                        "order_id": resp.order_id, "status": resp.status,
                        "is_paper": resp.is_paper, "price": exit_price,
                    }
                    self.sl_shadow = False
                else:
                    self.target_order = {
                        "order_id": resp.order_id, "status": resp.status,
                        "is_paper": resp.is_paper, "price": exit_price,
                    }
                    self.target_shadow = False
            except Exception as exc:
                logger.error("S7 market exit failed: %s", exc)

        pnl = round((exit_price - self.fill_price) * self.quantity, 2)
        try:
            side = "CALL" if self.signal_type == "CE" else "PUT"
            line_price = self.call_line if side == "CALL" else self.put_line
            self.risk.record_exit(
                exit_type=exit_type, side=side,
                line_price=float(line_price or 0), pnl=pnl,
            )
        except Exception as exc:
            logger.warning("S7 risk.record_exit failed: %s", exc)
        trade = {
            "date": (self._trading_date or date.today()).isoformat(),
            "signal": self.signal_type,
            "scenario": self.scenario,
            "option": self.option_symbol,
            "entry_price": self.fill_price,
            "exit_price": exit_price,
            "exit_type": exit_type,
            "exit_time": datetime.now().strftime("%H:%M:%S"),
            "lot_size": self.lot_size,
            "pnl": pnl,
            "timestamp": datetime.now().isoformat(),
        }
        self.trade_log.append(trade)
        self._append_trade_history(trade)
        self._trades_today += 1

        self.state = State.COMPLETED
        self._save_state()
        logger.info("S7 trade completed: %s @ %.2f (PnL %.2f)", exit_type, exit_price, pnl)

        # Allow re-entry up to daily cap
        if self._trades_today < self.max_trades_per_day:
            self._reset_to_idle(scenario=f"Last: {exit_type} | re-arm allowed")

    def _auto_square_off(self):
        if self.option_symbol and self.fill_price > 0:
            ltp = self.current_ltp or self.option_ltp or self.fill_price
            try:
                # Cancel any open exits, place market sell
                if self.sl_order and not self.sl_shadow:
                    self._cancel_order(self.sl_order)
                if self.target_order and not self.target_shadow:
                    self._cancel_order(self.target_order)
                req = OrderRequest(
                    tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                    side=OrderSide.SELL, quantity=self.quantity,
                    order_type=OrderType.MARKET, product=ProductType.MIS,
                    tag="S7SQOFF",
                )
                self.broker.place_order(req)
            except Exception as exc:
                logger.error("S7 squareoff failed: %s", exc)
            self._exit_position("AUTO_SQUAREOFF", ltp)
        self.is_active = False
        self.state = State.COMPLETED
        self._save_state()

    def _slippage_flatten(self, ref: float, slip: float):
        """Immediately flatten an entry that filled with too much slippage."""
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                side=OrderSide.SELL, quantity=self.quantity,
                order_type=OrderType.MARKET, product=ProductType.MIS,
                tag="S7SLIP",
            )
            self.broker.place_order(req)
        except Exception as exc:
            logger.error("S7 slippage flatten failed: %s", exc)
        self.scenario = f"Entry slippage {slip:.2f} > max — flattened"
        self._reset_to_idle(scenario=self.scenario, count_trade=True)

    def _cancel_order(self, order: Optional[dict]):
        if not order or not order.get("order_id") or order.get("is_paper"):
            return
        try:
            self.broker.cancel_order(order["order_id"])
        except Exception as exc:
            logger.debug("S7 cancel order failed: %s", exc)

    def _reset_to_idle(self, scenario: str = "—", count_trade: bool = False):
        if count_trade:
            self._trades_today += 1
        self.state = State.IDLE
        self.scenario = scenario
        self.signal = "NO_TRADE"
        self.signal_type = None
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
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True
        self._save_state()

    # ── Persistence ──────────────────────────────────

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            STATE_FILE.write_text(json.dumps({
                "is_active": self.is_active,
                "state": self.state.value,
                "scenario": self.scenario,
                "signal": self.signal,
                "trading_date": self._trading_date.isoformat() if self._trading_date else None,
                "ce_strike": self.ce_strike, "ce_symbol": self.ce_symbol, "ce_token": self.ce_token,
                "pe_strike": self.pe_strike, "pe_symbol": self.pe_symbol, "pe_token": self.pe_token,
                "call_line": self.call_line, "put_line": self.put_line,
                "ce_ltp": self.ce_ltp, "pe_ltp": self.pe_ltp,
                "spot_price": self.spot_price,
                "signal_type": self.signal_type,
                "entry_reason": self.entry_reason,
                "atm_strike": self.atm_strike, "strike": self.strike,
                "option_symbol": self.option_symbol, "option_token": self.option_token,
                "option_ltp": self.option_ltp, "fill_price": self.fill_price,
                "sl_price": self.sl_price, "target_price": self.target_price,
                "current_ltp": self.current_ltp,
                "entry_order": self.entry_order, "sl_order": self.sl_order,
                "target_order": self.target_order,
                "sl_shadow": self.sl_shadow, "target_shadow": self.target_shadow,
                "trades_today": self._trades_today, "trade_log": self.trade_log,
                "risk": self.risk.serialize(),
            }, indent=2, default=str))
        except Exception as exc:
            logger.error("S7 state save failed: %s", exc)

    def restore_state(self) -> bool:
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
            self.is_active = bool(data.get("is_active"))
            self.state = State(data.get("state", "IDLE"))
            self.scenario = str(data.get("scenario", "—"))
            self.signal = str(data.get("signal", "NO_TRADE"))
            td = data.get("trading_date")
            self._trading_date = date.fromisoformat(td) if td else None
            self.ce_strike = int(data.get("ce_strike") or 0)
            self.pe_strike = int(data.get("pe_strike") or 0)
            self.ce_symbol = str(data.get("ce_symbol") or "")
            self.pe_symbol = str(data.get("pe_symbol") or "")
            self.ce_token  = int(data.get("ce_token") or 0)
            self.pe_token  = int(data.get("pe_token") or 0)
            self.call_line = float(data.get("call_line") or 0)
            self.put_line  = float(data.get("put_line") or 0)
            self.ce_ltp = float(data.get("ce_ltp") or 0)
            self.pe_ltp = float(data.get("pe_ltp") or 0)
            self.spot_price = float(data.get("spot_price") or 0)
            self.signal_type = data.get("signal_type")
            self.entry_reason = str(data.get("entry_reason") or "")
            self.atm_strike = int(data.get("atm_strike") or 0)
            self.strike = int(data.get("strike") or 0)
            self.option_symbol = str(data.get("option_symbol") or "")
            self.option_token = int(data.get("option_token") or 0)
            self.option_ltp = float(data.get("option_ltp") or 0)
            self.fill_price = float(data.get("fill_price") or 0)
            self.sl_price = float(data.get("sl_price") or 0)
            self.target_price = float(data.get("target_price") or 0)
            self.current_ltp = float(data.get("current_ltp") or 0)
            self.entry_order = data.get("entry_order")
            self.sl_order = data.get("sl_order")
            self.target_order = data.get("target_order")
            self.sl_shadow = bool(data.get("sl_shadow", True))
            self.target_shadow = bool(data.get("target_shadow", True))
            self._trades_today = int(data.get("trades_today") or 0)
            self.trade_log = list(data.get("trade_log") or [])
            self.risk.restore(data.get("risk") or {})
            return True
        except Exception as exc:
            logger.warning("S7 state restore failed: %s", exc)
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
            logger.error("S7 trade history append failed: %s", exc)

    # ── Status payload ───────────────────────────────

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
            "lines": {"call_line": self.call_line, "put_line": self.put_line},
            "strikes": {
                "ce_strike": self.ce_strike, "ce_symbol": self.ce_symbol, "ce_token": self.ce_token,
                "pe_strike": self.pe_strike, "pe_symbol": self.pe_symbol, "pe_token": self.pe_token,
            },
            "ltp": {
                "ce": self.ce_ltp, "pe": self.pe_ltp,
                "ce_prev": self._prev_ce, "pe_prev": self._prev_pe,
            },
            "spot": {"price": self.spot_price},
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
                "max_entry_slippage": self.max_entry_slippage,
                "index_name": self.index_name,
                "call_line": self.call_line,
                "put_line": self.put_line,
            },
            "trade": {
                "signal_type": self.signal_type,
                "entry_reason": self.entry_reason,
                "strike": self.strike,
                "atm_strike": self.atm_strike,
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
            "risk": self.risk.status_payload(),
        }
