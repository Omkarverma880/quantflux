"""
NIFTY Signal Generator — Research module.

Analyses the option chain around the ATM strike and the NIFTY VWAP to produce
two independent, research-only market signals per completed candle:

  1. Option-Chain Signal  (PCR of summed Put/Call OI around ATM)
  2. VWAP Signal          (NIFTY LTP vs session VWAP)

Strictly read-only: it never places orders or touches live strategy state. It
reuses the existing Broker, Option-Chain, historical-candle and configuration
services — no duplicate market connections. Business logic (``calculations``,
``service``) is fully separated from the React UI.
"""
from research.nifty_signal_generator.service import NiftySignalGenerator

__all__ = ["NiftySignalGenerator"]
