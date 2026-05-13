"""
Hard auto-square-off fence.

Independent of any individual strategy: at the configured cutoff time
(default 15:15 IST), iterates every user with a Zerodha session for today
and force-exits all MIS option positions (NFO/BFO + product == MIS).

Equity holdings (CNC) are *never* touched — only intraday option legs.

Configurable via env var AUTO_SQUARE_OFF_TIME (HH:MM, default '15:15').
"""
from __future__ import annotations

import os
import asyncio
from datetime import datetime, time as dtime, date

from core.logger import get_logger

logger = get_logger("auto_squareoff")

OPTION_EXCHANGES = {"NFO", "BFO", "CDS", "MCX"}


def _parse_cutoff() -> dtime:
    raw = os.getenv("AUTO_SQUARE_OFF_TIME", "15:15").strip()
    try:
        hh, mm = raw.split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        logger.warning("Bad AUTO_SQUARE_OFF_TIME=%r — using 15:15", raw)
        return dtime(15, 15)


CUTOFF = _parse_cutoff()
WINDOW_END = dtime(15, 28)  # don't keep retrying after market close margin


def _squareoff_user(user_id: int) -> dict:
    """Square off all MIS option positions for one user. Returns summary."""
    from core.database import get_db_session
    from core.broker import (
        get_user_broker, OrderRequest, Exchange, OrderSide, OrderType, ProductType,
    )
    db = get_db_session()
    summary = {"user_id": user_id, "exited": [], "errors": []}
    try:
        broker = get_user_broker(db, user_id)
        try:
            positions = broker.get_positions() or []
        except Exception as exc:
            summary["errors"].append({"stage": "get_positions", "error": str(exc)})
            return summary

        for p in positions:
            qty = int(getattr(p, "quantity", 0) or 0)
            if qty == 0:
                continue
            exch = (p.exchange or "").upper()
            prod = (p.product or "").upper()
            # ONLY MIS option positions — never CNC, never equity holdings
            if prod != "MIS" or exch not in OPTION_EXCHANGES:
                continue
            try:
                req = OrderRequest(
                    tradingsymbol=p.tradingsymbol,
                    exchange=Exchange(p.exchange),
                    side=OrderSide.SELL if qty > 0 else OrderSide.BUY,
                    quantity=abs(qty),
                    order_type=OrderType.MARKET,
                    product=ProductType(p.product),
                    tag="AUTO315",
                )
                resp = broker.place_order(req)
                summary["exited"].append({
                    "tradingsymbol": p.tradingsymbol,
                    "quantity": qty,
                    "order_id": getattr(resp, "order_id", None),
                })
                logger.warning("[auto-squareoff] user=%s exited %s qty=%d",
                               user_id, p.tradingsymbol, qty)
            except Exception as exc:
                summary["errors"].append({
                    "tradingsymbol": p.tradingsymbol, "error": str(exc),
                })
                logger.error("[auto-squareoff] %s exit failed: %s",
                             p.tradingsymbol, exc)

        # Also clear any manual SL/TGT monitor entries for this user
        try:
            from app.routes.manual_trading_routes import _monitor as _mt
            for sym, t in list(_mt._trades.items()):
                if t.get("user_id") == user_id:
                    _mt.unregister(sym)
        except Exception:
            pass
    finally:
        db.close()
    return summary


def _run_for_all_users() -> list[dict]:
    """Iterate active users for today and run squareoff for each."""
    from core.database import get_db_session
    from core.models import ZerodhaSession
    db = get_db_session()
    try:
        sessions = db.query(ZerodhaSession).filter(
            ZerodhaSession.login_date == date.today()
        ).all()
        uids = [s.user_id for s in sessions]
    except Exception as exc:
        logger.error("[auto-squareoff] active-user query failed: %s", exc)
        uids = []
    finally:
        db.close()
    out = []
    for uid in uids:
        try:
            out.append(_squareoff_user(uid))
        except Exception as exc:
            logger.exception("[auto-squareoff] user %s failed: %s", uid, exc)
    return out


# Track last-fired date so we only fire once per trading day
_last_fired_date: date | None = None


async def fence_loop(interval_seconds: int = 30):
    """Background coroutine — fires at CUTOFF once per trading day."""
    global _last_fired_date
    logger.info("Auto-squareoff fence armed for %s IST (interval=%ss)",
                CUTOFF.strftime("%H:%M"), interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            now = datetime.now()
            if now.weekday() >= 5:
                continue
            today = now.date()
            if _last_fired_date == today:
                continue
            if CUTOFF <= now.time() <= WINDOW_END:
                logger.warning("[auto-squareoff] FENCE FIRING at %s",
                               now.strftime("%Y-%m-%d %H:%M:%S"))
                loop = asyncio.get_running_loop()
                try:
                    results = await loop.run_in_executor(None, _run_for_all_users)
                    total_exits = sum(len(r["exited"]) for r in results)
                    total_errs = sum(len(r["errors"]) for r in results)
                    logger.warning(
                        "[auto-squareoff] complete: users=%d exits=%d errors=%d",
                        len(results), total_exits, total_errs,
                    )
                except Exception as exc:
                    logger.exception("[auto-squareoff] run failed: %s", exc)
                _last_fired_date = today
        except asyncio.CancelledError:
            logger.info("Auto-squareoff fence cancelled")
            break
        except Exception as exc:
            logger.error("[auto-squareoff] loop error: %s", exc)
            await asyncio.sleep(60)


def run_now_for_user(user_id: int) -> dict:
    """Manual trigger — exposed via /api/risk/squareoff_now."""
    return _squareoff_user(user_id)
