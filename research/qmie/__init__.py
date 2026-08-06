"""
Research-10 — Quantum Market Intelligence Engine (QMIE).

An additive, READ-ONLY market-research / opportunity-ranking module. It ranks
research candidates across intraday / swing / positional / monthly horizons with
evidence, analytical risk, and provenance. It never places, modifies, cancels,
or simulates an order, and imports no order/execution client of any kind.
"""
from research.qmie.service import QMIEEngine

__all__ = ["QMIEEngine"]
