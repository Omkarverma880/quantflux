"""
Risk Fence subsystem — per-user JSON-on-disk config + background watcher.

Provides TWO independent gates:

  1) Advanced P&L Fence
     - lock_profit:  if aggregate live PnL >= +lock_profit  → trigger global squareoff
     - max_loss:     if aggregate live PnL <= -max_loss     → trigger global squareoff

  2) Day-Loss Control
     - max_day_loss: once aggregate PnL <= -max_day_loss   → triggered=True
       While triggered, all new manual + strategy orders are blocked
       and any incoming attempt also triggers a global squareoff.

State is stored on disk (no DB migration required) under
data/risk_fence/<user_id>.json — schema:

    {
      "pnl_fence":   {"enabled": bool, "lock_profit": float, "max_loss": float,
                      "triggered": bool, "triggered_at": str|null,
                      "trigger_reason": str|null, "trigger_pnl": float|null},
      "loss_control":{"enabled": bool, "max_day_loss": float,
                      "triggered": bool, "triggered_at": str|null,
                      "trigger_pnl": float|null},
      "updated_at":  iso str,
    }
"""
from __future__ import annotations

import json
import asyncio
import threading
from datetime import datetime, time as dtime, date
from pathlib import Path
from typing import Optional

from core.logger import get_logger

logger = get_logger("risk_fence")

FENCE_DIR = Path("data") / "risk_fence"
FENCE_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT = {
    "pnl_fence":    {"enabled": False, "lock_profit": 0.0, "max_loss": 0.0,
                     "triggered": False, "triggered_at": None,
                     "trigger_reason": None, "trigger_pnl": None,
                     "trigger_date": None},
    "loss_control": {"enabled": False, "max_day_loss": 0.0,
                     "triggered": False, "triggered_at": None,
                     "trigger_pnl": None, "trigger_date": None},
    "updated_at":   None,
}

_lock = threading.Lock()


# ── persistence ──────────────────────────────────────────

def _path(user_id: int) -> Path:
    return FENCE_DIR / f"{user_id}.json"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(user_id: int) -> dict:
    """Return full fence config for *user_id* (defaults if missing)."""
    p = _path(user_id)
    if not p.exists():
        return json.loads(json.dumps(_DEFAULT))
    try:
        data = json.loads(p.read_text())
    except Exception as exc:
        logger.warning("Could not load fence config for %s: %s", user_id, exc)
        return json.loads(json.dumps(_DEFAULT))
    merged = _deep_merge(json.loads(json.dumps(_DEFAULT)), data)
    # Auto-reset triggers on a new trading day
    today_iso = date.today().isoformat()
    changed = False
    for sec in ("pnl_fence", "loss_control"):
        td = merged.get(sec, {}).get("trigger_date")
        if merged[sec].get("triggered") and td and td != today_iso:
            merged[sec]["triggered"] = False
            merged[sec]["triggered_at"] = None
            merged[sec]["trigger_pnl"] = None
            merged[sec]["trigger_reason"] = None
            merged[sec]["trigger_date"] = None
            changed = True
    if changed:
        save_config(user_id, merged)
    return merged


def save_config(user_id: int, cfg: dict) -> dict:
    """Merge *cfg* into existing config and persist."""
    with _lock:
        current = load_config(user_id)
        merged = _deep_merge(current, cfg)
        merged["updated_at"] = datetime.now().isoformat()
        _path(user_id).write_text(json.dumps(merged, indent=2))
    return merged


def reset_triggers(user_id: int, section: Optional[str] = None) -> dict:
    """Clear triggered flags. *section* in (None, 'pnl_fence', 'loss_control')."""
    cfg = load_config(user_id)
    sections = ("pnl_fence", "loss_control") if section is None else (section,)
    for sec in sections:
        if sec in cfg:
            cfg[sec]["triggered"] = False
            cfg[sec]["triggered_at"] = None
            cfg[sec]["trigger_pnl"] = None
            cfg[sec]["trigger_reason"] = None
            cfg[sec]["trigger_date"] = None
    return save_config(user_id, cfg)


# ── runtime gates (called from order placement code paths) ───

