"""
Config + constants for the 4th-Candle Strategy (Equity Strategy #2).

Concept: on a 5-minute chart the 4th candle (09:30–09:35) high & low become
reference lines. If the first 3 candles are all RED and price later breaks ABOVE
the 4th-candle high → buy ATM CALL of that F&O stock. If the first 3 candles are
all GREEN and price breaks BELOW the 4th-candle low → buy ATM PUT. Mixed → no
trade. Positional (NRML) option orders; target/SL on the option premium.

Durable file-backed JSON, mirroring the other research modules. Config can be
saved as default or passed per-run without saving.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger

logger = get_logger("research.fourth_candle.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "fourth_candle.json"

# The strategy is defined on 5-minute candles.
TIMEFRAME = "5minute"
PRE_CANDLES = 3            # first 3 candles decide the bias (all red / all green)
SIGNAL_CANDLE = 4          # 4th candle (09:30–09:35) high/low = reference lines

DEFAULT_CONFIG: dict = {
    # ── entry ──
    "entry_cutoff": "14:30",          # no new breakouts after this (IST)
    "expiry_type": "monthly",         # F&O equity options are monthly
    "hold_to_expiry": True,           # NRML positional — hold until target/SL or expiry
    "square_off": "15:20",            # expiry-day square-off time
    "one_signal_per_day": True,       # one entry per stock per day
    # ── target / SL (on the option premium) ──
    "target_mode": "percent",         # percent | points
    "sl_mode": "percent",             # percent | points
    "target_value": 30.0,             # default 30% target
    "sl_value": 25.0,                 # default 25% stop
    # ── portfolio (paper/live) ──
    "max_positions": 10,
    "max_calls": 7,                   # 7 : 3 CE : PE by default (configurable)
    "max_puts": 3,
    "product": "NRML",                # normal (carry-forward), NOT intraday
    "lots": 1,                        # qty = lot_size × lots
    # ── universe / scan ──
    "history_days": 60,               # intraday lookback buffer for backtest
    "max_stocks": 0,                  # 0 = all
    "scan_interval": 60,              # live poll seconds
    # ── realistic costs ──
    "apply_costs": False,
    "slippage_bps": 20.0,
    "brokerage_per_order": 20.0,
    "charges_pct": 0.10,
    # ── live control (persisted with the strategy, not the research cfg) ──
    "paper_trade": True,
    "auto_start": False,
    "symbols": [],                    # strategy universe (watchlist) for paper/live
    # ── telegram (per-strategy; picks which universal bot to use) ──
    "telegram_alerts": False,
    "telegram_bot": "a",              # a | b  (configured in Settings → Telegram)
}

_NUM = {"target_value", "sl_value", "max_positions", "max_calls", "max_puts", "lots",
        "history_days", "max_stocks", "scan_interval",
        "slippage_bps", "brokerage_per_order", "charges_pct"}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    for k in _NUM:
        try:
            out[k] = float(out[k]) if isinstance(DEFAULT_CONFIG[k], float) else int(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    if out["target_mode"] not in ("percent", "points"):
        out["target_mode"] = "percent"
    if out["sl_mode"] not in ("percent", "points"):
        out["sl_mode"] = "percent"
    if out["expiry_type"] not in ("weekly", "monthly"):
        out["expiry_type"] = "monthly"
    if out["product"] not in ("NRML", "CNC", "MIS"):
        out["product"] = "NRML"
    out["target_value"] = max(0.0, out["target_value"])
    out["sl_value"] = max(0.0, out["sl_value"])
    out["max_positions"] = max(1, min(500, int(out["max_positions"])))
    out["max_calls"] = max(0, int(out["max_calls"]))
    out["max_puts"] = max(0, int(out["max_puts"]))
    out["lots"] = max(1, int(out["lots"]))
    out["scan_interval"] = max(10, min(600, int(out["scan_interval"])))
    out["history_days"] = max(5, min(400, int(out["history_days"])))
    for b in ("hold_to_expiry", "one_signal_per_day", "apply_costs", "paper_trade",
              "auto_start", "telegram_alerts"):
        out[b] = bool(out[b])
    if out["telegram_bot"] not in ("a", "b"):
        out["telegram_bot"] = "a"
    out["symbols"] = [str(s).strip().upper() for s in (out.get("symbols") or []) if str(s).strip()]
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("fourth_candle config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("fourth_candle config save failed: %s", exc)
    return cfg
