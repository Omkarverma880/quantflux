"""
Quantflux Price Action Signals.

    strategy.py   the Pine indicator, translated line for line
    live.py       today's signals from the broker, with the option each one implies
    backtest.py   the same rules over stored history, priced on real option candles

No volume filter and no futures dependency: the rules use swing levels, an EMA and VWAP, all of
which exist in the data we already hold.
"""
from research.pas import strategy  # noqa: F401
