"""
NIFTY Opening-Price Mean-Reversion — configuration.

Every strategy variable lives here so a parameter sweep is a dict, not an edit.
Nothing in this package hard-codes a level, a time or a size.

    BUY  = Daily Open − ENTRY_OFFSET
    SELL = Daily Open + ENTRY_OFFSET
    SL   = STOP_LOSS points against the entry
    TP   = TARGET points in favour
    New entries only between ENTRY_START and ENTRY_CUTOFF; flat by MARKET_CLOSE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, replace
from typing import Optional

from config import settings
from core.logger import get_logger

logger = get_logger("research.nifty_open_reversion.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "nifty_open_reversion.json"
RESULTS_DIR = settings.DATA_DIR / "backtest_results"

# ── strategy defaults (§34) ──────────────────────────────────────────
ENTRY_OFFSET = 50.0
STOP_LOSS = 50.0
TARGET = 50.0

ENTRY_START = "09:15"
ENTRY_CUTOFF = "10:30"
MARKET_CLOSE = "15:30"

STARTING_CAPITAL = 250_000.0
LOT_SIZE = 65

PROFIT_THRESHOLD_2_LOTS = 300_000.0
PROFIT_THRESHOLD_3_LOTS = 600_000.0
PROFIT_THRESHOLD_4_LOTS = 1_000_000.0
MAX_LOTS = 4

INSTRUMENT_MODES = ("spot", "option_buy", "option_sell")
LEVEL_MODES = ("points", "percent")


@dataclass
class Costs:
    """All zero by default, so the base run reproduces the pure point result."""
    brokerage_per_order: float = 0.0
    slippage_points: float = 0.0
    stt_pct: float = 0.0
    exchange_pct: float = 0.0
    gst_pct: float = 0.0
    sebi_pct: float = 0.0
    stamp_pct: float = 0.0

    @property
    def any_on(self) -> bool:
        return any(v for v in asdict(self).values())


@dataclass
class Config:
    # ── levels ──
    entry_offset: float = ENTRY_OFFSET
    stop_loss: float = STOP_LOSS
    target: float = TARGET
    level_mode: str = "points"          # points | percent (of the daily open)

    # ── session ──
    entry_start: str = ENTRY_START
    entry_cutoff: str = ENTRY_CUTOFF
    market_close: str = MARKET_CLOSE

    # ── sides ──
    trade_buy: bool = True
    trade_sell: bool = True
    max_trades_per_day: int = 2         # 1 BUY + 1 SELL
    max_per_side_per_day: int = 1

    # ── capital & scaling ──
    starting_capital: float = STARTING_CAPITAL
    lot_size: int = LOT_SIZE
    base_lots: int = 1
    max_lots: int = MAX_LOTS
    scale_enabled: bool = True
    profit_threshold_2: float = PROFIT_THRESHOLD_2_LOTS
    profit_threshold_3: float = PROFIT_THRESHOLD_3_LOTS
    profit_threshold_4: float = PROFIT_THRESHOLD_4_LOTS

    # ── instrument (§39) ──
    instrument_mode: str = "spot"       # spot | option_buy | option_sell
    strike_offset: int = 0              # points from ATM: 0, 50, 100, …
    expiry_type: str = "weekly"         # weekly | monthly
    option_sl_mode: str = "underlying"  # underlying | premium_points | premium_pct
    option_sl_value: float = 50.0
    option_tp_value: float = 50.0

    # ── costs ──
    costs: Costs = field(default_factory=Costs)

    # ── live control ──
    paper_trade: bool = True            # real orders need this OFF and the gate on
    auto_start: bool = False
    telegram_alerts: bool = False
    telegram_bot: str = "a"

    # ── data ──
    csv_path: str = ""                  # blank → pull from the broker
    symbol: str = "NIFTY 50"
    start_date: str = ""                # blank → all available
    end_date: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Config":
        d = dict(d or {})
        costs = d.pop("costs", None)
        cfg = cls()
        for k, v in d.items():
            if hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)
        if isinstance(costs, dict):
            cfg.costs = Costs(**{k: float(v) for k, v in costs.items() if hasattr(Costs, k)})
        return cfg.sanitized()

    def sanitized(self) -> "Config":
        c = replace(self)
        c.entry_offset = max(0.0, float(c.entry_offset))
        c.stop_loss = max(0.01, float(c.stop_loss))
        c.target = max(0.01, float(c.target))
        c.level_mode = c.level_mode if c.level_mode in LEVEL_MODES else "points"
        c.lot_size = max(1, int(c.lot_size))
        c.base_lots = max(1, int(c.base_lots))
        c.max_lots = max(c.base_lots, int(c.max_lots))
        c.max_trades_per_day = max(1, int(c.max_trades_per_day))
        c.max_per_side_per_day = max(1, int(c.max_per_side_per_day))
        c.starting_capital = max(1.0, float(c.starting_capital))
        c.instrument_mode = c.instrument_mode if c.instrument_mode in INSTRUMENT_MODES else "spot"
        c.strike_offset = max(0, int(c.strike_offset))
        c.expiry_type = c.expiry_type if c.expiry_type in ("weekly", "monthly") else "weekly"
        if c.option_sl_mode not in ("underlying", "premium_points", "premium_pct"):
            c.option_sl_mode = "underlying"
        for f in ("profit_threshold_2", "profit_threshold_3", "profit_threshold_4"):
            setattr(c, f, max(0.0, float(getattr(c, f))))
        c.trade_buy = bool(c.trade_buy)
        c.trade_sell = bool(c.trade_sell)
        c.scale_enabled = bool(c.scale_enabled)
        c.paper_trade = bool(c.paper_trade)
        c.auto_start = bool(c.auto_start)
        c.telegram_alerts = bool(c.telegram_alerts)
        c.telegram_bot = c.telegram_bot if c.telegram_bot in ("a", "b") else "a"
        return c

    # ── level helpers so points/percent share one code path ──
    def offset_for(self, daily_open: float) -> float:
        return daily_open * self.entry_offset / 100.0 if self.level_mode == "percent" else self.entry_offset

    def sl_for(self, daily_open: float) -> float:
        return daily_open * self.stop_loss / 100.0 if self.level_mode == "percent" else self.stop_loss

    def tp_for(self, daily_open: float) -> float:
        return daily_open * self.target / 100.0 if self.level_mode == "percent" else self.target


def load_config() -> Config:
    try:
        if CONFIG_FILE.exists():
            return Config.from_dict(json.loads(CONFIG_FILE.read_text()).get("config", {}))
    except Exception as exc:
        logger.debug("nifty_open_reversion config read failed: %s", exc)
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
        logger.error("nifty_open_reversion config save failed: %s", exc)
    return cfg
