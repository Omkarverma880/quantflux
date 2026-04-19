"""
Strategy 1 — Cumulative Volume Analysis for NIFTY Futures.

Fetches 1-minute OHLCV data, applies signed-volume logic,
computes a running cumulative volume from market open (09:15),
and exposes a data accessor for the dashboard route.

Falls back to realistic demo data ONLY when broker is not
authenticated. Once logged in, always shows real market data
(latest available trading day).

Reusable: change `instruments` and `spot_instrument` in config
to apply the same logic to any index/symbol.
"""
import random
import pandas as pd
from datetime import datetime, date, time as dtime, timedelta

from strategies.base_strategy import BaseStrategy, StrategyConfig
from core.broker import Broker
from core.logger import get_logger

logger = get_logger("strategy.cumulative_volume")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
BULLISH_THRESHOLD = 50_000
BEARISH_THRESHOLD = -50_000


class CumulativeVolumeStrategy(BaseStrategy):
    """
    Non-trading analysis strategy.
    Computes signed & cumulative volume for an index futures contract
    and provides the result table via compute().
    """

    def __init__(self, config: StrategyConfig, broker: Broker):
        super().__init__(config, broker)
        default_fut = self._current_month_futures()
        self.futures_instrument = config.params.get(
            "futures_instrument", default_fut
        )
        self.futures_token = int(config.params.get("futures_token", 0))
        self.spot_instrument = config.params.get(
            "spot_instrument", "NSE:NIFTY 50"
        )
        self.threshold = int(config.params.get("threshold", BULLISH_THRESHOLD))
        self._last_df: pd.DataFrame = pd.DataFrame()
        self._spot_price: float = 0.0
        self._trend_bias: str = "Neutral"
        self._is_demo: bool = False
        self._data_date: date = date.today()
        self._token_resolved: bool = False

    # ── Core computation ───────────────────────────────

    def compute(self, broker_authenticated: bool = False) -> dict:
        """
        Fetch latest data, compute cumulative volume, return result dict.
        Uses live data when broker is authenticated, demo data otherwise.
        """
        candles = []
        self._is_demo = False
        self._data_date = self._last_trading_day()

        if broker_authenticated:
            # Auto-resolve token from instrument name if not yet done
            if not self._token_resolved or self.futures_token == 0:
                self._resolve_token()

            # Fetch live candles
            if self.futures_token and self.futures_token != 0:
                candles = self._fetch_live_candles()

            # Fetch live spot price
            self._fetch_spot_price()

        # Fallback to demo ONLY if broker is NOT authenticated
        if not candles:
            if broker_authenticated:
                logger.warning("Authenticated but no candle data found")
            self._is_demo = not broker_authenticated
            candles = self._generate_demo_candles()
            if self._spot_price == 0:
                self._spot_price = 24850.50

        if not candles:
            return self._empty_result()

        df = pd.DataFrame(candles)
        df.rename(columns={"date": "datetime"}, inplace=True)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["time"] = df["datetime"].dt.strftime("%H:%M")

        # Signed volume
        df["signed_volume"] = df.apply(self._sign_volume, axis=1)

        # Cumulative volume from market open
        df["cumulative_volume"] = df["signed_volume"].cumsum()

        # Spot price column
        df["spot_price"] = self._spot_price

        # Determine trend bias from last cumulative value
        last_cv = int(df["cumulative_volume"].iloc[-1])
        if last_cv > self.threshold:
            self._trend_bias = "Bullish"
        elif last_cv < -self.threshold:
            self._trend_bias = "Bearish"
        else:
            self._trend_bias = "Neutral"

        self._last_df = df
        return self._build_result(df, last_cv)

    # ── Token resolution ─────────────────────────────

    @staticmethod
    def _current_month_futures() -> str:
        """Build the current month NIFTY futures symbol, e.g. NFO:NIFTY26APRFUT."""
        now = datetime.now()
        yy = now.strftime("%y")          # "26"
        mon = now.strftime("%b").upper() # "APR"
        return f"NFO:NIFTY{yy}{mon}FUT"

    def _resolve_token(self):
        """Auto-resolve instrument_token from the futures_instrument name."""
        try:
            parts = self.futures_instrument.split(":")
            if len(parts) != 2:
                return
            exchange, symbol = parts[0], parts[1]

            instruments = self.broker.get_instruments(exchange)
            for inst in instruments:
                if inst.get("tradingsymbol") == symbol:
                    self.futures_token = int(inst["instrument_token"])
                    self._token_resolved = True
                    logger.info(
                        f"Resolved {self.futures_instrument} -> token {self.futures_token}"
                    )
                    return

            logger.warning(f"Could not find token for {self.futures_instrument}")
        except Exception as e:
            logger.warning(f"Token resolution failed: {e}")

    # ── Date helpers ─────────────────────────────────

    @staticmethod
    def _last_trading_day() -> date:
        """
        Return the most recent trading day:
        - Weekday + market opened -> today
        - Weekday + before 09:15 -> previous weekday
        - Saturday/Sunday -> last Friday
        """
        now = datetime.now()
        d = now.date()
        if now.time() < MARKET_OPEN:
            d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    # ── Live data fetchers ─────────────────────────────

    def _fetch_live_candles(self) -> list:
        trading_day = self._last_trading_day()
        self._data_date = trading_day
        from_dt = datetime.combine(trading_day, MARKET_OPEN)
        to_dt = datetime.combine(trading_day, MARKET_CLOSE)

        now = datetime.now()
        if trading_day == now.date() and now < to_dt:
            to_dt = now

        try:
            candles = self.broker.get_historical_data(
                instrument_token=self.futures_token,
                from_date=from_dt,
                to_date=to_dt,
                interval="minute",
            )
            if candles:
                return candles
        except Exception as e:
            logger.warning(f"Live data unavailable for {trading_day}: {e}")

        # Try previous 5 weekdays if today had no data
        for _ in range(5):
            trading_day -= timedelta(days=1)
            while trading_day.weekday() >= 5:
                trading_day -= timedelta(days=1)
            self._data_date = trading_day
            from_dt = datetime.combine(trading_day, MARKET_OPEN)
            to_dt = datetime.combine(trading_day, MARKET_CLOSE)
            try:
                candles = self.broker.get_historical_data(
                    instrument_token=self.futures_token,
                    from_date=from_dt,
                    to_date=to_dt,
                    interval="minute",
                )
                if candles:
                    logger.info(f"Using data from {trading_day} (last available)")
                    return candles
            except Exception:
                continue

        return []

    def _fetch_spot_price(self):
        try:
            ltp = self.broker.get_ltp([self.spot_instrument])
            self._spot_price = ltp.get(self.spot_instrument, 0.0)
        except Exception:
            pass

    # ── Demo data generator ────────────────────────────

    def _generate_demo_candles(self) -> list:
        """
        Produces realistic 1-min NIFTY Futures candles for the
        last trading day. Only used when broker is NOT authenticated.
        """
        trading_day = self._last_trading_day()
        self._data_date = trading_day
        now = datetime.now()
        market_open = datetime.combine(trading_day, MARKET_OPEN)
        market_close = datetime.combine(trading_day, MARKET_CLOSE)

        if trading_day == now.date() and market_open <= now <= market_close:
            end = now
        else:
            end = market_close

        total_minutes = int((end - market_open).total_seconds() // 60)
        if total_minutes <= 0:
            total_minutes = 375

        random.seed(trading_day.toordinal())

        candles = []
        price = 24900.0 + random.uniform(-100, 100)

        for i in range(total_minutes):
            ts = market_open + timedelta(minutes=i)
            change = random.gauss(0, 8)
            o = round(price, 2)
            c = round(price + change, 2)
            h = round(max(o, c) + abs(random.gauss(0, 4)), 2)
            l = round(min(o, c) - abs(random.gauss(0, 4)), 2)
            base_vol = random.randint(3000, 25000)
            if i < 15 or i > total_minutes - 15:
                base_vol = int(base_vol * 2.5)
            candles.append({
                "date": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": base_vol,
            })
            price = c

        return candles

    # ── Helpers ────────────────────────────────────────

    @staticmethod
    def _sign_volume(row) -> int:
        if row["close"] < row["open"]:
            return -int(row["volume"])
        elif row["close"] > row["open"]:
            return int(row["volume"])
        return 0

    def _build_result(self, df: pd.DataFrame, last_cv: int) -> dict:
        now = datetime.now()
        rows = []
        for _, r in df.iterrows():
            cv = int(r["cumulative_volume"])
            if cv > self.threshold:
                highlight = "green"
            elif cv < -self.threshold:
                highlight = "red"
            else:
                highlight = "neutral"

            rows.append({
                "time": r["time"],
                "open": round(float(r["open"]), 2),
                "high": round(float(r["high"]), 2),
                "low": round(float(r["low"]), 2),
                "close": round(float(r["close"]), 2),
                "raw_volume": int(r["volume"]),
                "signed_volume": int(r["signed_volume"]),
                "cumulative_volume": cv,
                "spot_price": round(float(r["spot_price"]), 2),
                "highlight": highlight,
            })

        return {
            "symbol": self.futures_instrument,
            "spot_instrument": self.spot_instrument,
            "spot_price": round(self._spot_price, 2),
            "trend_bias": self._trend_bias,
            "threshold": self.threshold,
            "last_cumulative_volume": last_cv,
            "candle_count": len(rows),
            "is_demo": self._is_demo,
            "data_date": self._data_date.strftime("%Y-%m-%d"),
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows,
        }

    def _empty_result(self) -> dict:
        now = datetime.now()
        return {
            "symbol": self.futures_instrument,
            "spot_instrument": self.spot_instrument,
            "spot_price": round(self._spot_price, 2),
            "trend_bias": "Neutral",
            "threshold": self.threshold,
            "last_cumulative_volume": 0,
            "candle_count": 0,
            "is_demo": self._is_demo,
            "data_date": self._data_date.strftime("%Y-%m-%d"),
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
            "rows": [],
        }

    # ── BaseStrategy lifecycle (this strategy doesn't trade) ──

    def on_tick(self, tick_data: dict):
        """Not used — data is fetched on-demand via compute()."""
        pass
