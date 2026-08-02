"""
Append-only logging for OPEI recommendations (for win-rate / CSV analysis).

De-duplicates within a session so the same (side, level) at the same minute
isn't logged repeatedly while it keeps re-appearing on the live scan.
"""
from __future__ import annotations

from datetime import date, datetime

from core.logger import get_logger
from core.models import OPEIRecommendation

logger = get_logger("research.opei.log")


def _key(side, level, t):
    return (side, round(float(level or 0), 1), (t or "")[:5])


def log_recommendations(db, user_id: int, side: str, symbol: str, strike, premium,
                        recs: list[dict], signal_time: str) -> int:
    """Persist new institutional/activated recommendations (deduped for the day)."""
    if not recs:
        return 0
    today = date.today()
    existing = {
        _key(r.side, r.level, r.signal_time)
        for r in db.query(OPEIRecommendation.side, OPEIRecommendation.level,
                          OPEIRecommendation.signal_time)
                   .filter(OPEIRecommendation.user_id == user_id,
                           OPEIRecommendation.trade_date == today).all()
    }
    n = 0
    for r in recs:
        k = _key(side, r.get("level"), signal_time)
        if k in existing:
            continue
        existing.add(k)
        tgts = r.get("targets") or []
        db.add(OPEIRecommendation(
            user_id=user_id, trade_date=today, signal_time=signal_time, side=side,
            symbol=symbol, strike=int(strike) if strike else None,
            premium=premium, level=r.get("level"), confidence=r.get("confidence"),
            band=r.get("band"), sl=r.get("sl"), target1=tgts[0] if tgts else None,
            reasons=r.get("reasons") or [], data=r))
        n += 1
    if n:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("opei log commit failed: %s", exc)
            return 0
    return n


def update_outcomes(db, user_id: int, side: str, premium: float, now: datetime) -> int:
    """Track how each still-open recommendation for ``side`` is actually doing.

    Once live premium crosses a recommended level it is ``triggered``; from then
    on we accumulate the favourable/adverse excursion (points from the level) and
    flag target/SL hits. This turns the log into a real, self-calibrating record
    of what each level actually produced."""
    if premium is None:
        return 0
    today = now.date()
    rows = (db.query(OPEIRecommendation)
              .filter(OPEIRecommendation.user_id == user_id,
                      OPEIRecommendation.trade_date == today,
                      OPEIRecommendation.side == side,
                      OPEIRecommendation.target_hit.is_(False),
                      OPEIRecommendation.sl_hit.is_(False)).all())
    changed = 0
    p = float(premium)
    for r in rows:
        lvl = float(r.level or 0)
        tgt = float(r.target1 or 0)
        sl = float(r.sl or 0)
        if p >= lvl and not r.triggered:
            r.triggered = True
        if r.triggered:
            fav = round(p - lvl, 2)                       # points from the entry level
            r.mfe = max(float(r.mfe or 0), fav)
            r.mae = min(float(r.mae or 0), fav)
            try:
                st = datetime.strptime(r.signal_time or "00:00:00", "%H:%M:%S").time()
                r.duration_min = int((now - datetime.combine(today, st)).total_seconds() // 60)
            except Exception:
                pass
            if tgt and p >= tgt:
                r.target_hit, r.succeeded = True, True
            elif sl and p <= sl:
                r.sl_hit, r.succeeded = True, False
            changed += 1
    if changed:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("opei outcome update failed: %s", exc)
    return changed


def fetch_log(db, user_id: int, trade_date: str | None = None) -> list[dict]:
    q = db.query(OPEIRecommendation).filter(OPEIRecommendation.user_id == user_id)
    if trade_date:
        try:
            q = q.filter(OPEIRecommendation.trade_date == date.fromisoformat(trade_date))
        except ValueError:
            pass
    q = q.order_by(OPEIRecommendation.created_at.desc()).limit(1000)
    out = []
    for r in q.all():
        out.append({
            "date": r.trade_date.isoformat() if r.trade_date else None,
            "time": r.signal_time, "side": r.side, "symbol": r.symbol,
            "strike": r.strike, "premium": float(r.premium) if r.premium is not None else None,
            "level": float(r.level) if r.level is not None else None,
            "confidence": float(r.confidence) if r.confidence is not None else None,
            "band": r.band, "sl": float(r.sl) if r.sl is not None else None,
            "target1": float(r.target1) if r.target1 is not None else None,
            "reasons": r.reasons or [], "triggered": r.triggered,
            "target_hit": r.target_hit, "sl_hit": r.sl_hit,
            "succeeded": r.succeeded,
            "mfe": float(r.mfe) if r.mfe is not None else None,
            "mae": float(r.mae) if r.mae is not None else None,
            "duration_min": r.duration_min,
            "result": ("TARGET" if r.target_hit else ("SL" if r.sl_hit
                       else ("OPEN" if r.triggered else "WAITING"))),
        })
    return out
