"""
Flux Strategy Test Lab — one frozen strategy: the failed opening-range breakout iron fly.

    strategy.scan(closes)   ← the only place the rule lives
        ↑                        ↑
    backtest.run()          live.PaperEngine.check()
    (stored history)        (live data, PAPER only — no order path)
"""
