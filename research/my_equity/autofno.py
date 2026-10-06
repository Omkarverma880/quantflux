"""
Buy one ATM call when a research level triggers — opt-in, per stock.

This is the only part of My Equity Workspace that can spend money on its own, so every gate is
in one place and stated plainly:

  MASTER SWITCH ``auto_fno_enabled`` in the workspace settings. Off by default; while it is
                off nothing buys, whatever an individual stock says.
  OPT-IN        the stock's ``auto_fno`` flag is off until you turn it on, per stock.
  F&O ONLY      the stock must actually have listed options.
  ONCE A LEVEL  one buy per level per day, recorded in ``alert_state`` so a restart cannot
                double-fire it.
  MARKET HOURS  nothing fires outside the session.
  APP GATES     the order still goes through ``/manual`` order path, which refuses when the app
                is in paper mode or the risk fence is down.

The exit is deliberately NOT automated. You close it yourself, then record it on the level with
the booking dialog, which re-arms the level for its next touch.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from core.logger import get_logger
from research.my_equity import store as ST

logger = get_logger("research.my_equity.autofno")

SESSION_OPEN = 9 * 60 + 15
SESSION_CLOSE = 15 * 60 + 20          # leave the last few minutes alone
DEFAULT_LOTS = 1


def _minute(now: Optional[datetime] = None) -> int:
    n = now or datetime.now()
    return n.hour * 60 + n.minute


def in_session(now: Optional[datetime] = None) -> bool:
    m = _minute(now)
    return SESSION_OPEN <= m <= SESSION_CLOSE


def master_on(db, user_id: int) -> bool:
    """The workspace-wide tick box. Any doubt reads as off."""
    try:
        from research.my_equity import alerts as AL
        return bool(AL.load_config(db, user_id).get("auto_fno_enabled"))
    except Exception as exc:
        logger.debug("auto-fno: master switch unreadable, treating as off: %s", exc)
        return False


def _key(level: float, day: str) -> str:
    return f"autofno:{level:g}:{day}"


def already_fired(row, level: float, day: str) -> bool:
    return bool((getattr(row, "alert_state", None) or {}).get(_key(level, day)))


def _mark_fired(db, row, level: float, day: str, detail: dict) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    state = dict(getattr(row, "alert_state", None) or {})
    state[_key(level, day)] = detail
    row.alert_state = state
    flag_modified(row, "alert_state")
    db.commit()


def levels_triggering_today(r: dict) -> list[dict]:
    """Levels that touched today, each with the direction you gave it.

    A LONG level buys a call, a SHORT level buys a put. The direction is the level's, not the
    stock's, so one stock can hold both a long idea and a short one.
    """
    watch = r.get("watch") or {}
    hit = {float(x) for x in (watch.get("touched_today_levels") or [])}
    by_level = {float(row["level"]): row for row in (watch.get("rows") or []) if row.get("level") is not None}
    return [{"level": p, "side": str((by_level.get(p) or {}).get("trade_side") or "LONG").upper()}
            for p in sorted(hit)]


def check(db, user_id: int, svc, rows: Optional[list] = None,
          now: Optional[datetime] = None) -> list[dict]:
    """One pass. Returns what it did, which the caller logs; never raises into the loop."""
    done: list[dict] = []
    if svc is None or svc.broker is None or not in_session(now):
        return done
    if not master_on(db, user_id):            # one tick box disarms the whole workspace
        return done
    day = str((now or datetime.now()).date())
    try:
        table = rows if rows is not None else (svc.rows(db, user_id, refresh=False).get("rows") or [])
    except Exception as exc:
        logger.debug("auto-fno: could not read the table: %s", exc)
        return done

    for r in table:
        if not r.get("auto_fno") or not r.get("fno"):
            continue
        hits = levels_triggering_today(r)
        if not hits:
            continue
        stock = ST.get(db, user_id, r["id"])
        if stock is None:
            continue
        for hit in hits:
            level, side = hit["level"], hit["side"]
            if already_fired(stock, level, day):
                continue
            try:
                out = _buy_atm(db, user_id, svc, stock, r, level, side, day)
            except Exception as exc:
                logger.error("auto-fno: %s level %s failed: %s", r.get("symbol"), level, exc)
                out = {"ok": False, "error": str(exc)[:200]}
            _mark_fired(db, stock, level, day, {"at": datetime.now().strftime("%H:%M:%S"), **out})
            done.append({"symbol": r.get("symbol"), "level": level, "side": side, **out})
    return done


def _buy_atm(db, user_id: int, svc, stock, r: dict, level: float, side: str, day: str) -> dict:
    """Resolve the ATM option this level calls for and send one lot through the manual desk.

    LONG buys the call, SHORT buys the put. Either way it is a BUY — a long option, so the most
    that can be lost is the premium.
    """
    kind = "pe" if side == "SHORT" else "ce"
    chain = svc.option_chain(r["symbol"], float(r.get("ltp") or 0), around=1)
    if not chain.get("fno"):
        return {"ok": False, "error": chain.get("reason") or "no chain"}
    atm = next((c for c in chain["chain"] if c.get("atm")), None)
    ce = (atm or {}).get(kind)
    if not ce:
        return {"ok": False, "error": f"no ATM {kind.upper()} listed"}
    lot = int(ce.get("lot_size") or chain.get("lot_size") or 0)
    if lot <= 0:
        return {"ok": False, "error": "unknown lot size"}

    from config import settings
    if not settings.TRADING_ENABLED or settings.PAPER_TRADE:
        return {"ok": False, "paper": True, "symbol": ce["symbol"], "qty": lot * DEFAULT_LOTS,
                "error": "app is in paper mode — nothing sent"}
    from core.risk_fence import is_trading_blocked
    blocked, reason = is_trading_blocked(user_id)
    if blocked:
        return {"ok": False, "error": f"risk fence: {reason}"}

    from core.broker import Exchange, OrderRequest, OrderSide, OrderType, ProductType
    req = OrderRequest(tradingsymbol=ce["symbol"], exchange=Exchange.NFO,
                       side=OrderSide.BUY, quantity=lot * DEFAULT_LOTS,
                       order_type=OrderType.MARKET, product=ProductType.NRML)
    res = svc.broker.place_order(req)
    oid = getattr(res, "order_id", None) or ((res or {}).get("order_id") if res else None)
    out = {"ok": bool(oid), "order_id": oid, "symbol": ce["symbol"], "strike": atm["strike"],
           "option_type": kind.upper(), "lots": DEFAULT_LOTS, "qty": lot * DEFAULT_LOTS,
           "entry": ce.get("ltp"), "expiry": chain.get("expiry")}
    if not oid:
        out["error"] = "no order id returned"
    _record(db, user_id, r, level, out, day)
    logger.info("auto-fno: %s %s level %s -> bought %s (%s)",
                r["symbol"], side, level, ce["symbol"], oid)
    return out


def _record(db, user_id: int, r: dict, level: float, out: dict, day: str) -> None:
    """A trade_logs row, so the buy survives a restart and shows up with everything else."""
    try:
        from core.models import TradeLog
        db.add(TradeLog(
            user_id=user_id, strategy_name="my_equity_autofno",
            trade_date=date.fromisoformat(day), signal="BUY",
            option_symbol=out.get("symbol"), atm_strike=int(float(out.get("strike") or 0)),
            entry_price=out.get("entry"), lot_size=out.get("qty"),
            extra={"stock": r.get("symbol"), "level": level, "lots": out.get("lots"),
                   "option_type": out.get("option_type"),
                   "expiry": out.get("expiry"), "order_id": out.get("order_id"),
                   "exit": "manual — close it yourself, then book it on the level"}))
        db.commit()
    except Exception as exc:
        logger.warning("auto-fno: could not record the buy: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
