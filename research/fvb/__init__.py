"""
NIFTY_FUTURES_VOLUME_BREAKOUT and FALSE_BREAKOUT_FIREWALL.

    data.py        what exists, what is missing, and what must never be substituted
    structure.py   levels, buffers, and the relative-volume engine
    signals.py     the breakout/retest engine and every reason it declines
    firewall.py    the independent false-breakout layer
    execution.py   contract selection, sizing, the two separate stops, costs and slippage
    backtest.py    the chronological engine and the A-E variant comparison

Futures participation is unavailable until spot/NIFTYFUT is ingested. Until then the volume
engine reports UNAVAILABLE rather than passing, and option volume is offered as a separate,
differently-named source.
"""
from research.fvb import backtest, data, execution, firewall, signals, structure  # noqa: F401