def is_trading_blocked(user_id: int) -> tuple[bool, str | None]:
    """Return (blocked, reason). Blocks when loss_control or pnl_fence has fired."""
    cfg = load_config(user_id)
    if cfg["loss_control"].get("enabled") and cfg["loss_control"].get("triggered"):
        return True, (
            f"Day-Loss Control triggered "
            f"(P&L was ₹{cfg['loss_control'].get('trigger_pnl')}). "
            "Disable Loss Control on the dashboard to resume trading."
        )
    if cfg["pnl_fence"].get("enabled") and cfg["pnl_fence"].get("triggered"):
        return True, (
            f"P&L Fence triggered: {cfg['pnl_fence'].get('trigger_reason')} "
            f"(P&L was ₹{cfg['pnl_fence'].get('trigger_pnl')}). "
            "Reset the fence on the dashboard to resume trading."
        )
    return False, None


def assert_trading_allowed(user_id: int):
    """Raise HTTP 423 if blocked. Suitable for FastAPI route bodies."""
    from fastapi import HTTPException
    blocked, reason = is_trading_blocked(user_id)
    if blocked:
        raise HTTPException(status_code=423, detail=reason)


# ── trigger logic ───────────────────────────────────────

def _mark_triggered(user_id: int, section: str, reason: str, pnl: float) -> dict:
    cfg = load_config(user_id)
    cfg[section]["triggered"] = True
    cfg[section]["triggered_at"] = datetime.now().isoformat()
    cfg[section]["trigger_pnl"] = round(pnl, 2)
    cfg[section]["trigger_reason"] = reason
    cfg[section]["trigger_date"] = date.today().isoformat()
    return save_config(user_id, cfg)


# Fence scope: ONLY MIS option intraday orders/positions are touched.
# Same-day equity buys (CNC holdings, t1_quantity>0), NRML positions and
# any CNC positions are deliberately left untouched.
_OPTION_EXCHANGES = {"NFO", "BFO", "CDS", "MCX"}


def _global_squareoff_for_user(user_id: int):
    """Cancel pending MIS option orders + market-exit MIS option positions only."""
    from core.database import get_db_session
    from core.broker import (
        get_user_broker, OrderRequest, Exchange, OrderSide, OrderType, ProductType,
    )
    db = get_db_session()
    try:
        broker = get_user_broker(db, user_id)
        # Cancel pending MIS option orders
        try:
            for o in broker.get_orders() or []:
                status = str(o.get("status", "")).upper()
                if status not in ("OPEN", "TRIGGER PENDING",
                                  "MODIFY VALIDATION PENDING", "MODIFY PENDING"):
                    continue
                if str(o.get("product", "")).upper() != "MIS":
                    continue
                if str(o.get("exchange", "")).upper() not in _OPTION_EXCHANGES:
                    continue
                oid = str(o.get("order_id") or "")
                if oid:
                    try:
                        broker.cancel_order(oid)
                    except Exception as exc:
                        logger.error("[fence] cancel %s failed: %s", oid, exc)
        except Exception as exc:
            logger.error("[fence] get_orders failed: %s", exc)
        # Square off MIS option positions
        try:
            for p in broker.get_positions() or []:
                qty = int(getattr(p, "quantity", 0) or 0)
                if qty == 0:
                    continue
                if str(getattr(p, "product", "")).upper() != "MIS":
                    continue
                if str(getattr(p, "exchange", "")).upper() not in _OPTION_EXCHANGES:
                    continue
                try:
                    req = OrderRequest(
                        tradingsymbol=p.tradingsymbol,
                        exchange=Exchange(p.exchange),
                        side=OrderSide.SELL if qty > 0 else OrderSide.BUY,
                        quantity=abs(qty),
                        order_type=OrderType.MARKET,
                        product=ProductType(p.product),
                        tag="FENCE",
                    )
                    broker.place_order(req)
                    logger.warning("[fence] squared off %s qty=%d", p.tradingsymbol, qty)
                except Exception as exc:
                    logger.error("[fence] squareoff %s failed: %s", p.tradingsymbol, exc)
        except Exception as exc:
            logger.error("[fence] get_positions failed: %s", exc)
        # Clear manual SL/TGT monitor
        try:
            from app.routes.manual_trading_routes import _monitor as _mt
            for sym in list(_mt._trades.keys()):
                _mt.unregister(sym)
        except Exception:
            pass
    finally:
        db.close()


