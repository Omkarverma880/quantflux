"""
Signal evaluation — PURE and look-ahead safe.

A rule is ``{line, event, action}`` (e.g. previous-day VWAP · touch · BUY_CE).
Each bar is tested only against data at/or before it, so the same function is
correct for a backtest cutoff, a replay clock and a live tick.
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Optional

from research.vwap_options.vwap_engine import event_fired


def _hhmm(s: str, default=(9, 20)) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(*default)


def find_signals(bars: list[dict], series: list[dict], cfg: dict,
                 day=None) -> list[dict]:
    """All rule hits for one session, in time order and respecting the caps.

    ``bars``/``series`` are aligned 1:1 and must be chronological. Returns
    signal dicts carrying everything needed to resolve a contract downstream.
    """
    rules = [r for r in (cfg.get("rules") or []) if r.get("enabled")]
    if not rules:
        return []
    start = _hhmm(cfg.get("entry_start", "09:20"), (9, 20))
    cutoff = _hhmm(cfg.get("entry_cutoff", "15:00"), (15, 0))
    buf = float(cfg.get("touch_buffer_pts", 0) or 0)
    one_per_day = bool(cfg.get("one_signal_per_day"))
    max_trades = int(cfg.get("max_trades_per_day", 3))

    out: list[dict] = []
    fired_rules: set = set()
    for i, bar in enumerate(bars):
        dt = bar.get("_dt")
        if dt is None:
            continue
        if day is not None and dt.date() != day:
            continue
        t = dt.time()
        if t < start or t > cutoff:
            continue
        prev_close = float(bars[i - 1]["close"]) if i > 0 else None
        levels = series[i] if i < len(series) else {}
        for rule in rules:
            action = rule.get("action", "NONE")
            if action == "NONE":
                continue
            key = (rule["line"], rule["event"], action)
            if one_per_day and key in fired_rules:
                continue
            level = levels.get(rule["line"])
            if not event_fired(rule["event"], prev_close, bar, level, buf):
                continue
            fired_rules.add(key)
            out.append({
                "bar_index": i, "dt": dt, "date": dt.date(),
                "time": dt.strftime("%H:%M"),
                "line": rule["line"], "event": rule["event"], "action": action,
                "opt_type": "CE" if action == "BUY_CE" else "PE",
                "level": level, "index_price": round(float(bar["close"]), 2),
                "rule": f"{rule['line']} · {rule['event']} → {action}",
            })
            if len(out) >= max_trades:
                return out
    return out
