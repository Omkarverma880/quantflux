"""Research #13 — Quantflux Momentum & Market Replay Engine (QMRE). Paper-only."""
from research.qmre.service import QMREService
from research.qmre.positions import QMREPaperManager

__all__ = ["QMREService", "QMREPaperManager"]
