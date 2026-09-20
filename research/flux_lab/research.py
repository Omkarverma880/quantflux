"""
Flux Lab — the research harness.

A single backtest number is nearly worthless: it is one sample of one parameter set on one
stretch of history. This module exists to attack that number from four directions.

* **Parameter sweep** — a small, declared grid (capped, never a blind brute force) so you can see
  whether the result sits on a plateau or on a spike. A spike is overfitting with good manners.
* **Train / validation / out-of-sample** — chronological, never shuffled, and always reported as
  three separate numbers. Combining them into one P&L is how a backtest flatters itself.
* **Walk-forward** — roll a train window and a test window through the history and report each
  test window on its own. Consistency across windows matters more than one big total.
* **Robustness** — the same strategy under harsher assumptions: double slippage, higher costs,
  a later fill, slightly different stops and targets. A strategy that only survives its own
  favourite assumptions has not been validated, it has been decorated.

Nothing here decides which configuration is "best". It produces the numbers and leaves the
judgement where it belongs.
"""
from __future__ import annotations

import itertools
from copy import deepcopy
from typing import Callable, Optional

import pandas as pd

from core.logger import get_logger
from research.flux_lab import data as DATA
from research.flux_lab import engine as EN
from research.flux_lab import metrics as MX
from research.flux_lab import service as SV

logger = get_logger("research.flux_lab.research")

MAX_COMBINATIONS = 60          # a deliberate ceiling: research, not a grid-search machine


def _set_path(cfg: dict, path: str, value) -> None:
    """``execution.stop_pct`` / ``strategy.params.rsi_min`` → set that nested key."""
    node = cfg
    parts = path.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _headline(stats: dict) -> dict:
    h = dict(stats.get("headline") or {})
    eq = stats.get("equity") or {}
    h["max_drawdown"] = eq.get("max_drawdown")
    h["recovery_factor"] = eq.get("recovery_factor")
    return h


def _one(payload: dict, cache: dict, say: Callable[[str], None]) -> dict:
    """Run a configuration against a cached data bundle (loaded once per sweep)."""
    cfg = EN.config_from(payload)
    bundle = cache.get("bundle")
    result = EN.run(bundle["bars"], bundle["options"], cfg,
                    vix=bundle.get("vix"), futures=bundle.get("futures"), progress=lambda _m: None)
    if result.get("status") != "ok":
        return {"status": "error", "message": result.get("message")}
    return MX.summarise(result, cfg)


def _load_once(payload: dict, cache: dict, say) -> dict:
    cfg = EN.config_from(payload)
    key = (cfg.underlying, cfg.start, cfg.end)
    if cache.get("key") != key:
        say("loading data")
        cache["key"] = key
        cache["bundle"] = DATA.load(cfg.underlying, cfg.start, cfg.end)
    return cache["bundle"]


# ── parameter sweep ──────────────────────────────────────────────────
def sweep(payload: dict, say: Callable[[str], None], db=None, user_id: int = 0) -> dict:
    """Test a declared grid. ``grid`` maps a config path to the values to try."""
    grid: dict = payload.get("grid") or {}
    base = deepcopy(payload.get("config") or {})
    if not grid:
        return {"status": "error", "message": "no parameters were selected to sweep"}
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    if len(combos) > MAX_COMBINATIONS:
        return {"status": "error",
                "message": f"{len(combos)} combinations — that is grid-searching, not research. "
                           f"Keep it under {MAX_COMBINATIONS} (narrow a range or drop a parameter)."}
    cache: dict = {}
    _load_once(base, cache, say)
    rows = []
    for n, values in enumerate(combos, 1):
        say(f"{n}/{len(combos)}")
        cfg = deepcopy(base)
        for k, v in zip(keys, values):
            _set_path(cfg, k, v)
        stats = _one(cfg, cache, say)
        if stats.get("status") == "error":
            rows.append({"params": dict(zip(keys, values)), "error": stats.get("message")})
            continue
        rows.append({"params": dict(zip(keys, values)), **_headline(stats)})
    tested = [r for r in rows if "error" not in r]
    return {
        "status": "ok", "kind": "sweep", "combinations": len(combos), "rows": rows,
        "keys": keys,
        "note": "Nothing here is ranked for you. A parameter that only works at one exact value "
                "is a warning sign, not a discovery — look for a plateau across neighbours.",
        "stability": _stability(tested, keys),
    }