def _aggregate_pnl_for_user(user_id: int) -> Optional[float]:
    """Return intraday PnL across MIS option positions only (matches fence scope)."""
    from core.database import get_db_session
    from core.broker import get_user_broker
    db = get_db_session()
    try:
        broker = get_user_broker(db, user_id)
        positions = broker.get_positions() or []
        total = 0.0
        for p in positions:
            if str(getattr(p, "product", "")).upper() != "MIS":
                continue
            if str(getattr(p, "exchange", "")).upper() not in _OPTION_EXCHANGES:
                continue
            total += float(p.pnl or 0.0)
        return total
    except Exception as exc:
        logger.debug("[fence] pnl fetch failed for %s: %s", user_id, exc)
        return None
    finally:
        db.close()


def evaluate_user(user_id: int) -> dict:
    """Re-evaluate fence/loss state for *user_id* using live PnL.
    Returns dict with current pnl + any triggers fired."""
    cfg = load_config(user_id)
    if not (cfg["pnl_fence"].get("enabled") or cfg["loss_control"].get("enabled")):
        return {"pnl": None, "fired": []}
    pnl = _aggregate_pnl_for_user(user_id)
    if pnl is None:
        return {"pnl": None, "fired": []}
    fired: list[str] = []

    pf = cfg["pnl_fence"]
    if pf.get("enabled") and not pf.get("triggered"):
        lp = float(pf.get("lock_profit") or 0)
        ml = float(pf.get("max_loss") or 0)
        if lp > 0 and pnl >= lp:
            _mark_triggered(user_id, "pnl_fence",
                            f"Profit lock hit (≥ ₹{lp:.0f})", pnl)
            fired.append("pnl_fence_profit")
        elif ml > 0 and pnl <= -abs(ml):
            _mark_triggered(user_id, "pnl_fence",
                            f"Max loss hit (≤ -₹{abs(ml):.0f})", pnl)
            fired.append("pnl_fence_loss")

    lc = cfg["loss_control"]
    if lc.get("enabled") and not lc.get("triggered"):
        mdl = float(lc.get("max_day_loss") or 0)
        if mdl > 0 and pnl <= -abs(mdl):
            _mark_triggered(user_id, "loss_control",
                            f"Day loss limit hit (≤ -₹{abs(mdl):.0f})", pnl)
            fired.append("loss_control")

    if fired:
        logger.warning("[fence] user=%s fired=%s pnl=%.2f → squareoff",
                       user_id, fired, pnl)
        try:
            _global_squareoff_for_user(user_id)
        except Exception as exc:
            logger.exception("[fence] squareoff failed: %s", exc)
    return {"pnl": pnl, "fired": fired}


# ── background watcher ─────────────────────────────────

_MARKET_OPEN = dtime(9, 15)
_MARKET_CLOSE = dtime(15, 30)


async def watcher_loop(interval_seconds: int = 5):
    """Background coroutine — checks every active user's fence each tick."""
    logger.info("Risk-fence watcher started (interval=%ss)", interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            now = datetime.now()
            if now.weekday() >= 5:
                continue
            if not (_MARKET_OPEN <= now.time() <= _MARKET_CLOSE):
                continue

            from core.database import get_db_session
            from core.models import ZerodhaSession
            db = get_db_session()
            try:
                sessions = db.query(ZerodhaSession).filter(
                    ZerodhaSession.login_date == date.today()
                ).all()
                user_ids = [s.user_id for s in sessions]
            except Exception as exc:
                logger.debug("[fence] active-user query failed: %s", exc)
                user_ids = []
            finally:
                db.close()

            loop = asyncio.get_running_loop()
            for uid in user_ids:
                try:
                    await loop.run_in_executor(None, evaluate_user, uid)
                except Exception as exc:
                    logger.debug("[fence] eval %s failed: %s", uid, exc)
        except asyncio.CancelledError:
            logger.info("Risk-fence watcher cancelled")
            break
        except Exception as exc:
            logger.error("[fence] watcher loop error: %s", exc)
            await asyncio.sleep(15)
