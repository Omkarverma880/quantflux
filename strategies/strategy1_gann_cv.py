"""
Strategy 1 — Gann + Cumulative Volume Entry Strategy.

Entry Rules:
  - CV > +threshold  → BUY ATM CALL at floor Gann level of its LTP
  - CV < −threshold  → BUY ATM PUT  at floor Gann level of its LTP

Exit Rules:
  - SL  = entry − sl_points
  - TGT = entry + target_points

Constraints:
  - 1 trade per day maximum
  - After entry order is FILLED, then SL & Target orders are placed
  - Auto-resets on new trading day
"""
import bisect
import json
from datetime import date, datetime, time as dtime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from config import settings
from core.broker import (
    Broker, OrderRequest, OrderResponse,
    Exchange, OrderSide, OrderType, ProductType,
)
from core.logger import get_logger

logger = get_logger("strategy1.gann_cv")

GANN_CSV = Path(__file__).resolve().parent.parent / "gann_levels.csv"
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)
STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy1_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy1_trades.json"
ORDER_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "order_history.json"


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy1GannCV:
    """Gann floor entry driven by cumulative volume signal."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # Configurable params
        self.sl_points = float(config.get("sl_points", 45))
        self.target_points = float(config.get("target_points", 55))
        self.lot_size = int(config.get("lot_size", 65))
        self.cv_threshold = int(config.get("cv_threshold", 150_000))
        self.strike_interval = int(config.get("strike_interval", 50))

        # Shadow order proximity: place real order when LTP is within
        # this many points of SL or target
        self.sl_proximity = float(config.get("sl_proximity", 5))
        self.target_proximity = float(config.get("target_proximity", 5))

        # Gann target toggle: if True, target = ceiling Gann; else entry + target_points
        self.gann_target = bool(config.get("gann_target", False))

        # Re-entry: if True, automatically re-enter same trade after target hit
        self.re_entry = bool(config.get("re_entry", False))

        # Gann levels (sorted ascending)
        self.gann_levels = self._load_gann_levels()

        # State
        self.is_active: bool = False
        self.state: State = State.IDLE
        self._trading_date: Optional[date] = None

        # Signal / trade details
        self.signal_type: Optional[str] = None   # "CE" or "PE"
        self.atm_strike: int = 0
        self.option_symbol: str = ""
        self.option_token: int = 0
        self.option_ltp: float = 0.0
        self.gann_entry_price: float = 0.0
        self.fill_price: float = 0.0
        self.sl_price: float = 0.0
        self.target_price: float = 0.0
        self.current_ltp: float = 0.0

        # Orders
        self.entry_order: Optional[dict] = None
        self.sl_order: Optional[dict] = None
        self.target_order: Optional[dict] = None

        # Shadow order state: SL/Target kept in memory until LTP is close
        self.sl_shadow: bool = True      # True = SL is only in memory
        self.target_shadow: bool = True   # True = target is only in memory

        # Instruments cache (per day)
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None

        # Trade log
        self.trade_log: list[dict] = []

        # Entry diagnostics (why we didn't enter)
        self._entry_checklist: dict = {}

    # ── Gann helpers ───────────────────────────────

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

    def _floor_gann(self, price: float) -> int:
        """Largest Gann level ≤ price."""
        idx = bisect.bisect_right(self.gann_levels, price) - 1
        return self.gann_levels[max(0, idx)]

    def _ceil_gann(self, price: float) -> int:
        """Smallest Gann level > price (next Gann above)."""
        idx = bisect.bisect_right(self.gann_levels, price)
        if idx < len(self.gann_levels):
            return self.gann_levels[idx]
        return self.gann_levels[-1]

    def _prev_gann(self, gann_level: float) -> int:
        """Gann level immediately below the given Gann level, or 0 if none."""
        idx = bisect.bisect_left(self.gann_levels, gann_level) - 1
        if idx >= 0:
            return self.gann_levels[idx]
        return 0

    # ── Instrument helpers ─────────────────────────

    def _calc_atm(self, spot: float) -> int:
        return round(spot / self.strike_interval) * self.strike_interval

    def _get_instruments(self) -> list[dict]:
        today = date.today()
        if self._instruments_cache and self._instruments_date == today:
            return self._instruments_cache
        self._instruments_cache = self.broker.get_instruments("NFO")
        self._instruments_date = today
        return self._instruments_cache

    def _find_option(self, strike: int, opt_type: str) -> Optional[dict]:
        """Find nearest-expiry NIFTY option at the given strike."""
        instruments = self._get_instruments()
        today = date.today()

        candidates = []
        for inst in instruments:
            if (
                inst.get("name") == "NIFTY"
                and inst.get("instrument_type") == opt_type
                and inst.get("strike") == float(strike)
            ):
                expiry = inst.get("expiry")
                if isinstance(expiry, str):
                    expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
                if expiry and expiry >= today:
                    candidates.append((expiry, inst))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    # ── Controls ───────────────────────────────────

    def start(self, config: dict):
        self.sl_points = float(config.get("sl_points", self.sl_points))
        self.target_points = float(config.get("target_points", self.target_points))
        self.lot_size = int(config.get("lot_size", self.lot_size))
        self.cv_threshold = int(config.get("cv_threshold", self.cv_threshold))
        self.strike_interval = int(config.get("strike_interval", self.strike_interval))
        self.sl_proximity = float(config.get("sl_proximity", self.sl_proximity))
        self.target_proximity = float(config.get("target_proximity", self.target_proximity))
        self.gann_target = bool(config.get("gann_target", self.gann_target))
        self.re_entry = bool(config.get("re_entry", self.re_entry))
        self.is_active = True
        self._check_day_reset()
        self._save_state()
        logger.info(
            f"Strategy 1 started: SL={self.sl_points} TGT={self.target_points} "
            f"Lot={self.lot_size} CV_thresh={self.cv_threshold}"
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 1 stopped")

    def apply_config(self, config: dict) -> None:
        """
        Update tunables on a live strategy instance and, if a position is
        already open, recompute SL / target prices so toggles like
        'Gann Target' take effect immediately after Save.
        """
        self.sl_points = float(config.get("sl_points", self.sl_points))
        self.target_points = float(config.get("target_points", self.target_points))
        self.lot_size = int(config.get("lot_size", self.lot_size))
        self.cv_threshold = int(config.get("cv_threshold", self.cv_threshold))
        self.strike_interval = int(config.get("strike_interval", self.strike_interval))
        self.sl_proximity = float(config.get("sl_proximity", self.sl_proximity))
        self.target_proximity = float(config.get("target_proximity", self.target_proximity))
        self.gann_target = bool(config.get("gann_target", self.gann_target))
        self.re_entry = bool(config.get("re_entry", self.re_entry))

        # If we already have an open position, recompute SL / target from the
        # NEW config against the actual fill price. Only safe while the SL /
        # target orders are still shadow (not yet placed on the exchange).
        if self.state == State.POSITION_OPEN and self.fill_price > 0:
            if self.gann_target:
                new_target = float(self._ceil_gann(self.fill_price))
            else:
                new_target = self.fill_price + self.target_points

            if self.fill_price >= self.sl_points:
                new_sl = self.fill_price - self.sl_points
            else:
                new_sl = float(self._prev_gann(self.fill_price))

            # Only update shadow legs — if a leg is already live on the
            # exchange, leave it alone to avoid racing with the broker.
            if self.target_shadow:
                self.target_price = new_target
                if self.target_order:
                    self.target_order["price"] = new_target
            if self.sl_shadow:
                self.sl_price = new_sl
                if self.sl_order:
                    self.sl_order["price"] = new_sl

            logger.info(
                f"Config applied to open position: gann_target={self.gann_target} "
                f"SL={self.sl_price} TGT={self.target_price}"
            )

        self._save_state()

    # ── Day reset ──────────────────────────────────

    def _check_day_reset(self):
        today = date.today()
        if self._trading_date != today:
            old_date = self._trading_date
            self._trading_date = today

            # If position was still open from previous day, broker must have
            # squared it off — record it in trade history so it's not lost
            if self.state in (State.POSITION_OPEN, State.ORDER_PLACED) and self.fill_price > 0:
                logger.warning(
                    f"Orphaned {self.state.value} from {old_date} detected — "
                    f"recording as BROKER_SQUAREOFF"
                )
                trade = {
                    "date": (old_date or today).isoformat() if old_date else today.isoformat(),
                    "signal": self.signal_type,
                    "option": self.option_symbol,
                    "atm_strike": self.atm_strike,
                    "entry_price": self.fill_price,
                    "exit_type": "BROKER_SQUAREOFF",
                    "exit_price": self.current_ltp or self.fill_price,
                    "exit_time": "15:29",
                    "lot_size": self.lot_size,
                    "pnl": round(
                        ((self.current_ltp or self.fill_price) - self.fill_price) * self.lot_size, 2
                    ),
                    "timestamp": datetime.now().isoformat(),
                }
                self.trade_log.append(trade)
                self._append_trade_history(trade)

            if self.state in (State.COMPLETED, State.POSITION_OPEN, State.ORDER_PLACED):
                self.state = State.IDLE
                self.signal_type = None
                self.entry_order = None
                self.sl_order = None
                self.target_order = None
                self.sl_shadow = True
                self.target_shadow = True
                self.fill_price = 0.0
                self.current_ltp = 0.0
                self._instruments_cache = None
                self._entry_checklist = {}
                self._save_state()
                logger.info(f"New trading day {today}, reset to IDLE")

    # ── Main check (called every ~60 s) ────────────

    def check(self, cv_data: dict, spot_price: float) -> dict:
        if not self.is_active:
            return self.get_status()

        self._check_day_reset()

        if self.state == State.IDLE:
            self._check_entry_signal(cv_data, spot_price)
        elif self.state == State.ORDER_PLACED:
            self._check_entry_fill()
        elif self.state == State.POSITION_OPEN:
            # Auto square-off at 3:15 PM
            now_time = datetime.now().time()
            if now_time >= PRE_CLOSE_EXIT:
                logger.info(f"Auto square-off triggered at {now_time.strftime('%H:%M:%S')}")
                self._auto_square_off()
            else:
                self._check_exit()

        # Refresh current LTP for display
        self._refresh_current_ltp()

        return self.get_status()

    # ── Entry ──────────────────────────────────────

    def _check_entry_signal(self, cv_data: dict, spot_price: float):
        cv = cv_data.get("last_cumulative_volume", 0)

        # Build entry diagnostics checklist
        self._entry_checklist = {
            "cv_value": cv,
            "cv_threshold": self.cv_threshold,
            "cv_bullish": cv > self.cv_threshold,
            "cv_bearish": cv < -self.cv_threshold,
            "cv_direction": "Bullish" if cv > 0 else ("Bearish" if cv < 0 else "Neutral"),
            "cv_magnitude": abs(cv),
            "cv_pct": round(abs(cv) / self.cv_threshold * 100, 1) if self.cv_threshold > 0 else 0,
            "spot_price": round(spot_price, 2),
            "signal": None,
        }

        if cv > self.cv_threshold:
            self.signal_type = "CE"
            self._entry_checklist["signal"] = "CE"
        elif cv < -self.cv_threshold:
            self.signal_type = "PE"
            self._entry_checklist["signal"] = "PE"
        else:
            self.signal_type = None
            return

        logger.info(f"Signal detected: {self.signal_type} | CV={cv:,}")

        # ATM strike
        self.atm_strike = self._calc_atm(spot_price)

        # Find option instrument
        opt_info = self._find_option(self.atm_strike, self.signal_type)
        if not opt_info:
            logger.error(f"No {self.signal_type} option found at strike {self.atm_strike}")
            return

        self.option_symbol = opt_info["tradingsymbol"]
        self.option_token = int(opt_info["instrument_token"])
        # Use exchange lot size
        if opt_info.get("lot_size"):
            self.lot_size = int(opt_info["lot_size"])

        # Get LTP
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            self.option_ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0.0)
        except Exception as e:
            logger.error(f"LTP fetch failed for {self.option_symbol}: {e}")
            return

        if self.option_ltp <= 0:
            logger.warning(f"Invalid LTP {self.option_ltp}")
            return

        # Floor Gann entry
        self.gann_entry_price = float(self._floor_gann(self.option_ltp))

        # Target: ceiling Gann if gann_target enabled, else entry + target_points
        if self.gann_target:
            self.target_price = float(self._ceil_gann(self.gann_entry_price))
        else:
            self.target_price = self.gann_entry_price + self.target_points

        # SL: default sl_points, but if entry < sl_points use next lower Gann or 0
        if self.gann_entry_price >= self.sl_points:
            self.sl_price = self.gann_entry_price - self.sl_points
        else:
            self.sl_price = float(self._prev_gann(self.gann_entry_price))

        logger.info(
            f"{self.option_symbol} LTP={self.option_ltp} | "
            f"Gann entry={self.gann_entry_price} SL={self.sl_price} TGT={self.target_price}"
        )

        self._place_entry_order()

    def _place_entry_order(self):
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol,
                exchange=Exchange.NFO,
                side=OrderSide.BUY,
                quantity=self.lot_size,
                order_type=OrderType.LIMIT,
                product=ProductType.MIS,
                price=self.gann_entry_price,
                tag="S1ENTRY",
            )
            resp = self.broker.place_order(req)

            self.entry_order = {
                "order_id": resp.order_id,
                "status": resp.status,
                "is_paper": resp.is_paper,
                "price": self.gann_entry_price,
                "timestamp": datetime.now().isoformat(),
            }

            # Paper mode fills immediately
            if resp.is_paper and resp.status == "COMPLETE":
                self.fill_price = self.gann_entry_price
                self.entry_order["status"] = "COMPLETE"
                logger.info(f"Paper entry filled at {self.fill_price}")
                self._on_entry_filled()
            else:
                self.state = State.ORDER_PLACED
                self._save_state()
                logger.info(f"Entry order placed: {resp.order_id}")

        except Exception as e:
            logger.error(f"Entry order failed: {e}")

    # ── Fill check ─────────────────────────────────

    def _check_entry_fill(self):
        if not self.entry_order:
            self.state = State.IDLE
            return

        is_paper = self.entry_order.get("is_paper", False)

        if is_paper:
            # Paper: check LTP to simulate fill
            try:
                ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
                ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0)
            except Exception:
                return
            if ltp > 0 and ltp <= self.gann_entry_price:
                self.fill_price = self.gann_entry_price
                self.entry_order["status"] = "COMPLETE"
                self._on_entry_filled()
        else:
            # Live: check actual order status
            try:
                orders = self.broker.get_orders()
                for o in orders:
                    if str(o.get("order_id")) == str(self.entry_order["order_id"]):
                        status = o.get("status", "")
                        if status == "COMPLETE":
                            self.fill_price = float(o.get("average_price", self.gann_entry_price))
                            self.entry_order["status"] = "COMPLETE"
                            self._on_entry_filled()
                        elif status in ("CANCELLED", "REJECTED"):
                            self.entry_order["status"] = status
                            self.state = State.COMPLETED
                            self._save_state()
                            logger.warning(f"Entry order {status}")
                        break
            except Exception as e:
                logger.error(f"Order status check failed: {e}")

    def _on_entry_filled(self):
        """Called when entry is filled. Keep SL and Target in memory (shadow orders)."""
        # Target: ceiling Gann if gann_target enabled, else fill + target_points
        if self.gann_target:
            self.target_price = float(self._ceil_gann(self.fill_price))
        else:
            self.target_price = self.fill_price + self.target_points

        # SL: default sl_points, but if entry < sl_points use next lower Gann or 0
        if self.fill_price >= self.sl_points:
            self.sl_price = self.fill_price - self.sl_points
        else:
            self.sl_price = float(self._prev_gann(self.fill_price))

        # Shadow orders: track levels in memory only, place on exchange
        # when LTP approaches the level (within proximity buffer)
        self.sl_shadow = True
        self.target_shadow = True

        self.sl_order = {
            "order_id": "SHADOW-SL",
            "status": "SHADOW",
            "price": self.sl_price,
            "is_paper": settings.PAPER_TRADE,
            "timestamp": datetime.now().isoformat(),
        }
        self.target_order = {
            "order_id": "SHADOW-TGT",
            "status": "SHADOW",
            "price": self.target_price,
            "is_paper": settings.PAPER_TRADE,
            "timestamp": datetime.now().isoformat(),
        }

        self.state = State.POSITION_OPEN
        self._save_state()
        logger.info(
            f"Position open. SL={self.sl_price} TGT={self.target_price} "
            f"(shadow — will place when LTP within proximity)"
        )

    # ── Exit check ─────────────────────────────────

    def _check_exit(self):
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0)
        except Exception:
            return
        if ltp <= 0:
            return

        self.current_ltp = ltp

        if settings.PAPER_TRADE:
            self._check_exit_paper(ltp)
        else:
            self._check_exit_live(ltp)

    def _check_exit_paper(self, ltp: float):
        if ltp <= self.sl_price:
            self._complete_trade("SL_HIT", self.sl_price)
        elif ltp >= self.target_price:
            self._complete_trade("TARGET_HIT", self.target_price)

    def _check_exit_live(self, ltp: float):
        # ── Check if already-placed (non-shadow) orders have filled ──
        if not self.sl_shadow or not self.target_shadow:
            try:
                orders = self.broker.get_orders()
            except Exception:
                orders = []

            sl_hit = False
            tgt_hit = False

            for o in orders:
                oid = str(o.get("order_id", ""))
                status = o.get("status", "")
                if (
                    not self.sl_shadow
                    and self.sl_order
                    and oid == str(self.sl_order["order_id"])
                    and status == "COMPLETE"
                ):
                    sl_hit = True
                if (
                    not self.target_shadow
                    and self.target_order
                    and oid == str(self.target_order["order_id"])
                    and status == "COMPLETE"
                ):
                    tgt_hit = True

            if sl_hit:
                self._cancel_order(self.target_order)
                self._complete_trade("SL_HIT", self.sl_price)
                return
            elif tgt_hit:
                self._cancel_order(self.sl_order)
                self._complete_trade("TARGET_HIT", self.target_price)
                return

        # ── Shadow SL: place real order when LTP approaches SL ──
        if self.sl_shadow and ltp <= (self.sl_price + self.sl_proximity):
            # Cancel any existing target order on exchange first to avoid
            # duplicate SELL orders (Zerodha rejects two SELL orders)
            if not self.target_shadow and self.target_order:
                logger.info(
                    f"Cancelling existing target order {self.target_order.get('order_id')} before placing SL order"
                )
                self._cancel_order(self.target_order)
                self.target_order = None
                self.target_shadow = True

            if ltp <= self.sl_price:
                # LTP already at/below SL — place aggressive LIMIT SELL
                exit_price = max(0.05, round(ltp * 0.90, 2))
                logger.warning(
                    f"LTP {ltp} already at/below SL {self.sl_price} — placing LIMIT exit @ {exit_price}"
                )
                try:
                    sl_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.SELL,
                        quantity=self.lot_size,
                        order_type=OrderType.LIMIT,
                        product=ProductType.MIS,
                        price=exit_price,
                        tag="S1SL",
                    )
                    self.broker.place_order(sl_req)
                    self.sl_shadow = False
                    self._complete_trade("SL_HIT", ltp)
                    return
                except Exception as e:
                    logger.error(f"SL MARKET exit failed: {e}")
                    return
            else:
                logger.info(
                    f"LTP {ltp} within {self.sl_proximity} pts of SL {self.sl_price} — placing SL order on exchange"
                )
                try:
                    sl_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.SELL,
                        quantity=self.lot_size,
                        order_type=OrderType.SL_M,
                        product=ProductType.MIS,
                        trigger_price=self.sl_price,
                        tag="S1SL",
                    )
                    sl_resp = self.broker.place_order(sl_req)
                    self.sl_order = {
                        "order_id": sl_resp.order_id,
                        "status": "OPEN",
                        "price": self.sl_price,
                        "timestamp": datetime.now().isoformat(),
                    }
                    self.sl_shadow = False
                    self._save_state()
                    logger.info(f"SL order placed on exchange: {sl_resp.order_id}")
                except Exception as e:
                    logger.error(f"SL order placement failed: {e}")

        # ── Shadow Target: place real order when LTP approaches target ──
        if self.target_shadow and ltp >= (self.target_price - self.target_proximity):
            # Cancel any existing SL order on exchange first to avoid
            # duplicate SELL orders (Zerodha rejects two SELL orders)
            if not self.sl_shadow and self.sl_order:
                logger.info(
                    f"Cancelling existing SL order {self.sl_order.get('order_id')} before placing target order"
                )
                self._cancel_order(self.sl_order)
                self.sl_order = None
                self.sl_shadow = True

            if ltp >= self.target_price:
                # LTP already at/above target — place aggressive LIMIT SELL
                exit_price = max(0.05, round(ltp * 0.90, 2))
                logger.warning(
                    f"LTP {ltp} already at/above TGT {self.target_price} — placing LIMIT exit @ {exit_price}"
                )
                try:
                    tgt_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.SELL,
                        quantity=self.lot_size,
                        order_type=OrderType.LIMIT,
                        product=ProductType.MIS,
                        price=exit_price,
                        tag="S1TGT",
                    )
                    self.broker.place_order(tgt_req)
                    self.target_shadow = False
                    self._cancel_order(self.sl_order)
                    self._complete_trade("TARGET_HIT", ltp)
                    return
                except Exception as e:
                    logger.error(f"Target MARKET exit failed: {e}")
                    return
            else:
                logger.info(
                    f"LTP {ltp} within {self.target_proximity} pts of TGT {self.target_price} — placing target order on exchange"
                )
                try:
                    tgt_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.SELL,
                        quantity=self.lot_size,
                        order_type=OrderType.LIMIT,
                        product=ProductType.MIS,
                        price=self.target_price,
                        tag="S1TGT",
                    )
                    tgt_resp = self.broker.place_order(tgt_req)
                    self.target_order = {
                        "order_id": tgt_resp.order_id,
                        "status": "OPEN",
                        "price": self.target_price,
                        "timestamp": datetime.now().isoformat(),
                    }
                    self.target_shadow = False
                    self._save_state()
                    logger.info(f"Target order placed on exchange: {tgt_resp.order_id}")
                except Exception as e:
                    logger.error(f"Target order placement failed: {e}")

    def _auto_square_off(self):
        """Square off open position at market price (3:15 PM auto exit)."""
        # Cancel any pending SL/Target orders on exchange
        self._cancel_order(self.sl_order)
        self._cancel_order(self.target_order)

        # Get current LTP for exit price
        exit_price = self.current_ltp
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            exit_price = ltp_map.get(f"NFO:{self.option_symbol}", exit_price)
        except Exception:
            pass

        if not settings.PAPER_TRADE:
            # Place aggressive LIMIT sell order to square off
            sq_price = max(0.05, round(exit_price * 0.90, 2))
            try:
                req = OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO,
                    side=OrderSide.SELL,
                    quantity=self.lot_size,
                    order_type=OrderType.LIMIT,
                    product=ProductType.MIS,
                    price=sq_price,
                    tag="S1SQOFF",
                )
                resp = self.broker.place_order(req)
                logger.info(f"Auto square-off order placed: {resp.order_id}")
            except Exception as e:
                logger.error(f"Auto square-off order failed: {e}")
                return

        self._complete_trade("AUTO_SQUAREOFF", exit_price)
        logger.info(f"Auto square-off complete at {exit_price}")

    def _cancel_order(self, order: Optional[dict]):
        if not order or order.get("is_paper"):
            return
        # Shadow orders haven't been placed on exchange — nothing to cancel
        if order.get("status") == "SHADOW" or str(order.get("order_id", "")).startswith("SHADOW"):
            return
        try:
            self.broker.kite.cancel_order(
                variety="regular", order_id=order["order_id"]
            )
        except Exception as e:
            logger.warning(f"Cancel order failed: {e}")

    def _complete_trade(self, exit_type: str, exit_price: float):
        pnl = (exit_price - self.fill_price) * self.lot_size
        if exit_type == "SL_HIT":
            pnl = -abs(pnl)

        trade = {
            "date": (self._trading_date or date.today()).isoformat(),
            "signal": self.signal_type,
            "option": self.option_symbol,
            "atm_strike": self.atm_strike,
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

        logger.info(f"Trade done: {exit_type} | Entry={self.fill_price} Exit={exit_price} PnL={pnl:.2f}")

        # Re-entry: if target was hit and re_entry is enabled, place same entry again.
        # On SL_HIT we always stop the strategy — user must manually start again.
        if exit_type == "TARGET_HIT" and self.re_entry:
            logger.info("Re-entry enabled — placing same entry order again")
            self._append_trade_history(trade)
            self._save_order_snapshot()
            self._re_enter_trade()
            return

        self.state = State.COMPLETED
        if exit_type == "SL_HIT":
            # SL hit — deactivate strategy so user must manually start again
            self.is_active = False
            logger.info("SL hit — strategy deactivated. Please start again manually to resume.")
        self._save_state()
        self._append_trade_history(trade)
        self._save_order_snapshot()

    def _re_enter_trade(self):
        """Re-enter the same trade after target hit (same option, same Gann entry)."""
        # Reset order state but keep option/signal/price details
        self.fill_price = 0.0
        self.current_ltp = 0.0
        self.entry_order = None
        self.sl_order = None
        self.target_order = None
        self.sl_shadow = True
        self.target_shadow = True

        # Place same entry order
        self._place_entry_order()
        logger.info(
            f"Re-entry order placed: {self.option_symbol} @ {self.gann_entry_price} "
            f"SL={self.sl_price} TGT={self.target_price}"
        )

    # ── State persistence ──────────────────────────

    def _save_state(self):
        """Persist strategy state to disk so it survives restarts."""
        state_data = {
            "is_active": self.is_active,
            "state": self.state.value,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "signal_type": self.signal_type,
            "atm_strike": self.atm_strike,
            "option_symbol": self.option_symbol,
            "option_token": self.option_token,
            "option_ltp": self.option_ltp,
            "gann_entry_price": self.gann_entry_price,
            "fill_price": self.fill_price,
            "sl_price": self.sl_price,
            "target_price": self.target_price,
            "current_ltp": self.current_ltp,
            "entry_order": self.entry_order,
            "sl_order": self.sl_order,
            "target_order": self.target_order,
            "sl_shadow": self.sl_shadow,
            "target_shadow": self.target_shadow,
            "trade_log": self.trade_log[-50:],
            "config": {
                "sl_points": self.sl_points,
                "target_points": self.target_points,
                "lot_size": self.lot_size,
                "cv_threshold": self.cv_threshold,
                "strike_interval": self.strike_interval,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "gann_target": self.gann_target,
                "re_entry": self.re_entry,
            },
            "saved_at": datetime.now().isoformat(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state_data, indent=2, default=str))
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _append_trade_history(self, trade: dict):
        """Append a completed trade to the persistent history file."""
        try:
            trades = []
            if TRADE_HISTORY_FILE.exists():
                trades = json.loads(TRADE_HISTORY_FILE.read_text())
            trades.append(trade)
            TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRADE_HISTORY_FILE.write_text(json.dumps(trades, indent=2, default=str))
        except Exception as e:
            logger.error(f"Failed to append trade history: {e}")

    def _save_order_snapshot(self):
        """Snapshot today's Zerodha orders to persistent history file."""
        try:
            raw_orders = self.broker.get_orders()
            if not raw_orders:
                return
            today_str = (self._trading_date or date.today()).isoformat()
            today_orders = []
            for o in raw_orders:
                today_orders.append({
                    "time": str(o.get("order_timestamp", o.get("exchange_timestamp", ""))),
                    "tradingsymbol": o.get("tradingsymbol", ""),
                    "transaction_type": o.get("transaction_type", ""),
                    "quantity": o.get("quantity", 0),
                    "average_price": o.get("average_price", 0),
                    "price": o.get("price", 0),
                    "status": o.get("status", ""),
                    "order_id": str(o.get("order_id", "")),
                    "tag": o.get("tag", ""),
                })

            # Load existing history and replace today's entry
            history = []
            if ORDER_HISTORY_FILE.exists():
                try:
                    history = json.loads(ORDER_HISTORY_FILE.read_text())
                except Exception:
                    pass
            history = [d for d in history if d.get("date") != today_str]
            history.append({"date": today_str, "orders": today_orders})
            history.sort(key=lambda d: d.get("date", ""), reverse=True)
            ORDER_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            ORDER_HISTORY_FILE.write_text(json.dumps(history, indent=2, default=str))
            logger.info(f"Order snapshot saved for {today_str}: {len(today_orders)} orders")
        except Exception as e:
            logger.error(f"Failed to save order snapshot: {e}")

    def restore_state(self) -> bool:
        """Restore strategy state from disk. Returns True if state was restored."""
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load state file: {e}")
            return False

        # Only restore if it's the same trading day
        saved_date = data.get("trading_date", "")
        if saved_date != date.today().isoformat():
            # Recover orphaned trade from previous day
            saved_state = data.get("state", "IDLE")
            fill_price = data.get("fill_price", 0)
            if saved_state in ("POSITION_OPEN", "ORDER_PLACED") and fill_price > 0:
                current_ltp = data.get("current_ltp", fill_price)
                trade = {
                    "date": saved_date,
                    "signal": data.get("signal_type"),
                    "option": data.get("option_symbol", ""),
                    "atm_strike": data.get("atm_strike", 0),
                    "entry_price": fill_price,
                    "exit_type": "BROKER_SQUAREOFF",
                    "exit_price": current_ltp or fill_price,
                    "exit_time": "15:29",
                    "lot_size": data.get("config", {}).get("lot_size", self.lot_size),
                    "pnl": round(((current_ltp or fill_price) - fill_price) * data.get("config", {}).get("lot_size", self.lot_size), 2),
                    "timestamp": datetime.now().isoformat(),
                }
                # Only append if not already in history
                existing = []
                if TRADE_HISTORY_FILE.exists():
                    try:
                        existing = json.loads(TRADE_HISTORY_FILE.read_text())
                    except Exception:
                        pass
                already_logged = any(
                    t.get("date") == saved_date and t.get("option") == data.get("option_symbol", "")
                    for t in existing
                )
                if not already_logged:
                    self._append_trade_history(trade)
                    logger.warning(f"Recovered orphaned trade from {saved_date}: {trade}")

            logger.info(f"State file is from {saved_date}, today is {date.today()} — skipping restore")
            return False

        saved_state = data.get("state", "IDLE")
        if saved_state == "IDLE":
            return False

        # Restore all fields
        self.is_active = data.get("is_active", False)
        self.state = State(saved_state)
        self._trading_date = date.today()
        self.signal_type = data.get("signal_type")
        self.atm_strike = data.get("atm_strike", 0)
        self.option_symbol = data.get("option_symbol", "")
        self.option_token = data.get("option_token", 0)
        self.option_ltp = data.get("option_ltp", 0.0)
        self.gann_entry_price = data.get("gann_entry_price", 0.0)
        self.fill_price = data.get("fill_price", 0.0)
        self.sl_price = data.get("sl_price", 0.0)
        self.target_price = data.get("target_price", 0.0)
        self.current_ltp = data.get("current_ltp", 0.0)
        self.entry_order = data.get("entry_order")
        self.sl_order = data.get("sl_order")
        self.target_order = data.get("target_order")
        self.sl_shadow = data.get("sl_shadow", True)
        self.target_shadow = data.get("target_shadow", True)
        self.trade_log = data.get("trade_log", [])

        # Restore config
        cfg = data.get("config", {})
        if cfg:
            self.sl_points = float(cfg.get("sl_points", self.sl_points))
            self.target_points = float(cfg.get("target_points", self.target_points))
            self.lot_size = int(cfg.get("lot_size", self.lot_size))
            self.cv_threshold = int(cfg.get("cv_threshold", self.cv_threshold))
            self.strike_interval = int(cfg.get("strike_interval", self.strike_interval))
            self.sl_proximity = float(cfg.get("sl_proximity", self.sl_proximity))
            self.target_proximity = float(cfg.get("target_proximity", self.target_proximity))
            self.gann_target = bool(cfg.get("gann_target", self.gann_target))
            self.re_entry = bool(cfg.get("re_entry", self.re_entry))

        logger.info(
            f"State restored: {self.state.value} | "
            f"signal={self.signal_type} option={self.option_symbol} "
            f"fill={self.fill_price} sl_shadow={self.sl_shadow} tgt_shadow={self.target_shadow}"
        )
        return True

    # ── LTP refresh ────────────────────────────────

    def _refresh_current_ltp(self):
        if not self.option_symbol:
            return
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0.0)
            if ltp > 0:
                self.current_ltp = ltp
                self.option_ltp = ltp
        except Exception:
            pass

    # ── Status ─────────────────────────────────────

    def get_status(self) -> dict:
        unrealized_pnl = 0.0
        if self.state == State.POSITION_OPEN and self.current_ltp > 0 and self.fill_price > 0:
            unrealized_pnl = round((self.current_ltp - self.fill_price) * self.lot_size, 2)

        return {
            "is_active": self.is_active,
            "state": self.state.value,
            "signal_type": self.signal_type,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "config": {
                "sl_points": self.sl_points,
                "target_points": self.target_points,
                "lot_size": self.lot_size,
                "cv_threshold": self.cv_threshold,
                "strike_interval": self.strike_interval,
                "gann_target": self.gann_target,
                "re_entry": self.re_entry,
            },
            "trade": {
                "atm_strike": self.atm_strike,
                "option_symbol": self.option_symbol,
                "option_ltp": self.option_ltp,
                "gann_entry_price": self.gann_entry_price,
                "fill_price": self.fill_price,
                "sl_price": self.sl_price,
                "target_price": self.target_price,
                "current_ltp": self.current_ltp,
                "unrealized_pnl": unrealized_pnl,
            },
            "orders": {
                "entry": self.entry_order,
                "sl": self.sl_order,
                "target": self.target_order,
                "sl_shadow": self.sl_shadow,
                "target_shadow": self.target_shadow,
            },
            "entry_checklist": self._entry_checklist,
            "trade_log": self.trade_log[-20:],
        }

    # ── Backtest ───────────────────────────────────

    def backtest(self, cv_data: dict, broker_authenticated: bool = False) -> dict:
        """
        Walk through all candles of the day and simulate the strategy.
        Returns signal info, simulated entry/exit, and PnL.

        cv_data: output from CumulativeVolumeStrategy.compute()
        """
        rows = cv_data.get("rows", [])
        if not rows:
            return {"status": "no_data", "message": "No candle data available"}

        spot_price = cv_data.get("spot_price", 0)
        data_date_str = cv_data.get("data_date", date.today().isoformat())

        # ── Step 1: Find the first candle where CV crosses threshold ──
        signal_candle = None
        signal_type = None
        signal_cv = 0

        for row in rows:
            cv = row.get("cumulative_volume", 0)
            if cv > self.cv_threshold and signal_type is None:
                signal_type = "CE"
                signal_candle = row
                signal_cv = cv
                break
            elif cv < -self.cv_threshold and signal_type is None:
                signal_type = "PE"
                signal_candle = row
                signal_cv = cv
                break

        if not signal_candle:
            return {
                "status": "no_signal",
                "message": f"CV never crossed ±{self.cv_threshold:,} on {data_date_str}",
                "data_date": data_date_str,
                "max_cv": max((r["cumulative_volume"] for r in rows), default=0),
                "min_cv": min((r["cumulative_volume"] for r in rows), default=0),
                "candle_count": len(rows),
                "cv_threshold": self.cv_threshold,
                "cv_timeline": [
                    {"time": r["time"], "cv": r["cumulative_volume"]} for r in rows
                ],
            }

        signal_time = signal_candle["time"]
        signal_spot = signal_candle.get("close", spot_price)

        # ── Step 2: Determine ATM strike & find option ────────────────
        atm_strike = round(signal_spot / self.strike_interval) * self.strike_interval

        # Try to get real option data
        option_candles = []
        option_symbol = ""
        option_info = None

        if broker_authenticated:
            option_info = self._find_option(atm_strike, signal_type)
            if option_info:
                option_symbol = option_info["tradingsymbol"]
                opt_token = int(option_info["instrument_token"])
                try:
                    trading_day = datetime.strptime(data_date_str, "%Y-%m-%d").date()
                    from_dt = datetime.combine(trading_day, MARKET_OPEN)
                    to_dt = datetime.combine(trading_day, MARKET_CLOSE)
                    option_candles = self.broker.get_historical_data(
                        instrument_token=opt_token,
                        from_date=from_dt,
                        to_date=to_dt,
                        interval="minute",
                    )
                except Exception as e:
                    logger.warning(f"Could not fetch option historical data: {e}")

        # ── Step 3: Find option price at signal time & apply Gann ─────
        entry_price = 0.0
        gann_entry = 0.0
        option_price_at_signal = 0.0

        if option_candles:
            # Find option candle closest to signal_time
            for oc in option_candles:
                oc_time = oc["date"]
                if hasattr(oc_time, "strftime"):
                    oc_time_str = oc_time.strftime("%H:%M")
                else:
                    oc_time_str = str(oc_time)[11:16]
                if oc_time_str >= signal_time:
                    option_price_at_signal = float(oc["close"])
                    break

            if option_price_at_signal <= 0 and option_candles:
                option_price_at_signal = float(option_candles[-1]["close"])
        else:
            # Simulate option premium: ATM ~ 1-2% of spot
            option_price_at_signal = round(signal_spot * 0.008, 2)
            option_symbol = f"NIFTY{atm_strike}{signal_type} (simulated)"

        gann_entry = float(self._floor_gann(option_price_at_signal))
        entry_price = gann_entry
        # Target: ceiling Gann if gann_target enabled, else entry + target_points
        if self.gann_target:
            target_price = float(self._ceil_gann(entry_price))
        else:
            target_price = entry_price + self.target_points
        # SL: default sl_points, but if entry < sl_points use next lower Gann or 0
        if entry_price >= self.sl_points:
            sl_price = entry_price - self.sl_points
        else:
            sl_price = float(self._prev_gann(entry_price))

        # ── Step 4: Walk post-signal to check SL / Target ─────────────
        exit_type = None
        exit_price = 0.0
        exit_time = ""
        post_entry_prices = []

        if option_candles:
            # Use real option candles after signal
            in_trade = False
            for oc in option_candles:
                oc_time = oc["date"]
                if hasattr(oc_time, "strftime"):
                    oc_time_str = oc_time.strftime("%H:%M")
                else:
                    oc_time_str = str(oc_time)[11:16]

                if oc_time_str < signal_time:
                    continue

                # After entry, check if price would have filled the LIMIT entry
                if not in_trade:
                    if float(oc["low"]) <= entry_price:
                        in_trade = True
                    else:
                        continue

                low = float(oc["low"])
                high = float(oc["high"])
                close = float(oc["close"])
                post_entry_prices.append({
                    "time": oc_time_str, "open": float(oc["open"]),
                    "high": high, "low": low, "close": close,
                })

                if low <= sl_price:
                    exit_type = "SL_HIT"
                    exit_price = sl_price
                    exit_time = oc_time_str
                    break
                if high >= target_price:
                    exit_type = "TARGET_HIT"
                    exit_price = target_price
                    exit_time = oc_time_str
                    break
        else:
            # Simulate using futures candle movement as proxy
            in_trade = False
            entry_futures = signal_candle["close"]
            for row in rows:
                if row["time"] < signal_time:
                    continue
                if not in_trade:
                    in_trade = True
                    entry_futures = row["close"]
                    continue

                # Approximate option delta movement from futures
                delta = 0.5  # ATM rough delta
                fut_move = row["close"] - entry_futures
                opt_move = fut_move * delta
                sim_price = entry_price + opt_move

                post_entry_prices.append({
                    "time": row["time"],
                    "simulated_price": round(sim_price, 2),
                    "futures_close": row["close"],
                })

                if sim_price <= sl_price:
                    exit_type = "SL_HIT"
                    exit_price = sl_price
                    exit_time = row["time"]
                    break
                if sim_price >= target_price:
                    exit_type = "TARGET_HIT"
                    exit_price = target_price
                    exit_time = row["time"]
                    break

        # ── Step 5: Calculate PnL ─────────────────────────────────────
        pnl = 0.0
        if exit_type == "TARGET_HIT":
            pnl = (target_price - entry_price) * self.lot_size
        elif exit_type == "SL_HIT":
            pnl = -(entry_price - sl_price) * self.lot_size

        # If no exit happened, mark as open at last known price
        last_option_price = entry_price
        if post_entry_prices:
            last_option_price = post_entry_prices[-1].get(
                "close", post_entry_prices[-1].get("simulated_price", entry_price)
            )

        if not exit_type:
            exit_type = "OPEN"
            exit_price = last_option_price
            pnl = round((last_option_price - entry_price) * self.lot_size, 2)

        return {
            "status": "signal_found",
            "data_date": data_date_str,
            "is_simulated": len(option_candles) == 0,
            "candle_count": len(rows),
            "signal": {
                "type": signal_type,
                "time": signal_time,
                "cv_value": signal_cv,
                "spot_price": round(signal_spot, 2),
                "atm_strike": atm_strike,
            },
            "option": {
                "symbol": option_symbol,
                "premium_at_signal": round(option_price_at_signal, 2),
                "gann_entry_price": gann_entry,
            },
            "trade": {
                "entry_price": entry_price,
                "sl_price": sl_price,
                "target_price": target_price,
                "exit_type": exit_type,
                "exit_price": round(exit_price, 2),
                "exit_time": exit_time,
                "lot_size": self.lot_size,
                "pnl": round(pnl, 2),
            },
            "config": {
                "sl_points": self.sl_points,
                "target_points": self.target_points,
                "cv_threshold": self.cv_threshold,
                "strike_interval": self.strike_interval,
                "lot_size": self.lot_size,
                "gann_target": self.gann_target,
            },
            "price_trail": post_entry_prices[-50:],
            "cv_timeline": [
                {"time": r["time"], "cv": r["cumulative_volume"]} for r in rows
            ],
        }