def _stability(rows: list[dict], keys: list[str]) -> dict:
    """How much the result moves when a parameter moves — a cheap overfitting check."""
    if len(rows) < 3:
        return {}
    pnls = [r.get("net_pnl") or 0 for r in rows]
    best = max(pnls)
    median = sorted(pnls)[len(pnls) // 2]
    positive = sum(1 for p in pnls if p > 0)
    return {
        "best_net_pnl": round(best, 2), "median_net_pnl": round(median, 2),
        "profitable_combinations": positive, "total": len(rows),
        "best_to_median": round(best / median, 2) if median > 0 else None,
        "reading": ("the best result is far above the median, which usually means it is a spike "
                    "rather than an edge" if median > 0 and best > 3 * median else
                    "results are broadly similar across the grid" if positive > len(rows) * 0.6 else
                    "most combinations lose money"),
    }


# ── train / validation / out-of-sample ───────────────────────────────
def splits(payload: dict, say: Callable[[str], None], db=None, user_id: int = 0) -> dict:
    base = deepcopy(payload.get("config") or {})
    cfg = EN.config_from(base)
    if not cfg.start or not cfg.end:
        cov = DATA.coverage(cfg.underlying).get("options") or {}
        cfg.start, cfg.end = cov.get("first", ""), cov.get("last", "")
    parts = DATA.split_range(cfg.start, cfg.end,
                             float(payload.get("train", 0.6)), float(payload.get("validation", 0.2)))
    if parts.get("insufficient_data"):
        return {"status": "ok", "insufficient_data": True, **parts}
    out = {"status": "ok", "kind": "splits", "periods": {}}
    for name in ("train", "validation", "test"):
        window = parts[name]
        say(f"{name}: {window['start']} → {window['end']}")
        p = deepcopy(base)
        p["start"], p["end"] = window["start"], window["end"]
        cache: dict = {}
        _load_once(p, cache, say)
        stats = _one(p, cache, say)
        out["periods"][name] = {"window": window, **(_headline(stats) if stats.get("status") == "ok" else {}),
                                "insufficient_data": stats.get("insufficient_data", True)}
    out["note"] = ("The out-of-sample number is the only one that was not seen while choosing "
                   "parameters. Read it first, and read it alone.")
    return out


# ── walk-forward ─────────────────────────────────────────────────────
def walk_forward(payload: dict, say: Callable[[str], None], db=None, user_id: int = 0) -> dict:
    base = deepcopy(payload.get("config") or {})
    train_days = int(payload.get("train_days", 30))
    test_days = int(payload.get("test_days", 10))
    cfg = EN.config_from(base)
    if not cfg.start or not cfg.end:
        cov = DATA.coverage(cfg.underlying).get("options") or {}
        cfg.start, cfg.end = cov.get("first", ""), cov.get("last", "")
    cache: dict = {}
    bundle = _load_once({**base, "start": cfg.start, "end": cfg.end}, cache, say)
    days = sorted(pd.to_datetime(bundle["bars"]["timestamp"]).dt.date.unique())
    if len(days) < train_days + test_days:
        return {"status": "ok", "insufficient_data": True, "sessions": len(days),
                "message": f"{len(days)} sessions — a {train_days}/{test_days} walk-forward needs "
                           f"at least {train_days + test_days}."}
    windows = []
    i = train_days
    while i + test_days <= len(days):
        windows.append((days[i - train_days], days[i - 1], days[i], days[i + test_days - 1]))
        i += test_days
    rows = []
    for n, (ts, te, vs, ve) in enumerate(windows, 1):
        say(f"window {n}/{len(windows)}")
        p = deepcopy(base)
        p["start"], p["end"] = str(vs), str(ve)         # the test leg is what gets reported
        c2: dict = {}
        _load_once(p, c2, lambda _m: None)
        stats = _one(p, c2, say)
        rows.append({"window": n, "train": f"{ts} → {te}", "test": f"{vs} → {ve}",
                     **(_headline(stats) if stats.get("status") == "ok" else {})})
    traded = [r for r in rows if (r.get("trades") or 0) > 0]
    positive = sum(1 for r in traded if (r.get("net_pnl") or 0) > 0)
    return {
        "status": "ok", "kind": "walk_forward", "windows": rows,
        "train_days": train_days, "test_days": test_days,
        "summary": {"windows": len(rows), "windows_with_trades": len(traded),
                    "profitable_windows": positive,
                    "total_net_pnl": round(sum(r.get("net_pnl") or 0 for r in rows), 2),
                    "reading": "consistency across windows matters more than the total"},
    }


# ── robustness ───────────────────────────────────────────────────────
VARIANTS = [
    ("baseline", {}),
    ("double slippage", {"execution.slippage_pts": 1.0}),
    ("quadruple slippage", {"execution.slippage_pts": 2.0}),
    ("fill one bar later", {"execution.entry_model": "delay", "execution.entry_delay_bars": 2}),
    ("fill at the signal close", {"execution.entry_model": "signal_close"}),
    ("stop 5 points tighter", {"execution.stop_pct": 10.0}),
    ("stop 5 points wider", {"execution.stop_pct": 20.0}),
    ("target 10% lower", {"execution.target_pct": 20.0}),
    ("target 10% higher", {"execution.target_pct": 40.0}),
    ("costs off (reference only)", {"execution.costs_on": False}),
]


def robustness(payload: dict, say: Callable[[str], None], db=None, user_id: int = 0) -> dict:
    base = deepcopy(payload.get("config") or {})
    cache: dict = {}
    _load_once(base, cache, say)
    rows = []
    for n, (name, overrides) in enumerate(VARIANTS, 1):
        say(f"{n}/{len(VARIANTS)} · {name}")
        cfg = deepcopy(base)
        for path, value in overrides.items():
            _set_path(cfg, path, value)
        stats = _one(cfg, cache, say)
        if stats.get("status") == "error":
            rows.append({"variant": name, "error": stats.get("message")})
            continue
        rows.append({"variant": name, **_headline(stats)})
    base_row = next((r for r in rows if r["variant"] == "baseline"), {})
    base_pnl = base_row.get("net_pnl")
    for r in rows:
        if base_pnl and r.get("net_pnl") is not None:
            r["vs_baseline_pct"] = round((r["net_pnl"] - base_pnl) / abs(base_pnl) * 100, 1)
    survived = [r for r in rows if r["variant"] not in ("baseline", "costs off (reference only)")
                and (r.get("net_pnl") or 0) > 0]
    return {
        "status": "ok", "kind": "robustness", "rows": rows,
        "summary": {"variants": len(rows) - 1, "still_profitable": len(survived),
                    "reading": "a strategy that only works at one exact assumption is fragile, "
                               "whatever its headline number says"},
    }


def monte_carlo(payload: dict, say: Callable[[str], None], db=None, user_id: int = 0) -> dict:
    """Reshuffle a stored run's trades. Needs a run id, not a fresh backtest."""
    run_id = int(payload.get("run_id") or 0)
    trades = SV.trades_of(db, user_id, run_id, limit=100000) if db is not None else []
    if not trades:
        return {"status": "error", "message": "that run has no stored trades"}
    say(f"{len(trades)} trades")
    return {"status": "ok", "kind": "monte_carlo", "run_id": run_id,
            **MX.monte_carlo(trades, runs=int(payload.get("runs", 500)),
                             seed=int(payload.get("seed", 7)))}
