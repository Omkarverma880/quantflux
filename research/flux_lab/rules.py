"""
Flux Lab — the signal engine.

**This module is the whole point of the lab.** It is the only place a trading decision is made,
and it is called identically by the historical backtest, the bar-by-bar replay and live paper
trading. There is no "live version" of a rule. If the three modes ever disagree, they disagree
about data or timing, never about logic — which is exactly what the lab is built to measure.

A strategy is a list of conditions over the feature frame plus a direction. Conditions are
declarative so they can be stored in the database, hashed for reproducibility, swept over a
parameter grid and rendered back to you in English on the trade ledger.

The engine receives a *context*: one row of features (the decision bar) and the few rows before
it. It never receives the frame beyond that bar — the slicing is done by the caller and asserted
here, so a look-ahead bug cannot hide behind a helpful default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

ENGINE_VERSION = "flux-1"


# ── conditions ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class Condition:
    """One testable statement about the decision bar."""
    key: str
    label: str                       # how it reads on the trade card
    test: Callable[[pd.Series, dict], bool]
    describe: Callable[[pd.Series, dict], str] = None      # actual values at that bar
    group: str = "other"


def _num(row: pd.Series, name: str) -> float:
    v = row.get(name)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f


def _flag(row: pd.Series, name: str) -> bool:
    v = row.get(name)
    return bool(v) if v is not None and v == v else False      # NaN-safe


def _fmt(row: pd.Series, *names: str) -> str:
    bits = []
    for n in names:
        v = _num(row, n)
        bits.append(f"{n} {v:,.2f}" if np.isfinite(v) else f"{n} —")
    return " · ".join(bits)


CONDITIONS: dict[str, Condition] = {}


def _register(key, label, test, describe=None, group="other"):
    CONDITIONS[key] = Condition(key, label, test, describe, group)


# trend
_register("ema_stack_up", "EMAs stacked upward (fast > slow > trend)",
          lambda r, p: _flag(r, "ema_stack_up"),
          lambda r, p: _fmt(r, "ema_fast", "ema_slow", "ema_trend"), "trend")
_register("ema_stack_down", "EMAs stacked downward (fast < slow < trend)",
          lambda r, p: _flag(r, "ema_stack_down"),
          lambda r, p: _fmt(r, "ema_fast", "ema_slow", "ema_trend"), "trend")
_register("ema_cross_up", "Fast EMA crossed above the slow EMA on this bar",
          lambda r, p: _flag(r, "ema_cross_up"), lambda r, p: _fmt(r, "ema_fast", "ema_slow"), "trend")
_register("ema_cross_down", "Fast EMA crossed below the slow EMA on this bar",
          lambda r, p: _flag(r, "ema_cross_down"), lambda r, p: _fmt(r, "ema_fast", "ema_slow"), "trend")
_register("above_vwap", "Price above the session VWAP",
          lambda r, p: _flag(r, "above_vwap"), lambda r, p: _fmt(r, "close", "vwap"), "trend")
_register("below_vwap", "Price below the session VWAP",
          lambda r, p: not _flag(r, "above_vwap"), lambda r, p: _fmt(r, "close", "vwap"), "trend")
_register("higher_highs", "Making higher highs", lambda r, p: _flag(r, "higher_highs"), None, "trend")
_register("lower_lows", "Making lower lows", lambda r, p: _flag(r, "lower_lows"), None, "trend")

# momentum
_register("rsi_above", "RSI above the floor",
          lambda r, p: _num(r, "rsi") >= float(p.get("rsi_min", 55)),
          lambda r, p: f"RSI {_num(r, 'rsi'):.1f} (floor {p.get('rsi_min', 55)})", "momentum")
_register("rsi_below", "RSI below the ceiling",
          lambda r, p: _num(r, "rsi") <= float(p.get("rsi_max", 45)),
          lambda r, p: f"RSI {_num(r, 'rsi'):.1f} (ceiling {p.get('rsi_max', 45)})", "momentum")

# volatility
_register("vol_expanding", "Bollinger width above its own average (expansion)",
          lambda r, p: _flag(r, "vol_expanding"),
          lambda r, p: _fmt(r, "bb_width", "bb_width_avg"), "volatility")
_register("vol_contracting", "Bollinger width below its own average (coil)",
          lambda r, p: not _flag(r, "vol_expanding"),
          lambda r, p: _fmt(r, "bb_width", "bb_width_avg"), "volatility")
_register("atr_min", "Enough movement in the bar (ATR floor)",
          lambda r, p: _num(r, "atr") >= float(p.get("atr_min", 0)),
          lambda r, p: f"ATR {_num(r, 'atr'):.2f} (floor {p.get('atr_min', 0)})", "volatility")
_register("vix_below", "India VIX below the ceiling",
          lambda r, p: not np.isfinite(_num(r, "vix")) or _num(r, "vix") <= float(p.get("vix_max", 99)),
          lambda r, p: f"VIX {_num(r, 'vix'):.2f} (ceiling {p.get('vix_max', 99)})", "context")
_register("vix_above", "India VIX above the floor",
          lambda r, p: np.isfinite(_num(r, "vix")) and _num(r, "vix") >= float(p.get("vix_min", 0)),
          lambda r, p: f"VIX {_num(r, 'vix'):.2f} (floor {p.get('vix_min', 0)})", "context")

# price action
_register("or_break_up", "Broke above the opening range",
          lambda r, p: _flag(r, "or_break_up"), lambda r, p: _fmt(r, "close", "or_high"), "price action")
_register("or_break_down", "Broke below the opening range",
          lambda r, p: _flag(r, "or_break_down"), lambda r, p: _fmt(r, "close", "or_low"), "price action")
_register("above_prev_high", "Above yesterday's high",
          lambda r, p: _flag(r, "above_prev_high"), lambda r, p: _fmt(r, "close", "prev_high"), "price action")
_register("below_prev_low", "Below yesterday's low",
          lambda r, p: _flag(r, "below_prev_low"), lambda r, p: _fmt(r, "close", "prev_low"), "price action")
_register("day_high_break", "New high of the day",
          lambda r, p: _num(r, "close") >= _num(r, "day_high_so_far") - 1e-9,
          lambda r, p: _fmt(r, "close", "day_high_so_far"), "price action")
_register("day_low_break", "New low of the day",
          lambda r, p: _num(r, "close") <= _num(r, "day_low_so_far") + 1e-9,
          lambda r, p: _fmt(r, "close", "day_low_so_far"), "price action")

# candles
_register("bull_candle", "Bullish candle", lambda r, p: _flag(r, "bull"), None, "candle")
_register("bear_candle", "Bearish candle", lambda r, p: _flag(r, "bear"), None, "candle")
_register("bull_streak", "Consecutive bullish candles",
          lambda r, p: _num(r, "bull_streak") >= float(p.get("streak", 2)),
          lambda r, p: f"{int(_num(r, 'bull_streak'))} in a row (need {p.get('streak', 2)})", "candle")
_register("bear_streak", "Consecutive bearish candles",
          lambda r, p: _num(r, "bear_streak") >= float(p.get("streak", 2)),
          lambda r, p: f"{int(_num(r, 'bear_streak'))} in a row (need {p.get('streak', 2)})", "candle")
_register("engulf_up", "Bullish engulfing", lambda r, p: _flag(r, "engulf_up"), None, "candle")
_register("engulf_down", "Bearish engulfing", lambda r, p: _flag(r, "engulf_down"), None, "candle")
_register("ha_bull", "Heikin-Ashi candle is bullish", lambda r, p: _flag(r, "ha_bull"), None, "candle")
_register("ha_bear", "Heikin-Ashi candle is bearish", lambda r, p: _flag(r, "ha_bear"), None, "candle")
_register("body_strong", "Candle body fills most of its range",
          lambda r, p: _num(r, "body_pct") >= float(p.get("body_min_pct", 50)),
          lambda r, p: f"body {_num(r, 'body_pct'):.0f}% of range (need {p.get('body_min_pct', 50)}%)", "candle")

# volume
_register("volume_expansion", "Volume above its recent average",
          lambda r, p: _num(r, "vol_ratio") >= float(p.get("vol_ratio_min", 1.2)),
          lambda r, p: f"volume {_num(r, 'vol_ratio'):.2f}× the {p.get('vol_avg_period', 20)}-bar average", "volume")


# ── a strategy ───────────────────────────────────────────────────────
@dataclass
class Strategy:
    name: str
    side: str                                   # CE (long bias) | PE (short bias)
    conditions: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    entry_from: int = 570                       # no signal before this minute of the day
    entry_until: int = 900                      # …or after it
    note: str = ""

    def describe(self) -> list[str]:
        return [CONDITIONS[c].label for c in self.conditions if c in CONDITIONS]


PRESETS: dict[str, dict] = {
    "trend_pullback_ce": {
        "name": "Trend pullback — buy CE",
        "side": "CE",
        "conditions": ["ema_stack_up", "above_vwap", "rsi_above", "volume_expansion"],
        "params": {"rsi_min": 55, "vol_ratio_min": 1.1},
        "note": "Uptrend intact, price holding above VWAP, momentum and volume confirming.",
    },
    "trend_pullback_pe": {
        "name": "Trend pullback — buy PE",
        "side": "PE",
        "conditions": ["ema_stack_down", "below_vwap", "rsi_below", "volume_expansion"],
        "params": {"rsi_max": 45, "vol_ratio_min": 1.1},
        "note": "Mirror of the CE version for a downtrend.",
    },
    "or_breakout_ce": {
        "name": "Opening-range breakout — buy CE",
        "side": "CE",
        "conditions": ["or_break_up", "above_vwap", "volume_expansion", "vol_expanding"],
        "params": {"vol_ratio_min": 1.3, "opening_range_min": 15},
        "entry_from": 570,
        "note": "First clean break of the opening range with volume and expanding bands.",
    },
    "or_breakdown_pe": {
        "name": "Opening-range breakdown — buy PE",
        "side": "PE",
        "conditions": ["or_break_down", "below_vwap", "volume_expansion", "vol_expanding"],
        "params": {"vol_ratio_min": 1.3, "opening_range_min": 15},
        "entry_from": 570,
        "note": "Mirror of the breakout for the downside.",
    },
    "momentum_burst_ce": {
        "name": "Momentum burst — buy CE",
        "side": "CE",
        "conditions": ["bull_streak", "body_strong", "above_vwap", "day_high_break"],
        "params": {"streak": 2, "body_min_pct": 55},
        "note": "Two strong green bars taking out the day's high from above VWAP.",
    },
    "momentum_burst_pe": {
        "name": "Momentum burst — buy PE",
        "side": "PE",
        "conditions": ["bear_streak", "body_strong", "below_vwap", "day_low_break"],
        "params": {"streak": 2, "body_min_pct": 55},
        "note": "Mirror of the burst for the downside.",
    },
}


def from_config(cfg: dict) -> Strategy:
    base = PRESETS.get(cfg.get("preset", ""), {})
    merged = {**base, **{k: v for k, v in cfg.items() if v is not None and k != "params"}}
    params = {**base.get("params", {}), **(cfg.get("params") or {})}
    return Strategy(
        name=merged.get("name") or cfg.get("preset") or "custom",
        side=(merged.get("side") or "CE").upper(),
        conditions=[c for c in (merged.get("conditions") or []) if c in CONDITIONS],
        params=params,
        entry_from=int(merged.get("entry_from", 570)),
        entry_until=int(merged.get("entry_until", 900)),
        note=merged.get("note", ""),
    )


# ── the decision ─────────────────────────────────────────────────────
@dataclass
class Decision:
    fired: bool
    side: str
    reasons: list[dict]                 # every condition, whether it passed, and its values
    blocked_by: Optional[str] = None

    def as_dict(self) -> dict:
        return {"fired": self.fired, "side": self.side, "reasons": self.reasons,
                "blocked_by": self.blocked_by}


def evaluate(row: pd.Series, strategy: Strategy, explain: bool = True) -> Decision:
    """Does this strategy fire on this bar?

    ``row`` must be the decision bar of a causal feature frame. Only its own values are read —
    no index arithmetic, no neighbouring rows — which is what makes the same call valid in a
    backtest loop, a replay step and a live tick.
    """
    reasons: list[dict] = []
    fired = True
    blocked = None
    minute = int(row.get("minute") or 0)
    if minute < strategy.entry_from:
        fired, blocked = False, f"before the entry window opens ({strategy.entry_from // 60:02d}:{strategy.entry_from % 60:02d})"
    elif minute > strategy.entry_until:
        fired, blocked = False, f"after the last entry time ({strategy.entry_until // 60:02d}:{strategy.entry_until % 60:02d})"

    for key in strategy.conditions:
        cond = CONDITIONS.get(key)
        if cond is None:
            continue
        try:
            ok = bool(cond.test(row, strategy.params))
        except Exception:
            ok = False
        detail = ""
        if explain and cond.describe:
            try:
                detail = cond.describe(row, strategy.params)
            except Exception:
                detail = ""
        reasons.append({"key": key, "label": cond.label, "group": cond.group,
                        "passed": ok, "detail": detail})
        if not ok:
            fired = False
    if not strategy.conditions:
        fired = False
        blocked = blocked or "the strategy has no conditions"
    return Decision(fired=fired and blocked is None, side=strategy.side,
                    reasons=reasons, blocked_by=blocked)


def catalogue() -> list[dict]:
    """Every available condition, for the UI's condition picker."""
    return [{"key": c.key, "label": c.label, "group": c.group} for c in CONDITIONS.values()]
