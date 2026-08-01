"""
Previous-Month-VWAP Equity-Holding Research module.

Research-only cash-equity engine: buys the equity as a holding when price meets
the Previous-Month VWAP with the Previous-Week VWAP above it (the "green above
purple" setup), then tracks the holding to a target / stop / max-hold exit. It
never places orders — it only logs signals — and reuses the existing Broker,
the F&O universe, and the shared Prev-Period VWAP engine.
"""
from research.pmvwap_equity.service import PMVwapEquityResearch

__all__ = ["PMVwapEquityResearch"]
