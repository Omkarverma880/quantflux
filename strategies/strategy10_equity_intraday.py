"""
Strategy 10 — Equity Intraday Breakout (+ manual equity desk).

Concept
-------
For each stock in the *uploaded* stock list compute:
    level      = max(first-hour candle HIGH across last N trading days)
    avg_volume = average(first-hour candle VOLUME across last N trading days)

where "first-hour candle" is the 60-min candle that starts at 9:15 IST
(i.e. the 9:15-10:15 window).

Entry rule
----------
    default      : today's OPEN > level  →  BUY at MARKET (product=MIS)
    volume filter: (optional) once OPEN > level, WAIT until today's live
                   cumulative traded volume >= avg_volume, then BUY.

Exit
----
    • Hidden (shadow) SL / Target in *percent*: SL = entry × (1 − sl_pct),
      Target = entry × (1 + target_pct). Percentage keeps risk uniform across
      high- and low-priced stocks. Monitored on LTP; fired as MARKET when
      touched. Never resting on the exchange.
    • Flat auto-square-off at squareoff_time (default 15:15 IST).
    • Detects manual broker exits (live positions only).

Stock list
----------
The list of symbols is uploaded by the user (CSV) and persisted in
Postgres (global / shared). The route layer loads the latest list and
hands the symbols to this strategy via start()/refresh_stocks().

Manual desk
-----------
The same instance also supports discretionary equity orders placed from
the S10 page (add_manual_trade / modify_manual / exit_manual). Manual
positions get the same hidden SL/Target monitoring and land in the same
S10 trade history.

State machine (per stock)
-------------------------
    WATCHING → ARMED → ORDER_PLACED → POSITION_OPEN → SQUARED_OFF
                                                    → TARGET_HIT
                                                    → SL_HIT
                                                    → MANUAL_EXIT
             → SKIP  (open <= level, after cutoff, or no data)
             → ENTRY_FAILED

Global state:
    IDLE → RUNNING → COMPLETED
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime, timedelta
from enum import Enum
from math import floor
from pathlib import Path
from typing import Optional

from config import settings
from core.broker import (
    Broker, OrderRequest,
    Exchange, OrderSide, OrderType, ProductType,
)
from core.logger import get_logger

logger = get_logger("strategy10.equity_intraday")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
FIRST_HOUR_END = dtime(10, 15)

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy10_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy10_trades.json"


class GlobalState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


STOCK_IDLE = "WATCHING"
STOCK_ARMED = "ARMED"
STOCK_ORDER_PLACED = "ORDER_PLACED"
STOCK_POSITION_OPEN = "POSITION_OPEN"
STOCK_SQUARED_OFF = "SQUARED_OFF"
STOCK_TARGET_HIT = "TARGET_HIT"
STOCK_SL_HIT = "SL_HIT"
STOCK_MANUAL_EXIT = "MANUAL_EXIT"
STOCK_SKIP = "SKIP"
STOCK_ENTRY_FAILED = "ENTRY_FAILED"

# States that still need the check() loop to keep running.
STOCK_ACTIVE = {STOCK_ARMED, STOCK_ORDER_PLACED, STOCK_POSITION_OPEN}
# Finished states — no further action needed.
STOCK_TERMINAL = {
    STOCK_SQUARED_OFF, STOCK_TARGET_HIT, STOCK_SL_HIT,
    STOCK_MANUAL_EXIT, STOCK_SKIP, STOCK_ENTRY_FAILED,
}


class Strategy10EquityIntraday:
    """Multi-stock equity intraday breakout + manual equity desk."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # ── Config ──
        self.capital_per_stock: float = float(config.get("capital_per_stock", 20000))
        self.target_percent: float = float(config.get("target_percent", 2.0))
        self.sl_percent: float = float(config.get("sl_percent", 1.0))
        self.volume_filter: bool = bool(config.get("volume_filter", False))
        self.max_positions: int = int(config.get("max_positions", 5))
        self.lookback_days: int = int(config.get("lookback_days", 5))
        self.entry_cutoff: dtime = self._parse_time(config.get("entry_cutoff", "15:00"))
        self.squareoff_time: dtime = self._parse_time(config.get("squareoff_time", "15:15"))
        self.exchange: str = str(config.get("exchange", "NSE")).upper()
        # Re-entry: OFF by default. When ON, a stock may re-enter up to
        # max_reentries times after its SL/Target is hit (same day, while the
        # breakout condition still holds and a position slot is free).
        self.allow_reentry: bool = bool(config.get("allow_reentry", False))
        self.max_reentries: int = max(0, int(config.get("max_reentries", 1)))

        # ── Global state ──
        self.is_active: bool = False
        self.state: GlobalState = GlobalState.IDLE
        self._trading_date: Optional[date] = None
        self._check_lock = threading.Lock()
        self._squareoff_done: bool = False
        self._levels_ready: bool = False

        # ── Per-stock state dict ──
        self.stock_states: dict[str, dict] = {}

        # ── Instruments cache ──
        self._instruments_cache: Optional[list] = None
        self._instruments_date: Optional[date] = None

        # ── Trade log (session) ──
        self.trade_log: list[dict] = []

        # Throttle position polls (heavier call)
        self._position_check_counter: int = 0

    # ──────────────────────── Helpers ────────────────────────────────

    @staticmethod
    def _parse_time(val) -> dtime:
        try:
            hh, mm = str(val).strip().split(":")
            return dtime(int(hh), int(mm))
        except Exception:
            return dtime(9, 30)

    def _fresh_stock(self, exchange: Optional[str] = None) -> dict:
        return {
            "state": STOCK_IDLE,
            "exchange": (exchange or self.exchange).upper(),
            "level": 0.0,
            "avg_volume": 0.0,
            "level_date": None,
            "level_days_used": 0,
            "today_open": 0.0,
            "live_volume": 0.0,
            "ltp": 0.0,
            "order_id": None,
            "quantity": 0,
            "entry_price": 0.0,
            "entry_time": None,
            "sl_percent": self.sl_percent,
            "target_percent": self.target_percent,
            "sl_price": 0.0,
            "target_price": 0.0,
            "exit_order_id": None,
            "exit_price": 0.0,
            "pnl": None,
            "exit_reason": None,
            "skip_reason": None,
            "manual": False,
            "is_paper": False,
            "reentry_count": 0,
        }

    def _previous_trading_days(self, n: int) -> list[date]:
        """Return the last n trading days (Mon-Fri) before today (most-recent first)."""
        days = []
        d = date.today()
        while len(days) < n:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                days.append(d)
        return days

    def _get_instruments(self) -> list[dict]:
        today = date.today()
        if self._instruments_cache and self._instruments_date == today:
            return self._instruments_cache
        try:
            instruments = self.broker.get_instruments(self.exchange)
            self._instruments_cache = instruments
            self._instruments_date = today
            return instruments
        except Exception as exc:
            logger.warning("S10 instruments fetch failed: %s", exc)
            return self._instruments_cache or []

    def _resolve_token(self, symbol: str) -> Optional[int]:
        instruments = self._get_instruments()
        for inst in instruments:
            if inst.get("tradingsymbol") == symbol and inst.get("instrument_type") == "EQ":
                return int(inst["instrument_token"])
        for inst in instruments:
            if inst.get("tradingsymbol") == symbol:
                return int(inst["instrument_token"])
        return None

    def _key(self, symbol: str) -> str:
        exch = (self.stock_states.get(symbol, {}).get("exchange") or self.exchange).upper()
        return f"{exch}:{symbol}"

    # ──────────────────────── Stock list ─────────────────────────────

    def set_symbols(self, symbols: list) -> None:
        """Initialise stock_states from an uploaded symbol list.

        `symbols` is a list of {"symbol","exchange"} dicts or plain strings.
        Existing entries are preserved (so open positions / computed levels
        survive a refresh); symbols dropped from the list keep their entry
        only if they still hold an active position.
        """
        norm: list[tuple[str, str]] = []
        for s in symbols or []:
            if isinstance(s, dict):
                sym = (s.get("symbol") or "").strip().upper()
                exch = (s.get("exchange") or self.exchange).strip().upper()
            else:
                sym = str(s).strip().upper()
                exch = self.exchange
            if sym:
                norm.append((sym, exch))

        new_states: dict[str, dict] = {}
        for sym, exch in norm:
            if sym in self.stock_states:
                existing = self.stock_states[sym]
                existing["exchange"] = exch
                new_states[sym] = existing
            else:
                new_states[sym] = self._fresh_stock(exch)

        # Preserve dropped symbols that still hold an active position
        for sym, st in self.stock_states.items():
            if sym not in new_states and st.get("state") in STOCK_ACTIVE:
                new_states[sym] = st

        self.stock_states = new_states
        self._levels_ready = False
        self._save_state()
        logger.info("S10 stock list set: %d symbols", len(new_states))

    # ──────────────────────── Level computation ──────────────────────

    def _compute_levels(self):
        """Per stock: level = max(5 first-hour highs); avg_volume = mean(5 first-hour volumes)."""
        today = date.today()
        trading_days = self._previous_trading_days(self.lookback_days + 3)  # holiday buffer
        oldest_day = trading_days[-1]
        from_dt = datetime.combine(oldest_day, MARKET_OPEN)
        to_dt = datetime.combine(trading_days[0], FIRST_HOUR_END)

        computed = 0
        for symbol in list(self.stock_states.keys()):
            stock = self.stock_states[symbol]
            if stock.get("state") in STOCK_ACTIVE or stock.get("manual"):
                continue  # don't disturb live/manual entries
            if stock.get("level_date") == today.isoformat() and stock.get("level", 0) > 0:
                continue
            try:
                token = self._resolve_token(symbol)
                if not token:
                    stock["state"] = STOCK_SKIP
                    stock["skip_reason"] = "instrument not found"
                    continue

                candles = self.broker.get_historical_data(
                    instrument_token=token,
                    from_date=from_dt,
                    to_date=to_dt,
                    interval="60minute",
                ) or []
                if not candles:
                    stock["state"] = STOCK_SKIP
                    stock["skip_reason"] = "no historical data"
                    continue

                highs: list[float] = []
                vols: list[float] = []
                for c in candles:
                    dt = c.get("date")
                    if isinstance(dt, str):
                        try:
                            dt = datetime.fromisoformat(dt)
                        except Exception:
                            dt = None
                    is_first_hour = isinstance(dt, datetime) and dt.hour == 9 and dt.minute == 15
                    if is_first_hour:
                        highs.append(float(c["high"]))
                        vols.append(float(c.get("volume", 0) or 0))

                if not highs:  # fallback if candle alignment differs
                    highs = [float(c["high"]) for c in candles]
                    vols = [float(c.get("volume", 0) or 0) for c in candles]

                highs = highs[-self.lookback_days:]
                vols = vols[-self.lookback_days:]
                if not highs:
                    stock["state"] = STOCK_SKIP
                    stock["skip_reason"] = "no first-hour highs"
                    continue

                stock["level"] = round(max(highs), 2)
                stock["avg_volume"] = round(sum(vols) / len(vols), 0) if vols else 0.0
                stock["level_date"] = today.isoformat()
                stock["level_days_used"] = len(highs)
                if stock.get("state") == STOCK_SKIP:
                    stock["state"] = STOCK_IDLE
                    stock["skip_reason"] = None
                computed += 1
                logger.info(
                    "S10 %s level=%.2f avg_vol=%.0f (%d days)",
                    symbol, stock["level"], stock["avg_volume"], len(highs),
                )
            except Exception as exc:
                logger.error("S10 level computation failed for %s: %s", symbol, exc)
                stock["state"] = STOCK_SKIP
                stock["skip_reason"] = f"level error: {exc}"

        self._levels_ready = True
        self._save_state()
        logger.info("S10 levels computed for %d/%d stocks", computed, len(self.stock_states))

    # ──────────────────────── Market data ────────────────────────────

    def refresh_ltps(self) -> dict[str, float]:
        """Bulk-fetch LTP for every stock (fast — used by the check() loop)."""
        if not self.stock_states:
            return {}
        keys = [self._key(s) for s in self.stock_states]
        out: dict[str, float] = {}
        try:
            data = self.broker.get_ltp(keys) or {}
            for sym in self.stock_states:
                ltp = float(data.get(self._key(sym), 0) or 0)
                if ltp > 0:
                    self.stock_states[sym]["ltp"] = round(ltp, 2)
                out[sym] = self.stock_states[sym].get("ltp", 0.0)
        except Exception as exc:
            logger.debug("S10 bulk LTP failed: %s", exc)
        return out

    def refresh_quotes(self) -> None:
        """Bulk-fetch LTP + live cumulative volume for every stock.

        Used by the columnar view (/stocks) so the user can see today's
        running volume next to the 5-day average and verify the volume
        filter. Heavier than refresh_ltps, so it is only called on the
        ~2s UI poll, not the 1s check() loop.
        """
        if not self.stock_states:
            return
        keys = [self._key(s) for s in self.stock_states]
        try:
            q = self.broker.get_quote(keys) or {}
            for sym in self.stock_states:
                rec = q.get(self._key(sym)) or {}
                ltp = float(rec.get("last_price", 0) or 0)
                vol = float(rec.get("volume", 0) or 0)
                day_open = float((rec.get("ohlc") or {}).get("open", 0) or 0)
                if ltp > 0:
                    self.stock_states[sym]["ltp"] = round(ltp, 2)
                if vol > 0:
                    self.stock_states[sym]["live_volume"] = round(vol, 0)
                # Today's open is fixed for the session — populate it so the
                # columnar view shows Open / Open>Level even before Start.
                if day_open > 0:
                    self.stock_states[sym]["today_open"] = round(day_open, 2)
        except Exception as exc:
            logger.debug("S10 bulk quote failed: %s", exc)

    def _bulk_ohlc(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        try:
            return self.broker.get_ohlc([self._key(s) for s in symbols]) or {}
        except Exception as exc:
            logger.debug("S10 bulk OHLC failed: %s", exc)
            return {}

    def _bulk_volume(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        out: dict[str, float] = {}
        try:
            q = self.broker.get_quote([self._key(s) for s in symbols]) or {}
            for s in symbols:
                rec = q.get(self._key(s)) or {}
                out[s] = float(rec.get("volume", 0) or 0)
        except Exception as exc:
            logger.debug("S10 bulk quote failed: %s", exc)
        return out

    # ──────────────────────── Entry / Exit ───────────────────────────

    def _count_open_positions(self) -> int:
        return sum(
            1 for s in self.stock_states.values()
            if s.get("state") in (STOCK_ORDER_PLACED, STOCK_POSITION_OPEN)
        )

    @property
    def has_open_positions(self) -> bool:
        return any(s.get("state") in STOCK_ACTIVE for s in self.stock_states.values())

    def _qty_for(self, price: float) -> int:
        return max(1, floor(self.capital_per_stock / price)) if price > 0 else 1

    def _place_entry(self, symbol: str, ref_price: float, manual: bool = False,
                     quantity: Optional[int] = None,
                     sl_percent: Optional[float] = None,
                     target_percent: Optional[float] = None):
        stock = self.stock_states[symbol]
        qty = int(quantity) if quantity else self._qty_for(ref_price)
        stock["state"] = STOCK_ORDER_PLACED
        stock["quantity"] = qty
        stock["manual"] = manual
        if stock.get("today_open", 0) <= 0 and ref_price > 0:
            stock["today_open"] = round(ref_price, 2)
        stock["sl_percent"] = float(sl_percent) if sl_percent is not None else self.sl_percent
        stock["target_percent"] = float(target_percent) if target_percent is not None else self.target_percent
        try:
            exch = Exchange(stock.get("exchange", self.exchange))
        except ValueError:
            exch = Exchange.NSE
        try:
            req = OrderRequest(
                tradingsymbol=symbol,
                exchange=exch,
                side=OrderSide.BUY,
                quantity=qty,
                order_type=OrderType.MARKET,
                product=ProductType.MIS,
                tag="S10MAN" if manual else "S10ENTRY",
            )
            resp = self.broker.place_order(req)
            stock["order_id"] = resp.order_id
            stock["entry_time"] = datetime.now().isoformat()
            stock["is_paper"] = bool(resp.is_paper)
            if resp.is_paper and resp.status == "COMPLETE":
                self._finalize_fill(symbol, ref_price)
                logger.info("S10 paper entry filled: %s qty=%d @%.2f%s",
                            symbol, qty, ref_price, " (manual)" if manual else "")
            else:
                logger.info("S10 entry placed: %s order_id=%s%s",
                            symbol, resp.order_id, " (manual)" if manual else "")
            self._save_state()
        except Exception as exc:
            logger.error("S10 entry order failed for %s: %s", symbol, exc)
            stock["state"] = STOCK_ENTRY_FAILED
            stock["skip_reason"] = str(exc)
            self._save_state()

    def _finalize_fill(self, symbol: str, fill_price: float):
        stock = self.stock_states[symbol]
        entry = round(float(fill_price), 2)
        stock["entry_price"] = entry
        stock["sl_price"] = round(max(0.05, entry * (1 - stock["sl_percent"] / 100)), 2)
        stock["target_price"] = round(entry * (1 + stock["target_percent"] / 100), 2)
        stock["state"] = STOCK_POSITION_OPEN

    def _place_exit(self, symbol: str, reason: str):
        stock = self.stock_states[symbol]
        qty = int(stock.get("quantity", 0))
        if qty <= 0:
            stock["state"] = STOCK_SQUARED_OFF
            return
        try:
            exch = Exchange(stock.get("exchange", self.exchange))
        except ValueError:
            exch = Exchange.NSE
        try:
            req = OrderRequest(
                tradingsymbol=symbol,
                exchange=exch,
                side=OrderSide.SELL,
                quantity=qty,
                order_type=OrderType.MARKET,
                product=ProductType.MIS,
                tag="S10EXIT",
            )
            resp = self.broker.place_order(req)
            stock["exit_order_id"] = resp.order_id

            ltp = stock.get("ltp", 0) or 0
            if reason == "SL_HIT":
                exit_price = stock.get("sl_price") or ltp or stock.get("entry_price", 0)
                new_state = STOCK_SL_HIT
            elif reason == "TARGET_HIT":
                exit_price = stock.get("target_price") or ltp or stock.get("entry_price", 0)
                new_state = STOCK_TARGET_HIT
            elif reason == "MANUAL_EXIT":
                exit_price = ltp or stock.get("entry_price", 0)
                new_state = STOCK_MANUAL_EXIT
            else:  # AUTO_SQUAREOFF
                exit_price = ltp or stock.get("entry_price", 0)
                new_state = STOCK_SQUARED_OFF

            entry = float(stock.get("entry_price") or 0)
            stock["exit_price"] = round(float(exit_price), 2)
            stock["pnl"] = round((float(exit_price) - entry) * qty, 2)
            stock["exit_reason"] = reason
            stock["state"] = new_state

            self._append_trade(symbol, stock)

            # ── Re-entry: re-arm the stock after an SL/Target exit ──
            rc = int(stock.get("reentry_count", 0) or 0)
            if (
                self.allow_reentry
                and reason in ("SL_HIT", "TARGET_HIT")
                and not stock.get("manual")
                and rc < self.max_reentries
            ):
                fresh = self._fresh_stock(stock.get("exchange"))
                fresh.update({
                    "level": stock.get("level"),
                    "avg_volume": stock.get("avg_volume"),
                    "level_date": stock.get("level_date"),
                    "level_days_used": stock.get("level_days_used"),
                    "today_open": stock.get("today_open"),
                    "ltp": stock.get("ltp"),
                    "live_volume": stock.get("live_volume"),
                    "reentry_count": rc + 1,
                })
                self.stock_states[symbol] = fresh  # back to WATCHING
                logger.info("S10 %s re-armed for re-entry (%d/%d)",
                            symbol, rc + 1, self.max_reentries)

            self._save_state()
            logger.info("S10 exit %s: %s qty=%d @%.2f pnl=%.2f",
                        symbol, reason, qty, stock["exit_price"], stock["pnl"])
        except Exception as exc:
            logger.error("S10 exit order failed for %s: %s", symbol, exc)

    # ──────────────────────── Manual desk ────────────────────────────

    def add_manual_trade(self, symbol: str, quantity: Optional[int] = None,
                         capital: Optional[float] = None,
                         sl_percent: Optional[float] = None,
                         target_percent: Optional[float] = None,
                         exchange: Optional[str] = None) -> dict:
        symbol = symbol.strip().upper()
        if symbol not in self.stock_states:
            self.stock_states[symbol] = self._fresh_stock(exchange)
        stock = self.stock_states[symbol]
        if stock.get("state") in STOCK_ACTIVE:
            return {"status": "error", "message": f"{symbol} already has an active position"}

        # Resolve a reference price for qty calc
        ref = stock.get("ltp", 0) or 0
        if ref <= 0:
            try:
                ref = float((self.broker.get_ltp([self._key(symbol)]) or {}).get(self._key(symbol), 0) or 0)
            except Exception:
                ref = 0
        qty = int(quantity) if quantity else None
        if not qty:
            cap = float(capital) if capital else self.capital_per_stock
            qty = max(1, floor(cap / ref)) if ref > 0 else 1

        self.state = GlobalState.RUNNING
        self._place_entry(
            symbol, ref, manual=True, quantity=qty,
            sl_percent=sl_percent, target_percent=target_percent,
        )
        return {"status": "ok", **self._stock_view(symbol)}

    def modify_manual(self, symbol: str, sl_price: Optional[float] = None,
                      target_price: Optional[float] = None,
                      sl_percent: Optional[float] = None,
                      target_percent: Optional[float] = None) -> dict:
        symbol = symbol.strip().upper()
        stock = self.stock_states.get(symbol)
        if not stock or stock.get("state") != STOCK_POSITION_OPEN:
            return {"status": "error", "message": f"{symbol} has no open position to modify"}
        entry = float(stock.get("entry_price") or 0)
        if sl_price is not None:
            stock["sl_price"] = round(float(sl_price), 2)
            stock["sl_percent"] = round((entry - stock["sl_price"]) / entry * 100, 3) if entry else stock.get("sl_percent")
        elif sl_percent is not None:
            stock["sl_percent"] = float(sl_percent)
            stock["sl_price"] = round(max(0.05, entry * (1 - sl_percent / 100)), 2)
        if target_price is not None:
            stock["target_price"] = round(float(target_price), 2)
            stock["target_percent"] = round((stock["target_price"] - entry) / entry * 100, 3) if entry else stock.get("target_percent")
        elif target_percent is not None:
            stock["target_percent"] = float(target_percent)
            stock["target_price"] = round(entry * (1 + target_percent / 100), 2)
        self._save_state()
        return {"status": "ok", **self._stock_view(symbol)}

    def exit_manual(self, symbol: str) -> dict:
        symbol = symbol.strip().upper()
        stock = self.stock_states.get(symbol)
        if not stock or stock.get("state") != STOCK_POSITION_OPEN:
            return {"status": "error", "message": f"{symbol} has no open position to exit"}
        self._place_exit(symbol, "MANUAL_EXIT")
        return {"status": "ok", **self._stock_view(symbol)}

    # ──────────────────────── Main check loop ─────────────────────────

    def check(self) -> dict:
        with self._check_lock:
            return self._check_inner()

    def _check_inner(self) -> dict:
        now = datetime.now()
        self._check_day_reset()

        if self.state == GlobalState.IDLE and not self.has_open_positions:
            return self.get_status()

        # Refresh LTP for everything (drives monitoring + columnar view)
        self.refresh_ltps()

        in_entry_window = (
            self.is_active
            and MARKET_OPEN <= now.time() <= self.entry_cutoff
            and not self._squareoff_done
        )

        # ── Entry phase ──
        if in_entry_window:
            if not self._levels_ready:
                self._compute_levels()

            # Revive "soft" skips (cutoff / max-positions) back to WATCHING so
            # that extending the entry cutoff or freeing a slot lets them enter
            # again. Hard skips ("open below level") stay retired.
            for stock in self.stock_states.values():
                if (
                    stock.get("state") == STOCK_SKIP
                    and not stock.get("manual")
                    and stock.get("skip_reason") in ("no entry by cutoff", "max positions reached")
                ):
                    stock["state"] = STOCK_IDLE
                    stock["skip_reason"] = None

            # Resolve today's open for stocks still waiting
            need_open = [
                s for s, st in self.stock_states.items()
                if st.get("state") in (STOCK_IDLE, STOCK_ARMED)
                and not st.get("manual")
                and st.get("today_open", 0) <= 0
            ]
            ohlc = self._bulk_ohlc(need_open)
            for sym in need_open:
                rec = ohlc.get(self._key(sym)) or {}
                op = float((rec.get("ohlc") or {}).get("open", 0) or 0)
                if op > 0:
                    self.stock_states[sym]["today_open"] = round(op, 2)

            # Live volume for ARMED stocks (volume filter)
            armed = [s for s, st in self.stock_states.items() if st.get("state") == STOCK_ARMED]
            vol_map = self._bulk_volume(armed) if armed else {}

            open_positions = self._count_open_positions()
            for symbol, stock in self.stock_states.items():
                if stock.get("manual"):
                    continue
                st = stock.get("state")
                if st not in (STOCK_IDLE, STOCK_ARMED):
                    continue
                if open_positions >= self.max_positions:
                    stock["state"] = STOCK_SKIP
                    stock["skip_reason"] = "max positions reached"
                    continue
                level = stock.get("level", 0)
                today_open = stock.get("today_open", 0)
                if not level or today_open <= 0:
                    continue  # data not ready yet

                if today_open <= level:
                    stock["state"] = STOCK_SKIP
                    stock["skip_reason"] = "open below level"
                    continue

                # open > level confirmed
                if not self.volume_filter:
                    self._place_entry(symbol, stock.get("ltp") or today_open)
                    open_positions = self._count_open_positions()
                    continue

                # volume filter ON → arm and wait for live volume
                if st == STOCK_IDLE:
                    stock["state"] = STOCK_ARMED
                    st = STOCK_ARMED
                live_vol = vol_map.get(symbol, stock.get("live_volume", 0))
                stock["live_volume"] = round(live_vol, 0)
                if live_vol >= stock.get("avg_volume", 0) > 0:
                    self._place_entry(symbol, stock.get("ltp") or today_open)
                    open_positions = self._count_open_positions()

        # ── Past entry cutoff: retire anything still watching/armed ──
        if self.is_active and now.time() > self.entry_cutoff:
            for stock in self.stock_states.values():
                if stock.get("state") in (STOCK_IDLE, STOCK_ARMED) and not stock.get("manual"):
                    stock["state"] = STOCK_SKIP
                    stock["skip_reason"] = stock.get("skip_reason") or "no entry by cutoff"

        # ── Fill tracking (ORDER_PLACED → POSITION_OPEN) ──
        for symbol, stock in list(self.stock_states.items()):
            if stock.get("state") != STOCK_ORDER_PLACED:
                continue
            order_id = stock.get("order_id")
            if not order_id or stock.get("is_paper"):
                continue
            try:
                for o in (self.broker.get_orders() or []):
                    if str(o.get("order_id")) == str(order_id):
                        status = o.get("status", "")
                        if status == "COMPLETE":
                            fill = float(o.get("average_price") or o.get("price")
                                         or stock.get("today_open", 0))
                            self._finalize_fill(symbol, fill)
                            logger.info("S10 entry filled: %s @%.2f", symbol, fill)
                            self._save_state()
                        elif status in ("CANCELLED", "REJECTED"):
                            stock["state"] = STOCK_ENTRY_FAILED
                            stock["skip_reason"] = status
                            self._save_state()
                        break
                else:
                    et = stock.get("entry_time")
                    if et and (datetime.now() - datetime.fromisoformat(et)).total_seconds() > 90:
                        stock["state"] = STOCK_ENTRY_FAILED
                        stock["skip_reason"] = "stale (>90s)"
                        self._save_state()
            except Exception as exc:
                logger.debug("S10 fill check failed for %s: %s", symbol, exc)

        # ── Hidden SL / Target monitoring ──
        for symbol, stock in self.stock_states.items():
            if stock.get("state") != STOCK_POSITION_OPEN:
                continue
            ltp = stock.get("ltp", 0) or 0
            if ltp <= 0:
                continue
            if stock.get("sl_price") and ltp <= stock["sl_price"]:
                self._place_exit(symbol, "SL_HIT")
            elif stock.get("target_price") and ltp >= stock["target_price"]:
                self._place_exit(symbol, "TARGET_HIT")

        # ── Manual broker-exit detection (live only) ──
        self._position_check_counter += 1
        if self._position_check_counter % 5 == 0:
            self._detect_broker_exits()

        # ── Auto square-off ──
        if now.time() >= self.squareoff_time and not self._squareoff_done:
            self._auto_squareoff()

        # ── Global completion ──
        if self.stock_states and all(
            s.get("state") in STOCK_TERMINAL for s in self.stock_states.values()
        ):
            self.state = GlobalState.COMPLETED
            self._save_state()

        return self.get_status()

    def _detect_broker_exits(self):
        """Detect positions closed outside the strategy (live trades only)."""
        live_open = [
            s for s, st in self.stock_states.items()
            if st.get("state") == STOCK_POSITION_OPEN and not st.get("is_paper")
        ]
        if not live_open:
            return
        try:
            positions = self.broker.get_positions() or []
            held = {
                (p.tradingsymbol or "").upper()
                for p in positions
                if (p.product or "").upper() == "MIS" and int(getattr(p, "quantity", 0) or 0) != 0
            }
            for symbol in live_open:
                if symbol not in held:
                    stock = self.stock_states[symbol]
                    ltp = stock.get("ltp", 0) or 0
                    entry = float(stock.get("entry_price") or 0)
                    qty = int(stock.get("quantity") or 0)
                    stock["exit_price"] = round(ltp, 2) if ltp else 0.0
                    stock["pnl"] = round((ltp - entry) * qty, 2) if ltp and entry else 0.0
                    stock["exit_reason"] = "MANUAL_EXIT"
                    stock["state"] = STOCK_MANUAL_EXIT
                    self._append_trade(symbol, stock)
                    self._save_state()
                    logger.info("S10 broker exit detected: %s", symbol)
        except Exception as exc:
            logger.debug("S10 broker-exit detection failed: %s", exc)

    def _auto_squareoff(self):
        self._squareoff_done = True
        open_stocks = [
            s for s, st in self.stock_states.items()
            if st.get("state") == STOCK_POSITION_OPEN
        ]
        if open_stocks:
            logger.warning("S10 auto-squareoff firing for %d position(s)", len(open_stocks))
            for symbol in open_stocks:
                self._place_exit(symbol, "AUTO_SQUAREOFF")

    # ──────────────────────── Backtest ───────────────────────────────

    @staticmethod
    def _candle_dt(c: dict):
        dt = c.get("date")
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except Exception:
                return None
        return dt if isinstance(dt, datetime) else None

    def _first_hour_levels(self, token: int, ref_day: date):
        """level = max & avg_vol = mean of first-hour candles in the lookback
        window ending the day *before* ref_day."""
        days: list[date] = []
        d = ref_day
        while len(days) < self.lookback_days + 3:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                days.append(d)
        from_dt = datetime.combine(days[-1], MARKET_OPEN)
        to_dt = datetime.combine(days[0], FIRST_HOUR_END)
        candles = self.broker.get_historical_data(token, from_dt, to_dt, "60minute") or []
        highs, vols = [], []
        for c in candles:
            dt = self._candle_dt(c)
            if dt and dt.hour == 9 and dt.minute == 15:
                highs.append(float(c["high"]))
                vols.append(float(c.get("volume", 0) or 0))
        if not highs:
            highs = [float(c["high"]) for c in candles]
            vols = [float(c.get("volume", 0) or 0) for c in candles]
        highs = highs[-self.lookback_days:]
        vols = vols[-self.lookback_days:]
        if not highs:
            return None, None
        return max(highs), (sum(vols) / len(vols) if vols else 0.0)

    def _group_by_date(self, candles: list[dict]) -> dict:
        out: dict = {}
        for c in candles:
            dt = self._candle_dt(c)
            if dt:
                out.setdefault(dt.date(), []).append(c)
        return out

    def _simulate_core(self, symbol: str, level: float, avg_vol: float, minutes: list[dict]) -> dict:
        """Replay one day's minute candles given a precomputed level / avg_vol."""
        day_open = float(minutes[0]["open"])
        base = {"symbol": symbol, "level": round(level, 2), "day_open": round(day_open, 2)}
        if day_open <= level:
            return {**base, "result": "NO_TRADE", "reason": "open ≤ level", "pnl": 0}

        # Resolve entry candle (honouring the volume filter + entry cutoff)
        if self.volume_filter:
            entry_idx, cum = None, 0.0
            for i, c in enumerate(minutes):
                dt = self._candle_dt(c)
                if dt and dt.time() > self.entry_cutoff:
                    break
                cum += float(c.get("volume", 0) or 0)
                if avg_vol and cum >= avg_vol:
                    entry_idx = i
                    break
            if entry_idx is None:
                return {**base, "result": "NO_TRADE", "reason": "volume not met by cutoff", "pnl": 0}
            entry_price = float(minutes[entry_idx]["close"])
        else:
            entry_idx = 0
            entry_price = day_open

        entry_dt = self._candle_dt(minutes[entry_idx])
        qty = max(1, floor(self.capital_per_stock / entry_price)) if entry_price > 0 else 1
        sl = entry_price * (1 - self.sl_percent / 100)
        tgt = entry_price * (1 + self.target_percent / 100)

        exit_price, exit_reason, exit_dt = None, None, None
        for c in minutes[entry_idx:]:
            dt = self._candle_dt(c)
            if dt and dt.time() >= self.squareoff_time:
                exit_price, exit_reason, exit_dt = float(c["open"]), "SQUAREOFF", dt
                break
            lo, hi = float(c["low"]), float(c["high"])
            if lo <= sl:  # conservative: SL checked before target
                exit_price, exit_reason, exit_dt = sl, "SL_HIT", dt
                break
            if hi >= tgt:
                exit_price, exit_reason, exit_dt = tgt, "TARGET_HIT", dt
                break
        if exit_price is None:
            exit_price, exit_reason, exit_dt = float(minutes[-1]["close"]), "SQUAREOFF", self._candle_dt(minutes[-1])

        return {
            **base,
            "entry": round(entry_price, 2),
            "exit": round(exit_price, 2),
            "qty": qty,
            "result": exit_reason,
            "pnl": round((exit_price - entry_price) * qty, 2),
            "entry_time": entry_dt.strftime("%H:%M") if entry_dt else None,
            "exit_time": exit_dt.strftime("%H:%M") if exit_dt else None,
        }

    def _simulate_day(self, symbol: str, token: int, day: date) -> Optional[dict]:
        level, avg_vol = self._first_hour_levels(token, day)
        if level is None:
            return None
        from_dt = datetime.combine(day, MARKET_OPEN)
        to_dt = datetime.combine(day, MARKET_CLOSE)
        minutes = self.broker.get_historical_data(token, from_dt, to_dt, "minute") or []
        if not minutes:
            return None
        return self._simulate_core(symbol, level, avg_vol, minutes)

    def backtest(self, target_date: Optional[date] = None, symbols: Optional[list] = None) -> dict:
        syms = symbols or list(self.stock_states.keys())
        if not syms:
            return {"status": "error", "message": "No stocks loaded — upload a list first"}
        if target_date is None:
            target_date = self._previous_trading_days(1)[0]

        trades = []
        for sym in syms:
            token = self._resolve_token(sym)
            if not token:
                trades.append({"symbol": sym, "result": "NO_DATA", "reason": "instrument not found", "pnl": 0})
                continue
            try:
                r = self._simulate_day(sym, token, target_date)
                if r:
                    trades.append(r)
            except Exception as exc:
                logger.debug("S10 backtest %s failed: %s", sym, exc)

        return {"status": "ok", "date": target_date.isoformat(), "trades": trades,
                "summary": self._bt_summary(trades)}

    def backtest_multi(self, days: int = 30, symbols: Optional[list] = None) -> dict:
        """Aggregated multi-day backtest.

        Optimised to TWO historical calls per stock (one 60-min span for the
        rolling levels, one minute span for the days), so it scales to 30 days
        without a fetch-per-day explosion.
        """
        days = max(1, min(int(days), 30))
        syms = symbols or list(self.stock_states.keys())
        if not syms:
            return {"status": "error", "message": "No stocks loaded — upload a list first"}

        target_days = sorted(self._previous_trading_days(days))  # ascending
        oldest, newest = target_days[0], target_days[-1]
        # 60-min span must reach back lookback_days *trading* days before oldest.
        fh_start = oldest - timedelta(days=self.lookback_days * 3 + 12)

        daily_map = {d.isoformat(): {"total_pnl": 0.0, "trades_taken": 0} for d in target_days}
        all_trades: list[dict] = []

        for sym in syms:
            token = self._resolve_token(sym)
            if not token:
                continue
            try:
                c60 = self.broker.get_historical_data(
                    token, datetime.combine(fh_start, MARKET_OPEN),
                    datetime.combine(newest, FIRST_HOUR_END), "60minute") or []
                fh: dict = {}
                for c in c60:
                    dt = self._candle_dt(c)
                    if dt and dt.hour == 9 and dt.minute == 15:
                        fh[dt.date()] = (float(c["high"]), float(c.get("volume", 0) or 0))

                cmin = self.broker.get_historical_data(
                    token, datetime.combine(oldest, MARKET_OPEN),
                    datetime.combine(newest, MARKET_CLOSE), "minute") or []
                by_date = self._group_by_date(cmin)

                for d in target_days:
                    minutes = by_date.get(d)
                    if not minutes:
                        continue
                    prior = sorted([k for k in fh if k < d])[-self.lookback_days:]
                    if not prior:
                        continue
                    highs = [fh[k][0] for k in prior]
                    vols = [fh[k][1] for k in prior]
                    level = max(highs)
                    avg_vol = sum(vols) / len(vols) if vols else 0.0
                    r = self._simulate_core(sym, level, avg_vol, minutes)
                    if r.get("result") in ("NO_TRADE", "NO_DATA"):
                        continue
                    all_trades.append({**r, "date": d.isoformat()})
                    daily_map[d.isoformat()]["total_pnl"] += r["pnl"]
                    daily_map[d.isoformat()]["trades_taken"] += 1
            except Exception as exc:
                logger.debug("S10 multi backtest %s failed: %s", sym, exc)

        daily = [
            {"date": k, "total_pnl": round(v["total_pnl"], 2), "trades_taken": v["trades_taken"]}
            for k, v in sorted(daily_map.items())
        ]
        return {"status": "ok", "days": days, "daily": daily,
                "trades": all_trades[-300:], "summary": self._bt_summary(all_trades)}

    @staticmethod
    def _bt_summary(trades: list[dict]) -> dict:
        taken = [t for t in trades if t.get("result") not in ("NO_TRADE", "NO_DATA")]
        wins = [t for t in taken if (t.get("pnl") or 0) > 0]
        total = round(sum(t.get("pnl") or 0 for t in taken), 2)
        return {
            "candidates": len([t for t in trades if t.get("result") != "NO_DATA"]),
            "trades_taken": len(taken),
            "wins": len(wins),
            "losses": len(taken) - len(wins),
            "win_rate": round(100 * len(wins) / len(taken), 1) if taken else 0,
            "total_pnl": total,
        }

    # ──────────────────────── Lifecycle ──────────────────────────────

    def start(self, config: dict, symbols: Optional[list] = None):
        self.apply_config(config, save=False)
        self.is_active = True
        self.state = GlobalState.RUNNING
        self._squareoff_done = False
        self._check_day_reset()

        if symbols is not None:
            self.set_symbols(symbols)
        self._save_state()
        threading.Thread(target=self._compute_levels, daemon=True).start()
        logger.info("S10 started with %d stocks (levels computing in background)", len(self.stock_states))

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("S10 stopped (open positions stay monitored)")

    def apply_config(self, config: dict, save: bool = True):
        self.capital_per_stock = float(config.get("capital_per_stock", self.capital_per_stock))
        self.target_percent = float(config.get("target_percent", self.target_percent))
        self.sl_percent = float(config.get("sl_percent", self.sl_percent))
        self.volume_filter = bool(config.get("volume_filter", self.volume_filter))
        self.max_positions = int(config.get("max_positions", self.max_positions))
        self.lookback_days = int(config.get("lookback_days", self.lookback_days))
        self.entry_cutoff = self._parse_time(config.get("entry_cutoff", self.entry_cutoff.strftime("%H:%M")))
        self.squareoff_time = self._parse_time(config.get("squareoff_time", self.squareoff_time.strftime("%H:%M")))
        self.exchange = str(config.get("exchange", self.exchange)).upper()
        self.allow_reentry = bool(config.get("allow_reentry", self.allow_reentry))
        self.max_reentries = max(0, int(config.get("max_reentries", self.max_reentries)))
        if save:
            self._save_state()

    def refresh_stocks(self, symbols: Optional[list] = None):
        if self.state == GlobalState.IDLE:
            self.state = GlobalState.RUNNING
        if symbols is not None:
            self.set_symbols(symbols)
        threading.Thread(target=self._compute_levels, daemon=True).start()
        logger.info("S10 refresh_stocks: %d stocks, levels computing", len(self.stock_states))
        return {"status": "computing", "stock_count": len(self.stock_states)}

    # ──────────────────────── Day reset ──────────────────────────────

    def _check_day_reset(self, force: bool = False):
        today = date.today()
        if not force and self._trading_date == today:
            return
        self._trading_date = today
        self._squareoff_done = False
        self._levels_ready = False

        for sym, stock in list(self.stock_states.items()):
            keep_level = stock.get("level_date") == today.isoformat() and stock.get("level", 0) > 0
            fresh = self._fresh_stock(stock.get("exchange"))
            if keep_level:
                fresh["level"] = stock.get("level", 0.0)
                fresh["avg_volume"] = stock.get("avg_volume", 0.0)
                fresh["level_date"] = stock.get("level_date")
                fresh["level_days_used"] = stock.get("level_days_used", 0)
            self.stock_states[sym] = fresh

        if self.state == GlobalState.COMPLETED:
            self.state = GlobalState.RUNNING if self.is_active else GlobalState.IDLE
        self.trade_log = []
        self._save_state()

    # ──────────────────────── Persistence ────────────────────────────

    def _config_dict(self) -> dict:
        return {
            "capital_per_stock": self.capital_per_stock,
            "target_percent": self.target_percent,
            "sl_percent": self.sl_percent,
            "volume_filter": self.volume_filter,
            "max_positions": self.max_positions,
            "lookback_days": self.lookback_days,
            "entry_cutoff": self.entry_cutoff.strftime("%H:%M"),
            "squareoff_time": self.squareoff_time.strftime("%H:%M"),
            "exchange": self.exchange,
            "allow_reentry": self.allow_reentry,
            "max_reentries": self.max_reentries,
        }

    def _save_state(self):
        data = {
            "is_active": self.is_active,
            "state": self.state.value,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "squareoff_done": self._squareoff_done,
            "levels_ready": self._levels_ready,
            "stock_states": self.stock_states,
            "trade_log": self.trade_log[-100:],
            "config": self._config_dict(),
            "saved_at": datetime.now().isoformat(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.error("S10 save_state failed: %s", exc)

    def restore_state(self) -> bool:
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
        except Exception as exc:
            logger.warning("S10 restore_state read failed: %s", exc)
            return False
        if data.get("trading_date", "") != date.today().isoformat():
            logger.info("S10 state file from %s — skipping restore", data.get("trading_date"))
            return False
        try:
            self.is_active = bool(data.get("is_active", False))
            self.state = GlobalState(data.get("state", "IDLE"))
            self._trading_date = date.today()
            self._squareoff_done = bool(data.get("squareoff_done", False))
            self._levels_ready = bool(data.get("levels_ready", False))
            self.stock_states = dict(data.get("stock_states", {}))
            self.trade_log = list(data.get("trade_log", []))
            cfg = data.get("config", {}) or {}
            if cfg:
                self.apply_config(cfg, save=False)
            logger.info("S10 state restored: %s, %d stocks", self.state.value, len(self.stock_states))
            return True
        except Exception as exc:
            logger.warning("S10 restore_state apply failed: %s", exc)
            return False

    def _append_trade(self, symbol: str, stock: dict):
        trade = {
            "symbol": symbol,
            "date": (self._trading_date or date.today()).isoformat(),
            "type": "MANUAL" if stock.get("manual") else "AUTO",
            "level": stock.get("level"),
            "today_open": stock.get("today_open"),
            "entry_price": stock.get("entry_price"),
            "exit_price": stock.get("exit_price"),
            "quantity": stock.get("quantity"),
            "sl_price": stock.get("sl_price"),
            "target_price": stock.get("target_price"),
            "pnl": stock.get("pnl"),
            "exit_reason": stock.get("exit_reason"),
            "entry_time": stock.get("entry_time"),
            "exit_time": datetime.now().strftime("%H:%M:%S"),
            "timestamp": datetime.now().isoformat(),
        }
        self.trade_log.append(trade)
        try:
            trades = []
            if TRADE_HISTORY_FILE.exists():
                trades = json.loads(TRADE_HISTORY_FILE.read_text())
            trades.append(trade)
            TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRADE_HISTORY_FILE.write_text(json.dumps(trades, indent=2, default=str))
        except Exception as exc:
            logger.error("S10 trade history append failed: %s", exc)

    # ──────────────────────── Status ─────────────────────────────────

    def _stock_view(self, symbol: str) -> dict:
        s = self.stock_states.get(symbol, {})
        level = s.get("level") or 0
        today_open = s.get("today_open") or 0
        ltp = s.get("ltp") or 0
        entry = s.get("entry_price") or 0
        qty = s.get("quantity") or 0
        # Live unrealised P&L for open positions
        live_pnl = s.get("pnl")
        if s.get("state") == STOCK_POSITION_OPEN and entry and ltp:
            live_pnl = round((ltp - entry) * qty, 2)
        return {
            "symbol": symbol,
            "exchange": s.get("exchange", self.exchange),
            "level": level,
            "avg_volume": s.get("avg_volume", 0),
            "live_volume": s.get("live_volume", 0),
            "today_open": today_open,
            "open_above_level": (today_open > level) if (today_open and level) else None,
            "ltp": ltp,
            "state": s.get("state", STOCK_IDLE),
            "quantity": qty,
            "entry_price": entry or None,
            "sl_price": s.get("sl_price") or None,
            "target_price": s.get("target_price") or None,
            "exit_price": s.get("exit_price") or None,
            "pnl": live_pnl,
            "exit_reason": s.get("exit_reason"),
            "skip_reason": s.get("skip_reason"),
            "manual": bool(s.get("manual")),
            "entry_time": s.get("entry_time"),
        }

    def get_status(self) -> dict:
        try:
            self._check_day_reset()
        except Exception:
            pass
        stocks = [self._stock_view(sym) for sym in self.stock_states]
        total_pnl = sum(float(v.get("pnl") or 0) for v in stocks if v.get("pnl") is not None)
        return {
            "state": self.state.value,
            "is_active": self.is_active,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "levels_ready": self._levels_ready,
            "positions_open": self._count_open_positions(),
            "total_pnl": round(total_pnl, 2),
            "squareoff_done": self._squareoff_done,
            "stocks": stocks,
            "trade_log": self.trade_log[-50:],
            "config": self._config_dict(),
        }
