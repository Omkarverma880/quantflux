"""
Index Straddle Engine — configuration.

Every variable of the strategy lives here, so a parameter sweep is a dict and
never an edit. The defaults below are the exact configuration that produced the
research result, so a fresh backtest with an untouched config reproduces it:

    SHORT ATM straddle · 0–1 DTE only · enter 10:00 · exit 15:20
    stop −35% of credit · skip the day after a prior-day range above 1.3σ
    3 lots × 65 · premium from the calibrated research model

Changing any default changes the numbers. That is intended — but it means the
"does my backtest match the research" check must be run on the defaults.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, replace
from typing import Optional

from config import settings
from core.logger import get_logger

logger = get_logger("research.index_straddle.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "index_straddle.json"
RESULTS_DIR = settings.DATA_DIR / "backtest_results"

# ── research defaults: the configuration that produced the documented result ──
DIRECTION = "short"           # short = sell the straddle, long = buy it
STRUCTURE = "straddle"        # straddle (one strike) | strangle (two wings)
ENTRY_TIME = "10:00"
EXIT_TIME = "15:20"
STOP_PCT = 35.0               # % of the credit (short) or debit (long)
TARGET_PCT = 0.0              # 0 = no target, time exit only
DTE_MIN, DTE_MAX = 0, 1       # expiry day and the session before it
SKIP_PRIOR_RANGE_SIGMA = 1.3  # 0 disables the filter
LOT_SIZE = 65
LOTS = 3
STARTING_CAPITAL = 570_000.0  # ~1.9L margin per short-straddle lot

# friction measured off the real 1-minute option chain
HALF_SPREAD_PCT = 0.8         # per side, % of premium
BROKERAGE_PCT = 0.4           # round trip, % of premium

DIRECTIONS = ("short", "long")
STRUCTURES = ("straddle", "strangle")
STRIKE_MODES = ("atm_offset", "premium")
PREMIUM_SOURCES = ("model", "broker")


@dataclass
class Costs:
    """Friction, as measured: 0.8% of premium per side + 0.4% round trip."""
    half_spread_pct: float = HALF_SPREAD_PCT
    brokerage_pct: float = BROKERAGE_PCT

    @property
    def any_on(self) -> bool:
        return bool(self.half_spread_pct or self.brokerage_pct)


@dataclass
class Config:
    # ── what we trade ──
    direction: str = DIRECTION            # short | long
    structure: str = STRUCTURE            # straddle | strangle
    wing_offset: int = 100                # strangle only: points either side of ATM

    # ── strike selection ──
    strike_mode: str = "atm_offset"       # atm_offset | premium
    # Signed distance from ATM in points: negative = ITM, 0 = ATM, positive = OTM.
    # Resolved per option type, so −200 is 200 ITM for a CE and for a PE alike.
    strike_offset: int = 0
    target_premium: float = 100.0         # strike_mode == premium: pick the strike
                                          # whose premium is nearest this value

    # ── session ──
    entry_time: str = ENTRY_TIME
    exit_time: str = EXIT_TIME

    # ── exits ──
    stop_pct: float = STOP_PCT            # % adverse move on the combined premium
    target_pct: float = TARGET_PCT        # 0 = none
    stop_on: str = "combined"             # combined | per_leg

    # ── day selection (this is where the edge lives) ──
    dte_min: int = DTE_MIN
    dte_max: int = DTE_MAX
    skip_prior_range_sigma: float = SKIP_PRIOR_RANGE_SIGMA   # 0 = off
    skip_gap_sigma: float = 0.0                              # 0 = off
    skip_open_range_sigma: float = 0.0                       # 0 = off
    weekdays: list = field(default_factory=list)             # [] = every weekday

    # ── size & capital ──
    lot_size: int = LOT_SIZE
    lots: int = LOTS
    starting_capital: float = STARTING_CAPITAL

    # ── pricing ──
    premium_source: str = "model"         # model = calibrated research pricer
                                          # broker = the contract's own candles
    vrp: float = 1.30                     # implied / realized vol, model source only
    costs: Costs = field(default_factory=Costs)

    # ── live control ──
    paper_trade: bool = True              # real orders need this OFF and the gate on
    auto_start: bool = False
    telegram_alerts: bool = False
    telegram_bot: str = "a"

    # ── data ──
    csv_path: str = ""                    # blank → pull from the broker
    symbol: str = "NIFTY 50"
    index: str = "NIFTY"
    expiry_type: str = "weekly"
    start_date: str = ""
    end_date: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Config":
        d = dict(d or {})
        costs = d.pop("costs", None)
        cfg = cls()
        for k, v in d.items():
            if hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)
        if isinstance(costs, dict):
            cfg.costs = Costs(**{k: float(v) for k, v in costs.items()
                                 if hasattr(Costs, k)})
        return cfg.sanitized()

    def sanitized(self) -> "Config":
        c = replace(self)
        c.direction = c.direction if c.direction in DIRECTIONS else "short"
        c.structure = c.structure if c.structure in STRUCTURES else "straddle"
        c.strike_mode = c.strike_mode if c.strike_mode in STRIKE_MODES else "atm_offset"
        c.premium_source = (c.premium_source if c.premium_source in PREMIUM_SOURCES
                            else "model")
        c.wing_offset = max(0, min(2000, int(c.wing_offset)))
        c.strike_offset = max(-2000, min(2000, int(c.strike_offset)))
        c.target_premium = max(1.0, float(c.target_premium))
        c.stop_pct = max(1.0, min(500.0, float(c.stop_pct)))
        c.target_pct = max(0.0, min(500.0, float(c.target_pct)))
        c.stop_on = c.stop_on if c.stop_on in ("combined", "per_leg") else "combined"
        c.dte_min = max(0, min(30, int(c.dte_min)))
        c.dte_max = max(c.dte_min, min(30, int(c.dte_max)))
        c.skip_prior_range_sigma = max(0.0, float(c.skip_prior_range_sigma))
        c.skip_gap_sigma = max(0.0, float(c.skip_gap_sigma))
        c.skip_open_range_sigma = max(0.0, float(c.skip_open_range_sigma))
        c.weekdays = [int(w) for w in (c.weekdays or []) if 0 <= int(w) <= 6]
        c.lot_size = max(1, int(c.lot_size))
        c.lots = max(1, min(100, int(c.lots)))
        c.starting_capital = max(1.0, float(c.starting_capital))
        c.vrp = max(0.5, min(3.0, float(c.vrp)))
        c.expiry_type = c.expiry_type if c.expiry_type in ("weekly", "monthly") else "weekly"
        c.paper_trade = bool(c.paper_trade)
        c.auto_start = bool(c.auto_start)
        c.telegram_alerts = bool(c.telegram_alerts)
        c.telegram_bot = c.telegram_bot if c.telegram_bot in ("a", "b") else "a"
        return c

    # ── derived helpers ──
    @property
    def qty(self) -> int:
        """Quantity per leg."""
        return self.lot_size * self.lots

    @property
    def is_short(self) -> bool:
        return self.direction == "short"

    def legs(self, atm: float, step: int = 50) -> tuple[float, float]:
        """(call strike, put strike) for this configuration."""
        if self.structure == "strangle":
            return atm + self.wing_offset, atm - self.wing_offset
        # straddle: one strike, shifted by the signed offset per option type
        if not self.strike_offset:
            return atm, atm
        return atm + self.strike_offset, atm - self.strike_offset

    def describe(self) -> str:
        side = "SELL" if self.is_short else "BUY"
        if self.structure == "strangle":
            what = f"{self.wing_offset}-wide strangle"
        elif self.strike_mode == "premium":
            what = f"~Rs{self.target_premium:g}-premium straddle"
        elif self.strike_offset == 0:
            what = "ATM straddle"
        else:
            mny = "OTM" if self.strike_offset > 0 else "ITM"
            what = f"{abs(self.strike_offset)} {mny} straddle"
        return (f"{side} {what} · {self.lots} lot(s) × {self.lot_size} · "
                f"DTE {self.dte_min}–{self.dte_max} · {self.entry_time}→{self.exit_time} · "
                f"stop {self.stop_pct:.0f}%")


def load_config() -> Config:
    try:
        if CONFIG_FILE.exists():
            return Config.from_dict(json.loads(CONFIG_FILE.read_text()).get("config", {}))
    except Exception as exc:
        logger.debug("index_straddle config read failed: %s", exc)
    return Config()


def save_config(partial: dict) -> Config:
    cur = load_config().to_dict()
    costs = {**cur.get("costs", {}), **((partial or {}).get("costs") or {})}
    merged = {**cur, **(partial or {}), "costs": costs}
    cfg = Config.from_dict(merged)
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg.to_dict()}, indent=2, default=str))
    except Exception as exc:
        logger.error("index_straddle config save failed: %s", exc)
    return cfg


def research_defaults() -> Config:
    """The exact configuration behind the documented research result."""
    return Config()
