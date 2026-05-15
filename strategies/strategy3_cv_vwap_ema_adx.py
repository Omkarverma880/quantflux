"""
Strategy 3 — CV + VWAP + EMA200 + ADX Momentum Strategy.

Bidirectional: trades both CALL (bullish) and PUT (bearish).

Entry Rules (3-Phase Setup):
  Phase 1 — Trend Alignment:
    Direction (price-based):
      - EMA 200: Spot above → Bullish (BUY CE), Spot below → Bearish (BUY PE)
      - VWAP alignment: confirms directional bias
    Strength (non-directional):
      - ADX ≥ threshold for trend strength
      - |CV| ≥ threshold for volume participation (magnitude, not direction)

  Phase 2 — Pullback Detection:
    - Price pulls back and touches/crosses EMA 20 against trend direction
    - Bullish: price dips to or below EMA 20, then recovers above it
    - Bearish: price rallies to or above EMA 20, then falls below it

  Phase 3 — Breakout Confirmation:
    - Current candle closes in trend direction (breakout candle)
    - For bullish: close > open (green candle) after pullback
    - For bearish: close < open (red candle) after pullback

Exit Rules:
  - SL / Target / trailing stop
  - Trend exit (price crosses EMA 200 against direction)
  - Auto square-off (env-based time)

Constraints:
  - Max N trades per day (from settings)
  - Max daily loss (from settings)
  - Auto-resets on new trading day
"""
import bisect
import json
import math
import threading
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

logger = get_logger("strategy3.cv_vwap_ema_adx")

GANN_CSV = Path(__file__).resolve().parent.parent / "gann_levels.csv"
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy3_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy3_trades.json"
ORDER_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "order_history.json"

# Auto square-off time from env (default 15:15)
_sq_off = getattr(settings, "AUTO_SQUARE_OFF_TIME", None)
if _sq_off:
    try:
        _h, _m = map(int, str(_sq_off).split(":"))
        PRE_CLOSE_EXIT = dtime(_h, _m)
    except Exception:
        PRE_CLOSE_EXIT = dtime(15, 15)
else:
    PRE_CLOSE_EXIT = dtime(15, 15)


