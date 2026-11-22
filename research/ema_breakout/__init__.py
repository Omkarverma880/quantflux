"""
20 EMA Single Candle Breakout — long and short.

    strategy.py   the Pine state machine, block for block
    backtest.py   the same rules over stored history, priced on real option candles
    live.py       the paper/live desk, with the app's three order gates

A close through the EMA arms a setup; the first counter-coloured candle inside the next two
candles sets the trigger at its high (long) or low (short); entry is a close through that
trigger; exit is a close back across the EMA. The index decides, and the trade is taken in
the option chain — buying or selling, at a strike you choose.
"""
from research.ema_breakout import strategy  # noqa: F401
