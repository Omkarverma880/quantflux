"""
Telegram alerts for My Equity Workspace.

A watchlist that waits to be visited is a spreadsheet. This module runs inside the existing
per-user background loop during market hours and tells you the three things you would otherwise
have to sit and watch for:

  * price has reached a research level you are waiting for,
  * a triggered level has run to the target you wrote down,
  * or it has broken the stop you wrote down.

Plus two digests: what is close this morning, and what happened by the close.

Every alert fires **once per level per day** — the state is stored on the stock row, so a
restart or a second worker cannot produce a burst of repeats. Alerts are off for a stock the
moment you switch them off, and the whole thing is silent when Telegram is not configured.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, time as dtime
from typing import Optional

from core.logger import get_logger
from core.models import StrategyConfig
from research.my_equity import store as ST

logger = get_logger("research.my_equity.alerts")

CONFIG_NAME = "my_equity_alerts"
DEFAULT_CONFIG = {
    "enabled": False,               # opt in, so nobody is surprised by a message
    "bot": "a",
    "level_alerts": True,           # price reached a level you are waiting for
    "exit_alerts": True,            # a triggered level hit its target or stop
    "morning_digest": True,
    "morning_at": "09:20",
    "close_digest": True,
    "close_at": "15:35",
    "near_pct": 2.0,                # "close to a level" in the morning digest
}
CHECK_EVERY_S = 30.0

_last_check: dict[int, float] = {}
_digest_done: dict[str, str] = {}       # f"{user}:{kind}" -> date already sent
_lock = threading.Lock()


# ── config ───────────────────────────────────────────────────────────
def load_config(db, user_id: int) -> dict:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    cfg = {**DEFAULT_CONFIG, **((row.config or {}) if row else {})}
    cfg["near_pct"] = max(0.1, min(float(cfg.get("near_pct") or 2.0), 20.0))
    for key in ("morning_at", "close_at"):
        cfg[key] = str(cfg.get(key) or DEFAULT_CONFIG[key])[:5]
    return cfg


def save_config(db, user_id: int, updates: dict) -> dict:
    cfg = {**load_config(db, user_id),
           **{k: v for k, v in (updates or {}).items() if k in DEFAULT_CONFIG}}
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        db.add(StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=cfg))
    else:
        row.config = cfg
    db.commit()
    return load_config(db, user_id)


# ── sending ──────────────────────────────────────────────────────────
def _send(text: str, bot: str) -> bool:
    try:
        from core import notify
        if not notify.enabled(bot):
            return False
        return bool(notify.send(text, bot=bot).get("ok"))
    except Exception as exc:
        logger.debug("telegram send failed: %s", exc)
        return False


def telegram_ready(bot: str = "a") -> bool:
    try:
        from core import notify
        return notify.enabled(bot)
    except Exception:
        return False


def _fired(row, key: str, today: str) -> bool:
    return (row.alert_state or {}).get(key) == today


def _mark(db, row, key: str, today: str) -> None:
    state = dict(row.alert_state or {})
    state[key] = today
    # keep the file small: only today's and yesterday's keys matter
    row.alert_state = {k: v for k, v in state.items() if v >= today} or {key: today}
    db.commit()


def _money(v: Optional[float]) -> str:
    return "—" if v is None else f"{float(v):,.2f}"


# ── the live check ───────────────────────────────────────────────────
def check(db, user_id: int, svc) -> list[str]:
    """One pass over the workspace; returns the alerts that were sent."""
    cfg = load_config(db, user_id)
    if not cfg["enabled"] or not telegram_ready(cfg["bot"]):
        return []
    today = date.today().isoformat()
    sent: list[str] = []
    try:
        data = svc.rows(db, user_id, refresh=True)
    except Exception as exc:
        logger.debug("alerts: rows failed: %s", exc)
        return []
    by_id = {s.id: s for s in ST.list_stocks(db, user_id)}
    for r in data.get("rows", []):
        stock = by_id.get(r.get("id"))
        if stock is None or not getattr(stock, "alerts_on", True) or not r.get("history"):
            continue
        watch = r.get("watch") or {}
        for lv in watch.get("rows", []):
            level = lv["level"]
            # three things are worth a message, and the day's dedupe key caps them at one:
            #   reached today · came back to a level it triggered on earlier · sitting on it
            reached_today = bool(lv.get("triggered") and lv.get("triggered_on") == today)
            back_at_it = bool(lv.get("near") and lv.get("triggered") and not reached_today)
            waiting_at_it = bool(lv.get("near") and not lv.get("triggered"))
            if cfg["level_alerts"] and (reached_today or back_at_it or waiting_at_it):
                key = f"touch:{level}"
                if not _fired(stock, key, today):
                    verb = "reached" if reached_today else "is back at" if back_at_it else "is at"
                    if _send(_touch_text(r, lv, verb), cfg["bot"]):
                        _mark(db, stock, key, today)
                        sent.append(f"{r['symbol']} {verb} {level}")
            if not cfg["exit_alerts"] or not lv.get("triggered"):
                continue
            if lv.get("target_hit") and lv.get("target_hit_on") == today:
                key = f"target:{level}"
                if not _fired(stock, key, today) and _send(_exit_text(r, lv, True), cfg["bot"]):
                    _mark(db, stock, key, today)
                    sent.append(f"{r['symbol']} target {lv.get('target')}")
            if lv.get("stop_hit") and lv.get("stop_hit_on") == today:
                key = f"stop:{level}"
                if not _fired(stock, key, today) and _send(_exit_text(r, lv, False), cfg["bot"]):
                    _mark(db, stock, key, today)
                    sent.append(f"{r['symbol']} stop {lv.get('stop')}")
    return sent


def _touch_text(r: dict, lv: dict, verb: str = "is at") -> str:
    bits = [f"🎯 <b>{r['symbol']}</b> {verb} your research level <b>{lv['level']:g}</b>",
            f"Last trade {_money(r.get('ltp'))} ({r.get('change_pct', 0):+.2f}% today)"]
    if lv.get("target"):
        bits.append(f"Your target {lv['target']:g} · stop {lv.get('stop', '—')}")
    if r.get("rsi") is not None:
        bits.append(f"RSI {r['rsi']:.0f} · researched {r.get('added_on')}")
    if r.get("note"):
        bits.append(f"<i>{r['note'][:160]}</i>")
    return "\n".join(bits)


def _exit_text(r: dict, lv: dict, target: bool) -> str:
    if target:
        head = f"✅ <b>{r['symbol']}</b> reached your target <b>{lv['target']:g}</b>"
    else:
        head = f"🛑 <b>{r['symbol']}</b> broke your stop <b>{lv['stop']:g}</b>"
    return "\n".join([
        head,
        f"Entry level {lv['level']:g} on {lv.get('triggered_on')} · {lv.get('days_since')} days",
        f"Now {_money(r.get('ltp'))} ({lv.get('pnl_pct', 0):+.2f}% from the level)",
    ])


# ── digests ──────────────────────────────────────────────────────────
def _hhmm(value: str) -> dtime:
    try:
        h, m = str(value).split(":")[:2]
        return dtime(int(h), int(m))
    except Exception:
        return dtime(9, 20)


def digests(db, user_id: int, svc, now: Optional[datetime] = None) -> list[str]:
    """Send the morning and closing digests once each, when their time has passed."""
    cfg = load_config(db, user_id)
    if not cfg["enabled"] or not telegram_ready(cfg["bot"]):
        return []
    now = now or datetime.now()
    today = now.date().isoformat()
    out = []
    for kind, on_key, at_key, builder in (("morning", "morning_digest", "morning_at", morning_text),
                                          ("close", "close_digest", "close_at", close_text)):
        if not cfg[on_key] or now.time() < _hhmm(cfg[at_key]):
            continue
        mark = f"{user_id}:{kind}"
        with _lock:
            if _digest_done.get(mark) == today:
                continue
            _digest_done[mark] = today          # claim it before sending, so two ticks cannot race
        try:
            text = builder(svc.rows(db, user_id, refresh=True), cfg)
        except Exception as exc:
            logger.debug("digest build failed: %s", exc)
            with _lock:
                _digest_done.pop(mark, None)
            continue
        if text and _send(text, cfg["bot"]):
            out.append(kind)
    return out


def morning_text(data: dict, cfg: dict) -> Optional[str]:
    rows = [r for r in data.get("rows", []) if r.get("history")]
    if not rows:
        return None
    near, running = [], []
    for r in rows:
        w = r.get("watch") or {}
        if w.get("primary"):
            p = w["primary"]
            running.append(f"• {r['symbol']} {p['pnl_pct']:+.1f}% from {p['level']:g}"
                           + (f" · {p['progress_pct']:.0f}% to target" if p.get("progress_pct") is not None else ""))
            continue
        d = w.get("waiting_distance_pct")
        if d is not None and abs(d) <= cfg["near_pct"]:
            near.append(f"• {r['symbol']} {_money(r.get('ltp'))} — {abs(d):.1f}% "
                        f"{'above' if d > 0 else 'below'} {w.get('waiting_for'):g}")
    bits = [f"☀️ <b>My Equity Workspace</b> — {date.today():%d %b}"]
    bits.append(f"{len(rows)} stocks · {sum(1 for r in rows if (r.get('watch') or {}).get('primary'))} triggered")
    bits.append("")
    bits.append(f"<b>Within {cfg['near_pct']:g}% of a level</b>" if near else "No level is close today.")
    bits.extend(near[:12])
    if running:
        bits.append("")
        bits.append("<b>Running</b>")
        bits.extend(running[:12])
    return "\n".join(bits)


def close_text(data: dict, cfg: dict) -> Optional[str]:
    rows = [r for r in data.get("rows", []) if r.get("history")]
    if not rows:
        return None
    today = date.today().isoformat()
    hit_today, targets, stops = [], [], []
    for r in rows:
        for lv in (r.get("watch") or {}).get("rows", []):
            if lv.get("triggered_on") == today:
                hit_today.append(f"• {r['symbol']} reached {lv['level']:g}")
            if lv.get("target_hit_on") == today:
                targets.append(f"• {r['symbol']} target {lv['target']:g} ({lv.get('pnl_pct', 0):+.1f}%)")
            if lv.get("stop_hit_on") == today:
                stops.append(f"• {r['symbol']} stopped at {lv['stop']:g} ({lv.get('pnl_pct', 0):+.1f}%)")
    s = data.get("summary") or {}
    bits = [f"🌙 <b>Workspace close</b> — {date.today():%d %b}"]
    if s.get("avg_pnl_pct") is not None:
        bits.append(f"{s.get('triggered', 0)} triggered · average {s['avg_pnl_pct']:+.2f}%"
                    + (f" · best {s['best'][0]} {s['best'][1]:+.1f}%" if s.get("best") else ""))
    for title, group in (("Levels reached today", hit_today), ("Targets", targets), ("Stops", stops)):
        if group:
            bits.append("")
            bits.append(f"<b>{title}</b>")
            bits.extend(group[:10])
    if not (hit_today or targets or stops):
        bits.append("")
        bits.append("Nothing reached a level today.")
    return "\n".join(bits)


# ── the background hook ──────────────────────────────────────────────
def tick(db, user_id: int, svc, now: Optional[datetime] = None) -> dict:
    """Called from the per-user background loop; rate-limited internally."""
    import time as _t
    now = now or datetime.now()
    if now.weekday() >= 5:
        return {"skipped": "weekend"}
    last = _last_check.get(user_id, 0.0)
    if _t.time() - last < CHECK_EVERY_S:
        return {"skipped": "too soon"}
    _last_check[user_id] = _t.time()
    sent = check(db, user_id, svc) if dtime(9, 15) <= now.time() <= dtime(15, 30) else []
    digested = digests(db, user_id, svc, now)
    return {"alerts": sent, "digests": digested}
