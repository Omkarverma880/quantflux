"""
OPEI paper-position manager (Research-9) — PAPER ONLY, never places an order.

Tracks the FIRST recommended label per side per day (the first CE and the first
PE the engine best-recommends), in two sections:

  • Section 1 — no SL / no target; squared off at the configured IST time.
  • Section 2 — highest target (or configured target) + SL; optional re-entry
    after a target/SL exit.

Driven off the live OPEI snapshot each refresh (same place outcomes are tracked).
Entry is taken at the recommended best-level price; P&L is marked against the
live premium. No noise: only one open position per (side, section) at a time.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime

from core.logger import get_logger
from core.models import OPEIPaperPosition

logger = get_logger("research.opei.positions")


def _hhmm(s: str) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(15, 15)


def _best_level(sd: dict):
    levels = sd.get("levels") or []
    return next((l for l in levels if l.get("is_best")), levels[0] if levels else None)


def _sec2_target(entry: float, best: dict, cfg: dict) -> float:
    mode = cfg.get("sec2_target_mode", "highest")
    if mode == "percent":
        return round(entry * (1 + float(cfg.get("sec2_target_value", 10)) / 100.0), 2)
    if mode == "points":
        return round(entry + float(cfg.get("sec2_target_value", 10)), 2)
    tgts = best.get("targets") or []
    return round(max(tgts), 2) if tgts else round(entry * 1.1, 2)     # highest


def _sec2_sl(entry: float, best: dict, cfg: dict):
    mode = cfg.get("sec2_sl_mode", "recommended")
    if mode == "percent":
        return round(entry * (1 - float(cfg.get("sec2_sl_value", 5)) / 100.0), 2)
    if mode == "points":
        return round(entry - float(cfg.get("sec2_sl_value", 5)), 2)
    sl = best.get("sl")
    return round(float(sl), 2) if sl else round(entry * 0.95, 2)      # recommended


def _mark(pos: OPEIPaperPosition, premium: float):
    qty = int(pos.qty or 0)
    pnl = round((premium - float(pos.entry_price)) * qty, 2)
    pos.ltp = round(premium, 2)
    pos.pnl = pnl
    pos.mfe = round(max(float(pos.mfe or 0), pnl), 2)
    pos.mae = round(min(float(pos.mae or 0), pnl), 2)


def _close(pos: OPEIPaperPosition, price: float, reason: str, now: datetime):
    _mark(pos, price)
    pos.status = reason
    pos.exit_price = round(price, 2)
    pos.exit_reason = reason
    pos.exit_time = now.strftime("%H:%M:%S")


def _create(db, user_id, today, section, side, sd, entry, target, sl, qty, now) -> OPEIPaperPosition:
    pos = OPEIPaperPosition(
        user_id=user_id, trade_date=today, section=section, side=side,
        symbol=sd.get("symbol"), strike=sd.get("strike"), qty=qty,
        entry_price=round(entry, 2), entry_time=now.strftime("%H:%M:%S"),
        target=round(target, 2) if target is not None else None,
        sl=round(sl, 2) if sl is not None else None,
        ltp=round(entry, 2), mfe=0, mae=0, pnl=0, status="OPEN")
    db.add(pos)
    return pos


def manage_positions(db, user_id: int, snap: dict, cfg: dict, now: datetime) -> int:
    """Advance paper positions from one OPEI snapshot. Returns rows changed."""
    if not cfg.get("positions_enabled", True):
        return 0
    today = now.date()
    squareoff = _hhmm(cfg.get("squareoff_time", "15:15"))
    past_squareoff = now.time() >= squareoff
    qty = int(cfg.get("position_qty", 75) or 75)
    changed = 0

    for side in ("CE", "PE"):
        sd = (snap.get("sides") or {}).get(side)
        if not sd:
            continue
        premium = float(sd.get("premium") or 0)
        if premium <= 0:
            continue
        best = _best_level(sd)

        rows = (db.query(OPEIPaperPosition)
                  .filter(OPEIPaperPosition.user_id == user_id,
                          OPEIPaperPosition.trade_date == today,
                          OPEIPaperPosition.side == side)
                  .order_by(OPEIPaperPosition.id).all())
        s1 = [r for r in rows if r.section == 1]
        s2 = [r for r in rows if r.section == 2]

        # ── Section 1: first label of the day; no SL/target; square off ──
        open1 = next((r for r in s1 if r.status == "OPEN"), None)
        if not s1 and best:                       # first recommendation → enter
            _create(db, user_id, today, 1, side, sd, best["level"], None, None, qty, now)
            changed += 1
        elif open1:
            _mark(open1, premium)
            changed += 1
            if past_squareoff:
                _close(open1, premium, "SQUAREOFF", now)

        # ── Section 2: highest target + SL, optional re-entry ──
        open2 = next((r for r in s2 if r.status == "OPEN"), None)
        if open2:
            _mark(open2, premium)
            changed += 1
            if open2.target and premium >= float(open2.target):
                _close(open2, float(open2.target), "TARGET", now)
            elif open2.sl and premium <= float(open2.sl):
                _close(open2, float(open2.sl), "SL", now)
            elif past_squareoff:
                _close(open2, premium, "SQUAREOFF", now)
        elif best and not past_squareoff:
            reentry = bool(cfg.get("sec2_reentry"))
            if not s2 or reentry:                 # first ever, or re-entry after a close
                entry = float(best["level"])
                _create(db, user_id, today, 2, side, sd, entry,
                        _sec2_target(entry, best, cfg), _sec2_sl(entry, best, cfg), qty, now)
                changed += 1

    if changed:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("opei positions commit failed: %s", exc)
            return 0
    return changed


def fetch_positions(db, user_id: int, trade_date: str | None = None) -> dict:
    q = db.query(OPEIPaperPosition).filter(OPEIPaperPosition.user_id == user_id)
    if trade_date:
        try:
            q = q.filter(OPEIPaperPosition.trade_date == date.fromisoformat(trade_date))
        except ValueError:
            pass
    else:
        q = q.filter(OPEIPaperPosition.trade_date == date.today())
    rows = q.order_by(OPEIPaperPosition.section, OPEIPaperPosition.id).all()

    def _d(r):
        return {
            "id": r.id, "section": r.section, "side": r.side, "symbol": r.symbol,
            "strike": r.strike, "qty": r.qty,
            "entry_price": float(r.entry_price) if r.entry_price is not None else None,
            "entry_time": r.entry_time,
            "target": float(r.target) if r.target is not None else None,
            "sl": float(r.sl) if r.sl is not None else None,
            "ltp": float(r.ltp) if r.ltp is not None else None,
            "mfe": float(r.mfe) if r.mfe is not None else 0,
            "mae": float(r.mae) if r.mae is not None else 0,
            "pnl": float(r.pnl) if r.pnl is not None else 0,
            "status": r.status, "exit_price": float(r.exit_price) if r.exit_price is not None else None,
            "exit_time": r.exit_time, "exit_reason": r.exit_reason,
        }
    out = [_d(r) for r in rows]
    return {"section1": [r for r in out if r["section"] == 1],
            "section2": [r for r in out if r["section"] == 2]}
