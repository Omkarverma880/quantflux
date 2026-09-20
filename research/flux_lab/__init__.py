"""
Flux Strategy Test Lab — one signal engine, three modes.

    rules.evaluate(bar, strategy)
        ↑                    ↑                    ↑
    engine.run()        live.replay_trace()   live.PaperEngine.check()
    (history)           (step through)        (live, paper only)

The lab measures NIFTY option-buying ideas against the history Quantflux already stores, then
lets the identical logic run forward bar by bar and, finally, on live data as paper trades. No
part of it places an order.
"""
from research.flux_lab import (  # noqa: F401
    data, engine, features, live, metrics, research, rules, service,
)
