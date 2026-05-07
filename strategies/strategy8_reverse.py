"""
Strategy 8 — CE/PE Reverse Line Touch Entry.

Mirrors Strategy 7 in every way EXCEPT entry direction:

  • CALL line touched on CE LTP   →  BUY a PUT  (reverse)
  • PUT  line touched on PE LTP   →  BUY a CALL (reverse)

Two reverse-strike selection modes:
  • AUTO   : 200-point ITM reverse strike, computed from the monitored
             strike. Ex.: monitoring 24300CE → BUY 24500PE.
                          monitoring 24300PE → BUY 24100CE.
  • MANUAL : user provides explicit `manual_pe_strike` (reverse PUT used
             when CALL line is hit) and `manual_ce_strike` (reverse CALL
             used when PUT line is hit).

Trigger semantics (per spec):
  • CALL trigger fires when  ce_ltp  >=  call_line
  • PUT  trigger fires when  pe_ltp  >=  put_line

Every other behaviour (state machine, shadow SL/TGT promotion, 60s
stale-order cancel, 15:15 squareoff, daily reset, persistence, trade
log, slippage flatten) is identical to Strategy 7.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime
from enum import Enum
from typing import Optional

from config import settings
from core.broker import (
    Broker, OrderRequest,
    Exchange, OrderSide, OrderType, ProductType,
)
from core.logger import get_logger

logger = get_logger("strategy8.reverse")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy8_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy8_trades.json"

REVERSE_OFFSET_DEFAULT = 200  # 200-point ITM reverse strike


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy8Reverse:
    """CE/PE reverse line touch-entry strategy."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # ── Risk / sizing ──
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

        # ── Reverse-strike selection mode ──
        # "AUTO" → 200-pt ITM reverse strike;  "MANUAL" → user picked
        self.reverse_mode: str = str(config.get("reverse_mode", "AUTO")).upper()
        self.reverse_offset: int = int(config.get("reverse_offset", REVERSE_OFFSET_DEFAULT))
        # Manual reverse strikes
        self.manual_pe_strike: int = int(config.get("manual_pe_strike", 0) or 0)  # used on CALL trigger
        self.manual_ce_strike: int = int(config.get("manual_ce_strike", 0) or 0)  # used on PUT  trigger
        self.manual_pe_symbol: str = str(config.get("manual_pe_symbol", "") or "")
        self.manual_ce_symbol: str = str(config.get("manual_ce_symbol", "") or "")
        self.manual_pe_token: int  = int(config.get("manual_pe_token", 0) or 0)
        self.manual_ce_token: int  = int(config.get("manual_ce_token", 0) or 0)

        # ── User-defined lines (option-price levels, monitored side) ──
        self.call_line: float = float(config.get("call_line", 0) or 0)
        self.put_line:  float = float(config.get("put_line", 0) or 0)

        # ── Monitored strikes ──
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
        self.signal: str = "NO_TRADE"  # REVERSE_BUY_PUT / REVERSE_BUY_CALL / NO_TRADE
        self._trading_date: Optional[date] = None
        self._check_lock = threading.Lock()

        # ── Live LTPs ──
        self.ce_ltp: float = 0.0
        self.pe_ltp: float = 0.0
        self._prev_ce: float = 0.0
        self._prev_pe: float = 0.0
        # Spot is still resolved internally for ATM math, but the UI
        # never shows an index card / chart — it is reference only.
        self.spot_price: float = 0.0

        # ── Active trade ──
        self.signal_type: Optional[str] = None  # "CE" / "PE" — option BOUGHT
        self.trigger_side: Optional[str] = None # "CALL" / "PUT"  — line that fired
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

        # ── Visualisation hooks (consumed by frontend) ──
        self.last_trigger_at: Optional[str] = None
        self.last_trigger_side: Optional[str] = None
        self.last_trigger_price: float = 0.0

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
            "Strategy 8 (REVERSE) started: mode=%s offset=%d CE=%s PE=%s CALL=%.2f PUT=%.2f",
            self.reverse_mode, self.reverse_offset,
            self.ce_symbol or self.ce_strike, self.pe_symbol or self.pe_strike,
            self.call_line, self.put_line,
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 8 stopped")

    def apply_config(self, config: dict, save: bool = True) -> None:
        for k in ("sl_points", "target_points", "sl_proximity", "target_proximity",
                  "max_entry_slippage"):
            if k in config:
                setattr(self, k, float(config.get(k) or 0) or getattr(self, k))
        for k in ("lot_size", "strike_interval", "max_trades_per_day", "reverse_offset"):
            if k in config:
                setattr(self, k, int(config.get(k) or 0) or getattr(self, k))
        if "lots" in config:
            self.lots = max(1, int(config.get("lots") or self.lots))
        if "index_name" in config:
            self.index_name = str(config.get("index_name") or self.index_name).upper()
        if "reverse_mode" in config:
            self.reverse_mode = str(config.get("reverse_mode") or "AUTO").upper()
        if "manual_pe_strike" in config:
            self.manual_pe_strike = int(config.get("manual_pe_strike") or 0)
        if "manual_ce_strike" in config:
            self.manual_ce_strike = int(config.get("manual_ce_strike") or 0)
        if "manual_pe_symbol" in config:
            self.manual_pe_symbol = str(config.get("manual_pe_symbol") or "")
        if "manual_ce_symbol" in config:
            self.manual_ce_symbol = str(config.get("manual_ce_symbol") or "")
        if "manual_pe_token" in config:
            self.manual_pe_token = int(config.get("manual_pe_token") or 0)
        if "manual_ce_token" in config:
            self.manual_ce_token = int(config.get("manual_ce_token") or 0)
        if "call_line" in config:
            self.call_line = float(config.get("call_line") or 0)
        if "put_line" in config:
            self.put_line = float(config.get("put_line") or 0)

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
            self._prev_ce = self.ce_ltp
            self._prev_pe = self.pe_ltp
            self._save_state()
            logger.info(
                "S8 lines updated: CALL=%.2f (CE=%.2f) | PUT=%.2f (PE=%.2f)",
                self.call_line, self.ce_ltp, self.put_line, self.pe_ltp,
            )
        return {"call_line": self.call_line, "put_line": self.put_line}

    def set_strikes(self, ce: Optional[dict], pe: Optional[dict]) -> dict:
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
        self._resolve_missing_strike_symbols()
        self._save_state()
        return {
            "ce_strike": self.ce_strike, "ce_symbol": self.ce_symbol,
            "pe_strike": self.pe_strike, "pe_symbol": self.pe_symbol,
        }

    def set_reverse_strikes(self, manual_pe: Optional[dict], manual_ce: Optional[dict]) -> dict:
        """Manual mode — user picks the reverse strikes that will be bought."""
        if manual_pe is not None:
            self.manual_pe_strike = int(manual_pe.get("strike") or 0)
            self.manual_pe_symbol = str(manual_pe.get("tradingsymbol") or "")
            self.manual_pe_token  = int(manual_pe.get("token") or 0)
        if manual_ce is not None:
            self.manual_ce_strike = int(manual_ce.get("strike") or 0)
            self.manual_ce_symbol = str(manual_ce.get("tradingsymbol") or "")
            self.manual_ce_token  = int(manual_ce.get("token") or 0)
        self._save_state()
        return {
            "manual_pe_strike": self.manual_pe_strike,
            "manual_pe_symbol": self.manual_pe_symbol,
            "manual_ce_strike": self.manual_ce_strike,
            "manual_ce_symbol": self.manual_ce_symbol,
        }

    def set_reverse_mode(self, mode: str) -> str:
        m = (mode or "AUTO").upper()
        if m not in ("AUTO", "MANUAL"):
            m = "AUTO"
        self.reverse_mode = m
        self._save_state()
        return self.reverse_mode

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
            logger.warning("S8 orphaned %s from %s — recording BROKER_SQUAREOFF",
                           self.state.value, old_date)
            trade = {
                "date": (old_date or today).isoformat(),
                "signal": self.signal_type,
                "trigger_side": self.trigger_side,
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
        self.state = State.IDLE
        self.scenario = "—"
        self.signal = "NO_TRADE"
        self.signal_type = None
        self.trigger_side = None
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
        keys = []
        if self.ce_symbol:
            keys.append(f"NFO:{self.ce_symbol}")
        if self.pe_symbol:
            keys.append(f"NFO:{self.pe_symbol}")
        # Spot is fetched silently for ATM math; never surfaced as primary card.
        keys.append("NSE:NIFTY 50")
        try:
            ltp = self.broker.get_ltp(keys) or {}
        except Exception as exc:
            logger.debug("S8 LTP fetch failed: %s", exc)
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
            logger.warning("S8 intraday fetch failed: %s", exc)
            return []
        out: list[dict] = []
        for c in candles:
            ts = c.get("date")
            t_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else (str(ts)[-8:] if ts else "")
            out.append({"t": t_str, "y": float(c.get("close", 0))})
        return out

    # ── Reverse-strike resolution ────────────────────

    def _resolve_reverse_target(self, trigger_side: str) -> Optional[dict]:
        """Determine the option that should be BOUGHT in response to a touch.

        trigger_side: "CALL"  → BUY a PUT  (reverse)
                      "PUT"   → BUY a CALL (reverse)
        """
        if trigger_side == "CALL":
            target_type = "PE"
            if self.reverse_mode == "MANUAL" and self.manual_pe_strike > 0:
                strike = self.manual_pe_strike
                symbol = self.manual_pe_symbol
                token  = self.manual_pe_token
            else:
                if not self.ce_strike:
                    return None
                strike = int(self.ce_strike + self.reverse_offset)
                symbol, token = "", 0
        else:  # "PUT"
            target_type = "CE"
            if self.reverse_mode == "MANUAL" and self.manual_ce_strike > 0:
                strike = self.manual_ce_strike
                symbol = self.manual_ce_symbol
                token  = self.manual_ce_token
            else:
                if not self.pe_strike:
                    return None
                strike = int(self.pe_strike - self.reverse_offset)
                symbol, token = "", 0

        if not symbol or not token:
            opt = self._find_option(strike, target_type)
            if not opt:
                return None
            symbol = opt["tradingsymbol"]
            token  = int(opt["instrument_token"])
            lot = int(opt.get("lot_size") or 0)
        else:
            lot = self.lot_size

        return {
            "type": target_type, "strike": strike,
            "tradingsymbol": symbol, "token": token,
            "lot_size": lot or self.lot_size,
        }

    # ── Main check ────────────────────────────────────

    def check(self, *_args, **_kwargs) -> dict:
        if not self.is_active and self.state != State.POSITION_OPEN:
            return self.get_status()
        if not self._check_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self.last_check_at = datetime.now()
            self._check_day_reset()
            self.fetch_ltps()

            if self.state == State.POSITION_OPEN and datetime.now().time() >= PRE_CLOSE_EXIT:
                logger.info("S8 auto square-off triggered")
                self._auto_square_off()
                return self.get_status()

            if self.state == State.IDLE:
                self._scan_for_touch()
            elif self.state == State.ORDER_PLACED:
                self._check_entry_fill()
            elif self.state == State.POSITION_OPEN:
                self._check_exit()

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

        # CASE-1: CE LTP >= CALL line  →  BUY PUT (reverse)
        if self.call_line > 0 and self.ce_ltp > 0 and self.ce_strike > 0:
            if self.ce_ltp >= self.call_line:
                self.scenario = (
                    f"CALL line {self.call_line:.2f} hit on {self.ce_symbol} "
                    f"({self.ce_ltp:.2f}) → REVERSE BUY PUT"
                )
                self.signal = "REVERSE_BUY_PUT"
                self.trigger_side = "CALL"
                self.entry_reason = (
                    f"CE LTP {self.ce_ltp:.2f} ≥ CALL line {self.call_line:.2f} (reverse)"
                )
                self.last_trigger_at = datetime.now().isoformat()
                self.last_trigger_side = "CALL"
                self.last_trigger_price = self.call_line
                self._fire_entry("CALL")
                return

        # CASE-2: PE LTP >= PUT line  →  BUY CALL (reverse)
        if self.put_line > 0 and self.pe_ltp > 0 and self.pe_strike > 0:
            if self.pe_ltp >= self.put_line:
                self.scenario = (
                    f"PUT line {self.put_line:.2f} hit on {self.pe_symbol} "
                    f"({self.pe_ltp:.2f}) → REVERSE BUY CALL"
                )
                self.signal = "REVERSE_BUY_CALL"
                self.trigger_side = "PUT"
                self.entry_reason = (
                    f"PE LTP {self.pe_ltp:.2f} ≥ PUT line {self.put_line:.2f} (reverse)"
                )
                self.last_trigger_at = datetime.now().isoformat()
                self.last_trigger_side = "PUT"
                self.last_trigger_price = self.put_line
                self._fire_entry("PUT")
                return

        bits = []
        if self.call_line > 0 and self.ce_symbol:
            bits.append(f"CALL {self.call_line:.2f} ({self.ce_strike}CE @ {self.ce_ltp:.2f})")
        if self.put_line > 0 and self.pe_symbol:
            bits.append(f"PUT {self.put_line:.2f} ({self.pe_strike}PE @ {self.pe_ltp:.2f})")
        self.scenario = "Armed (REVERSE) | " + " · ".join(bits) if bits else "Select strikes & lines to arm"
        self.signal = "NO_TRADE"

    # ── Entry / exit / orders ─────────────────────────

    def _fire_entry(self, trigger_side: str):
        target = self._resolve_reverse_target(trigger_side)
        if not target:
            logger.error("S8 cannot resolve reverse target for %s", trigger_side)
            self.scenario = f"Cannot resolve reverse {trigger_side} target"
            return
        self.signal_type = target["type"]
        self.trigger_side = trigger_side
        self.atm_strike = round(self.spot_price / self.strike_interval) * self.strike_interval if self.spot_price > 0 else 0
        self.strike = int(target["strike"])
        self.option_symbol = str(target["tradingsymbol"])
        self.option_token = int(target["token"])
        self.lot_size = int(target.get("lot_size") or self.lot_size)
        # Reference LTP for slippage check
        try:
            ltp = self.broker.get_ltp([f"NFO:{self.option_symbol}"]) or {}
            self.option_ltp = float(ltp.get(f"NFO:{self.option_symbol}", 0) or 0)
        except Exception:
            self.option_ltp = 0.0

        if not self.option_symbol:
            logger.error("S8 cannot fire entry — option symbol missing")
            self.state = State.IDLE
            return

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
                tag="S8ENTRY",
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
                logger.info("S8 entry order placed: %s", resp.order_id)
        except Exception as exc:
            logger.error("S8 entry order failed: %s", exc)
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
                    logger.info("S8 entry stale (%.0fs) — cancelling", elapsed)
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
                                "S8 entry slippage breach: ref=%.2f fill=%.2f slip=%.2f > max=%.2f",
                                ref, self.fill_price, slip, self.max_entry_slippage,
                            )
                            self._slippage_flatten(ref, slip)
                            return
                        self._on_entry_filled()
                    elif status in ("CANCELLED", "REJECTED"):
                        logger.warning("S8 entry %s — re-arm", status)
                        self._reset_to_idle(scenario=f"Entry {status} — re-arming")
                    break
        except Exception as exc:
            logger.error("S8 fill check failed: %s", exc)

    def _on_entry_filled(self):
        self.target_price = float(self.fill_price + self.target_points)
        self.sl_price = max(0.05, self.fill_price - self.sl_points)
        self.sl_shadow = True
        self.target_shadow = True
        self.sl_order = {"status": "SHADOW", "is_paper": False, "price": self.sl_price, "order_id": None}
        self.target_order = {"status": "SHADOW", "is_paper": False, "price": self.target_price, "order_id": None}
        self.state = State.POSITION_OPEN
        self._save_state()
        logger.info(
            "S8 REVERSE entry filled: %s @ %.2f | SL %.2f | TGT %.2f",
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
            logger.debug("S8 LTP refresh failed: %s", exc)

    def _check_exit(self):
        if not self.option_symbol or self.fill_price <= 0:
            return
        ltp = self.current_ltp or self.option_ltp
        if ltp <= 0:
            return
        if self.sl_shadow and ltp <= (self.sl_price + self.sl_proximity):
            self._promote_sl_to_market()
        if self.target_shadow and ltp >= (self.target_price - self.target_proximity):
            self._promote_target_to_market()
        if self.sl_shadow and ltp <= self.sl_price:
            self._exit_position("SL_HIT", ltp)
            return
        if self.target_shadow and ltp >= self.target_price:
            self._exit_position("TARGET_HIT", ltp)
            return
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
                trigger_price=self.sl_price, price=self.sl_price, tag="S8SL",
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
            logger.error("S8 promote SL failed: %s", exc)

    def _promote_target_to_market(self):
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                side=OrderSide.SELL, quantity=self.quantity,
                order_type=OrderType.LIMIT, product=ProductType.MIS,
                price=self.target_price, tag="S8TGT",
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
            logger.error("S8 promote TGT failed: %s", exc)

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
        try:
            if exit_type == "SL_HIT" and self.target_order and not self.target_shadow:
                self._cancel_order(self.target_order)
            if exit_type == "TARGET_HIT" and self.sl_order and not self.sl_shadow:
                self._cancel_order(self.sl_order)
        except Exception:
            pass
        if (exit_type == "SL_HIT" and self.sl_shadow) or \
           (exit_type == "TARGET_HIT" and self.target_shadow):
            try:
                req = OrderRequest(
                    tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                    side=OrderSide.SELL, quantity=self.quantity,
                    order_type=OrderType.MARKET, product=ProductType.MIS,
                    tag=f"S8{'SL' if exit_type=='SL_HIT' else 'TGT'}",
                )
                resp = self.broker.place_order(req)
                if exit_type == "SL_HIT":
                    self.sl_order = {"order_id": resp.order_id, "status": resp.status, "is_paper": resp.is_paper, "price": exit_price}
                    self.sl_shadow = False
                else:
                    self.target_order = {"order_id": resp.order_id, "status": resp.status, "is_paper": resp.is_paper, "price": exit_price}
                    self.target_shadow = False
            except Exception as exc:
                logger.error("S8 market exit failed: %s", exc)

        pnl = round((exit_price - self.fill_price) * self.quantity, 2)
        trade = {
            "date": (self._trading_date or date.today()).isoformat(),
            "signal": self.signal_type,
            "trigger_side": self.trigger_side,
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
        logger.info("S8 trade completed: %s @ %.2f (PnL %.2f)", exit_type, exit_price, pnl)

        if self._trades_today < self.max_trades_per_day:
            self._reset_to_idle(scenario=f"Last: {exit_type} | re-arm allowed")

    def _auto_square_off(self):
        if self.option_symbol and self.fill_price > 0:
            ltp = self.current_ltp or self.option_ltp or self.fill_price
            try:
                if self.sl_order and not self.sl_shadow:
                    self._cancel_order(self.sl_order)
                if self.target_order and not self.target_shadow:
                    self._cancel_order(self.target_order)
                req = OrderRequest(
                    tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                    side=OrderSide.SELL, quantity=self.quantity,
                    order_type=OrderType.MARKET, product=ProductType.MIS,
                    tag="S8SQOFF",
                )
                self.broker.place_order(req)
            except Exception as exc:
                logger.error("S8 squareoff failed: %s", exc)
            self._exit_position("AUTO_SQUAREOFF", ltp)
        self.is_active = False
        self.state = State.COMPLETED
        self._save_state()

    def _slippage_flatten(self, ref: float, slip: float):
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                side=OrderSide.SELL, quantity=self.quantity,
                order_type=OrderType.MARKET, product=ProductType.MIS,
                tag="S8SLIP",
            )
            self.broker.place_order(req)
        except Exception as exc:
            logger.error("S8 slippage flatten failed: %s", exc)
        self.scenario = f"Entry slippage {slip:.2f} > max — flattened"
        self._reset_to_idle(scenario=self.scenario, count_trade=True)

    def _cancel_order(self, order: Optional[dict]):
        if not order or not order.get("order_id") or order.get("is_paper"):
            return
        try:
            self.broker.cancel_order(order["order_id"])
        except Exception as exc:
            logger.debug("S8 cancel order failed: %s", exc)

    def _reset_to_idle(self, scenario: str = "—", count_trade: bool = False):
        if count_trade:
            self._trades_today += 1
        self.state = State.IDLE
        self.scenario = scenario
        self.signal = "NO_TRADE"
        self.signal_type = None
        self.trigger_side = None
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
                "reverse_mode": self.reverse_mode, "reverse_offset": self.reverse_offset,
                "manual_pe_strike": self.manual_pe_strike, "manual_pe_symbol": self.manual_pe_symbol, "manual_pe_token": self.manual_pe_token,
                "manual_ce_strike": self.manual_ce_strike, "manual_ce_symbol": self.manual_ce_symbol, "manual_ce_token": self.manual_ce_token,
                "signal_type": self.signal_type, "trigger_side": self.trigger_side,
                "entry_reason": self.entry_reason,
                "atm_strike": self.atm_strike, "strike": self.strike,
                "option_symbol": self.option_symbol, "option_token": self.option_token,
                "option_ltp": self.option_ltp, "fill_price": self.fill_price,
                "sl_price": self.sl_price, "target_price": self.target_price,
                "current_ltp": self.current_ltp,
                "entry_order": self.entry_order, "sl_order": self.sl_order,
                "target_order": self.target_order,
                "sl_shadow": self.sl_shadow, "target_shadow": self.target_shadow,
                "last_trigger_at": self.last_trigger_at, "last_trigger_side": self.last_trigger_side,
                "last_trigger_price": self.last_trigger_price,
                "trades_today": self._trades_today, "trade_log": self.trade_log,
            }, indent=2, default=str))
        except Exception as exc:
            logger.error("S8 state save failed: %s", exc)

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
            self.reverse_mode = str(data.get("reverse_mode") or "AUTO").upper()
            self.reverse_offset = int(data.get("reverse_offset") or REVERSE_OFFSET_DEFAULT)
            self.manual_pe_strike = int(data.get("manual_pe_strike") or 0)
            self.manual_ce_strike = int(data.get("manual_ce_strike") or 0)
            self.manual_pe_symbol = str(data.get("manual_pe_symbol") or "")
            self.manual_ce_symbol = str(data.get("manual_ce_symbol") or "")
            self.manual_pe_token = int(data.get("manual_pe_token") or 0)
            self.manual_ce_token = int(data.get("manual_ce_token") or 0)
            self.signal_type = data.get("signal_type")
            self.trigger_side = data.get("trigger_side")
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
            self.last_trigger_at = data.get("last_trigger_at")
            self.last_trigger_side = data.get("last_trigger_side")
            self.last_trigger_price = float(data.get("last_trigger_price") or 0)
            self._trades_today = int(data.get("trades_today") or 0)
            self.trade_log = list(data.get("trade_log") or [])
            return True
        except Exception as exc:
            logger.warning("S8 state restore failed: %s", exc)
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
            logger.error("S8 trade history append failed: %s", exc)

    # ── Status payload ───────────────────────────────

    def get_status(self) -> dict:
        unrealized = 0.0
        if self.state == State.POSITION_OPEN and self.current_ltp > 0 and self.fill_price > 0:
            unrealized = round((self.current_ltp - self.fill_price) * self.quantity, 2)
        # Pre-compute the reverse target preview for the UI
        preview_call = self._resolve_reverse_target("CALL") if (self.ce_strike or (self.reverse_mode == "MANUAL" and self.manual_pe_strike)) else None
        preview_put  = self._resolve_reverse_target("PUT")  if (self.pe_strike or (self.reverse_mode == "MANUAL" and self.manual_ce_strike)) else None

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
            "ltp": {"ce": self.ce_ltp, "pe": self.pe_ltp,
                    "ce_prev": self._prev_ce, "pe_prev": self._prev_pe},
            # spot_price kept for ATM math, not surfaced as a primary card
            "atm_ref": {"price": self.spot_price, "atm": self.atm_strike or (round(self.spot_price / self.strike_interval) * self.strike_interval if self.spot_price > 0 else 0)},
            "reverse": {
                "mode": self.reverse_mode,
                "offset": self.reverse_offset,
                "manual_pe_strike": self.manual_pe_strike,
                "manual_pe_symbol": self.manual_pe_symbol,
                "manual_ce_strike": self.manual_ce_strike,
                "manual_ce_symbol": self.manual_ce_symbol,
                "preview_on_call_trigger": preview_call,
                "preview_on_put_trigger":  preview_put,
            },
            "trigger": {
                "last_at": self.last_trigger_at,
                "last_side": self.last_trigger_side,
                "last_price": self.last_trigger_price,
            },
            "config": {
                "sl_points": self.sl_points, "target_points": self.target_points,
                "lot_size": self.lot_size, "lots": self.lots, "quantity": self.quantity,
                "strike_interval": self.strike_interval,
                "sl_proximity": self.sl_proximity, "target_proximity": self.target_proximity,
                "max_trades_per_day": self.max_trades_per_day,
                "max_entry_slippage": self.max_entry_slippage,
                "index_name": self.index_name,
                "call_line": self.call_line, "put_line": self.put_line,
                "reverse_mode": self.reverse_mode, "reverse_offset": self.reverse_offset,
            },
            "trade": {
                "signal_type": self.signal_type,
                "trigger_side": self.trigger_side,
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
                "entry": self.entry_order, "sl": self.sl_order,
                "target": self.target_order,
                "sl_shadow": self.sl_shadow, "target_shadow": self.target_shadow,
            },
            "trades_today": self._trades_today,
            "trade_log": self.trade_log[-20:],
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
        }
