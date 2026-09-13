"""
Index Straddle Engine — backtest and research (read-only).

Short or long straddle / strangle on an index, intraday only, selected by
distance to expiry. The short configuration is the one the research supports;
the long configuration is included because it is the natural counterpart and
because the data says clearly that it loses — both are here so the claim can be
re-checked rather than taken on trust.
"""
from research.index_straddle.config import Config, Costs, load_config, save_config
from research.index_straddle.service import run, parity, RESEARCH_REFERENCE

__all__ = ["Config", "Costs", "load_config", "save_config", "run", "parity",
           "RESEARCH_REFERENCE"]
