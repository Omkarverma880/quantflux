"""
Flux Strategy Test Lab — the combined hammer strategy.

    patterns.py   both TradingView definitions, translated line by line
    engine.py     signals on NIFTY candles, trades taken in the option chain
    coverage.py   what the stored history covers (cheap, cached)
    service.py    background jobs and saved runs
    live.py       the background loop's hook — backtest-only, no order path

Index points and option rupees are reported separately and never summed.
"""
