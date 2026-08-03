"""
Configuration for the Prev-Month-VWAP Equity-Holding Research.

Config-driven so scenarios can be tuned before deploying a strategy. Durable
file-backed JSON, mirroring the other research modules.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger
from research.pmvwap_equity.constants import (
    TIMEFRAME_MAP, DEFAULT_TIMEFRAME, ENTRY_CROSS_UP, ENTRY_TOUCH,
)

logger = get_logger("research.pmvwap_equity.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "pmvwap_equity.json"

DEFAULT_CONFIG: dict = {
    # ── entry ──
    "timeframe": DEFAULT_TIMEFRAME,
    "entry_mode": ENTRY_CROSS_UP,          # cross_up | touch
    "vwap_buffer": 0.0,                     # points tolerance around Prev-Month VWAP
    "require_pw_above_pm": True,            # green (Prev-Week) must be above purple (Prev-Month)
    "one_signal_per_day": True,
    "entry_start": "09:20",
    "signal_cutoff": "15:15",
    "history_days": 90,                     # lookback to build Prev-Month/Week VWAP
    # ── holding simulation ──
    "capital_per_trade": 100000,           # sizing → qty = capital // entry price
    "fixed_qty": 0,                         # >0 overrides capital sizing
    "target_pct": 10.0,                     # holding target %
    "stop_pct": 0.0,                        # 0 = no stop
    "max_hold_days": 20,                    # square the holding after N calendar days
    "exit_on": "close",                     # close | high_low
    # ── universe filters ──
    "min_price": 0, "max_price": 0, "min_volume": 0,
    "sectors": [], "ignore_ban": True,
    "high_vol_only": False, "high_vol_metric": "atr_pct", "high_vol_threshold": 3.0,
    # ── realistic costs (net-of-costs toggle) ──
    "apply_costs": False,                   # net P&L after brokerage + STT + slippage
    "slippage_bps": 5.0,                    # per side
    "brokerage_per_order": 0.0,             # ₹ per order (delivery often free)
    "charges_pct": 0.12,                    # STT+exchange+SEBI+stamp+GST, % of turnover
    # ── portfolio capital model (true ROI on a fixed pool) ──
    "portfolio_mode": False,
    "portfolio_capital": 1000000,           # ₹ pool
    "max_concurrent": 10,                   # max simultaneous holdings
    # ── performance / display ──
    "max_stocks": 0, "scan_interval": 60,
    "telegram_alerts": True,                # push each new live-scan signal to Telegram (needs universal Telegram enabled)
}

_INT_KEYS = {"history_days", "capital_per_trade", "fixed_qty", "max_hold_days",
             "min_price", "max_price", "min_volume", "max_stocks", "scan_interval",
             "portfolio_capital", "max_concurrent"}
_FLOAT_KEYS = {"vwap_buffer", "target_pct", "stop_pct", "high_vol_threshold",
               "slippage_bps", "brokerage_per_order", "charges_pct"}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    if out["timeframe"] not in TIMEFRAME_MAP:
        out["timeframe"] = DEFAULT_TIMEFRAME
    if out["entry_mode"] not in (ENTRY_CROSS_UP, ENTRY_TOUCH):
        out["entry_mode"] = ENTRY_CROSS_UP
    if out["exit_on"] not in ("close", "high_low"):
        out["exit_on"] = "close"
    for k in _INT_KEYS:
        try:
            out[k] = int(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    for k in _FLOAT_KEYS:
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    out["scan_interval"] = max(3, min(600, out["scan_interval"]))
    out["history_days"] = max(35, min(400, out["history_days"]))
    out["max_hold_days"] = max(1, min(250, out["max_hold_days"]))
    out["max_concurrent"] = max(1, min(500, out["max_concurrent"]))
    out["portfolio_capital"] = max(0, out["portfolio_capital"])
    if not isinstance(out["sectors"], list):
        out["sectors"] = []
    for b in ("require_pw_above_pm", "one_signal_per_day", "ignore_ban", "high_vol_only",
              "apply_costs", "portfolio_mode", "telegram_alerts"):
        out[b] = bool(out[b])
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("pmvwap_equity config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("pmvwap_equity config save failed: %s", exc)
    return cfg
