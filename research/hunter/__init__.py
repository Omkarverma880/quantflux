"""
Hunter — the stock setup scanner (Equity Strategies → Hunter).

    universe.py  the NIFTY 500 and its industries
    data.py      daily bars for all of them (reuses the My Equity daily cache)
    patterns.py  what a base, a breakout and each stage mean — the only place the rules live
    scan.py      one pass over the universe: the stage board and what changed since last close
    store.py     one snapshot per scan date, so the diff is real
    service.py   jobs, settings, filtering, and the automatic scan after the close

Screener only: it finds and tracks setups. It never places an order.
"""
