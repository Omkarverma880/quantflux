"""
Abstract base class for all trading strategies.
Every strategy you create must inherit from BaseStrategy.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.broker import Broker, OrderRequest, OrderResponse
from core.logger import get_logger


@dataclass
class StrategyConfig:
    """Configuration passed to every strategy instance."""
    name: str
    instruments: list[str]                  # e.g. ["NSE:RELIANCE", "NSE:NIFTY 50"]
    capital: float = 100000.0               # capital allocated to this strategy
    max_positions: int = 5
    enabled: bool = True
    params: dict = field(default_factory=dict)  # strategy-specific parameters


class BaseStrategy(ABC):
    """
    Lifecycle:
        1. __init__  → called once
        2. on_start  → called when engine starts this strategy
        3. on_tick / on_candle → called on every data update
        4. on_stop   → called at EOD or manual stop

    Subclass must implement:
        - on_tick()  or  on_candle()
        - Should call self.buy() / self.sell() to place orders
    """

    def __init__(self, config: StrategyConfig, broker: Broker):
        self.config = config
        self.broker = broker
        self.logger = get_logger(f"strategy.{config.name}")
        self._active_orders: list[OrderResponse] = []
        self._positions: dict[str, int] = {}   # symbol → net qty
        self._pnl: float = 0.0
        self._trade_count: int = 0
        self._is_running: bool = False

    # ── Lifecycle hooks ────────────────────────────────

    def on_start(self):
        """Called once when the strategy starts. Override for setup."""
        self.logger.info(f"Strategy [{self.config.name}] started.")
        self._is_running = True

    @abstractmethod
    def on_tick(self, tick_data: dict):
        """
        Called on every tick update.
        tick_data format: {instrument_token: {last_price, volume, ...}}
        """
        ...

    def on_candle(self, symbol: str, candle: dict):
        """
        Optional: called when a candle closes.
        candle: {open, high, low, close, volume, timestamp}
        Override if your strategy is candle-based.
        """
        pass

    def on_stop(self):
        """Called at end of day or manual stop. Override for cleanup."""
        self.logger.info(
            f"Strategy [{self.config.name}] stopped. "
            f"Trades: {self._trade_count} | PnL: {self._pnl:.2f}"
        )
        self._is_running = False

    def on_order_update(self, order: dict):
        """Called when an order status changes."""
        pass

    # ── Trading helpers ────────────────────────────────

    def buy(
        self,
        symbol: str,
        qty: int,
        exchange: str = "NSE",
        order_type: str = "MARKET",
        price: float = 0.0,
        product: str = "MIS",
        tag: str = "",
    ) -> Optional[OrderResponse]:
        """Place a buy order."""
        from core.broker import OrderSide, OrderType, ProductType, Exchange as Ex

        req = OrderRequest(
            tradingsymbol=symbol,
            exchange=Ex(exchange),
            side=OrderSide.BUY,
            quantity=qty,
            order_type=OrderType(order_type),
            product=ProductType(product),
            price=price,
            tag=tag or self.config.name[:20],
        )
        resp = self.broker.place_order(req)
        self._active_orders.append(resp)
        self._positions[symbol] = self._positions.get(symbol, 0) + qty
        self._trade_count += 1
        return resp

    def sell(
        self,
        symbol: str,
        qty: int,
        exchange: str = "NSE",
        order_type: str = "MARKET",
        price: float = 0.0,
        product: str = "MIS",
        tag: str = "",
    ) -> Optional[OrderResponse]:
        """Place a sell order."""
        from core.broker import OrderSide, OrderType, ProductType, Exchange as Ex

        req = OrderRequest(
            tradingsymbol=symbol,
            exchange=Ex(exchange),
            side=OrderSide.SELL,
            quantity=qty,
            order_type=OrderType(order_type),
            product=ProductType(product),
            price=price,
            tag=tag or self.config.name[:20],
        )
        resp = self.broker.place_order(req)
        self._active_orders.append(resp)
        self._positions[symbol] = self._positions.get(symbol, 0) - qty
        self._trade_count += 1
        return resp

    # ── Position helpers ───────────────────────────────

    def get_position(self, symbol: str) -> int:
        """Net quantity held for a symbol."""
        return self._positions.get(symbol, 0)

    def has_position(self, symbol: str) -> bool:
        return self._positions.get(symbol, 0) != 0

    def close_all_positions(self):
        """Market-close all open positions."""
        for symbol, qty in list(self._positions.items()):
            if qty > 0:
                self.sell(symbol, qty)
            elif qty < 0:
                self.buy(symbol, abs(qty))
        self.logger.info("All positions closed.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def trade_count(self) -> int:
        return self._trade_count

    @property
    def net_pnl(self) -> float:
        return self._pnl

    def __repr__(self):
        return f"<Strategy: {self.config.name} | running={self._is_running}>"
