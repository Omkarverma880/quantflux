"""
Strategy 9 — Line Of Control (LOC).

Direct, line-driven option trading. NO reverse logic. Each side (CE / PE)
has THREE independent user-drawn lines:

    • BUY line     (Entry)
    • TARGET line  (Exit on profit)
    • SL line      (Exit on loss)

Together they form the Line Of Control for that side.

Entry semantics (one-sided):
    • CE LTP  >=  ce_buy_line  →  BUY CE  (same strike that is monitored)
    • PE LTP  >=  pe_buy_line  →  BUY PE  (same strike that is monitored)

Exit semantics (after a fill on side X):
    • LTP >= X.target_line  →  MARKET exit (TARGET_HIT)
    • LTP <= X.sl_line      →  MARKET exit (SL_HIT)

Single-cycle rule:
    Once an entry has fired, NO further entries of any kind are placed
    until the open trade exits (target or SL or 15:15 squareoff). Lines
    on the *opposite* side are ignored during a live position.

Live line-edit:
    All six lines can be repositioned at any time, even during an open
    position. The new values take effect on the very next tick — no
    restart required, no reset of the trade state.
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
from core.risk_controller import RiskController

logger = get_logger("strategy9.loc")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy9_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy9_trades.json"


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy9LOC:
    """Line Of Control — 3-line direct option entry / exit engine."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # ── Sizing / risk ──
        self.lot_size           = int(config.get("lot_size", 65))
        self.lots               = max(1, int(config.get("lots", 1)))
        self.strike_interval    = int(config.get("strike_interval", 50))
        self.max_trades_per_day = max(1, int(config.get("max_trades_per_day", 3)))
        # Shadow→real promotion windows (option-price points). Match the
        # behaviour used by strategies 5 and 6: SL / target are tracked
        # internally until LTP comes within `proximity` of the line, then a
        # real exchange order is placed.
        self.sl_proximity       = float(config.get("sl_proximity", 5))
        self.target_proximity   = float(config.get("target_proximity", 5))
        self.index_name         = str(config.get("index_name", "NIFTY")).upper()

        # ── Six user-drawn lines (option-price levels) ──
        self.ce_buy_line:    float = float(config.get("ce_buy_line", 0) or 0)
        self.ce_target_line: float = float(config.get("ce_target_line", 0) or 0)
        self.ce_sl_line:     float = float(config.get("ce_sl_line", 0) or 0)
        self.pe_buy_line:    float = float(config.get("pe_buy_line", 0) or 0)
        self.pe_target_line: float = float(config.get("pe_target_line", 0) or 0)
        self.pe_sl_line:     float = float(config.get("pe_sl_line", 0) or 0)

        # ── Monitored strikes ──
        self.ce_strike: int = int(config.get("ce_strike", 0) or 0)
        self.pe_strike: int = int(config.get("pe_strike", 0) or 0)
        self.ce_symbol: str = str(config.get("ce_symbol", "") or "")
        self.pe_symbol: str = str(config.get("pe_symbol", "") or "")
        self.ce_token:  int = int(config.get("ce_token", 0) or 0)
        self.pe_token:  int = int(config.get("pe_token", 0) or 0)

        # ── State ──
        self.is_active: bool = False
        self.state: State = State.IDLE
        self.scenario: str = "—"
        self.signal: str = "NO_TRADE"  # BUY_CALL / BUY_PUT / NO_TRADE
        self._trading_date: Optional[date] = None
        self._check_lock = threading.Lock()

        # ── Live LTPs ──
        self.ce_ltp: float = 0.0
        self.pe_ltp: float = 0.0
        self._prev_ce: float = 0.0
        self._prev_pe: float = 0.0
        # Spot is fetched silently for ATM math; never the trigger source.
        self.spot_price: float = 0.0

        # ── Active trade ──
        self.signal_type: Optional[str] = None  # "CE" / "PE" — option BOUGHT
        self.trigger_side: Optional[str] = None # "CALL" / "PUT" — line that fired
        self.entry_reason: str = ""
        self.atm_strike: int = 0
        self.strike: int = 0
        self.option_symbol: str = ""
        self.option_token: int = 0
        self.option_ltp: float = 0.0
        self.fill_price: float = 0.0
        # Active target / SL — sourced from the user lines on the BOUGHT side.
        self.sl_price: float = 0.0
        self.target_price: float = 0.0
        self.entry_time: Optional[str] = None
        self.current_ltp: float = 0.0

        # ── Orders ──
        self.entry_order:  Optional[dict] = None
        self.sl_order:     Optional[dict] = None
        self.target_order: Optional[dict] = None
        # Shadow flags — True means the leg is tracked internally only.
        # Flips to False once a real SL-M / LIMIT order is on the exchange.
        self.sl_shadow:     bool = True
        self.target_shadow: bool = True

        # ── Visualisation hooks ──
        self.last_trigger_at: Optional[str] = None
        self.last_trigger_side: Optional[str] = None
        self.last_trigger_price: float = 0.0
        self.last_exit_at: Optional[str] = None
        self.last_exit_type: Optional[str] = None
        self.last_exit_price: float = 0.0

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
            "Strategy 9 (LOC) started: CE=%s PE=%s | CE buy=%.2f tgt=%.2f sl=%.2f | PE buy=%.2f tgt=%.2f sl=%.2f",
            self.ce_symbol or self.ce_strike, self.pe_symbol or self.pe_strike,
            self.ce_buy_line, self.ce_target_line, self.ce_sl_line,
            self.pe_buy_line, self.pe_target_line, self.pe_sl_line,
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 9 stopped")

    def apply_config(self, config: dict, save: bool = True) -> None:
        for k in ("sl_proximity", "target_proximity"):
            if k in config:
                try:
                    setattr(self, k, float(config.get(k) or 0))
                except (TypeError, ValueError):
                    pass
        for k in ("lot_size", "strike_interval", "max_trades_per_day"):
            if k in config:
                setattr(self, k, int(config.get(k) or 0) or getattr(self, k))
        if "lots" in config:
            self.lots = max(1, int(config.get("lots") or self.lots))
        if "index_name" in config:
            self.index_name = str(config.get("index_name") or self.index_name).upper()

        # Lines arrive on every config push — apply them so live edits work.
        self.set_lines(
            ce_buy_line=config.get("ce_buy_line"),
            ce_target_line=config.get("ce_target_line"),
            ce_sl_line=config.get("ce_sl_line"),
            pe_buy_line=config.get("pe_buy_line"),
            pe_target_line=config.get("pe_target_line"),
            pe_sl_line=config.get("pe_sl_line"),
            save=False,
        )

        if save:
            self._save_state()

    def set_lines(
        self,
        ce_buy_line: Optional[float] = None,
        ce_target_line: Optional[float] = None,
        ce_sl_line: Optional[float] = None,
        pe_buy_line: Optional[float] = None,
        pe_target_line: Optional[float] = None,
        pe_sl_line: Optional[float] = None,
        save: bool = True,
    ) -> dict:
        def _set(attr: str, val):
            if val is None:
                return False
            v = float(val or 0)
            if v == getattr(self, attr):
                return False
            setattr(self, attr, v)
            return True

        changed = False
        changed |= _set("ce_buy_line",    ce_buy_line)
        changed |= _set("ce_target_line", ce_target_line)
        changed |= _set("ce_sl_line",     ce_sl_line)
        changed |= _set("pe_buy_line",    pe_buy_line)
        changed |= _set("pe_target_line", pe_target_line)
        changed |= _set("pe_sl_line",     pe_sl_line)

        # While in position, target / SL track their respective user lines.
        if self.state == State.POSITION_OPEN and self.signal_type:
            if self.signal_type == "CE":
                if self.ce_target_line > 0:
                    self.target_price = self.ce_target_line
                if self.ce_sl_line > 0:
                    self.sl_price = max(0.05, self.ce_sl_line)
            else:
                if self.pe_target_line > 0:
                    self.target_price = self.pe_target_line
                if self.pe_sl_line > 0:
                    self.sl_price = max(0.05, self.pe_sl_line)

        if changed:
            self._prev_ce = self.ce_ltp
            self._prev_pe = self.pe_ltp
            if save:
                self._save_state()
            logger.info(
                "S9 lines updated: CE buy=%.2f tgt=%.2f sl=%.2f | PE buy=%.2f tgt=%.2f sl=%.2f",
                self.ce_buy_line, self.ce_target_line, self.ce_sl_line,
                self.pe_buy_line, self.pe_target_line, self.pe_sl_line,
            )
        return self._lines_payload()

    def _lines_payload(self) -> dict:
        return {
            "ce": {"buy": self.ce_buy_line, "target": self.ce_target_line, "sl": self.ce_sl_line},
            "pe": {"buy": self.pe_buy_line, "target": self.pe_target_line, "sl": self.pe_sl_line},
        }

    def set_strikes(self, ce: Optional[dict], pe: Optional[dict]) -> dict:
        if ce is not None:
            new_strike = int(ce.get("strike") or 0)
            new_symbol = str(ce.get("tradingsymbol") or "")
            new_token  = int(ce.get("token") or 0)
            if new_strike != self.ce_strike or (new_symbol and new_symbol != self.ce_symbol):
                self.ce_strike = new_strike
                if new_symbol:
                    self.ce_symbol = new_symbol
                if new_token:
                    self.ce_token = new_token
                self.ce_ltp = 0.0
                self._prev_ce = 0.0
        if pe is not None:
            new_strike = int(pe.get("strike") or 0)
            new_symbol = str(pe.get("tradingsymbol") or "")
            new_token  = int(pe.get("token") or 0)
            if new_strike != self.pe_strike or (new_symbol and new_symbol != self.pe_symbol):
                self.pe_strike = new_strike
                if new_symbol:
                    self.pe_symbol = new_symbol
                if new_token:
                    self.pe_token = new_token
                self.pe_ltp = 0.0
                self._prev_pe = 0.0
        # Only run the (potentially slow) instruments lookup if the frontend
        # didn't already supply a symbol — typical case is symbol+token are
        # both present, so this is a no-op fast path.
        if (self.ce_strike and not self.ce_symbol) or (self.pe_strike and not self.pe_symbol):
            try:
                self._resolve_missing_strike_symbols()
            except Exception as exc:
                logger.warning("S9 strike symbol resolve failed: %s", exc)
        try:
            self._save_state()
        except Exception as exc:
            logger.warning("S9 set_strikes save_state failed: %s", exc)
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
            logger.warning("S9 orphaned %s from %s — recording BROKER_SQUAREOFF",
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
        keys.append("NSE:NIFTY 50")
        try:
            ltp = self.broker.get_ltp(keys) or {}
        except Exception as exc:
            logger.debug("S9 LTP fetch failed: %s", exc)
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
            logger.warning("S9 intraday fetch failed: %s", exc)
            return []
        out: list[dict] = []
        for c in candles:
            ts = c.get("date")
            t_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else (str(ts)[-8:] if ts else "")
            out.append({"t": t_str, "y": float(c.get("close", 0))})
        return out

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
                logger.info("S9 auto square-off triggered")
                self._auto_square_off()
                return self.get_status()

            if self.state == State.IDLE:
                self._scan_for_entry()
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

    def _scan_for_entry(self):
        # Single-cycle: nothing to do if we are not idle. (already enforced)
        ce_armed = self.ce_buy_line > 0 and self.ce_strike > 0 and self.ce_symbol
        pe_armed = self.pe_buy_line > 0 and self.pe_strike > 0 and self.pe_symbol
        if not ce_armed and not pe_armed:
            self.scenario = "Draw a BUY line on CE or PE side to arm"
            return
        if self._trades_today >= self.max_trades_per_day:
            self.scenario = "Max trades reached"
            return

        # Tick-level fresh-crossover arming
        if self.ce_ltp > 0:
            self.risk.update_price_for_arming(side="CALL", current_price=self.ce_ltp)
        if self.pe_ltp > 0:
            self.risk.update_price_for_arming(side="PUT", current_price=self.pe_ltp)

        # CE side — strict UP-crossover through BUY line (prev < line <= ltp)
        if ce_armed and self.ce_ltp > 0:
            prev_ce = self._prev_ce or self.ce_ltp
            crossed_up = prev_ce < self.ce_buy_line <= self.ce_ltp
            equal_touch = (
                abs(self.ce_ltp - self.ce_buy_line) < 0.05
                and prev_ce != self.ce_ltp
                and prev_ce < self.ce_buy_line
            )
            if crossed_up or equal_touch:
                ok, reason = self.risk.allow_entry(
                    side="CALL", current_price=self.ce_ltp, line_price=self.ce_buy_line
                )
                if not ok:
                    self.scenario = f"CE BUY blocked — {reason}"
                    self.signal = "NO_TRADE"
                    return
                self.scenario = (
                    f"CE BUY line {self.ce_buy_line:.2f} crossed on {self.ce_symbol} "
                    f"({prev_ce:.2f} → {self.ce_ltp:.2f}) → BUY CALL"
                )
                self.signal = "BUY_CALL"
                self.trigger_side = "CALL"
                self.entry_reason = (
                    f"CE LTP crossed {prev_ce:.2f} → {self.ce_ltp:.2f} through CE BUY line {self.ce_buy_line:.2f}"
                )
                self.last_trigger_at = datetime.now().isoformat()
                self.last_trigger_side = "CALL"
                self.last_trigger_price = self.ce_buy_line
                self._fire_entry("CE")
                return

        # PE side — strict UP-crossover through BUY line (prev < line <= ltp)
        if pe_armed and self.pe_ltp > 0:
            prev_pe = self._prev_pe or self.pe_ltp
            crossed_up = prev_pe < self.pe_buy_line <= self.pe_ltp
            equal_touch = (
                abs(self.pe_ltp - self.pe_buy_line) < 0.05
                and prev_pe != self.pe_ltp
                and prev_pe < self.pe_buy_line
            )
            if crossed_up or equal_touch:
                ok, reason = self.risk.allow_entry(
                    side="PUT", current_price=self.pe_ltp, line_price=self.pe_buy_line
                )
                if not ok:
                    self.scenario = f"PE BUY blocked — {reason}"
                    self.signal = "NO_TRADE"
                    return
                self.scenario = (
                    f"PE BUY line {self.pe_buy_line:.2f} crossed on {self.pe_symbol} "
                    f"({prev_pe:.2f} → {self.pe_ltp:.2f}) → BUY PUT"
                )
                self.signal = "BUY_PUT"
                self.trigger_side = "PUT"
                self.entry_reason = (
                    f"PE LTP crossed {prev_pe:.2f} → {self.pe_ltp:.2f} through PE BUY line {self.pe_buy_line:.2f}"
                )
                self.last_trigger_at = datetime.now().isoformat()
            self.last_trigger_side = "PUT"
            self.last_trigger_price = self.pe_buy_line
            self._fire_entry("PE")
            return

        bits = []
        if ce_armed:
            bits.append(f"CE buy {self.ce_buy_line:.2f} ({self.ce_strike}CE @ {self.ce_ltp:.2f})")
        if pe_armed:
            bits.append(f"PE buy {self.pe_buy_line:.2f} ({self.pe_strike}PE @ {self.pe_ltp:.2f})")
        self.scenario = "Armed (LOC) | " + " · ".join(bits) if bits else "Draw lines to arm"
        self.signal = "NO_TRADE"

    # ── Entry / exit / orders ─────────────────────────

    def _fire_entry(self, side: str):
        """side = "CE" / "PE" — option to be BOUGHT (same as monitored)."""
        if side == "CE":
            symbol, token, strike = self.ce_symbol, self.ce_token, self.ce_strike
            ref_ltp = self.ce_ltp
            buy_line = self.ce_buy_line
            tgt_line = self.ce_target_line
            sl_line  = self.ce_sl_line
        else:
            symbol, token, strike = self.pe_symbol, self.pe_token, self.pe_strike
            ref_ltp = self.pe_ltp
            buy_line = self.pe_buy_line
            tgt_line = self.pe_target_line
            sl_line  = self.pe_sl_line
        if not symbol:
            logger.error("S9 cannot fire — option symbol missing for %s", side)
            return

        # ── Pre-trade validity guards ──
        # No "gap-up" rejection any more. The entry is sent as a LIMIT order
        # pegged to the user's BUY line itself — the broker physically cannot
        # fill above it, so a gap-up simply results in the order resting
        # unfilled (and getting stale-cancelled). That is the user's stated
        # intent: "fill anywhere at-or-below my BUY line, never above".
        #
        # Sanity guards: if LTP is already at/past the target or at/below SL
        # the trade is born stopped-out / has no upside, so skip it.
        if tgt_line > 0 and ref_ltp >= tgt_line:
            self.scenario = (
                f"{side} entry skipped — LTP {ref_ltp:.2f} \u2265 target line {tgt_line:.2f}"
            )
            self.signal = "NO_TRADE"
            self.trigger_side = None
            return
        if sl_line > 0 and ref_ltp <= sl_line:
            self.scenario = (
                f"{side} entry skipped — LTP {ref_ltp:.2f} \u2264 SL line {sl_line:.2f}"
            )
            self.signal = "NO_TRADE"
            self.trigger_side = None
            return

        self.signal_type = side
        self.atm_strike = round(self.spot_price / self.strike_interval) * self.strike_interval if self.spot_price > 0 else 0
        self.strike = int(strike)
        self.option_symbol = symbol
        self.option_token = int(token)
        self.option_ltp = float(ref_ltp or 0)
        # Remember the user's BUY line for this fire — it is also the LIMIT
        # price cap used by _place_entry_order.
        self._entry_buy_line = float(buy_line or 0)
        self.entry_time = datetime.now().strftime("%H:%M:%S")
        self._place_entry_order()

    def _place_entry_order(self):
        prev_state = self.state
        self.state = State.ORDER_PLACED
        # LIMIT BUY at the user's BUY line EXACTLY. For an option BUY, the
        # broker's matching engine will fill at the lowest available ask
        # that is ≤ the limit price — so a fill can ONLY happen at or below
        # the line. If the market gaps above, the order rests unfilled and
        # is stale-cancelled (the user's explicit preference: "miss the
        # trade, never overpay"). max_entry_slippage is gone — the LIMIT
        # price IS the slippage guard, enforced by the exchange itself.
        buy_line = float(getattr(self, "_entry_buy_line", 0) or 0)
        if buy_line > 0:
            limit_price = round(buy_line, 2)
            order_type = OrderType.LIMIT
        else:
            # Defensive fallback — should not happen because _fire_entry only
            # runs when buy_line > 0, but keep MARKET as a last-resort path.
            limit_price = 0.0
            order_type = OrderType.MARKET
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol,
                exchange=Exchange.NFO,
                side=OrderSide.BUY,
                quantity=self.quantity,
                order_type=order_type,
                product=ProductType.MIS,
                price=limit_price,
                tag="S9ENTRY",
            )
            resp = self.broker.place_order(req)
            self.entry_order = {
                "order_id": resp.order_id,
                "status": resp.status,
                "is_paper": resp.is_paper,
                "price": self.option_ltp,
                "buy_line": buy_line,
                "limit_price": limit_price,
                "timestamp": datetime.now().isoformat(),
            }
            if resp.is_paper and resp.status == "COMPLETE":
                self.fill_price = self.option_ltp
                self.entry_order["status"] = "COMPLETE"
                self._on_entry_filled()
            elif str(resp.status).upper() in ("REJECTED", "CANCELLED"):
                logger.warning(
                    "S9 entry %s synchronously — resetting to IDLE for re-arm",
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
                logger.info("S9 entry order placed: %s", resp.order_id)
        except Exception as exc:
            logger.error("S9 entry order failed: %s", exc)
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
                # Tight 10s window: a touch-line entry that doesn't fill almost
                # immediately means price has moved away from the BUY line and
                # the setup is no longer valid. Don't let stale orders sit and
                # fill 30+ seconds later at a much worse price.
                if elapsed > 10 and self.entry_order.get("status") != "COMPLETE":
                    logger.info("S9 entry stale (%.0fs) — cancelling", elapsed)
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
                        # No slippage check needed — the LIMIT order at the
                        # BUY line guarantees fill_price ≤ buy_line.
                        self._on_entry_filled()
                    elif status in ("CANCELLED", "REJECTED"):
                        logger.warning("S9 entry %s — re-arm", status)
                        self._reset_to_idle(scenario=f"Entry {status} — re-arming")
                    break
        except Exception as exc:
            logger.error("S9 fill check failed: %s", exc)

    def _on_entry_filled(self):
        # Target / SL come straight from the user lines on the bought side.
        if self.signal_type == "CE":
            self.target_price = self.ce_target_line if self.ce_target_line > 0 else self.fill_price * 1.5
            self.sl_price     = max(0.05, self.ce_sl_line) if self.ce_sl_line > 0 else max(0.05, self.fill_price * 0.7)
        else:
            self.target_price = self.pe_target_line if self.pe_target_line > 0 else self.fill_price * 1.5
            self.sl_price     = max(0.05, self.pe_sl_line) if self.pe_sl_line > 0 else max(0.05, self.fill_price * 0.7)
        # Start with both legs in SHADOW mode — no resting exchange orders
        # yet. They are promoted to real SL-M / LIMIT orders only when LTP
        # comes within sl_proximity / target_proximity of the line. This
        # mirrors strategies 5 and 6 and lets the user drag the lines freely
        # while far from price.
        self.sl_shadow = True
        self.target_shadow = True
        self.sl_order = {"status": "MONITOR", "is_paper": False, "price": self.sl_price, "order_id": None}
        self.target_order = {"status": "MONITOR", "is_paper": False, "price": self.target_price, "order_id": None}
        self.state = State.POSITION_OPEN
        self.risk.record_entry(side=self.trigger_side or "")
        self._save_state()
        logger.info(
            "S9 entry filled: %s @ %.2f | TGT %.2f (shadow) | SL %.2f (shadow)",
            self.option_symbol, self.fill_price, self.target_price, self.sl_price,
        )

    def _refresh_current_ltp(self):
        if self.state != State.POSITION_OPEN or not self.option_symbol:
            return
        # The bought option is the same as the monitored side — reuse LTP.
        if self.signal_type == "CE" and self.ce_ltp > 0:
            self.current_ltp = self.ce_ltp
            return
        if self.signal_type == "PE" and self.pe_ltp > 0:
            self.current_ltp = self.pe_ltp
            return
        try:
            ltp = self.broker.get_ltp([f"NFO:{self.option_symbol}"]) or {}
            v = float(ltp.get(f"NFO:{self.option_symbol}", 0) or 0)
            if v > 0:
                self.current_ltp = v
        except Exception as exc:
            logger.debug("S9 LTP refresh failed: %s", exc)

    def _check_exit(self):
        if not self.option_symbol or self.fill_price <= 0:
            return
        # Pull live target / SL straight from the user lines so live drag works.
        if self.signal_type == "CE":
            self.target_price = self.ce_target_line if self.ce_target_line > 0 else self.target_price
            self.sl_price     = max(0.05, self.ce_sl_line) if self.ce_sl_line > 0 else self.sl_price
            ltp = self.ce_ltp or self.current_ltp
        else:
            self.target_price = self.pe_target_line if self.pe_target_line > 0 else self.target_price
            self.sl_price     = max(0.05, self.pe_sl_line) if self.pe_sl_line > 0 else self.sl_price
            ltp = self.pe_ltp or self.current_ltp

        if ltp <= 0:
            return

        # ── Live-drag handling ──
        # If a real exchange order is already resting and the user has since
        # dragged the corresponding line to a new value, cancel the resting
        # order and revert to shadow. The proximity-promotion block below
        # will re-place at the new price on the very next tick that qualifies.
        if (not self.sl_shadow and self.sl_order
                and self.sl_order.get("order_id")
                and abs(float(self.sl_order.get("price", 0)) - self.sl_price) > 0.04):
            logger.info(
                "S9 SL line moved %.2f → %.2f — cancelling resting order to re-promote",
                float(self.sl_order.get("price", 0)), self.sl_price,
            )
            self._cancel_order(self.sl_order)
            self.sl_order = {"status": "MONITOR", "is_paper": False,
                             "price": self.sl_price, "order_id": None}
            self.sl_shadow = True
        if (not self.target_shadow and self.target_order
                and self.target_order.get("order_id")
                and abs(float(self.target_order.get("price", 0)) - self.target_price) > 0.04):
            logger.info(
                "S9 TGT line moved %.2f → %.2f — cancelling resting order to re-promote",
                float(self.target_order.get("price", 0)), self.target_price,
            )
            self._cancel_order(self.target_order)
            self.target_order = {"status": "MONITOR", "is_paper": False,
                                 "price": self.target_price, "order_id": None}
            self.target_shadow = True

        # ── If a real exchange order is already resting, watch it ──
        # If SL-M or TGT-LIMIT was promoted to the exchange, the broker will
        # execute it autonomously. Poll order status and react.
        if (not self.sl_shadow and self.sl_order and self.sl_order.get("order_id")) or \
           (not self.target_shadow and self.target_order and self.target_order.get("order_id")):
            try:
                orders = self.broker.get_orders()
            except Exception:
                orders = []
            for o in orders:
                oid = str(o.get("order_id", ""))
                status = o.get("status", "")
                if (
                    not self.sl_shadow and self.sl_order
                    and oid == str(self.sl_order.get("order_id"))
                ):
                    if status == "COMPLETE":
                        self._cancel_order(self.target_order)
                        self._exit_position("SL_HIT", self.sl_price, already_filled=True,
                                           fill_price=float(o.get("average_price", self.sl_price)))
                        return
                    if status in ("CANCELLED", "REJECTED"):
                        logger.warning("S9 SL order %s on exchange (%s) — reverting to shadow",
                                       oid, status)
                        self.sl_order = None
                        self.sl_shadow = True
                if (
                    not self.target_shadow and self.target_order
                    and oid == str(self.target_order.get("order_id"))
                ):
                    if status == "COMPLETE":
                        self._cancel_order(self.sl_order)
                        self._exit_position("TARGET_HIT", self.target_price, already_filled=True,
                                           fill_price=float(o.get("average_price", self.target_price)))
                        return
                    if status in ("CANCELLED", "REJECTED"):
                        logger.warning("S9 TGT order %s on exchange (%s) — reverting to shadow",
                                       oid, status)
                        self.target_order = None
                        self.target_shadow = True

        # ── Hard breach: LTP already past the line → MARKET exit ──
        # SL takes precedence on a same-tick conflict.
        if self.sl_price > 0 and ltp <= self.sl_price:
            self._exit_position("SL_HIT", ltp)
            return
        if self.target_price > 0 and ltp >= self.target_price:
            self._exit_position("TARGET_HIT", ltp)
            return

        # ── Proximity promotion: shadow → real exchange order ──
        # SL leg
        if self.sl_shadow and self.sl_price > 0 and ltp <= (self.sl_price + self.sl_proximity):
            logger.info(
                "S9 SL proximity hit: ltp=%.2f sl=%.2f prox=%.2f — promoting",
                ltp, self.sl_price, self.sl_proximity,
            )
            # When one leg goes real, the other reverts to shadow so we don't
            # leave a stale resting order on the exchange while the line might
            # still move under the user's drag.
            if not self.target_shadow and self.target_order:
                self._cancel_order(self.target_order)
                self.target_order = {"status": "MONITOR", "is_paper": False,
                                     "price": self.target_price, "order_id": None}
                self.target_shadow = True
            self.sl_shadow = False
            try:
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.SL_M,
                    product=ProductType.MIS, trigger_price=self.sl_price, tag="S9SL",
                ))
                self.sl_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "is_paper": getattr(resp, "is_paper", False),
                    "price": self.sl_price,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info("S9 SL-M order placed on exchange: %s", resp.order_id)
                self._save_state()
            except Exception as exc:
                logger.error("S9 SL placement failed: %s", exc)
                self.sl_shadow = True

        # Target leg
        if self.target_shadow and self.target_price > 0 and ltp >= (self.target_price - self.target_proximity):
            logger.info(
                "S9 TGT proximity hit: ltp=%.2f tgt=%.2f prox=%.2f — promoting",
                ltp, self.target_price, self.target_proximity,
            )
            if not self.sl_shadow and self.sl_order:
                self._cancel_order(self.sl_order)
                self.sl_order = {"status": "MONITOR", "is_paper": False,
                                 "price": self.sl_price, "order_id": None}
                self.sl_shadow = True
            self.target_shadow = False
            try:
                resp = self.broker.place_order(OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO, side=OrderSide.SELL,
                    quantity=self.quantity, order_type=OrderType.LIMIT,
                    product=ProductType.MIS, price=self.target_price, tag="S9TGT",
                ))
                self.target_order = {
                    "order_id": resp.order_id, "status": "OPEN",
                    "is_paper": getattr(resp, "is_paper", False),
                    "price": self.target_price,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info("S9 TGT LIMIT order placed on exchange: %s", resp.order_id)
                self._save_state()
            except Exception as exc:
                logger.error("S9 target placement failed: %s", exc)
                self.target_shadow = True

    def _exit_position(self, exit_type: str, exit_price: float,
                       already_filled: bool = False, fill_price: float = 0.0):
        # If the exchange already executed our promoted SL-M / LIMIT, skip
        # sending another MARKET order — just book the trade.
        if not already_filled:
            try:
                # Cancel any still-resting promoted leg before sending the
                # MARKET flatten so we don't double-sell.
                self._cancel_order(self.sl_order)
                self._cancel_order(self.target_order)
                req = OrderRequest(
                    tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                    side=OrderSide.SELL, quantity=self.quantity,
                    order_type=OrderType.MARKET, product=ProductType.MIS,
                    tag=f"S9{'SL' if exit_type=='SL_HIT' else 'TGT' if exit_type=='TARGET_HIT' else 'SQOFF'}",
                )
                resp = self.broker.place_order(req)
                ord_dict = {
                    "order_id": resp.order_id, "status": resp.status,
                    "is_paper": resp.is_paper, "price": exit_price,
                    "timestamp": datetime.now().isoformat(),
                }
                if exit_type == "SL_HIT":
                    self.sl_order = ord_dict
                else:
                    self.target_order = ord_dict
            except Exception as exc:
                logger.error("S9 exit order failed: %s", exc)
        else:
            exit_price = fill_price or exit_price

        pnl = round((exit_price - self.fill_price) * self.quantity, 2)
        try:
            line_price = (
                self.ce_buy_line if self.trigger_side == "CALL"
                else self.pe_buy_line
            )
            self.risk.record_exit(
                exit_type=exit_type,
                side=self.trigger_side or "",
                line_price=float(line_price or 0),
                pnl=pnl,
            )
        except Exception as exc:
            logger.warning("S9 risk.record_exit failed: %s", exc)
        trade = {
            "date": (self._trading_date or date.today()).isoformat(),
            "signal": self.signal_type,
            "trigger_side": self.trigger_side,
            "scenario": self.scenario,
            "option": self.option_symbol,
            "entry_price": self.fill_price,
            "entry_time": self.entry_time,
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
        self.last_exit_at = datetime.now().isoformat()
        self.last_exit_type = exit_type
        self.last_exit_price = exit_price

        self.state = State.COMPLETED
        self._save_state()
        logger.info("S9 trade completed: %s @ %.2f (PnL %.2f)", exit_type, exit_price, pnl)

        if self._trades_today < self.max_trades_per_day:
            self._reset_to_idle(scenario=f"Last: {exit_type} | re-arm allowed")

    def _auto_square_off(self):
        if self.option_symbol and self.fill_price > 0:
            ltp = self.current_ltp or self.option_ltp or self.fill_price
            self._exit_position("AUTO_SQUAREOFF", ltp)
        self.is_active = False
        self.state = State.COMPLETED
        self._save_state()

    def _slippage_flatten(self, ref: float, slip: float):
        """Retained for backward compatibility / state-file callers.
        No longer used in the live path — entry slippage is now structurally
        impossible because entry is a LIMIT order at the user's BUY line."""
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                side=OrderSide.SELL, quantity=self.quantity,
                order_type=OrderType.MARKET, product=ProductType.MIS,
                tag="S9SLIP",
            )
            self.broker.place_order(req)
        except Exception as exc:
            logger.error("S9 slippage flatten failed: %s", exc)
        self.scenario = f"Entry slippage {slip:.2f} > max — flattened"
        self._reset_to_idle(scenario=self.scenario, count_trade=True)

    def _cancel_order(self, order: Optional[dict]):
        if not order or not order.get("order_id") or order.get("is_paper"):
            return
        try:
            self.broker.cancel_order(order["order_id"])
        except Exception as exc:
            logger.debug("S9 cancel order failed: %s", exc)

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
        self.entry_time = None
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
                "ce_buy_line": self.ce_buy_line, "ce_target_line": self.ce_target_line, "ce_sl_line": self.ce_sl_line,
                "pe_buy_line": self.pe_buy_line, "pe_target_line": self.pe_target_line, "pe_sl_line": self.pe_sl_line,
                "ce_ltp": self.ce_ltp, "pe_ltp": self.pe_ltp,
                "spot_price": self.spot_price,
                "signal_type": self.signal_type, "trigger_side": self.trigger_side,
                "entry_reason": self.entry_reason,
                "atm_strike": self.atm_strike, "strike": self.strike,
                "option_symbol": self.option_symbol, "option_token": self.option_token,
                "option_ltp": self.option_ltp, "fill_price": self.fill_price,
                "sl_price": self.sl_price, "target_price": self.target_price,
                "entry_time": self.entry_time,
                "current_ltp": self.current_ltp,
                "entry_order": self.entry_order, "sl_order": self.sl_order,
                "target_order": self.target_order,
                "sl_shadow": self.sl_shadow, "target_shadow": self.target_shadow,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "last_trigger_at": self.last_trigger_at, "last_trigger_side": self.last_trigger_side,
                "last_trigger_price": self.last_trigger_price,
                "last_exit_at": self.last_exit_at, "last_exit_type": self.last_exit_type,
                "last_exit_price": self.last_exit_price,
                "trades_today": self._trades_today, "trade_log": self.trade_log,
                "risk": self.risk.serialize(),
            }, indent=2, default=str))
        except Exception as exc:
            logger.error("S9 state save failed: %s", exc)

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
            self.ce_buy_line    = float(data.get("ce_buy_line") or 0)
            self.ce_target_line = float(data.get("ce_target_line") or 0)
            self.ce_sl_line     = float(data.get("ce_sl_line") or 0)
            self.pe_buy_line    = float(data.get("pe_buy_line") or 0)
            self.pe_target_line = float(data.get("pe_target_line") or 0)
            self.pe_sl_line     = float(data.get("pe_sl_line") or 0)
            self.ce_ltp = float(data.get("ce_ltp") or 0)
            self.pe_ltp = float(data.get("pe_ltp") or 0)
            self.spot_price = float(data.get("spot_price") or 0)
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
            self.entry_time = data.get("entry_time")
            self.current_ltp = float(data.get("current_ltp") or 0)
            self.entry_order = data.get("entry_order")
            self.sl_order = data.get("sl_order")
            self.target_order = data.get("target_order")
            # Default to shadow=True on restore so any stale persisted real
            # order is cancelled & re-promoted on the next qualifying tick.
            self.sl_shadow = bool(data.get("sl_shadow", True))
            self.target_shadow = bool(data.get("target_shadow", True))
            self.sl_proximity = float(data.get("sl_proximity") or self.sl_proximity)
            self.target_proximity = float(data.get("target_proximity") or self.target_proximity)
            self.last_trigger_at = data.get("last_trigger_at")
            self.last_trigger_side = data.get("last_trigger_side")
            self.last_trigger_price = float(data.get("last_trigger_price") or 0)
            self.last_exit_at = data.get("last_exit_at")
            self.last_exit_type = data.get("last_exit_type")
            self.last_exit_price = float(data.get("last_exit_price") or 0)
            self._trades_today = int(data.get("trades_today") or 0)
            self.trade_log = list(data.get("trade_log") or [])
            self.risk.restore(data.get("risk") or {})
            return True
        except Exception as exc:
            logger.warning("S9 state restore failed: %s", exc)
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
            logger.error("S9 trade history append failed: %s", exc)

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
            "lines": self._lines_payload(),
            "strikes": {
                "ce_strike": self.ce_strike, "ce_symbol": self.ce_symbol, "ce_token": self.ce_token,
                "pe_strike": self.pe_strike, "pe_symbol": self.pe_symbol, "pe_token": self.pe_token,
            },
            "ltp": {"ce": self.ce_ltp, "pe": self.pe_ltp,
                    "ce_prev": self._prev_ce, "pe_prev": self._prev_pe},
            "atm_ref": {
                "price": self.spot_price,
                "atm": self.atm_strike or (round(self.spot_price / self.strike_interval) * self.strike_interval if self.spot_price > 0 else 0),
            },
            "trigger": {
                "last_at": self.last_trigger_at,
                "last_side": self.last_trigger_side,
                "last_price": self.last_trigger_price,
            },
            "exit": {
                "last_at": self.last_exit_at,
                "last_type": self.last_exit_type,
                "last_price": self.last_exit_price,
            },
            "config": {
                "lot_size": self.lot_size, "lots": self.lots, "quantity": self.quantity,
                "strike_interval": self.strike_interval,
                "max_trades_per_day": self.max_trades_per_day,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "index_name": self.index_name,
                "ce_buy_line": self.ce_buy_line, "ce_target_line": self.ce_target_line, "ce_sl_line": self.ce_sl_line,
                "pe_buy_line": self.pe_buy_line, "pe_target_line": self.pe_target_line, "pe_sl_line": self.pe_sl_line,
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
                "entry_time": self.entry_time,
                "current_ltp": self.current_ltp,
                "unrealized_pnl": unrealized,
            },
            "orders": {
                "entry": self.entry_order, "sl": self.sl_order,
                "target": self.target_order,
            },
            "trades_today": self._trades_today,
            "trade_log": self.trade_log[-20:],
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "risk": self.risk.status_payload(),
        }
