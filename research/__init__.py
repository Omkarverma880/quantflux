"""
Research package — backtest / analytics modules that are strictly
read-only with respect to live trading.

Modules here never place orders, never touch live strategy state, and never
modify credentials. They reuse the existing Broker (Zerodha Kite) wrapper for
historical data only.
"""
