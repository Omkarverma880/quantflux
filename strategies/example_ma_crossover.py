"""
Example strategy to demonstrate how to build a strategy.
This is a simple moving average crossover — replace with your own logic.

To use:
1. Uncomment the import + registration in strategies/registry.py
2. Add "example_ma_crossover" to ACTIVE_STRATEGIES in .env
"""
from collections import defaultdict
from strategies.base_strategy import BaseStrategy, StrategyConfig
from core.broker import Broker


class ExampleMACrossover(BaseStrategy):
    """
    Simple Moving Average Crossover (demonstration only).
    BUY when short MA crosses above long MA.
    SELL when short MA crosses below long MA.
    """

    def __init__(self, config: StrategyConfig, broker: Broker):
        super().__init__(config, broker)
        self.short_window = config.params.get("short_window", 5)
        self.long_window = config.params.get("long_window", 20)
        self._prices: dict[str, list[float]] = defaultdict(list)
        self._last_signal: dict[str, str] = {}

    def on_start(self):
        super().on_start()
        self.logger.info(
            f"MA Crossover: short={self.short_window}, long={self.long_window}"
        )

    def on_tick(self, tick_data: dict):
        for symbol, tick in tick_data.items():
            ltp = tick.get("last_price", 0)
            if ltp <= 0:
                continue

            self._prices[symbol].append(ltp)
            prices = self._prices[symbol]

            # Need enough data
            if len(prices) < self.long_window:
                continue

            short_ma = sum(prices[-self.short_window:]) / self.short_window
            long_ma = sum(prices[-self.long_window:]) / self.long_window

            prev_signal = self._last_signal.get(symbol)

            if short_ma > long_ma and prev_signal != "BUY":
                if not self.has_position(symbol):
                    # Extract bare symbol from "NSE:RELIANCE"
                    bare = symbol.split(":")[-1] if ":" in symbol else symbol
                    exchange = symbol.split(":")[0] if ":" in symbol else "NSE"
                    self.buy(bare, qty=1, exchange=exchange)
                self._last_signal[symbol] = "BUY"

            elif short_ma < long_ma and prev_signal != "SELL":
                if self.has_position(symbol):
                    bare = symbol.split(":")[-1] if ":" in symbol else symbol
                    exchange = symbol.split(":")[0] if ":" in symbol else "NSE"
                    self.sell(bare, qty=1, exchange=exchange)
                self._last_signal[symbol] = "SELL"

            # Trim price history
            if len(prices) > self.long_window * 3:
                self._prices[symbol] = prices[-self.long_window * 2:]

    def on_stop(self):
        self.close_all_positions()
        super().on_stop()