class State(str, Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETED = "COMPLETED"


# ── Indicator helpers (pure functions) ──────────────

def _ema(values: list[float], period: int) -> list[float]:
    """Compute EMA from a list of floats. Returns same-length list (NaN-padded)."""
    if not values or period <= 0:
        return []
    result = [float("nan")] * len(values)
    k = 2 / (period + 1)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Compute ADX from high/low/close lists. Returns same-length list."""
    n = len(closes)
    if n < period + 1:
        return [0.0] * n

    tr_list = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        tr_list[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        plus_dm[i] = h_diff if h_diff > l_diff and h_diff > 0 else 0
        minus_dm[i] = l_diff if l_diff > h_diff and l_diff > 0 else 0

    # Smooth with Wilder's method
    atr = [0.0] * n
    s_plus = [0.0] * n
    s_minus = [0.0] * n

    atr[period] = sum(tr_list[1:period + 1]) / period
    s_plus[period] = sum(plus_dm[1:period + 1]) / period
    s_minus[period] = sum(minus_dm[1:period + 1]) / period

    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr_list[i]) / period
        s_plus[i] = (s_plus[i - 1] * (period - 1) + plus_dm[i]) / period
        s_minus[i] = (s_minus[i - 1] * (period - 1) + minus_dm[i]) / period

    dx = [0.0] * n
    for i in range(period, n):
        if atr[i] == 0:
            continue
        di_plus = 100 * s_plus[i] / atr[i]
        di_minus = 100 * s_minus[i] / atr[i]
        di_sum = di_plus + di_minus
        if di_sum > 0:
            dx[i] = 100 * abs(di_plus - di_minus) / di_sum

    # ADX = EMA of DX
    adx_vals = [0.0] * n
    start = 2 * period
    if start < n:
        adx_vals[start] = sum(dx[period:start + 1]) / (period + 1) if start >= period else 0
        for i in range(start + 1, n):
            adx_vals[i] = (adx_vals[i - 1] * (period - 1) + dx[i]) / period

    return adx_vals


def _vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> list[float]:
    """Compute intraday VWAP from HLC + volume. Returns same-length list."""
    n = len(closes)
    if n == 0:
        return []
    cum_vol = 0.0
    cum_tp_vol = 0.0
    result = [0.0] * n
    for i in range(n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        v = volumes[i] if i < len(volumes) else 0
        cum_tp_vol += tp * v
        cum_vol += v
        result[i] = cum_tp_vol / cum_vol if cum_vol > 0 else tp
    return result


class Strategy3CvVwapEmaAdx:
    """CV + VWAP + EMA200 + ADX Momentum Strategy."""

    def __init__(self, broker: Broker, config: dict):
        self.broker = broker

        # Configurable params
        self.sl_points = float(config.get("sl_points", 40))
        self.target_points = float(config.get("target_points", 60))
        self.trailing_sl = float(config.get("trailing_sl", 20))
        self.lot_size = int(config.get("lot_size", 65))
        self.cv_threshold = int(config.get("cv_threshold", 100_000))
        self.adx_threshold = float(config.get("adx_threshold", 25))
        self.strike_interval = int(config.get("strike_interval", 50))

        # Shadow order proximity
        self.sl_proximity = float(config.get("sl_proximity", 5))
        self.target_proximity = float(config.get("target_proximity", 5))

        # CV filter toggle (default OFF — decoupled from entry)
        self.use_cv_filter = bool(config.get("use_cv_filter", False))

        # Risk limits (from settings)
        self.max_trades_per_day = int(config.get(
            "max_trades_per_day",
            getattr(settings, "MAX_TRADES_PER_DAY", 5),
        ))
        self.max_loss_per_day = float(config.get(
            "max_loss_per_day",
            getattr(settings, "MAX_LOSS_PER_DAY", 5000),
        ))

        # Gann levels
        self.gann_levels = self._load_gann_levels()

        # State
        self.is_active: bool = False
        self.state: State = State.IDLE
        self._trading_date: Optional[date] = None

        # Concurrency guard — prevents duplicate orders when the background
        # loop and the frontend /check + /status timers race each other.
        self._check_lock = threading.Lock()

        # Indicator cache (updated each check)
        self.ema200: float = 0.0
        self.ema20: float = 0.0
        self.vwap: float = 0.0
        self.adx: float = 0.0
        self.cv_trend: str = "—"    # "Bullish" / "Bearish" / "Neutral"
        self.spot_vs_ema200: str = "—"  # "Above" / "Below"
        self.spot_vs_vwap: str = "—"    # "Above" / "Below"

        # 3-phase entry setup tracking
        self._setup_phase: str = "NONE"  # NONE → TREND_ALIGNED → PULLBACK_SEEN → ARMED
        self._setup_direction: Optional[str] = None  # "CE" or "PE"
        self._pullback_touched: bool = False  # price touched EMA20 zone

        # Entry diagnostics (why we didn't enter)
        self._entry_checklist: dict = {}

        # Signal / trade details
        self.signal_type: Optional[str] = None
        self.signal_reason: str = ""
        self.atm_strike: int = 0
        self.option_symbol: str = ""
        self.option_token: int = 0
        self.option_ltp: float = 0.0
        self.entry_price: float = 0.0
        self.fill_price: float = 0.0
        self.sl_price: float = 0.0
        self.target_price: float = 0.0
        self.current_ltp: float = 0.0
        self.trailing_active: bool = False

        # Orders
        self.entry_order: Optional[dict] = None
        self.sl_order: Optional[dict] = None
        self.target_order: Optional[dict] = None
        self.sl_shadow: bool = True
        self.target_shadow: bool = True

        # Instruments cache
        self._instruments_cache = None
        self._instruments_date: Optional[date] = None

        # Daily counters
        self._trades_today: int = 0
        self._daily_pnl: float = 0.0

        # Trade log
        self.trade_log: list[dict] = []

        # Multi-day candle cache for indicators
        self._hist_candles: list[dict] = []
        self._hist_last_fetch: float = 0.0
        self._hist_futures_token: Optional[int] = None

    # ── Gann helpers ──────────────────────────────

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
        idx = bisect.bisect_right(self.gann_levels, price) - 1
        return self.gann_levels[max(0, idx)]

    def _ceil_gann(self, price: float) -> int:
        idx = bisect.bisect_right(self.gann_levels, price)
        if idx < len(self.gann_levels):
            return self.gann_levels[idx]
        return self.gann_levels[-1]

    def _prev_gann(self, gann_level: float) -> int:
        idx = bisect.bisect_left(self.gann_levels, gann_level) - 1
        if idx >= 0:
            return self.gann_levels[idx]
        return 0

    # ── Instrument helpers ────────────────────────

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

    # ── Indicator computation ─────────────────────

    def _resolve_futures_token(self) -> int:
        """Get the NIFTY current-month futures instrument token."""
        if self._hist_futures_token:
            return self._hist_futures_token
        try:
            now = datetime.now()
            yy = now.strftime("%y")
            mon = now.strftime("%b").upper()
            symbol = f"NIFTY{yy}{mon}FUT"
            instruments = self.broker.get_instruments("NFO")
            for inst in instruments:
                if inst.get("tradingsymbol") == symbol:
                    self._hist_futures_token = int(inst["instrument_token"])
                    logger.info(f"Resolved futures token: {symbol} -> {self._hist_futures_token}")
                    return self._hist_futures_token
            logger.warning(f"Could not find token for {symbol}")
        except Exception as e:
            logger.warning(f"Futures token resolution failed: {e}")
        return 0

    def _fetch_multiday_candles(self) -> list[dict]:
        """Fetch 5 trading days of 1-minute candles for continuous indicator computation.

        Caches the historical portion (prior days) and only refreshes today's candles
        every 30 seconds to avoid excessive API calls.
        """
        now = datetime.now()

        # Return cache if fetched within last 30 seconds
        if (
            self._hist_candles
            and self._hist_last_fetch
            and (now - self._hist_last_fetch).total_seconds() < 30
        ):
            return self._hist_candles

        token = self._resolve_futures_token()
        if not token:
            return []

        try:
            # Fetch last 5 calendar days (covers ~3-4 trading days)
            today = now.date()
            from_date = today - timedelta(days=7)
            from_dt = datetime.combine(from_date, MARKET_OPEN)
            to_dt = now if today == now.date() else datetime.combine(today, MARKET_CLOSE)

            candles = self.broker.get_historical_data(
                instrument_token=token,
                from_date=from_dt,
                to_date=to_dt,
                interval="minute",
            )
            if candles:
                self._hist_candles = candles
                self._hist_last_fetch = now
                logger.debug(f"Fetched {len(candles)} multi-day 1m candles for indicators")
            return self._hist_candles or []
        except Exception as e:
            logger.warning(f"Multi-day candle fetch failed: {e}")
            return self._hist_candles or []

    def _compute_indicators(self, cv_data: dict) -> dict:
        """Compute EMA200, EMA20, VWAP, ADX, CV trend.

        EMA 200, EMA 20, ADX: computed from multi-day continuous 1m candles
        (matches TradingView/Kite chart behavior).
        VWAP: computed from today's intraday candles only (standard).
        CV trend: from today's CV data rows.
        """
        # ── Multi-day candles for EMA / ADX ──
        hist_candles = self._fetch_multiday_candles()

        # Fallback to today-only CV rows if historical fetch fails
        rows = cv_data.get("rows", [])
        if not hist_candles and not rows:
            return {
                "ema200": 0, "ema20": 0, "vwap": 0, "adx": 0,
                "cv_trend": "—", "cv_slope": "—", "spot_vs_ema200": "—", "spot_vs_vwap": "—",
                "last_candle_bullish": None,
            }

        # Build OHLCV arrays from multi-day candles
        if hist_candles:
            closes = [float(c.get("close", 0)) for c in hist_candles]
            highs = [float(c.get("high", c.get("close", 0))) for c in hist_candles]
            lows = [float(c.get("low", c.get("close", 0))) for c in hist_candles]
            volumes = [float(c.get("volume", 0)) for c in hist_candles]

            # Today's candles for VWAP (intraday reset)
            today = date.today()
            today_candles = []
            for c in hist_candles:
                dt = c.get("date")
                if dt:
                    if hasattr(dt, "date"):
                        candle_date = dt.date()
                    elif isinstance(dt, str):
                        candle_date = datetime.fromisoformat(dt).date()
                    else:
                        candle_date = today
                    if candle_date == today:
                        today_candles.append(c)
        else:
            # Fallback: use CV rows (today only)
            closes = [float(r.get("close", 0)) for r in rows]
            highs = []
            lows = []
            for r in rows:
                c = float(r.get("close", 0))
                h = float(r.get("high", c))
                l = float(r.get("low", c))
                if h == 0: h = c * 1.001
                if l == 0: l = c * 0.999
                highs.append(h)
                lows.append(l)
            volumes = [abs(float(r.get("raw_volume", 0))) for r in rows]
            today_candles = None  # will use same data for VWAP

        # ── EMA 200 / EMA 20 / ADX — multi-day ──
        ema200_series = _ema(closes, 200)
        ema20_series = _ema(closes, 20)
        adx_series = _adx(highs, lows, closes, 14)

        ema200_val = ema200_series[-1] if ema200_series else 0
        ema20_val = ema20_series[-1] if ema20_series else 0
        adx_val = adx_series[-1] if adx_series else 0

        # ── VWAP — intraday only (standard) ──
        if today_candles:
            t_highs = [float(c.get("high", c.get("close", 0))) for c in today_candles]
            t_lows = [float(c.get("low", c.get("close", 0))) for c in today_candles]
            t_closes = [float(c.get("close", 0)) for c in today_candles]
            t_volumes = [float(c.get("volume", 0)) for c in today_candles]
            vwap_series = _vwap(t_highs, t_lows, t_closes, t_volumes)
            vwap_val = vwap_series[-1] if vwap_series else 0
        elif rows:
            # Fallback: compute from CV rows
            r_highs = [float(r.get("high", r.get("close", 0))) for r in rows]
            r_lows = [float(r.get("low", r.get("close", 0))) for r in rows]
            r_closes = [float(r.get("close", 0)) for r in rows]
            r_volumes = [abs(float(r.get("raw_volume", 0))) for r in rows]
            vwap_series = _vwap(r_highs, r_lows, r_closes, r_volumes)
            vwap_val = vwap_series[-1] if vwap_series else 0
        else:
            vwap_val = 0

        # ── CV trend — based on absolute level, with slope as momentum ──
        cvs = [r.get("cumulative_volume", 0) for r in rows]
        last_cv = cvs[-1] if cvs else 0
        # Primary trend = which side of zero the CV sits
        if last_cv > 0:
            cv_trend = "Bullish"
        elif last_cv < 0:
            cv_trend = "Bearish"
        else:
            cv_trend = "Neutral"

        # Slope = momentum (rising/falling) over last 5 candles
        if len(cvs) >= 5:
            slope = cvs[-1] - cvs[-5]
            if slope > 0:
                cv_slope = "Rising"
            elif slope < 0:
                cv_slope = "Falling"
            else:
                cv_slope = "Flat"
        else:
            cv_slope = "—"

        # Spot vs indicators
        spot = closes[-1] if closes else 0
        spot_vs_ema200 = "Above" if spot > ema200_val > 0 else ("Below" if ema200_val > 0 else "—")
        spot_vs_vwap = "Above" if spot > vwap_val > 0 else ("Below" if vwap_val > 0 else "—")

        # Last candle direction for breakout confirmation
        last_candle_bullish = None
        if rows:
            last_o = float(rows[-1].get("open", 0))
            last_c = float(rows[-1].get("close", 0))
            if last_o > 0 and last_c > 0:
                last_candle_bullish = last_c > last_o

        return {
            "ema200": round(ema200_val, 2),
            "ema20": round(ema20_val, 2),
            "vwap": round(vwap_val, 2),
            "adx": round(adx_val, 2),
            "cv_trend": cv_trend,
            "cv_slope": cv_slope,
            "spot_vs_ema200": spot_vs_ema200,
            "spot_vs_vwap": spot_vs_vwap,
            "last_candle_bullish": last_candle_bullish,
            "candle_count": len(closes),
        }

    # ── Controls ──────────────────────────────────

    def start(self, config: dict):
        self.sl_points = float(config.get("sl_points", self.sl_points))
        self.target_points = float(config.get("target_points", self.target_points))
        self.trailing_sl = float(config.get("trailing_sl", self.trailing_sl))
        self.lot_size = int(config.get("lot_size", self.lot_size))
        self.cv_threshold = int(config.get("cv_threshold", self.cv_threshold))
        self.adx_threshold = float(config.get("adx_threshold", self.adx_threshold))
        self.strike_interval = int(config.get("strike_interval", self.strike_interval))
        self.sl_proximity = float(config.get("sl_proximity", self.sl_proximity))
        self.target_proximity = float(config.get("target_proximity", self.target_proximity))
        self.max_trades_per_day = int(config.get("max_trades_per_day", self.max_trades_per_day))
        self.max_loss_per_day = float(config.get("max_loss_per_day", self.max_loss_per_day))
        self.use_cv_filter = bool(config.get("use_cv_filter", self.use_cv_filter))
        self.is_active = True
        self._check_day_reset()
        self._save_state()
        logger.info(
            f"Strategy 3 started: SL={self.sl_points} TGT={self.target_points} "
            f"Trail={self.trailing_sl} ADX_thresh={self.adx_threshold} CV_thresh={self.cv_threshold}"
        )

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 3 stopped")

    # ── Day reset ─────────────────────────────────

    def _check_day_reset(self):
        today = date.today()
        if self._trading_date != today:
            old_date = self._trading_date
            self._trading_date = today

            if self.state in (State.POSITION_OPEN, State.ORDER_PLACED) and self.fill_price > 0:
                logger.warning(
                    f"Orphaned {self.state.value} from {old_date} — recording as BROKER_SQUAREOFF"
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
                    "pnl": round(((self.current_ltp or self.fill_price) - self.fill_price) * self.lot_size, 2),
                    "timestamp": datetime.now().isoformat(),
                }
                self.trade_log.append(trade)
                self._append_trade_history(trade)

            if self.state in (State.COMPLETED, State.POSITION_OPEN, State.ORDER_PLACED):
                self.state = State.IDLE
                self.signal_type = None
                self.signal_reason = ""
                self.entry_order = None
                self.sl_order = None
                self.target_order = None
                self.sl_shadow = True
                self.target_shadow = True
                self.fill_price = 0.0
                self.current_ltp = 0.0
                self.trailing_active = False
                self._trades_today = 0
                self._daily_pnl = 0.0
                self._instruments_cache = None
                self._setup_phase = "NONE"
                self._setup_direction = None
                self._pullback_touched = False
                self._entry_checklist = {}
                self._save_state()
                logger.info(f"New trading day {today}, reset to IDLE")

    # ── Main check ────────────────────────────────

    def check(self, cv_data: dict, spot_price: float) -> dict:
        if not self.is_active:
            return self.get_status()

        # Non-blocking lock — skip tick if another thread is already in check().
        if not self._check_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self._check_day_reset()

            # Compute indicators every check
            indicators = self._compute_indicators(cv_data)
            self.ema200 = indicators["ema200"]
            self.ema20 = indicators["ema20"]
            self.vwap = indicators["vwap"]
            self.adx = indicators["adx"]
            self.cv_trend = indicators["cv_trend"]
            # Use actual spot price (not futures close) for trend display
            # so the UI matches the entry logic exactly
            if spot_price > 0 and self.ema200 > 0:
                self.spot_vs_ema200 = "Above" if spot_price > self.ema200 else "Below"
            else:
                self.spot_vs_ema200 = indicators["spot_vs_ema200"]
            if spot_price > 0 and self.vwap > 0:
                self.spot_vs_vwap = "Above" if spot_price > self.vwap else "Below"
            else:
                self.spot_vs_vwap = indicators["spot_vs_vwap"]

            if self.state == State.IDLE:
                self._check_entry_signal(cv_data, spot_price, indicators)
            elif self.state == State.ORDER_PLACED:
                self._check_entry_fill(spot_price, indicators)
            elif self.state == State.POSITION_OPEN:
                now_time = datetime.now().time()
                if now_time >= PRE_CLOSE_EXIT:
                    logger.info(f"Auto square-off triggered at {now_time.strftime('%H:%M:%S')}")
                    self._auto_square_off()
                else:
                    self._check_exit()

            self._refresh_current_ltp()
            return self.get_status()
        finally:
            self._check_lock.release()

    # ── Entry ─────────────────────────────────────

    def _check_entry_signal(self, cv_data: dict, spot_price: float, indicators: dict):
        """3-Phase entry: Trend Alignment → Pullback Detection → Breakout Confirmation."""

        # ── Build entry diagnostics checklist ──
        cv = cv_data.get("last_cumulative_volume", 0)
        ema200 = indicators["ema200"]
        ema20 = indicators["ema20"]
        vwap_val = indicators["vwap"]
        adx_val = indicators["adx"]
        cv_trend = indicators["cv_trend"]
        cv_slope = indicators.get("cv_slope", "—")
        last_candle_bullish = indicators.get("last_candle_bullish")

        # Determine price-based trend direction from EMA200
        ema200_trend = "Bullish" if (spot_price > ema200 > 0) else ("Bearish" if ema200 > 0 else "—")
        # CV alignment: trend (absolute level) matches price trend
        cv_aligned = (
            (ema200_trend == "Bullish" and cv_trend == "Bullish")
            or (ema200_trend == "Bearish" and cv_trend == "Bearish")
        )

        checklist = {
            "risk_ok": True,
            "risk_reason": "",
            "ema200_trend": ema200_trend,
            "above_vwap": spot_price > vwap_val if vwap_val > 0 else None,
            "below_vwap": spot_price < vwap_val if vwap_val > 0 else None,
            "vwap_aligned": (
                (ema200_trend == "Bullish" and spot_price > vwap_val)
                or (ema200_trend == "Bearish" and spot_price < vwap_val)
            ) if vwap_val > 0 else False,
            "adx_strong": adx_val >= self.adx_threshold,
            "adx_value": round(adx_val, 1),
            "cv_active": abs(cv) >= self.cv_threshold,
            "cv_bullish": cv > self.cv_threshold,
            "cv_bearish": cv < -self.cv_threshold,
            "cv_value": cv,
            "cv_trend": cv_trend,
            "cv_slope": cv_slope,
            "cv_aligned": cv_aligned,
            "use_cv_filter": self.use_cv_filter,
            "setup_phase": self._setup_phase,
            "setup_direction": self._setup_direction,
            "pullback_touched": self._pullback_touched,
            "last_candle_bullish": last_candle_bullish,
            "spot_price": round(spot_price, 2),
            "ema200_val": ema200,
            "ema20_val": ema20,
            "vwap_val": vwap_val,
        }

        # Risk checks
        if self._trades_today >= self.max_trades_per_day:
            checklist["risk_ok"] = False
            checklist["risk_reason"] = f"Max trades reached ({self._trades_today}/{self.max_trades_per_day})"
            self._entry_checklist = checklist
            return
        if self._daily_pnl <= -self.max_loss_per_day:
            checklist["risk_ok"] = False
            checklist["risk_reason"] = f"Daily loss limit hit (₹{self._daily_pnl:.0f})"
            self._entry_checklist = checklist
            return

        # ── Phase 1: Trend Alignment ──
        # Direction is determined by PRICE ACTION (EMA200 + VWAP)
        # Strength is confirmed by ADX + CV magnitude (direction-agnostic)
        price_bullish = spot_price > ema200 > 0 and spot_price > vwap_val
        price_bearish = spot_price < ema200 and ema200 > 0 and spot_price < vwap_val
        trend_strong = adx_val >= self.adx_threshold
        cv_magnitude_ok = abs(cv) >= self.cv_threshold

        # If CV filter is disabled, bypass the CV magnitude check
        if not self.use_cv_filter:
            cv_magnitude_ok = True

        bullish_trend = price_bullish and trend_strong and cv_magnitude_ok
        bearish_trend = price_bearish and trend_strong and cv_magnitude_ok

        # Determine current trend direction
        if bullish_trend:
            trend_dir = "CE"
        elif bearish_trend:
            trend_dir = "PE"
        else:
            trend_dir = None

        # ── Setup state machine ──
        # If trend breaks or direction changes, reset setup
        if trend_dir is None or (self._setup_direction and trend_dir != self._setup_direction):
            if self._setup_phase != "NONE":
                logger.info(f"Setup reset: trend broken (was {self._setup_direction} phase={self._setup_phase})")
            self._setup_phase = "NONE"
            self._setup_direction = None
            self._pullback_touched = False
            checklist["setup_phase"] = "NONE"
            self._entry_checklist = checklist
            return

        # Phase 1 → TREND_ALIGNED
        if self._setup_phase == "NONE" and trend_dir:
            self._setup_phase = "TREND_ALIGNED"
            self._setup_direction = trend_dir
            self._pullback_touched = False
            logger.info(f"Setup Phase 1: Trend aligned {trend_dir} | Spot={spot_price:.0f} EMA200={ema200:.0f}")

        # ── Phase 2: Pullback Detection ──
        # Bullish: price touches or dips below EMA20 (then must recover)
        # Bearish: price touches or rallies above EMA20 (then must fall)
        if self._setup_phase == "TREND_ALIGNED":
            ema20_zone = ema20 * 0.001  # 0.1% tolerance for "touching"
            if self._setup_direction == "CE":
                # Bullish pullback: price dips to EMA20 zone or below
                if spot_price <= ema20 + ema20_zone:
                    self._pullback_touched = True
                    logger.info(f"Setup Phase 2: Pullback detected (Spot={spot_price:.0f} touched EMA20={ema20:.0f})")
            else:
                # Bearish pullback: price rallies to EMA20 zone or above
                if spot_price >= ema20 - ema20_zone:
                    self._pullback_touched = True
                    logger.info(f"Setup Phase 2: Pullback detected (Spot={spot_price:.0f} touched EMA20={ema20:.0f})")

            if self._pullback_touched:
                self._setup_phase = "PULLBACK_SEEN"

        # ── Phase 3: Breakout Confirmation ──
        # After pullback, wait for a candle that confirms direction
        if self._setup_phase == "PULLBACK_SEEN":
            breakout_confirmed = False

            if self._setup_direction == "CE":
                # Bullish breakout: green candle (close > open) AND price back above EMA20
                if last_candle_bullish is True and spot_price > ema20:
                    breakout_confirmed = True
            else:
                # Bearish breakout: red candle (close < open) AND price back below EMA20
                if last_candle_bullish is False and spot_price < ema20:
                    breakout_confirmed = True

            if breakout_confirmed:
                self._setup_phase = "ARMED"
                logger.info(f"Setup Phase 3: Breakout confirmed! Entry ARMED for {self._setup_direction}")

        checklist["setup_phase"] = self._setup_phase
        checklist["pullback_touched"] = self._pullback_touched
        self._entry_checklist = checklist

        # ── Execute entry only when ARMED ──
        if self._setup_phase != "ARMED":
            return

        # All 3 phases passed — generate signal
        self.signal_type = self._setup_direction
        self.signal_reason = (
            f"{'Bullish' if self.signal_type == 'CE' else 'Bearish'}: "
            f"Spot({spot_price:.0f}){'>' if self.signal_type == 'CE' else '<'}EMA200({ema200:.0f}) "
            f"ADX={adx_val:.1f} CV={cv:,} VWAP={vwap_val:.0f} "
            f"[Pullback→Breakout confirmed]"
        )
        logger.info(f"Signal: {self.signal_reason}")

        # Reset setup for next trade
        self._setup_phase = "NONE"
        self._setup_direction = None
        self._pullback_touched = False

        self.atm_strike = self._calc_atm(spot_price)
        opt_info = self._find_option(self.atm_strike, self.signal_type)
        if not opt_info:
            logger.error(f"No {self.signal_type} option at strike {self.atm_strike}")
            self.signal_type = None
            return

        self.option_symbol = opt_info["tradingsymbol"]
        self.option_token = int(opt_info["instrument_token"])
        if opt_info.get("lot_size"):
            self.lot_size = int(opt_info["lot_size"])

        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            self.option_ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0.0)
        except Exception as e:
            logger.error(f"LTP fetch failed for {self.option_symbol}: {e}")
            return

        if self.option_ltp <= 0:
            logger.warning(f"Invalid LTP {self.option_ltp}")
            return

        # Entry at LTP (tick-rounded) — momentum entry, don't wait for a deep pullback
        tick = 0.05
        self.entry_price = round(round(self.option_ltp / tick) * tick, 2)
        self.target_price = self.entry_price + self.target_points
        if self.entry_price >= self.sl_points:
            self.sl_price = round(round((self.entry_price - self.sl_points) / tick) * tick, 2)
        else:
            self.sl_price = round(round(float(self._prev_gann(self.entry_price)) / tick) * tick, 2)

        logger.info(
            f"{self.option_symbol} LTP={self.option_ltp} | "
            f"Entry={self.entry_price} SL={self.sl_price} TGT={self.target_price}"
        )
        self._place_entry_order()

    def _place_entry_order(self):
        # Pre-flip state BEFORE the broker call so concurrent ticks that
        # slip past the lock still see us as busy. Reverted on failure.
        prev_state = self.state
        self.state = State.ORDER_PLACED
        try:
            req = OrderRequest(
                tradingsymbol=self.option_symbol,
                exchange=Exchange.NFO,
                side=OrderSide.BUY,
                quantity=self.lot_size,
                order_type=OrderType.LIMIT,
                product=ProductType.MIS,
                price=self.entry_price,
                tag="S3ENTRY",
            )
            resp = self.broker.place_order(req)
            self.entry_order = {
                "order_id": resp.order_id,
                "status": resp.status,
                "is_paper": resp.is_paper,
                "price": self.entry_price,
                "timestamp": datetime.now().isoformat(),
            }
            if resp.is_paper and resp.status == "COMPLETE":
                self.fill_price = self.entry_price
                self.entry_order["status"] = "COMPLETE"
                logger.info(f"Paper entry filled at {self.fill_price}")
                self._on_entry_filled()
            else:
                # state already == ORDER_PLACED
                self._save_state()
                logger.info(f"Entry order placed: {resp.order_id}")
        except Exception as e:
            logger.error(f"Entry order failed: {e}")
            # Revert so we can retry on next cycle
            self.state = prev_state
            self.entry_order = None
            self._save_state()

    # ── Fill check ────────────────────────────────

    def _check_entry_fill(self, spot_price: float = 0, indicators: dict = None):
        if not self.entry_order:
            self.state = State.IDLE
            return

        # ── Trend guard: cancel unfilled order if trend has reversed ──
        if indicators and spot_price > 0 and self.signal_type:
            ema200 = indicators.get("ema200", 0)
            vwap_val = indicators.get("vwap", 0)
            if ema200 > 0 and vwap_val > 0:
                still_bullish = spot_price > ema200 and spot_price > vwap_val
                still_bearish = spot_price < ema200 and spot_price < vwap_val
                trend_ok = (
                    (self.signal_type == "CE" and still_bullish)
                    or (self.signal_type == "PE" and still_bearish)
                )
                if not trend_ok:
                    logger.info(
                        f"Trend reversed while waiting for fill — cancelling {self.signal_type} order. "
                        f"Spot={spot_price:.0f} EMA200={ema200:.0f} VWAP={vwap_val:.0f}"
                    )
                    self._cancel_order(self.entry_order)
                    self.entry_order["status"] = "CANCELLED"
                    self.state = State.IDLE
                    self.signal_type = None
                    self.signal_reason = ""
                    self._setup_phase = "NONE"
                    self._setup_direction = None
                    self._pullback_touched = False
                    self._save_state()
                    return

        # ── Order staleness: cancel if unfilled for >60 seconds ──
        placed_at = self.entry_order.get("timestamp")
        if placed_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(placed_at)).total_seconds()
                if elapsed > 60:
                    logger.info(f"Entry order stale ({elapsed:.0f}s) — cancelling")
                    self._cancel_order(self.entry_order)
                    self.entry_order["status"] = "CANCELLED"
                    self.state = State.IDLE
                    self.signal_type = None
                    self.signal_reason = ""
                    self._setup_phase = "NONE"
                    self._setup_direction = None
                    self._pullback_touched = False
                    self._save_state()
                    return
            except Exception:
                pass

        is_paper = self.entry_order.get("is_paper", False)
        if is_paper:
            try:
                ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
                ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0)
            except Exception:
                return
            if ltp > 0 and ltp <= self.entry_price:
                self.fill_price = self.entry_price
                self.entry_order["status"] = "COMPLETE"
                self._on_entry_filled()
        else:
            try:
                orders = self.broker.get_orders()
                for o in orders:
                    if str(o.get("order_id")) == str(self.entry_order["order_id"]):
                        status = o.get("status", "")
                        if status == "COMPLETE":
                            self.fill_price = float(o.get("average_price", self.entry_price))
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
        self.target_price = self.fill_price + self.target_points
        if self.fill_price >= self.sl_points:
            self.sl_price = self.fill_price - self.sl_points
        else:
            self.sl_price = float(self._prev_gann(self.fill_price))

        self.sl_shadow = True
        self.target_shadow = True
        self.trailing_active = False

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

        self._trades_today += 1
        self.state = State.POSITION_OPEN
        self._save_state()
        logger.info(
            f"Position open. SL={self.sl_price} TGT={self.target_price} "
            f"(shadow — will place when LTP within proximity)"
        )

    # ── Exit check ────────────────────────────────

    def _check_exit(self):
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            ltp = ltp_map.get(f"NFO:{self.option_symbol}", 0)
        except Exception:
            return
        if ltp <= 0:
            return

        self.current_ltp = ltp

        # ── Trend exit: price crosses EMA200 against trade direction ──
        if self.ema200 > 0 and self.signal_type:
            # Get current spot price for trend check
            try:
                spot_ltp = self.broker.get_ltp(["NSE:NIFTY 50"])
                spot = spot_ltp.get("NSE:NIFTY 50", 0)
            except Exception:
                spot = 0

            if spot > 0:
                if self.signal_type == "CE" and spot < self.ema200:
                    # Bullish trade but price dropped below EMA200 — trend reversed
                    logger.info(
                        f"Trend exit: Spot({spot:.0f}) crossed below EMA200({self.ema200:.0f}), "
                        f"closing CE position"
                    )
                    self._cancel_order(self.sl_order)
                    self._cancel_order(self.target_order)
                    if not settings.PAPER_TRADE:
                        try:
                            req = OrderRequest(
                                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                                side=OrderSide.SELL, quantity=self.lot_size,
                                order_type=OrderType.LIMIT, product=ProductType.MIS,
                                price=max(0.05, round(ltp * 0.90, 2)), tag="S3TREND",
                            )
                            self.broker.place_order(req)
                        except Exception as e:
                            logger.error(f"Trend exit order failed: {e}")
                            return
                    self._complete_trade("TREND_EXIT", ltp)
                    return
                elif self.signal_type == "PE" and spot > self.ema200:
                    # Bearish trade but price rallied above EMA200 — trend reversed
                    logger.info(
                        f"Trend exit: Spot({spot:.0f}) crossed above EMA200({self.ema200:.0f}), "
                        f"closing PE position"
                    )
                    self._cancel_order(self.sl_order)
                    self._cancel_order(self.target_order)
                    if not settings.PAPER_TRADE:
                        try:
                            req = OrderRequest(
                                tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                                side=OrderSide.SELL, quantity=self.lot_size,
                                order_type=OrderType.LIMIT, product=ProductType.MIS,
                                price=max(0.05, round(ltp * 0.90, 2)), tag="S3TREND",
                            )
                            self.broker.place_order(req)
                        except Exception as e:
                            logger.error(f"Trend exit order failed: {e}")
                            return
                    self._complete_trade("TREND_EXIT", ltp)
                    return

        # Trailing SL logic: once profit > trailing_sl, trail the SL up
        if self.trailing_sl > 0 and self.fill_price > 0:
            profit = ltp - self.fill_price
            if profit >= self.trailing_sl:
                new_sl = ltp - self.trailing_sl
                if new_sl > self.sl_price:
                    old_sl = self.sl_price
                    self.sl_price = new_sl
                    self.trailing_active = True
                    if self.sl_order:
                        self.sl_order["price"] = new_sl
                    logger.info(f"Trailing SL updated: {old_sl:.2f} → {new_sl:.2f}")

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
        # Check filled orders
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
                    and status == "COMPLETE"
                ):
                    self._cancel_order(self.target_order)
                    self._complete_trade("SL_HIT", self.sl_price)
                    return
                if (
                    not self.target_shadow and self.target_order
                    and oid == str(self.target_order["order_id"])
                    and status == "COMPLETE"
                ):
                    self._cancel_order(self.sl_order)
                    self._complete_trade("TARGET_HIT", self.target_price)
                    return

        # Shadow SL placement
        if self.sl_shadow and ltp <= (self.sl_price + self.sl_proximity):
            if not self.target_shadow and self.target_order:
                self._cancel_order(self.target_order)
                self.target_order = None
                self.target_shadow = True

            if ltp <= self.sl_price:
                exit_price = max(0.05, round(ltp * 0.90, 2))
                try:
                    sl_req = OrderRequest(
                        tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                        side=OrderSide.SELL, quantity=self.lot_size,
                        order_type=OrderType.LIMIT, product=ProductType.MIS,
                        price=exit_price, tag="S3SL",
                    )
                    self.broker.place_order(sl_req)
                    self.sl_shadow = False
                    self._complete_trade("SL_HIT", ltp)
                    return
                except Exception as e:
                    logger.error(f"SL exit failed: {e}")
                    return
            else:
                try:
                    sl_req = OrderRequest(
                        tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                        side=OrderSide.SELL, quantity=self.lot_size,
                        order_type=OrderType.SL_M, product=ProductType.MIS,
                        trigger_price=self.sl_price, tag="S3SL",
                    )
                    sl_resp = self.broker.place_order(sl_req)
                    self.sl_order = {
                        "order_id": sl_resp.order_id, "status": "OPEN",
                        "price": self.sl_price, "timestamp": datetime.now().isoformat(),
                    }
                    self.sl_shadow = False
                    self._save_state()
                except Exception as e:
                    logger.error(f"SL order placement failed: {e}")

        # Shadow Target placement
        if self.target_shadow and ltp >= (self.target_price - self.target_proximity):
            if not self.sl_shadow and self.sl_order:
                self._cancel_order(self.sl_order)
                self.sl_order = None
                self.sl_shadow = True

            if ltp >= self.target_price:
                exit_price = max(0.05, round(ltp * 0.90, 2))
                try:
                    tgt_req = OrderRequest(
                        tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                        side=OrderSide.SELL, quantity=self.lot_size,
                        order_type=OrderType.LIMIT, product=ProductType.MIS,
                        price=exit_price, tag="S3TGT",
                    )
                    self.broker.place_order(tgt_req)
                    self.target_shadow = False
                    self._cancel_order(self.sl_order)
                    self._complete_trade("TARGET_HIT", ltp)
                    return
                except Exception as e:
                    logger.error(f"Target exit failed: {e}")
                    return
            else:
                try:
                    tgt_req = OrderRequest(
                        tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                        side=OrderSide.SELL, quantity=self.lot_size,
                        order_type=OrderType.LIMIT, product=ProductType.MIS,
                        price=self.target_price, tag="S3TGT",
                    )
                    tgt_resp = self.broker.place_order(tgt_req)
                    self.target_order = {
                        "order_id": tgt_resp.order_id, "status": "OPEN",
                        "price": self.target_price, "timestamp": datetime.now().isoformat(),
                    }
                    self.target_shadow = False
                    self._save_state()
                except Exception as e:
                    logger.error(f"Target order placement failed: {e}")

    def _auto_square_off(self):
        self._cancel_order(self.sl_order)
        self._cancel_order(self.target_order)

        exit_price = self.current_ltp
        try:
            ltp_map = self.broker.get_ltp([f"NFO:{self.option_symbol}"])
            exit_price = ltp_map.get(f"NFO:{self.option_symbol}", exit_price)
        except Exception:
            pass

        if not settings.PAPER_TRADE:
            sq_price = max(0.05, round(exit_price * 0.90, 2))
            try:
                req = OrderRequest(
                    tradingsymbol=self.option_symbol, exchange=Exchange.NFO,
                    side=OrderSide.SELL, quantity=self.lot_size,
                    order_type=OrderType.LIMIT, product=ProductType.MIS,
                    price=sq_price, tag="S3SQOFF",
                )
                resp = self.broker.place_order(req)
                logger.info(f"Auto square-off order placed: {resp.order_id}")
            except Exception as e:
                logger.error(f"Auto square-off failed: {e}")
                return

        self._complete_trade("AUTO_SQUAREOFF", exit_price)

    def _cancel_order(self, order: Optional[dict]):
        if not order or order.get("is_paper"):
            return
        if order.get("status") == "SHADOW" or str(order.get("order_id", "")).startswith("SHADOW"):
            return
        try:
            self.broker.kite.cancel_order(variety="regular", order_id=order["order_id"])
        except Exception as e:
            logger.warning(f"Cancel order failed: {e}")

    def _complete_trade(self, exit_type: str, exit_price: float):
        pnl = (exit_price - self.fill_price) * self.lot_size
        if exit_type == "SL_HIT":
            pnl = -abs(pnl)

        self._daily_pnl += pnl

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
            "signal_reason": self.signal_reason,
            "indicators": {
                "ema200": self.ema200,
                "ema20": self.ema20,
                "vwap": self.vwap,
                "adx": self.adx,
                "cv_trend": self.cv_trend,
            },
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

    # ── State persistence ─────────────────────────

    def _save_state(self):
        state_data = {
            "is_active": self.is_active,
            "state": self.state.value,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "signal_type": self.signal_type,
            "signal_reason": self.signal_reason,
            "atm_strike": self.atm_strike,
            "option_symbol": self.option_symbol,
            "option_token": self.option_token,
            "option_ltp": self.option_ltp,
            "entry_price": self.entry_price,
            "fill_price": self.fill_price,
            "sl_price": self.sl_price,
            "target_price": self.target_price,
            "current_ltp": self.current_ltp,
            "trailing_active": self.trailing_active,
            "entry_order": self.entry_order,
            "sl_order": self.sl_order,
            "target_order": self.target_order,
            "sl_shadow": self.sl_shadow,
            "target_shadow": self.target_shadow,
            "indicators": {
                "ema200": self.ema200,
                "ema20": self.ema20,
                "vwap": self.vwap,
                "adx": self.adx,
                "cv_trend": self.cv_trend,
                "spot_vs_ema200": self.spot_vs_ema200,
                "spot_vs_vwap": self.spot_vs_vwap,
            },
            "setup": {
                "phase": self._setup_phase,
                "direction": self._setup_direction,
                "pullback_touched": self._pullback_touched,
            },
            "entry_checklist": self._entry_checklist,
            "trades_today": self._trades_today,
            "daily_pnl": self._daily_pnl,
            "trade_log": self.trade_log[-50:],
            "config": {
                "sl_points": self.sl_points,
                "target_points": self.target_points,
                "trailing_sl": self.trailing_sl,
                "lot_size": self.lot_size,
                "cv_threshold": self.cv_threshold,
                "adx_threshold": self.adx_threshold,
                "strike_interval": self.strike_interval,
                "sl_proximity": self.sl_proximity,
                "target_proximity": self.target_proximity,
                "max_trades_per_day": self.max_trades_per_day,
                "max_loss_per_day": self.max_loss_per_day,
                "use_cv_filter": self.use_cv_filter,
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
                    "pnl": round(((current_ltp or fill_price) - fill_price) * lot, 2),
                    "timestamp": datetime.now().isoformat(),
                }
                existing = []
                if TRADE_HISTORY_FILE.exists():
                    try:
                        existing = json.loads(TRADE_HISTORY_FILE.read_text())
                    except Exception:
                        pass
                already = any(
                    t.get("date") == saved_date and t.get("option") == data.get("option_symbol", "")
                    for t in existing
                )
                if not already:
                    self._append_trade_history(trade)
            return False

        saved_state = data.get("state", "IDLE")
        if saved_state == "IDLE":
            return False

        self.is_active = data.get("is_active", False)
        self.state = State(saved_state)
        self._trading_date = date.today()
        self.signal_type = data.get("signal_type")
        self.signal_reason = data.get("signal_reason", "")
        self.atm_strike = data.get("atm_strike", 0)
        self.option_symbol = data.get("option_symbol", "")
        self.option_token = data.get("option_token", 0)
        self.option_ltp = data.get("option_ltp", 0.0)
        self.entry_price = data.get("entry_price", 0.0)
        self.fill_price = data.get("fill_price", 0.0)
        self.sl_price = data.get("sl_price", 0.0)
        self.target_price = data.get("target_price", 0.0)
        self.current_ltp = data.get("current_ltp", 0.0)
        self.trailing_active = data.get("trailing_active", False)
        self.entry_order = data.get("entry_order")
        self.sl_order = data.get("sl_order")
        self.target_order = data.get("target_order")
        self.sl_shadow = data.get("sl_shadow", True)
        self.target_shadow = data.get("target_shadow", True)
        self.trade_log = data.get("trade_log", [])
        self._trades_today = data.get("trades_today", 0)
        self._daily_pnl = data.get("daily_pnl", 0.0)

        ind = data.get("indicators", {})
        self.ema200 = ind.get("ema200", 0)
        self.ema20 = ind.get("ema20", 0)
        self.vwap = ind.get("vwap", 0)
        self.adx = ind.get("adx", 0)
        self.cv_trend = ind.get("cv_trend", "—")
        self.spot_vs_ema200 = ind.get("spot_vs_ema200", "—")
        self.spot_vs_vwap = ind.get("spot_vs_vwap", "—")

        setup = data.get("setup", {})
        self._setup_phase = setup.get("phase", "NONE")
        self._setup_direction = setup.get("direction")
        self._pullback_touched = setup.get("pullback_touched", False)
        self._entry_checklist = data.get("entry_checklist", {})

        cfg = data.get("config", {})
        if cfg:
            self.sl_points = float(cfg.get("sl_points", self.sl_points))
            self.target_points = float(cfg.get("target_points", self.target_points))
            self.trailing_sl = float(cfg.get("trailing_sl", self.trailing_sl))
            self.lot_size = int(cfg.get("lot_size", self.lot_size))
            self.cv_threshold = int(cfg.get("cv_threshold", self.cv_threshold))
            self.adx_threshold = float(cfg.get("adx_threshold", self.adx_threshold))
            self.strike_interval = int(cfg.get("strike_interval", self.strike_interval))
            self.sl_proximity = float(cfg.get("sl_proximity", self.sl_proximity))
            self.target_proximity = float(cfg.get("target_proximity", self.target_proximity))
            self.max_trades_per_day = int(cfg.get("max_trades_per_day", self.max_trades_per_day))
            self.max_loss_per_day = float(cfg.get("max_loss_per_day", self.max_loss_per_day))
            self.use_cv_filter = bool(cfg.get("use_cv_filter", self.use_cv_filter))

        logger.info(
            f"State restored: {self.state.value} | signal={self.signal_type} "
            f"option={self.option_symbol} fill={self.fill_price}"
        )
        return True

    # ── LTP refresh ───────────────────────────────

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

    # ── Status ────────────────────────────────────

    def get_status(self) -> dict:
        try:
            self._check_day_reset()
        except Exception:
            pass
        unrealized_pnl = 0.0
        if self.state == State.POSITION_OPEN and self.current_ltp > 0 and self.fill_price > 0:
            unrealized_pnl = round((self.current_ltp - self.fill_price) * self.lot_size, 2)

        return {
            "is_active": self.is_active,
            "state": self.state.value,
            "signal_type": self.signal_type,
            "signal_reason": self.signal_reason,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "indicators": {
                "ema200": self.ema200,
                "ema20": self.ema20,
                "vwap": self.vwap,
                "adx": self.adx,
                "cv_trend": self.cv_trend,
                "spot_vs_ema200": self.spot_vs_ema200,
                "spot_vs_vwap": self.spot_vs_vwap,
            },
            "setup": {
                "phase": self._setup_phase,
                "direction": self._setup_direction,
                "pullback_touched": self._pullback_touched,
            },
            "entry_checklist": self._entry_checklist,
            "config": {
                "sl_points": self.sl_points,
                "target_points": self.target_points,
                "trailing_sl": self.trailing_sl,
                "lot_size": self.lot_size,
                "cv_threshold": self.cv_threshold,
                "adx_threshold": self.adx_threshold,
                "strike_interval": self.strike_interval,
                "max_trades_per_day": self.max_trades_per_day,
                "max_loss_per_day": self.max_loss_per_day,
                "use_cv_filter": self.use_cv_filter,
            },
            "trade": {
                "atm_strike": self.atm_strike,
                "option_symbol": self.option_symbol,
                "option_ltp": self.option_ltp,
                "entry_price": self.entry_price,
                "fill_price": self.fill_price,
                "sl_price": self.sl_price,
                "target_price": self.target_price,
                "current_ltp": self.current_ltp,
                "unrealized_pnl": unrealized_pnl,
                "trailing_active": self.trailing_active,
            },
            "orders": {
                "entry": self.entry_order,
                "sl": self.sl_order,
                "target": self.target_order,
                "sl_shadow": self.sl_shadow,
                "target_shadow": self.target_shadow,
            },
            "risk": {
                "trades_today": self._trades_today,
                "daily_pnl": round(self._daily_pnl, 2),
                "max_trades_per_day": self.max_trades_per_day,
                "max_loss_per_day": self.max_loss_per_day,
            },
            "trade_log": self.trade_log[-20:],
        }
