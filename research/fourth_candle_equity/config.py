"""
Config for the 4th-Candle CASH-EQUITY Strategy (Equity Strategy #3).

Same 4th-candle setup as the options version, but trades the STOCK directly —
LONG on a CALL bias (break above the 4th-candle high), SHORT on a PUT bias
(break below the 4th-candle low) — as MIS intraday or CNC holding, with target/
SL on the stock price. Cash-equity 5-min data goes back years, so this works for
long historical backtests where option data doesn't exist.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger

logger = get_logger("research.fourth_candle_equity.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "fourth_candle_equity.json"

TIMEFRAME = "5minute"
PRE_CANDLES = 3
SIGNAL_CANDLE = 4

DEFAULT_CONFIG: dict = {
    # ── entry ──
    "entry_cutoff": "14:30",
    "one_signal_per_day": True,
    "reverse_signal": False,          # 3 red→SHORT, 3 green→LONG when set
    "square_off": "15:15",            # MIS intraday square-off time
    # ── target / SL (on the stock price) ──
    "target_mode": "percent",         # percent | points
    "sl_mode": "percent",
    "target_value": 1.5,              # default 1.5% target (cash equity moves are smaller)
    "sl_value": 0.75,                 # default 0.75% stop
    # ── product / sizing ──
    "product": "MIS",                 # MIS (intraday) | CNC (delivery / holding)
    "max_hold_days": 5,               # CNC only — days to hold before square-off
    "qty_mode": "capital",            # capital | fixed
    "capital_per_trade": 100000,      # qty = capital // entry
    "fixed_qty": 1,
    # ── portfolio (paper/live) ──
    "max_positions": 10,
    "max_long": 7,                    # 7 : 3 long : short by default (configurable)
    "max_short": 3,
    # ── universe / scan ──
    "history_days": 60,
    "max_stocks": 0,
    "scan_interval": 60,
    # ── realistic costs ──
    "apply_costs": False,
    "slippage_bps": 5.0,
    "brokerage_per_order": 20.0,
    "charges_pct": 0.05,
    # ── live control ──
    "paper_trade": True,
    "auto_start": False,
    "symbols": [],
    # ── telegram ──
    "telegram_alerts": False,
    "telegram_bot": "a",
}

_NUM = {"target_value", "sl_value", "max_hold_days", "capital_per_trade", "fixed_qty",
        "max_positions", "max_long", "max_short", "history_days", "max_stocks",
        "scan_interval", "slippage_bps", "brokerage_per_order", "charges_pct"}


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
    if out["product"] not in ("MIS", "CNC"):
        out["product"] = "MIS"
    if out["qty_mode"] not in ("capital", "fixed"):
        out["qty_mode"] = "capital"
    out["target_value"] = max(0.0, out["target_value"])
    out["sl_value"] = max(0.0, out["sl_value"])
    out["max_hold_days"] = max(0, min(60, int(out["max_hold_days"])))
    out["capital_per_trade"] = max(0, int(out["capital_per_trade"]))
    out["fixed_qty"] = max(1, int(out["fixed_qty"]))
    out["max_positions"] = max(1, min(500, int(out["max_positions"])))
    out["max_long"] = max(0, int(out["max_long"]))
    out["max_short"] = max(0, int(out["max_short"]))
    out["scan_interval"] = max(10, min(600, int(out["scan_interval"])))
    out["history_days"] = max(5, min(400, int(out["history_days"])))
    for b in ("one_signal_per_day", "reverse_signal", "apply_costs", "paper_trade",
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
        logger.debug("fourth_candle_equity config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("fourth_candle_equity config save failed: %s", exc)
    return cfg
