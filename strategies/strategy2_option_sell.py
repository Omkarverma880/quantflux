"""
Strategy 2 — Gann + Cumulative Volume Option Selling Strategy.

Entry Rules:
  - CV > +threshold (bullish)  → SELL ATM PUT  at ceiling Gann level of its LTP
  - CV < −threshold (bearish)  → SELL ATM CALL at ceiling Gann level of its LTP

Exit Rules (inverted from buying — premium must decay for profit):
  - SL  = entry + sl_points   (premium rising = loss)
  - TGT = entry − target_points (premium decaying = profit)

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

logger = get_logger("strategy2.option_sell")

GANN_CSV = Path(__file__).resolve().parent.parent / "gann_levels.csv"
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)
STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy2_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy2_trades.json"
ORDER_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "order_history.json"


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


class Strategy2OptionSell:
    """Gann ceiling entry (option selling) driven by cumulative volume signal."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # Configurable params
        self.sl_points = float(config.get("sl_points", 45))
        self.target_points = float(config.get("target_points", 55))
        self.lot_size = int(config.get("lot_size", 65))
        self.cv_threshold = int(config.get("cv_threshold", 150_000))
        self.strike_interval = int(config.get("strike_interval", 50))

        # Shadow order proximity
        self.sl_proximity = float(config.get("sl_proximity", 5))
        self.target_proximity = float(config.get("target_proximity", 5))

        # Gann target toggle: if True, target = prev Gann; else entry - target_points
        self.gann_target = bool(config.get("gann_target", False))

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

        # Shadow order state
        self.sl_shadow: bool = True
        self.target_shadow: bool = True

        # Instruments cache
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None

        # Trade log
        self.trade_log: list[dict] = []

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

    def _next_gann(self, gann_level: float) -> int:
        """Gann level immediately above the given Gann level."""
        idx = bisect.bisect_right(self.gann_levels, gann_level)
        if idx < len(self.gann_levels):
            return self.gann_levels[idx]
        return self.gann_levels[-1]

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
        self.is_active = True
        self._check_day_reset()
        self._save_state()
        logger.info(
            f"Strategy 2 started: SL={self.sl_points} TGT={self.target_points} "
            f"Lot={self.lot_size} CV_thresh={self.cv_threshold}"
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 2 stopped")

    # ── Day reset ──────────────────────────────────

    def _check_day_reset(self):
        today = date.today()
        if self._trading_date != today:
            old_date = self._trading_date
            self._trading_date = today

            if self.state in (State.POSITION_OPEN, State.ORDER_PLACED) and self.fill_price > 0:
                logger.warning(
                    f"Orphaned {self.state.value} from {old_date} detected — "
                    f"recording as BROKER_SQUAREOFF"
                )
                # For selling: profit = entry - exit (premium decay)
                exit_ltp = self.current_ltp or self.fill_price
                trade = {
                    "date": (old_date or today).isoformat() if old_date else today.isoformat(),
                    "signal": self.signal_type,
                    "option": self.option_symbol,
                    "atm_strike": self.atm_strike,
                    "entry_price": self.fill_price,
                    "exit_type": "BROKER_SQUAREOFF",
                    "exit_price": exit_ltp,
                    "exit_time": "15:29",
                    "lot_size": self.lot_size,
                    "pnl": round(
                        (self.fill_price - exit_ltp) * self.lot_size, 2
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
            now_time = datetime.now().time()
            if now_time >= PRE_CLOSE_EXIT:
                logger.info(f"Auto square-off triggered at {now_time.strftime('%H:%M:%S')}")
                self._auto_square_off()
            else:
                self._check_exit()

        self._refresh_current_ltp()

        return self.get_status()

    # ── Entry ──────────────────────────────────────
    # SELLING: bullish → sell PUT, bearish → sell CALL (opposite of buying)

    def _check_entry_signal(self, cv_data: dict, spot_price: float):
        cv = cv_data.get("last_cumulative_volume", 0)

        if cv > self.cv_threshold:
            # Bullish → sell PUT (premium will decay as market goes up)
            self.signal_type = "PE"
        elif cv < -self.cv_threshold:
            # Bearish → sell CALL (premium will decay as market goes down)
            self.signal_type = "CE"
        else:
            self.signal_type = None
            return

        logger.info(f"Sell signal detected: SELL {self.signal_type} | CV={cv:,}")

        # ATM strike
        self.atm_strike = self._calc_atm(spot_price)

        # Find option instrument
        opt_info = self._find_option(self.atm_strike, self.signal_type)
        if not opt_info:
            logger.error(f"No {self.signal_type} option found at strike {self.atm_strike}")
            return

        self.option_symbol = opt_info["tradingsymbol"]
        self.option_token = int(opt_info["instrument_token"])
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

        # Ceiling Gann entry (sell high)
        self.gann_entry_price = float(self._ceil_gann(self.option_ltp))

        # Target (premium decay): prev Gann if gann_target, else entry - target_points
        if self.gann_target:
            self.target_price = float(self._prev_gann(self.gann_entry_price))
        else:
            self.target_price = max(0.05, self.gann_entry_price - self.target_points)

        # SL (premium rise): entry + sl_points, or next Gann above
        self.sl_price = self.gann_entry_price + self.sl_points

        logger.info(
            f"SELL {self.option_symbol} LTP={self.option_ltp} | "
            f"Gann entry={self.gann_entry_price} SL={self.sl_price} TGT={self.target_price}"
        )

        self._place_entry_order()

    def _place_entry_order(self):
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol,
                exchange=Exchange.NFO,
                side=OrderSide.SELL,   # SELLING the option
                quantity=self.lot_size,
                order_type=OrderType.LIMIT,
                product=ProductType.MIS,
                price=self.gann_entry_price,
                tag="S2ENTRY",
            )
            resp = self.broker.place_order(req)

            self.entry_order = {
                "order_id": resp.order_id,
                "status": resp.status,
                "is_paper": resp.is_paper,
                "price": self.gann_entry_price,
                "timestamp": datetime.now().isoformat(),
            }

            if resp.is_paper and resp.status == "COMPLETE":
                self.fill_price = self.gann_entry_price
                self.entry_order["status"] = "COMPLETE"
                logger.info(f"Paper entry filled at {self.fill_price}")
                self._on_entry_filled()
            else:
                self.state = State.ORDER_PLACED
                self._save_state()
                logger.info(f"Entry sell order placed: {resp.order_id}")

        except Exception as e:
            logger.error(f"Entry order failed: {e}")

    # ── Fill check ─────────────────────────────────

    def _check_entry_fill(self):
        if not self.entry_order:
            self.state = State.IDLE
            return

        is_paper = self.entry_order.get("is_paper", False)

        if is_paper:
            try:
                ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
                ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0)
            except Exception:
                return
            # For selling: fill when price rises to our limit
            if ltp > 0 and ltp >= self.gann_entry_price:
                self.fill_price = self.gann_entry_price
                self.entry_order["status"] = "COMPLETE"
                self._on_entry_filled()
        else:
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
        """Called when entry sell is filled."""
        # Target (premium decay): prev Gann if gann_target, else fill - target_points
        if self.gann_target:
            self.target_price = float(self._prev_gann(self.fill_price))
        else:
            self.target_price = max(0.05, self.fill_price - self.target_points)

        # SL (premium rise): fill + sl_points
        self.sl_price = self.fill_price + self.sl_points

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
            f"Short position open. SL={self.sl_price} TGT={self.target_price} "
            f"(shadow — will place when LTP within proximity)"
        )

    # ── Exit check ─────────────────────────────────
    # For selling: SL when price RISES, Target when price DROPS

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
        # SL: premium rose above SL level
        if ltp >= self.sl_price:
            self._complete_trade("SL_HIT", self.sl_price)
        # Target: premium decayed below target level
        elif ltp <= self.target_price:
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

        # ── Shadow SL: place real BUY order when LTP approaches SL (rising) ──
        if self.sl_shadow and ltp >= (self.sl_price - self.sl_proximity):
            # Cancel any existing target order on exchange first to avoid
            # duplicate exit orders
            if not self.target_shadow and self.target_order:
                logger.info(
                    f"Cancelling existing target order {self.target_order.get('order_id')} before placing SL order"
                )
                self._cancel_order(self.target_order)
                self.target_order = None
                self.target_shadow = True

            if ltp >= self.sl_price:
                # LTP already at/above SL — place aggressive LIMIT BUY to cover
                exit_price = round(ltp * 1.10, 2)
                logger.warning(
                    f"LTP {ltp} already at/above SL {self.sl_price} — placing LIMIT BUY @ {exit_price}"
                )
                try:
                    sl_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.BUY,   # Buy back to cover
                        quantity=self.lot_size,
                        order_type=OrderType.LIMIT,
                        product=ProductType.MIS,
                        price=exit_price,
                        tag="S2SL",
                    )
                    self.broker.place_order(sl_req)
                    self.sl_shadow = False
                    self._complete_trade("SL_HIT", ltp)
                    return
                except Exception as e:
                    logger.error(f"SL BUY exit failed: {e}")
                    return
            else:
                logger.info(
                    f"LTP {ltp} within {self.sl_proximity} pts of SL {self.sl_price} — placing SL order on exchange"
                )
                try:
                    sl_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.BUY,   # Buy back to cover
                        quantity=self.lot_size,
                        order_type=OrderType.SL_M,
                        product=ProductType.MIS,
                        trigger_price=self.sl_price,
                        tag="S2SL",
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

        # ── Shadow Target: place real BUY order when LTP drops to target ──
        if self.target_shadow and ltp <= (self.target_price + self.target_proximity):
            # Cancel any existing SL order on exchange first to avoid
            # duplicate exit orders
            if not self.sl_shadow and self.sl_order:
                logger.info(
                    f"Cancelling existing SL order {self.sl_order.get('order_id')} before placing target order"
                )
                self._cancel_order(self.sl_order)
                self.sl_order = None
                self.sl_shadow = True

            if ltp <= self.target_price:
                # LTP already at/below target — place aggressive LIMIT BUY
                exit_price = max(0.05, round(ltp * 0.90, 2))
                logger.warning(
                    f"LTP {ltp} already at/below TGT {self.target_price} — placing LIMIT BUY @ {exit_price}"
                )
                try:
                    tgt_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.BUY,   # Buy back to cover
                        quantity=self.lot_size,
                        order_type=OrderType.LIMIT,
                        product=ProductType.MIS,
                        price=exit_price,
                        tag="S2TGT",
                    )
                    self.broker.place_order(tgt_req)
                    self.target_shadow = False
                    self._cancel_order(self.sl_order)
                    self._complete_trade("TARGET_HIT", ltp)
                    return
                except Exception as e:
                    logger.error(f"Target BUY exit failed: {e}")
                    return
            else:
                logger.info(
                    f"LTP {ltp} within {self.target_proximity} pts of TGT {self.target_price} — placing target order on exchange"
                )
                try:
                    tgt_req = OrderRequest(
                        tradingsymbol=self.option_symbol,
                        exchange=Exchange.NFO,
                        side=OrderSide.BUY,   # Buy back to cover
                        quantity=self.lot_size,
                        order_type=OrderType.LIMIT,
                        product=ProductType.MIS,
                        price=self.target_price,
                        tag="S2TGT",
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
        """Square off short position at market price (3:15 PM)."""
        self._cancel_order(self.sl_order)
        self._cancel_order(self.target_order)

        exit_price = self.current_ltp
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            exit_price = ltp_map.get(f"NFO:{self.option_symbol}", exit_price)
        except Exception:
            pass

        if not settings.PAPER_TRADE:
            # Buy back to cover the short
            sq_price = round(exit_price * 1.10, 2)
            try:
                req = OrderRequest(
                    tradingsymbol=self.option_symbol,
                    exchange=Exchange.NFO,
                    side=OrderSide.BUY,
                    quantity=self.lot_size,
                    order_type=OrderType.LIMIT,
                    product=ProductType.MIS,
                    price=sq_price,
                    tag="S2SQOFF",
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
        if order.get("status") == "SHADOW" or str(order.get("order_id", "")).startswith("SHADOW"):
            return
        try:
            self.broker.kite.cancel_order(
                variety="regular", order_id=order["order_id"]
            )
        except Exception as e:
            logger.warning(f"Cancel order failed: {e}")

    def _complete_trade(self, exit_type: str, exit_price: float):
        # For selling: PnL = (entry - exit) * lot_size
        pnl = (self.fill_price - exit_price) * self.lot_size
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

        self.state = State.COMPLETED
        self._save_state()
        self._append_trade_history(trade)
        self._save_order_snapshot()
        logger.info(f"Trade done: {exit_type} | Entry={self.fill_price} Exit={exit_price} PnL={pnl:.2f}")

    # ── State persistence ──────────────────────────

    def _save_state(self):
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
            },
            "saved_at": datetime.now().isoformat(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state_data, indent=2, default=str))
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _append_trade_history(self, trade: dict):
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
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load state file: {e}")
            return False

        saved_date = data.get("trading_date", "")
        if saved_date != date.today().isoformat():
            saved_state = data.get("state", "IDLE")
            fill_price = data.get("fill_price", 0)
            if saved_state in ("POSITION_OPEN", "ORDER_PLACED") and fill_price > 0:
                current_ltp = data.get("current_ltp", fill_price)
                lot = data.get("config", {}).get("lot_size", self.lot_size)
                trade = {
                    "date": saved_date,
                    "signal": data.get("signal_type"),
                    "option": data.get("option_symbol", ""),
                    "atm_strike": data.get("atm_strike", 0),
                    "entry_price": fill_price,
                    "exit_type": "BROKER_SQUAREOFF",
                    "exit_price": current_ltp or fill_price,
                    "exit_time": "15:29",
                    "lot_size": lot,
                    "pnl": round((fill_price - (current_ltp or fill_price)) * lot, 2),
                    "timestamp": datetime.now().isoformat(),
                }
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
            # Selling: profit when premium drops
            unrealized_pnl = round((self.fill_price - self.current_ltp) * self.lot_size, 2)

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
            "trade_log": self.trade_log[-20:],
        }
