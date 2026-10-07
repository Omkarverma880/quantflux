"""
Market status, and which trading session the screen is showing.

The rule this file exists to enforce: the dashboard is never empty because the market is shut.
At 15:31 it stops calling itself live and starts calling itself the final picture of the day
that just ended — the same numbers, relabelled honestly. Overnight and at the weekend it keeps
showing the last completed session. The next morning it opens a new one.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from research.institutional_flow.sources import now_ist

OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
PRE_OPEN_MIN = 9 * 60          # the pre-open session starts here

LIVE = "LIVE"
CLOSED = "CLOSED"
PRE_OPEN = "PRE_OPEN"
WEEKEND = "WEEKEND"
HOLIDAY = "HOLIDAY"

# NSE trading holidays. Kept here deliberately rather than guessed from a weekday rule, and
# treated as "best known" — a date missing from this list only means the screen says CLOSED a
# little later than it could, never that it invents data.
HOLIDAYS_2026 = {
    "2026-01-26", "2026-03-04", "2026-03-25", "2026-04-01", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-08-15", "2026-09-14", "2026-10-02",
    "2026-10-21", "2026-11-09", "2026-12-25",
}


def _minute(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS_2026


def previous_trading_day(d: date) -> date:
    p = d - timedelta(days=1)
    for _ in range(12):                      # a long weekend plus holidays, never unbounded
        if is_trading_day(p):
            return p
        p -= timedelta(days=1)
    return p


def market_status(at: Optional[datetime] = None) -> dict:
    """Where we are in the trading day, and what the screen should therefore be showing.

    ``session_date`` is the session whose data belongs on screen: today while the market is
    open or has just closed, and the last completed trading day at every other time. It is
    never None, which is what stops the dashboard going blank out of hours.
    """
    n = at or now_ist()
    today, m = n.date(), _minute(n)

    if not is_trading_day(today):
        why = WEEKEND if today.weekday() >= 5 else HOLIDAY
        prev = previous_trading_day(today)
        return {
            "status": why, "live": False, "tone": "closed",
            "label": "MARKET CLOSED — WEEKEND" if why == WEEKEND else "MARKET CLOSED — HOLIDAY",
            "session_date": prev.isoformat(), "is_today": False,
            "now": n.isoformat(timespec="seconds"),
            "detail": f"Latest completed session: {prev.strftime('%d %b %Y')}",
            "should_poll": False,
        }

    if m < PRE_OPEN_MIN:
        prev = previous_trading_day(today)
        return {
            "status": CLOSED, "live": False, "tone": "closed",
            "label": "MARKET CLOSED — PRE-MARKET",
            "session_date": prev.isoformat(), "is_today": False,
            "now": n.isoformat(timespec="seconds"),
            "detail": f"Latest completed session: {prev.strftime('%d %b %Y')} · "
                      f"today's session opens at 09:15",
            "should_poll": False,
        }

    if m < OPEN_MIN:
        return {
            "status": PRE_OPEN, "live": False, "tone": "delayed",
            "label": "PRE-OPEN SESSION",
            "session_date": today.isoformat(), "is_today": True,
            "now": n.isoformat(timespec="seconds"),
            "detail": "Pre-open auction — continuous trading begins at 09:15",
            "should_poll": True,
        }

    if m <= CLOSE_MIN:
        return {
            "status": LIVE, "live": True, "tone": "live",
            "label": "MARKET OPEN — LIVE DATA",
            "session_date": today.isoformat(), "is_today": True,
            "now": n.isoformat(timespec="seconds"),
            "detail": f"Current session: {today.strftime('%d %b %Y')}",
            "should_poll": True,
        }

    return {
        "status": CLOSED, "live": False, "tone": "closed",
        "label": "MARKET CLOSED — FINAL SESSION DATA",
        "session_date": today.isoformat(), "is_today": True,
        "now": n.isoformat(timespec="seconds"),
        "detail": f"Latest completed session: {today.strftime('%d %b %Y')}",
        "should_poll": False,
    }


def data_age(retrieved_at: Optional[str], at: Optional[datetime] = None) -> dict:
    """How old a reading is, said plainly. An unlabelled timestamp invites a wrong assumption."""
    if not retrieved_at:
        return {"seconds": None, "text": "unknown"}
    try:
        then = datetime.fromisoformat(str(retrieved_at))
    except ValueError:
        return {"seconds": None, "text": "unknown"}
    secs = max(0, int(((at or now_ist()) - then).total_seconds()))
    if secs < 60:
        text = f"{secs}s ago"
    elif secs < 3600:
        text = f"{secs // 60}m ago"
    elif secs < 86400:
        text = f"{secs // 3600}h ago"
    else:
        text = f"{secs // 86400}d ago"
    return {"seconds": secs, "text": text}
