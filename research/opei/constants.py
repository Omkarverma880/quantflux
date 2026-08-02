"""
Constants for the Option Premium Entry Intelligence Engine (OPEI).

Research-only, decision-support engine. It NEVER places orders. It continuously
scores the probability of an *explosive premium expansion* for the selected CE
and PE, and publishes the highest-probability premium entry levels.
"""
from __future__ import annotations

RESEARCH_ID = "opei"
RESEARCH_LABEL = "Option Premium Entry Intelligence Engine"

INDEX_NAME = "NIFTY"
INDEX_SPOT_TRADINGSYMBOL = "NIFTY 50"
VIX_TRADINGSYMBOL = "INDIA VIX"

# ── Strike selection (offset from ATM, in points; +ITM for CE = below spot) ──
# label → signed offset applied to the ATM strike. For a CALL, ITM strikes are
# BELOW spot (negative offset); for a PUT, ITM strikes are ABOVE spot. The
# service resolves the correct strike per side from this magnitude + direction.
STRIKE_OFFSETS: dict[str, int] = {
    "ATM": 0,
    "100 ITM": 100, "200 ITM": 200, "300 ITM": 300,
    "100 OTM": -100, "200 OTM": -200, "300 OTM": -300,
}
DEFAULT_STRIKE = "200 ITM"

# ── Confluence categories (each scored 0–100; combined by configurable weights) ──
CATEGORIES = [
    "trend", "momentum", "vwap", "volume", "oi",
    "volatility", "liquidity", "breadth", "premium_structure", "time",
]
CATEGORY_LABELS = {
    "trend": "Trend / Price Action", "momentum": "Momentum", "vwap": "VWAP",
    "volume": "Volume", "oi": "Open Interest", "volatility": "Volatility",
    "liquidity": "Liquidity / Order Flow", "breadth": "Market Breadth",
    "premium_structure": "Premium Structure", "time": "Time / Session",
}

# Default (non-equal) weights — fully configurable from the UI. They express how
# much each confluence bucket contributes to premium-expansion probability.
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 16, "momentum": 14, "premium_structure": 14, "volume": 12,
    "oi": 12, "vwap": 10, "volatility": 8, "breadth": 6, "liquidity": 5, "time": 3,
}

# ── Score bands ──
BANDS = [
    (95, "Institutional Grade", "#22c55e"),
    (90, "Excellent", "#4ade80"),
    (80, "Very Strong", "#a3e635"),
    (70, "Good", "#eab308"),
    (0, "Ignore", "#6b7280"),
]
INSTITUTIONAL_THRESHOLD = 95

# ── Heatmap colours ──
def band_color(score: float) -> str:
    for lo, _label, color in BANDS:
        if score >= lo:
            return color
    return "#6b7280"


def band_label(score: float) -> str:
    for lo, label, _c in BANDS:
        if score >= lo:
            return label
    return "Ignore"
