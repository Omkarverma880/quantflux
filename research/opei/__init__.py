"""
Option Premium Entry Intelligence Engine (OPEI) — research module.

A read-only, decision-support engine. It does NOT generate BUY/SELL signals and
NEVER places orders. It continuously scores the probability of an explosive
premium expansion for the selected CE and PE using live Zerodha data, and
publishes the highest-probability premium entry levels with a weighted
confluence score, per-category breakdown and Telegram alerts.
"""
from research.opei.service import OPEIEngine

__all__ = ["OPEIEngine"]
