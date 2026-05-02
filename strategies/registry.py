"""
Strategy registry — auto-discovers and registers all strategies.
To add a new strategy:
  1. Create a file in strategies/ (e.g. my_strategy.py)
  2. Define a class inheriting BaseStrategy
  3. Add it to STRATEGY_MAP below
"""
from strategies.base_strategy import BaseStrategy
from strategies.cumulative_volume import CumulativeVolumeStrategy
from strategies.strategy1_gann_cv import Strategy1GannCV
from strategies.strategy2_option_sell import Strategy2OptionSell
from strategies.strategy3_cv_vwap_ema_adx import Strategy3CvVwapEmaAdx
from strategies.strategy4_high_low_retest import Strategy4HighLowRetest

# ──────────────── Strategy Registry ────────────────
# Map strategy name → class
# Add your strategies here as you build them.
STRATEGY_MAP: dict[str, type] = {
    "strategy1_gann_cv": Strategy1GannCV,
    "strategy2_option_sell": Strategy2OptionSell,
    "strategy3_cv_vwap_ema_adx": Strategy3CvVwapEmaAdx,
    "strategy4_high_low_retest": Strategy4HighLowRetest,
}


def get_strategy_class(name: str) -> type[BaseStrategy]:
    """Retrieve a strategy class by name."""
    if name not in STRATEGY_MAP:
        available = ", ".join(STRATEGY_MAP.keys()) or "(none registered)"
        raise ValueError(
            f"Strategy '{name}' not found. Available: {available}"
        )
    return STRATEGY_MAP[name]


def list_strategies() -> list[str]:
    return list(STRATEGY_MAP.keys())
