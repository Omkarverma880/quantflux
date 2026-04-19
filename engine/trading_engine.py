"""
Strategy Engine — the heart of the trading system.
Manages strategy lifecycle, tick distribution, and scheduling.
"""
import time
import threading
from datetime import datetime, time as dtime
from typing import Optional

from kiteconnect import KiteTicker

from config import settings
from core.broker import get_broker, Broker
from core.auth import get_kite
from core.risk_manager import get_risk_manager, RiskManager
from core.logger import get_logger
from strategies.base_strategy import BaseStrategy, StrategyConfig
from strategies.registry import get_strategy_class

logger = get_logger("engine")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_CLOSE_EXIT = dtime(15, 15)  # auto square-off time


class TradingEngine:
    """
    Orchestrates everything:
    - Authenticates with Zerodha
    - Instantiates active strategies
    - Subscribes to ticks via WebSocket
    - Routes ticks to strategies
    - Handles EOD square-off
    """

    def __init__(self):
        self.broker: Broker = get_broker()
        self.risk_manager: RiskManager = get_risk_manager()
        self._strategies: list[BaseStrategy] = []
        self._instrument_tokens: dict[int, str] = {}  # token → "EXCHANGE:SYMBOL"
        self._ticker: Optional[KiteTicker] = None
        self._running = False

    # ── Setup ──────────────────────────────────────────

    def load_strategies(self, strategy_configs: list[dict]):
        """
        Load strategies from config dicts.
        Each dict: {"name": "...", "instruments": [...], "capital": ..., "params": {...}}
        """
        for cfg_dict in strategy_configs:
            name = cfg_dict["name"]
            try:
                cls = get_strategy_class(name)
                config = StrategyConfig(
                    name=name,
                    instruments=cfg_dict.get("instruments", []),
                    capital=cfg_dict.get("capital", 100000),
                    max_positions=cfg_dict.get("max_positions", 5),
                    params=cfg_dict.get("params", {}),
                )
                strategy = cls(config=config, broker=self.broker)
                self._strategies.append(strategy)
                logger.info(f"Loaded strategy: {name}")
            except ValueError as e:
                logger.error(str(e))

    def _resolve_instrument_tokens(self):
        """Map instrument symbols to tokens for tick subscription."""
        all_instruments = set()
        for s in self._strategies:
            all_instruments.update(s.config.instruments)

        if not all_instruments:
            logger.warning("No instruments to subscribe.")
            return

        # Get instruments list from NSE + NFO
        nse_instruments = self.broker.get_instruments("NSE")
        nfo_instruments = self.broker.get_instruments("NFO")
        all_inst_list = nse_instruments + nfo_instruments

        symbol_to_token = {}
        for inst in all_inst_list:
            key = f"{inst['exchange']}:{inst['tradingsymbol']}"
            symbol_to_token[key] = inst["instrument_token"]

        for sym in all_instruments:
            if sym in symbol_to_token:
                token = symbol_to_token[sym]
                self._instrument_tokens[token] = sym
            else:
                logger.warning(f"Could not find instrument token for: {sym}")

        logger.info(f"Resolved {len(self._instrument_tokens)} instrument tokens.")

    # ── WebSocket Tick Handling ────────────────────────

    def _start_ticker(self):
        """Start KiteTicker WebSocket for live ticks."""
        kite = get_kite()
        self._ticker = KiteTicker(settings.KITE_API_KEY, kite.access_token)

        tokens = list(self._instrument_tokens.keys())

        def on_ticks(ws, ticks):
            self._distribute_ticks(ticks)

        def on_connect(ws, response):
            logger.info("Ticker connected. Subscribing to instruments.")
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)

        def on_close(ws, code, reason):
            logger.warning(f"Ticker closed: {code} - {reason}")

        def on_error(ws, code, reason):
            logger.error(f"Ticker error: {code} - {reason}")

        self._ticker.on_ticks = on_ticks
        self._ticker.on_connect = on_connect
        self._ticker.on_close = on_close
        self._ticker.on_error = on_error

        self._ticker.connect(threaded=True)

    def _distribute_ticks(self, ticks: list):
        """Route tick data to all active strategies."""
        if not self.risk_manager.is_trading_allowed:
            return

        now = datetime.now().time()

        # Auto square-off before market close
        if now >= PRE_CLOSE_EXIT:
            self._auto_square_off()
            return

        # Only trade during market hours
        if not (MARKET_OPEN <= now <= MARKET_CLOSE):
            return

        tick_dict = {}
        for tick in ticks:
            token = tick.get("instrument_token")
            if token in self._instrument_tokens:
                tick_dict[self._instrument_tokens[token]] = tick

        for strategy in self._strategies:
            if strategy.is_running:
                try:
                    strategy.on_tick(tick_dict)
                except Exception as e:
                    logger.error(
                        f"Error in strategy {strategy.config.name}: {e}",
                        exc_info=True,
                    )

    # ── Engine Controls ────────────────────────────────

    def start(self):
        """Start the trading engine."""
        logger.info("=" * 60)
        logger.info("TRADING ENGINE STARTING")
        logger.info(f"Paper Trade: {settings.PAPER_TRADE}")
        logger.info(f"Strategies loaded: {len(self._strategies)}")
        logger.info("=" * 60)

        self.broker.connect()

        for strategy in self._strategies:
            strategy.on_start()

        self._resolve_instrument_tokens()

        if self._instrument_tokens:
            self._start_ticker()

        self._running = True
        logger.info("Engine is running. Press Ctrl+C to stop.")

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Gracefully stop the engine."""
        logger.info("Stopping trading engine …")
        self._running = False

        for strategy in self._strategies:
            try:
                strategy.on_stop()
            except Exception as e:
                logger.error(f"Error stopping {strategy.config.name}: {e}")

        if self._ticker:
            self._ticker.close()

        logger.info("Trading engine stopped.")

    def _auto_square_off(self):
        """Close all positions across all strategies at EOD."""
        if not self._running:
            return

        logger.warning("AUTO SQUARE-OFF triggered (pre-market-close).")
        for strategy in self._strategies:
            try:
                strategy.close_all_positions()
            except Exception as e:
                logger.error(f"Square-off error in {strategy.config.name}: {e}")

    # ── Status ─────────────────────────────────────────

    def status(self) -> dict:
        return {
            "running": self._running,
            "paper_trade": settings.PAPER_TRADE,
            "strategies": [
                {
                    "name": s.config.name,
                    "running": s.is_running,
                    "trades": s.trade_count,
                    "pnl": s.net_pnl,
                }
                for s in self._strategies
            ],
            "risk": {
                "daily_pnl": self.risk_manager.daily_pnl,
                "trade_count": self.risk_manager.daily_trade_count,
                "trading_allowed": self.risk_manager.is_trading_allowed,
            },
        }
