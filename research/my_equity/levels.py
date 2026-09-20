"""
Research levels: did price reach them, when, and what has happened since.

A level triggers on the first session **after the research date** whose range contains it —
which means a trigger is found from the stored daily candles, not from the app happening to be
running that day. Add a stock months late with the real research date and its history is
reconstructed exactly as it happened.

From the trigger onwards the level carries its own record: the days since, the gain from the
level to the last trade, the best and worst the trade has been (from daily highs and lows) and
where price sits now. When several levels are tracked, the first one to trigger is the primary
one and the others keep their own numbers — a second trigger is a second entry, not a
correction of the first.

Everything here is per share and long-only: these are research levels you buy at, so a gain is
price above the level. Nothing is a position and nothing is an order.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd

MAX_LEVELS = 12


def _num(v) -> Optional[float]:
    try:
        f = round(float(str(v).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def normalise(raw) -> list[dict]:
    """Accept 3100, '3100', or {'price': 3100, 'track': False, 'target': 3400, 'stop': 2950}.

    Always returns the dict form. ``target`` and ``stop`` are optional — they are where you
    said you would take the money and where you would admit the idea was wrong.
    """
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[dict] = []
    seen: set[float] = set()
    for it in items:
        track, target, stop = True, None, None
        if isinstance(it, dict):
            price = it.get("price")
            track = bool(it.get("track", True))
            target, stop = _num(it.get("target")), _num(it.get("stop"))
        else:
            price = it
        p = _num(price)
        if p is None or p in seen:
            continue
        seen.add(p)
        row = {"price": p, "track": track}
        # a target below the level, or a stop above it, is a typo — drop it rather than mislead
        if target and target > p:
            row["target"] = target
        if stop and stop < p:
            row["stop"] = stop
        out.append(row)
    return sorted(out, key=lambda x: x["price"], reverse=True)[:MAX_LEVELS]


def prices(levels) -> list[float]:
    return [l["price"] for l in normalise(levels)]


def _as_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(str(v)).date()
    except Exception:
        return None


def _trigger_row(d: pd.DataFrame, level: float, since: Optional[date]) -> Optional[pd.Series]:
    """The first session at or after ``since`` whose high/low bracket the level."""
    frame = d
    if since is not None:
        frame = d[d["date"] >= since]
    hit = frame[(frame["low"] <= level) & (frame["high"] >= level)]
    return hit.iloc[0] if len(hit) else None


def evaluate(d: pd.DataFrame, raw_levels, research_date, ltp: Optional[float],
             touch_pct: float = 0.25, day: Optional[dict] = None) -> dict:
    """The state of every research level, and the P&L of the ones that have triggered."""
    levels = normalise(raw_levels)
    out = {"levels": levels, "rows": [], "touched": False, "touched_today": False,
           "touched_today_levels": [], "tolerance_pct": float(touch_pct or 0.25),
           "triggered_count": 0, "tracked_count": sum(1 for l in levels if l["track"])}
    if not levels:
        return out
    since = _as_date(research_date)
    today = date.today()
    day = day or {}
    day_low, day_high = float(day.get("low") or 0), float(day.get("high") or 0)
    px = float(ltp) if ltp else (float(d["close"].iloc[-1]) if len(d) else None)

    rows = []
    for lv in levels:
        level = lv["price"]
        row = {"level": level, "track": lv["track"], "triggered": False}
        # the target and stop are part of the plan, so they show whether or not it has triggered
        if lv.get("target"):
            row["target"] = lv["target"]
            row["target_pct"] = round((lv["target"] - level) / level * 100, 2)
        if lv.get("stop"):
            row["stop"] = lv["stop"]
            row["stop_pct"] = round((lv["stop"] - level) / level * 100, 2)
        if px:
            row["distance_pct"] = round((px - level) / level * 100, 2)
            row["side"] = "above" if px > level else "below" if px < level else "at"
            row["near"] = abs(row["distance_pct"]) <= out["tolerance_pct"]
        if day_low and day_high and day_low <= level <= day_high:
            out["touched_today_levels"].append(level)
        if len(d):
            hit = _trigger_row(d, level, since)
            if hit is not None:
                t_date = _as_date(hit["date"])
                after = d[d["date"] >= t_date]
                high, low = float(after["high"].max()), float(after["low"].min())
                row.update({
                    "triggered": True,
                    "triggered_on": t_date.isoformat(),
                    "days_since": (today - t_date).days,
                    "sessions_since": int(len(after)),
                    "pnl_pct": round((px - level) / level * 100, 2) if px else None,
                    "pnl_per_share": round(px - level, 2) if px else None,
                    "max_gain_pct": round((high - level) / level * 100, 2),
                    "max_drawdown_pct": round((low - level) / level * 100, 2),
                    "high_since": round(high, 2), "low_since": round(low, 2),
                })
                row.update(_exit_state(row, lv, after, px))
                out["triggered_count"] += 1
        rows.append(row)

    out["rows"] = rows
    out["touched"] = any(r.get("near") for r in rows)
    out["touched_today"] = bool(out["touched_today_levels"])
    nearest = min((r for r in rows if r.get("distance_pct") is not None),
                  key=lambda r: abs(r["distance_pct"]), default=None)
    out["nearest"] = nearest

    tracked_hits = [r for r in rows if r["triggered"] and r["track"]]
    tracked_hits.sort(key=lambda r: r["triggered_on"])
    if tracked_hits:
        out["primary"] = tracked_hits[0]
        out["extra"] = tracked_hits[1:]
        out["pnl_pct"] = out["primary"]["pnl_pct"]
        out["best_pnl_pct"] = max((r["pnl_pct"] for r in tracked_hits if r["pnl_pct"] is not None),
                                  default=None)
        out["status"] = "triggered"
    else:
        waiting = [r for r in rows if r["track"] and not r["triggered"]]
        out["primary"] = None
        out["extra"] = []
        out["status"] = "waiting" if waiting else ("untracked" if rows else "none")
        if waiting:
            closest = min(waiting, key=lambda r: abs(r.get("distance_pct") or 9e9))
            out["waiting_for"] = closest["level"]
            out["waiting_distance_pct"] = closest.get("distance_pct")
    return out


def _exit_state(row: dict, lv: dict, after, px: Optional[float]) -> dict:
    """Where the trade stands against the target and stop you wrote down for this level."""
    level = lv["price"]
    target, stop = lv.get("target"), lv.get("stop")
    out: dict = {"state": "running"}
    if target:
        reached = after[after["high"] >= target]
        out["target_hit"] = bool(len(reached))
        out["target_hit_on"] = _as_date(reached.iloc[0]["date"]).isoformat() if len(reached) else None
        if px:
            span = target - level
            out["progress_pct"] = round(max(0.0, min(150.0, (px - level) / span * 100)), 1) if span else None
    if stop:
        broken = after[after["low"] <= stop]
        out["stop_hit"] = bool(len(broken))
        out["stop_hit_on"] = _as_date(broken.iloc[0]["date"]).isoformat() if len(broken) else None
    # whichever came first decides how the trade reads
    t_on, s_on = out.get("target_hit_on"), out.get("stop_hit_on")
    if t_on and (not s_on or t_on <= s_on):
        out["state"] = "target reached"
    elif s_on:
        out["state"] = "stopped out"
    return out


def summary_line(watch: dict) -> Optional[str]:
    """One plain sentence for the table's Watch column."""
    p = watch.get("primary")
    if p:
        n = len(watch.get("extra") or [])
        more = f" (+{n} more level{'s' if n > 1 else ''} hit)" if n else ""
        word = "up" if (p.get("pnl_pct") or 0) >= 0 else "down"
        state = p.get("state")
        if state == "target reached":
            tail = f" · target {p['target']:g} reached on {p['target_hit_on']}"
        elif state == "stopped out":
            tail = f" · stopped out at {p['stop']:g} on {p['stop_hit_on']}"
        elif p.get("progress_pct") is not None:
            tail = f" · {p['progress_pct']:.0f}% of the way to {p['target']:g}"
        else:
            tail = ""
        return (f"Level {p['level']:g} hit on {p['triggered_on']} · {word} "
                f"{abs(p.get('pnl_pct') or 0):.1f}% in {p['days_since']} days{more}{tail}")
    if watch.get("status") == "waiting" and watch.get("waiting_for") is not None:
        d = watch.get("waiting_distance_pct")
        if d is None:
            return f"Waiting for {watch['waiting_for']:g}"
        return (f"{abs(d):.1f}% {'above' if d > 0 else 'below'} your {watch['waiting_for']:g} level"
                if abs(d) > 0.05 else f"At your {watch['waiting_for']:g} level")
    return None
