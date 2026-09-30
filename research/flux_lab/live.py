"""
Flux Lab — live hook.

The lab is a backtest today: the hammer strategy has not been forward-tested, so nothing here
watches the market or holds a position. The server's background loop calls ``ENGINE.check`` on a
timer, so this module keeps that entry point and does nothing with it.

**There is no order path here, and there never was.** When paper trading is added it will price
positions from quotes and write rows to ``flux_lab_trades`` with ``mode='PAPER'`` — never an order.
"""
from __future__ import annotations

from core.logger import get_logger

logger = get_logger("research.flux_lab.live")


class PaperEngine:
    """Placeholder for the background loop. Returns immediately, every time."""

    def check(self, db, user_id: int, broker) -> dict:
        return {"skipped": "the hammer strategy is backtest-only for now"}


ENGINE = PaperEngine()
