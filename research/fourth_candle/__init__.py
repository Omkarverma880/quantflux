"""
4th-Candle Strategy (Equity Strategy #2) — research/backtest/simulate engine.

Read-only backtest + simulate paths that never place orders. The live/paper
order-placing strategy lives in ``strategies/fourth_candle_strategy.py`` and
reuses this engine's pure logic.
"""
from research.fourth_candle.service import FourthCandleResearch

__all__ = ["FourthCandleResearch"]
