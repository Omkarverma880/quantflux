"""
Constants for the Previous-Month-VWAP Equity-Holding Research module.

Research-only: it simulates BUYING the equity as a holding when price touches
the Previous-Month VWAP (purple) AND the Previous-Week VWAP (green) is above it
— exactly the "green line above purple line" setup from the reference chart.
No options, no orders — signals are only logged.
"""
from __future__ import annotations

RESEARCH_ID = "pmvwap_equity"
RESEARCH_LABEL = "Previous Month VWAP Equity Holding"

# Reuse the straddle module's shared timings / timeframe map (no duplication).
from research.pmvwap_straddle.constants import (   # noqa: E402,F401
    MARKET_OPEN, MARKET_CLOSE, TIMEFRAME_MAP, DEFAULT_TIMEFRAME, INDEX_EXCLUDE,
)

# Entry style.
ENTRY_CROSS_UP = "cross_up"     # prev close below, current crosses up (reversal)
ENTRY_TOUCH = "touch"           # candle range straddles the level (a touch)

# Holding exit reasons.
EXIT_TARGET = "TARGET"
EXIT_STOP = "STOP"
EXIT_MAXHOLD = "MAXHOLD"
EXIT_END = "END"                # ran out of data (still effectively open)

STATE_OPEN = "OPEN"
STATE_CLOSED = "CLOSED"

DIRECTION_LONG = "LONG"
