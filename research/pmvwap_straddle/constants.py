"""
Constants for the Previous-Month-VWAP Straddle Research module.

Research-only: it simulates buying an ATM CE + PE straddle when price crosses
the Previous-Month VWAP from below. No orders are ever placed.
"""
from __future__ import annotations

from datetime import time as dtime

RESEARCH_ID = "pmvwap_straddle"
RESEARCH_LABEL = "Previous Month VWAP Straddle"

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# Underlying candle timeframe → Zerodha historical interval.
TIMEFRAME_MAP: dict[str, str] = {
    "1m": "minute", "3m": "3minute", "5m": "5minute", "10m": "10minute",
    "15m": "15minute", "30m": "30minute", "1h": "60minute", "1d": "day",
}
DEFAULT_TIMEFRAME = "15m"

# Index underlyings carry F&O too — this module is equities-only, so skip them.
INDEX_EXCLUDE = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "SENSEX", "BANKEX", "SENSEX50",
}

# Trade lifecycle states (live semantics; a completed backtest ends FULL_EXIT).
STATE_OPEN = "OPEN"
STATE_HALF = "HALF EXIT"
STATE_FULL = "FULL EXIT"

# Per-leg exit reasons.
EXIT_TARGET = "TARGET"
EXIT_SQUAREOFF = "SQUAREOFF"
EXIT_OPEN = "OPEN"        # still running (live)

DIRECTION_LONG = "LONG"   # crossed Prev-Month VWAP from below
