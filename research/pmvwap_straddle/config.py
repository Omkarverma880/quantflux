"""
Configuration for the Previous-Month-VWAP Straddle Research.

Everything is configurable so scenarios can be tuned before any strategy is
deployed. Durable file-backed JSON, mirroring the other research modules.
"""
from __future__ import annotations

import json

from config import settings
from core.logger import get_logger
from research.pmvwap_straddle.constants import TIMEFRAME_MAP, DEFAULT_TIMEFRAME

logger = get_logger("research.pmvwap_straddle.config")

CONFIG_FILE = settings.DATA_DIR / "research" / "pmvwap_straddle.json"

DEFAULT_CONFIG: dict = {
    # ── core ──
    "timeframe": DEFAULT_TIMEFRAME,       # underlying candle TF for VWAP + crossing
    # ── entry selection ──
    # entry_mode: "vwap_cross"  = cross Prev-Month VWAP from below (legacy default)
    #             "level_touch" = enter when LTP touches a level derived from the
    #                             Prev-Month VWAP by a signed % or points offset.
    "entry_mode": "vwap_cross",
    "entry_offset_mode": "percent",       # percent | points
    "entry_offset_dir": "below",          # above | below | both (where the trigger sits vs VWAP)
    "entry_offset_value": 0.0,            # magnitude (≥0). 0 = the VWAP itself
    # legs: "both" = ATM straddle (CE+PE) | "call" = CE only | "put" = PE only
    "legs": "both",
    # exit_mode: "combined_pnl" = exit BOTH legs when combined P&L hits target/SL
    #            "leg_target"   = legacy independent second-leg target-% exit
    "exit_mode": "combined_pnl",
    # Monthly straddle: HOLD across days until target/SL, else square off on the
    # EXPIRY day at ``square_off`` — no daily square-off.
    "hold_to_expiry": True,
    # Target / SL defined by ₹ amount, % of combined premium, or points.
    "target_mode": "amount",              # amount | percent | points
    "sl_mode": "amount",                  # amount | percent | points
    "target_amount": 20000.0,             # combined-P&L target (₹) — amount mode
    "sl_amount": 6500.0,                  # combined-P&L stop-loss (₹) — amount mode
    "target_percent": 30.0,               # % of combined entry premium — percent mode
    "sl_percent": 15.0,
    "target_points": 30.0,                # points on combined premium — points mode
    "sl_points": 15.0,
    "target_pct": 50.0,                   # combined-premium target % (leg_target mode)
    "vwap_buffer": 0.0,                   # points tolerance around Prev-Month VWAP
    "expiry_type": "monthly",             # equity options are monthly
    "square_off": "15:20",                # intraday square-off (HH:MM)
    "entry_start": "09:20",               # no entries before
    "signal_cutoff": "15:00",             # no new entries after
    "one_signal_per_day": True,           # first crossing only vs. every crossing
    "history_days": 90,                   # lookback to build Prev-Month VWAP
    # ── universe filters ──
    "min_price": 0,                       # 0 = disabled
    "max_price": 0,
    "min_volume": 0,                      # min underlying day volume
    "min_adv": 0,                         # min average daily volume (20d)
    "min_atr": 0.0,                       # min ATR (points)
    "min_atr_pct": 0.0,                   # min ATR % of price
    "sectors": [],                        # [] = all sectors
    "ignore_ban": True,                   # skip F&O ban-period stocks
    # ── high-volatility mode ──
    "high_vol_only": False,
    "high_vol_metric": "atr_pct",         # atr_pct | range_pct
    "high_vol_threshold": 3.0,            # % threshold
    # ── realistic costs (net-of-costs toggle) ──
    "apply_costs": False,                 # net premium P&L after brokerage + STT + slippage
    "slippage_bps": 20.0,                 # per side, on premium
    "brokerage_per_order": 20.0,          # ₹ per order (4 orders per straddle)
    "charges_pct": 0.10,                  # STT+exchange+GST, % of premium turnover
    # ── performance / display ──
    "max_stocks": 0,                      # 0 = all; cap for multi-scan
    "scan_interval": 60,                  # live poll seconds (3–600)
    "lot_size_display": True,
    "per_stock_budget_ms": 0,             # 0 = unlimited; else time-box multi-scan
}

_NUM_KEYS = {"target_pct", "target_amount", "sl_amount", "target_percent", "sl_percent",
             "target_points", "sl_points", "entry_offset_value", "vwap_buffer", "history_days",
             "min_price", "max_price", "min_volume", "min_adv", "min_atr", "min_atr_pct",
             "high_vol_threshold", "max_stocks", "scan_interval", "per_stock_budget_ms",
             "slippage_bps", "brokerage_per_order", "charges_pct"}


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG and v is not None})
    if out["timeframe"] not in TIMEFRAME_MAP:
        out["timeframe"] = DEFAULT_TIMEFRAME
    if out["expiry_type"] not in ("weekly", "monthly"):
        out["expiry_type"] = "monthly"
    for k in _NUM_KEYS:
        try:
            out[k] = float(out[k]) if isinstance(DEFAULT_CONFIG[k], float) else int(out[k])
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    out["target_pct"] = max(0.0, out["target_pct"])
    out["target_amount"] = max(0.0, out["target_amount"])
    out["sl_amount"] = max(0.0, out["sl_amount"])
    out["scan_interval"] = max(3, min(600, int(out["scan_interval"])))
    out["history_days"] = max(35, min(400, int(out["history_days"])))
    if out["exit_mode"] not in ("combined_pnl", "leg_target"):
        out["exit_mode"] = "combined_pnl"
    if out["target_mode"] not in ("amount", "percent", "points"):
        out["target_mode"] = "amount"
    if out["sl_mode"] not in ("amount", "percent", "points"):
        out["sl_mode"] = "amount"
    if out["entry_mode"] not in ("vwap_cross", "level_touch"):
        out["entry_mode"] = "vwap_cross"
    if out["entry_offset_mode"] not in ("percent", "points"):
        out["entry_offset_mode"] = "percent"
    if out["entry_offset_dir"] not in ("above", "below", "both"):
        out["entry_offset_dir"] = "below"
    out["entry_offset_value"] = abs(float(out["entry_offset_value"]))   # magnitude only
    if out["legs"] not in ("both", "call", "put"):
        out["legs"] = "both"
    out["hold_to_expiry"] = bool(out["hold_to_expiry"])
    if out["high_vol_metric"] not in ("atr_pct", "range_pct"):
        out["high_vol_metric"] = "atr_pct"
    if not isinstance(out["sectors"], list):
        out["sectors"] = []
    for b in ("one_signal_per_day", "ignore_ban", "high_vol_only", "lot_size_display", "apply_costs"):
        out[b] = bool(out[b])
    return out


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()).get("config", {}) or {})
    except Exception as exc:
        logger.debug("pmvwap_straddle config read failed: %s", exc)
    return sanitize(cfg)


def save_config(partial: dict) -> dict:
    cfg = sanitize({**load_config(), **(partial or {})})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"config": cfg}, indent=2))
    except Exception as exc:
        logger.error("pmvwap_straddle config save failed: %s", exc)
    return cfg
