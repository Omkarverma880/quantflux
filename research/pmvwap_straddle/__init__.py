"""
Previous-Month-VWAP Straddle Research module.

Research-only options engine: when price crosses the Previous-Month VWAP from
below, it simulates buying an ATM CE + PE straddle and exits each leg
independently at a configurable combined-premium target. It never places
orders — it only logs signals — and reuses the existing Broker, instrument
dump and shared Prev-Period VWAP engine.
"""
from research.pmvwap_straddle.service import PMVwapStraddleResearch

__all__ = ["PMVwapStraddleResearch"]
