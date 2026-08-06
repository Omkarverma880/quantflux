"""
Constants for Research-10 — Quantum Market Intelligence Engine (QMIE).

QMIE is a READ-ONLY research / opportunity-ranking module. It never places,
modifies, cancels, or simulates an order, and never touches any order API. It
consumes only the existing read-only Broker data methods (historical bars,
quotes, instrument dump) and publishes ranked research candidates with evidence,
risk grades and provenance.

This file holds the versioned, configurable defaults (horizon profiles, gate
thresholds, component weights, score/risk bands). Nothing here can cause an
order — there are no execution constants of any kind.
"""
from __future__ import annotations

RESEARCH_ID = "qmie"
RESEARCH_LABEL = "Quantum Market Intelligence Engine"
RULESET_VERSION = "qmie-ruleset-1.0.0"
SCHEMA_VERSION = "1.0.0"

BENCHMARK_SYMBOL = "NIFTY 50"        # NSE index used for relative strength

# ── Horizon profiles (versioned; see §40.5 / §10.4 of the blueprint) ──────────
# interval      : Zerodha historical interval
# history_days  : calendar days of history to request (warm-up, not the test set)
# min_bars      : minimum completed bars required, else the candidate is Unavailable
# atr_period    : ATR lookback
# lookback      : window for slope / relative-strength / structure
# min_rr        : minimum first-target reward-to-risk to be eligible-ranked
# target_atr    : first-target distance in ATR multiples (research hypothesis)
# stop_atr      : invalidation distance in ATR multiples (research hypothesis)
# stale_days    : a daily bar older than this many sessions is stale
HORIZONS: dict[str, dict] = {
    "intraday":   {"interval": "15minute", "history_days": 12,  "min_bars": 60,
                   "atr_period": 14, "lookback": 40,  "min_rr": 1.25, "target_atr": 1.5, "stop_atr": 1.0, "stale_days": 0},
    "swing":      {"interval": "day",      "history_days": 220, "min_bars": 120,
                   "atr_period": 14, "lookback": 40,  "min_rr": 1.75, "target_atr": 2.5, "stop_atr": 1.2, "stale_days": 4},
    "positional": {"interval": "day",      "history_days": 420, "min_bars": 200,
                   "atr_period": 21, "lookback": 80,  "min_rr": 2.00, "target_atr": 3.5, "stop_atr": 1.5, "stale_days": 5},
    "monthly":    {"interval": "day",      "history_days": 800, "min_bars": 300,
                   "atr_period": 21, "lookback": 160, "min_rr": 2.25, "target_atr": 5.0, "stop_atr": 2.0, "stale_days": 7},
}
DEFAULT_HORIZON = "swing"

# ── Confluence components (each normalized 0–100) and non-equal default weights.
COMPONENTS = ["trend", "relative_strength", "volume", "volatility", "liquidity"]
COMPONENT_LABELS = {
    "trend": "Trend / Structure", "relative_strength": "Relative Strength",
    "volume": "Volume / Participation", "volatility": "Volatility Fit", "liquidity": "Liquidity",
}
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 30, "relative_strength": 28, "volume": 16, "volatility": 14, "liquidity": 12,
}

# ── Liquidity: absolute floor on median daily traded value (₹), horizon-scaled.
LIQUIDITY_FLOOR = {"intraday": 200_000_000, "swing": 50_000_000,
                   "positional": 30_000_000, "monthly": 20_000_000}

# ── Score → opportunity band (display only) ──
SCORE_BANDS = [
    (85, "Exceptional", "#22c55e"), (75, "Strong", "#4ade80"),
    (65, "Constructive", "#a3e635"), (50, "Developing", "#eab308"),
    (0, "Weak", "#6b7280"),
]

# ── Analytical risk grades (indicative research risk, NOT execution risk) ──
RISK_GRADES = ["Low", "Moderate", "High", "Severe"]
RISK_COLORS = {"Low": "#22c55e", "Moderate": "#eab308", "High": "#f97316", "Severe": "#ef4444"}

# ── Candidate lifecycle states (§40.3) ──
STATE_ELIGIBLE = "eligible"
STATE_WARNING = "eligible_warning"
STATE_RESTRICTED = "restricted"
STATE_UNAVAILABLE = "unavailable"

DISCLAIMER = "QMIE research only — no order is created, transmitted, or executed."


def score_band(score: float):
    for lo, label, color in SCORE_BANDS:
        if score >= lo:
            return label, color
    return "Weak", "#6b7280"
