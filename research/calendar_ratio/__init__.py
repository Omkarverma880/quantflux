"""
Weekly/monthly calendar ratio spread.

    structure.py  the six legs, chosen by premium, and the expiry pair they need
    live.py       today's structure from the broker, for NIFTY or SENSEX
    backtest.py   the same structure over stored history, on real option prices

Sells two near-dated options and buys three far-dated ones on each side. Premium in and
premium out are close by design: the position bets on the near expiry decaying faster than
the far one, so it only means anything while the two expiries differ.
"""
from research.calendar_ratio import structure  # noqa: F401
